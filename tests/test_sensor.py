"""Sensor platform tests: error_code decoding + advanced sensor registry defaults."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import REVOLUTIONS_PER_MINUTE, UnitOfTime
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    AB_BUS_ERROR_VALUE,
    ADV_OUTDOOR_START,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
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


# M-2 (filterhours-review.md): the test above compares registry wiring against
# `meta.enabled_default` itself — it proves meta -> registry plumbing works,
# but is a tautology against the *policy* of which fields get to be
# default-on: if someone flips a field's `enabled_default` in ADV_SENSOR_META,
# the test above stays green regardless, because both sides of its assertion
# move together. Reviewer proved this with mutation (M-E/M-F in
# filterhours-review.md): promoting compressor_rpm or indoor_fan_rpm to
# default-on left the full suite at 190 passed.
# NOTE (qa-filterwording, topic `cleanup` round 3): the task spec for this test
# guessed the current policy was "indoor_temp alone" — that guess is WRONG
# against the real, already-reviewed code (filterhours-review.md's C-3 audited
# every field's enabled_default before/after topic `cleanup` round 2 and found
# none changed). The actual, accepted split — confirmed here by reading
# ADV_SENSOR_META directly, not by trusting the task's parenthetical — is
# "temperature-ish fields default-enabled; rpm/current/filter_timer
# default-disabled," which is also exactly what the tautological test above
# already said in its own docstring before this fix. This list is NOT
# "field creation is gated by CONF_ENABLE_ADVANCED except indoor_temp" (a
# different axis, in sensor.py's async_setup_entry) — it's specifically
# `AdvancedSensorMeta.enabled_default`, i.e. entity_registry_enabled_default,
# which only matters for fields that do get created.
DEFAULT_ENABLED_ADVANCED_FIELDS = frozenset({
    "indoor_temp",
    "indoor_coil_temp_tcj",
    "indoor_coil_temp_tc2",
    "outdoor_evaporator_temp_te",
    "outdoor_temp_to",
    "outdoor_discharge_temp_td",
    "outdoor_suction_temp_ts",
})


def test_only_temperature_fields_default_enabled_among_advanced_sensors():
    """M-2 (filterhours-review.md): compares ADV_SENSOR_META against a policy
    list hardcoded independently here, so — unlike the split test above —
    this one can actually catch a field being promoted to (or demoted from)
    default-on. Reviewer proved the split test above is a tautology by
    mutation (M-E/M-F: promoting compressor_rpm or indoor_fan_rpm to
    default-on left the full suite at 190 passed) — this test is what closes
    that gap.

    The policy this pins down: the 7 temperature-device-class fields default
    enabled; the other 5 (fan/compressor rpm, compressor current, filter
    timer) default disabled, because their values are either noisier or less
    universally meaningful across models than a plain temperature reading —
    an integration-engineer judgment call from topic `core`, not something
    this topic changed (filterhours-review.md C-3: no field's enabled_default
    changed across topic `cleanup` round 2).

    If this test goes RED because someone deliberately moved a field between
    the two groups: do NOT just edit this list to match. Go re-read
    CLAUDE.md's advanced-telemetry decision and confirm the change is an
    intentional, justified policy update — then update this list as part of
    that change, not as a silent side effect of a test failure.
    """
    for field_key, meta in ADV_SENSOR_META.items():
        expected = field_key in DEFAULT_ENABLED_ADVANCED_FIELDS
        assert meta.enabled_default == expected, (
            f"{field_key}: enabled_default={meta.enabled_default}, but the "
            f"hardcoded policy list says it should be {expected} "
            f"(DEFAULT_ENABLED_ADVANCED_FIELDS={sorted(DEFAULT_ENABLED_ADVANCED_FIELDS)})"
        )


def test_filter_sign_timer_has_hours_duration_meta():
    """Unit confirmed 2026-08-07 (two independent measurements — see sensor.py
    comment): 1 tick = 1 hour the unit has been running. The evidence (a ~60min
    tick period, plus the accumulated value matching known daily on-hours) does
    NOT distinguish compressor-runtime from fan-runtime from unit-on-time —
    `climate = cool` for the measurement window doesn't mean the compressor (an
    inverter compressor that can idle at setpoint) span the whole window, so
    "compressor" is not a claim this data supports (filterhours-review.md M-1).
    total_increasing supports the counter resetting on filter cleaning. Still
    opt-in (enabled_default=False)."""
    meta = ADV_SENSOR_META["filter_sign_timer"]
    assert meta.device_class is SensorDeviceClass.DURATION
    assert meta.unit is UnitOfTime.HOURS
    assert meta.state_class is SensorStateClass.TOTAL_INCREASING
    assert meta.enabled_default is False


# --- telemetry topic (2026-08-05): group A case 2 — reading != creating an entity


async def test_indoor_temp_sensor_exists_but_other_11_dont_when_advanced_disabled(
    hass, fake_clients
):
    """Item 1 (`roomtemp` topic, 2026-08-05) — rewritten contract, not deleted:
    this test's ORIGINAL intent (telemetry topic, "reading a register" and
    "creating an entity for it" are two separate things — no advanced sensor
    should leak out while the option is off) is still fully in force for the
    other 11 fields, checked exactly as before by unique_id. What changed is
    that indoor_temp (TA) is now a deliberate, single exception: sensor.py
    creates it unconditionally (see ADV_SENSOR_META["indoor_temp"]'s comment)
    because (a) a return-air thermistor is something every AC needs for its
    own control loop, so TA is the one advanced register we're confident
    exists on every model/firmware — unlike the other 11, which stay opt-in
    specifically because we don't know that yet — and (b) AB64Coordinator
    already reads it every poll unconditionally for climate.
    current_temperature regardless of this entity existing, so creating the
    entity costs zero extra bus traffic — unlike the other 11, which would
    each add a real read if defaulted on the same way.

    Checks all 12 unique_ids explicitly (indoor_temp present, the other 11
    absent) rather than a blanket "nothing exists" or "something exists"
    assertion, so this one test proves both halves of the new contract at
    once and can't be satisfied by a mutation that gets either half wrong."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, 4012, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: False})

    assert entry.runtime_data.data["advanced"]["indoor_temp"] == 24, (
        "sanity: the block really was read, not just defaulted"
    )

    registry = er.async_get(hass)
    indoor_temp_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_indoor_temp"
    )
    assert indoor_temp_entity_id is not None, (
        "indoor_temp must exist even while advanced telemetry is disabled"
    )
    assert hass.states.get(indoor_temp_entity_id).state == "24"

    for field_key in ADV_SENSOR_META:
        if field_key == "indoor_temp":
            continue
        unique_id = f"{entry.entry_id}_{field_key}"
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None, (
            f"{field_key}: no sensor entity should exist while advanced is disabled"
        )


