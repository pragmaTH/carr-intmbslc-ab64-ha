"""Tests for `translations/th.json` (topic `thai`, targeting v0.1.8, not yet
committed at the time this file was written).

**Round 2 correction (2026-08-07, topic `thai2`)**: round 1 of this file
asserted `th.json` must NOT have an "entity" block, on the assumption that
translating entity names would fork `entity_id` on a Thai-language HA
instance. That assumption was wrong — proved from `homeassistant==2025.10.1`
source (installed in `.venv-test`, see `test_th_entity_id_language_is_not_yet_native_for_thai`
below for the live check): `Entity.suggested_object_id` derives its slug from
`object_id_language`, which is `hass.config.language` only if that language is
in `homeassistant.generated.languages.NATIVE_ENTITY_IDS` — otherwise it falls
back to English. `'th'` is not in that list (it's Latin-script languages
only), so HA itself guarantees English-based entity_id generation for Thai
regardless of what `th.json` contains. See `.claude/shared/plan-thai.md`'s
"🔴 แก้ไขรอบ 2" section for the full writeup. Contract now locked (AC-6):

- path: custom_components/carr_ab64/translations/th.json
- top-level keys are exactly {"config", "options", "entity"} — all three
  blocks of strings.json, 45 keys total.
- keys inside th.json mirror strings.json's keys exactly (whole file, not
  just config+options) — nothing extra, nothing missing.

docs-engineer owns th.json itself — this file only ever reads it. Do not add
th.json, en.json, or strings.json edits here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "carr_ab64"
TH_PATH = INTEGRATION_DIR / "translations" / "th.json"


def _load_th() -> dict:
    return json.loads(TH_PATH.read_text(encoding="utf-8"))


def _load_strings() -> dict:
    return json.loads((INTEGRATION_DIR / "strings.json").read_text(encoding="utf-8"))


def _leaf_paths(node: dict, prefix: str = "") -> set[str]:
    """Dotted key-path for every string leaf under `node` (dicts only — this
    schema has no lists)."""
    paths: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _leaf_paths(value, path)
        else:
            paths.add(path)
    return paths


def _get_by_path(node: dict, path: str):
    for part in path.split("."):
        node = node[part]
    return node


# Q-3 whitelist: leaf key-paths where the Thai value is legitimately allowed to
# be byte-identical to the English value (e.g. pure technical jargon with no
# accepted Thai rendering). Empty until a real case is found and justified in
# done/qa-thai.md — do not add entries here "just in case".
THAI_EQUALS_ENGLISH_ALLOWED: set[str] = set()


# --- R-1: th.json parses, and now covers all 3 strings.json blocks -----------


def test_th_json_top_level_keys_are_exactly_config_options_and_entity():
    """R-1: reversed from round 1, which asserted "entity" must be ABSENT.
    See the module docstring's round-2 correction — HA's own
    NATIVE_ENTITY_IDS mechanism (checked directly in
    test_th_entity_id_language_is_not_yet_native_for_thai below) makes that
    original concern moot, so the user chose to translate all 3 blocks."""
    th = _load_th()
    assert set(th.keys()) == {"config", "options", "entity"}


# --- R-2: key-parity against the WHOLE strings.json, not just config+options --


def test_th_json_key_parity_with_full_strings_json():
    """R-2: widened from round 1's config+options-only comparison — now that
    "entity" is translated too, parity must hold against the entire
    strings.json, not a subset of it."""
    th = _load_th()
    strings = _load_strings()

    th_paths = _leaf_paths(th)
    en_paths = _leaf_paths(strings)

    missing = sorted(en_paths - th_paths)  # in English, absent from Thai
    extra = sorted(th_paths - en_paths)  # in Thai, not in English (typo/stray key)

    assert not missing and not extra, (
        f"th.json key-parity mismatch — missing from th.json: {missing!r}; "
        f"extra/unrecognized keys in th.json: {extra!r}"
    )


def test_th_json_has_45_keys():
    """R-3: was test_th_json_has_31_keys (config+options only) — now that
    "entity" (14 keys: sensor x13 + binary_sensor x1) is in scope too,
    31 + 14 = 45. Sanity guard for the exact count called out in the task
    spec — a passing parity test above already implies this, but a stale
    expectation (e.g. strings.json growing without th.json following) should
    be visible as its own explicit number, not just inferred from set
    equality."""
    th = _load_th()
    assert len(_leaf_paths(th)) == 45


# --- Q-3: every Thai leaf is a real, non-empty, actually-translated string ----


def test_th_json_values_are_nonempty_and_actually_translated():
    """R-7: now walks the whole file (including "entity") since `en` below is
    the full strings.json, not the config+options subset round 1 used."""
    th = _load_th()
    en = _load_strings()

    offenders: list[str] = []
    for path in sorted(_leaf_paths(th)):
        th_value = _get_by_path(th, path)
        if not isinstance(th_value, str) or not th_value.strip():
            offenders.append(f"{path}: not a non-empty string ({th_value!r})")
            continue
        if path in THAI_EQUALS_ENGLISH_ALLOWED:
            continue
        en_value = _get_by_path(en, path)
        if th_value == en_value:
            offenders.append(f"{path}: identical to English value (untranslated?)")

    assert offenders == [], "\n".join(offenders)


# --- Q-4: EW11/8899 gateway-port warning must survive translation -------------


def test_th_json_port_data_description_still_warns_about_ew11_8899():
    """Thai-language counterpart of
    test_port_data_description_still_warns_about_ew11_8899
    (tests/test_config_flow_selectors.py) — that test locks strings.json/en.json
    and must stay untouched (Q-6); this is a new, separate test for th.json so
    the same real-world gotcha (EW11 gateways default to 8899, not 502) isn't
    silently lost for Thai-language users."""
    th = _load_th()
    step = th["config"]["step"]
    assert "8899" in step["user"]["data_description"]["port"]
    assert "8899" in step["reconfigure"]["data_description"]["port"]


# --- Q-5: load-bearing numbers must survive translation verbatim --------------


@pytest.mark.parametrize(
    "expected",
    # m-1 (cleanup-review.md): "6.9" and "13.8" are the advanced-telemetry-on
    # bus-math figures for 1 and 2 units — same family as 4.6/9.2/18.4/27.6
    # below, just previously missing from this list. Reviewer proved this gap
    # with mutation E: swapping "6.9/13.8" for wrong numbers in th.json left
    # the full suite green (nothing else in the suite reads these two).
    ["502", "8899", "115", "4.6", "9.2", "18.4", "27.6", "6.9", "13.8", "1-64"],
)
def test_th_json_contains_expected_number(expected: str):
    raw = TH_PATH.read_text(encoding="utf-8")
    assert expected in raw, f"{expected!r} not found anywhere in th.json"


def test_th_json_contains_advanced_sensor_count_11():
    """Split out from the parametrized numeric check above: a plain substring
    search for "11" would trivially pass off of "115" (ms figure) alone
    without the count of extra sensors actually being present, so this needs a
    digit-boundary match instead of `"11" in raw`."""
    raw = TH_PATH.read_text(encoding="utf-8")
    assert re.search(r"(?<!\d)11(?!\d)", raw), "standalone '11' (advanced sensor count) not found in th.json"


# --- R-4: the risk that justifies translating "entity" at all -----------------


def test_th_entity_id_language_is_not_yet_native_for_thai():
    """R-4 — the single most important test added in round 2.

    Translating the "entity" block is only safe (i.e. doesn't fork entity_id
    per language on a Thai-language HA instance) because of a mechanism
    internal to Home Assistant: `homeassistant.helpers.entity_platform.
    EntityPlatform.async_load_translations` computes

        object_id_language = (
            hass.config.language if hass.config.language in NATIVE_ENTITY_IDS
            else DEFAULT_LANGUAGE  # "en"
        )

    and *that* language, not hass.config.language directly, is what
    `Entity.suggested_object_id` slugifies into entity_id. `NATIVE_ENTITY_IDS`
    is a fixed list of Latin-script language codes generated by HA itself
    (`homeassistant.generated.languages`) — deliberately imported here rather
    than hardcoded, so this test is a live check against whatever HA version
    is actually pinned, not a frozen snapshot of today's list.

    If this test goes RED: Home Assistant has added "th" to
    NATIVE_ENTITY_IDS. That means entity_id on a *newly installed* Thai-
    language instance would start slugifying from the Thai entity names in
    th.json instead of falling back to English — new entity_id values would
    no longer match this repo's README Entities table, and would differ from
    entity_id on any instance installed before the HA upgrade. Do NOT treat
    this as a simple test update — stop and re-read `.claude/shared/
    plan-thai.md`'s "🔴 แก้ไขรอบ 2" section (the entire justification for
    translating "entity" instead of leaving it English rests on this
    condition staying true) and take it back to planner before deciding
    anything, including whether to just update this assertion.
    """
    from homeassistant.generated import languages

    assert "th" not in languages.NATIVE_ENTITY_IDS


# --- R-5: parenthetical sensor abbreviations must survive translation --------


@pytest.mark.parametrize(
    "path,abbreviation",
    [
        ("entity.sensor.indoor_temp.name", "(TA)"),
        ("entity.sensor.indoor_coil_temp_tcj.name", "(TCJ)"),
        ("entity.sensor.indoor_coil_temp_tc2.name", "(TC2)"),
        ("entity.sensor.outdoor_evaporator_temp_te.name", "(TE)"),
        ("entity.sensor.outdoor_temp_to.name", "(TO)"),
        ("entity.sensor.outdoor_discharge_temp_td.name", "(TD)"),
        ("entity.sensor.outdoor_suction_temp_ts.name", "(TS)"),
    ],
)
def test_th_json_sensor_abbreviations_survive_translation(path: str, abbreviation: str):
    """R-5: these parenthetical codes (TA/TCJ/TC2/TE/TO/TD/TS) are the exact
    register labels used in the vendor manual and in CLAUDE.md's advanced
    telemetry decisions — dropping them in translation would break the link
    between an entity's display name and the register it reads."""
    th = _load_th()
    assert abbreviation in _get_by_path(th, path)


