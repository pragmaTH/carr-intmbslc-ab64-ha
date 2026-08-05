"""Climate entity tests: read/write register mapping and temperature range."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.climate import ATTR_HVAC_MODE, DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    ADV_INDOOR_START,
    CONF_ENABLE_ADVANCED,
    CONF_UNIT_ID,
    DOMAIN,
    REG_FAN,
    REG_MODE,
    REG_ON_OFF,
    REG_SETPOINT,
    REG_SWING,
)

from tests.conftest import (
    HAPPY_BASIC_REGS,
    TEST_HOST,
    TEST_NAME,
    TEST_PORT,
    TEST_UNIT_ID,
    seed_happy_path,
)

ENTITY_ID = "climate.living_room_ac"


async def _setup(hass, fake_clients, *, basic_regs=None, options=None):
    client = seed_happy_path(fake_clients, basic_regs=basic_regs)
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
    return entry, client


async def test_read_mapping_hvac_fan_swing_temperature(hass, fake_clients):
    """Case 25 (read side): on_off/mode/fan/swing/set_temp registers -> HA climate attrs."""
    regs = [1, 4, 3, 10, 25, 0, 0, 0, 0, 0, 0, 0]  # on, COOL, HIGH, swing ON, 25C
    await _setup(hass, fake_clients, basic_regs=regs)

    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.COOL
    assert state.attributes["fan_mode"] == "high"
    assert state.attributes["swing_mode"] == "on"
    assert state.attributes["temperature"] == 25


async def test_off_register_reports_hvac_off_regardless_of_mode_register(hass, fake_clients):
    regs = [0, 4, 3, 0, 25, 0, 0, 0, 0, 0, 0, 0]  # on_off=0 but mode register still COOL
    await _setup(hass, fake_clients, basic_regs=regs)
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.OFF


async def test_set_swing_on_writes_10_not_1(hass, fake_clients):
    """Case 25 (write side): the single most important swing gotcha in CLAUDE.md."""
    entry, client = await _setup(hass, fake_clients)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_swing_mode",
        {"entity_id": ENTITY_ID, "swing_mode": "on"},
        blocking=True,
    )
    await hass.async_block_till_done()

    swing_writes = [w for w in client.write_log if w[1] == REG_SWING]
    assert swing_writes[-1] == (TEST_UNIT_ID, REG_SWING, 10)


async def test_set_swing_off_writes_0(hass, fake_clients):
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[1, 4, 3, 10, 25, 0, 0, 0, 0, 0, 0, 0]
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_swing_mode",
        {"entity_id": ENTITY_ID, "swing_mode": "off"},
        blocking=True,
    )
    await hass.async_block_till_done()
    swing_writes = [w for w in client.write_log if w[1] == REG_SWING]
    assert swing_writes[-1] == (TEST_UNIT_ID, REG_SWING, 0)


async def test_set_fan_mode_writes_correct_code(hass, fake_clients):
    entry, client = await _setup(hass, fake_clients)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_fan_mode",
        {"entity_id": ENTITY_ID, "fan_mode": "high"},
        blocking=True,
    )
    await hass.async_block_till_done()
    fan_writes = [w for w in client.write_log if w[1] == REG_FAN]
    assert fan_writes[-1] == (TEST_UNIT_ID, REG_FAN, 3)


async def test_set_temperature_writes_setpoint_register(hass, fake_clients):
    entry, client = await _setup(hass, fake_clients)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 20},
        blocking=True,
    )
    await hass.async_block_till_done()
    setpoint_writes = [w for w in client.write_log if w[1] == REG_SETPOINT]
    assert setpoint_writes[-1] == (TEST_UNIT_ID, REG_SETPOINT, 20)


async def test_turning_on_from_off_writes_on_off_then_mode(hass, fake_clients):
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[0, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {"entity_id": ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    mode_writes = [w for w in client.write_log if w[1] == REG_MODE]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 1)
    assert mode_writes[-1] == (TEST_UNIT_ID, REG_MODE, 4)


async def test_set_temperature_above_max_is_rejected(hass, fake_clients):
    """Case 26: outside 16-32 -> rejected, no register write happens."""
    entry, client = await _setup(hass, fake_clients)
    with pytest.raises((ServiceValidationError, vol.Invalid, HomeAssistantError)):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 40},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert not any(w[1] == REG_SETPOINT and w[2] == 40 for w in client.write_log)


async def test_set_temperature_below_min_is_rejected(hass, fake_clients):
    entry, client = await _setup(hass, fake_clients)
    with pytest.raises((ServiceValidationError, vol.Invalid, HomeAssistantError)):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 10},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert not any(w[1] == REG_SETPOINT and w[2] == 10 for w in client.write_log)


async def test_current_temperature_has_real_value_when_advanced_disabled(hass, fake_clients):
    """Telemetry topic (2026-08-05), group A case 1 — the whole reason this round
    happened: current_temperature must show a real reading from first install,
    without the user ever touching advanced telemetry options. This REVERSES the
    old case-27 assertion here (current_temperature was None with advanced off) —
    that was correct under the pre-2026-08-05 contract, where the indoor block
    (register 4012, TA) was only read when CONF_ENABLE_ADVANCED was on. Since then
    AB64Coordinator reads the indoor block unconditionally every poll (see the
    comment in climate.py's current_temperature and coordinator.py's
    _async_update_data) — only the other 11 advanced fields, and their sensor
    entities, stay opt-in. See test_no_advanced_sensor_entities_created_when_
    disabled_even_though_indoor_is_read below for the "reading a value" vs
    "creating a sensor entity" distinction this reversal depends on."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, ADV_INDOOR_START, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        options={CONF_ENABLE_ADVANCED: False},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.attributes.get("current_temperature") == 24


