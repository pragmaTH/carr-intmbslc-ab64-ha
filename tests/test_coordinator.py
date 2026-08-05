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
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.carr_ab64.const import (
    AB_BUS_FAULT_DEBOUNCE_SECONDS,
    ADV_INDOOR_START,
    ADV_NO_VALUE,
    ADV_OUTDOOR_START,
    BASIC_BLOCK_START,
    CONF_ENABLE_ADVANCED,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DOMAIN,
    MAX_CONSECUTIVE_READ_FAILURES,
    POST_WRITE_REFRESH_DELAY,
    REG_SETPOINT,
    REG_SW_VERSION,
    UNSUPPORTED_BLOCK_FAILURES,
    UNSUPPORTED_BLOCK_RETRY_LADDER,
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
    """Case 5: confirmed real bring-up values decode to the locked data contract.

    `advanced` is no longer always `{}` with CONF_ENABLE_ADVANCED off — the
    telemetry topic (2026-08-05) made the indoor block (TA + 4 siblings) read
    unconditionally every poll, specifically so climate.current_temperature has
    a value from first install (see test_happy_path_data_contract's sibling in
    test_climate.py, test_current_temperature_has_real_value_when_advanced_
    disabled). The outdoor block is still opt-in-only, so `advanced` here holds
    exactly the 5 indoor keys — no outdoor keys — all reading 0 because this
    fixture never seeds ADV_INDOOR_START registers (the fake client's default
    read value)."""
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
        "advanced": {
            "indoor_temp": 0,
            "indoor_coil_temp_tcj": 0,
            "indoor_coil_temp_tc2": 0,
            "indoor_fan_rpm": 0,
            "filter_sign_timer": 0,
        },
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


# --- telemetry topic (2026-08-05): group A — block-read counts ------------------


async def test_advanced_disabled_reads_basic_and_indoor_blocks_only(hass, fake_clients):
    """Item 3: with CONF_ENABLE_ADVANCED off, every poll must read exactly the
    basic block and the indoor advanced block (for current_temperature) — and
    must NOT touch the outdoor block (ADV_OUTDOOR_START/4410) at all. Asserted
    against the addresses the fake client actually saw, not against
    coordinator.data (which can look right even if an extra/wrong read happened
    on the wire)."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    read_log_before = len(client.read_log)
    await entry.runtime_data.async_refresh()
    addresses = {addr for (_, addr, _) in client.read_log[read_log_before:]}
    assert addresses == {BASIC_BLOCK_START, ADV_INDOOR_START}
    assert ADV_OUTDOOR_START not in addresses


async def test_advanced_enabled_reads_all_three_blocks_and_creates_12_sensors(
    hass, fake_clients
):
    """Item 4: with CONF_ENABLE_ADVANCED on, every poll reads basic + indoor +
    outdoor (3 blocks), and all 12 advanced sensor entities (5 indoor + 7
    outdoor — see ADV_INDOOR_FIELDS/ADV_OUTDOOR_FIELDS in const.py) exist in the
    entity registry. The count half is a light restatement of
    test_options_flow.py's TOTAL_ADVANCED_SENSORS check, kept here so this one
    test proves "3 blocks read" and "12 sensors exist" together for the same
    setup rather than relying on two files agreeing by coincidence."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry(options={CONF_ENABLE_ADVANCED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    read_log_before = len(client.read_log)
    await entry.runtime_data.async_refresh()
    addresses = {addr for (_, addr, _) in client.read_log[read_log_before:]}
    assert addresses == {BASIC_BLOCK_START, ADV_INDOOR_START, ADV_OUTDOOR_START}

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    advanced_entities = [
        e
        for e in registry.entities.values()
        if e.domain == "sensor" and "error_code" not in e.unique_id
    ]
    assert len(advanced_entities) == 12


async def test_enabling_advanced_via_options_reload_starts_reading_outdoor_block(
    hass, fake_clients
):
    """Item 5: installed with advanced off (outdoor never read), then opted in
    via options — after the reload this triggers, the outdoor block must start
    being read and all 12 sensors must appear, closing the loop from "off at
    install" to "on via options" rather than just testing each state in
    isolation."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    read_log_before = len(client.read_log)
    await entry.runtime_data.async_refresh()
    addresses_before = {addr for (_, addr, _) in client.read_log[read_log_before:]}
    assert ADV_OUTDOOR_START not in addresses_before

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_ENABLE_ADVANCED: True}
    )
    await hass.async_block_till_done()  # options update listener triggers a reload

    reloaded_entry = hass.config_entries.async_get_entry(entry.entry_id)
    read_log_before2 = len(client.read_log)
    await reloaded_entry.runtime_data.async_refresh()
    addresses_after = {addr for (_, addr, _) in client.read_log[read_log_before2:]}
    assert ADV_OUTDOOR_START in addresses_after

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    advanced_entities = [
        e
        for e in registry.entities.values()
        if e.domain == "sensor" and "error_code" not in e.unique_id
    ]
    assert len(advanced_entities) == 12


# --- telemetry topic (2026-08-05): group B — advanced-block backoff -------------


async def test_advanced_block_two_consecutive_failures_still_retries_on_third_poll(
    hass, fake_clients
):
    """Item 6: 2 consecutive failures on the indoor block must NOT pause it yet —
    UNSUPPORTED_BLOCK_FAILURES is 3, so the 3rd poll must still attempt the
    read (that 3rd failure is what crosses the threshold and triggers the
    pause going forward, not this one)."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
    coordinator = entry.runtime_data
    await coordinator.async_refresh()  # miss 1
    await coordinator.async_refresh()  # miss 2

    read_log_before = len(client.read_log)
    await coordinator.async_refresh()  # miss 3 — still attempted
    reads = client.read_log[read_log_before:]
    assert any(addr == ADV_INDOOR_START for (_, addr, _) in reads), (
        "3rd poll must still attempt the read"
    )


async def test_advanced_block_stops_reading_after_three_consecutive_failures(
    hass, fake_clients
):
    """Item 7: once 3 consecutive failures have happened, the NEXT poll must not
    even attempt the read (no read_start for that address at all — not just a
    read that fails again), and the affected field must read as None."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
    coordinator = entry.runtime_data
    for _ in range(UNSUPPORTED_BLOCK_FAILURES):
        await coordinator.async_refresh()

    assert coordinator.data["advanced"]["indoor_temp"] is None

    read_log_before = len(client.read_log)
    await coordinator.async_refresh()
    reads = client.read_log[read_log_before:]
    assert not any(addr == ADV_INDOOR_START for (_, addr, _) in reads), (
        "block must be paused — no read attempt on this poll"
    )


async def test_advanced_block_retries_after_cooldown_then_resets_on_success(
    hass, fake_clients
):
    """Items 8+9 together (sequential states of one run, not independent setups):
    once paused, the block must stay silent until its cooldown has actually
    elapsed (checked at 1s under the threshold first, so this isn't just
    "eventually retries"), retry exactly once when it's due, and — if that
    retry succeeds — reset ALL of the backoff state (failure counter, retry_at,
    AND the ladder step — see UNSUPPORTED_BLOCK_RETRY_LADDER in const.py) so
    subsequent polls go back to reading every time rather than needing 3 fresh
    failures again to re-pause.

    Updated 2026-08-05 (review M-2): the first trip's cooldown is now
    UNSUPPORTED_BLOCK_RETRY_LADDER[0] == 60s, not a flat 600s — see
    test_backoff_ladder_climbs_60_300_600_then_caps_at_600 below for the
    climbing behavior across repeated failed retries, and
    test_backoff_step_fully_resets_after_success_ladder_restarts_at_60s for the
    m-3 gap this test's "counter reset" assertion alone didn't close (a step
    that failed to reset wouldn't show up as a behavioral difference here,
    since a step-0 and a step-2 block both "read normally" right after a
    success — the difference only appears on the *next* trip)."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
        coordinator = entry.runtime_data
        for _ in range(UNSUPPORTED_BLOCK_FAILURES):
            await coordinator.async_refresh()
        assert coordinator.data["advanced"]["indoor_temp"] is None

        # Not due yet (1s under the first rung, 60s) -> still silent.
        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[0] - 1))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        reads = client.read_log[read_log_before:]
        assert not any(addr == ADV_INDOOR_START for (_, addr, _) in reads), (
            "must not retry before the cooldown has actually elapsed"
        )

        # Now due -> retries once. Fix the underlying failure and give it a
        # distinguishable value so "succeeded" is unambiguous (not just "not None").
        client.clear_fail_read_at(TEST_UNIT_ID)
        client.set_registers(TEST_UNIT_ID, ADV_INDOOR_START, [24, 0, 0, 0, 0, 0, 0, 0, 0])
        frozen.tick(timedelta(seconds=2))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        reads = client.read_log[read_log_before:]
        assert any(addr == ADV_INDOOR_START for (_, addr, _) in reads), (
            "must retry exactly once once the cooldown has elapsed"
        )
        assert coordinator.data["advanced"]["indoor_temp"] == 24

        # m-3: all THREE pieces of backoff state must reset, not just the ones
        # that happen to be externally visible via coordinator.data.
        assert coordinator._block_failures["indoor"] == 0
        assert coordinator._block_retry_at["indoor"] is None
        assert coordinator._block_retry_step["indoor"] == 0

        # Item 9: counter reset by that success -> next poll reads normally again,
        # not gated behind another cooldown wait.
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        reads = client.read_log[read_log_before:]
        assert any(addr == ADV_INDOOR_START for (_, addr, _) in reads), (
            "counter must have reset on the successful retry — normal polling resumed"
        )
        assert coordinator.data["advanced"]["indoor_temp"] == 24