# --- R-6: compressor sensors must not translate-in a unit that isn't there ---


_FORBIDDEN_UNIT_TOKENS = ("แอมป์", "rpm", "รอบ/นาที", "rps")
_STANDALONE_LATIN_A = re.compile(r"(?<![A-Za-z])A(?![A-Za-z])")


@pytest.mark.parametrize(
    "path",
    ["entity.sensor.compressor_current.name", "entity.sensor.compressor_rpm.name"],
)
def test_th_json_compressor_names_carry_no_unit(path: str):
    """R-6: per CLAUDE.md, compressor current (register 4415) has no
    confirmed unit and must never be presented as amps, and compressor speed
    (register 4417) is rps, not rpm — the *English* names ("Compressor
    current", "Compressor speed") deliberately carry no unit at all. A Thai
    translation that adds one (explicitly or via an abbreviation like "A")
    would assert a unit the English original doesn't, which is worse than a
    literal mistranslation because it looks authoritative."""
    th = _load_th()
    value = _get_by_path(th, path)
    lowered = value.lower()
    for forbidden in _FORBIDDEN_UNIT_TOKENS:
        assert forbidden not in lowered, f"{path} contains forbidden unit token {forbidden!r}: {value!r}"
    assert not _STANDALONE_LATIN_A.search(value), f"{path} contains a standalone 'A' (implies Amps): {value!r}"


