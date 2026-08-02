"""Sensor platform tests: error_code decoding + advanced sensor registry defaults."""
from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    AB_BUS_ERROR_VALUE,
    CONF_ENABLE_ADVANCED,
    CONF_UNIT_ID,
    DOMAIN,
    ERROR_CODES,
)
from custom_components.carr_ab64.sensor import ADV_SENSOR_META

from tests.conftest import HAPPY_BASIC_REGS, TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path

ERROR_ENTITY_ID = "sensor.living_room_ac_error_code"


async def _setup(hass, fake_clients, *, basic_regs=None, options=None):
    seed_happy_path(fake_clients, basic_regs=basic_regs)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        options=options or {},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_known_error_code_decodes_to_table_entry(hass, fake_clients):
    """Case 28: code 67 -> E03 + matching description from const.py's ERROR_CODES."""
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = 67
    await _setup(hass, fake_clients, basic_regs=regs)

    state = hass.states.get(ERROR_ENTITY_ID)
    assert int(state.state) == 67
    expected_remote_code, expected_description, expected_category = ERROR_CODES[67]
    assert state.attributes["remote_code"] == expected_remote_code == "E03"
    assert state.attributes["description"] == expected_description
    assert state.attributes["category"] == expected_category


async def test_unknown_error_code_does_not_crash(hass, fake_clients):
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = 999  # not in ERROR_CODES and not the 65535 AB-bus sentinel
    await _setup(hass, fake_clients, basic_regs=regs)

    state = hass.states.get(ERROR_ENTITY_ID)
    assert int(state.state) == 999
    assert state.attributes["description"] == "Unknown error code"


async def test_65535_gets_ab_bus_link_attributes_not_error_table_lookup(hass, fake_clients):
    """65535 is a distinct fault class (AB64 lost its AB Bus link) — must not be
    looked up in ERROR_CODES, which deliberately excludes it."""
    assert AB_BUS_ERROR_VALUE not in ERROR_CODES
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = AB_BUS_ERROR_VALUE
    await _setup(hass, fake_clients, basic_regs=regs)

    state = hass.states.get(ERROR_ENTITY_ID)
    assert int(state.state) == AB_BUS_ERROR_VALUE
    assert state.attributes["category"] == "ab_bus_link"


async def test_advanced_sensors_have_correct_enabled_default_split(hass, fake_clients):
    """Temperature-ish fields default enabled; rpm/current/filter_timer default
    disabled — an integration-engineer judgment call, not spec'd in plan-core.md,
    so pin it down explicitly here."""
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: True})
    registry = er.async_get(hass)

    for field_key, meta in ADV_SENSOR_META.items():
        unique_id = f"{entry.entry_id}_{field_key}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id is not None, f"no entity registered for unique_id {unique_id}"
        reg_entry = registry.async_get(entity_id)
        is_enabled = reg_entry.disabled_by is None
        assert is_enabled == meta.enabled_default, (
            f"{field_key}: expected enabled_default={meta.enabled_default}, "
            f"registry disabled_by={reg_entry.disabled_by}"
        )


def test_filter_sign_timer_has_no_state_class():
    """m7: unit/meaning of this register (hours? cycle count?) is unknown, so HA
    must not build a long-term statistic for it that can't be interpreted."""
    assert ADV_SENSOR_META["filter_sign_timer"].state_class is None
