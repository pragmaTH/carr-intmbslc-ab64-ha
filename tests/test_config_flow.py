"""Config flow tests.

Rewritten for the 2026-08-05 `unitstep` topic (plan-unitstep.md): unit_id moved
into the single `user` step (was its own `async_step_unit`/`STEP_UNIT_SCHEMA`,
now removed), and auto-pick was deleted entirely — a scan that finds exactly one
unit-id must still land on `select_unit` and wait for confirmation, the same as
a scan that finds several. See test_scan_finds_one_unit_still_requires_
confirmation below, the regression guard for that decision.

unit_id is still always confirmed by an actual register read through the shared
hub — never trusted from a DIP switch reading alone (CLAUDE.md gotcha).
"""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import (
    BASIC_BLOCK_COUNT,
    BASIC_BLOCK_START,
    CONF_UNIT_ID,
    DEFAULT_UNIT_ID_SCAN_RANGE,
    DOMAIN,
)

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path


def _suggested_values(schema_dict) -> dict[str, object]:
    """Pull HA's `add_suggested_values_to_schema` suggestions back out of a
    re-shown form's schema — these live in `marker.description["suggested_value"]`,
    NOT `marker.default` (see homeassistant.data_entry_flow.FlowHandler.
    add_suggested_values_to_schema), so `schema({})` alone won't reveal them."""
    return {str(k): (k.description or {}).get("suggested_value") for k in schema_dict}


