"""Binary sensor test: ab_bus_link mirrors coordinator.data, never recomputes."""
from __future__ import annotations

from datetime import timedelta

from freezegun import freeze_time
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    AB_BUS_FAULT_DEBOUNCE_SECONDS,
    CONF_UNIT_ID,
    DOMAIN,
)

from tests.conftest import HAPPY_BASIC_REGS, TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path

ENTITY_ID = "binary_sensor.living_room_ac_ab_bus_link"


async def _setup(hass, fake_clients, *, basic_regs=None):
    seed_happy_path(fake_clients, basic_regs=basic_regs)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_device_class_is_problem(hass, fake_clients):
    await _setup(hass, fake_clients)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["device_class"] == BinarySensorDeviceClass.PROBLEM


async def test_off_on_happy_path(hass, fake_clients):
    await _setup(hass, fake_clients)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"


async def test_reads_ab_bus_fault_from_coordinator_not_recomputed_locally(hass, fake_clients):
    """Case 29: the entity must be a pure reflection of coordinator.data['ab_bus_fault']
    (which is already debounced) — it must not re-derive from error_code==65535 itself,
    or the debounce would be bypassed at the entity layer."""
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = 65535

    with freeze_time("2026-01-01 00:00:00") as frozen:
        entry = await _setup(hass, fake_clients, basic_regs=regs)

        # error_code IS 65535 right now, but coordinator.data["ab_bus_fault"] is still
        # False pre-debounce — the binary_sensor must agree, not look at error_code itself.
        assert entry.runtime_data.data["error_code"] == 65535
        assert entry.runtime_data.data["ab_bus_fault"] is False
        state = hass.states.get(ENTITY_ID)
        assert state.state == "off"

        frozen.tick(timedelta(seconds=AB_BUS_FAULT_DEBOUNCE_SECONDS + 1))
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert entry.runtime_data.data["ab_bus_fault"] is True
        state = hass.states.get(ENTITY_ID)
        assert state.state == "on"


async def test_binary_sensor_reflects_forced_coordinator_data_verbatim(hass, fake_clients):
    """Directly proves the entity is a pure reflection of coordinator.data rather than
    recomputing: overwrite coordinator.data with ab_bus_fault True (regardless of
    error_code) and confirm the entity follows that dict, not its own logic."""
    entry = await _setup(hass, fake_clients)
    coordinator = entry.runtime_data

    forced_data = dict(coordinator.data)
    forced_data["ab_bus_fault"] = True
    coordinator.data = forced_data
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
