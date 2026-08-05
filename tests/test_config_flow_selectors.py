"""Tests for PORT_SELECTOR / UNIT_ID_SELECTOR (config_flow.py) — introduced across
integration-core-fix4/fix6 to fix a real HA frontend quirk: a plain
`vol.All(vol.Coerce(int), vol.Range(min=...))` gets rendered as a number field
pre-filled with `min`, so a user could submit the form untouched and silently get
port=1 or an out-of-range unit_id. `selector.NumberSelector` renders as a
genuinely empty box, but returns a float, so both selectors chain
`vol.Coerce(int)` — CONF_PORT/CONF_UNIT_ID must stay int everywhere else
(unique_id string, AsyncModbusTcpClient port kwarg, Modbus register writes), or a
float would silently create wrong/duplicate identities (e.g. unique_id
"host:8899.0:2" instead of "host:8899:2").

unit_id used to live in its own step (`STEP_UNIT_SCHEMA`/`async_step_unit`),
removed 2026-08-05 (`unitstep` topic) when it was folded into `STEP_USER_SCHEMA`
as an optional key — see test_step_user_schema_unit_id_marker_has_no_default
below. The same topic also fixed the address space itself: the vendor's
SW1+SW2 DIP table is 1-based (1-64), not 0-63 — UNIT_ID_SELECTOR's min/max moved
accordingly (see test_unit_id_selector_still_enforces_min_max).

Standalone schema-level tests here don't need `hass` — PORT_SELECTOR/
UNIT_ID_SELECTOR/STEP_USER_SCHEMA are plain voluptuous objects constructible
without any HA runtime. Reconfigure prefill and the live full-scan path are
tested through the real flow in test_config_flow.py-style fixtures since that
schema is only built inside async_step_reconfigure, not a module constant.
"""
from __future__ import annotations

import json
from pathlib import Path

import voluptuous as vol
import pytest

from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.const import CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.config_flow import (
    PORT_SELECTOR,
    STEP_USER_SCHEMA,
    UNIT_ID_SELECTOR,
)
from custom_components.carr_ab64.const import CONF_UNIT_ID, DEFAULT_PORT, DOMAIN

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "carr_ab64"


def _marker_for(schema: vol.Schema, key: str):
    return next(k for k in schema.schema if str(k) == key)


# --- PORT_SELECTOR --------------------------------------------------------------


def test_step_user_schema_port_marker_default_is_502():
    """The "no default" regression guard this test used to be (pre-2026-08-04) was
    itself pinning a decision the user has since reversed: `references/
    ac-modbus-ab64-reference.md` warned against defaulting the port because the
    bring-up EW11 gateway actually used 8899, not 502. On a real HA setup that
    turned out worse than the risk it was avoiding — an empty NumberSelector box
    renders as `0`, which `PORT_SELECTOR`'s own `min=1` always rejects, so "no
    default" guaranteed a submit error instead of just sometimes being wrong.

    The user decided 2026-08-04 to default the field to 502 (the Modbus TCP
    standard, and what `homeassistant.components.modbus` itself defaults to) and
    keep the 8899/EW11 warning in the field's `data_description` instead — see
    `test_port_data_description_still_warns_about_ew11_8899` below for that half.

    Still required, NOT reversed by this decision:
    - `selector.NumberSelectorMode.BOX` on `PORT_SELECTOR` — keeps this a typeable
      field instead of a slider; a slider has no sane "unset" position.
    - `vol.Coerce(int)` on `PORT_SELECTOR` — `NumberSelector` itself returns a
      float, so a bare `default=502` would submit as `502.0` and corrupt
      `unique_id` into `"host:502.0:2"` instead of `"host:502:2"`.
    """
    marker = _marker_for(STEP_USER_SCHEMA, CONF_PORT)
    assert marker.default is not vol.UNDEFINED
    assert marker.default() == DEFAULT_PORT
    assert type(marker.default()) is int