async def _start_user_flow(
    hass, *, name=TEST_NAME, host=TEST_HOST, port=TEST_PORT, unit_id=None
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    payload = {"name": name, "host": host, "port": port}
    if unit_id is not None:
        payload[CONF_UNIT_ID] = unit_id
    return await hass.config_entries.flow.async_configure(result["flow_id"], payload)


def _selected_ids(result) -> set[int]:
    schema_dict = result["data_schema"].schema
    validator = next(v for k, v in schema_dict.items() if str(k) == CONF_UNIT_ID)
    return set(validator.container)


# --- Case 1: manual unit-id finishes in one step, no select_unit ------------------


async def test_manual_unit_id_finishes_in_one_step_no_select_unit(hass, fake_clients):
    """Case 1: typing a unit-id that verifies must go straight to CREATE_ENTRY —
    no intermediate select_unit step, since the user already knows which physical
    unit this is. entry.data holds exactly {host, port, unit_id} (3 keys); the
    4th piece of user input (name) becomes entry.title, not an entry.data key —
    consistent with every other entry-shape test in this suite (see e.g.
    test_entry_in_v0_1_2_shape_still_sets_up_without_migration below)."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass, unit_id=TEST_UNIT_ID)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_NAME
    assert result["data"] == {
        "host": TEST_HOST,
        "port": TEST_PORT,
        CONF_UNIT_ID: TEST_UNIT_ID,
    }

    await hass.async_block_till_done()
    state = hass.states.get("climate.living_room_ac")
    assert state is not None, "entity_id must reflect the chosen device name"


# --- Case 2: manual unit-id that doesn't answer -> read_timeout, form retains input


async def test_manual_unit_id_verify_fails_form_returns_prefilled_not_blank(hass, fake_clients):
    """Case 2: a failed verify must re-show the `user` form (not blank, not a
    different step) with `read_timeout`, and must carry back everything the user
    already typed via HA's suggested-value mechanism — losing the host/port
    while only the unit-id was wrong would be a bad para-cut."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass, unit_id=9)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "read_timeout"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0

    suggested = _suggested_values(result["data_schema"].schema)
    assert suggested["name"] == TEST_NAME
    assert suggested["host"] == TEST_HOST
    assert suggested["port"] == TEST_PORT
    assert suggested[CONF_UNIT_ID] == 9


# --- Case 3 (🔴 regression guard for the 2026-08-05 "no auto-pick" decision) -------


async def test_scan_finds_one_unit_still_requires_confirmation(hass, fake_clients):
    """Case 3 — regression guard for decision 2026-08-05 (plan-unitstep.md): the
    OLD behavior auto-created the entry the instant a scan found exactly one
    unit-id, with zero confirmation. The user explicitly ordered that removed:
    it made one feature behave 3 different ways depending on how many units
    happened to respond (silently finish on 1 hit, ask on >1, and silently
    finish *again* once a duplicate got filtered down to 1 — see the "เพิ่ม
    เครื่องที่ 2" row in plan-unitstep.md's problem table). This must land on
    select_unit even with only one candidate on offer, never CREATE_ENTRY
    directly — mutation-tested (see done/qa-unitstep.md): re-adding an
    `if len(found) == 1: return await self._async_finish_unit(found[0])`
    shortcut on the scan branch makes this test fail."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)  # unit_id left blank -> scan

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_unit"
    assert _selected_ids(result) == {TEST_UNIT_ID}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: TEST_UNIT_ID}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == TEST_UNIT_ID


# --- Case 4: scan finds multiple, user picks one ----------------------------------


async def test_scan_finds_multiple_candidates_user_selects(hass, fake_clients):
    """Case 4."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(5, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    assert result["step_id"] == "select_unit"
    assert _selected_ids(result) == {2, 5}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


# --- Case 5 / Case 6: distinct "nothing answered" vs "all already configured" -----


async def test_scan_finds_zero_candidates_errors_no_units_found(hass, fake_clients):
    """Case 5."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True
    client.known_units.clear()

    result = await _start_user_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_units_found"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0


async def test_scan_finds_units_but_all_already_configured_gives_dedicated_error(
    hass, fake_clients
):
    """Case 6 (m3, retained): if every unit-id that responds is already claimed by
    another entry on this host:port, the error is the dedicated
    all_units_already_configured key — distinct from no_units_found (nothing on
    the bus answered at all), because the fix for each is completely different."""
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

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "all_units_already_configured"}


async def test_scan_finds_nothing_at_all_still_gives_no_units_found_even_with_existing_entry(
    hass, fake_clients
):
    """Sibling of the case-6 test above: when NOTHING on the bus answers (not even
    an already-configured unit), the error must stay no_units_found — proving the
    two error keys are distinguished by cause, not just renamed."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.only_respond_to_known_units = True
    client.known_units.clear()  # nobody responds, not even unit 2

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Existing Unit",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="New Unit")

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_units_found"}


async def test_scan_dropdown_filters_out_already_configured_units(hass, fake_clients):
    """m3 (retained): an entry already exists at unit=2 on this host:port; a scan
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

    assert result["step_id"] == "select_unit"
    offered = _selected_ids(result)
    assert offered == {3, 4}
    assert 2 not in offered


# --- Case 7 / Case 8: select_unit labels show real state, degrade gracefully ------


async def test_select_unit_labels_show_real_state_on_and_off(hass, fake_clients):
    """Case 7 (plan-unitstep.md): the confirmation page must show live state, not
    a bare number — an "off" AC is otherwise indistinguishable from any other
    found unit-id, defeating the whole point of asking the user to confirm."""
    client = seed_happy_path(fake_clients, unit_id=2)  # on, mode=auto, set_temp=22
    client.set_registers(5, 0, [0, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])  # off
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    assert result["step_id"] == "select_unit"

    schema_dict = result["data_schema"].schema
    validator = next(v for k, v in schema_dict.items() if str(k) == CONF_UNIT_ID)
    label_on = validator.container[2]
    label_off = validator.container[5]
    assert "on" in label_on and "22" in label_on
    assert "off" in label_off


async def test_select_unit_label_fallback_when_state_read_fails_flow_still_completes(
    hass, fake_clients
):
    """Case 8: a unit that responds to the scan probe (single register at
    BASIC_BLOCK_START) but whose full-block label read fails must degrade its
    label to a bare unit-id string — never raise or abort the whole flow just
    because a cosmetic extra read failed. fail_read_block_at is scoped to the
    label read's (address, count) specifically so the scan probe itself (same
    address, count=1) still succeeds — otherwise the unit wouldn't be "found" at
    all and this wouldn't be testing the label-fallback path."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(5, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True
    client.fail_read_block_at(5, BASIC_BLOCK_START, BASIC_BLOCK_COUNT)

    result = await _start_user_flow(hass)
    assert result["step_id"] == "select_unit"

    schema_dict = result["data_schema"].schema
    validator = next(v for k, v in schema_dict.items() if str(k) == CONF_UNIT_ID)
    assert validator.container[5] == "5"
    assert "—" in validator.container[2]  # unit 2's label read succeeded normally

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


# --- M-1 (review/unitstep-review.md): single acquire covers scan + labels --------


