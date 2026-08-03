"""Config flow for the Carrier-Toshiba AB64 (Modbus) integration.

unit_id is always confirmed by actually reading register 0 through the shared hub
(scan a range, or verify a manually entered value) — never trusted from a DIP
switch reading alone (see reference section 8, gotcha 1).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from pymodbus.exceptions import ConnectionException

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID_SCAN_RANGE,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    REG_ON_OFF,
)
from .hub import AB64ReadError, async_acquire_hub, async_release_hub

_LOGGER = logging.getLogger(__name__)

# Per-candidate probe timeout while scanning the unit-id range — separate from the
# hub's normal 3s/1-retry timeout (AB64Hub.MODBUS_TIMEOUT), which stays untouched for
# regular polling. A full 0-63 scan at 3s/candidate was ~190s with no progress
# indicator; at 1s/candidate (and skipping address 0, see DEFAULT_UNIT_ID_SCAN_RANGE
# in const.py) it's ~63s worst case.
SCAN_PROBE_TIMEOUT = 1.0

# A plain `vol.All(vol.Coerce(int), vol.Range(min=1, ...))` gets rendered by the HA
# frontend as a number field that shows the range's `min` as a pre-filled value —
# there's no field left blank, so a user who doesn't know their gateway's port (see
# the "not defaulted" wording in strings.json) can submit the form untouched and get
# port 1, which is never correct. A `selector.NumberSelector` renders as a genuinely
# empty box instead; it validates min/max itself but returns a float, so it's still
# chained with vol.Coerce(int) to keep CONF_PORT an int everywhere else (unique_id
# string, AsyncModbusTcpClient(port=...), etc.).
PORT_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
    ),
    vol.Coerce(int),
)

# Same reasoning/shape as PORT_SELECTOR (a plain vol.Range-based validator renders as
# a number field/slider that can't be left empty on the frontend). This one matters
# even more: unit_id is genuinely optional (blank = scan the whole range), and 0 is
# the Modbus broadcast address that never answers, so a widget that can't render
# "empty" would silently steer every user away from the scan path. mode=BOX (not
# SLIDER) is required for a slider-type widget to have no natural "unset" position.
UNIT_ID_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=63, mode=selector.NumberSelectorMode.BOX)
    ),
    vol.Coerce(int),
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT): PORT_SELECTOR,
    }
)

STEP_UNIT_SCHEMA = vol.Schema({vol.Optional(CONF_UNIT_ID): UNIT_ID_SELECTOR})


class AB64ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for carr_ab64."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._name: str = ""
        self._found_units: list[int] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
            }
            self._name = user_input[CONF_NAME]
            return await self.async_step_unit()
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_unit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            manual_id = user_input.get(CONF_UNIT_ID)
            if manual_id is not None:
                candidates = [manual_id]
            else:
                # Unit-id 0 is the Modbus broadcast address and never answers a
                # read — skip it in a full scan (a user can still type 0 manually;
                # it will just fail verification with a message pointing at the
                # DIP switch, see strings.json).
                candidates = [u for u in DEFAULT_UNIT_ID_SCAN_RANGE if u != 0]
            try:
                found = await self._async_scan_units(candidates)
            except ConnectionException:
                errors["base"] = "cannot_connect"
            else:
                error_key: str | None = None
                if manual_id is None:
                    # Don't offer unit-ids that already belong to another entry on
                    # this same host:port — picking one would just abort the flow.
                    responded = found
                    already_configured = self._configured_unit_ids(
                        self._data[CONF_HOST], self._data[CONF_PORT]
                    )
                    found = [u for u in responded if u not in already_configured]
                    if not found:
                        # Distinguish "nothing on the bus answered" (wiring/baud/
                        # gateway problem) from "everything that answered is
                        # already another entry" (normal when adding a 2nd AC on
                        # the same gateway) — the fix for each is completely
                        # different and pointing at wiring here would be wrong.
                        error_key = "all_units_already_configured" if responded else "no_units_found"
                elif not found:
                    error_key = "read_timeout"

                if error_key:
                    errors["base"] = error_key
                elif len(found) == 1:
                    return await self._async_finish_unit(found[0])
                else:
                    self._found_units = found
                    return await self.async_step_select_unit()
        return self.async_show_form(
            step_id="unit", data_schema=STEP_UNIT_SCHEMA, errors=errors
        )

    def _configured_unit_ids(self, host: str, port: int) -> set[int]:
        """unit-ids already claimed by another entry on this (host, port)."""
        return {
            entry.data[CONF_UNIT_ID]
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_HOST) == host and entry.data.get(CONF_PORT) == port
        }

    async def async_step_select_unit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_unit(int(user_input[CONF_UNIT_ID]))
        schema = vol.Schema(
            {vol.Required(CONF_UNIT_ID): vol.In({u: str(u) for u in self._found_units})}
        )
        return self.async_show_form(step_id="select_unit", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            unit_id = user_input[CONF_UNIT_ID]
            unique_id = f"{host}:{port}:{unit_id}"

            duplicate = any(
                other.entry_id != entry.entry_id and other.unique_id == unique_id
                for other in self.hass.config_entries.async_entries(DOMAIN)
            )
            if duplicate:
                errors["base"] = "already_configured"
            else:
                try:
                    hub = await async_acquire_hub(self.hass, host, port)
                except ConnectionException:
                    errors["base"] = "cannot_connect"
                else:
                    try:
                        await hub.async_read_holding(unit_id, REG_ON_OFF, 1)
                    except AB64ReadError:
                        errors["base"] = "read_timeout"
                    finally:
                        await async_release_hub(self.hass, host, port)

            if not errors:
                data = {
                    **entry.data,
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_UNIT_ID: unit_id,
                }
                await self.async_set_unique_id(unique_id)
                return self.async_update_reload_and_abort(
                    entry,
                    data=data,
                    unique_id=unique_id,
                    reason="reconfigure_successful",
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): PORT_SELECTOR,
                vol.Required(
                    CONF_UNIT_ID, default=entry.data[CONF_UNIT_ID]
                ): UNIT_ID_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=data_schema, errors=errors
        )

    async def _async_scan_units(self, candidates: list[int]) -> list[int]:
        """Try reading register 0 for each candidate unit_id through the shared hub.

        Each probe is bounded by SCAN_PROBE_TIMEOUT, separate from the hub's normal
        read timeout, so a full-range scan doesn't cost ~3s per non-existent unit_id.
        """
        hub = await async_acquire_hub(self.hass, self._data[CONF_HOST], self._data[CONF_PORT])
        try:
            found: list[int] = []
            for unit_id in candidates:
                try:
                    async with asyncio.timeout(SCAN_PROBE_TIMEOUT):
                        await hub.async_read_holding(unit_id, REG_ON_OFF, 1)
                except (AB64ReadError, TimeoutError):
                    continue
                found.append(unit_id)
            return found
        finally:
            await async_release_hub(self.hass, self._data[CONF_HOST], self._data[CONF_PORT])

    async def _async_finish_unit(self, unit_id: int) -> ConfigFlowResult:
        unique_id = f"{self._data[CONF_HOST]}:{self._data[CONF_PORT]}:{unit_id}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        data = {**self._data, CONF_UNIT_ID: unit_id}
        return self.async_create_entry(title=self._name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> AB64OptionsFlow:
        return AB64OptionsFlow()


class AB64OptionsFlow(OptionsFlow):
    """Poll interval + advanced telemetry opt-in."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options

        if user_input is not None:
            scan_interval = user_input[CONF_SCAN_INTERVAL]
            if scan_interval < MIN_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "scan_interval_too_low"
            else:
                return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=(user_input or options).get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.Coerce(int),
                vol.Required(
                    CONF_ENABLE_ADVANCED,
                    default=(user_input or options).get(CONF_ENABLE_ADVANCED, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
