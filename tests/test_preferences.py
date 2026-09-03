"""Tests for aipager.preferences — per-scope /settings storage."""

from __future__ import annotations

import json

import pytest

from aipager import preferences as prefs


# ---- defaults ------------------------------------------------------------


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


# ---- get_preferences never raises, for any input ---------------------------
#
# entrypoints.md's contract: "always returns a fully-resolved value — never
# raises, never returns None, regardless of whether chat_id has ever been
# seen before". A session with an unstamped scope (``TrackedSession.
# scope_chat_id == 0``) or one whose scope resolution otherwise degrades to
# ``None``/``""`` must resolve to plain defaults, not blow up.
@pytest.mark.parametrize("chat_id", [None, 0, "", -1001234567890, "-1", "abc"])
def test_get_preferences_never_raises(chat_id):
    p = prefs.get_preferences(chat_id)
    assert p.layout in ("card", "merged", "replace")
    assert p.simple_formatting is False
    assert p.answer_length == "none"
    assert p.language_level == "none"


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

def test_style_text_has_only_the_delivery_rule_when_all_default():
    # Contract change ("status-line-at-card-bottom"): style_text always
    # carries the unconditional delivery-rule bullet, so "nothing injected"
    # now means "no /settings bullets", not an empty string.
    p = prefs.Preferences(layout="card", simple_formatting=False,
                          answer_length="none", language_level="none")
    text = prefs.style_text(p)
    assert _style_bullets(text) == []
    assert prefs._DELIVERY_LINE in text


def test_style_text_formatting_only():
    p = prefs.Preferences(layout="card", simple_formatting=True,
                          answer_length="none", language_level="none")
    text = prefs.style_text(p)
    assert text.startswith("Apply this reply guidance")
    assert "No tables" in text
    # one style bullet + the unconditional delivery rule
    assert _style_bullets(text) == [prefs._FORMATTING_LINE]


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


def test_style_text_none_values_inject_no_style_bullets():
    # Contract change ("status-line-at-card-bottom"): style_text always
    # carries the unconditional delivery-rule bullet, so "nothing injected"
    # now means "no /settings bullets", not an empty string.
    p = prefs.Preferences(layout="card", simple_formatting=False,
                          answer_length="none", language_level="none")
    assert _style_bullets(prefs.style_text(p)) == []


def test_extra_short_is_a_valid_length_with_its_own_line():
    """Added on user request 2026-08-15 — tighter than "short"."""
    p = prefs.set_preference(777, "answer_length", "xshort")
    assert p.answer_length == "xshort"
    text = prefs.style_text(p)
    assert "one or two sentences" in text
    # A distinct instruction, not an alias for "short".
    short_text = prefs.style_text(
        prefs.set_preference(777, "answer_length", "short")
    )
    assert text != short_text


def test_extra_short_survives_a_reload():
    prefs.set_preference(778, "answer_length", "xshort")
    assert prefs.get_preferences(778).answer_length == "xshort"


# ---- is_valid_value ---------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("layout", "card"), ("layout", "merged"), ("layout", "replace"),
    ("simple_formatting", True), ("simple_formatting", False),
    ("answer_length", "none"), ("answer_length", "xshort"),
    ("language_level", "none"), ("language_level", "advanced"),
])
def test_is_valid_value_accepts_every_real_option(field, value):
    assert prefs.is_valid_value(field, value) is True


@pytest.mark.parametrize("field,value", [
    ("layout", "sideways"),
    ("simple_formatting", "yes"),   # str, not bool — must not pass as truthy
    ("simple_formatting", 1),       # int, not bool
    ("answer_length", "enormous"),
    ("language_level", None),
    ("not_a_real_field", "card"),
])
def test_is_valid_value_rejects_bad_field_or_value(field, value):
    assert prefs.is_valid_value(field, value) is False


def test_is_valid_value_never_raises_on_unknown_field():
    # The whole point: an unknown field is a normal `False`, not KeyError.
    assert prefs.is_valid_value("bogus", "whatever") is False


# ---- resolve_preferences ------------------------------------------------
#
# Success criteria this file pins directly (see design.md):
# - resolve_preferences(scope, {}) == get_preferences(scope) exactly.
# - An override wins for its own field only; the other three track scope.
# - Changing the scope default after an override is set moves
#   `get_preferences`'s value but never the override itself.
# - An invalid/corrupt override degrades to "unset" for that field alone.

def test_resolve_with_no_overrides_matches_get_preferences_exactly():
    prefs.set_preference(50, "answer_length", "long")
    assert prefs.resolve_preferences(50, None) == prefs.get_preferences(50)
    assert prefs.resolve_preferences(50, {}) == prefs.get_preferences(50)


def test_resolve_override_wins_regardless_of_scope_value():
    prefs.set_preference(51, "answer_length", "long")
    resolved = prefs.resolve_preferences(51, {"answer_length": "short"})
    assert resolved.answer_length == "short"


def test_resolve_override_touches_only_its_own_field():
    prefs.set_preference(52, "answer_length", "long")
    prefs.set_preference(52, "language_level", "advanced")
    resolved = prefs.resolve_preferences(52, {"answer_length": "short"})
    # The overridden field changed...
    assert resolved.answer_length == "short"
    # ...but every other field still tracks the SCOPE's own value, not some
    # blanked-out default. This is the guard most likely to be implemented
    # backwards: resolve_preferences must not silently discard the scope's
    # other settings just because one field is overridden.
    assert resolved.language_level == "advanced"
    assert resolved.layout == prefs.get_preferences(52).layout
    assert resolved.simple_formatting == prefs.get_preferences(52).simple_formatting


