"""Coordinator / error-handling tests.

Covers: happy-path data contract, ConfigEntryNotReady vs read-timeout (two
distinct failure paths per CLAUDE.md), the register-11==65535 debounce (up to
~17s of legitimate transient readings after power-up per the AB64's internal
12s comms timeout + 5s LED warm-up), and advanced-block independence.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    AB_BUS_FAULT_DEBOUNCE_SECONDS,
    ADV_INDOOR_START,
    ADV_NO_VALUE,
    ADV_OUTDOOR_START,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DOMAIN,
    MAX_CONSECUTIVE_READ_FAILURES,
    REG_SW_VERSION,
)
from custom_components.carr_ab64.coordinator import AB64Coordinator

from tests.conftest import (
    HAPPY_BASIC_REGS,
    TEST_HOST,
    TEST_NAME,
    TEST_PORT,
    TEST_UNIT_ID,
    FakeModbusClient,
    seed_happy_path,
)


def _make_entry(*, options: dict | None = None, unit_id: int = TEST_UNIT_ID) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: unit_id},
        options=options or {},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{unit_id}",
        title=TEST_NAME,
    )


async def test_happy_path_data_contract(hass, fake_clients):
    """Case 5: confirmed real bring-up values decode to the locked data contract."""
    seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    data = entry.runtime_data.data
    assert data == {
        "on_off": 1,
        "mode": 0,
        "fan_speed": 2,
        "swing": 0,
        "set_temp": 22,
        "error_code": 0,
        "ab_bus_fault": False,
        "advanced": {},
    }


async def test_tcp_connect_failure_is_config_entry_not_ready(hass, fake_clients, caplog):
    """Case 6: TCP connect itself fails -> ConfigEntryNotReady, distinct from case 7."""
    client = FakeModbusClient(TEST_HOST, TEST_PORT)
    client.fail_connect = True
    fake_clients[(TEST_HOST, TEST_PORT)] = client

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state == ConfigEntryState.SETUP_RETRY
    assert "cannot connect" in caplog.text.lower() or "unable to connect" in caplog.text.lower()


async def test_read_timeout_after_connect_is_distinct_from_connect_failure(
    hass, fake_clients, caplog
):
    """Case 7: TCP connects fine but the first register read times out.

    Also ends up ConfigEntryNotReady (HA's built-in behavior when first refresh
    fails), but the log text must differ from the cannot-connect case so the two
    layers (network vs addressing) aren't collapsed into one generic message.
    """
    client = seed_happy_path(fake_clients)
    client.fail_read_at(TEST_UNIT_ID, 0)

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state == ConfigEntryState.SETUP_RETRY
    assert "simulated read failure" in caplog.text or "read" in caplog.text.lower()
    assert "unable to connect" not in caplog.text.lower()


# --- workstream C (unitstep topic, 2026-08-05): tolerate a run of consecutive
# basic-block read failures instead of going unavailable on one dropped frame —
# this is the mechanism that makes MIN_SCAN_INTERVAL == 1 usable (item 16). -------


async def test_two_consecutive_misses_stay_available_third_goes_unavailable(hass, fake_clients):
    """Item 16, bullets 1+2: one good poll, then two consecutive basic-block read
    failures — the climate entity must stay available and keep showing the last
    good reading (not go blank), since MAX_CONSECUTIVE_READ_FAILURES == 3. The
    3rd consecutive miss crosses the threshold: last_update_success must flip
    False and the entity must go unavailable."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data["set_temp"] == 22

    client.fail_read_at(TEST_UNIT_ID, 0)

    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data["set_temp"] == 22  # still last known value, not blank
    state = hass.states.get("climate.living_room_ac")
    assert state.state != "unavailable"

    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data["set_temp"] == 22
    state = hass.states.get("climate.living_room_ac")
    assert state.state != "unavailable"

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    state = hass.states.get("climate.living_room_ac")
    assert state.state == "unavailable"