async def test_advanced_enabled_creates_exactly_12_sensors_indoor_temp_not_duplicated(
    hass, fake_clients, caplog
):
    """Item 2 (🔴): with CONF_ENABLE_ADVANCED on, the always-created indoor_temp
    entity and the opt-in loop over ADVANCED_FIELD_KEYS must combine into
    exactly 12 sensor entities — 13 (one duplicate indoor_temp) is the failure
    mode this guards against, e.g. if the opt-in loop's `if key !=
    "indoor_temp"` skip (sensor.py's async_setup_entry) were ever dropped.

    The registry-count/unique_id-set checks below turn out NOT to be enough on
    their own — verified while writing this test, not assumed: when
    async_add_entities() actually receives two AB64AdvancedSensor objects with
    the same unique_id, HA's own entity_platform silently drops the second one
    (logs an ERROR, "does not generate unique IDs ... already exists —
    ignoring", but doesn't raise), so the registry ends up with exactly one
    indoor_temp entry either way and a pure count/uniqueness check can't tell
    the two code paths apart. The caplog assertion below is what actually
    catches the duplicate-construction bug — mutation-checked in
    done/qa-roomtemp.md dropping the skip and confirming this specific
    assertion (not the count) is what fails."""
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: True})

    registry = er.async_get(hass)
    advanced_entities = [
        e
        for e in registry.entities.values()
        if e.domain == "sensor" and "error_code" not in e.unique_id
    ]
    assert len(advanced_entities) == 12

    unique_ids = [e.unique_id for e in advanced_entities]
    assert len(unique_ids) == len(set(unique_ids)), "duplicate unique_id detected"

    indoor_temp_ids = [u for u in unique_ids if u == f"{entry.entry_id}_indoor_temp"]
    assert len(indoor_temp_ids) == 1

    duplicate_warnings = [
        r
        for r in caplog.records
        if r.levelname == "ERROR" and "already exists" in r.message and "indoor_temp" in r.message
    ]
    assert duplicate_warnings == [], (
        "two AB64AdvancedSensor(..., 'indoor_temp', ...) objects were constructed "
        "and handed to async_add_entities — HA silently dropped the extra one, "
        "but it should never have been created in the first place"
    )


