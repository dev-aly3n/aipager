"""Tests for aipager.bot.notify.NotifyMixin.notify — event dispatch.

The notify() coroutine is the entry point that hook_receiver calls when
a session changes state. Each event type is a separate branch; we test
them one at a time, with mocked Telegram I/O so no network is touched.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, Forbidden

from aipager.state import SessionRegistry, Status, TrackedSession


def _sess(label="jim", *, status=Status.BUSY, busy_msg_id=100):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    return s


# ---- early-return paths --------------------------------------------------

def test_notify_no_app_is_noop(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    sess = _sess()
    # MUST NOT raise even though _app is None
    run_async(bot.notify(sess, "tool_use", {"tool_summary": "x"}))


def test_notify_pinned_update_does_nothing(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "pinned_update", {}))
    # No send_message, no edit_message_text
    bot._app.bot.send_message.assert_not_called()


# ---- user_prompt_submit -------------------------------------------------

def test_user_prompt_submit_skips_when_busy_msg_exists(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    # Mock _send_busy_and_animate to detect if it gets called
    bot._send_busy_and_animate = AsyncMock()
    run_async(bot.notify(sess, "user_prompt_submit", {}))
    bot._send_busy_and_animate.assert_not_awaited()


def test_user_prompt_submit_sends_busy_when_no_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._send_busy_and_animate = AsyncMock()
    run_async(bot.notify(sess, "user_prompt_submit", {}))
    bot._send_busy_and_animate.assert_awaited_once_with(sess)


# ---- tool_use ------------------------------------------------------------

def test_tool_use_appends_to_history(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)  # no edit fires
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Read: /x",
        "tool_name": "Read",
        "tool_input_full": None,
    }))
    assert sess.tool_history == [("Read: /x", False)]
    assert sess.last_tool_summary == "Read: /x"


def test_tool_use_write_fires_diff_preview(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._send_diff_preview = AsyncMock()
    monkeypatch.setenv("AIPAGER_DIFF_VIEW", "1")
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Write: /x",
        "tool_name": "Write",
        "tool_input_full": {"file_path": "/x", "content": "y"},
    }))
    # The diff-preview task is created (fire-and-forget)
    # We can't assert it was awaited (asyncio.create_task), but we
    # can assert tool_history is updated.
    assert ("Write: /x", False) in sess.tool_history


def test_tool_use_diff_preview_disabled_via_env(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._send_diff_preview = AsyncMock()
    monkeypatch.setenv("AIPAGER_DIFF_VIEW", "0")
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Write: /x",
        "tool_name": "Write",
        "tool_input_full": {"file_path": "/x", "content": "y"},
    }))
    bot._send_diff_preview.assert_not_awaited()


def test_tool_use_edits_busy_when_debounce_elapsed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.last_tool_edit_at = 0  # ensures debounce window has passed
    bot._edit_busy_raw = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
    }))
    bot._edit_busy_raw.assert_awaited_once()


def test_tool_use_clears_busy_msg_when_edit_returns_none(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.last_tool_edit_at = 0
    bot._edit_busy_raw = AsyncMock(return_value=None)  # message gone
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
    }))
    assert sess.busy_msg_id is None
    bot._stop_animation.assert_called_once()


# ---- tool_done / tool_failed --------------------------------------------

def test_tool_done_marks_last_undone_entry(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Read: /x", False), ("Bash: ls", False)]
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
    }))
    assert sess.tool_history == [("Read: /x", False), ("Bash: ls", True)]


def test_tool_failed_marks_with_failed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Bash: ls", False)]
    run_async(bot.notify(sess, "tool_failed", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
    }))
    assert sess.tool_history == [("Bash: ls", "failed")]


def test_tool_done_no_exact_match_marks_last_undone(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Read: /x", False), ("Bash: rm", False)]
    # Summary doesn't match either entry — marks the LAST undone
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "completely different",
    }))
    assert sess.tool_history == [("Read: /x", False), ("Bash: rm", True)]


# ---- subagent_start / subagent_stop ------------------------------------

def test_subagent_start_appends_and_increments_count(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.active_subagents["agent-1"] = {"type": "x", "started_at": 0.0, "history_idx": None}
    run_async(bot.notify(sess, "subagent_start", {
        "agent_id": "agent-1", "agent_type": "explore",
    }))
    assert sess.subagent_count_this_turn == 1
    # The tool_history got a new entry and its index was stored
    assert sess.active_subagents["agent-1"]["history_idx"] == 0
    assert "explore" in sess.tool_history[0][0]


def test_subagent_stop_marks_history_done(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 3.5, "history_idx": 0,
    }))
    assert sess.tool_history[0][1] is True
    assert "3s" in sess.tool_history[0][0]


def test_subagent_stop_long_elapsed_uses_minutes(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 125.0, "history_idx": 0,
    }))
    assert "m" in sess.tool_history[0][0]
    assert "5s" in sess.tool_history[0][0]


def test_subagent_stop_with_no_match_appends_done(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = []  # no matching start
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-x", "agent_type": "explore",
        "elapsed": 1.0, "history_idx": None,
    }))
    # New entry appended as done
    assert len(sess.tool_history) == 1
    assert sess.tool_history[0][1] is True


# ---- compacting ---------------------------------------------------------

def test_compacting_edits_busy_when_present(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._edit_busy_raw = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._stop_animation.assert_called_once()
    bot._edit_busy_raw.assert_awaited_once()


def test_compacting_sends_new_when_no_busy(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 555


# ---- context_warning ---------------------------------------------------

def test_context_warning_sends_with_compact_button(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "context_warning", {"context_pct": 85}))
    bot._app.bot.send_message.assert_awaited_once()
    call = bot._app.bot.send_message.await_args
    text = call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")
    assert "85%" in text
    assert call.kwargs.get("reply_markup") is not None


def test_context_warning_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(side_effect=BadRequest("nope"))
    # MUST NOT raise
    run_async(bot.notify(sess, "context_warning", {"context_pct": 85}))


# ---- stale_busy --------------------------------------------------------

def test_stale_busy_sends_alert(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 5}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "5+ min" in text
    assert "subscription" in text.lower()


def test_stale_busy_swallows_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
    run_async(bot.notify(sess, "stale_busy", {"minutes": 2}))


# ---- compact_done ------------------------------------------------------

def test_compact_done_edits_busy_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    # Skip the 2-second pause
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.notify.asyncio.sleep", _no_sleep)
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    assert sess.last_token_pct == 5
    bot._stop_animation.assert_called_once()
    bot._start_animation.assert_called_once()


def test_compact_done_sends_new_when_no_busy(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.notify.asyncio.sleep", _no_sleep)
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 10,
    }))
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 999


# ---- session_end -------------------------------------------------------

def test_session_end_deletes_busy_and_sends_alert(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._app.bot.delete_message = AsyncMock()
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    bot._app.bot.delete_message.assert_awaited_once()
    assert sess.busy_msg_id is None
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "crashed or killed" in text


def test_session_end_unknown_source_label(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    run_async(bot.notify(sess, "session_end", {"source": "completely-unknown"}))
    text = bot._app.bot.send_message.await_args.args[1]
    # Falls back to "exited" generic label
    assert "exited" in text


def test_session_end_user_logout(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    run_async(bot.notify(sess, "session_end", {"source": "logout"}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "logged out" in text


# ---- BUSY status branch -------------------------------------------------

def test_busy_status_edits_last_msg(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=None)
    sess.last_msg_id = 77
    bot._app.bot.edit_message_text = AsyncMock()
    # Pass an unrecognized event — falls through to the status-based branch
    run_async(bot.notify(sess, "unrecognized_event", {}))
    bot._app.bot.edit_message_text.assert_awaited_once()
    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs.get("message_id") == 77


def test_busy_status_swallows_edit_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=None)
    sess.last_msg_id = 77
    bot._app.bot.edit_message_text = AsyncMock(side_effect=BadRequest("old"))
    # Must not raise
    run_async(bot.notify(sess, "unrecognized_event", {}))
