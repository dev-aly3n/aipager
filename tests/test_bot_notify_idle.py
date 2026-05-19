"""Tests for the IDLE + INTERACTIVE branches of NotifyMixin.notify.

The bot's `notify()` dispatcher dispatches on event type for the
"live busy-status events" (tool_use, subagent_*, compacting, etc.)
but ALSO has two big status-based branches:
- ``sess.status == Status.IDLE``: send the final response summary, optionally
  with a file attachment when the response is too long.
- ``sess.status == Status.INTERACTIVE``: render an inline permission prompt.

This file covers those paths.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from aipager.state import Status, TrackedSession


def _sess(label="jim", status=Status.IDLE, *, busy_msg_id=None):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    return s


# ---- IDLE: simple "Finished" message ------------------------------------

def test_idle_sends_finished_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Finished" in text
    assert "jim" in text


def test_idle_clears_busy_msg_when_present(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_awaited_once()
    assert sess.busy_msg_id is None


def test_idle_swallows_delete_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock(side_effect=BadRequest("old"))
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # Cleared anyway
    assert sess.busy_msg_id is None


def test_idle_marks_tools_done_clears_subagents(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.tool_history = [("Bash: ls", False), ("Read: /x", False)]
    sess.active_subagents = {"a1": {"type": "x"}}
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # All tools marked done
    assert all(done is True for _, done in sess.tool_history)
    assert sess.active_subagents == {}


def test_idle_with_short_summary_includes_blockquote(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "Short reply"}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Short reply" in text


def test_idle_with_html_summary_preserves_html(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    html_summary = "<code>print(1)</code>"
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": html_summary, "html_summary": True,
    }))
    text = bot._app.bot.send_message.await_args.args[1]
    # HTML markers preserved (not escaped)
    assert "<code>" in text


def test_idle_shows_elapsed_time(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_started_at = time.monotonic() - 75  # 1m 15s
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    text = bot._app.bot.send_message.await_args.args[1]
    # Should include "1m" or "75s"
    assert "m" in text or "75s" in text


def test_idle_shows_lines_changed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_lines_added = 10
    sess.last_lines_removed = 5
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "+10" in text
    assert "-5" in text


def test_idle_clears_trigger_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.trigger_msg_id = 555
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    assert sess.trigger_msg_id is None  # reply cycle complete


# ---- IDLE: API error path -----------------------------------------------

def test_idle_detects_api_error_and_sends_friendly_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = "do thing"  # enables retry button
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 429 rate_limit_error",
    }))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Rate limit" in text or "rate limit" in text.lower()
    # Retry button attached (because last_prompt is set)
    kb = bot._app.bot.send_message.await_args.kwargs.get("reply_markup")
    assert kb is not None


def test_idle_api_error_no_last_prompt_no_retry_button(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = ""  # no retry possible
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 500 internal server error",
    }))
    kb = bot._app.bot.send_message.await_args.kwargs.get("reply_markup")
    assert kb is None


def test_idle_api_error_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = "x"
    bot._app.bot.send_message = AsyncMock(side_effect=BadRequest("nope"))
    # MUST NOT raise
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 429 rate_limit",
    }))


# ---- IDLE: pending-queue flush -----------------------------------------

def test_idle_flushes_next_queued_prompt(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.queue_prompt("queued prompt", 100)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # The queued prompt was popped and injected
    assert sess.pending_queue == []
    bot._send_busy_and_animate.assert_awaited_once()


# ---- INTERACTIVE: inline permission ------------------------------------

def test_interactive_with_busy_msg_inlines_permission(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    # pending_permission set, busy_msg_id used
    assert sess.pending_permission is not None
    assert sess.pending_permission["tool_summary"] == "ls"


def test_interactive_without_busy_msg_sends_separate(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=None)
    bot._stop_animation = MagicMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Permission needed" in text


def test_interactive_ask_user_question_inline(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_inline_ask_keyboard = MagicMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {
            "name": "AskUserQuestion",
            "input": {"questions": [{
                "question": "Pick one", "options": [
                    {"label": "A"}, {"label": "B"},
                ],
            }]},
        },
    }))
    assert sess.pending_permission["ask_question"] is True
    assert sess.pending_permission["question"] == "Pick one"


def test_interactive_ask_user_question_no_questions_degrades(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_permission_keyboard = MagicMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "AskUserQuestion",
                       "input": {"questions": []},
                       "summary": "Loading"},
    }))
    # Degraded to Allow/Deny keyboard
    bot._build_permission_keyboard.assert_called_once()


def test_interactive_inline_falls_back_when_edit_returns_none(mk_bot, run_async):
    """If _edit_busy_raw returns None (msg gone), fall back to separate."""
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=None)  # msg deleted
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    # Fallback sent a separate message
    bot._app.bot.send_message.assert_awaited_once()


def test_interactive_selector_keyboard_used_when_options_supplied(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=None)
    bot._stop_animation = MagicMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": None,
        "selector_text": "Pick option",
        "selector_options": [(1, "Yes"), (2, "No")],
    }))
    bot._app.bot.send_message.assert_awaited_once()


def test_interactive_team_rule_auto_denies_tool(mk_bot, run_async):
    """When team.yaml's deny_tools matches, the prompt is auto-denied."""
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=["Bash"]),
    )
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    # Driver is not an admin (so they ARE subject to the rule)
    bot.team.users[2] = TeamUser(id=2, label="dev", role=Role.DEVELOPER)
    sess.last_driver_user_id = 2
    bot._auto_deny = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "rm -rf /",
                       "input": {"command": "rm -rf /"}},
    }))
    bot._auto_deny.assert_awaited_once()