def test_resolve_all_four_fields_can_be_overridden_independently():
    overrides = {
        "layout": "merged",
        "simple_formatting": True,
        "answer_length": "xshort",
        "language_level": "simple",
    }
    resolved = prefs.resolve_preferences(53, overrides)
    assert resolved.layout == "merged"
    assert resolved.simple_formatting is True
    assert resolved.answer_length == "xshort"
    assert resolved.language_level == "simple"


def test_resolve_none_is_unset_not_a_selectable_value():
    """None must mean 'no override here', never a literal override value —
    collapsing that would destroy the tri-state and (for answer_length /
    language_level) collide with the real, selectable string "none"."""
    resolved = prefs.resolve_preferences(54, {"answer_length": None})
    assert resolved.answer_length == prefs.get_preferences(54).answer_length


def test_resolve_string_none_is_a_real_selectable_value_distinct_from_unset():
    """The operator added the "none" *value* deliberately in v0.6.0 ("don't
    apply any rule"). It must be honoured as a real override, not treated
    as if the field were unset."""
    prefs.set_preference(55, "answer_length", "long")
    resolved = prefs.resolve_preferences(55, {"answer_length": "none"})
    assert resolved.answer_length == "none"
    # And it must be distinguishable, in the raw mapping itself, from an
    # actually-absent key — this is the mapping resolve_preferences takes,
    # not a JSON-serialized round trip, so `is None` is the correct test.
    assert resolved.answer_length is not None


def test_resolve_invalid_override_value_degrades_to_unset_for_that_field_only():
    prefs.set_preference(56, "answer_length", "long")
    prefs.set_preference(56, "language_level", "advanced")
    resolved = prefs.resolve_preferences(56, {
        "answer_length": "not-a-real-length",   # e.g. hand-edited state file
        "language_level": "simple",
    })
    assert resolved.answer_length == "long"       # fell back to scope
    assert resolved.language_level == "simple"    # this one still applied


def test_resolve_unknown_override_key_is_ignored_not_an_error():
    resolved = prefs.resolve_preferences(57, {"not_a_real_field": "x"})
    assert resolved == prefs.get_preferences(57)


def test_resolve_wrong_type_simple_formatting_override_degrades_to_scope():
    """`False` is a legal value for simple_formatting — a non-bool override
    (corrupt state) must fail validation, not be coerced by truthiness."""
    prefs.set_preference(58, "simple_formatting", True)
    resolved = prefs.resolve_preferences(58, {"simple_formatting": "true"})
    assert resolved.simple_formatting is True  # scope value, override rejected


def test_resolve_does_not_mutate_the_overrides_mapping():
    overrides = {"answer_length": "short"}
    before = dict(overrides)
    prefs.resolve_preferences(59, overrides)
    assert overrides == before


def test_resolve_returns_a_fresh_preferences_not_the_scope_cached_instance():
    scope_prefs = prefs.get_preferences(60)
    resolved = prefs.resolve_preferences(60, {"answer_length": "short"})
    assert resolved is not scope_prefs


# ---- scope-change independence — the tag/fill mechanic's foundation ----
#
# design.md's central invariant: `scope_default` (get_preferences, ignoring
# overrides) and `effective` (resolve_preferences, WITH overrides) must come
# from two independent calls that never contaminate each other. This test
# pins that at the pure-function level, before any HTTP payload exists.

def test_changing_scope_default_after_override_moves_scope_value_only():
    prefs.set_preference(61, "answer_length", "medium")
    overrides = {"answer_length": "short"}
    # Before the scope change.
    assert prefs.get_preferences(61).answer_length == "medium"
    assert prefs.resolve_preferences(61, overrides).answer_length == "short"

    # The scope default changes (e.g. an admin edits /settings)...
    prefs.set_preference(61, "answer_length", "long")

    # ...scope_default moves...
    assert prefs.get_preferences(61).answer_length == "long"
    # ...but the override — untouched — still wins, unaffected by the
    # scope's change. Overriding the operator's own construction: the
    # override dict itself was never re-read from anywhere, so this is
    # really asserting resolve_preferences never lets a stale scope read
    # leak into what should stay a session's own explicit choice.
    assert prefs.resolve_preferences(61, overrides).answer_length == "short"


# ---- diff_preview ("diff-preview-settings-toggle") -------------------------

def test_diff_preview_defaults_off_and_round_trips():
    assert prefs.get_preferences(7).diff_preview is False
    prefs.set_preference(7, "diff_preview", True)
    assert prefs.get_preferences(7).diff_preview is True
    prefs.set_preference(7, "diff_preview", False)
    assert prefs.get_preferences(7).diff_preview is False


def test_diff_preview_missing_from_stored_scope_resolves_false():
    """Guard 6: a preferences.json written before the field existed keeps
    every other field and resolves the new one to its default."""
    prefs._PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    prefs._PREFERENCES_PATH.write_text(json.dumps(
        {"9": {"layout": "merged", "answer_length": "short"}}), encoding="utf-8")
    prefs._cache = None
    p = prefs.get_preferences(9)
    assert p.diff_preview is False
    assert p.layout == "merged"
    assert p.answer_length == "short"


def test_diff_preview_rejects_non_bool():
    with pytest.raises(ValueError):
        prefs.set_preference(7, "diff_preview", "on")
    assert prefs.is_valid_value("diff_preview", True)
    assert not prefs.is_valid_value("diff_preview", 1)


def test_resolve_diff_preview_session_override_both_directions():
    prefs.set_preference(8, "diff_preview", False)
    assert prefs.resolve_preferences(8, {"diff_preview": True}).diff_preview is True
    prefs.set_preference(8, "diff_preview", True)
    assert prefs.resolve_preferences(8, {"diff_preview": False}).diff_preview is False
    # an invalid override falls back to the scope value, like every field
    assert prefs.resolve_preferences(8, {"diff_preview": "no"}).diff_preview is True
