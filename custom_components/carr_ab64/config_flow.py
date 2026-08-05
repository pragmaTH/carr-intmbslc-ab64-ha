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

from .climate import MODE_TO_HVAC
from .const import (
    BASIC_BLOCK_COUNT,
    BASIC_BLOCK_START,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID_SCAN_RANGE,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    REG_ON_OFF,
)
from .coordinator import BASIC_REG_OFFSETS
from .hub import AB64Hub, AB64ReadError, async_acquire_hub, async_release_hub

_LOGGER = logging.getLogger(__name__)

# Per-candidate probe timeout while scanning the unit-id range — separate from the
# hub's normal 3s/1-retry timeout (AB64Hub.MODBUS_TIMEOUT), which stays untouched for
# regular polling. A full 64-address scan at 3s/candidate was ~192s with no progress
# indicator; at 1s/candidate it's ~64s worst case.
SCAN_PROBE_TIMEOUT = 1.0

# Still a selector.NumberSelector (not a plain vol.Range-based validator) so mode=BOX
# keeps this a typeable number field rather than a slider — a slider has no sensible
# "type your own port" affordance. It still returns a float, so vol.Coerce(int) stays
# chained on top: with default=DEFAULT_PORT below, an untouched submit would otherwise
# come back as 502.0, which breaks equality against the int CONF_PORT stored on other
# entries (unique_id string, AsyncModbusTcpClient(port=...), etc.).
#
# Previously this field had no default, on the theory that NumberSelector renders as
# an empty box. Confirmed wrong against a real HA setup: it renders 0, which min=1
# always rejects, so a user who didn't know their port and submitted as-is got an
# unhelpful validation error instead of an empty field. See DEFAULT_PORT in const.py
# for why 502 is now used as the default.
PORT_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
    ),
    vol.Coerce(int),
)

# Same reasoning/shape as PORT_SELECTOR (a plain vol.Range-based validator renders as
# a number field/slider that can't be left empty on the frontend). This one matters
# even more: unit_id is genuinely optional (blank = scan), and the 1-64 range (not
# 0-something) reflects that the vendor's SW1+SW2 address table is 1-based — see
# DEFAULT_UNIT_ID_SCAN_RANGE in const.py. mode=BOX (not SLIDER) is required for a
# slider-type widget to have no natural "unset" position.
UNIT_ID_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=64, mode=selector.NumberSelectorMode.BOX)
    ),
    vol.Coerce(int),
)

# unit_id is `vol.Optional` with no default — deliberately left blank-able (blank =
# scan every address; see async_step_user). It used to live in its own step
# (`STEP_UNIT_SCHEMA`/`async_step_unit`), but that gave the flow three different
# behaviors depending on how many units a scan found (silently finish on 1 hit, ask
# on >1, silently finish again once the first one's taken by another entry) —
# decision 2026-08-05: fold it into this schema and never auto-pick, so the flow has
# exactly one shape: fill it in and verify directly, or leave it blank and always
# confirm from the scan results (see async_step_select_unit).
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
        vol.Optional(CONF_UNIT_ID): UNIT_ID_SELECTOR,
    }
)