async def test_current_temperature_reflects_indoor_temp_when_advanced_enabled(
    hass, fake_clients
):
    client = seed_happy_path(fake_clients)
    from custom_components.carr_ab64.const import ADV_INDOOR_START

    client.set_registers(TEST_UNIT_ID, ADV_INDOOR_START, [24, 0, 0, 0, 0, 0, 0, 0, 0])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        options={CONF_ENABLE_ADVANCED: True},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.attributes.get("current_temperature") == 24


async def test_current_temperature_reflects_negative_value(hass, fake_clients):
    """M2 case 9: current_temperature must show the decoded negative value
    (-5), never the raw unsigned 65531 register content."""
    client = seed_happy_path(fake_clients)
    client.set_registers(TEST_UNIT_ID, ADV_INDOOR_START, [65531, 0, 0, 0, 0, 0, 0, 0, 0])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        options={CONF_ENABLE_ADVANCED: True},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.attributes.get("current_temperature") == -5


# --- M3: write no longer stuck behind the 10s Debouncer cooldown ------------------


async def test_state_updates_immediately_after_write_no_10s_wait(hass, fake_clients):
    """Case 10: after set_temperature, the new value must already be visible once
    hass.async_block_till_done() returns — proves async_refresh() (immediate) is
    used, not async_request_refresh() (goes through HA's 10s Debouncer)."""
    entry, client = await _setup(hass, fake_clients)

    client.set_registers(TEST_UNIT_ID, 0, [1, 0, 2, 0, 20, 0, 0, 0, 0, 0, 0, 0])
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 20},
        blocking=True,
    )
    await hass.async_block_till_done()

    # No sleep/time-travel anywhere above — if this were still debounced 10s, the
    # coordinator's data (and thus this state) would still show the pre-write value.
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["temperature"] == 20


async def test_coordinator_uses_async_refresh_not_async_request_refresh(hass, fake_clients):
    """Same case 10, verified by spying on which coordinator method is actually
    called, not just by timing — a spy is immune to a future change that
    coincidentally keeps timing-based assertions passing for the wrong reason."""
    entry, client = await _setup(hass, fake_clients)
    coordinator = entry.runtime_data

    with (
        patch.object(coordinator, "async_refresh", wraps=coordinator.async_refresh) as refresh_spy,
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as request_refresh_spy,
    ):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 21},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert refresh_spy.await_count == 1
    assert request_refresh_spy.await_count == 0


