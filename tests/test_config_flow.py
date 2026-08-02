"""Config flow tests.

unit_id is always confirmed by an actual register read through the shared hub —
never trusted from a DIP switch reading alone (CLAUDE.md gotcha: the unit-id that
actually worked in the real bring-up didn't match what the DIP switches showed).
"""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import CONF_UNIT_ID, DOMAIN

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path


async def _start_user_flow(hass, *, name=TEST_NAME, host=TEST_HOST, port=TEST_PORT):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": name, "host": host, "port": port}
    )


async def test_normal_flow_scan_finds_one_unit_title_matches_name(hass, fake_clients):
    """Case 12: name -> host/port -> scan finds exactly one unit -> entry created
    with the user-chosen title, and the climate entity_id reflects that name."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unit"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_NAME
    assert result["data"][CONF_UNIT_ID] == TEST_UNIT_ID

    await hass.async_block_till_done()
    state = hass.states.get("climate.living_room_ac")
    assert state is not None, "entity_id must reflect the chosen device name, not carr_ab64_xxx"


async def test_scan_finds_zero_candidates_errors_no_units_found(hass, fake_clients):
    """Case 13: scan across the full range finds nothing -> error, no entry created."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True
    client.known_units.clear()  # nobody on the bus

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unit"
    assert result["errors"] == {"base": "no_units_found"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0


async def test_scan_finds_multiple_candidates_user_selects(hass, fake_clients):
    """Case 14: scan finds >1 candidate -> select_unit step -> user picks one."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True
    client.set_registers(5, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_unit"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


async def test_manual_unit_id_verify_fails_no_entry_created(hass, fake_clients):
    """Case 15: user types a unit_id manually, but it doesn't actually answer ->
    error, no entry created (never trust an address without a real read)."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 9}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unit"
    assert result["errors"] == {"base": "read_timeout"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0


async def test_second_entry_same_host_port_different_unit_id_succeeds(hass, fake_clients):
    """Case 16: distinct from shared-hub test — this asserts the *flow* allows a
    second entry on the same gateway (must NOT abort already_configured)."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(5, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Unit 2",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="Unit 5")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 5}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_second_entry_full_duplicate_aborts_already_configured(hass, fake_clients):
    """Case 17: host, port, AND unit_id all match an existing entry -> abort."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.only_respond_to_known_units = True

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Unit 2",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="Duplicate")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 2}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_changes_unit_id_without_remove_and_readd(hass, fake_clients):
    """Case 18: reconfigure unit 2 -> 3 succeeds; same entry_id, entry stays usable."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(3, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    original_entry_id = entry.entry_id

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 3},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = hass.config_entries.async_get_entry(original_entry_id)
    assert updated is not None
    assert updated.data[CONF_UNIT_ID] == 3
    assert updated.unique_id == f"{TEST_HOST}:{TEST_PORT}:3"


async def test_reconfigure_conflict_with_existing_entry_is_rejected(hass, fake_clients):
    """Case 19: reconfiguring entry A to a (host, port, unit_id) already held by
    entry B must be rejected, and entry A's data must be left untouched."""
    seed_happy_path(fake_clients, unit_id=2)

    entry_a = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="A",
    )
    entry_a.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 3},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:3",
        title="B",
    )
    entry_b.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry_a.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 3},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}

    unchanged = hass.config_entries.async_get_entry(entry_a.entry_id)
    assert unchanged.data[CONF_UNIT_ID] == 2


async def test_cannot_connect_and_read_timeout_are_distinct_error_keys(hass, fake_clients):
    """Case 20: TCP-layer failure vs addressing-layer failure must be two different
    error keys in the flow result, not collapsed into one generic error."""
    from tests.conftest import FakeModbusClient

    failing_client = FakeModbusClient(TEST_HOST, TEST_PORT)
    failing_client.fail_connect = True
    fake_clients[(TEST_HOST, TEST_PORT)] = failing_client

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_connect"}

    # Reset for a fresh flow: TCP connects fine this time, but no unit answers.
    del fake_clients[(TEST_HOST, TEST_PORT)]
    working_client = seed_happy_path(fake_clients)
    working_client.only_respond_to_known_units = True
    working_client.known_units.clear()

    result2 = await _start_user_flow(hass, host=TEST_HOST, port=TEST_PORT)
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], {CONF_UNIT_ID: 9}
    )
    assert result2["errors"] == {"base": "read_timeout"}
    assert result["errors"]["base"] != result2["errors"]["base"]


# --- m3: full-range scan filters out unit-ids already claimed on this host:port ---