class AB64ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for carr_ab64."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._name: str = ""
        self._found_units: list[int] = []
        self._unit_labels: dict[int, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: user_input[CONF_PORT],
            }
            self._name = user_input[CONF_NAME]
            manual_id = user_input.get(CONF_UNIT_ID)
            candidates = [manual_id] if manual_id is not None else list(
                DEFAULT_UNIT_ID_SCAN_RANGE
            )
            try:
                hub = await async_acquire_hub(
                    self.hass, self._data[CONF_HOST], self._data[CONF_PORT]
                )
            except ConnectionException:
                errors["base"] = "cannot_connect"
            else:
                # One acquire covers both scanning and (on the scan path) building
                # confirmation-page labels below — acquiring a second time for the
                # labels used to close/reconnect the shared client mid-flow (3
                # connects/2 closes per submission) and let a second connect failure
                # escape as an unhandled exception instead of becoming
                # cannot_connect. Release happens in `finally` no matter which
                # branch returns, so a failure here can't leak the refcount the way
                # the m1 setup-entry bug did.
                try:
                    found = await self._async_scan_units(hub, candidates)

                    error_key: str | None = None
                    if manual_id is None:
                        # Don't offer unit-ids that already belong to another entry
                        # on this same host:port — picking one would just abort the
                        # flow.
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
                    elif manual_id is not None:
                        # Typed in and verified directly — the user already knows
                        # which physical unit this is, so finish immediately. Only
                        # the scan path below ever shows a confirmation page.
                        return await self._async_finish_unit(found[0])
                    else:
                        # Blank input => scanned. Always confirm, even when exactly
                        # one unit responded — decision 2026-08-05: no auto-pick, so
                        # this step behaves the same way regardless of how many
                        # units answer.
                        self._found_units = found
                        self._unit_labels = await self._async_build_unit_labels(
                            hub, found
                        )
                        return await self.async_step_select_unit()
                except ConnectionException:
                    errors["base"] = "cannot_connect"
                finally:
                    await async_release_hub(
                        self.hass, self._data[CONF_HOST], self._data[CONF_PORT]
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
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
        schema = vol.Schema({vol.Required(CONF_UNIT_ID): vol.In(self._unit_labels)})
        return self.async_show_form(step_id="select_unit", data_schema=schema)

    async def _async_build_unit_labels(
        self, hub: AB64Hub, units: list[int]
    ) -> dict[int, str]:
        """Read each responding unit's basic block once (read-only FC03) so the
        confirmation page can show real state instead of a bare unit-id — otherwise
        there's no way to tell which physical AC a given number belongs to. This is
        informational only: a read failure for one unit degrades that unit's label to
        a bare number, it never raises or aborts the flow.

        Takes an already-acquired hub instead of acquiring its own — see
        async_step_user, which acquires one hub covering both scanning and this,
        so the flow only connects once per submission instead of the shared client
        being closed and reconnected mid-flow.
        """
        labels: dict[int, str] = {}
        for unit_id in units:
            try:
                regs = await hub.async_read_holding(
                    unit_id, BASIC_BLOCK_START, BASIC_BLOCK_COUNT
                )
            except (AB64ReadError, ConnectionException):
                labels[unit_id] = str(unit_id)
                continue
            if regs[BASIC_REG_OFFSETS["on_off"]] == 0:
                labels[unit_id] = f"{unit_id} — off"
                continue
            hvac_mode = MODE_TO_HVAC.get(regs[BASIC_REG_OFFSETS["mode"]])
            mode_name = hvac_mode.value if hvac_mode else "unknown mode"
            set_temp = regs[BASIC_REG_OFFSETS["set_temp"]]
            labels[unit_id] = f"{unit_id} — on · {mode_name} · set {set_temp} °C"
        return labels

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
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

    async def _async_scan_units(self, hub: AB64Hub, candidates: list[int]) -> list[int]:
        """Try reading register 0 for each candidate unit_id through the given hub.

        Each probe is bounded by SCAN_PROBE_TIMEOUT, separate from the hub's normal
        read timeout, so a full-range scan doesn't cost ~3s per non-existent unit_id.

        Takes an already-acquired hub instead of acquiring its own — see
        async_step_user, which owns the single acquire/release pair covering both
        this scan and (on the scan path) building confirmation-page labels.
        """
        found: list[int] = []
        for unit_id in candidates:
            try:
                async with asyncio.timeout(SCAN_PROBE_TIMEOUT):
                    await hub.async_read_holding(unit_id, REG_ON_OFF, 1)
            except (AB64ReadError, TimeoutError):
                continue
            found.append(unit_id)
        return found

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