async def test_connection_lost_during_label_phase_is_caught_not_leaked(hass, fake_clients):
    """M-1: before this fix, _async_build_unit_labels acquired its own hub — a
    second acquire/connect that sat outside the try/except ConnectionException
    wrapping the scan — so a ConnectionException raised while building labels
    escaped async_step_user entirely (the user saw an unhandled "Unknown error"
    after waiting out a ~64s scan). Now one hub, acquired once, covers both
    phases, and _async_build_unit_labels itself catches
    (AB64ReadError, ConnectionException) per unit (see case 8,
    test_select_unit_label_fallback_when_state_read_fails_flow_still_completes
    above) — so a connection lost mid-label-read no longer even needs a
    form-level cannot_connect error: it degrades that unit's label to a bare
    number and the flow still reaches select_unit. This is the "can't simulate
    cannot_connect from the label phase anymore" case flagged in the task spec
    — proving the *stronger* actual guarantee instead: a real ConnectionException
    (triggered via the fake client's connected flag, not a fabricated exception)
    during the label phase is caught, not leaked. Mutation-checked in
    done/qa-unitstep-m1.md: reverting to two separate acquires makes this
    scenario raise uncaught instead."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True
    # DEFAULT_UNIT_ID_SCAN_RANGE has exactly this many candidates, each costing
    # exactly one read call during the scan — disconnecting right after the last
    # of them lands the drop on the very first label-phase read.
    client.disconnect_after_n_reads = len(DEFAULT_UNIT_ID_SCAN_RANGE)

    result = await _start_user_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_unit"
    assert not result.get("errors")

    schema_dict = result["data_schema"].schema
    validator = next(v for k, v in schema_dict.items() if str(k) == CONF_UNIT_ID)
    assert validator.container[TEST_UNIT_ID] == str(TEST_UNIT_ID), (
        "label must have fallen back to a bare unit-id, not crashed the flow"
    )


async def test_scan_to_select_unit_round_connects_only_once(hass, fake_clients):
    """M-1, connection-churn regression guard: before this fix, a single flow
    submission that reaches select_unit connected 3 times and closed twice —
    scan: acquire+connect #1, release+close #1; labels: acquire+connect #2
    (reconnecting the just-closed client), release+close #2; then a 3rd connect
    once the resulting entry is actually set up after the user confirms. Closing
    at refcount 0 between the scan and label phases matters beyond wasted
    round-trips: it means the *next* connect has to succeed again against the
    real gateway to keep the flow going, and a TCP stack that takes even a
    moment to fully release a just-closed socket (or a gateway with a limited
    connection-slot count) can turn a single transient reconnect hiccup into a
    config-flow error indistinguishable from a real wiring problem. With one
    hub acquired once and reused for both scan and labels, this round connects
    exactly once — verified against the real fake-client counters, not assumed."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)

    assert result["step_id"] == "select_unit"
    assert client.connect_calls == 1
    assert client.close_calls == 1


# --- Case 9: pre-unitstep entry shape still sets up, no migration -----------------


async def test_entry_in_v0_1_2_shape_still_sets_up_without_migration(hass, fake_clients):
    """Case 9 (plan-unitstep.md acceptance criteria): entry.data's shape and
    VERSION are untouched by this topic — a real user already has a v0.1.2 entry
    installed (created before unit-id moved into the user step), so it must keep
    loading with no migration step. This instantiates the pre-unitstep entry
    shape directly ({host, port, unit_id}, VERSION 1) rather than going through
    the new config flow, to prove the *stored* shape — not just the flow that
    creates it today — is unaffected."""
    seed_happy_path(fake_clients)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: TEST_UNIT_ID},
        version=1,
        unique_id=f"{TEST_HOST}:{TEST_PORT}:{TEST_UNIT_ID}",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not None
    assert entry.runtime_data.data["set_temp"] == 22


# --- Case 10: duplicate (host, port, unit_id) still aborts, both paths ------------


