"""Options flow tests: poll interval + advanced telemetry opt-in."""
from __future__ import annotations

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


async def test_default_scan_interval_is_30(hass, fake_clients):
    """Case 21."""
    entry = await _setup_entry(hass, fake_clients)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["data_schema"]({})[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL == 30


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
    """Case 24."""
    from datetime import timedelta

    entry = await _setup_entry(hass, fake_clients)
    assert entry.runtime_data.update_interval == timedelta(seconds=30)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60, CONF_ENABLE_ADVANCED: False}
    )
    await hass.async_block_till_done()

    reloaded_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert reloaded_entry.runtime_data.update_interval == timedelta(seconds=60)
