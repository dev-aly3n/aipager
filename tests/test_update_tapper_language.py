"""Multilingual phase 3b: /update's pop-ups are the tapper's, its edits the
chat's. A Persian group with a member whose own chat is Russian: every
alert and toast after a tap reads Russian, the edited message Persian
(design section A; callbacks.py's tap gate included)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from aipager.bot import update_flow
from aipager.policy import load_policy
from aipager.scope import Member, Scope

GROUP, ADMIN, MEMBER = -1001, 4, 2


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="user")))])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.edit_message_text = AsyncMock()
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(MEMBER, "ru")
    preferences.set_ui_language(ADMIN, "ru")
    return b


def _tap(user, data="_:up:chk"):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=50, chat_id=GROUP)
    query.message.chat = MagicMock(id=GROUP, type="supergroup")
    query.from_user = MagicMock(id=user, language_code=None)
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query


def _answers(query):
    return [c.args[0] for c in query.answer.await_args_list if c.args]


def test_a_russian_member_gets_a_russian_denied_alert(bot, run_async):
    update, query = _tap(MEMBER)
    run_async(update_flow.handle_callback(bot, update, query, "_", "up:go:ap"))
    assert _answers(query) == ["🚫 Обновлять aipager может только админ."]
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_the_tap_gate_update_refusal_is_the_tappers(bot, run_async):
    """callbacks.py's gate refuses an update tap before /update sees it:
    the alert is the tapper's language too."""
    update, query = _tap(MEMBER)
    run_async(bot._handle_callback(update, MagicMock()))
    assert _answers(query) == ["🚫 Обновлять aipager может только админ."]


def test_the_stale_toast_is_russian(bot, run_async):
    update, query = _tap(ADMIN)
    run_async(update_flow.handle_callback(bot, update, query, "_", "up:now:123"))
    assert _answers(query) == ["Это обновление уже завершено"]


def test_checking_toast_russian_edit_persian(bot, run_async):
    bot.updates._spawn = lambda coro: coro.close()
    update, query = _tap(ADMIN)
    run_async(update_flow.handle_callback(bot, update, query, "_", "up:chk"))
    assert _answers(query) == ["Проверяю…"]
    edit = query.edit_message_text.await_args.args[0]
    assert edit == "🔎 \u200fدر حال چک کردن…\u200f"
