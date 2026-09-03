"""Tests for `aipager.session_monitor` watchdogs (items 2.2 and 2.4)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

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

def test_interactive_session_demoted_after_timeout(steady_clock, monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.last_hook_at = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - 60
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


# ---- design.md "model Claude Code background-agent jobs" — TTL sweep
# firing job_agents_lost only when the session was IDLE-with-job-open and
# the table is now empty.

def test_ttl_sweep_fires_job_agents_lost_when_idle_and_now_empty(
    monkeypatch, run_async,
):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.active_subagents["a1"] = {
        "type": "explore",
        "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
    }
    registry._sessions["claude-hiva"] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-hiva"]),
    )
    run_async(monitor._scan())
    assert sess.active_subagents == {}
    notify_fn.assert_awaited_once()
    fired_sess, event, ctx = notify_fn.await_args.args
    assert event == "job_agents_lost"
    assert fired_sess is sess
    assert ctx == {}


def test_ttl_sweep_does_not_fire_job_agents_lost_when_busy(monkeypatch, run_async):
    """A session that flipped back to BUSY (the background agent's own
    tool call re-entered before this scan) is still genuinely working,
    not orphaned — no job_agents_lost."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.BUSY)
    sess.active_subagents["a1"] = {
        "type": "explore",
        "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
    }
    registry._sessions["claude-hiva"] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-hiva"]),
    )
    run_async(monitor._scan())
    assert sess.active_subagents == {}  # still dropped by the TTL sweep
    notify_fn.assert_not_awaited()  # but no job_agents_lost


def test_ttl_sweep_does_not_fire_when_a_fresh_agent_remains(monkeypatch, run_async):
    """The table is not YET empty (a fresh agent is still tracked) — the
    job is still genuinely open, not orphaned."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.active_subagents["stale"] = {
        "type": "explore",
        "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
    }
    sess.active_subagents["fresh"] = {
        "type": "plan", "started_at": time.monotonic() - 60,
    }
    registry._sessions["claude-hiva"] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-hiva"]),
    )
    run_async(monitor._scan())
    assert "fresh" in sess.active_subagents
    notify_fn.assert_not_awaited()


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


def _iso(epoch: float) -> str:
    """Millisecond ISO-8601 with a ``Z`` suffix, matching transcript
    timestamps (``2026-09-03T10:59:17.897Z``)."""
    from datetime import datetime, timezone
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def test_recovery_rejects_stale_text_from_a_preserved_earlier_reentry(
        monkeypatch, run_async, tmp_path):
    """Live regression (intent.md "Mechanism"): a background-job re-entry
    (``preserve_job_state=True``) leaves ``busy_started_wall`` pinned at
    the ORIGINAL job's turn start, so the file-mtime guard above — which
    reads busy_started_wall — is satisfied by ANY write since that
    original start, including one from an EARLIER re-entry whose answer
    already went out (10:59:17, for a turn that actually began at
    11:00:32). ``turn_entered_wall`` is stamped on every entry, preserved
    re-entries included, and is what ``extract_last_response``'s own
    ``since=`` guard reads — it must reject that earlier entry's text
    even though the outer mtime guard let recovery proceed."""
    now = time.time()
    earlier_reentry_ts = now - 300     # the 10:59:17-equivalent stale text
    original_job_start = now - 600     # busy_started_wall: pinned way back
    latest_turn_start = now - 120      # turn_entered_wall: the CURRENT turn

    lines = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text",
                        "text": "Stale answer from an earlier re-entry."}],
            "stop_reason": "end_turn"},
         "timestamp": _iso(earlier_reentry_ts)},
    ]
    tp = tmp_path / "rec2.jsonl"
    tp.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    # File mtime must clear IDLE_RECOVERY_GRACE / written_this_turn (both
    # anchored on busy_started_wall) — the entry's OWN embedded timestamp
    # above is what the new, stricter guard checks instead.
    fresh = now - (IDLE_RECOVERY_GRACE + 5)
    os.utime(tp, (fresh, fresh))

    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - (IDLE_RECOVERY_GRACE + 20)
    sess.busy_started_wall = original_job_start
    sess.turn_entered_wall = latest_turn_start
    sess.transcript_path = str(tp)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    # The outer (mtime) guard is satisfied, so recovery still proceeds —
    # but the entry-level guard must have rejected the stale text.
    assert sess.status == Status.IDLE
    assert calls and calls[-1][0] == "idle_prompt"
    assert calls[-1][1].get("summary") == ""
    assert calls[-1][1].get("no_response") is True


def test_recovery_delivers_text_written_after_the_current_turn_began(
        monkeypatch, run_async, tmp_path):
    """Same preserved-``busy_started_wall`` shape as above, but the
    transcript's text was genuinely written during the CURRENT turn
    (after ``turn_entered_wall``) — recovery must still deliver it."""
    now = time.time()
    original_job_start = now - 600
    latest_turn_start = now - 120
    genuine_answer_ts = now - 15   # written well after turn_entered_wall

    lines = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Genuine current-turn answer."}],
            "stop_reason": "end_turn"},
         "timestamp": _iso(genuine_answer_ts)},
    ]
    tp = tmp_path / "rec3.jsonl"
    tp.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    fresh = now - (IDLE_RECOVERY_GRACE + 5)
    os.utime(tp, (fresh, fresh))

    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - (IDLE_RECOVERY_GRACE + 20)
    sess.busy_started_wall = original_job_start
    sess.turn_entered_wall = latest_turn_start
    sess.transcript_path = str(tp)
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
    assert calls[-1][1].get("summary") == "Genuine current-turn answer."


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


# ----- a deliberate restart is not a disappearance -----

def test_restarting_session_is_not_marked_gone(monkeypatch, run_async):
    """`/perms` kills and relaunches; between the two the socket is absent by
    design. Marking it GONE raced the relaunch and alarmed the user about a
    session that was coming right back."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.restarting_until = time.monotonic() + 10
    registry._sessions["claude-jim"] = sess
    notified = []

    async def _notify(s, event, ctx):
        notified.append(event)

    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning([]),      # socket gone
    )
    run_async(monitor._scan())
    assert sess.status == Status.IDLE
    assert notified == []


