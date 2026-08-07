"""Black-box tests: the /settings admin gate contract from entrypoints.md.

- Navigation (`_:set`, `_:set:<section>`, `_:set:back`, `_:set:close`) is
  always allowed, regardless of admin status.
- Value taps (`_:set:<section>:<value>`) in a GROUP scope are admin-gated:
  a non-admin tap must not mutate anything and must produce a
  ``show_alert=True`` toast; an admin tap succeeds.
- DM scopes skip the admin check entirely.

Driven through ``bot._handle_callback(update, context)`` — the same harness
already used throughout the test suite (see tests/test_bot_callbacks_extra.py,
tests/integration/perms_mode_ux/test_team_admin_gate.py) for a Telegram-only
surface that has no HTTP route of its own.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.preferences import get_preferences


DM_CHAT_ID = 424242
GROUP_CHAT_ID = -100987654321


def _mk_query(callback_data, *, chat_id, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def _toast_text_and_alert(query):
    """Extract whatever text + show_alert kwarg query.answer() was called
    with, regardless of positional/keyword calling convention."""
    call = query.answer.await_args
    text = None
    if call is not None:
        if call.args:
            text = call.args[0]
        elif "text" in call.kwargs:
            text = call.kwargs["text"]
    show_alert = call.kwargs.get("show_alert") if call is not None else None
    return text, show_alert


# ---- DM: no admin gate at all ---------------------------------------------

def test_dm_value_tap_mutates_the_preference(mk_bot, run_async):
    bot = mk_bot()
    update, query = _mk_query("_:set:length:short", chat_id=DM_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    assert get_preferences(DM_CHAT_ID).answer_length == "short"


def test_dm_value_tap_never_consults_is_admin(mk_bot, run_async):
    """entrypoints.md: DM scopes 'skip the check entirely'."""
    bot = mk_bot()
    def _boom(*a, **kw):
        raise AssertionError("_is_admin must not be called for a DM scope")
    bot._is_admin = MagicMock(side_effect=_boom)
    update, query = _mk_query("_:set:level:advanced", chat_id=DM_CHAT_ID + 1)
    run_async(bot._handle_callback(update, MagicMock()))
    assert get_preferences(DM_CHAT_ID + 1).language_level == "advanced"


def test_dm_value_tap_rerenders_in_place(mk_bot, run_async):
    bot = mk_bot()
    update, query = _mk_query("_:set:formatting:on", chat_id=DM_CHAT_ID + 2)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited()


# ---- group: navigation is never gated --------------------------------------

@pytest.mark.parametrize("cb", ["_:set", "_:set:layout", "_:set:formatting",
                                 "_:set:length", "_:set:level", "_:set:back",
                                 "_:set:close"])
def test_group_non_admin_navigation_never_shows_admin_alert(mk_bot, run_async, cb):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    update, query = _mk_query(cb, chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    text, show_alert = _toast_text_and_alert(query)
    assert show_alert is not True, (
        f"navigation action {cb!r} must be open to every scope member, "
        f"but got a show_alert toast: {text!r}"
    )


def test_group_non_admin_can_open_root_menu(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    update, query = _mk_query("_:set", chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited()


def test_close_removes_the_inline_keyboard(mk_bot, run_async):
    """entrypoints.md: '_:set:close | Remove the inline keyboard
    (edit reply_markup=None)'."""
    bot = mk_bot()
    update, query = _mk_query("_:set:close", chat_id=DM_CHAT_ID + 3)
    run_async(bot._handle_callback(update, MagicMock()))
    # The implementation may express "remove the keyboard" either as
    # edit_message_text(..., reply_markup=None) or the narrower
    # edit_message_reply_markup(reply_markup=None) — entrypoints.md only
    # commits to the observable effect (reply_markup=None), not the method.
    reply_markup = "UNSET"
    for mock_call in query.mock_calls:
        name = mock_call[0]
        kwargs = mock_call[2]
        if name in ("edit_message_text", "edit_message_reply_markup", "()"):
            if "reply_markup" in kwargs:
                reply_markup = kwargs["reply_markup"]
    assert reply_markup is None, (
        f"'_:set:close' must remove the keyboard (reply_markup=None); "
        f"calls were: {query.mock_calls!r}"
    )


