"""Sensor platform for carr_ab64: error_code (always) + advanced telemetry (opt-in)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import REVOLUTIONS_PER_MINUTE, UnitOfElectricCurrent, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AB_BUS_ERROR_VALUE,
    ADV_INDOOR_FIELDS,
    ADV_OUTDOOR_FIELDS,
    CONF_ENABLE_ADVANCED,
    ERROR_CODES,
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
    "compressor_current": AdvancedSensorMeta(
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.AMPERE,
        SensorStateClass.MEASUREMENT,
        enabled_default=False,
    ),
    "compressor_rpm": AdvancedSensorMeta(
        None, REVOLUTIONS_PER_MINUTE, SensorStateClass.MEASUREMENT, enabled_default=False
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
    entities: list[SensorEntity] = [AB64ErrorCodeSensor(coordinator)]
    if entry.options.get(CONF_ENABLE_ADVANCED, False):
        entities.extend(
            AB64AdvancedSensor(coordinator, key, ADV_SENSOR_META[key])
            for key in ADVANCED_FIELD_KEYS
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
    """One advanced telemetry field (indoor/outdoor). Only created when opted in."""

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