async def test_backoff_ladder_climbs_60_300_600_then_caps_at_600(hass, fake_clients):
    """Item 2 (review M-2): a block that keeps failing every retry must climb
    UNSUPPORTED_BLOCK_RETRY_LADDER (60 -> 300 -> 600) rather than reusing the
    same pause every time, and must cap at the ladder's last rung (600s)
    instead of continuing to grow once it's climbed past the end. Also covers
    m-3 gap #1 directly: each failed retry must schedule a genuinely new
    cooldown, not fall back to reading every poll — checked explicitly at the
    60->300 transition below ("still silent partway through the new wait"),
    and implicitly at every other transition by the same "not due yet" pattern
    used throughout this test."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        # First trip needs 2 more failures (setup's first_refresh was failure #1).
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert coordinator._block_retry_step["indoor"] == 1  # tripped once already

        # --- Rung 1 -> 2: 60s pause, retry fails, next pause becomes 300s -------
        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[0] - 1))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert not any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "must not retry before the 60s rung is actually due"

        frozen.tick(timedelta(seconds=2))  # now past the 60s mark
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "must retry once the 60s rung is due"
        assert coordinator._block_retry_step["indoor"] == 2

        # m-3 gap #1, explicit: mid-way through the NEW (300s) wait, still silent —
        # proves the failed retry set a fresh cooldown, not "read every poll".
        frozen.tick(timedelta(seconds=150))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert not any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "a failed retry must schedule a new wait, not resume reading every poll"

        # --- Rung 2 -> 3: finish the 300s wait, retry fails, next becomes 600s --
        frozen.tick(timedelta(seconds=150))  # total 300s since the previous retry
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "must retry once the 300s rung is due"
        assert coordinator._block_retry_step["indoor"] == 3

        # --- Rung 3: 600s wait, retry fails, must cap at 600s (not grow to 900s) ---
        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[2] - 1))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert not any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "must not retry before the 600s rung is actually due"

        frozen.tick(timedelta(seconds=2))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "must retry once the 600s rung is due"
        # Capped: index clamps to the ladder's last rung instead of indexing past it.
        assert coordinator._block_retry_step["indoor"] == 4

        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[2] - 1))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert not any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "still capped at 600s, not grown to some longer wait"


async def test_backoff_step_fully_resets_after_success_ladder_restarts_at_60s(
    hass, fake_clients
):
    """m-3 gap #2, the sharper version: the "counter reset" assertion in
    test_advanced_block_retries_after_cooldown_then_resets_on_success only
    proves reads resume — that would look identical whether or not the ladder
    STEP specifically reset, since a step-0 and a step-2 block both read
    normally right after a success. The only way to actually distinguish them
    is to trip the block again afterwards and check where the NEW ladder
    starts. Climbs to step 2 (a 300s pause pending), lets a retry succeed, then
    fails 3 fresh times — the resulting pause must be 60s again, not 300s or
    600s, or the mutation this test exists to catch (step never reset) would
    make production hardware endure a resurfaced hiccup's pause growing
    forever across unrelated incidents."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        await coordinator.async_refresh()
        await coordinator.async_refresh()  # 3rd failure -> tripped, step 0 -> 1

        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[0] + 1))
        await coordinator.async_refresh()  # 60s retry fails -> step 1 -> 2
        assert coordinator._block_retry_step["indoor"] == 2

        # Let the pending (300s) retry succeed.
        client.clear_fail_read_at(TEST_UNIT_ID)
        client.set_registers(TEST_UNIT_ID, ADV_INDOOR_START, [24, 0, 0, 0, 0, 0, 0, 0, 0])
        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[1] + 1))
        await coordinator.async_refresh()
        assert coordinator.data["advanced"]["indoor_temp"] == 24
        assert coordinator._block_retry_step["indoor"] == 0, (
            "step must be back to 0 immediately after the success"
        )

        # Fresh trip: 3 new consecutive failures.
        client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()  # 3rd fresh failure -> trips again

        # If step had NOT reset, this would trip at LADDER[2]=600s (or LADDER[1]=300s)
        # instead of LADDER[0]=60s — check the 60s rung specifically.
        frozen.tick(timedelta(seconds=UNSUPPORTED_BLOCK_RETRY_LADDER[0] - 1))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert not any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        )

        frozen.tick(timedelta(seconds=2))
        read_log_before = len(client.read_log)
        await coordinator.async_refresh()
        assert any(
            a == ADV_INDOOR_START for (_, a, _) in client.read_log[read_log_before:]
        ), "the fresh trip must pause only 60s, proving the ladder step really reset"