async def test_rapid_successive_writes_transaction_count_on_bus(hass, fake_clients):
    """Case 11 (planner-added, important): simulate a user dragging the setpoint
    slider — 5 setpoint changes fired in quick succession — and MEASURE the real
    number of Modbus transactions this produces on the shared RS-485 bus.

    This does not assert a pass/fail threshold on the count (the task explicitly
    says that judgment call belongs to planner/reviewer, not qa). It fires the
    writes, reads client.op_log afterwards, and reports the real write/read/total
    transaction counts in the test output and in done/qa-core-fix.md.
    """
    entry, client = await _setup(hass, fake_clients)
    # Isolate the 5-write window from setup-time traffic (1 sw_version read in
    # __init__.py + 1 initial coordinator refresh) — those aren't part of what a
    # user dragging a slider produces, they're one-time entry-setup cost.
    baseline = len(client.op_log)

    for temp in (18, 19, 20, 21, 22):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: temp},
            blocking=True,
        )
    await hass.async_block_till_done()

    ops_during_writes = client.op_log[baseline:]
    write_ops = [e for e in ops_during_writes if e.startswith("write_start")]
    read_ops = [e for e in ops_during_writes if e.startswith("read_start")]
    total_ops = len(write_ops) + len(read_ops)

    print(
        f"\n[case 11] 5 rapid set_temperature calls -> "
        f"{len(write_ops)} write frame(s), {len(read_ops)} read frame(s), "
        f"{total_ops} total transaction(s) on the bus"
    )

    # Sanity: every write must actually have happened (no calls silently dropped/
    # coalesced) — the setpoint writes themselves are still 1:1 with user intent.
    setpoint_writes = [w for w in client.write_log if w[1] == REG_SETPOINT]
    assert len(setpoint_writes) == 5
    assert [w[2] for w in setpoint_writes] == [18, 19, 20, 21, 22]

    # The real finding this test exists to surface: async_refresh() has zero
    # cooldown, so each write is immediately followed by a full refresh before the
    # next write can even start (serialized by the hub lock). Since the telemetry
    # topic (2026-08-05) made AB64Coordinator read the indoor advanced block (TA,
    # register 4012) on every poll — not just when advanced telemetry is opted in,
    # see current_temperature in climate.py — each refresh is now 2 read frames
    # (basic block + indoor block), not 1: 5 user actions become 5 writes + 5×2
    # reads = 15 frames, not the 10 this test measured before that change.
    assert len(write_ops) == 5
    assert len(read_ops) == 10
    assert total_ops == 15

    # Final state must reflect the LAST write, not an earlier one clobbered by a
    # stale read racing with a later write (the concern raised in the task spec).
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["temperature"] == 22


# --- m4: unknown mode code -> hvac_mode None, warning logged once per code --------


async def test_unknown_mode_code_reports_hvac_mode_none_not_off(hass, fake_clients):
    regs = [1, 99, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]  # on_off=1, mode=99 (unknown)
    await _setup(hass, fake_clients, basic_regs=regs)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "unknown"


async def test_unknown_mode_code_warning_logged_only_once(hass, fake_clients, caplog):
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[1, 99, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]
    )
    coordinator = entry.runtime_data

    for _ in range(3):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    warnings = [
        r for r in caplog.records if r.levelname == "WARNING" and "Unknown mode code" in r.message
    ]
    assert len(warnings) == 1


# --- m-INT1: set_temperature carrying hvac_mode alongside temperature -------------
#
# HA core's climate component does NOT call async_set_hvac_mode() separately when a
# service call sets both `temperature` and `hvac_mode` at once — everything that
# isn't a temperature-range attribute is forwarded straight into
# async_set_temperature(**kwargs). Verified against climate/__init__.py at the
# 2025.10.1 tag in integration-core-fix2's done report.


async def test_set_temperature_with_hvac_mode_writes_both_single_refresh(hass, fake_clients):
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]  # currently AUTO
    )
    baseline = len(client.op_log)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 24, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    await hass.async_block_till_done()

    mode_writes = [w for w in client.write_log if w[1] == REG_MODE]
    setpoint_writes = [w for w in client.write_log if w[1] == REG_SETPOINT]
    assert mode_writes[-1] == (TEST_UNIT_ID, REG_MODE, 4)  # MODE_COOL
    assert setpoint_writes[-1] == (TEST_UNIT_ID, REG_SETPOINT, 24)

    # Exactly one refresh for this single service call, not two — the mode write
    # must have gone through with refresh=False. One refresh is 2 read frames
    # (basic block + the indoor advanced block read unconditionally since the
    # telemetry topic, 2026-08-05 — see the transaction-count test above), not 1.
    read_ops = [e for e in client.op_log[baseline:] if e.startswith("read_start")]
    assert len(read_ops) == 2

    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.COOL
    assert state.attributes["temperature"] == 24


async def test_set_temperature_with_hvac_mode_turns_on_from_off_first(hass, fake_clients):
    """Same pattern as async_set_hvac_mode: if the unit is currently off, turning
    it on (REG_ON_OFF=1) must happen before the mode write, not be skipped."""
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[0, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]  # currently OFF
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 24, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    mode_writes = [w for w in client.write_log if w[1] == REG_MODE]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 1)
    assert mode_writes[-1] == (TEST_UNIT_ID, REG_MODE, 4)
    # on_off write must come before the mode write, same ordering as async_set_hvac_mode.
    on_off_index = client.write_log.index(on_off_writes[-1])
    mode_index = client.write_log.index(mode_writes[-1])
    assert on_off_index < mode_index


