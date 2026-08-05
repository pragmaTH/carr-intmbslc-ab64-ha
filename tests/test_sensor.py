"""Sensor platform tests: error_code decoding + advanced sensor registry defaults."""
from __future__ import annotations

from homeassistant.const import REVOLUTIONS_PER_MINUTE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    AB_BUS_ERROR_VALUE,
    ADV_OUTDOOR_START,
    CONF_ENABLE_ADVANCED,
    CONF_UNIT_ID,
    DOMAIN,
    ERROR_CODES,
    REVOLUTIONS_PER_SECOND,
)
from custom_components.carr_ab64.sensor import ADV_SENSOR_META, AB64AdvancedSensor

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


# --- telemetry topic (2026-08-05): group A case 2 — reading != creating an entity


async def test_no_advanced_sensor_entities_created_when_disabled_even_though_indoor_is_read(
    hass, fake_clients
):
    """Item 2: "reading a register" and "creating an entity for it" are two
    separate things — this test is what stops a future refactor from quietly
    merging them (e.g. "we're already reading indoor_temp, might as well always
    show the sensor too"). With CONF_ENABLE_ADVANCED off: (a) the indoor block
    IS being read — coordinator.data proves it has a real value — but (b) not
    one of the 12 advanced sensor entities (indoor or outdoor) exists in the
    entity registry, checked by unique_id rather than guessing entity_id slugs."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, 4012, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: False})

    assert entry.runtime_data.data["advanced"]["indoor_temp"] == 24, (
        "sanity: the block really was read, not just defaulted"
    )

    registry = er.async_get(hass)
    for field_key in ADV_SENSOR_META:
        unique_id = f"{entry.entry_id}_{field_key}"
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None, (
            f"{field_key}: no sensor entity should exist while advanced is disabled"
        )


# --- telemetry topic (2026-08-05): group C — unit regression guards -------------


def test_compressor_current_has_no_device_class_or_unit():
    """Item 11: compressor_current previously carried
    SensorDeviceClass.CURRENT + unit "A" — HA then treats the raw register as a
    real current reading. A ramp test on real hardware (Carrier 38TGV0391A3,
    3-phase, ~11 kW rated — plan-telemetry.md) measured this register at 29-65
    while the discharge pipe (TD) stayed near 80°C the whole time: 29-65 A on 3
    phases at that heat output would mean 17-38 kW of *input* power from an
    11 kW-rated unit, which doesn't add up. The scale is wrong, but with data
    from only one unit there's no way to derive a correct divisor, and guessing
    one (e.g. /10) would bake an unverifiable assumption into every user's card
    — so device_class and unit are dropped entirely rather than kept wrong."""
    meta = ADV_SENSOR_META["compressor_current"]
    assert meta.device_class is None
    assert meta.unit is None


def test_compressor_rpm_unit_is_rps_not_rpm():
    """Item 12: the same ramp test measured compressor_rpm at 23-58 the whole
    time, including under high load with the discharge pipe near 80°C — a
    compressor spinning at 23-58 *rpm* (practically stopped) cannot produce
    that heat. Read as rps (23-58 revolutions per second, i.e. roughly
    1,400-3,500 rpm) the numbers become physically ordinary for an inverter
    compressor under load. No scaling is applied to get there — see
    test_no_scaling_applied_to_compressor_values below — only the declared
    unit changes."""
    meta = ADV_SENSOR_META["compressor_rpm"]
    assert meta.unit == REVOLUTIONS_PER_SECOND == "rps"
    assert meta.device_class is None


def test_fan_speeds_stay_rpm_not_rps():
    """Item 13: indoor_fan_rpm and lowest_fan_rpm are deliberately NOT touched by
    the compressor_rpm -> rps fix above, even though both are "speed" fields
    read from the same advanced blocks. Physics rules out rps for fans the same
    way it ruled out rpm for the compressor: real fan readings in this dataset
    run into the hundreds (e.g. lowest outdoor fan at 860-1000, plan-
    telemetry.md's ramp test) — read as rps that would be 566 rps = 34,000 rpm,
    an impossible fan speed. This test exists specifically to stop a future
    "make all the speed fields consistent" cleanup from lumping fans in with
    the compressor fix."""
    assert ADV_SENSOR_META["indoor_fan_rpm"].unit == REVOLUTIONS_PER_MINUTE
    assert ADV_SENSOR_META["lowest_fan_rpm"].unit == REVOLUTIONS_PER_MINUTE


async def test_no_scaling_applied_to_compressor_values(hass, fake_clients):
    """Item 14: end-to-end regression guard — asserts the actual sensor entity's
    native_value (not just coordinator.data) equals the raw register value
    exactly, for both compressor fields. Catches a future division/
    multiplication introduced ANYWHERE in the pipeline (coordinator decode,
    the sensor's native_value, or a wrapper added later) — not just at the
    meta-config level checked by the two tests above. Uses the exact raw
    values from the real ramp test in plan-telemetry.md's idle row
    (current=29, rpm=58) rather than round numbers, so a "looks right by
    coincidence" scaling bug (e.g. an accidental /1) is less likely to slip
    past unnoticed. compressor_current is offset 5 and compressor_rpm is
    offset 7 within the 9-register outdoor block starting at ADV_OUTDOOR_START
    (const.py: 4415 and 4417, both minus 4410)."""
    client = seed_happy_path(fake_clients)
    client.set_registers(
        TEST_UNIT_ID, ADV_OUTDOOR_START, [0, 0, 0, 0, 0, 29, 0, 58, 0]
    )
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: True})

    assert entry.runtime_data.data["advanced"]["compressor_current"] == 29
    assert entry.runtime_data.data["advanced"]["compressor_rpm"] == 58

    current_sensor = AB64AdvancedSensor(
        entry.runtime_data, "compressor_current", ADV_SENSOR_META["compressor_current"]
    )
    rpm_sensor = AB64AdvancedSensor(
        entry.runtime_data, "compressor_rpm", ADV_SENSOR_META["compressor_rpm"]
    )
    assert current_sensor.native_value == 29
    assert rpm_sensor.native_value == 58