async def test_basic_block_three_strikes_unaffected_by_advanced_block_backoff_state(
    hass, fake_clients
):
    """Item 10 (🔴): MAX_CONSECUTIVE_READ_FAILURES (basic block, drives
    UpdateFailed/entity-unavailable) and UNSUPPORTED_BLOCK_FAILURES (per
    advanced-block backoff, degrades that block's fields to None) are separate
    counters and must never cross-contaminate. Proves both directions: (1) the
    indoor block failing 3x into backoff must NOT make last_update_success
    False while basic reads keep succeeding; (2) once the basic block itself
    starts failing (with an already-paused advanced block sitting alongside
    it), it still takes exactly MAX_CONSECUTIVE_READ_FAILURES misses — not
    more, not fewer — to flip the entity unavailable."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    # Phase 1: indoor block fails into backoff; basic block keeps succeeding.
    client.fail_read_at(TEST_UNIT_ID, ADV_INDOOR_START)
    for _ in range(UNSUPPORTED_BLOCK_FAILURES):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True, (
        "advanced block backoff must not affect overall update success"
    )
    assert coordinator.data["advanced"]["indoor_temp"] is None

    # Phase 2: now the basic block starts failing too — its own 3-strike
    # tolerance must apply on its own schedule, unaffected by the already-paused
    # advanced block.
    client.fail_read_at(TEST_UNIT_ID, BASIC_BLOCK_START)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False

    state = hass.states.get("climate.living_room_ac")
    assert state.state == "unavailable"


# --- post-write refresh (0.1.7): AB64 takes ~1.7s to reflect a written value, ----
# so the immediate post-write refresh always reads back the pre-write value; a
# single follow-up refresh is scheduled POST_WRITE_REFRESH_DELAY later to pick up
# the settled value without waiting for the next regular poll. --------------------


def _count_basic_block_reads(client, since_index: int = 0) -> int:
    """Count refreshes (not raw read frames) by counting reads at
    BASIC_BLOCK_START specifically — every AB64Coordinator refresh reads that
    address exactly once regardless of how many advanced blocks it also reads,
    so this is a stable proxy for "how many refreshes happened" independent of
    the CONF_ENABLE_ADVANCED state."""
    return sum(
        1 for (_, addr, _) in client.read_log[since_index:] if addr == BASIC_BLOCK_START
    )


async def test_write_triggers_immediate_refresh_then_one_more_after_delay(
    hass, fake_clients
):
    """Item 1: a single write must produce exactly one immediate refresh (the
    existing pre-0.1.7 behavior) and exactly one more refresh
    POST_WRITE_REFRESH_DELAY later — not zero (the whole point of this
    feature), not more than one (see the burst test below)."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        read_log_before = len(client.read_log)
        await coordinator.async_write(REG_SETPOINT, 24)
        await hass.async_block_till_done()
        assert _count_basic_block_reads(client, read_log_before) == 1, (
            "immediate refresh must happen right away"
        )

        frozen.tick(timedelta(seconds=POST_WRITE_REFRESH_DELAY))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert _count_basic_block_reads(client, read_log_before) == 2, (
            "exactly one follow-up refresh must fire at +POST_WRITE_REFRESH_DELAY"
        )


