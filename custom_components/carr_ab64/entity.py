"""Base entity + shared DeviceInfo for carr_ab64."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AB64Coordinator


class AB64Entity(CoordinatorEntity[AB64Coordinator]):
    """Base entity sharing DeviceInfo across all platforms of one config entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AB64Coordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Carrier-Toshiba",
            model="CARR-INTMBSLC-AB64 (Intesis INMBSTOS001R000)",
            sw_version=coordinator.sw_version,
        )