# --- Q-1 (topic `cleanup`): en/th info-parity for the "115ms is measured, not
# a vendor spec" caveat --------------------------------------------------------

_SCAN_INTERVAL_DESC_PATH = "options.step.init.data_description.scan_interval"


@pytest.mark.parametrize(
    "loader,expected_marker",
    [(_load_strings, "measured"), (_load_th, "วัดจริง")],
    ids=["en", "th"],
)
def test_scan_interval_description_marks_115ms_as_measured(loader, expected_marker):
    """Q-1 (topic `cleanup`) — guards against nit n-1 (`poll5s-review.md`,
    reiterated `thai-review.md` m-2) coming back: the scan_interval
    data_description explains that one Modbus read takes ~115 ms.
    `translations/th.json` has always said this is a *measured* figure, not a
    manufacturer spec ("วัดจริง ไม่ใช่ค่าจากผู้ผลิต") — `strings.json` (and
    therefore `translations/en.json`, which must stay byte-identical to it)
    didn't say that before topic `cleanup`, so an English-language reader
    could mistake 115 ms for a datasheet number instead of the single-unit
    DEBUG-log measurement CLAUDE.md's poll-interval decision describes it as.

    This test locks only a *marker word* per language ("measured" / "วัดจริง"),
    not the surrounding sentence — docs-engineer is free to word the rest of
    the description however they like; only the presence of the
    "this-was-measured" caveat is load-bearing, checked independently per
    language, so English and Thai can never silently drift apart on this
    fact again the way they did before this task.

    Why this lives in a file named `_th` even though the English side
    (`strings.json`) is one of the two parametrized cases: every other test
    in this file already loads `strings.json` as the English baseline to
    diff Thai against (see `_load_strings`, used throughout for key-parity
    and untranslated-value checks) — this is the same "compare th against en
    for one specific fact" shape, just checking presence of a marker instead
    of key-parity or non-identity. There's no other test file whose subject
    is "does this one English/Thai pair of strings agree," so splitting the
    English half out into a different file would separate two assertions
    that only make sense read together.
    """
    description = _get_by_path(loader(), _SCAN_INTERVAL_DESC_PATH)
    assert expected_marker in description, (
        f"{loader.__name__}: scan_interval data_description is missing the "
        f"'measured, not a vendor spec' marker ({expected_marker!r}) — see "
        "CLAUDE.md's poll-interval decision: the 115ms figure comes from one "
        "real unit's DEBUG log, not a datasheet, and losing this caveat in "
        "either language lets readers of that language mistake it for a spec "
        f"(got: {description!r})"
    )