async def test_burst_of_writes_collapses_to_one_follow_up_refresh(hass, fake_clients):
    """Item 2 (🔴): 10 rapid writes (dragging a temperature slider) must leave
    exactly ONE pending follow-up, not 10 — this is the post-write-refresh
    analog of the original core-review finding M3 (rounds `core`/`core-fix`):
    that bug was N rapid setpoint writes each producing their own independent
    refresh with no coalescing, discovered via the same "drag the slider"
    scenario. The fix there was serializing through the hub lock with
    async_refresh() having zero cooldown; the fix here is
    _async_schedule_post_write_refresh() cancelling any previously-pending
    follow-up before scheduling a new one (see its docstring in
    coordinator.py) — a different mechanism guarding against the same class of
    "N user actions -> N-times the bus traffic" bug, so it needs its own
    regression guard rather than assuming M3's fix covers it."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        read_log_before = len(client.read_log)
        for temp in range(18, 28):  # 10 rapid writes
            await coordinator.async_write(REG_SETPOINT, temp)
        await hass.async_block_till_done()
        assert _count_basic_block_reads(client, read_log_before) == 10, (
            "each write still gets its own immediate refresh — only the "
            "follow-up is meant to coalesce"
        )

        after_immediate = len(client.read_log)
        frozen.tick(timedelta(seconds=POST_WRITE_REFRESH_DELAY))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        followup_reads = _count_basic_block_reads(client, after_immediate)
        assert followup_reads == 1, f"expected exactly 1 follow-up refresh, got {followup_reads}"


async def test_pending_follow_up_cancelled_on_unload_no_error(hass, fake_clients, caplog):
    """Item 3 (🔴): unloading the entry while a follow-up is still pending must
    cancel it — a follow-up that fires after unload would call async_refresh()
    on a coordinator whose entry/hub is already torn down. Ticks time past the
    delay AFTER unload and asserts both that no extra read happened and that
    nothing logged an ERROR (a crash inside the async_call_later callback
    would otherwise be silently swallowed by HA's event loop and only show up
    as a log entry, not a raised exception this test could catch directly)."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        await coordinator.async_write(REG_SETPOINT, 24)
        await hass.async_block_till_done()
        assert coordinator._post_write_refresh_unsub is not None, (
            "sanity: a follow-up really is pending before unload"
        )

        read_log_before = len(client.read_log)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        # The direct, unambiguous check: cancellation must have actually run.
        # Read-count/no-ERROR checks alone are NOT enough here — verified while
        # writing this test, not assumed: async_release_hub() (called during
        # unload) closes the shared hub's client, so even an UNCANCELLED
        # follow-up that fires later hits AB64Hub.async_read_holding's "not
        # connected" check and raises ConnectionException — which
        # _async_update_data's own MAX_CONSECUTIVE_READ_FAILURES tolerance (see
        # the workstream-C tests above) then silently absorbs (self.data isn't
        # None yet, so it just returns the last-known data instead of raising),
        # producing neither a new read_log entry nor an ERROR log either way.
        # That resilience layer would otherwise mask exactly the bug this test
        # exists to catch.
        assert coordinator._post_write_refresh_unsub is None, (
            "the pending follow-up must be cancelled synchronously during unload"
        )

        frozen.tick(timedelta(seconds=POST_WRITE_REFRESH_DELAY + 1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

        assert len(client.read_log) == read_log_before, (
            "no refresh should happen after unload"
        )
        assert not any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.parametrize("scan_interval", [1, 2])
async def test_no_follow_up_scheduled_when_scan_interval_leq_delay(
    hass, fake_clients, scan_interval
):
    """Item 4: when update_interval <= POST_WRITE_REFRESH_DELAY, the regular
    poll already lands at or before that point, so scheduling a follow-up
    would just be a redundant extra read — checked at both boundary values (1
    and the boundary itself, 2). Asserted via the coordinator's own
    _post_write_refresh_unsub staying None immediately after the write, not by
    counting reads at t+2s: at scan_interval<=2 the coordinator's own regular
    poll ALSO lands at that exact instant (same timer mechanism,
    async_fire_time_changed fires both), so a read-count check there can't
    distinguish "no follow-up was ever scheduled" from "a follow-up fired at
    the same moment as a coincidental regular poll" — checking the internal
    unsub handle is the only way to test this unambiguously."""
    client = seed_happy_path(fake_clients)
    entry = _make_entry(options={CONF_SCAN_INTERVAL: scan_interval})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    await coordinator.async_write(REG_SETPOINT, 24)
    await hass.async_block_till_done()

    assert coordinator._post_write_refresh_unsub is None, (
        f"no follow-up should be scheduled when scan_interval={scan_interval} "
        f"<= POST_WRITE_REFRESH_DELAY"
    )


async def test_no_optimistic_state_value_changes_only_on_settled_follow_up_read(
    hass, fake_clients
):
    """Item 5 (🔴): proves the mechanism AND the no-optimistic-state prohibition
    in one test. The fake client's write_register() is made to ACK the write
    (matching a real Modbus write confirmation) without actually updating its
    stored register — modeling the AB64's measured lag between accepting a
    write and its own register mirror reflecting it. So: immediate refresh
    must still show the OLD value (22) — proving coordinator.data is never
    set from the value just written, only from a real read — and only the
    follow-up refresh, after the fake "settles" to the new value, may show 24.
    A regression that set coordinator.data optimistically from the written
    value would make the FIRST assertion below fail; this is deliberately
    checked separately from whether the mechanism eventually gets the right
    value at all."""
    with freeze_time("2026-01-01 00:00:00") as frozen:
        client = seed_happy_path(fake_clients)  # set_temp starts at 22
        client.suppress_write_persist(TEST_UNIT_ID, REG_SETPOINT)
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.data["set_temp"] == 22

        await coordinator.async_write(REG_SETPOINT, 24)
        await hass.async_block_till_done()
        assert coordinator.data["set_temp"] == 22, (
            "immediate refresh reads the not-yet-settled register — must NOT "
            "be set optimistically from the value just written"
        )
        state = hass.states.get("climate.living_room_ac")
        assert state.attributes["temperature"] == 22

        # Hardware "catches up" for real before the follow-up read.
        client.set_registers(TEST_UNIT_ID, REG_SETPOINT, [24])
        frozen.tick(timedelta(seconds=POST_WRITE_REFRESH_DELAY))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

        assert coordinator.data["set_temp"] == 24
        state = hass.states.get("climate.living_room_ac")
        assert state.attributes["temperature"] == 24
