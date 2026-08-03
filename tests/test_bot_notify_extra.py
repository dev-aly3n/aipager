"""Additional notify.py tests — file attachment, observers fanout, edge cases."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import Forbidden

from aipager.state import Status, TrackedSession


def _sess(status=Status.IDLE):
    s = TrackedSession(name="claude-jim", label="jim", status=status)
    s.busy_started_at = time.monotonic()
    return s


@pytest.fixture(autouse=True)
def _mock_send_rich_message(monkeypatch):
    """Prevent real HTTP calls in every test in this module."""
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(return_value={}),
    )


# ===== IDLE: long response (>32 768 UTF-8 bytes) → file attachment =======

def test_idle_long_response_sends_file_attachment(mk_bot, run_async):
    """A raw_md body that exceeds 32 768 UTF-8 bytes triggers overflow:
    the body is truncated to a safe boundary AND a .txt attachment is sent."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    # 34 000 'x' chars → 34 000 UTF-8 bytes > 32 768.
    long_md = "x" * 34_000
    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": long_md,
    }))
    # File attachment fired
    bot._app.bot.send_document.assert_awaited_once()


def test_idle_under_limit_no_attachment(mk_bot, run_async):
    """A body under 32 768 UTF-8 bytes must NOT trigger the file attachment."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": "# Header\n\n" + ("x" * 500),
    }))
    bot._app.bot.send_document.assert_not_awaited()


def test_idle_truncation_failed_no_longer_applies(mk_bot, run_async, monkeypatch):
    """The new IDLE path does not use _send_with_retry, so TruncationFailed
    from that call site is never raised. The header goes via send_message
    and the body via sendRichMessage (both independently)."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "Short content",
        "raw_md": "Short content",
    }))
    # Only the header send_message fires; no fallback "attachment" notice.
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    assert "Finished" in text
    assert "attachment" not in text


def test_idle_oversized_file_skips_attachment(mk_bot, run_async, monkeypatch):
    """If the response > TELEGRAM_MAX_DOC_BYTES, skip the file send."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.TELEGRAM_MAX_DOC_BYTES", 100)
    long_md = "x" * 34_000
    run_async(bot.notify(sess, "idle_prompt", {
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
    long_md = "x" * 34_000
    # MUST NOT raise
    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": long_md,
    }))


def test_idle_file_send_generic_exception_swallowed(mk_bot, run_async):
    """Generic exception on send_document is swallowed."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock(side_effect=RuntimeError("io"))
    bot._maybe_update_bot_name = AsyncMock()
    long_md = "x" * 34_000
    run_async(bot.notify(sess, "idle_prompt", {
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