async def test_consecutive_failure_counter_resets_on_success_not_accumulated(
    hass, fake_clients
):
    """Item 16, bullet 3: 2 misses, then a success, then 2 more misses must NOT
    add up to a "4th miss" unavailable — the counter has to reset to 0 on any
    successful poll, not just decrement or keep climbing across an intervening
    success."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    client.fail_read_at(TEST_UNIT_ID, 0)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True  # 2 misses, still under threshold

    client.clear_fail_read_at(TEST_UNIT_ID)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True

    client.fail_read_at(TEST_UNIT_ID, 0)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True, (
        "counter must have reset at the success in between, not accumulated to 4"
    )


async def test_first_poll_failure_still_raises_config_entry_not_ready(hass, fake_clients):
    """Item 16, bullet 4 — the safety rail on the tolerance mechanism above:
    coordinator.data is None during the very first refresh
    (async_config_entry_first_refresh), so the "tolerate N misses" branch must
    never apply there — swallowing the very first failure would let an entry
    finish setup having never successfully read anything, which is worse than
    the flakiness this feature exists to smooth over. This is the same guarantee
    as the pre-existing test_read_timeout_after_connect_is_distinct_from_
    connect_failure (case 7) above; kept as its own named test so this specific
    coupling to the consecutive-failures feature is pinned under its own name
    rather than relying on someone remembering case 7 also covers it."""
    client = seed_happy_path(fake_clients)
    client.fail_read_at(TEST_UNIT_ID, 0)

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state == ConfigEntryState.SETUP_RETRY


async def test_single_poll_65535_does_not_flag_fault_yet(hass, fake_clients):
    """Case 8: one poll at 65535 must NOT be a fault — could be the ~17s legitimate
    post-power-up transient (5s LED warm-up + 12s AB64-internal AC comms timeout)."""
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = 65535
    seed_happy_path(fake_clients, basic_regs=regs)

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.data["error_code"] == 65535
    assert entry.runtime_data.data["ab_bus_fault"] is False


async def test_65535_past_debounce_threshold_flags_fault(hass, fake_clients):
    """Case 9: 65535 held continuously past AB_BUS_FAULT_DEBOUNCE_SECONDS -> fault True."""
    regs = list(HAPPY_BASIC_REGS)
    regs[11] = 65535

    with freeze_time("2026-01-01 00:00:00") as frozen:
        seed_happy_path(fake_clients, basic_regs=regs)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.data["ab_bus_fault"] is False

        frozen.tick(timedelta(seconds=AB_BUS_FAULT_DEBOUNCE_SECONDS - 1))
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data["ab_bus_fault"] is False, (
            "must still be False just under the debounce threshold"
        )

        frozen.tick(timedelta(seconds=2))
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data["ab_bus_fault"] is True


async def test_65535_timer_resets_when_value_returns_to_normal(hass, fake_clients):
    """Case 10: a normal reading in between must reset the debounce timer, not just
    delay it — the next 65535 has to start counting from zero again."""
    fault_regs = list(HAPPY_BASIC_REGS)
    fault_regs[11] = 65535

    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients, basic_regs=fault_regs)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Nearly at the threshold...
        frozen.tick(timedelta(seconds=AB_BUS_FAULT_DEBOUNCE_SECONDS - 1))
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data["ab_bus_fault"] is False

        # ...then one normal poll resets the clock.
        client.set_registers(TEST_UNIT_ID, 0, HAPPY_BASIC_REGS)
        frozen.tick(timedelta(seconds=1))
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data["ab_bus_fault"] is False
        assert entry.runtime_data.data["error_code"] == 0

        # Fault again, but less than the full debounce window since the reset.
        client.set_registers(TEST_UNIT_ID, 0, fault_regs)
        frozen.tick(timedelta(seconds=AB_BUS_FAULT_DEBOUNCE_SECONDS - 2))
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data["ab_bus_fault"] is False, (
            "timer should have restarted at the last normal reading, not stayed "
            "primed from the first fault streak"
        )


async def test_advanced_block_failure_is_isolated_and_does_not_raise_update_failed(
    hass, fake_clients
):
    """Case 11: advanced telemetry opted in but the indoor block register range isn't
    actually present on this unit -> only those fields go None; climate/error_code
    (basic block) keep working and the coordinator does not raise UpdateFailed."""
    client = seed_happy_path(fake_clients)
    client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)

    entry = _make_entry(options={CONF_ENABLE_ADVANCED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    data = entry.runtime_data.data
    assert data["on_off"] == 1
    assert data["error_code"] == 0
    assert data["advanced"]["indoor_temp"] is None
    assert data["advanced"]["indoor_coil_temp_tcj"] is None
    # Outdoor block was readable -> its fields must have real values, not None.
    assert data["advanced"]["outdoor_evaporator_temp_te"] is not None
    assert entry.runtime_data.last_update_success is True


# --- M2: two's complement decode + 0xFFFF "no value" sentinel ---------------------


class TestDecodeAdvancedValuePure:
    """Pure-Python unit tests of AB64Coordinator._decode_advanced_value — no HA
    setup needed, mirrors the verification style in done/integration-core-fix.md."""

    def test_negative_five_two_complement(self):
        assert AB64Coordinator._decode_advanced_value("outdoor_evaporator_temp_te", 65531) == -5

    def test_negative_two_two_complement(self):
        assert AB64Coordinator._decode_advanced_value("indoor_temp", 65534) == -2

    def test_positive_temperature_untouched(self):
        assert AB64Coordinator._decode_advanced_value("outdoor_temp_to", 25) == 25

    def test_non_temperature_field_large_raw_not_decoded_as_negative(self):
        """Case 7: 40000 > 32767 would look negative if two's-complement-decoded,
        but rpm/current/timer fields must never go negative -> stays 40000."""
        for field in ("compressor_rpm", "filter_sign_timer", "compressor_current",
                      "indoor_fan_rpm", "lowest_fan_rpm"):
            assert AB64Coordinator._decode_advanced_value(field, 40000) == 40000, field

    def test_sentinel_0xffff_is_none_for_every_field_including_non_temperature(self):
        """Case 8 (pure part): 0xFFFF -> None regardless of field kind."""
        assert AB64Coordinator._decode_advanced_value("indoor_temp", ADV_NO_VALUE) is None
        assert AB64Coordinator._decode_advanced_value("compressor_rpm", ADV_NO_VALUE) is None
        assert AB64Coordinator._decode_advanced_value("filter_sign_timer", ADV_NO_VALUE) is None


async def test_negative_temperature_reflected_in_coordinator_data(hass, fake_clients):
    """Case 6 (integration level): a real advanced-block read returning 65531 for a
    temperature field ends up as -5 in coordinator.data, not the raw 65531."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, ADV_OUTDOOR_START, [65531, 0, 0, 0, 0, 0, 0, 0, 0])

    entry = _make_entry(options={CONF_ENABLE_ADVANCED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.data["advanced"]["outdoor_evaporator_temp_te"] == -5


async def test_sentinel_field_goes_unavailable_while_others_stay_normal(hass, fake_clients):
    """Case 8 (integration level): 0xFFFF on one advanced field -> only that
    sensor's entity state is unavailable; entity for a sibling field with a real
    value stays normal."""
    client = seed_happy_path(fake_clients)
    client.set_registers(
        TEST_UNIT_ID, ADV_OUTDOOR_START, [65531, ADV_NO_VALUE, 0, 0, 0, 0, 0, 0, 0]
    )

    entry = _make_entry(options={CONF_ENABLE_ADVANCED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.data["advanced"]["outdoor_evaporator_temp_te"] == -5
    assert entry.runtime_data.data["advanced"]["outdoor_temp_to"] is None

    ok_state = hass.states.get("sensor.living_room_ac_outdoor_evaporator_temperature_te")
    none_state = hass.states.get("sensor.living_room_ac_outdoor_temperature_to")
    assert ok_state.state == "-5"
    assert none_state.state == "unavailable"


# --- m1: hub refcount must not leak if forward_entry_setups fails after connect --


async def test_hub_released_when_forward_entry_setups_fails(hass, fake_clients):
    """m1: __init__.py moved `entry.runtime_data = ...` and
    `async_forward_entry_setups` into the same try/except as the first refresh —
    if forward_entry_setups raises, the refcount bumped by async_acquire_hub()
    earlier in setup must still be rolled back, or the shared client for this
    (host, port) never closes even after every entry using it is removed."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", side_effect=RuntimeError("boom")
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    domain_data = hass.data.get(DOMAIN, {})
    assert (TEST_HOST, TEST_PORT) not in domain_data.get("hubs", {}), (
        "hub entry must be gone (refcount rolled back to 0), not leaked"
    )
    assert client.close_calls == 1


async def test_setup_succeeds_with_sw_version_none_when_sw_version_read_fails_normally(
    hass, fake_clients
):
    """Sibling of the m1 test above, guarding the *other* direction: the inner
    try/except around the sw_version read in async_setup_entry is deliberately
    narrower than the outer one (it only catches AB64ReadError/
    ConnectionException). A normal read failure there — timeout or error
    response, exactly what a unit that doesn't expose register 50 would produce
    — must still degrade to sw_version = None with setup succeeding, not abort
    the whole entry. Written alongside the RuntimeError test below because
    widening the outer except to fix m1 is exactly the kind of change that could
    accidentally swallow this distinction if the inner try/except got flattened
    away instead of kept nested."""
    client = seed_happy_path(fake_clients)
    client.fail_read_at(TEST_UNIT_ID, REG_SW_VERSION)
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.sw_version is None


async def test_hub_released_when_sw_version_read_raises_unexpected_error(
    hass, fake_clients, monkeypatch
):
    """m1, the actual finding: `async_acquire_hub()` bumps refcount, but (before
    this fix) only the try/except around
    `async_config_entry_first_refresh`/`async_forward_entry_setups` rolled it
    back — the sw_version read sat in its own narrower try/except just above that
    only catches (AB64ReadError, ConnectionException). Anything else raised
    there used to escape async_setup_entry entirely, past the rollback.

    hub.py:84 has a real RuntimeError for exactly this: if none of pymodbus's
    known unit-id kwarg names (`device_id`/`slave`/`unit`) match the installed
    client's signature. Trigger it for real — not a fabricated exception — by
    clearing the candidate list so _resolve_unit_kwarg's own code takes that
    branch, simulating an incompatible future pymodbus version."""
    client = seed_happy_path(fake_clients)
    monkeypatch.setattr("custom_components.carr_ab64.hub._UNIT_KWARG_CANDIDATES", ())
    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    domain_data = hass.data.get(DOMAIN, {})
    assert (TEST_HOST, TEST_PORT) not in domain_data.get("hubs", {}), (
        "hub entry must be gone (refcount rolled back to 0), not leaked"
    )
    assert client.close_calls == 1


async def test_hub_released_when_coordinator_construction_raises(hass, fake_clients):
    """m1 follow-up: `coordinator = AB64Coordinator(hass, entry, hub)` was moved
    inside the same outer try/except as the sw_version read (see the comment in
    __init__.py) specifically because coordinator construction itself can raise
    — e.g. a `TypeError` out of `timedelta(seconds=scan_interval)` in
    AB64Coordinator.__init__ if a non-numeric scan_interval ever reaches
    entry.options (bad migration / hand-edited .storage). Before that move, a
    failure here escaped async_setup_entry without rolling back the refcount
    bumped by async_acquire_hub() just above — the same leak class as the
    original m1 finding, one line earlier.

    A mutation test (reviewer, review/portdefault-review.md m-1) found this
    specific line had no test pinning it: reverting just the "coordinator inside
    try" move left all 118 tests green. This is that missing pin — verified by
    moving `coordinator = AB64Coordinator(...)` back outside the try on a
    disposable scratch copy of this package and confirming this test alone then
    fails (see done/qa-portdefault-m1.md for the transcript); the real
    custom_components/ tree was never touched to do that check.

    The other justification once floated for putting this line inside the try —
    a `KeyError` from `entry.data[CONF_UNIT_ID]` if that key is ever missing —
    doesn't actually apply: `__init__.py`'s `unit_id = entry.data[CONF_UNIT_ID]`
    runs *before* `async_acquire_hub()` is even called, so a missing key blows up
    before there's any refcount to leak in the first place. Only the
    scan_interval/TypeError path is reachable *after* the hub is acquired, which
    is what makes it the one worth a regression test."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry(options={CONF_SCAN_INTERVAL: "abc"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    domain_data = hass.data.get(DOMAIN, {})
    assert (TEST_HOST, TEST_PORT) not in domain_data.get("hubs", {}), (
        "hub entry must be gone (refcount rolled back to 0), not leaked"
    )
    assert client.close_calls == 1


# --- m-QA1 group 1: result.isError() branches (advanced-block side) ---------------


async def test_advanced_block_iserror_response_is_isolated_like_the_exception_path(
    hass, fake_clients
):
    """A Modbus exception *response* (isError() True, no exception raised — the
    IllegalDataAddress case reviewer flagged as common on real hardware for
    registers that don't exist on a given unit) must degrade the same way an
    outright ModbusException does: only that block's fields go None, coordinator
    does not raise UpdateFailed. Mirrors
    test_advanced_block_failure_is_isolated_and_does_not_raise_update_failed,
    which only covered the raised-exception path."""
    client = seed_happy_path(fake_clients)
    client.error_response_at_read(TEST_UNIT_ID, ADV_INDOOR_START)

    entry = _make_entry(options={CONF_ENABLE_ADVANCED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    data = entry.runtime_data.data
    assert data["on_off"] == 1
    assert data["advanced"]["indoor_temp"] is None
    assert data["advanced"]["outdoor_evaporator_temp_te"] is not None
    assert entry.runtime_data.last_update_success is True


# --- m-QA1 group 2: write failure (raised exception path) -> HomeAssistantError ---


async def test_write_failure_raises_homeassistant_error(hass, fake_clients):
    """coordinator.py:172-173 — AB64WriteError/ConnectionException from a write
    must surface to the caller as HomeAssistantError. `FakeModbusClient.fail_write`
    has existed since qa-core but no test had ever set it until now."""
    from homeassistant.exceptions import HomeAssistantError

    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.fail_write = True
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.async_write(4, 22)
