"""Additional session_monitor tests covering the start/stop loop and
the on_sessions_changed callback."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock


from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession


async def _coroutine_returning(value):
    return value


def _mk_monitor(registry, notify_fn=None):
    async def _noop(*a, **k): return None
    return SessionMonitor(registry, notify_fn or _noop)


# ---- start / stop ------------------------------------------------------

def test_start_creates_background_task(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))

    async def _go():
        await monitor.start()
        # Cancel after a moment
        monitor.stop()
        # Give the task a chance to settle
        try:
            await monitor._task
        except asyncio.CancelledError:
            pass

    run_async(_go())


def test_stop_with_no_task_is_noop():
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monitor.stop()  # MUST NOT raise


# ---- _scan: session_end notify failure swallowed -----------------------

def test_scan_session_end_notify_failure_swallowed(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.transcript_path = "/nope"
    registry._sessions["claude-jim"] = sess

    async def _failing_notify(*a, **k):
        raise RuntimeError("notify broken")

    monitor = _mk_monitor(registry, _failing_notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))  # session disappeared
    # MUST NOT raise
    run_async(monitor._scan())
    assert sess.status == Status.GONE


# ---- _scan: on_sessions_changed fires on additions ---------------------

def test_scan_calls_on_sessions_changed_on_new_session(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monitor.on_sessions_changed = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-new"]))
    run_async(monitor._scan())
    monitor.on_sessions_changed.assert_awaited_once()


def test_scan_on_sessions_changed_failure_swallowed(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    async def _failing():
        raise RuntimeError("callback broken")
    monitor.on_sessions_changed = _failing
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-new"]))
    run_async(monitor._scan())  # MUST NOT raise


# ---- _scan: GONE session recovered on socket reappearance ---------------

def test_scan_recovers_gone_session_clears_gone_at(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = 1234.0
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))  # came back
    run_async(monitor._scan())
    assert sess.status == Status.IDLE
    assert sess.gone_at is None


# ---- _scan: stale_busy notify failure swallowed ------------------------

def test_scan_stale_busy_notify_failure_swallowed(monkeypatch, run_async):
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    registry._sessions["claude-jim"] = sess

    async def _failing_notify(*a, **k):
        raise RuntimeError("notify broken")

    monitor = _mk_monitor(registry, _failing_notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    # MUST NOT raise
    run_async(monitor._scan())
    # stale_warned was set so we don't spam
    assert sess.stale_warned is True


# ---- _loop: error in scan is logged but loop continues ------------------

def test_loop_swallows_scan_exception(monkeypatch, run_async):
    """The loop catches Exception from _scan and continues to next iteration."""
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    calls = {"n": 0}
    async def _raising_scan():
        calls["n"] += 1
        raise RuntimeError("scan broken")
    monitor._scan = _raising_scan
    # Make sleep raise CancelledError on second call → exits the loop
    sleep_calls = {"n": 0}
    async def _sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError("done")
    monkeypatch.setattr("aipager.session_monitor.asyncio.sleep", _sleep)
    async def _go():
        try:
            await monitor._loop()
        except asyncio.CancelledError:
            pass
    run_async(_go())
    # _scan ran twice — the first error was swallowed and the loop iterated again
    assert calls["n"] >= 2


# ---- _scan: stale_busy suppressed while a tool call is in flight -------

def test_scan_stale_busy_suppressed_during_tool_in_flight(monkeypatch, run_async):
    """A tool that runs longer than STALE_BUSY_TIMEOUT must NOT trigger the
    'stuck' warning, because no hooks fire between PreToolUse and
    PostToolUse. The check must re-arm after the tool finishes, so
    stale_warned stays False."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    # No hooks for 3 min — beyond STALE_BUSY_TIMEOUT (default 120s).
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    # Tool started 3 min ago — well under the 15 min cap.
    sess.pending_tool_started_at = time.monotonic() - 180.0
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False  # re-armed for post-tool


def test_scan_stale_busy_fires_when_tool_exceeds_inflight_cap(monkeypatch,
                                                              run_async):
    """A tool that has been in flight beyond TOOL_INFLIGHT_MAX_SECONDS is
    treated as genuinely wedged — the warning must fire."""
    from aipager.session_monitor import (
        STALE_BUSY_TIMEOUT,
        TOOL_INFLIGHT_MAX_SECONDS,
    )
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    # Tool started 16 min ago — over the 15 min cap.
    sess.pending_tool_started_at = (
        time.monotonic() - TOOL_INFLIGHT_MAX_SECONDS - 60
    )
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True


def test_scan_stale_busy_fires_when_no_tool_in_flight(monkeypatch, run_async):
    """Preserve existing behavior: no tool in flight, 2+ min of quiet →
    warning fires. Regression guard for the plain 'stuck' path."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    sess.pending_tool_started_at = None  # nothing in flight
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True