def test_back_returns_to_the_root_menu_content(mk_bot, run_async):
    """entrypoints.md: '_:set:back | Return to the root menu'. The edited
    text after Back must match what render_settings_root would show."""
    from aipager.bot.settings_menu import render_settings_root
    chat_id = DM_CHAT_ID + 4
    bot = mk_bot()
    update, query = _mk_query("_:set:back", chat_id=chat_id)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited()
    expected_text, _kb = render_settings_root(chat_id)
    call = query.edit_message_text.await_args
    actual_text = call.args[0] if call.args else call.kwargs.get("text")
    assert actual_text == expected_text, (
        "'_:set:back' must re-render exactly the root menu content"
    )


def test_open_root_menu_content_matches_render_settings_root(mk_bot, run_async):
    """'/settings' opening (here simulated by the '_:set' re-render action)
    must show the same content render_settings_root produces."""
    from aipager.bot.settings_menu import render_settings_root
    chat_id = DM_CHAT_ID + 5
    bot = mk_bot()
    update, query = _mk_query("_:set", chat_id=chat_id)
    run_async(bot._handle_callback(update, MagicMock()))
    expected_text, _kb = render_settings_root(chat_id)
    call = query.edit_message_text.await_args
    actual_text = call.args[0] if call.args else call.kwargs.get("text")
    assert actual_text == expected_text


def test_section_open_content_matches_render_settings_section(mk_bot, run_async):
    from aipager.bot.settings_menu import render_settings_section
    chat_id = DM_CHAT_ID + 6
    bot = mk_bot()
    update, query = _mk_query("_:set:length", chat_id=chat_id)
    run_async(bot._handle_callback(update, MagicMock()))
    expected_text, _kb = render_settings_section(chat_id, "length")
    call = query.edit_message_text.await_args
    actual_text = call.args[0] if call.args else call.kwargs.get("text")
    assert actual_text == expected_text


# ---- group: value taps are admin-gated -------------------------------------

def test_group_non_admin_value_tap_does_not_mutate(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    before = get_preferences(GROUP_CHAT_ID).answer_length
    update, query = _mk_query("_:set:length:long", chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    assert get_preferences(GROUP_CHAT_ID).answer_length == before


def test_group_non_admin_value_tap_shows_alert_toast(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    update, query = _mk_query("_:set:layout:merged", chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    _text, show_alert = _toast_text_and_alert(query)
    assert show_alert is True


def test_group_non_admin_value_tap_toast_mentions_admin(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    update, query = _mk_query("_:set:layout:replace", chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    text, _show_alert = _toast_text_and_alert(query)
    assert text is not None
    assert "admin" in text.lower()


def test_group_admin_value_tap_mutates(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=True)
    update, query = _mk_query("_:set:level:simple", chat_id=GROUP_CHAT_ID - 1)
    run_async(bot._handle_callback(update, MagicMock()))
    assert get_preferences(GROUP_CHAT_ID - 1).language_level == "simple"


def test_group_admin_value_tap_does_not_show_admin_denial(mk_bot, run_async):
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=True)
    update, query = _mk_query("_:set:formatting:on", chat_id=GROUP_CHAT_ID - 2)
    run_async(bot._handle_callback(update, MagicMock()))
    text, show_alert = _toast_text_and_alert(query)
    assert show_alert is not True


def test_group_non_admin_denial_does_not_affect_other_group_scope(mk_bot, run_async):
    """A refused write in one group must never leak into another scope."""
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    other_group = GROUP_CHAT_ID - 3
    before_other = get_preferences(other_group).answer_length
    update, query = _mk_query("_:set:length:medium", chat_id=GROUP_CHAT_ID)
    run_async(bot._handle_callback(update, MagicMock()))
    assert get_preferences(other_group).answer_length == before_other
