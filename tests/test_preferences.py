"""Tests for aipager.preferences — per-scope /settings storage."""

from __future__ import annotations

import json

import pytest

from aipager import preferences as prefs


# ---- defaults ------------------------------------------------------------

def test_unknown_scope_returns_defaults():
    p = prefs.get_preferences(999)
    assert p.simple_formatting is False
    assert p.answer_length == "none"
    assert p.language_level == "none"


def test_layout_default_seeded_by_keep_finished_card_true(monkeypatch):
    monkeypatch.setattr(prefs, "KEEP_FINISHED_CARD", True)
    assert prefs.get_preferences(999).layout == "card"


def test_layout_default_seeded_by_keep_finished_card_false(monkeypatch):
    monkeypatch.setattr(prefs, "KEEP_FINISHED_CARD", False)
    assert prefs.get_preferences(999).layout == "replace"


def test_stored_layout_wins_over_keep_finished_card_seed(monkeypatch):
    monkeypatch.setattr(prefs, "KEEP_FINISHED_CARD", False)
    prefs.set_preference(42, "layout", "card")
    assert prefs.get_preferences(42).layout == "card"


# ---- round-trip ------------------------------------------------------------

def test_set_and_get_round_trips():
    prefs.set_preference(42, "simple_formatting", True)
    prefs.set_preference(42, "answer_length", "short")
    prefs.set_preference(42, "language_level", "advanced")
    p = prefs.get_preferences(42)
    assert p.simple_formatting is True
    assert p.answer_length == "short"
    assert p.language_level == "advanced"


def test_negative_group_chat_id_round_trips():
    prefs.set_preference(-1001234567890, "answer_length", "long")
    assert prefs.get_preferences(-1001234567890).answer_length == "long"


def test_set_preference_persists_to_disk(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    prefs.set_preference(42, "simple_formatting", True)
    data = json.loads(path.read_text())
    assert data["42"]["simple_formatting"] is True


def test_other_scopes_unaffected_by_a_write():
    prefs.set_preference(1, "answer_length", "short")
    prefs.set_preference(2, "answer_length", "long")
    assert prefs.get_preferences(1).answer_length == "short"
    assert prefs.get_preferences(2).answer_length == "long"


def test_atomic_write_via_os_replace(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    calls = []
    real_replace = prefs.os.replace

    def _spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)
    monkeypatch.setattr(prefs.os, "replace", _spy)
    prefs.set_preference(42, "layout", "replace")
    assert calls and calls[0][0].endswith(".tmp")
    assert calls[0][1] == str(path)


# ---- invalid input ----------------------------------------------------

def test_set_preference_unknown_field_raises_no_write(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    with pytest.raises(ValueError):
        prefs.set_preference(42, "bogus_field", "x")
    assert not path.exists()


def test_set_preference_invalid_value_raises_no_write(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    with pytest.raises(ValueError):
        prefs.set_preference(42, "layout", "sideways")
    assert not path.exists()


def test_set_preference_wrong_type_raises(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    with pytest.raises(ValueError):
        prefs.set_preference(42, "simple_formatting", "yes")  # str, not bool
    assert not path.exists()


# ---- fail-safe loading --------------------------------------------------

def test_corrupt_json_file_degrades_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    p = prefs.get_preferences(42)
    assert p.answer_length == "none"


def test_non_object_root_degrades_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    p = prefs.get_preferences(42)
    assert p.answer_length == "none"


def test_corrupt_scope_entry_isolated(tmp_path, monkeypatch):
    """One scope being garbage doesn't touch any other scope."""
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({
        "1": "not an object",
        "2": {"answer_length": "long"},
    }))
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    assert prefs.get_preferences(1).answer_length == "none"
    assert prefs.get_preferences(2).answer_length == "long"


def test_corrupt_field_value_isolated_within_scope(tmp_path, monkeypatch):
    """A bad value for one field doesn't blank out the scope's other fields."""
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({
        "1": {"layout": "sideways", "answer_length": "medium"},
    }))
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    p = prefs.get_preferences(1)
    assert p.layout in ("card", "replace")  # falls back to the seed default
    assert p.answer_length == "medium"  # unaffected


def test_missing_file_never_writes_on_read(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    prefs.get_preferences(42)
    assert not path.exists()


def test_next_write_overwrites_a_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(prefs, "_PREFERENCES_PATH", path)
    prefs.set_preference(1, "answer_length", "short")
    data = json.loads(path.read_text())
    assert data["1"]["answer_length"] == "short"


# ---- style_text ------------------------------------------------------------

def test_style_text_empty_when_all_default():
    p = prefs.Preferences(layout="card", simple_formatting=False,
                          answer_length="none", language_level="none")
    assert prefs.style_text(p) == ""


def test_style_text_formatting_only():
    p = prefs.Preferences(layout="card", simple_formatting=True,
                          answer_length="none", language_level="none")
    text = prefs.style_text(p)
    assert text.startswith("Apply this reply-style guidance")
    assert "No tables" in text
    assert text.count("\n- ") == 1


def test_style_text_order_is_formatting_length_level():
    p = prefs.Preferences(layout="card", simple_formatting=True,
                          answer_length="short", language_level="advanced")
    text = prefs.style_text(p)
    fmt_idx = text.index("No tables")
    len_idx = text.index("Keep the answer short")
    lvl_idx = text.index("precise technical language")
    assert fmt_idx < len_idx < lvl_idx


def test_style_text_length_and_level_variants():
    for length, phrase in prefs._LENGTH_LINES.items():
        p = prefs.Preferences(layout="card", simple_formatting=False,
                              answer_length=length, language_level="none")
        assert phrase in prefs.style_text(p)
    for level, phrase in prefs._LEVEL_LINES.items():
        p = prefs.Preferences(layout="card", simple_formatting=False,
                              answer_length="none", language_level=level)
        assert phrase in prefs.style_text(p)


def test_style_text_none_values_inject_nothing():
    p = prefs.Preferences(layout="card", simple_formatting=False,
                          answer_length="none", language_level="none")
    assert prefs.style_text(p) == ""
