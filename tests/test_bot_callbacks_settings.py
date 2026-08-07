"""Tests for the `_:set:*` /settings callback routing in callbacks.py.

Mirrors test_bot_callbacks_extra.py's naming/fixture style. Covers:
- navigation (root/section/back/close) edits the message in place,
- DM value-tap mutates + persists,
- group non-admin value-tap is refused (toast, no mutation),
- group admin value-tap succeeds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.policy import load_policy
from aipager.scope import Member, Scope


@pytest.fixture
def mk_query():
    def _mk(callback_data, *, user_id=12345, chat_id=-1001, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
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
    return _mk


def _group_scopes():
    return [Scope(
        chat_id=-1001, kind="group", label="dev",
        members=(
            Member(id=1, label="owner", role="owner"),
            Member(id=2, label="bob", role="user"),
        ),
    )]


def _dm_scopes():
    return [Scope(
        chat_id=555, kind="dm", label="dm",
        members=(Member(id=1, label="aly", role="owner"),),
    )]


# ---- navigation (always allowed, never gated) -----------------------------

def test_root_navigation_edits_in_place(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    kwargs = query.edit_message_text.await_args.kwargs
    assert kwargs.get("parse_mode") == "HTML"
    assert kwargs.get("reply_markup") is not None


def test_section_open_edits_in_place(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:layout", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "layout" in text.lower()


def test_back_returns_to_root(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:back", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    text = query.edit_message_text.await_args.args[0]
    assert "settings" in text.lower()


def test_close_removes_keyboard(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:close", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    query.edit_message_text.assert_not_awaited()


def test_unknown_section_toasts_invalid(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:bogus", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    # Two answer() calls: the eager ack, then the toast text.
    assert query.answer.await_args.args[0] == "Invalid callback"


# ---- DM value-tap: mutates + persists --------------------------------------

def test_dm_value_tap_mutates_and_persists(mk_bot, mk_query, run_async):
    bot = mk_bot()  # personal mode — scopes=None, _is_admin always True
    update, query = mk_query("_:set:formatting:on", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(555).simple_formatting is True
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "formatting" in text.lower()


def test_dm_scope_value_tap_mutates(mk_bot, mk_query, run_async):
    bot = mk_bot(scopes=_dm_scopes())
    bot.policy = load_policy()
    update, query = mk_query("_:set:length:short", chat_id=555, user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(555).answer_length == "short"


# ---- group: non-admin refused, admin succeeds ------------------------------

def test_group_non_admin_value_tap_refused(mk_bot, mk_query, run_async):
    bot = mk_bot(scopes=_group_scopes())
    bot.policy = load_policy()
    update, query = mk_query("_:set:layout:merged", chat_id=-1001, user_id=2)  # bob = user role
    run_async(bot._handle_callback(update, MagicMock()))
    # No mutation — falls back to the KEEP_FINISHED_CARD seed default.
    assert prefs.get_preferences(-1001).layout != "merged"
    # A toast fired with admin-related wording.
    toast_texts = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    assert any("admin" in t.lower() for t in toast_texts)
    assert any("settings" in t.lower() for t in toast_texts)
    query.edit_message_text.assert_not_awaited()


def test_group_admin_value_tap_succeeds(mk_bot, mk_query, run_async):
    bot = mk_bot(scopes=_group_scopes())
    bot.policy = load_policy()
    update, query = mk_query("_:set:layout:merged", chat_id=-1001, user_id=1)  # owner
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(-1001).layout == "merged"
    query.edit_message_text.assert_awaited_once()


def test_group_admin_marker_moves_in_place(mk_bot, mk_query, run_async):
    bot = mk_bot(scopes=_group_scopes())
    bot.policy = load_policy()
    update, query = mk_query("_:set:level:advanced", chat_id=-1001, user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    marked = [b.text for row in kb.inline_keyboard for b in row if "✅" in b.text]
    assert len(marked) == 1
    assert "Advanced" in marked[0]


def test_invalid_value_toasts_and_does_not_mutate(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:layout:sideways", chat_id=555)
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(555).layout != "sideways"
    assert query.answer.await_args.args[0] == "Invalid value"
