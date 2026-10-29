"""Roadmap 8.121: one derived state for a session waiting for its own
background agents.

``TrackedSession.shown_state()`` is the one rule every surface reads: an
IDLE session whose job still runs background agents (``active_subagents``,
``job_agent_count()``) is ``tasks`` with that count, never the
session-wide running set (``bg_agents``). Agents only (design-amend-5 A):
a background shell alone never makes a session wait, in the job or not,
and neither does the job's grace window or a pending continuation with
nothing left running. The job itself (``job_background_open()``) still
counts all of them; only the shown state does not.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import (
    SHOWN_BUSY,
    SHOWN_GONE,
    SHOWN_IDLE,
    SHOWN_NEEDS_YOU,
    SHOWN_TASKS,
    SHOWN_UNKNOWN,
    ShownState,
    Status,
    TrackedSession,
)


def _sess(status: Status = Status.IDLE) -> TrackedSession:
    return TrackedSession(name="claude-x1", label="x1", status=status)


def _agent(sess: TrackedSession, agent_id: str, kind: str = "Explore") -> None:
    sess.active_subagents[agent_id] = {"type": kind, "started_at": time.monotonic()}


def _shell(sess: TrackedSession, task_id: str, *, in_job: bool) -> None:
    sess.bg_shells[task_id] = {"label": f"sleep {task_id}", "in_job": in_job,
                               "started_at": time.monotonic()}


# ---- the rule ------------------------------------------------------------------------

def test_an_idle_session_whose_job_runs_an_agent_is_tasks():
    sess = _sess()
    _agent(sess, "a1")
    assert sess.shown_state() == ShownState(SHOWN_TASKS, 1)


def test_a_busy_session_with_job_agents_stays_busy():
    """Guard 1: the status is mapped first, so a working turn whose agents
    run is ``busy``, never ``tasks``."""
    sess = _sess(Status.BUSY)
    _agent(sess, "a1")
    _shell(sess, "s1", in_job=True)
    assert sess.shown_state() == ShownState(SHOWN_BUSY, 0)


@pytest.mark.parametrize("status, state", [
    (Status.INTERACTIVE, SHOWN_NEEDS_YOU),
    (Status.GONE, SHOWN_GONE),
    (Status.UNKNOWN, SHOWN_UNKNOWN),
], ids=["interactive", "gone", "unknown"])
def test_the_other_statuses_never_become_tasks(status, state):
    sess = _sess(status)
    _agent(sess, "a1")
    _shell(sess, "s1", in_job=True)
    sess.job_continuation_active = True
    assert sess.shown_state() == ShownState(state, 0)


def test_an_idle_session_with_no_job_is_idle():
    assert _sess().shown_state() == ShownState(SHOWN_IDLE, 0)


def test_the_count_comes_from_the_job_not_the_running_set():
    """Guard 2: three agents run in the session, one is the job's. The
    state names the job's agent only."""
    sess = _sess()
    for agent_id in ("a1", "a2", "a3"):
        sess.bg_agents[agent_id] = {"type": "Explore", "started_at": 0.0}
    _agent(sess, "a1")
    assert sess.job_agent_count() == 1
    assert sess.shown_state() == ShownState(SHOWN_TASKS, 1)


def test_a_job_shell_without_agents_leaves_the_session_idle():
    """Guard (design-amend-5 A): a shell the job still holds (a dev server
    a turn started and every later turn carries, live F3) does not make
    the session wait, though the job stays open for the card."""
    sess = _sess()
    _shell(sess, "s-in", in_job=True)
    assert sess.job_background_open() is True
    assert sess.shown_state() == ShownState(SHOWN_IDLE, 0)


def test_a_job_shell_beside_an_agent_is_not_counted():
    sess = _sess()
    _agent(sess, "a1")
    _shell(sess, "s-in", in_job=True)
    _shell(sess, "s-in2", in_job=True)
    assert sess.shown_state() == ShownState(SHOWN_TASKS, 1)


def test_shells_outside_the_job_alone_leave_the_session_idle():
    """A dev server a later turn left out of the job: idle (the bar still
    notes the shell)."""
    sess = _sess()
    _shell(sess, "s-out", in_job=False)
    sess.bg_agents["a9"] = {"type": "Plan", "started_at": 0.0}
    assert sess.shown_state() == ShownState(SHOWN_IDLE, 0)


def test_the_grace_window_with_nothing_running_is_idle():
    """Guard 3: the last agent stopped and the continuation has not come
    yet. Nothing records whether an agent or a shell armed the window, so
    it shows idle (about a second live) until the continuation is busy."""
    sess = _sess()
    sess.job_grace_until = time.monotonic() + 60
    assert sess.job_background_open() is True
    assert sess.shown_state() == ShownState(SHOWN_IDLE, 0)


def test_a_pending_continuation_with_nothing_running_is_idle():
    sess = _sess()
    sess.job_continuation_active = True
    assert sess.job_background_open() is True
    assert sess.shown_state() == ShownState(SHOWN_IDLE, 0)


def test_the_count_follows_each_agent():
    sess = _sess()
    _agent(sess, "a1")
    _agent(sess, "a2", "Plan")
    _shell(sess, "s1", in_job=True)
    assert sess.shown_state() == ShownState(SHOWN_TASKS, 2)
    del sess.active_subagents["a1"]
    assert sess.shown_state() == ShownState(SHOWN_TASKS, 1)
    del sess.active_subagents["a2"]
    assert sess.shown_state() == ShownState(SHOWN_IDLE, 0)


def test_the_ids_are_the_wire_words():
    # The Mini App and the bar use these ids as they are.
    assert (SHOWN_BUSY, SHOWN_NEEDS_YOU, SHOWN_TASKS, SHOWN_IDLE, SHOWN_GONE,
            SHOWN_UNKNOWN) == ("busy", "needs_you", "tasks", "idle", "gone", "unknown")
