"""DataUpdateCoordinator for a single AB64 config entry (one indoor unit / unit_id)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pymodbus.exceptions import ConnectionException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    AB_BUS_ERROR_VALUE,
    AB_BUS_FAULT_DEBOUNCE_SECONDS,
    ADV_INDOOR_FIELDS,
    ADV_INDOOR_START,
    ADV_INDOOR_COUNT,
    ADV_NO_VALUE,
    ADV_OUTDOOR_FIELDS,
    ADV_OUTDOOR_START,
    ADV_OUTDOOR_COUNT,
    ADV_TEMPERATURE_FIELDS,
    BASIC_BLOCK_COUNT,
    BASIC_BLOCK_START,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONSECUTIVE_READ_FAILURES,
    REG_ERROR,
    REG_FAN,
    REG_MODE,
    REG_ON_OFF,
    REG_SETPOINT,
    REG_SWING,
)
from .hub import AB64Hub, AB64ReadError, AB64WriteError

_LOGGER = logging.getLogger(__name__)

BASIC_REG_OFFSETS = {
    "on_off": REG_ON_OFF - BASIC_BLOCK_START,
    "mode": REG_MODE - BASIC_BLOCK_START,
    "fan_speed": REG_FAN - BASIC_BLOCK_START,
    "swing": REG_SWING - BASIC_BLOCK_START,
    "set_temp": REG_SETPOINT - BASIC_BLOCK_START,
    "error_code": REG_ERROR - BASIC_BLOCK_START,
}


class AB64Coordinator(DataUpdateCoordinator[dict]):
    """Coordinator for one AB64 config entry.

    `coordinator.data` is the locked data contract (see plan-core.md):

        {
            "on_off": int, "mode": int, "fan_speed": int, "swing": int,
            "set_temp": int, "error_code": int, "ab_bus_fault": bool,
            "advanced": dict[str, int | None],
        }
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: AB64Hub) -> None:
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.entry = entry
        self.hub = hub
        self.unit_id: int = entry.data[CONF_UNIT_ID]
        self.sw_version: str | None = None
        self._fault_since: datetime | None = None
        self._consecutive_failures = 0

    @property
    def advanced_enabled(self) -> bool:
        return self.entry.options.get(CONF_ENABLE_ADVANCED, False)

    async def _async_update_data(self) -> dict:
        try:
            regs = await self.hub.async_read_holding(
                self.unit_id, BASIC_BLOCK_START, BASIC_BLOCK_COUNT
            )
        except (AB64ReadError, ConnectionException) as err:
            self._consecutive_failures += 1
            # self.data is None on the very first poll (during
            # async_config_entry_first_refresh) — that case must always raise so HA
            # surfaces ConfigEntryNotReady instead of "successfully" setting up an
            # entry that has never read anything. Once there's a known-good value,
            # tolerate a run of misses (see MAX_CONSECUTIVE_READ_FAILURES in
            # const.py) instead of flapping every entity to unavailable on one
            # dropped RS-485 frame — this matters more now that MIN_SCAN_INTERVAL is
            # 1s.
            if self.data is not None and self._consecutive_failures < MAX_CONSECUTIVE_READ_FAILURES:
                _LOGGER.debug(
                    "Basic block read failed for unit %s (%s/%s consecutive "
                    "failures), keeping last known data: %s",
                    self.unit_id,
                    self._consecutive_failures,
                    MAX_CONSECUTIVE_READ_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        self._consecutive_failures = 0
        error_code = regs[BASIC_REG_OFFSETS["error_code"]]
        ab_bus_fault = self._compute_ab_bus_fault(error_code)

        advanced: dict[str, int | None] = {}
        if self.advanced_enabled:
            advanced.update(
                await self._async_read_advanced_block(
                    ADV_INDOOR_START, ADV_INDOOR_COUNT, ADV_INDOOR_FIELDS
                )
            )
            advanced.update(
                await self._async_read_advanced_block(
                    ADV_OUTDOOR_START, ADV_OUTDOOR_COUNT, ADV_OUTDOOR_FIELDS
                )
            )

        return {
            "on_off": regs[BASIC_REG_OFFSETS["on_off"]],
            "mode": regs[BASIC_REG_OFFSETS["mode"]],
            "fan_speed": regs[BASIC_REG_OFFSETS["fan_speed"]],
            "swing": regs[BASIC_REG_OFFSETS["swing"]],
            "set_temp": regs[BASIC_REG_OFFSETS["set_temp"]],
            "error_code": error_code,
            "ab_bus_fault": ab_bus_fault,
            "advanced": advanced,
        }

    def _compute_ab_bus_fault(self, error_code: int) -> bool:
        """Debounce register 11 == 65535 — see AB_BUS_FAULT_DEBOUNCE_SECONDS in const.py."""
        now = dt_util.utcnow()
        if error_code != AB_BUS_ERROR_VALUE:
            self._fault_since = None
            return False
        if self._fault_since is None:
            self._fault_since = now
        elapsed = (now - self._fault_since).total_seconds()
        return elapsed >= AB_BUS_FAULT_DEBOUNCE_SECONDS

    async def _async_read_advanced_block(
        self, start: int, count: int, fields: dict[int, str]
    ) -> dict[str, int | None]:
        """Read one advanced telemetry block. Failures degrade to None, never raise UpdateFailed."""
        try:
            regs = await self.hub.async_read_holding(self.unit_id, start, count)
        except (AB64ReadError, ConnectionException) as err:
            _LOGGER.debug(
                "Advanced block %s-%s unavailable for unit %s: %s",
                start,
                start + count - 1,
                self.unit_id,
                err,
            )
            return {name: None for name in fields.values()}
        return {
            name: self._decode_advanced_value(name, regs[addr - start])
            for addr, name in fields.items()
        }

    @staticmethod
    def _decode_advanced_value(name: str, raw: int) -> int | None:
        """0xFFFF means "no value" (same sentinel family as register 11); temperature
        fields are additionally decoded as 16-bit two's complement since TE/TO/TD/TS
        can go negative during defrost/low-load (see ADV_TEMPERATURE_FIELDS)."""
        if raw == ADV_NO_VALUE:
            return None
        if name in ADV_TEMPERATURE_FIELDS and raw > 32767:
            return raw - 65536
        return raw

    async def async_write(self, address: int, value: int, *, refresh: bool = True) -> None:
        """Write one register through the shared hub, then refresh coordinator data.

        Uses `async_refresh()` (immediate) rather than `async_request_refresh()`
        (which goes through HA's 10s Debouncer cooldown) — after a user-initiated
        write, waiting up to 10s for the UI to reflect it reads as "command didn't
        take" and invites a repeat write, which just adds more traffic to a bus that
        may already be shared with other AB64 units.
        """
        try:
            await self.hub.async_write_register(self.unit_id, address, value)
        except (AB64WriteError, ConnectionException) as err:
            raise HomeAssistantError(str(err)) from err
        if refresh:
            await self.async_refresh()