async def test_manual_duplicate_aborts_already_configured(hass, fake_clients):
    """Case 10, manual-entry path."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.only_respond_to_known_units = True

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Existing",
    )
    existing.add_to_hass(hass)

    result = await _start_user_flow(hass, name="Duplicate", unit_id=2)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_select_unit_duplicate_aborts_already_configured(hass, fake_clients):
    """Case 10, scan+select path: picking an already-configured unit-id from the
    select_unit dropdown must still abort. This models a race (another entry
    claims unit 2 between the scan and this flow's confirm submit) since m3's
    filtering (test_scan_dropdown_filters_out_already_configured_units above)
    already keeps a *known* duplicate off the dropdown in the normal case."""
    client = seed_happy_path(fake_clients, unit_id=2)
    client.set_registers(3, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass, name="New Unit")
    assert result["step_id"] == "select_unit"

    existing = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": TEST_PORT, CONF_UNIT_ID: 2},
        unique_id=f"{TEST_HOST}:{TEST_PORT}:2",
        title="Raced In",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 2}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- Case 11: .strip() host and port default 502, carried over from portdefault ---


async def test_user_step_strips_whitespace_from_host(hass, fake_clients):
    """Case 11 (n2, carried over from the portdefault topic): must still hold in
    the collapsed single-step flow."""
    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass, host=f"  {TEST_HOST}  ", unit_id=TEST_UNIT_ID)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == TEST_HOST
    assert " " not in result["result"].unique_id


async def test_user_step_port_defaults_to_502_when_omitted(hass, fake_clients):
    """Case 11 (port default, carried over from the portdefault topic):
    STEP_USER_SCHEMA's default=502 must still take effect end-to-end through the
    real flow, not just at the schema level (see test_config_flow_selectors.py
    for that half)."""
    client = seed_happy_path(fake_clients)  # TEST_PORT is already 502
    client.only_respond_to_known_units = True

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": TEST_NAME, "host": TEST_HOST, CONF_UNIT_ID: TEST_UNIT_ID},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == 502
    assert type(result["data"]["port"]) is int


# --- Case 12 / 13 (workstream B): 1-64 address space, flow-level half -------------


async def test_full_range_scan_reaches_unit_id_64(hass, fake_clients):
    """Case 12: DEFAULT_UNIT_ID_SCAN_RANGE must include 64 — a box set to
    (SW2=3, SW1=15) is a real, valid address (see const.py) that the
    pre-2026-08-05 range(0, 64) bug could never reach. Schema-level min/max
    boundary checks (0 and 65 invalid) live in test_config_flow_selectors.py."""
    client = seed_happy_path(fake_clients, unit_id=64)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass)
    assert result["step_id"] == "select_unit"
    assert 64 in _selected_ids(result)


async def test_manual_unit_id_64_verifies_and_creates_entry(hass, fake_clients):
    """Case 13, flow-level half: 64 is the top of the real 1-64 address space, not
    an off-by-one edge case to reject."""
    client = seed_happy_path(fake_clients, unit_id=64)
    client.only_respond_to_known_units = True

    result = await _start_user_flow(hass, unit_id=64)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 64


# --- cannot_connect vs read_timeout, and the bounded-scan-probe-timeout guard -----


async def test_cannot_connect_and_read_timeout_are_distinct_error_keys(hass, fake_clients):
    """Case 20 (retained): TCP-layer failure vs addressing-layer failure must be
    two different error keys, not collapsed into one generic error."""
    from tests.conftest import FakeModbusClient

    failing_client = FakeModbusClient(TEST_HOST, TEST_PORT)
    failing_client.fail_connect = True
    fake_clients[(TEST_HOST, TEST_PORT)] = failing_client

    result = await _start_user_flow(hass)
    assert result["errors"] == {"base": "cannot_connect"}

    del fake_clients[(TEST_HOST, TEST_PORT)]
    working_client = seed_happy_path(fake_clients)
    working_client.only_respond_to_known_units = True
    working_client.known_units.clear()

    result2 = await _start_user_flow(hass, unit_id=9)
    assert result2["errors"] == {"base": "read_timeout"}
    assert result["errors"]["base"] != result2["errors"]["base"]


async def test_scan_probe_timeout_skips_hung_candidate_without_hanging(hass, fake_clients):
    """m-QA1 group 4 (retained): a hung/very slow candidate on the bus must be
    skipped by SCAN_PROBE_TIMEOUT, not eventually included after a long wait —
    now closed out through select_unit (case 3 applies universally) instead of
    landing straight on CREATE_ENTRY."""
    from custom_components.carr_ab64.config_flow import SCAN_PROBE_TIMEOUT

    client = seed_happy_path(fake_clients, unit_id=5)
    client.set_registers(9, 0, [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0])
    client.only_respond_to_known_units = True
    client.delay_per_unit[9] = SCAN_PROBE_TIMEOUT + 0.3

    result = await _start_user_flow(hass)
    assert result["step_id"] == "select_unit"
    assert _selected_ids(result) == {5}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_ID: 5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_ID] == 5


# --- Reconfigure: untouched by this topic, coverage retained unchanged -----------


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


async def test_reconfigure_strips_whitespace_from_host(hass, fake_clients):
    """n2, reconfigure side: the same whitespace hazard applies to the reconfigure
    step's host field, entered separately from the user step's."""
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

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": f"  {TEST_HOST}  ", "port": TEST_PORT, CONF_UNIT_ID: 2},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated.data["host"] == TEST_HOST
    assert " " not in updated.unique_id


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
