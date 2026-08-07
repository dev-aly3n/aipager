"""Black-box tests: aipager.bot.settings_menu render functions.

Both ``render_settings_root`` and ``render_settings_section`` are documented
in entrypoints.md as pure, side-effect-free functions safe to call directly.
We assert against the literal callback_data contract table in
entrypoints.md — that table is the load-bearing 64-byte-capped protocol
between a button tap and the routing logic, so it is asserted on literally.
"""

from __future__ import annotations

from aipager.bot.settings_menu import render_settings_root, render_settings_section
from aipager.preferences import set_preference


def _all_callback_data(keyboard):
    return [btn.callback_data for row in keyboard.inline_keyboard for btn in row]


def _marked_buttons(keyboard):
    return [btn for row in keyboard.inline_keyboard for btn in row if "✅" in btn.text]


# ---- root menu -------------------------------------------------------------

def test_root_has_all_four_section_actions():
    _, kb = render_settings_root(1)
    cbs = _all_callback_data(kb)
    for expected in ("_:set:layout", "_:set:formatting", "_:set:length", "_:set:level"):
        assert expected in cbs, f"missing {expected!r} in root keyboard: {cbs}"


def test_root_has_close_button():
    _, kb = render_settings_root(1)
    assert "_:set:close" in _all_callback_data(kb)


def test_root_does_not_offer_back():
    """Root is the top level; a Back action there would be a dead end."""
    _, kb = render_settings_root(1)
    assert "_:set:back" not in _all_callback_data(kb)


def test_root_exactly_one_marker_reflecting_current_prefs():
    """entrypoints.md: 'Exactly one button among the four section rows
    contains a ✅ / current-value marker reflecting get_preferences'."""
    _, kb = render_settings_root(2)
    assert len(_marked_buttons(kb)) == 1


def test_root_callback_data_all_under_64_bytes():
    _, kb = render_settings_root(3)
    for cb in _all_callback_data(kb):
        assert len(cb.encode("utf-8")) <= 64, f"{cb!r} exceeds Telegram's 64-byte cap"


def test_root_still_exactly_one_marker_after_a_second_option_is_customized():
    """entrypoints.md states unconditionally: 'Exactly one button among the
    four section rows contains a ... marker reflecting get_preferences'.
    Customizing a second option (on top of the always-resolved layout
    section) must not produce a second marker."""
    chat_id = 4001
    set_preference(chat_id, "simple_formatting", True)
    _, kb_after = render_settings_root(chat_id)
    assert len(_marked_buttons(kb_after)) == 1


# ---- section menus ----------------------------------------------------------

def test_section_layout_has_all_three_values_and_back():
    _, kb = render_settings_section(5, "layout")
    cbs = _all_callback_data(kb)
    for expected in ("_:set:layout:card", "_:set:layout:merged",
                     "_:set:layout:replace", "_:set:back"):
        assert expected in cbs, f"missing {expected!r}: {cbs}"


def test_section_formatting_has_off_and_on():
    _, kb = render_settings_section(5, "formatting")
    cbs = _all_callback_data(kb)
    assert "_:set:formatting:off" in cbs
    assert "_:set:formatting:on" in cbs


def test_section_length_has_four_distinct_values_including_none():
    _, kb = render_settings_section(5, "length")
    cbs = _all_callback_data(kb)
    for expected in ("_:set:length:none", "_:set:length:short",
                     "_:set:length:medium", "_:set:length:long"):
        assert expected in cbs, f"missing {expected!r}: {cbs}"
    # "none" must be a genuinely separate button, not merged with another value
    assert len(set(cbs) & {
        "_:set:length:none", "_:set:length:short",
        "_:set:length:medium", "_:set:length:long",
    }) == 4


def test_section_level_none_and_normal_are_separate_buttons():
    """CRITICAL user correction: 'don't apply any rule' is its own button,
    never a synonym for 'normal'."""
    _, kb = render_settings_section(5, "level")
    cbs = _all_callback_data(kb)
    assert "_:set:level:none" in cbs
    assert "_:set:level:normal" in cbs
    assert "_:set:level:simple" in cbs
    assert "_:set:level:advanced" in cbs
    assert len(set(cbs)) == len(cbs), f"duplicate callback_data entries: {cbs}"


def test_section_unknown_returns_none():
    assert render_settings_section(5, "not-a-real-section") is None


def test_section_empty_string_returns_none():
    assert render_settings_section(5, "") is None


def test_section_exactly_one_value_marked_by_default():
    """Default layout is 'card' (KEEP_FINISHED_CARD truthy by default)."""
    _, kb = render_settings_section(6, "layout")
    assert len(_marked_buttons(kb)) == 1


def test_section_marker_reflects_a_changed_value():
    chat_id = 7001
    set_preference(chat_id, "answer_length", "short")
    _, kb = render_settings_section(chat_id, "length")
    marked = _marked_buttons(kb)
    assert len(marked) == 1
    assert "_:set:length:short" in [b.callback_data for b in marked]


def test_all_section_callback_data_under_64_bytes():
    for section in ("layout", "formatting", "length", "level"):
        _, kb = render_settings_section(8, section)
        for cb in _all_callback_data(kb):
            assert len(cb.encode("utf-8")) <= 64, (
                f"{section}: {cb!r} exceeds Telegram's 64-byte cap"
            )


def test_render_functions_have_no_side_effects_on_disk(tmp_path):
    path = tmp_path / "home" / ".config" / "aipager" / "preferences.json"
    render_settings_root(9)
    render_settings_section(9, "layout")
    render_settings_section(9, "formatting")
    render_settings_section(9, "length")
    render_settings_section(9, "level")
    assert not path.exists(), "pure render functions must not write to disk"
