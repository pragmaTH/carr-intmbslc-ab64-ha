"""Binary sensor platform for carr_ab64: AB Bus link fault (debounced)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AB64Coordinator
from .entity import AB64Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AB64Coordinator = entry.runtime_data
    async_add_entities([AB64BusLinkBinarySensor(coordinator)])


class AB64BusLinkBinarySensor(AB64Entity, BinarySensorEntity):
    """`on` only after register 11 == 65535 has held continuously past the debounce
    window computed by the coordinator — never checked directly here."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "ab_bus_link"

    def __init__(self, coordinator: AB64Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_ab_bus_link"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("ab_bus_fault")
