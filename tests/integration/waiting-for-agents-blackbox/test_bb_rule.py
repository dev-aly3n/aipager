"""design.md "Chosen approach 1" and the success criteria: one pure
derived state, ``TrackedSession.shown_state() -> ShownState``.

Equivalence partitions over (status) x (job open by agents / job shells /
grace / continuation / nothing) x (work outside the job), boundary values
on the grace clock (real ``time.monotonic()`` offsets, never patched) and
on the counts (0, 1, 2), and the count source (job tables, never the
session-wide ``bg_*``).
"""

from __future__ import annotations

import importlib
import time

import pytest

from aipager import state as state_mod
from aipager.state import ShownState, Status

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")
mk = kit.make_session


# ---- the exported ids --------------------------------------------------------

@pytest.mark.parametrize("name, value", [
    ("SHOWN_BUSY", "busy"), ("SHOWN_NEEDS_YOU", "needs_you"),
    ("SHOWN_TASKS", "tasks"), ("SHOWN_IDLE", "idle"),
    ("SHOWN_GONE", "gone"), ("SHOWN_UNKNOWN", "unknown"),
])
def test_state_id_constants(name, value):
    assert getattr(state_mod, name) == value


def test_shown_state_is_a_named_tuple_of_state_agents_shells():
    assert ShownState("tasks", 2, 1)._fields == ("state", "agents", "shells")


def test_shown_state_returns_a_shown_state():
    assert isinstance(mk(agents=1).shown_state(), ShownState)


# ---- status mapping (the derived state never overrides these) ---------------

@pytest.mark.parametrize("status, expected", [
    (Status.BUSY, ("busy", 0, 0)),
    (Status.INTERACTIVE, ("needs_you", 0, 0)),
    (Status.GONE, ("gone", 0, 0)),
    (Status.UNKNOWN, ("unknown", 0, 0)),
], ids=["busy", "interactive", "gone", "unknown"])
def test_non_idle_status_with_job_work_keeps_its_own_state(status, expected):
    """BUSY stays busy, INTERACTIVE wins, GONE/UNKNOWN never wait; counts
    are zero outside ``tasks``."""
    sess = mk(status=status, agents=2, job_shells=1)
    assert tuple(sess.shown_state()) == expected


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE],
                         ids=["busy", "interactive"])
def test_non_idle_status_with_grace_open_is_not_tasks(status):
    sess = mk(status=status)
    sess.job_grace_until = time.monotonic() + 60
    assert sess.shown_state().state != "tasks"


def test_idle_without_any_work_is_idle():
    assert tuple(mk().shown_state()) == ("idle", 0, 0)


# ---- IDLE with an open job: tasks, counted from the job ----------------------

@pytest.mark.parametrize("agents, shells", [
    (1, 0), (2, 0), (0, 1), (0, 2), (1, 1), (2, 1), (5, 3),
], ids=["1a", "2a", "1s", "2s", "1a1s", "2a1s", "5a3s"])
def test_idle_with_job_work_is_tasks_with_the_job_counts(agents, shells):
    sess = mk(agents=agents, job_shells=shells)
    assert tuple(sess.shown_state()) == ("tasks", agents, shells)


def test_counts_come_from_the_job_not_the_session_wide_tables():
    """design.md guard 2: 1 job agent beside 3 session-wide agents, one
    job shell beside one shell outside the job, gives (1, 1)."""
    sess = mk(agents=1, other_agents=2, job_shells=1, other_shells=1)
    assert len(sess.bg_agents) == 3
    assert tuple(sess.shown_state()) == ("tasks", 1, 1)


def test_job_counts_is_the_job_agents_and_job_shells():
    sess = mk(agents=2, other_agents=4, job_shells=1, other_shells=3)
    assert sess.job_counts() == (2, 1)


def test_job_counts_of_an_idle_session_with_no_job_is_zero():
    assert mk(other_agents=2, other_shells=2).job_counts() == (0, 0)


def test_shells_outside_the_job_alone_are_idle():
    """A dev server a later turn did not carry is not job work."""
    assert mk(other_shells=2).shown_state().state == "idle"


def test_session_wide_agents_alone_are_idle():
    """Agents that survived /stop (bg_agents only) are not job work."""
    assert mk(other_agents=2).shown_state().state == "idle"


def test_shown_state_agrees_with_job_counts():
    sess = mk(agents=3, job_shells=2, other_shells=1)
    shown = sess.shown_state()
    assert (shown.agents, shown.shells) == sess.job_counts()


def test_the_state_is_live_when_the_last_job_agent_leaves():
    """Boundary: popping the last agent (no grace, no continuation) flips
    the state on the next call; nothing is cached."""
    sess = mk(agents=1)
    assert sess.shown_state().state == "tasks"
    sess.active_subagents.clear()
    assert sess.shown_state().state == "idle"


def test_a_job_shell_leaving_the_job_is_no_longer_counted():
    sess = mk(agents=1, job_shells=2)
    sess.bg_shells["j0"]["in_job"] = False
    assert tuple(sess.shown_state()) == ("tasks", 1, 1)


# ---- the zero-count case: open job, nothing running --------------------------

def test_grace_window_open_with_nothing_running_is_tasks_zero():
    sess = mk()
    sess.job_grace_until = time.monotonic() + 60
    assert tuple(sess.shown_state()) == ("tasks", 0, 0)


def test_grace_just_inside_is_still_tasks():
    sess = mk()
    sess.job_grace_until = time.monotonic() + 1.0
    assert sess.shown_state().state == "tasks"


def test_grace_just_expired_is_idle():
    sess = mk()
    sess.job_grace_until = time.monotonic() - 0.01
    assert tuple(sess.shown_state()) == ("idle", 0, 0)


def test_grace_long_expired_is_idle():
    sess = mk()
    sess.job_grace_until = time.monotonic() - 3600
    assert sess.shown_state().state == "idle"


def test_pending_continuation_with_nothing_running_is_tasks_zero():
    sess = mk()
    sess.job_continuation_active = True
    assert tuple(sess.shown_state()) == ("tasks", 0, 0)


def test_grace_with_shells_outside_the_job_counts_zero_shells():
    """Finishing up: the work outside the job never leaks into the count."""
    sess = mk(other_shells=2, other_agents=1)
    sess.job_grace_until = time.monotonic() + 60
    assert tuple(sess.shown_state()) == ("tasks", 0, 0)


# ---- purity -------------------------------------------------------------------

def test_shown_state_does_not_change_the_session():
    sess = mk(agents=2, job_shells=1, other_shells=1)
    before = (dict(sess.active_subagents), {k: dict(v) for k, v in sess.bg_shells.items()},
              sess.status, sess.job_grace_until, sess.job_continuation_active)
    sess.shown_state()
    sess.shown_state()
    after = (dict(sess.active_subagents), {k: dict(v) for k, v in sess.bg_shells.items()},
             sess.status, sess.job_grace_until, sess.job_continuation_active)
    assert before == after


def test_shown_state_twice_gives_the_same_answer():
    sess = mk(agents=1, job_shells=1)
    assert sess.shown_state() == sess.shown_state()