def test_a_genuinely_missing_session_is_still_marked_gone(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    notified = []

    async def _notify(s, event, ctx):
        notified.append(event)

    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning([]),
    )
    run_async(monitor._scan())
    assert sess.status == Status.GONE
    assert "session_end" in notified


def test_busy_not_recovered_while_job_background_open(
    monkeypatch, run_async, tmp_path,
):
    """Requirement 1 of "close the background-job endgame": the missed-Stop
    idle-recovery must stand down while a background job is open — the
    transcript is lazily flushed (observed 72-minute lag live), so
    "finished + quiet" is meaningless for the whole background window.
    Without the stand-down this recovery fired 8+ times in one live run,
    ping-ponging BUSY→IDLE."""
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.active_subagents["agent-1"] = {
        "type": "Explore", "started_at": time.monotonic(), "history_idx": None,
    }
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY  # NOT recovered
    assert not any(e == "idle_prompt" for e, _ in calls)


# ----- Idle-recovery must stand down while a tool/compaction is in flight
# (the reported bug: a long Bash/pytest run made the transcript look
# finished-and-quiet, and the fallback recovered mid-turn) -----

def test_busy_not_recovered_while_tool_in_flight(monkeypatch, run_async, tmp_path):
    """This is the defect: a tool started 30s ago (well under
    TOOL_INFLIGHT_MAX_SECONDS) must stand the recovery down exactly like
    it already stands the stale-busy warning down."""
    from aipager.session_monitor import TOOL_INFLIGHT_MAX_SECONDS
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.pending_tool_started_at = time.monotonic() - 30.0
    assert 30.0 < TOOL_INFLIGHT_MAX_SECONDS
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY  # NOT recovered
    assert not any(e == "idle_prompt" for e, _ in calls)


def test_busy_not_recovered_while_compact_in_flight(monkeypatch, run_async, tmp_path):
    """Same false positive, via a compaction in flight instead of a tool."""
    from aipager.session_monitor import COMPACT_INFLIGHT_MAX_SECONDS
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.compact_started_at = time.monotonic() - 300.0
    assert 300.0 < COMPACT_INFLIGHT_MAX_SECONDS
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY  # NOT recovered
    assert not any(e == "idle_prompt" for e, _ in calls)


def test_busy_recovered_when_tool_exceeds_inflight_cap(
    steady_clock, monkeypatch, run_async, tmp_path,
):
    """A genuinely wedged tool must not block recovery forever."""
    from aipager.session_monitor import TOOL_INFLIGHT_MAX_SECONDS
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.busy_started_at = steady_clock() - (IDLE_RECOVERY_GRACE + 20)
    sess.pending_tool_started_at = steady_clock() - TOOL_INFLIGHT_MAX_SECONDS - 60
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


