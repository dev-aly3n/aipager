"""Tests for `aipager.session_monitor` watchdogs (items 2.2 and 2.4)."""

from __future__ import annotations

import time

import pytest

from aipager.session_monitor import (
    INTERACTIVE_TIMEOUT_SECONDS,
    SUBAGENT_TTL_SECONDS,
    SessionMonitor,
)
from aipager.state import SessionRegistry, Status, TrackedSession


def _mk_monitor(registry: SessionRegistry, notify=None) -> SessionMonitor:
    async def _noop(*a, **kw):
        return None
    return SessionMonitor(registry, notify or _noop)


# ----- 2.2 INTERACTIVE watchdog -----

def test_interactive_session_demoted_after_timeout(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.last_hook_at = time.monotonic() - INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())
    # Auto-demoted to BUSY, permission cleared
    assert sess.status == Status.BUSY
    assert sess.pending_permission is None


def test_interactive_within_timeout_not_demoted(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.last_hook_at = time.monotonic() - 60  # 1 minute, well under 5
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())
    assert sess.status == Status.INTERACTIVE
    assert sess.pending_permission == {"tool": "Bash"}


def test_interactive_without_baseline_not_demoted(monkeypatch, run_async):
    """If last_hook_at and busy_started_at are both 0, there's no baseline
    to compare against, so we must not demote (avoids false positives on
    sessions freshly loaded after daemon restart)."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.last_hook_at = 0.0
    sess.busy_started_at = 0.0
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())
    assert sess.status == Status.INTERACTIVE


# ----- 2.4 Subagent TTL sweep -----

def test_subagent_dropped_after_ttl(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.BUSY)
    # Stale subagent (started > 1h ago)
    sess.active_subagents["agent-stale"] = {
        "type": "explore",
        "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
        "history_idx": 0,
    }
    # Fresh subagent (started 5 min ago)
    sess.active_subagents["agent-fresh"] = {
        "type": "plan",
        "started_at": time.monotonic() - 300,
        "history_idx": 1,
    }
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    run_async(monitor._scan())
    assert "agent-stale" not in sess.active_subagents
    assert "agent-fresh" in sess.active_subagents


def test_subagent_without_started_at_kept(run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.BUSY)
    sess.active_subagents["agent-x"] = {"type": "research"}  # no started_at
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    # Should not crash and should not drop the entry.
    import aipager.dtach.inject as di
    pytest_monkeypatch = pytest.MonkeyPatch()
    try:
        pytest_monkeypatch.setattr(
            di, "list_sessions",
            lambda: _coroutine_returning(["claude-jim"]),
        )
        run_async(monitor._scan())
        assert "agent-x" in sess.active_subagents
    finally:
        pytest_monkeypatch.undo()


async def _coroutine_returning(value):
    return value
