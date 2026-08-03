"""M1 case 18: strings.json and translations/en.json must stay byte-for-byte
equal as JSON objects.

strings.json is what HA's translation-string-extraction tooling reads; en.json is
what actually ships and renders at runtime. They drifting apart silently (e.g. one
edited without the other) means the source-of-truth text and the shipped text
disagree — this regression test exists specifically to catch that drift, not to
validate the content itself (see M1 in done/integration-core-fix.md: this fix
round rewrote the `unit` step text in both files after changing scan timing/
skip-unit-0 behavior).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "carr_ab64"


def _load_strings() -> dict:
    return json.loads((INTEGRATION_DIR / "strings.json").read_text())


def test_strings_json_equals_translations_en_json():
    strings = _load_strings()
    translations_en = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert strings == translations_en


def test_every_data_description_key_has_a_matching_data_key():
    """hassfest's translations schema (gen_data_entry_schema) requires every key in
    a step's `data_description` to also exist in that step's `data` — violating
    this fails hassfest but NOT plain pytest, so it needs its own check here
    rather than relying on `strings.json == translations/en.json` alone (see
    integration-core-fix4's "จุดที่ qa-engineer ควรเขียน test เพิ่ม")."""
    strings = _load_strings()
    offenders = []
    for step_id, step in strings["config"]["step"].items():
        data_keys = set(step.get("data", {}))
        for key in step.get("data_description", {}):
            if key not in data_keys:
                offenders.append(f"config.step.{step_id}.data_description.{key}")
    assert offenders == []


def test_no_stray_angle_bracket_placeholders_in_any_string_value():
    """Regression guard for the exact bug hit while drafting integration-core-fix5:
    a first-draft data_description used `<name>` as generic placeholder syntax and
    hassfest's translations validator rejected it as HTML
    ("the string should not contain HTML for dictionary value"). Walk every string
    leaf in strings.json and fail if any contains an angle-bracket pair, so this
    class of mistake is caught by plain pytest too, not only by a hassfest run
    that can't happen locally on this dev machine (see done/qa-core.md)."""
    strings = _load_strings()
    offenders: list[str] = []

    def _walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                _walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            if re.search(r"<[^\s>]*>", node):
                offenders.append(path)

    _walk(strings, "strings")
    assert offenders == []
