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


# ----- Idle-recovery fallback (missed Stop hook) -----

import json  # noqa: E402
import os  # noqa: E402

from aipager.session_monitor import IDLE_RECOVERY_GRACE  # noqa: E402


def _write_transcript(tmp_path, lines, age_seconds):
    p = tmp_path / "rec.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    old = time.time() - age_seconds
    os.utime(p, (old, old))
    return str(p)


def _busy_session(transcript_path, busy_age):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - busy_age
    sess.transcript_path = transcript_path
    return sess


_COMPLETE = [
    {"type": "user", "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "All done."}],
        "stop_reason": "end_turn"}},
]
_IN_PROGRESS = [
    {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash"}],
        "stop_reason": "tool_use"}},
]


def test_busy_recovered_when_turn_complete_and_quiet(monkeypatch, run_async, tmp_path):
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 5)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.IDLE
    assert calls and calls[-1][0] == "idle_prompt"
    assert calls[-1][1].get("summary") == "All done."


def test_busy_not_recovered_while_turn_in_progress(monkeypatch, run_async, tmp_path):
    tp = _write_transcript(tmp_path, _IN_PROGRESS, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 5)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY
    assert not any(e == "idle_prompt" for e, _ in calls)


def test_busy_not_recovered_when_recently_active(monkeypatch, run_async, tmp_path):
    # Turn looks complete, but the transcript was just written — the normal
    # Stop hook should win; the monitor must not race it.
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=1)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 5)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY
    assert not any(e == "idle_prompt" for e, _ in calls)


def test_busy_not_recovered_before_grace(monkeypatch, run_async, tmp_path):
    # Quiet transcript + complete turn, but the session only just went BUSY.
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=1)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY
