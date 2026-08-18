"""Black-box tests for design.md success criteria 6, 7, 8 and 9 -- the
deadline sweeper.

- Criterion 9: `expired_compacting_sessions(sessions, now)` is pure (no
  I/O, no sleeping) -- entrypoints.md's own example construction is used
  verbatim.
- Criteria 6/7/8: `TelegramBot.notify(sess, "compact_timeout", {...})`'s
  observable text and BUSY-resume-vs-clear branches, per entrypoints.md's
  `TelegramBot.notify` "New in this feature" section.
- The sweeper's core design invariant (intent.md / design.md "Goal"): the
  watchdog that calls this must NOT be gated on `status == BUSY` -- a
  compacting session observed at `Status.IDLE` (the exact live-bug state)
  must still be swept. Verified against `SessionMonitor._scan()` per
  entrypoints.md's documented pattern (mirrors `test_session_monitor.py`).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.session_monitor import SessionMonitor, expired_compacting_sessions
from aipager.state import Status, TrackedSession, SessionRegistry


async def _coroutine_returning(value):
    return value


def _sess(label="jim", *, status, busy_msg_id=42):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    return s


def _edit_text(call) -> str:
    if "text" in call.kwargs:
        return call.kwargs["text"]
    return call.args[0]


# ===== Criterion 9: pure function, no waiting ==============================

def test_expired_compacting_sessions_empty_before_deadline():
    sess = TrackedSession(name="claude-x", label="x")
    sess.busy_msg_id = 1
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=5.0)
    assert expired_compacting_sessions({"x": sess}, now=1004.0) == []


def test_expired_compacting_sessions_includes_name_after_deadline():
    sess = TrackedSession(name="claude-x", label="x")
    sess.busy_msg_id = 1
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=5.0)
    assert expired_compacting_sessions({"x": sess}, now=1006.0) == ["x"]


def test_expired_compacting_sessions_empty_with_no_live_compaction():
    sess = TrackedSession(name="claude-x", label="x")
    assert expired_compacting_sessions({"x": sess}, now=99999.0) == []


def test_expired_compacting_sessions_empty_with_none_deadline():
    """deadline_seconds=None means no forced expiry, ever."""
    sess = TrackedSession(name="claude-x", label="x")
    sess.busy_msg_id = 1
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=None)
    assert expired_compacting_sessions({"x": sess}, now=10_000_000.0) == []


def test_expired_compacting_sessions_ignores_busy_only_sessions():
    sess = TrackedSession(name="claude-x", label="x")
    sess.busy_msg_id = 1  # plain busy card, never compacting
    assert expired_compacting_sessions({"x": sess}, now=99999.0) == []


# ===== Criterion 6: the timeout text itself ================================

def test_compact_timeout_text_contains_didnt_confirm_completion(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    text = _edit_text(bot._app.bot.edit_message_text.await_args)
    assert "didn't confirm completion" in text


def test_compact_timeout_text_never_contains_compacted(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    text = _edit_text(bot._app.bot.edit_message_text.await_args)
    assert "Compacted" not in text


def test_compact_timeout_text_contains_session_label(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(label="idioom", status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    text = _edit_text(bot._app.bot.edit_message_text.await_args)
    assert "idioom" in text


def test_compact_timeout_and_compact_done_texts_are_actually_distinct(
    mk_bot, run_async,
):
    """Runtime (not hand-typed) comparison of the two REAL texts the bot
    produces, tying criteria 4 and 6 together the way design.md demands
    ('textually distinct from criterion 4's text')."""
    bot_a = mk_bot()
    sess_a = _sess(status=Status.BUSY, busy_msg_id=42)
    sess_a.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot_a._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot_a._start_animation = MagicMock()
    run_async(bot_a.notify(sess_a, "compact_timeout", {"elapsed_seconds": 200.0}))
    timeout_text = _edit_text(bot_a._app.bot.edit_message_text.await_args)

    bot_b = mk_bot()
    sess_b = _sess(status=Status.BUSY, busy_msg_id=42)
    bot_b._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot_b._start_animation = MagicMock()
    run_async(bot_b.notify(sess_b, "compact_done", {"before_pct": 80, "after_pct": 5}))
    done_text = _edit_text(bot_b._app.bot.edit_message_text.await_args)

    assert timeout_text != done_text
    assert "Compacted" not in timeout_text
    assert "didn't confirm completion" not in done_text


# ===== Criterion 7: BUSY + busy layer beneath -> resume =====================

def test_compact_timeout_at_busy_status_pops_back_to_busy_layer(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    assert sess.busy_msg_id == 42
    assert sess.stack_top_kind() == "busy"


def test_compact_timeout_at_busy_status_resumes_animation(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    bot._start_animation.assert_called_once()


# ===== Criterion 8: non-BUSY (the reported bug's exact state) -> clear =====

def test_compact_timeout_at_idle_status_clears_the_whole_stack(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None


def test_compact_timeout_at_idle_status_does_not_resume_animation(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    bot._start_animation.assert_not_called()


def test_compact_timeout_edits_the_message_exactly_once(mk_bot, run_async):
    """entrypoints.md: 'exactly one edit_message_text-style call' -- no
    delete, no second message."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._app.bot.delete_message = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    bot._app.bot.edit_message_text.assert_awaited_once()
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()


# ===== Sweeper wiring: NOT gated on Status.BUSY (the core design fix) =====

@pytest.mark.parametrize("status", [Status.BUSY, Status.IDLE])
def test_scan_dispatches_compact_timeout_regardless_of_status(
    monkeypatch, run_async, status,
):
    """intent.md's own bug report: the session was observed at
    `status: idle` with the card still spinning, invisible to every
    watchdog gated on BUSY. The new sweeper must catch it at EITHER
    status -- this is the single invariant the whole feature exists to
    establish."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=status)
    sess.busy_msg_id = 42
    sess.push_compacting(
        msg_id=42, now=time.monotonic() - 1000.0, deadline_seconds=1.0,
    )
    registry._sessions["claude-jim"] = sess

    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())

    compact_timeout_calls = [
        c for c in notify_fn.await_args_list if c.args[1] == "compact_timeout"
    ]
    assert len(compact_timeout_calls) == 1
    assert compact_timeout_calls[0].args[0] is sess
    assert "elapsed_seconds" in compact_timeout_calls[0].args[2]


def test_scan_does_not_dispatch_compact_timeout_before_deadline(
    monkeypatch, run_async,
):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.push_compacting(
        msg_id=42, now=time.monotonic(), deadline_seconds=10_000.0,
    )
    registry._sessions["claude-jim"] = sess

    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())

    compact_timeout_calls = [
        c for c in notify_fn.await_args_list if c.args[1] == "compact_timeout"
    ]
    assert compact_timeout_calls == []
