"""Black-box tests: aipager.preferences fail-safe loading + defaults.

Covers design.md's "Fail-safe rules" and Stage 1/entrypoints.md's contract
for ``get_preferences`` / ``set_preference`` / ``style_text``. Written
against the promises in spec.md / design.md / entrypoints.md only.

The on-disk location is never imported from ``aipager.preferences`` (it is
explicitly on the "Tester must not import" list in entrypoints.md); instead
we mirror the exact tmp-redirect the autouse ``_isolate_home_paths`` fixture
in ``tests/conftest.py`` already performs (``tmp_path/home/.config/aipager/
preferences.json``) using only the same ``tmp_path`` fixture instance every
test already receives, and write bytes at that path ourselves to simulate a
corrupt file.
"""

from __future__ import annotations

import json
import os

import pytest

from aipager.preferences import Preferences, get_preferences, set_preference, style_text


def _prefs_file(tmp_path):
    p = tmp_path / "home" / ".config" / "aipager" / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---- defaults are load-bearing ------------------------------------------


def _style_bullets(text: str) -> list[str]:
    """The /settings-driven bullets only.

    Contract change ("status-line-at-card-bottom"): style_text now always
    carries one unconditional delivery-rule bullet (a fact about how
    aipager delivers messages, not a style preference), so "no style set"
    means "no bullets besides that one" rather than an empty string.
    """
    from aipager.preferences import _DELIVERY_LINE
    return [
        ln[2:] for ln in text.splitlines()
        if ln.startswith("- ") and ln[2:] != _DELIVERY_LINE
    ]


def test_unseen_chat_id_never_raises_and_returns_defaults():
    prefs = get_preferences(1234567890)
    assert prefs.simple_formatting is False
    assert prefs.answer_length == "none"
    assert prefs.language_level == "none"


