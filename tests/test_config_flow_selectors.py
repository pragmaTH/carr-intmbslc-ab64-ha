"""Tests for PORT_SELECTOR / UNIT_ID_SELECTOR (config_flow.py) — introduced across
integration-core-fix4/fix6 to fix a real HA frontend quirk: a plain
`vol.All(vol.Coerce(int), vol.Range(min=...))` gets rendered as a number field
pre-filled with `min`, so a user could submit the form untouched and silently get
port=1 or unit_id=0. `selector.NumberSelector` renders as a genuinely empty box,
but returns a float, so both selectors chain `vol.Coerce(int)` — CONF_PORT/
CONF_UNIT_ID must stay int everywhere else (unique_id string, AsyncModbusTcpClient
port kwarg, Modbus register writes), or a float would silently create wrong/
duplicate identities (e.g. unique_id "host:8899.0:2" instead of "host:8899:2").

Standalone schema-level tests here don't need `hass` — PORT_SELECTOR/
UNIT_ID_SELECTOR/STEP_USER_SCHEMA/STEP_UNIT_SCHEMA are plain voluptuous objects
constructible without any HA runtime. Reconfigure prefill and the live full-scan
path are tested through the real flow in test_config_flow.py-style fixtures since
that schema is only built inside async_step_reconfigure, not a module constant.
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
    STEP_UNIT_SCHEMA,
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


# --- UNIT_ID_SELECTOR -------------------------------------------------------------


def test_step_unit_schema_blank_submit_drops_the_key_entirely():
    """Case 2: submitting {} must make CONF_UNIT_ID absent from the result, not
    just None — async_step_unit relies on `user_input.get(CONF_UNIT_ID)` being
    None to route into the full-range scan path."""
    result = STEP_UNIT_SCHEMA({})
    assert CONF_UNIT_ID not in result


def test_step_unit_schema_marker_has_no_default():
    """Unlike CONF_PORT (see test_step_user_schema_port_marker_default_is_502
    above), unit_id must stay default-less: blank submit is a meaningful value
    here (it means "scan the whole range"), not a placeholder waiting to be
    filled in — giving it any default would silently steer every user away from
    the scan path. The 2026-08-04 port-default decision explicitly did not touch
    this marker; this test guards against someone assuming it should follow."""
    marker = _marker_for(STEP_UNIT_SCHEMA, CONF_UNIT_ID)
    assert marker.default is vol.UNDEFINED


def test_unit_id_selector_returns_int_not_float():
    result = UNIT_ID_SELECTOR(0)
    assert result == 0
    assert type(result) is int


def test_step_unit_schema_manual_zero_validates_as_int():
    """0 is the Modbus broadcast address (never answers) but must still validate
    and reach async_step_unit's manual-probe path as int(0), not be blocked by
    the selector or silently coerced to something else."""
    result = STEP_UNIT_SCHEMA({"unit_id": 0})
    assert result == {"unit_id": 0}
    assert type(result["unit_id"]) is int


@pytest.mark.parametrize("bad_unit_id", [-1, 64])
def test_unit_id_selector_still_enforces_min_max(bad_unit_id):
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
        result["flow_id"], {"name": TEST_NAME, "host": TEST_HOST, "port": TEST_PORT}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert type(result["data"][CONF_PORT]) is int
    assert type(result["data"][CONF_UNIT_ID]) is int

    created_entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    host_part, port_part, unit_part = created_entry.unique_id.split(":")
    # The exact symptom PORT_SELECTOR's vol.Coerce(int) exists to prevent: a stray
    # float turning "host:502:2" into "host:502.0:2".
    assert port_part == str(TEST_PORT)
    assert unit_part == str(TEST_UNIT_ID)
