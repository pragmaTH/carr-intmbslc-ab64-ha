"""Sensor platform for carr_ab64: error_code and indoor_temp (always) + the other 11
advanced telemetry fields (opt-in)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import REVOLUTIONS_PER_MINUTE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AB_BUS_ERROR_VALUE,
    ADV_INDOOR_FIELDS,
    ADV_OUTDOOR_FIELDS,
    CONF_ENABLE_ADVANCED,
    ERROR_CODES,
    REVOLUTIONS_PER_SECOND,
)
from .coordinator import AB64Coordinator
from .entity import AB64Entity


@dataclass(frozen=True)
class AdvancedSensorMeta:
    device_class: SensorDeviceClass | None
    unit: str | None
    state_class: SensorStateClass | None
    enabled_default: bool = True


ADV_SENSOR_META: dict[str, AdvancedSensorMeta] = {
    # indoor_temp (TA, register 4012) is the one advanced field created regardless of
    # CONF_ENABLE_ADVANCED (see async_setup_entry below) — every AC needs a
    # return-air thermistor to run its own control loop, so this is the one register
    # in the advanced group we're confident exists on every model/firmware, unlike
    # the other 11, which stay opt-in because we don't know that yet. It's also
    # already read every poll unconditionally by AB64Coordinator for
    # climate.current_temperature, so making this entity default-on adds zero extra
    # bus cost — the other 11 fields would each cost real read time if defaulted on
    # the same way.
    "indoor_temp": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "indoor_coil_temp_tcj": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "indoor_coil_temp_tc2": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "indoor_fan_rpm": AdvancedSensorMeta(
        None, REVOLUTIONS_PER_MINUTE, SensorStateClass.MEASUREMENT, enabled_default=False
    ),
    # No state_class: meaning/unit of this register (hours? cycle count?) is unknown,
    # so we don't want HA building a long-term statistic that can't be interpreted.
    "filter_sign_timer": AdvancedSensorMeta(None, None, None, enabled_default=False),
    "outdoor_evaporator_temp_te": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "outdoor_temp_to": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "outdoor_discharge_temp_td": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    "outdoor_suction_temp_ts": AdvancedSensorMeta(
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    ),
    # No device_class/unit: a real ramp test (idle -> high load) measured this
    # register at 29-65 while TD (discharge pipe) stayed near 80°C the whole time.
    # 29-65 A on a 3-phase compressor at that heat output would mean 17-38 kW of
    # input power from an ~11 kW-rated unit, which doesn't add up — the scale is
    # wrong, but with data from only one unit there's no way to derive the right
    # divisor, and guessing one (e.g. /10) would be an unverifiable assumption baked
    # into every user's card. Presented as a raw number instead so state_class
    # trend/statistics still work without implying a specific unit that's likely
    # wrong. See REVOLUTIONS_PER_SECOND in const.py for why compressor_rpm gets a
    # unit fix but this one doesn't.
    "compressor_current": AdvancedSensorMeta(
        None, None, SensorStateClass.MEASUREMENT, enabled_default=False
    ),
    "compressor_rpm": AdvancedSensorMeta(
        None, REVOLUTIONS_PER_SECOND, SensorStateClass.MEASUREMENT, enabled_default=False
    ),
    "lowest_fan_rpm": AdvancedSensorMeta(
        None, REVOLUTIONS_PER_MINUTE, SensorStateClass.MEASUREMENT, enabled_default=False
    ),
}

ADVANCED_FIELD_KEYS: list[str] = [*ADV_INDOOR_FIELDS.values(), *ADV_OUTDOOR_FIELDS.values()]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AB64Coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        AB64ErrorCodeSensor(coordinator),
        # See the comment on ADV_SENSOR_META["indoor_temp"] above for why this one
        # advanced field is unconditional. unique_id is field_key-driven either way
        # (see AB64AdvancedSensor.__init__), so creating it here rather than via the
        # opt-in loop below does not change it for anyone already using this entity.
        AB64AdvancedSensor(coordinator, "indoor_temp", ADV_SENSOR_META["indoor_temp"]),
    ]
    if entry.options.get(CONF_ENABLE_ADVANCED, False):
        entities.extend(
            AB64AdvancedSensor(coordinator, key, ADV_SENSOR_META[key])
            for key in ADVANCED_FIELD_KEYS
            # indoor_temp was already added above, unconditionally — skip it here so
            # enabling the option doesn't create a second entity for the same field.
            if key != "indoor_temp"
        )
    async_add_entities(entities)


class AB64ErrorCodeSensor(AB64Entity, SensorEntity):
    """Raw AC error code with decoded description/category/remote_code attributes."""

    _attr_translation_key = "error_code"

    def __init__(self, coordinator: AB64Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_error_code"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("error_code")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        code = self.coordinator.data.get("error_code")
        if code is None:
            return {}
        if code == AB_BUS_ERROR_VALUE:
            return {
                "remote_code": "N/A",
                "description": (
                    "AB Bus link down — AB64 cannot reach the AC unit "
                    "(see the ab_bus_link binary_sensor)"
                ),
                "category": "ab_bus_link",
            }
        remote_code, description, category = ERROR_CODES.get(
            code, ("unknown", "Unknown error code", "unknown")
        )
        return {"remote_code": remote_code, "description": description, "category": category}


class AB64AdvancedSensor(AB64Entity, SensorEntity):
    """One advanced telemetry field (indoor/outdoor). Created when opted in via
    CONF_ENABLE_ADVANCED — except indoor_temp, which is always created (see
    async_setup_entry and the comment on ADV_SENSOR_META["indoor_temp"])."""

    def __init__(self, coordinator: AB64Coordinator, field_key: str, meta: AdvancedSensorMeta) -> None:
        super().__init__(coordinator)
        self._field_key = field_key
        self._attr_translation_key = field_key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{field_key}"
        self._attr_device_class = meta.device_class
        self._attr_native_unit_of_measurement = meta.unit
        self._attr_state_class = meta.state_class
        self._attr_entity_registry_enabled_default = meta.enabled_default

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("advanced", {}).get(
            self._field_key
        ) is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("advanced", {}).get(self._field_key)