def test_busy_recovered_when_compact_exceeds_inflight_cap(
    steady_clock, monkeypatch, run_async, tmp_path,
):
    """Same for a compaction that has overrun its own, much longer cap."""
    from aipager.session_monitor import COMPACT_INFLIGHT_MAX_SECONDS
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.busy_started_at = steady_clock() - (IDLE_RECOVERY_GRACE + 20)
    sess.compact_started_at = steady_clock() - COMPACT_INFLIGHT_MAX_SECONDS - 60
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


def test_idle_recovery_ctx_flags_recovered_true(monkeypatch, run_async, tmp_path):
    """bot/notify.py needs to tell a recovery-originated idle apart from a
    real hook-driven one to suppress a header-only "Finished" when there
    is nothing new to say — the recovery path must thread that flag."""
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

    assert calls and calls[-1][0] == "idle_prompt"
    assert calls[-1][1].get("recovered") is True


def test_idle_recovery_consults_the_shared_work_in_flight_helper(
    monkeypatch, run_async, tmp_path,
):
    """Proves the recovery branch ASKS TrackedSession.work_in_flight_reason
    rather than re-deriving the condition: with no pending_tool_started_at
    or compact_started_at set at all, patching the helper to report
    "in flight" must still suppress recovery."""
    from aipager.state import TrackedSession
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(
        TrackedSession, "work_in_flight_reason",
        lambda self, now: ("tool", 1.0),
    )

    calls = []
    async def _notify(s, event, ctx):
        calls.append((event, ctx))
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.status == Status.BUSY
    assert not any(e == "idle_prompt" for e, _ in calls)


def test_stale_busy_consults_the_shared_work_in_flight_helper(
    monkeypatch, run_async,
):
    """Same proof for the stale-busy path: with no pending_tool_started_at
    or compact_started_at set, patching the helper to report "in flight"
    must still suppress the warning."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    from aipager.state import TrackedSession
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(
        TrackedSession, "work_in_flight_reason",
        lambda self, now: ("compact", 1.0),
    )

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False


def test_idle_recovery_logs_stand_down_once_per_episode(
    monkeypatch, run_async, tmp_path, caplog,
):
    """The stand-down must be diagnosable from the journal (one INFO line
    naming the reason) without spamming a line per 2s scan tick for the
    whole duration of a long tool call."""
    import logging
    caplog.set_level(logging.INFO, logger="aipager.session_monitor")
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.pending_tool_started_at = time.monotonic() - 30.0
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())
    run_async(monitor._scan())
    run_async(monitor._scan())

    standdown_logs = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "idle-recovery stood down" in r.message
    ]
    assert len(standdown_logs) == 1
    assert sess.status == Status.BUSY


def test_recovery_stand_down_flag_resets_once_tool_clears(
    monkeypatch, run_async, tmp_path,
):
    """The stand-down log gate must re-arm the moment nothing is in
    flight any more — even on a tick where the OUTER recovery-due gate
    itself isn't satisfied (transcript too fresh here) — otherwise a
    later, unrelated stand-down episode would silently never get its own
    log line."""
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=1)  # too fresh to recover
    sess = _busy_session(tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.recovery_stand_down_logged = True  # a prior episode already logged
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert sess.recovery_stand_down_logged is False
    assert sess.status == Status.BUSY  # unrelated to recovery firing


def test_grace_expiry_closes_orphaned_job(monkeypatch, run_async, tmp_path):
    """A job whose <task-notification> continuation never arrives may not
    tick forever: once the grace deadline passes with the session IDLE,
    the monitor closes the job via job_grace_expired and clears the
    endgame state ("close the background-job endgame" requirement 2's
    fallback)."""
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=5)
    sess = _busy_session(tp, busy_age=30)
    sess.status = Status.IDLE
    sess.job_interim_seen = True
    sess.job_grace_until = time.monotonic() - 1  # already expired

    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append(event)
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert "job_grace_expired" in calls
    assert sess.job_interim_seen is False
    assert sess.job_grace_until == 0.0


def test_grace_not_expired_while_continuation_running(
    monkeypatch, run_async, tmp_path,
):
    """A BUSY session (the continuation turn itself, or a new real turn)
    is genuinely working — the grace-expiry close must not fire."""
    tp = _write_transcript(tmp_path, _COMPLETE, age_seconds=5)
    sess = _busy_session(tp, busy_age=5)
    sess.job_interim_seen = True
    sess.job_continuation_active = True
    sess.job_grace_until = time.monotonic() - 1

    registry = SessionRegistry()
    registry._sessions["claude-jim"] = sess

    calls = []
    async def _notify(s, event, ctx):
        calls.append(event)
    monitor = _mk_monitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _coroutine_returning(["claude-jim"]))

    run_async(monitor._scan())

    assert "job_grace_expired" not in calls
