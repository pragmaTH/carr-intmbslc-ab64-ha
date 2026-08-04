"""The Carrier-Toshiba AB64 (Modbus) integration."""
from __future__ import annotations

import logging

from pymodbus.exceptions import ConnectionException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_UNIT_ID, REG_SW_VERSION
from .coordinator import AB64Coordinator
from .hub import AB64ReadError, async_acquire_hub, async_release_hub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    unit_id = entry.data[CONF_UNIT_ID]

    try:
        hub = await async_acquire_hub(hass, host, port)
    except ConnectionException as err:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port}: {err}") from err

    try:
        coordinator = AB64Coordinator(hass, entry, hub)

        try:
            sw_regs = await hub.async_read_holding(unit_id, REG_SW_VERSION, 1)
        except (AB64ReadError, ConnectionException):
            coordinator.sw_version = None
        else:
            coordinator.sw_version = str(sw_regs[0])

        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Covers coordinator construction, sw_version-read, first-refresh, and
        # forward_entry_setups failures alike: any of them leaves this entry never
        # fully set up, so the hub refcount bumped by async_acquire_hub() above must
        # be rolled back here — otherwise a failed setup leaks a reference and the
        # shared client never closes even after the last entry on this host:port is
        # removed. Coordinator construction itself can raise (TypeError if a
        # non-numeric scan_interval in entry.options reaches timedelta(seconds=...)),
        # so it belongs inside this try too, not before it. The inner try/except above
        # is deliberately narrower and still runs first: a sw_version read failure
        # alone must degrade to sw_version = None, not abort setup.
        await async_release_hub(hass, host, port)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Only closes the shared client once every entry on this host:port has
        # released it — see AB64Hub refcounting in hub.py.
        await async_release_hub(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    return unload_ok
