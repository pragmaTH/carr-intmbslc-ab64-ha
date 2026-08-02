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
    CONF_UNIT_ID,
    DOMAIN,
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
