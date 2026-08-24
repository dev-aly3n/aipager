"""design.md success criteria:
- "Phantom SubagentStop events (empty agent_type, unmatched id) neither
  corrupt active_subagents nor add a row to the card's timeline."
- "Once the 1h subagent TTL sweep empties active_subagents for a session
  sitting in the waiting state, the card resolves to an honest
  'background agent lost' terminal state instead of ticking forever" —
  and, per the task brief, must NOT fire while the session is BUSY.

Error-guessing targets: an unmatched SubagentStop with a NON-empty
agent_type (contrast case — design.md's fix is specifically the
empty-type suppression, so a non-empty unmatched type must keep
appending a row exactly as it always did) and a TTL sweep landing on a
BUSY session (agents die silently while the foreground turn is still
running — must not fire the terminal event).
"""

from __future__ import annotations

import time

import pytest
from unittest.mock import AsyncMock

from aipager.session_monitor import SUBAGENT_TTL_SECONDS, SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession


async def _coroutine_returning(value):
    return value


def _mk_monitor(registry, notify=None):
    return SessionMonitor(registry, notify or AsyncMock())


# ---- phantom SubagentStop bookkeeping (via HookReceiver) ------------------

def test_phantom_stop_unknown_id_empty_type_does_not_touch_real_agent(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    sess = mk_job_session(active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": time.monotonic(),
                              "history_idx": 0},
    })
    registry._sessions[sess.name] = sess

    for i in range(1, 6):
        send_hook(recv, hook_event_name="SubagentStop", session="hiva",
                 agent_id=f"unknown-{i}", agent_type="")

    assert "ab2ae82400fc97e4c" in sess.active_subagents
    assert len(sess.active_subagents) == 1


def test_phantom_stop_does_not_corrupt_an_otherwise_empty_table(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    sess = mk_job_session(active_subagents={})
    registry._sessions[sess.name] = sess

    send_hook(recv, hook_event_name="SubagentStop", session="hiva",
             agent_id="unknown-1", agent_type="")

    assert sess.active_subagents == {}


# ---- phantom-stop row suppression vs. the non-empty-type contrast --------

def test_empty_type_unmatched_stop_adds_no_tool_history_row(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = []

    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "unknown-1", "agent_type": "", "elapsed": 0.0,
        "history_idx": None,
    }))

    assert sess.tool_history == [], (
        "an empty-type phantom stop wrote a meaningless row — "
        f"got {sess.tool_history!r}")


def test_nonempty_type_unmatched_stop_still_adds_a_row(mk_bot, run_async):
    """Contrast: the fix targets ONLY the empty-type case — an unmatched
    stop that DOES carry a real agent_type (a genuine subagent whose
    start we simply never saw, e.g. across a daemon restart) must keep
    the pre-existing behaviour of appending a done row."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = []

    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "unknown-y", "agent_type": "Explore", "elapsed": 1.0,
        "history_idx": None,
    }))

    assert len(sess.tool_history) == 1
    assert sess.tool_history[0][1] is True


# ---- TTL sweep: job_agents_lost only on IDLE-with-job-open ---------------

def test_ttl_sweep_fires_job_agents_lost_when_idle_and_table_now_empty(
        run_async, mk_job_session):
    registry = SessionRegistry()
    sess = mk_job_session(status=Status.IDLE, active_subagents={
        "agent-stale": {
            "type": "Explore",
            "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
            "history_idx": 0,
        },
    })
    registry._sessions[sess.name] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)

    import aipager.dtach.inject as di
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(di, "list_sessions",
                  lambda: _coroutine_returning([sess.name]))
        run_async(monitor._scan())
    finally:
        mp.undo()

    assert sess.active_subagents == {}
    fired = [c for c in notify_fn.await_args_list if c.args[1] == "job_agents_lost"]
    assert len(fired) == 1, (
        f"expected exactly one job_agents_lost, got calls: "
        f"{[c.args[1] for c in notify_fn.await_args_list]}")


def test_ttl_sweep_does_not_fire_job_agents_lost_while_session_is_busy(
        run_async, mk_job_session):
    """Task brief's explicit ask: a session still BUSY (foreground turn in
    progress) when its background agent's TTL expires must NOT be told
    the job is over — the foreground turn owns the card until its own
    Stop."""
    registry = SessionRegistry()
    sess = mk_job_session(status=Status.BUSY, active_subagents={
        "agent-stale": {
            "type": "Explore",
            "started_at": time.monotonic() - SUBAGENT_TTL_SECONDS - 60,
            "history_idx": 0,
        },
    })
    registry._sessions[sess.name] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)

    import aipager.dtach.inject as di
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(di, "list_sessions",
                  lambda: _coroutine_returning([sess.name]))
        run_async(monitor._scan())
    finally:
        mp.undo()

    # The stale agent is still dropped (existing TTL behaviour) ...
    assert "agent-stale" not in sess.active_subagents
    # ... but no terminal "job lost" event may fire while BUSY.
    fired = [c for c in notify_fn.await_args_list if c.args[1] == "job_agents_lost"]
    assert fired == [], (
        f"job_agents_lost fired while session was BUSY: "
        f"{[c.args[1] for c in notify_fn.await_args_list]}")


def test_ttl_sweep_does_not_fire_when_table_was_never_open(run_async, mk_job_session):
    """A session that was never job-open (no agents ever tracked) reaching
    IDLE via the sweep must not spuriously fire the terminal event —
    was_job_open must be captured BEFORE popping, per design.md, not
    inferred after the fact from an empty table alone."""
    registry = SessionRegistry()
    sess = mk_job_session(status=Status.IDLE, active_subagents={})
    registry._sessions[sess.name] = sess
    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)

    import aipager.dtach.inject as di
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(di, "list_sessions",
                  lambda: _coroutine_returning([sess.name]))
        run_async(monitor._scan())
    finally:
        mp.undo()

    fired = [c for c in notify_fn.await_args_list if c.args[1] == "job_agents_lost"]
    assert fired == []
