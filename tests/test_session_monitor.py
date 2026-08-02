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
    sess.busy_started_wall = time.time() - busy_age
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
# Tail of a turn the user aborted mid-tool. turn_appears_complete() returns
# True here — it stops at the trailing user entry and matches "Request
# interrupted" — which is correct for the turn this tail describes, but says
# nothing about a NEWER turn whose prompt hasn't been written yet. Both
# tests below depend on that True.
_INTERRUPTED = [
    {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "Stale answer from the previous turn."}],
        "stop_reason": "tool_use"}},
    {"type": "user", "message": {
        "role": "user",
        "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}},
]


def test_busy_recovered_when_turn_complete_and_quiet(monkeypatch, run_async, tmp_path):
    # Turn started 28s ago; the assistant wrote its reply 13s ago and the
    # file has been quiet since — the genuine missed-Stop-hook case.
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
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
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
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


def test_busy_not_recovered_when_transcript_predates_turn(
        monkeypatch, run_async, tmp_path):
    # Production regression: the user re-sent a prompt that never reached
    # claude, so the transcript still ends on the PREVIOUS turn's abort
    # marker. Everything the old condition looked at said "finished and
    # quiet", and recovery published that turn's text as the new answer.
    # The transcript predates the turn, so recovery must stand down.
    tp = _write_transcript(tmp_path, _INTERRUPTED, age_seconds=250)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 1)
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


def test_busy_recovered_when_interrupt_marker_written_during_turn(
        monkeypatch, run_async, tmp_path):
    # Same abort-marker tail, but written AFTER the turn started — the user
    # really did interrupt this turn, so recovery is still correct here.
    tp = _write_transcript(tmp_path, _INTERRUPTED, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
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
    assert any(e == "idle_prompt" for e, _ in calls)


def test_turn_start_stamped_on_every_new_turn_path():
    # The guard reads busy_started_wall, so a path that begins a turn
    # without stamping it would disable idle-recovery for that session
    # forever. Stamping lives in transition(), so every entry point that
    # starts a turn is covered — Telegram prompt, terminal
    # UserPromptSubmit, and the INTERACTIVE watchdog's demotion.
    registry = SessionRegistry()
    for start in (Status.IDLE, Status.UNKNOWN):
        sess = TrackedSession(name="claude-jim", label="jim", status=start)
        sess.busy_started_wall = 0.0
        registry._sessions["claude-jim"] = sess
        registry.transition("claude-jim", Status.BUSY)
        assert sess.busy_started_wall > 0.0, f"not stamped from {start}"


def test_interactive_watchdog_demotion_keeps_turn_start():
    # The watchdog demotes a crashed permission prompt INTERACTIVE→BUSY.
    # Reaching INTERACTIVE requires having been BUSY, so the marker is
    # already set and must survive the demotion rather than be reset.
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_wall = time.time() - 60
    turn_start = sess.busy_started_wall
    registry._sessions["claude-jim"] = sess
    registry.transition("claude-jim", Status.INTERACTIVE)
    registry.transition("claude-jim", Status.BUSY)
    assert sess.busy_started_wall == turn_start


def test_busy_transition_does_not_restamp_mid_turn():
    # Same-state calls are a no-op, so a redundant BUSY transition must not
    # push the marker forward and blind the guard to a mid-turn write.
    # The sleep only makes the assertion unambiguous if that no-op ever
    # regresses; the behaviour under test is not timing-dependent.
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    registry.transition("claude-jim", Status.BUSY)
    first = sess.busy_started_wall
    time.sleep(0.01)
    registry.transition("claude-jim", Status.BUSY)
    assert sess.busy_started_wall == first


def test_permission_answer_does_not_restamp_turn_start():
    # BUSY → INTERACTIVE → BUSY is one turn interrupted by a permission
    # prompt, not a new turn. Re-stamping on the answer would move the
    # marker past writes this turn already made, permanently failing the
    # idle-recovery guard for the rest of it.
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    registry.transition("claude-jim", Status.BUSY)
    turn_start = sess.busy_started_wall
    time.sleep(0.01)
    registry.transition("claude-jim", Status.INTERACTIVE)
    registry.transition("claude-jim", Status.BUSY)
    assert sess.busy_started_wall == turn_start


def test_new_turn_after_idle_restamps_turn_start():
    # The counterpart: once the turn really ends, the next prompt must get
    # a fresh marker, or the guard would compare against a stale turn.
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    registry.transition("claude-jim", Status.BUSY)
    first_turn = sess.busy_started_wall
    time.sleep(0.01)
    sess.last_idle_at = 0.0  # bypass the IDLE debounce
    registry.transition("claude-jim", Status.IDLE)
    registry.transition("claude-jim", Status.BUSY)
    assert sess.busy_started_wall > first_turn


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
