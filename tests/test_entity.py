"""AB64Entity's shared DeviceInfo (entity.py) — model/model_id split introduced in
integration-core-fix6 (planner's choice C from fix5's device-info naming review):
`model` states the relationship ("this is an AC, controlled via an AB64") instead
of naming the interface box, which used to make the HA device page read as if the
device itself were an AB64 box rather than the air conditioner it represents.
`model_id` keeps the exact interface box identifier for debugging/support.
"""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import CONF_UNIT_ID, DOMAIN

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path


async def test_device_info_model_states_the_relationship_not_the_box(hass, fake_clients):
    seed_happy_path(fake_clients)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.manufacturer == "Carrier-Toshiba"
    assert device.model == "Air conditioner (via AB64 Modbus interface)"
    assert device.model_id == "CARR-INTMBSLC-AB64 (Intesis INMBSTOS001R000)"
    assert device.name == TEST_NAME
