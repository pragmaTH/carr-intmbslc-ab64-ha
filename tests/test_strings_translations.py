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
from pathlib import Path

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "carr_ab64"


def test_strings_json_equals_translations_en_json():
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    translations_en = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert strings == translations_en
