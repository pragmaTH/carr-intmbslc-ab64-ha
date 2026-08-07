"""Options flow tests: poll interval + advanced telemetry opt-in."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    ADV_INDOOR_FIELDS,
    ADV_OUTDOOR_FIELDS,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path

TOTAL_ADVANCED_SENSORS = len(ADV_INDOOR_FIELDS) + len(ADV_OUTDOOR_FIELDS)


def _make_entry(*, options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        options=options or {},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )


async def _setup_entry(hass, fake_clients, **kwargs) -> MockConfigEntry:
    seed_happy_path(fake_clients)
    entry = _make_entry(**kwargs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_default_scan_interval(hass, fake_clients):
    """Case 21 (renamed for v0.1.8, was test_default_scan_interval_is_30): the
    literal 5 here is intentional, not just a mirror of DEFAULT_SCAN_INTERVAL —
    it pins the bus-math-chosen value from plan-poll5s.md so a future accidental
    edit to the constant (e.g. reverting to 30) fails this test even though the
    schema-vs-constant equality alone wouldn't catch that."""
    entry = await _setup_entry(hass, fake_clients)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["data_schema"]({})[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL == 5


async def test_scan_interval_below_minimum_is_validation_error_not_clamped(hass, fake_clients):
    """Case 22 (updated 2026-08-05, workstream C item 15): MIN_SCAN_INTERVAL was
    lowered from 10 to 1 so a user can poll fast enough to measure how long a
    climate command actually takes to reflect back (they explicitly rejected
    optimistic state — see plan-unitstep.md workstream C). 0 -> rejected with an
    explicit error, entry.options untouched (must NOT silently clamp); 1 ->
    accepted (previously invalid under the old floor of 10, now the floor
    itself)."""
    entry = await _setup_entry(hass, fake_clients)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 0, CONF_ENABLE_ADVANCED: False}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SCAN_INTERVAL: "scan_interval_too_low"}
    assert CONF_SCAN_INTERVAL not in entry.options

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 1, CONF_ENABLE_ADVANCED: False}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 1


async def test_advanced_telemetry_off_by_default_creates_no_advanced_entities(
    hass, fake_clients
):
    """Case 23 (part 1), updated for the `roomtemp` topic (2026-08-05): opt-in
    default False now creates exactly ONE advanced-group sensor entity —
    indoor_temp, which sensor.py creates unconditionally regardless of
    CONF_ENABLE_ADVANCED (see ADV_SENSOR_META["indoor_temp"]'s comment in
    sensor.py) — not zero. The other 11 fields must still be absent; the
    dedicated indoor_temp-vs-the-other-11 breakdown (unique_id-based, not this
    unique_id-substring filter) lives in test_sensor.py's
    test_indoor_temp_sensor_exists_but_other_11_dont_when_advanced_disabled."""
    await _setup_entry(hass, fake_clients)

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    advanced_entities = [
        e
        for e in registry.entities.values()
        if e.domain == "sensor" and "error_code" not in e.unique_id
    ]
    assert len(advanced_entities) == 1
    assert advanced_entities[0].unique_id.endswith("_indoor_temp")


async def test_enabling_advanced_telemetry_and_reload_creates_all_entities(hass, fake_clients):
    """Case 23 (part 2): enabling + reload creates every advanced sensor in const.py."""
    entry = await _setup_entry(hass, fake_clients)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_ENABLE_ADVANCED: True}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # options update listener triggers a reload

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    advanced_entities = [
        e for e in registry.entities.values() if e.domain == "sensor" and "error_code" not in e.unique_id
    ]
    assert len(advanced_entities) == TOTAL_ADVANCED_SENSORS


async def test_scan_interval_change_updates_coordinator_update_interval(hass, fake_clients):
    """Case 24. The pre-change baseline asserts against DEFAULT_SCAN_INTERVAL
    (not a hardcoded 30, per qa-poll5s.md's line-125 flag) — this entry is set
    up with empty options via _setup_entry(), so its starting update_interval
    IS whatever the default currently is; hardcoding 30 here would have made
    this test silently rely on the pre-0.1.8 default instead of testing what it
    says it tests (that changing the option updates the coordinator)."""
    entry = await _setup_entry(hass, fake_clients)
    assert entry.runtime_data.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60, CONF_ENABLE_ADVANCED: False}
    )
    await hass.async_block_till_done()

    reloaded_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert reloaded_entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_existing_scan_interval_option_survives_default_change(hass, fake_clients):
    """v0.1.8 guard (plan-poll5s.md Acceptance Criteria #3): DEFAULT_SCAN_INTERVAL
    moved 30 -> 5, but an entry that already has an explicit scan_interval saved
    in its options (e.g. a pre-0.1.8 user who never touched the option, so it
    was persisted as 30 by an earlier options-flow save, or anyone who chose 30
    on purpose) must keep exactly that value after the upgrade — there must be
    no migration that rewrites existing options to the new default."""
    entry = await _setup_entry(hass, fake_clients, options={CONF_SCAN_INTERVAL: 30})
    assert entry.runtime_data.update_interval == timedelta(seconds=30)


async def test_empty_options_entry_gets_new_default_scan_interval(hass, fake_clients):
    """v0.1.8 guard, counterpart to the migration test above: an entry with NO
    scan_interval in options (fresh install, or one that's never opened the
    options flow) is the case that's SUPPOSED to change — it must pick up the
    new DEFAULT_SCAN_INTERVAL (5s), confirming the "no migration" guard above
    isn't just masking a coordinator that ignores the new default entirely."""
    entry = await _setup_entry(hass, fake_clients, options={})
    assert entry.runtime_data.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)
    assert entry.runtime_data.update_interval == timedelta(seconds=5)