def test_unseen_chat_id_layout_defaults_to_card_when_keep_finished_card_on(monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    assert get_preferences(999001).layout == "card"


def test_unseen_chat_id_layout_seeds_to_replace_when_keep_finished_card_off(monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    assert get_preferences(999002).layout == "replace"


def test_style_text_empty_when_everything_default():
    prefs = get_preferences(999003)
    assert _style_bullets(style_text(prefs)) == []


def test_style_text_none_answer_length_injects_nothing():
    prefs = Preferences(layout="card", simple_formatting=False,
                        answer_length="none", language_level="none")
    assert _style_bullets(style_text(prefs)) == []


def test_style_text_language_none_and_normal_are_distinct():
    """The user's explicit correction: 'none' injects nothing, 'normal' is a
    real, separate instruction — they must not collapse to the same text."""
    none_prefs = Preferences(layout="card", simple_formatting=False,
                             answer_length="none", language_level="none")
    normal_prefs = Preferences(layout="card", simple_formatting=False,
                               answer_length="none", language_level="normal")
    none_text = style_text(none_prefs)
    normal_text = style_text(normal_prefs)
    # Contract change ("status-line-at-card-bottom"): both carry the
    # unconditional delivery rule, so the distinction is in the STYLE
    # bullets, not in emptiness.
    assert _style_bullets(none_text) == []
    assert _style_bullets(normal_text) != []
    assert none_text != normal_text
    assert "Use plain, professional language" in normal_text


def test_style_text_answer_length_none_and_short_are_distinct():
    none_prefs = Preferences(layout="card", simple_formatting=False,
                             answer_length="none", language_level="none")
    short_prefs = Preferences(layout="card", simple_formatting=False,
                              answer_length="short", language_level="none")
    assert _style_bullets(style_text(none_prefs)) == []
    short_text = style_text(short_prefs)
    assert _style_bullets(short_text) != []
    assert "Keep the answer short" in short_text


def test_style_text_simple_formatting_on_injects_formatting_rule():
    prefs = Preferences(layout="card", simple_formatting=True,
                        answer_length="none", language_level="none")
    text = style_text(prefs)
    assert text != ""
    assert "No tables" in text or "no tables" in text.lower()


def test_style_text_multiple_active_options_all_present():
    prefs = Preferences(layout="card", simple_formatting=True,
                        answer_length="long", language_level="advanced")
    text = style_text(prefs)
    assert "No tables" in text or "no tables" in text.lower()
    assert "long" in text.lower()
    assert "precise technical language" in text.lower() or "advanced" in text.lower()


# ---- set_preference validation -------------------------------------------

def test_set_preference_unknown_field_raises_valueerror():
    with pytest.raises(ValueError):
        set_preference(42, "not_a_real_field", "x")


def test_set_preference_invalid_value_raises_valueerror():
    with pytest.raises(ValueError):
        set_preference(42, "layout", "sideways")


def test_set_preference_invalid_value_does_not_write_disk(tmp_path):
    path = _prefs_file(tmp_path)
    with pytest.raises(ValueError):
        set_preference(4242, "answer_length", "extremely-long")
    assert not path.exists(), (
        "a rejected set_preference() call must not touch disk at all"
    )


def test_set_preference_round_trip_visible_without_restart():
    set_preference(555, "answer_length", "medium")
    assert get_preferences(555).answer_length == "medium"


def test_set_preference_persists_across_a_fresh_read(tmp_path, monkeypatch):
    """Simulate 'daemon restart': clear the in-memory cache and re-read."""
    set_preference(777, "language_level", "simple")
    import aipager.preferences as prefs_mod
    prefs_mod._cache = None  # force a fresh load from disk
    assert get_preferences(777).language_level == "simple"


# ---- read-only never writes -----------------------------------------------

def test_get_preferences_alone_never_creates_the_file(tmp_path):
    path = _prefs_file(tmp_path)
    get_preferences(1)
    get_preferences(-100)
    assert not path.exists(), "a pure read must never create preferences.json"


# ---- corrupt-file fail-safe -------------------------------------------

def test_missing_file_behaves_like_v050_defaults(tmp_path):
    path = _prefs_file(tmp_path)
    assert not path.exists()
    prefs = get_preferences(31337)
    assert prefs.simple_formatting is False
    assert prefs.answer_length == "none"
    assert prefs.language_level == "none"


def test_malformed_json_file_falls_back_to_defaults_no_crash(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text("{not valid json at all")
    prefs = get_preferences(31338)  # must not raise
    assert prefs.simple_formatting is False
    assert prefs.answer_length == "none"


def test_empty_file_falls_back_to_defaults_no_crash(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text("")
    prefs = get_preferences(31339)  # must not raise
    assert prefs.answer_length == "none"


def test_json_array_root_falls_back_to_defaults_no_crash(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps(["not", "an", "object"]))
    prefs = get_preferences(31340)  # must not raise
    assert prefs.answer_length == "none"


def test_json_scalar_root_falls_back_to_defaults_no_crash(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps("just a string"))
    prefs = get_preferences(31341)  # must not raise
    assert prefs.language_level == "none"


def test_corrupt_scope_entry_only_affects_that_scope(tmp_path):
    """A per-scope value that isn't a JSON object must not blank out any
    other scope's stored preferences."""
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps({
        "111": "not-a-dict-at-all",
        "222": {"layout": "merged"},
    }))
    broken = get_preferences(111)
    healthy = get_preferences(222)
    assert broken.answer_length == "none"  # degraded to defaults
    assert healthy.layout == "merged", (
        "scope 222's valid stored layout must survive scope 111 being corrupt"
    )


def test_unrecognised_field_value_only_affects_that_field(tmp_path):
    """One bad field must not blank out the rest of that scope's fields."""
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps({
        "333": {"layout": "sideways", "simple_formatting": True},
    }))
    prefs = get_preferences(333)
    assert prefs.simple_formatting is True, (
        "a sibling field's valid value must still load even though "
        "'layout' held an unrecognised value"
    )


def test_unrecognised_field_value_falls_back_to_that_fields_default(tmp_path, monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps({"334": {"layout": "sideways"}}))
    prefs = get_preferences(334)
    assert prefs.layout == "card"  # falls back to the built-in/seeded default


def test_negative_group_chat_id_round_trips_through_json_keys(tmp_path):
    """Group chat ids are negative; JSON object keys must be strings and
    round-trip via int()/str() per design.md's schema note."""
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps({"-1001234567890": {"answer_length": "long"}}))
    prefs = get_preferences(-1001234567890)
    assert prefs.answer_length == "long"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_unreadable_file_falls_back_to_defaults_no_crash(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text(json.dumps({"31342": {"answer_length": "long"}}))
    path.chmod(0o000)
    try:
        prefs = get_preferences(31342)  # must not raise
        assert prefs.answer_length == "none"
    finally:
        path.chmod(0o644)


def test_corrupt_file_is_healed_by_the_next_successful_write(tmp_path):
    path = _prefs_file(tmp_path)
    path.write_text("{ this is not json")
    get_preferences(1)  # trigger the fallback-to-empty load
    set_preference(9999, "simple_formatting", True)  # must not raise
    # File on disk must now be valid JSON reflecting the successful write.
    data = json.loads(path.read_text())
    assert data.get("9999", {}).get("simple_formatting") is True
