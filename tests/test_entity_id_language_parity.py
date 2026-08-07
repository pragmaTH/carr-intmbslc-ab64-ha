"""Permanent version of the temporary test `reviewer` wrote for `thai-review.md`
C-7, then deleted per their role's own constraint (reviewer doesn't own test
files). See `.claude/shared/tasks/qa-thai3.md` (S-1..S-4) and
`.claude/shared/review/thai-review.md` S-1/C-6.

**What this test catches that `test_th_entity_id_language_is_not_yet_native_for_thai`
(R-4, tests/test_translations_th.py) cannot.** R-4 checks the *cause*: that
`hass.config.language == "th"` doesn't make HA's `NATIVE_ENTITY_IDS` gate treat
Thai as a Latin-script language, so `object_id_language` falls back to English
when generating `entity_id`. But `Entity.suggested_object_id` only takes that
fallback path when the entity class does NOT override the `name` property —
`homeassistant/helpers/entity.py`'s `suggested_object_id` has an identity check
(`type.__getattribute__(self.__class__, "name") is
type.__getattribute__(Entity, "name")`) that opts out of the whole mechanism
the moment any subclass defines its own `name` property instead of using
`_attr_translation_key`/`_attr_name`. If a future PR adds a new sensor and
writes `def name(self): return "..."` out of habit (rather than
`_attr_translation_key`, which is what every entity class in this integration
uses today — see thai-review.md C-6, which audited all of them; re-confirmed
here with a plain `grep -n "def name(self" custom_components/carr_ab64/*.py`
returning nothing), that entity's `entity_id` would silently start being
generated from the *displayed* (language-dependent) name instead of the
English translation key, R-4 would stay green throughout (it only ever checks
the HA-level list, never this integration's entity classes), and nobody would
notice until a Thai-language user's `entity_id` didn't match this repo's
README.

So this test asserts the *outcome* directly, black-box, the way a user would
experience it: set up this integration twice, once per language, and diff
what actually got generated in the registry.

**Implementation note — do NOT create a second `HomeAssistant` instance by
hand** (e.g. `pytest_homeassistant_custom_component.common.
async_test_home_assistant()`) to get a "fresh" registry per language. A first
draft of this file did exactly that and broke the rest of the suite: on any
assertion failure inside that manually-created instance's context block,
`hass.async_stop()` never runs, so `EVENT_HOMEASSISTANT_CLOSE` never fires,
the instance is never removed from pytest_homeassistant_custom_component's
module-global `INSTANCES` list, and the *next* test's `verify_cleanup`
autouse fixture finds `len(INSTANCES) >= 2` and tries to stop every leaked
instance — including ones whose event loop pytest already tore down —
raising `RuntimeError: Event loop is closed` in dozens of unrelated tests'
teardown. It also needs a manual `hass.data.pop("custom_components", ...)`
(what the `enable_custom_integrations` fixture normally does) that's easy to
forget, causing "Integration not found". The fix here uses only the
standard `hass`/`fake_clients` fixtures from `conftest.py` and reuses ONE
`hass` instance for both languages: set up for "en", capture the registry,
`hass.config_entries.async_remove()` the entry (which purges its entity
registry rows — verified empirically that this drops the count to 0), flip
`hass.config.language`, then set up a fresh entry. That new entry gets a new
`entry_id` (a fresh ULID `MockConfigEntry` assigns on construction), and
every entity's `unique_id` is `f"{entry.entry_id}_{field_key}"` — so despite
the *config entry's* dedup `unique_id` (host:port:unit_id) being reused on
purpose, every *entity* `unique_id` differs from the first round's. HA can't
match any registry row to a pre-existing one, so every entity row is created
from scratch and its `entity_id` is computed fresh under whatever
`hass.config.language` is at that moment — not carried over from the first
round. (Verified in thai-review.md's "รอบปิด S-1" C-11: registry rows == 0
before the second setup, and every entity `unique_id` differs between
rounds, e.g. `...CVNV8QD_indoor_temp` vs. `...WBV62X_indoor_temp`.)

**Verified by mutation, not just by inspection** (thai-review.md C-10):
monkeypatching a `name` property directly onto `AB64AdvancedSensor` (the
exact regression this test exists to catch — see above) makes
`test_entity_id_set_is_identical_between_en_and_th` fail with a concrete
diff of the forked entity_ids, while `test_th_entity_id_language_is_not_yet_native_for_thai`
(R-4) stays green under the identical mutation — empirical proof the two
tests guard different layers and neither is redundant with the other.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carr_ab64.const import CONF_ENABLE_ADVANCED, CONF_UNIT_ID, DOMAIN

from tests.conftest import TEST_HOST, TEST_NAME, TEST_PORT, TEST_UNIT_ID, seed_happy_path


async def _setup_entry(hass, fake_clients: dict) -> ConfigEntry:
    seed_happy_path(fake_clients)
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
    return entry


def _entity_names(hass, entry: ConfigEntry) -> dict[str, str | None]:
    """{entity_id: original_name}, read from the registry (not `hass.states`)
    so disabled-by-default entities — which still get an entity_id and an
    `original_name` at creation time, just no state — are included too. With
    advanced telemetry on, this integration creates 15 entities regardless of
    each one's individual enabled_default."""
    registry = er.async_get(hass)
    return {
        reg_entry.entity_id: reg_entry.original_name
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_entity_id_set_is_identical_between_en_and_th(hass, fake_clients):
    """S-1: the actual promise this integration makes (plan-thai.md's Goal:
    "entity_id ... ไม่เปลี่ยนแปลงเลยไม่ว่าตั้ง HA เป็นภาษาอะไร") — verified end-to-end
    by actually setting up the integration under both languages, not inferred
    from an HA-internals check."""
    hass.config.language = "en"
    en_entry = await _setup_entry(hass, fake_clients)
    en_names = _entity_names(hass, en_entry)

    assert await hass.config_entries.async_remove(en_entry.entry_id) == {"require_restart": False}
    await hass.async_block_till_done()
    assert _entity_names(hass, en_entry) == {}, "registry rows for the en entry should be purged by async_remove"

    hass.config.language = "th"
    th_entry = await _setup_entry(hass, fake_clients)
    th_names = _entity_names(hass, th_entry)

    # Guard against this test degrading into a silent tautology (thai-review.md
    # m-5): if the language flip stopped having any effect (e.g. a future HA
    # version requires `hass.config.async_update()` instead of a direct
    # attribute set to invalidate the translation cache), both rounds would
    # render in English and the entity_id sets below would trivially match
    # for the wrong reason. This assertion previously lived only in the
    # sibling test below (S-2) — relying on a *different* test function to
    # keep this one honest meant deleting that sibling would silently turn
    # this one into a tautology with no failing signal anywhere. Assert it
    # here too so this test stands on its own.
    assert en_names != th_names, (
        "language flip had no effect — both setup rounds rendered identical "
        f"entity names ({en_names!r}); this test is about to become a "
        "tautology (thai-review.md m-5) — check whether hass.config.language "
        "still invalidates the translation cache the way it used to"
    )

    assert set(en_names) == set(th_names), (
        f"entity_id sets differ between languages — missing from th: "
        f"{sorted(set(en_names) - set(th_names))}; extra in th: "
        f"{sorted(set(th_names) - set(en_names))}"
    )
    # Sanity: this integration creates 15 entities with advanced telemetry on
    # (13 sensor + 1 binary_sensor + 1 climate) — a set-equality check alone
    # could pass vacuously if setup silently created zero entities in both
    # (thai-review.md n-3). If this number changes because a sensor was
    # deliberately added/removed, update it here; if it changes for any
    # other reason, setup didn't create the entities it should have.
    assert len(en_names) == 15, (
        f"expected 15 entities (13 sensor + 1 binary_sensor + 1 climate) with "
        f"advanced telemetry on, got {len(en_names)}: {sorted(en_names)}"
    )


async def test_entity_names_actually_differ_between_en_and_th(hass, fake_clients):
    """S-2: the flip side of the test above — if `original_name` came back
    identical for en vs. th, the Thai translation isn't being applied at all
    (a different bug than entity_id forking, but one this same setup can
    catch for free). `climate.living_room_ac` is deliberately excluded: it
    has no `_attr_translation_key` (its display name is just the device
    name, which is user-chosen text, not a translated string) so identical
    `original_name` there is correct, not a translation failure.
    """
    hass.config.language = "en"
    en_entry = await _setup_entry(hass, fake_clients)
    en_names = _entity_names(hass, en_entry)

    await hass.config_entries.async_remove(en_entry.entry_id)
    await hass.async_block_till_done()

    hass.config.language = "th"
    th_entry = await _setup_entry(hass, fake_clients)
    th_names = _entity_names(hass, th_entry)

    # Guard against a vacuous pass (thai-review.md n-4): if setup silently
    # created zero entities in both rounds, `en_names` would be empty, the
    # loop below would never run, and `untranslated == []` would pass without
    # having checked anything.
    assert len(en_names) == 15, f"expected 15 entities, got {len(en_names)}: {sorted(en_names)}"

    untranslated = [
        entity_id
        for entity_id, en_name in en_names.items()
        if en_name is not None and en_name == th_names.get(entity_id)
    ]
    assert untranslated == [], f"entities with identical name in en and th (untranslated?): {untranslated}"