# --- m-2 (cleanup-review.md): AC-2's wording fixes must not be silently revertible ---


def test_th_json_scan_interval_description_keeps_ac2_wording_fixes():
    """m-2 (cleanup-review.md, topic `cleanup` "เก็บตก") — guards both halves of
    AC-2 (`plan-cleanup.md`), which had ZERO test coverage before this: reviewer
    proved with mutation D that reverting the Thai scan_interval description
    entirely back to its pre-`docs-cleanup` wording left the full suite green
    (190 passed), silently undoing a whole topic's worth of Thai-wording work.

    AC-2(ข) — technical-term policy: `entry` (English) must survive, `รายการ`
    (the ambiguous Thai word it replaced) must not come back. This is a
    marker-word check like Q-1's `measured`/`วัดจริง` above, not a full-sentence
    lock, per `plan-cleanup.md`'s own "ห้ามเขียนเทสต์ที่ล็อกทั้งประโยค" rule
    (locking the whole sentence would go red on every future wording polish
    that isn't actually a regression).

    AC-2(ก) — `เวลาเลือกค่า` moved from trailing the sentence to leading its
    final clause. Locking exact position (e.g. "must be the Nth word") would
    be just as brittle as locking the whole sentence, so this only asserts
    the one thing that must never be true again: the description must NOT
    *end* with `เวลาเลือกค่า` (the trailing form the wording was fixed away
    from). Any non-trailing placement — including a future rewording that
    moves the phrase elsewhere in the middle — is fine and won't trip this.
    """
    description = _get_by_path(_load_th(), _SCAN_INTERVAL_DESC_PATH)

    assert "entry" in description, (
        f"th.json scan_interval description lost the English technical term "
        f"'entry' (AC-2(ข), cleanup-review.md m-2) — got: {description!r}"
    )
    assert "รายการ" not in description, (
        f"th.json scan_interval description brought back the ambiguous Thai "
        f"word 'รายการ' that AC-2(ข) replaced with 'entry' — got: {description!r}"
    )
    assert not description.rstrip(" .").endswith("เวลาเลือกค่า"), (
        f"th.json scan_interval description has 'เวลาเลือกค่า' trailing the "
        f"sentence again — AC-2(ก) (cleanup-review.md m-2) moved it to lead "
        f"the final clause instead — got: {description!r}"
    )


# --- Q-6: guard that the existing en-parity test stays en-only ----------------


def test_en_parity_test_file_still_covers_only_en():
    """Q-6: this repo's existing strings.json==translations/en.json byte-parity
    test (tests/test_strings_translations.py) must not be widened into an
    all-language parity test — th.json has its own, separate parity test above
    (Q-2). This is a static guard against someone "helpfully" editing that file
    instead of adding here: if th.json ever gets referenced from there, this
    fails and flags it as a regression to undo, not a green light."""
    src = (Path(__file__).parent / "test_strings_translations.py").read_text(encoding="utf-8")
    assert "th.json" not in src
    assert '"th"' not in src