async def test_set_temperature_with_hvac_mode_off_writes_on_off_zero_not_keyerror(
    hass, fake_clients
):
    """The regression this test exists for: HVAC_TO_MODE has no HVACMode.OFF key,
    so naively doing `HVAC_TO_MODE[hvac_mode]` for hvac_mode=off raises KeyError.
    Must instead write REG_ON_OFF=0, exactly like async_set_hvac_mode(OFF) does,
    and must NOT touch REG_MODE at all."""
    entry, client = await _setup(hass, fake_clients)  # currently on, AUTO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    mode_writes = [w for w in client.write_log if w[1] == REG_MODE]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 0)
    assert mode_writes == []  # REG_MODE must never be touched for hvac_mode=off


async def test_set_temperature_service_with_hvac_mode_off_and_no_temperature(hass, fake_clients):
    """async_set_temperature's own OFF handling (not async_set_hvac_mode) exercised
    directly against the entity method — HA's real SET_TEMPERATURE_SCHEMA requires
    at least ATTR_TEMPERATURE for an entity that only supports TARGET_TEMPERATURE
    (not a high/low range), so a real service call can never omit it; calling the
    entity method directly still validates async_set_temperature's own defensive
    handling of that combination."""
    from custom_components.carr_ab64.climate import AB64Climate

    entry, client = await _setup(hass, fake_clients)
    climate_entity = AB64Climate(entry.runtime_data)

    await climate_entity.async_set_temperature(hvac_mode=HVACMode.OFF)
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    setpoint_writes = [w for w in client.write_log if w[1] == REG_SETPOINT]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 0)
    assert setpoint_writes == []


# --- m-QA1 group 3: turn_on / turn_off / hvac_mode=off — the most-used paths ------


async def test_climate_turn_on(hass, fake_clients):
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[0, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]  # currently OFF
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN, "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 1)


async def test_climate_turn_off(hass, fake_clients):
    entry, client = await _setup(hass, fake_clients)  # currently on
    await hass.services.async_call(
        CLIMATE_DOMAIN, "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 0)
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.OFF


# --- F1: set_temperature with an hvac_mode this unit doesn't support --------------
#
# HA core's SET_TEMPERATURE_SCHEMA only vol.Coerce(HVACMode)'s the hvac_mode key —
# it does NOT check the value against this entity's own hvac_modes (unlike the
# dedicated climate.set_hvac_mode service, which core validates via
# _valid_mode_or_raise before ever calling the entity). Without a guard,
# HVAC_TO_MODE[hvac_mode] would raise a bare KeyError instead of a user-facing
# error — and worse, if that lookup happened after an on_off=1 write (unit was
# off), the unit would be left ON with no mode ever set: a partial write.


async def test_set_temperature_with_unsupported_hvac_mode_raises_no_partial_write(
    hass, fake_clients
):
    from homeassistant.exceptions import ServiceValidationError

    # Starts OFF specifically so a partial-write bug would be visible as an
    # on_off=1 write happening before the error — the worst-case failure mode.
    entry, client = await _setup(
        hass, fake_clients, basic_regs=[0, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 24, ATTR_HVAC_MODE: HVACMode.HEAT_COOL},
            blocking=True,
        )
    await hass.async_block_till_done()

    # No register write at all — not REG_ON_OFF, not REG_MODE, not REG_SETPOINT.
    assert client.write_log == []
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.OFF  # unchanged


@pytest.mark.parametrize("hvac_mode", [HVACMode.COOL, HVACMode.OFF])
async def test_set_temperature_with_supported_hvac_mode_still_works(
    hass, fake_clients, hvac_mode
):
    """Regression guard alongside the case above: modes this unit DOES support
    (cool = a regular HVAC_TO_MODE entry, off = the special-cased branch) must be
    completely unaffected by the new validation."""
    entry, client = await _setup(hass, fake_clients)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": ENTITY_ID, ATTR_TEMPERATURE: 24, ATTR_HVAC_MODE: hvac_mode},
        blocking=True,
    )
    await hass.async_block_till_done()

    setpoint_writes = [w for w in client.write_log if w[1] == REG_SETPOINT]
    assert setpoint_writes[-1] == (TEST_UNIT_ID, REG_SETPOINT, 24)
    state = hass.states.get(ENTITY_ID)
    assert state.state == hvac_mode


async def test_climate_set_hvac_mode_off_via_dedicated_service(hass, fake_clients):
    """The pre-existing async_set_hvac_mode(OFF) path (distinct service call from
    set_temperature) — same most-used-path gap the reviewer flagged."""
    entry, client = await _setup(hass, fake_clients)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {"entity_id": ENTITY_ID, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()

    on_off_writes = [w for w in client.write_log if w[1] == REG_ON_OFF]
    assert on_off_writes[-1] == (TEST_UNIT_ID, REG_ON_OFF, 0)
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.OFF
