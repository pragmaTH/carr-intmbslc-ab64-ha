"""Climate platform for carr_ab64 — one climate entity per config entry."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    SWING_OFF as HA_SWING_OFF,
    SWING_ON as HA_SWING_ON,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FAN_AUTO as CODE_FAN_AUTO,
    FAN_HIGH as CODE_FAN_HIGH,
    FAN_LOW as CODE_FAN_LOW,
    FAN_MEDIUM as CODE_FAN_MEDIUM,
    MODE_AUTO,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN,
    MODE_HEAT,
    REG_FAN,
    REG_MODE,
    REG_ON_OFF,
    REG_SETPOINT,
    REG_SWING,
    SWING_OFF,
    SWING_ON,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
)
from .coordinator import AB64Coordinator
from .entity import AB64Entity

_LOGGER = logging.getLogger(__name__)

MODE_TO_HVAC: dict[int, HVACMode] = {
    MODE_AUTO: HVACMode.AUTO,
    MODE_HEAT: HVACMode.HEAT,
    MODE_DRY: HVACMode.DRY,
    MODE_FAN: HVACMode.FAN_ONLY,
    MODE_COOL: HVACMode.COOL,
}
HVAC_TO_MODE: dict[HVACMode, int] = {v: k for k, v in MODE_TO_HVAC.items()}

FAN_MODE_TO_CODE: dict[str, int] = {
    FAN_AUTO: CODE_FAN_AUTO,
    FAN_LOW: CODE_FAN_LOW,
    FAN_MEDIUM: CODE_FAN_MEDIUM,
    FAN_HIGH: CODE_FAN_HIGH,
}
CODE_TO_FAN_MODE: dict[int, str] = {v: k for k, v in FAN_MODE_TO_CODE.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AB64Coordinator = entry.runtime_data
    async_add_entities([AB64Climate(coordinator)])


class AB64Climate(AB64Entity, ClimateEntity):
    """Single climate entity: on/off, hvac mode, fan speed, swing, setpoint."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.COOL,
    ]
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]
    _attr_swing_modes = [HA_SWING_OFF, HA_SWING_ON]
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_target_temperature_step = TEMP_STEP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: AB64Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_climate"
        self._logged_unknown_modes: set[int] = set()

    @property
    def hvac_mode(self) -> HVACMode | None:
        data = self.coordinator.data
        if data["on_off"] == 0:
            return HVACMode.OFF
        mode = data["mode"]
        hvac_mode = MODE_TO_HVAC.get(mode)
        if hvac_mode is None:
            # on_off == 1 but the mode code isn't one we know — reporting OFF here
            # would misrepresent a unit that's actually running, so surface unknown
            # instead of guessing.
            if mode not in self._logged_unknown_modes:
                self._logged_unknown_modes.add(mode)
                _LOGGER.warning(
                    "Unknown mode code %s from unit %s while on_off=1; "
                    "hvac_mode will report as unknown",
                    mode,
                    self.coordinator.unit_id,
                )
            return None
        return hvac_mode

    @property
    def fan_mode(self) -> str | None:
        return CODE_TO_FAN_MODE.get(self.coordinator.data["fan_speed"])

    @property
    def swing_mode(self) -> str | None:
        return HA_SWING_ON if self.coordinator.data["swing"] == SWING_ON else HA_SWING_OFF

    @property
    def target_temperature(self) -> float | None:
        return self.coordinator.data["set_temp"]

    @property
    def current_temperature(self) -> float | None:
        # Only available when advanced telemetry (register 4012, TA) is opt-in enabled.
        return self.coordinator.data["advanced"].get("indoor_temp")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        # Core 2025.10.1 passes hvac_mode straight through kwargs here when a caller
        # sets both at once (e.g. `climate.set_temperature: {temperature: 24,
        # hvac_mode: cool}`) — it does NOT call async_set_hvac_mode() for us. Set the
        # mode first (same on_off/mode write pattern as async_set_hvac_mode below),
        # then the setpoint, with only the final write triggering a refresh.
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)

        # Unlike the `climate.set_hvac_mode` service (which core validates against
        # this entity's `hvac_modes` before calling async_set_hvac_mode via
        # _valid_mode_or_raise), `climate.set_temperature`'s hvac_mode is only
        # vol.Coerce(HVACMode)'d by core — any valid HVACMode enum member gets
        # through, whether or not this unit supports it. Reject it here with the
        # same user-facing error type the other service uses, before writing
        # anything, instead of a bare KeyError out of HVAC_TO_MODE below.
        if hvac_mode is not None and hvac_mode != HVACMode.OFF and hvac_mode not in HVAC_TO_MODE:
            raise ServiceValidationError(
                f"{hvac_mode} is not a supported hvac_mode for this unit "
                f"(supported: {', '.join(m.value for m in HVAC_TO_MODE)}, off)"
            )

        if hvac_mode is not None:
            if hvac_mode == HVACMode.OFF:
                await self.coordinator.async_write(REG_ON_OFF, 0, refresh=temperature is None)
            else:
                if self.coordinator.data["on_off"] == 0:
                    await self.coordinator.async_write(REG_ON_OFF, 1, refresh=False)
                await self.coordinator.async_write(
                    REG_MODE, HVAC_TO_MODE[hvac_mode], refresh=temperature is None
                )

        if temperature is None:
            return
        await self.coordinator.async_write(REG_SETPOINT, int(temperature))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_write(REG_FAN, FAN_MODE_TO_CODE[fan_mode])

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        value = SWING_ON if swing_mode == HA_SWING_ON else SWING_OFF
        await self.coordinator.async_write(REG_SWING, value)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write(REG_ON_OFF, 0)
            return
        if self.coordinator.data["on_off"] == 0:
            await self.coordinator.async_write(REG_ON_OFF, 1, refresh=False)
        await self.coordinator.async_write(REG_MODE, HVAC_TO_MODE[hvac_mode])

    async def async_turn_on(self) -> None:
        await self.coordinator.async_write(REG_ON_OFF, 1)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_write(REG_ON_OFF, 0)
