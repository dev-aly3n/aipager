"""Tests for aipager.bot.settings_menu — pure render functions for /settings.

Both render functions are pure (no bot/I-O beyond the read inside
``get_preferences``), so they're exercised directly without a TelegramBot.
"""

from __future__ import annotations

from aipager import preferences as prefs
from aipager.bot import settings_menu as sm


def _all_callback_data(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def _all_texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


# ---- render_settings_root -------------------------------------------------

def test_root_has_four_sections_then_per_session_then_close():
    """Order is asserted, not just membership: Close stays last, and the
    per-session row sits directly above it. The chat-level sections
    keep their existing order and callback data untouched — adding the
    per-session entry point must not disturb what was already there.
    """
    _text, kb = sm.render_settings_root(1)
    data = _all_callback_data(kb)
    assert data == [
        "_:set:layout", "_:set:diffs", "_:set:formatting", "_:set:length",
        "_:set:level", "_:spref", "_:set:close",
    ]


def test_root_layout_button_always_marked_at_defaults():
    _text, kb = sm.render_settings_root(1)
    texts = _all_texts(kb)
    layout_text = texts[0]
    assert "✅" in layout_text
    # The other four, untouched, carry no marker at their default value.
    assert "✅" not in texts[1]  # diffs
    assert "✅" not in texts[2]
    assert "✅" not in texts[3]
    assert "✅" not in texts[4]
    assert "✅" not in texts[5]  # per-session
    assert "✅" not in texts[6]  # Close


def test_root_marks_customized_sections():
    prefs.set_preference(1, "simple_formatting", True)
    prefs.set_preference(1, "answer_length", "short")
    _text, kb = sm.render_settings_root(1)
    texts = _all_texts(kb)
    assert "✅" not in texts[1]  # diffs still default
    assert "✅" in texts[2]  # formatting
    assert "✅" in texts[3]  # length
    assert "✅" not in texts[4]  # level still default


def test_root_returns_markup_and_text():
    text, kb = sm.render_settings_root(1)
    assert isinstance(text, str) and text
    assert kb is not None


# ---- render_settings_section -----------------------------------------------

def test_unknown_section_returns_none():
    assert sm.render_settings_section(1, "nope") is None


def test_section_layout_has_three_values_and_back():
    _text, kb = sm.render_settings_section(1, "layout")
    data = _all_callback_data(kb)
    assert data == [
        "_:set:layout:card", "_:set:layout:merged",
        "_:set:layout:replace", "_:set:back",
    ]


def test_section_formatting_values_are_on_off():
    _text, kb = sm.render_settings_section(1, "formatting")
    data = _all_callback_data(kb)
    assert data == ["_:set:formatting:off", "_:set:formatting:on", "_:set:back"]


def test_section_length_values():
    _text, kb = sm.render_settings_section(1, "length")
    data = _all_callback_data(kb)
    assert data == [
        "_:set:length:none", "_:set:length:xshort", "_:set:length:short",
        "_:set:length:medium", "_:set:length:long", "_:set:back",
    ]


def test_section_level_values():
    _text, kb = sm.render_settings_section(1, "level")
    data = _all_callback_data(kb)
    assert data == [
        "_:set:level:none", "_:set:level:simple",
        "_:set:level:normal", "_:set:level:advanced", "_:set:back",
    ]


def test_section_exactly_one_value_marked_at_default():
    _text, kb = sm.render_settings_section(1, "length")
    texts = _all_texts(kb)
    marked = [t for t in texts if "✅" in t]
    assert len(marked) == 1
    assert "Don't apply any rule" in marked[0]


def test_section_marker_moves_after_set_preference():
    prefs.set_preference(1, "answer_length", "medium")
    _text, kb = sm.render_settings_section(1, "length")
    texts = _all_texts(kb)
    marked = [t for t in texts if "✅" in t]
    assert len(marked) == 1
    assert "Medium" in marked[0]


def test_section_back_button_present():
    _text, kb = sm.render_settings_section(1, "layout")
    texts = _all_texts(kb)
    assert any("Back" in t for t in texts)


# ---- callback_data 64-byte cap ---------------------------------------------

def test_all_callback_data_under_64_bytes():
    _root_text, root_kb = sm.render_settings_root(1)
    all_data = _all_callback_data(root_kb)
    for section in sm.SECTIONS:
        _t, kb = sm.render_settings_section(1, section)
        all_data.extend(_all_callback_data(kb))
    for data in all_data:
        assert len(data.encode("utf-8")) <= 64, data


# ---- diff previews ("diff-preview-settings-toggle") ------------------------

def test_section_diffs_values_are_on_off():
    _text, kb = sm.render_settings_section(1, "diffs")
    assert _all_callback_data(kb) == ["_:set:diffs:off", "_:set:diffs:on", "_:set:back"]


def test_root_marks_diffs_only_when_on():
    _text, kb = sm.render_settings_root(1)
    assert "Diff previews" in _all_texts(kb)[1]
    assert "✅" not in _all_texts(kb)[1]
    prefs.set_preference(1, "diff_preview", True)
    _text, kb = sm.render_settings_root(1)
    assert "✅" in _all_texts(kb)[1]


def test_schema_carries_diff_preview_as_a_boolean_section():
    entry = next(e for e in sm.settings_schema() if e["field"] == "diff_preview")
    assert entry["section"] == "diffs"
    assert [o["value"] for o in entry["options"]] == [False, True]
    assert all(o["help"] for o in entry["options"])