def test_step_user_schema_untouched_port_submit_yields_int_502():
    """The scenario the default actually exists for: a user submits the `user`
    step without touching the port field at all. Must come back as the int `502`,
    not `502.0` — see the docstring above for why a float would corrupt
    unique_id. This is now *easier* to hit than before 2026-08-04 (nobody has to
    type anything for it to happen), which is exactly why the type check matters
    more now, not less."""
    validated = STEP_USER_SCHEMA({"name": TEST_NAME, "host": TEST_HOST})
    assert validated["port"] == 502
    assert type(validated["port"]) is int


def test_port_data_description_still_warns_about_ew11_8899():
    """Defaulting the port to 502 is only safe *behaviorally* as long as an EW11
    user (whose gateway actually listens on 8899) still sees a warning telling
    them to check their gateway's own config page — otherwise the default turns
    from "usually right, and wrong loudly" into "usually right, and wrong
    silently" the moment someone trims this string down. Checked in both the
    `user` and `reconfigure` steps since the same PORT_SELECTOR/text is reused in
    both."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    step = strings["config"]["step"]
    assert "8899" in step["user"]["data_description"]["port"]
    assert "8899" in step["reconfigure"]["data_description"]["port"]


def test_unit_id_data_description_still_has_the_sw1_sw2_formula():
    """n-10 (review/unitstep-review.md round 2): unlike the port field (see
    test_port_data_description_still_warns_about_ew11_8899 above), the unit_id
    field's data_description had no test pinning its content at all. The
    formula it carries — unit-id = SW2 × 16 + SW1 + 1 — is not a nicety: it's
    the exact thing that cost the original bring-up session real time (the
    vendor's own DIP table is 1-based, so reading the switches directly gives
    an answer that's off by one), and it's not written down anywhere else the
    user would see it while filling in this form — the manual's table is a
    physical document, not something HA surfaces. Losing this string silently
    would reintroduce that exact off-by-one trap for every future user with
    only the switches in front of them.

    Checks strings.json AND translations/en.json independently (rather than
    relying on test_strings_json_equals_translations_en_json) so a future edit
    that trims the formula from only one of the two files still fails here,
    not just there. `×` must be the real multiplication sign (U+00D7), not an
    ASCII "x" — a find/replace or manual retype is a plausible way this
    formula could quietly degrade to something that still "looks right" at a
    glance."""
    for filename in ("strings.json", "translations/en.json"):
        data = json.loads((INTEGRATION_DIR / filename).read_text())
        text = data["config"]["step"]["user"]["data_description"]["unit_id"]
        assert "SW2 × 16 + SW1 + 1" in text, f"{filename}: formula missing or × isn't U+00D7"
        assert "1-64" in text, f"{filename}: address range missing"
        assert "blank" in text, f"{filename}: blank-means-scan explanation missing"


def test_port_selector_returns_int_not_float():
    result = PORT_SELECTOR(8899)
    assert result == 8899
    assert type(result) is int  # NumberSelector itself returns float — == would
    # pass even for 8899.0, so the type check is the actual point of this test.


def test_step_user_schema_full_submit_yields_int_port():
    validated = STEP_USER_SCHEMA({"name": TEST_NAME, "host": TEST_HOST, "port": 8899})
    assert validated["port"] == 8899
    assert type(validated["port"]) is int


@pytest.mark.parametrize("bad_port", [0, 70000])
def test_port_selector_still_enforces_min_max(bad_port):
    with pytest.raises(vol.Invalid):
        PORT_SELECTOR(bad_port)


# --- UNIT_ID_SELECTOR ---------------------------------------------------------


def test_step_user_schema_blank_unit_id_drops_the_key_entirely():
    """Case 2 (retained from the old STEP_UNIT_SCHEMA test): submitting the user
    step without unit_id must make CONF_UNIT_ID absent from the validated result,
    not just None — async_step_user relies on `user_input.get(CONF_UNIT_ID)`
    being None to route into the full-range scan path."""
    result = STEP_USER_SCHEMA({"name": TEST_NAME, "host": TEST_HOST})
    assert CONF_UNIT_ID not in result


def test_step_user_schema_unit_id_marker_has_no_default():
    """Unlike CONF_PORT (see test_step_user_schema_port_marker_default_is_502
    above), unit_id must stay default-less: blank submit is a meaningful value
    here (it means "scan the whole range"), not a placeholder waiting to be
    filled in — giving it any default would silently steer every user away from
    the scan path. The 2026-08-04 port-default decision explicitly did not touch
    this marker, and folding unit_id into STEP_USER_SCHEMA on 2026-08-05
    (unitstep topic) didn't either; this test guards against someone assuming it
    should follow either time."""
    marker = _marker_for(STEP_USER_SCHEMA, CONF_UNIT_ID)
    assert marker.default is vol.UNDEFINED


def test_unit_id_selector_returns_int_not_float():
    result = UNIT_ID_SELECTOR(1)
    assert result == 1
    assert type(result) is int


def test_step_user_schema_manual_unit_id_validates_as_int():
    """A manually entered unit-id must reach async_step_user's manual-verify path
    as a real int, not be blocked by the selector or silently coerced to
    something else. Uses 64 (the top of the real 1-64 address space) rather than
    the old test's 0 — 0 is no longer a valid address at all as of the
    2026-08-05 unitstep/workstream-B fix (the vendor's SW1+SW2 table is 1-based;
    0 was never a real DIP setting, see test_unit_id_selector_still_enforces_
    min_max below for the boundary-rejection half)."""
    result = STEP_USER_SCHEMA({"name": TEST_NAME, "host": TEST_HOST, "unit_id": 64})
    assert result[CONF_UNIT_ID] == 64
    assert type(result[CONF_UNIT_ID]) is int


def test_unit_id_selector_accepts_boundary_64():
    """Case 13 (workstream B), valid half: 64 is a real address (SW2=3, SW1=15),
    not an off-by-one to reject — companion to test_unit_id_selector_still_
    enforces_min_max below, which covers the now-invalid boundary (0 and 65)."""
    result = UNIT_ID_SELECTOR(64)
    assert result == 64
    assert type(result) is int


@pytest.mark.parametrize("bad_unit_id", [-1, 0, 65])
def test_unit_id_selector_still_enforces_min_max(bad_unit_id):
    """Case 13 (workstream B), invalid half: the address space is 1-64 (the
    vendor's SW1+SW2 DIP table is 1-based — see DEFAULT_UNIT_ID_SCAN_RANGE in
    const.py), so 0 must now be rejected (it used to be accepted as the Modbus
    broadcast address, a piece of reasoning the 2026-08-05 fix retired entirely —
    0 was never a real DIP setting) and 65 stays rejected as before."""
    with pytest.raises(vol.Invalid):
        UNIT_ID_SELECTOR(bad_unit_id)


# --- Reconfigure prefill: both fields, both int, via the real flow ---------------


async def test_reconfigure_form_prefills_port_and_unit_id_as_original_int_values(
    hass, fake_clients
):
    seed_happy_path(fake_clients, unit_id=5)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": TEST_HOST, "port": 8899, CONF_UNIT_ID: 5},
        unique_id=f"{TEST_HOST}:8899:5",
        title=TEST_NAME,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    defaults = result["data_schema"]({})

    assert defaults[CONF_PORT] == 8899
    assert type(defaults[CONF_PORT]) is int
    assert defaults[CONF_UNIT_ID] == 5
    assert type(defaults[CONF_UNIT_ID]) is int


async def test_full_flow_created_entry_has_int_port_and_unit_id(hass, fake_clients):
    """Closes the loop end-to-end (not just at the schema level): the actual
    entry.data written by a live flow submission must be int for both fields —
    this is exactly the scenario that would have produced unique_id
    "host:8899.0:2" before PORT_SELECTOR's vol.Coerce(int) was added."""
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    client = seed_happy_path(fake_clients)
    client.only_respond_to_known_units = True

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": TEST_NAME,
            "host": TEST_HOST,
            "port": TEST_PORT,
            CONF_UNIT_ID: TEST_UNIT_ID,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert type(result["data"][CONF_PORT]) is int
    assert type(result["data"][CONF_UNIT_ID]) is int

    created_entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    host_part, port_part, unit_part = created_entry.unique_id.split(":")
    # The exact symptom PORT_SELECTOR's vol.Coerce(int) exists to prevent: a stray
    # float turning "host:502:2" into "host:502.0:2".
    assert port_part == str(TEST_PORT)
    assert unit_part == str(TEST_UNIT_ID)
