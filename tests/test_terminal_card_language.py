"""The busy card settled by a dialog answered at the terminal (roadmap
8.113) reads in the session chat's language, like its Stopped and Denied
siblings (review rev-iter1-003). English stays byte for byte."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, policy_snapshot, preferences
from aipager.bot import session_ops
from aipager.state import Status

from tests import terminal_end_tx

NAME = "claude-dlg"
CHAT = 424242


@pytest.fixture
def world(mk_bot, tmp_path):
    bot = mk_bot()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    bot._drain_next_queued = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "dlg"
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.status = Status.INTERACTIVE
    sess.busy_msg_id = 60
    sess.busy_started_at = time.monotonic() - 9
    sess.interactive_entered_at = time.monotonic() - 5
    sess.pending_tool_started_at = time.monotonic() - 6
    sess.parent_tool_started_at = sess.pending_tool_started_at
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    sess.transcript_path = str(tmp_path / "t.jsonl")
    # What a terminal "No" leaves (live, roadmap 8.152).
    terminal_end_tx.terminal_no(sess.transcript_path)
    policy_snapshot.mark_turn_open(NAME)
    return bot, sess


def _settle(bot, sess, run_async) -> list[str]:
    run_async(bot.notify(sess, "dialog_ended_in_terminal",
                         {"entered_at": sess.interactive_entered_at}))
    return [c.args[1] for c in bot._edit_busy_raw.await_args_list]


@pytest.mark.parametrize("code, word", [("fa", "ترمینال"), ("ru", "терминале")])
def test_a_terminal_no_settles_the_card_in_the_chats_language(world, run_async, code, word):
    bot, sess = world
    preferences.set_ui_language(CHAT, code)
    texts = _settle(bot, sess, run_async)
    want = i18n.get(code).html(session_ops.CARD_DENIED_TERMINAL, session="dlg")
    assert texts == [want]
    assert word in want and "Denied" not in want


@pytest.mark.parametrize("code, word", [("fa", "ترمینال"), ("ru", "терминале")])
def test_an_escaped_question_settles_the_card_in_the_chats_language(world, run_async,
                                                                    code, word):
    bot, sess = world
    preferences.set_ui_language(CHAT, code)
    sess.pending_permission = {"ask_question": True, "question": "Which one?"}
    texts = _settle(bot, sess, run_async)
    want = i18n.get(code).html(session_ops.CARD_STOPPED_TERMINAL, session="dlg")
    assert texts == [want]
    assert word in want and "Stopped" not in want


def test_english_is_unchanged():
    assert (session_ops._terminal_card_text("a<b", refused=True)
            == "🚫 <b>a&lt;b</b> · Denied in the terminal")
    assert (session_ops._terminal_card_text("a<b", refused=False)
            == "⚠️ <b>a&lt;b</b> · Stopped in the terminal")