async def test_scan_dropdown_filters_out_already_configured_units(hass, fake_clients):
    """m3 case 12: an entry already exists at unit=2 on this host:port; a scan
    that finds [2, 3, 4] must only offer [3, 4] — picking 2 from the dropdown
    would just abort with already_configured, so don't offer it at all."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(3, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.set_registers(4, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Existing Unit",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="New Unit")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_unit"
    schema_dict = result["data_schema"].schema
    validator = next(v for k, v in schema_dict.items() if str(k) == CONF_UNIT_ID)
    offered = set(validator.container)
    assert offered == {3, 4}
    assert 2 not in offered


async def test_scan_finds_units_but_all_already_configured_gives_dedicated_error(
    hass, fake_clients
):
    """m3 edge case, fixed in integration-core-fix2 (was `no_units_found`, which
    read as a wiring/baud/gateway problem): if every unit-id that responds is
    already claimed by another entry on this host:port, the error is now the
    dedicated `all_units_already_configured` key — distinct from `no_units_found`
    (nothing on the bus answered at all), because the fix for each is completely
    different. Manual-entry path (`manual_id is not None`) is unaffected and
    still uses `read_timeout` — not covered by this test."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.only_respond_to_known_units = True

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Existing Unit",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="New Unit")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unit"
    assert result["errors"] == {"base": "all_units_already_configured"}


async def test_scan_finds_nothing_at_all_still_gives_no_units_found(hass, fake_clients):
    """Sibling of the test above: when NOTHING on the bus answers (not even an
    already-configured unit), the error must stay `no_units_found` — proving the
    two error keys are actually distinguished by cause, not just renamed."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.only_respond_to_known_units = True
    client.known_units.clear()  # nobody responds, not even unit 2

    result = await _start_user_flow(hass, name="New Unit")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unit"
    assert result["errors"] == {"base": "no_units_found"}


# --- M1: full-range scan skips the Modbus broadcast address (unit-id 0) -----------


async def test_full_range_scan_never_probes_unit_id_0(hass, fake_clients):
    """M1 case 17: even if something answered at unit-id 0 (it never does on real
    hardware — 0 is the Modbus broadcast address), a full-range scan must not
    include it as a candidate at all."""
    client = seed_happy_path(fake_clients, unit_id=5)
    client.set_registers(0, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])  # pretend 0 "answers"
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    # If 0 had been probed, this would land on select_unit ([0, 5] found) instead
    # of going straight to a single CREATE_ENTRY.
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


async def test_manual_unit_id_0_is_still_probed_directly(hass, fake_clients):
    """M1: full-range scan skips 0, but a user who manually types 0 must still get
    a real verification attempt against it, not a silent/automatic rejection."""
    client = seed_happy_path(fake_clients, unit_id=0)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 0}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 0


# --- m-QA1 group 4: SCAN_PROBE_TIMEOUT — the core mechanism of the M1 fix had no
# regression guard at all until now (M1 lowered scan time from ~190s to ~63s
# worst-case specifically by bounding each probe; nothing proved that bound
# actually applies to a real hung candidate). ---------------------------------


async def test_scan_probe_timeout_skips_hung_candidate_without_hanging(hass, fake_clients):
    from custom_components.carr_ab64.config_flow import SCAN_PROBE_TIMEOUT

    client = seed_happy_path(fake_clients, unit_id=5)
    client.set_registers(9, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True
    # unit 9 "answers" but takes longer than SCAN_PROBE_TIMEOUT to do it —
    # simulates a hung/very slow candidate on the bus during a scan.
    client.delay_per_unit[9] = SCAN_PROBE_TIMEOUT + 0.3

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    # If unit 9 had NOT timed out, this would land on select_unit ([5, 9] found)
    # instead of a single CREATE_ENTRY for the fast responder alone — proving the
    # slow candidate was actually skipped, not just eventually included after a
    # long wait.
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


# --- m-QA1 group 5: reconfigure error paths (config_flow.py:154-155,159-160) ------


async def test_reconfigure_cannot_connect(hass, fake_clients):
    seed_happy_path(fake_clients, unit_id=2)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Reconfigure toward a different gateway that refuses the TCP connection.
    from tests.conftest import FakeModbusClient

    new_host = "192.0.2.2"
    failing_client = FakeModbusClient(new_host, TEST_PORT)
    failing_client.fail_connect = True
    fake_clients[(new_host, TEST_PORT)] = failing_client

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": new_host, "port": TEST_PORT, CONF_UNIT_ID: 2}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    # Entry's original data must be untouched by the failed attempt.
    unchanged = hass.config_entries.async_get_entry(entry.entry_id)
    assert unchanged.data["host"] == TEST_HOST


async def test_reconfigure_read_timeout(hass, fake_clients):
    client = seed_happy_path(fake_clients, unit_id=2)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Same gateway (connects fine — it's already the shared hub for this entry),
    # but unit-id 3 never answers register 0.
    from custom_components.carr_ab64.const import REG_ON_OFF

    client.fail_read_at(3, REG_ON_OFF)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 3}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "read_timeout"}
    unchanged = hass.config_entries.async_get_entry(entry.entry_id)
    assert unchanged.data[CONF_UNIT_ID] == 2
