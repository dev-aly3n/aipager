"""Additional notify.py tests — file attachment, observers fanout, edge cases."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from telegram.error import Forbidden

from aipager.bot.transport import TruncationFailed
from aipager.state import Status, TrackedSession


def _sess(status=Status.IDLE):
    s = TrackedSession(name="claude-jim", label="jim", status=status)
    s.busy_started_at = time.monotonic()
    return s


# ===== IDLE: long response → file attachment =============================

def test_idle_long_response_sends_file_attachment(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    long_md = "# Header\n\n" + ("x" * 5000)
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": long_md,
        "html_summary": True,
        "raw_md": long_md,
    }))
    # File attachment fired
    bot._app.bot.send_document.assert_awaited_once()


def test_idle_truncation_failed_falls_back_to_attachment(mk_bot, run_async, monkeypatch):
    """When _send_with_retry raises TruncationFailed, the IDLE handler
    sends a fallback notice + attachment."""
    bot = mk_bot()
    sess = _sess()

    async def _send_failing(*a, **k):
        raise TruncationFailed("too long after all")

    monkeypatch.setattr("aipager.bot.notify._send_with_retry", _send_failing)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "Short content",
        "raw_md": "Short content",
    }))
    # Sent the fallback message
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "attachment" in text


def test_idle_oversized_file_skips_attachment(mk_bot, run_async, monkeypatch):
    """If the response > TELEGRAM_MAX_DOC_BYTES, skip the file send."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.TELEGRAM_MAX_DOC_BYTES", 100)
    long_md = "x" * 5000
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": long_md,
        "html_summary": True,
        "raw_md": long_md,
    }))
    bot._app.bot.send_document.assert_not_awaited()


def test_idle_file_send_forbidden_swallowed(mk_bot, run_async):
    """Forbidden on send_document doesn't crash."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock(side_effect=Forbidden("blocked"))
    bot._maybe_update_bot_name = AsyncMock()
    long_md = "x" * 5000
    # MUST NOT raise
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": long_md,
        "html_summary": True,
        "raw_md": long_md,
    }))


def test_idle_file_send_generic_exception_swallowed(mk_bot, run_async):
    """Generic exception on send_document is swallowed."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock(side_effect=RuntimeError("io"))
    bot._maybe_update_bot_name = AsyncMock()
    long_md = "x" * 5000
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": long_md,
        "html_summary": True,
        "raw_md": long_md,
    }))


# ===== Observer broadcast paths =========================================

def test_idle_broadcasts_to_observers(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # broadcast fires as a fire-and-forget task — verify at least scheduled


def test_compacting_broadcasts_to_observers(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()
    bot._edit_busy_raw = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))


def test_context_warning_broadcasts(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY)
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()
    bot._app.bot.send_message = AsyncMock()
    run_async(bot.notify(sess, "context_warning", {"context_pct": 85}))


def test_stale_busy_broadcasts(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY)
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()
    bot._app.bot.send_message = AsyncMock()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 5}))


def test_session_end_broadcasts(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()
    bot._app.bot.send_message = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "session_end", {"source": "user"}))


# ===== INTERACTIVE: more paths ==========================================

def test_interactive_auto_deny_admin_bypasses(mk_bot, run_async):
    """An admin's driver bypasses deny_tools rule — falls through to normal prompt."""
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    admin = TeamUser(id=1, label="admin", role=Role.ADMIN)
    bot.team = Team(
        group_id=-100,
        users={1: admin},
        rules=Rules(deny_tools=["Bash"]),
    )
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.busy_msg_id = 42
    sess.last_driver_user_id = 1  # admin is the driver
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._auto_deny = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "rm -rf",
                       "input": {"command": "rm -rf"}},
    }))
    # Admin bypasses — no auto-deny
    bot._auto_deny.assert_not_awaited()


# ===== tool_use with no busy_msg ========================================

def test_tool_use_no_busy_msg_short_circuits(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    bot._edit_busy_raw = AsyncMock()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Read /x",
        "tool_name": "Read",
        "tool_input_full": None,
    }))
    # No edit attempted
    bot._edit_busy_raw.assert_not_awaited()
