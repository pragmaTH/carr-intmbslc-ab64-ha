"""DataUpdateCoordinator for a single AB64 config entry (one indoor unit / unit_id)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pymodbus.exceptions import ConnectionException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
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
    POST_WRITE_REFRESH_DELAY,
    REG_ERROR,
    REG_FAN,
    REG_MODE,
    REG_ON_OFF,
    REG_SETPOINT,
    REG_SWING,
    UNSUPPORTED_BLOCK_FAILURES,
    UNSUPPORTED_BLOCK_RETRY_LADDER,
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
        # Per-advanced-block backoff state (indoor/outdoor tracked independently) —
        # see UNSUPPORTED_BLOCK_FAILURES/UNSUPPORTED_BLOCK_RETRY_LADDER in const.py.
        # Deliberately separate from _consecutive_failures above, which is
        # basic-block-only and drives UpdateFailed, not a block-level pause.
        self._block_failures: dict[str, int] = {"indoor": 0, "outdoor": 0}
        self._block_retry_at: dict[str, datetime | None] = {"indoor": None, "outdoor": None}
        # Index into UNSUPPORTED_BLOCK_RETRY_LADDER for this block's *next* pause —
        # advances by 1 each time a retry-after-cooldown attempt fails (clamped to the
        # ladder's last rung), resets to 0 on any success.
        self._block_retry_step: dict[str, int] = {"indoor": 0, "outdoor": 0}
        # Handle for the single pending post-write follow-up refresh, if any — see
        # async_write/_async_schedule_post_write_refresh. Never more than one at a
        # time: scheduling a new one cancels whatever this currently holds first.
        self._post_write_refresh_unsub: CALLBACK_TYPE | None = None

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

        # Indoor block (TA/coil temps/fan/filter timer) is read every poll regardless
        # of advanced_enabled — climate.current_temperature depends on indoor_temp
        # and must have a value from first install, not only once a user opts into
        # advanced telemetry. Reading it is independent from *displaying* it: sensor.py
        # still only creates the 5 indoor AB64AdvancedSensor entities when the option
        # is on — this dict just needs the value available for climate to read.
        advanced: dict[str, int | None] = {}
        advanced.update(
            await self._async_read_advanced_block(
                "indoor", ADV_INDOOR_START, ADV_INDOOR_COUNT, ADV_INDOOR_FIELDS
            )
        )
        if self.advanced_enabled:
            advanced.update(
                await self._async_read_advanced_block(
                    "outdoor", ADV_OUTDOOR_START, ADV_OUTDOOR_COUNT, ADV_OUTDOOR_FIELDS
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
        self, block_key: str, start: int, count: int, fields: dict[int, str]
    ) -> dict[str, int | None]:
        """Read one advanced telemetry block. Failures degrade to None, never raise
        UpdateFailed — but after UNSUPPORTED_BLOCK_FAILURES consecutive failures for
        this block_key, stop attempting the read entirely for a while rather than
        paying a full read timeout on every single poll (see const.py; this matters
        most for the indoor block, which is now read unconditionally rather than only
        when opted in). The pause climbs a ladder (UNSUPPORTED_BLOCK_RETRY_LADDER)
        instead of jumping straight to the longest pause, so a brief RS-485 hiccup —
        the common case — recovers in under a minute instead of being punished with
        the same 10-minute silence reserved for a register that's genuinely missing
        on this AC model/firmware.
        """
        now = dt_util.utcnow()
        retry_at = self._block_retry_at[block_key]
        if retry_at is not None and now < retry_at:
            return {name: None for name in fields.values()}

        try:
            regs = await self.hub.async_read_holding(self.unit_id, start, count)
        except (AB64ReadError, ConnectionException) as err:
            was_paused = retry_at is not None
            self._block_failures[block_key] += 1
            failures = self._block_failures[block_key]
            if failures >= UNSUPPORTED_BLOCK_FAILURES:
                step = self._block_retry_step[block_key]
                delay = UNSUPPORTED_BLOCK_RETRY_LADDER[
                    min(step, len(UNSUPPORTED_BLOCK_RETRY_LADDER) - 1)
                ]
                self._block_retry_at[block_key] = now + timedelta(seconds=delay)
                self._block_retry_step[block_key] = step + 1
                _LOGGER.debug(
                    "%s block (%s-%s) for unit %s %s; pausing reads for %s seconds: %s",
                    block_key,
                    start,
                    start + count - 1,
                    self.unit_id,
                    "still unresponsive after the retry" if was_paused else
                    f"failed {failures} times in a row",
                    delay,
                    err,
                )
            else:
                _LOGGER.debug(
                    "%s block (%s-%s) unavailable for unit %s (%s/%s consecutive "
                    "failures): %s",
                    block_key,
                    start,
                    start + count - 1,
                    self.unit_id,
                    failures,
                    UNSUPPORTED_BLOCK_FAILURES,
                    err,
                )
            return {name: None for name in fields.values()}

        if retry_at is not None:
            _LOGGER.debug(
                "%s block (%s-%s) for unit %s responded again after being paused; "
                "resuming normal reads",
                block_key,
                start,
                start + count - 1,
                self.unit_id,
            )
        self._block_failures[block_key] = 0
        self._block_retry_at[block_key] = None
        self._block_retry_step[block_key] = 0
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

        That immediate refresh reads back the pre-write value every time (measured
        on real hardware: the AB64 takes ~1.7s to reflect a written value in its
        registers — see POST_WRITE_REFRESH_DELAY in const.py), so a single follow-up
        refresh is also scheduled a couple seconds out to pick up the settled value
        without making the user wait for the next regular poll. This is NOT
        optimistic state: both refreshes are real reads through the same
        _async_update_data path as every other poll — nothing here ever writes the
        value just sent into coordinator.data directly. The user has locked this as
        a hard requirement (values must always come from what the hardware actually
        reports), so don't "simplify" this by setting the field from `value` instead
        of scheduling a real read.
        """
        try:
            await self.hub.async_write_register(self.unit_id, address, value)
        except (AB64WriteError, ConnectionException) as err:
            raise HomeAssistantError(str(err)) from err
        if refresh:
            await self.async_refresh()
            self._async_schedule_post_write_refresh()

    @callback
    def _async_schedule_post_write_refresh(self) -> None:
        """Schedule exactly one follow-up read POST_WRITE_REFRESH_DELAY after a write.

        No-op when update_interval is already <= POST_WRITE_REFRESH_DELAY: the next
        regular poll will already catch the settled value at that point, so scheduling
        one here would just be an extra read on top of the regular poll — and the
        users running an interval that short are already the ones most exposed to bus
        load (see MIN_SCAN_INTERVAL/the options data_description bus-time warning),
        the last group that should be paying for a redundant read.

        Rapid repeated writes (e.g. dragging a temperature slider) must not stack N
        pending follow-ups for N writes — any previous pending one is cancelled
        before a new one is scheduled, so there's ever at most one in flight.
        """
        if self.update_interval is not None and (
            self.update_interval.total_seconds() <= POST_WRITE_REFRESH_DELAY
        ):
            return
        if self._post_write_refresh_unsub is not None:
            self._post_write_refresh_unsub()
        self._post_write_refresh_unsub = async_call_later(
            self.hass, POST_WRITE_REFRESH_DELAY, self._async_post_write_refresh
        )

    async def _async_post_write_refresh(self, _now: datetime) -> None:
        self._post_write_refresh_unsub = None
        await self.async_refresh()

    @callback
    def async_cancel_pending_post_write_refresh(self) -> None:
        """Cancel a pending post-write follow-up, if any — call on entry unload.

        Without this, a follow-up scheduled just before the user removes/reloads the
        entry would still fire afterwards and call async_refresh() on a coordinator
        whose entry is already torn down.
        """
        if self._post_write_refresh_unsub is not None:
            self._post_write_refresh_unsub()
            self._post_write_refresh_unsub = None