def test_indoor_temp_unique_id_format_is_pinned():
    """Item 3: indoor_temp's unique_id must stay exactly
    f"{entry_id}_indoor_temp" — the same format it already had back when it
    was only created via the opt-in loop (telemetry topic). This isn't a
    style preference: HA identifies an entity across restarts/reloads by
    unique_id, not by entity_id or field name. If this ever changed (e.g. to
    disambiguate the always-on vs opt-in code paths with a different key), a
    user who already has this entity would silently get a brand-new entity
    instead — losing history/statistics on the old one and possibly ending up
    with two indoor-temperature entities on their dashboard until they notice
    and clean up the orphan by hand. Sibling of test_advanced_sensors_have_
    correct_enabled_default_split above, which already proves every
    ADV_SENSOR_META key (including indoor_temp) round-trips through this same
    f"{entry_id}_{field_key}" pattern end-to-end; this test pins the exact
    string shape independent of any entry_id, so a change here fails even if
    that broader round-trip test's entry_id happened to still line up."""
    from custom_components.carr_ab64.sensor import AB64AdvancedSensor

    class _FakeEntry:
        entry_id = "abc123"
        title = "Fake AC"

    class _FakeCoordinator:
        entry = _FakeEntry()
        sw_version = None

    sensor = AB64AdvancedSensor(_FakeCoordinator(), "indoor_temp", ADV_SENSOR_META["indoor_temp"])
    assert sensor.unique_id == "abc123_indoor_temp"


async def test_indoor_temp_survives_toggling_advanced_option_twice(hass, fake_clients):
    """Item 4: off -> on -> off (2 reloads via the options update listener) must
    leave indoor_temp as the SAME entity throughout — not removed and
    recreated, which would show up as the entity's `id` (registry-internal,
    stable across reloads for the same unique_id) changing, or transiently as
    zero indoor_temp entities existing at any point in the sequence."""
    entry = await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: False})
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_indoor_temp"

    initial = registry.async_get(
        registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    )
    assert initial is not None

    for enable in (True, False):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_ENABLE_ADVANCED: enable}
        )
        await hass.async_block_till_done()

        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id is not None, f"indoor_temp missing after toggling to {enable}"
        current = registry.async_get(entity_id)
        assert current.id == initial.id, (
            f"indoor_temp got recreated (different registry entry) after toggling to {enable}"
        )


async def test_climate_current_temperature_unaffected_by_indoor_temp_sensor_advanced_off(
    hass, fake_clients
):
    """Item 5 (advanced off): climate.current_temperature reads coordinator.data
    directly (see climate.py) and must keep working identically regardless of
    whether the indoor_temp SENSOR entity exists — the two are independent
    consumers of the same coordinator field. Split into two tests (this one
    and the _advanced_on sibling below) rather than one test looping both
    states on the same hass instance, since a second MockConfigEntry with the
    same unique_id/title would collide with the first entry's already-claimed
    climate.living_room_ac entity_id and stop testing what it claims to."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, 4012, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: False})

    state = hass.states.get("climate.living_room_ac")
    assert state.attributes.get("current_temperature") == 24


async def test_climate_current_temperature_unaffected_by_indoor_temp_sensor_advanced_on(
    hass, fake_clients
):
    """Item 5 (advanced on): sibling of the test above — same guarantee must
    hold with the option enabled and the indoor_temp sensor entity actually
    present."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, 4012, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    await _setup(hass, fake_clients, options={CONF_ENABLE_ADVANCED: True})

    state = hass.states.get("climate.living_room_ac")
    assert state.attributes.get("current_temperature") == 24


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
