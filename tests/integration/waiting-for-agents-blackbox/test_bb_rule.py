"""design.md "Chosen approach 1", as amended (design-amend-5 A, agents
only): one pure derived state, ``TrackedSession.shown_state() ->
ShownState``.

Equivalence partitions over (status) x (job open by agents / job shells /
grace / continuation / nothing) x (work outside the job), boundary values
on the grace clock (real ``time.monotonic()`` offsets, never patched) and
on the count (0, 1, 2), and the count source (the job's agent table, never
the session-wide ``bg_agents``). Only the job's agents make a session
wait: a job shell, the grace window or a pending continuation alone leave
it idle, though the job stays open (``job_background_open()``).
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


def test_shown_state_is_a_named_tuple_of_state_and_agents():
    assert ShownState("tasks", 2)._fields == ("state", "agents")


def test_shown_state_returns_a_shown_state():
    assert isinstance(mk(agents=1).shown_state(), ShownState)


# ---- status mapping (the derived state never overrides these) ---------------

@pytest.mark.parametrize("status, expected", [
    (Status.BUSY, ("busy", 0)),
    (Status.INTERACTIVE, ("needs_you", 0)),
    (Status.GONE, ("gone", 0)),
    (Status.UNKNOWN, ("unknown", 0)),
], ids=["busy", "interactive", "gone", "unknown"])
def test_non_idle_status_with_job_work_keeps_its_own_state(status, expected):
    """BUSY stays busy, INTERACTIVE wins, GONE/UNKNOWN never wait; the
    count is zero outside ``tasks``."""
    sess = mk(status=status, agents=2, job_shells=1)
    assert tuple(sess.shown_state()) == expected


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE],
                         ids=["busy", "interactive"])
def test_non_idle_status_with_grace_open_is_not_tasks(status):
    sess = mk(status=status)
    sess.job_grace_until = time.monotonic() + 60
    assert sess.shown_state().state != "tasks"


def test_idle_without_any_work_is_idle():
    assert tuple(mk().shown_state()) == ("idle", 0)


# ---- IDLE with the job's agents: tasks, counted from the job ------------------

@pytest.mark.parametrize("agents, shells", [
    (1, 0), (2, 0), (1, 1), (2, 1), (5, 3),
], ids=["1a", "2a", "1a1s", "2a1s", "5a3s"])
def test_idle_with_job_agents_is_tasks_with_the_agent_count(agents, shells):
    sess = mk(agents=agents, job_shells=shells)
    assert tuple(sess.shown_state()) == ("tasks", agents)


@pytest.mark.parametrize("shells", [1, 2], ids=["1s", "2s"])
def test_idle_with_only_job_shells_is_idle(shells):
    """design-amend-5 A (live F3): a shell the job holds (a dev server a
    new turn carries into its job) never makes the session wait, though
    the job is open and the card still follows it."""
    sess = mk(job_shells=shells)
    assert sess.job_background_open() is True
    assert tuple(sess.shown_state()) == ("idle", 0)


def test_the_count_comes_from_the_job_not_the_session_wide_tables():
    """design.md guard 2: 1 job agent beside 3 session-wide agents and
    shells in and out of the job gives 1."""
    sess = mk(agents=1, other_agents=2, job_shells=1, other_shells=1)
    assert len(sess.bg_agents) == 3
    assert tuple(sess.shown_state()) == ("tasks", 1)


def test_job_agent_count_is_the_job_agents():
    sess = mk(agents=2, other_agents=4, job_shells=1, other_shells=3)
    assert sess.job_agent_count() == 2


def test_job_agent_count_of_an_idle_session_with_no_job_is_zero():
    assert mk(other_agents=2, other_shells=2).job_agent_count() == 0


def test_shells_outside_the_job_alone_are_idle():
    """A dev server a later turn did not carry is not job work."""
    assert mk(other_shells=2).shown_state().state == "idle"


def test_session_wide_agents_alone_are_idle():
    """Agents that survived /stop (bg_agents only) are not job work."""
    assert mk(other_agents=2).shown_state().state == "idle"


def test_shown_state_agrees_with_job_agent_count():
    sess = mk(agents=3, job_shells=2, other_shells=1)
    assert sess.shown_state().agents == sess.job_agent_count()


def test_the_state_is_live_when_the_last_job_agent_leaves():
    """Boundary: popping the last agent flips the state on the next call;
    nothing is cached. A job shell left behind does not hold it."""
    sess = mk(agents=1, job_shells=1)
    assert sess.shown_state().state == "tasks"
    sess.active_subagents.clear()
    assert sess.shown_state().state == "idle"


def test_a_job_shell_leaving_the_job_changes_nothing():
    sess = mk(agents=1, job_shells=2)
    sess.bg_shells["j0"]["in_job"] = False
    assert tuple(sess.shown_state()) == ("tasks", 1)


# ---- open job, no agent running: idle -----------------------------------------

def test_grace_window_open_with_nothing_running_is_idle():
    """Nothing records whether an agent or a shell armed the window, so it
    shows idle (about a second live, until the continuation turn)."""
    sess = mk()
    sess.job_grace_until = time.monotonic() + 60
    assert sess.job_background_open() is True
    assert tuple(sess.shown_state()) == ("idle", 0)


def test_grace_just_expired_is_idle():
    sess = mk()
    sess.job_grace_until = time.monotonic() - 0.01
    assert tuple(sess.shown_state()) == ("idle", 0)


def test_pending_continuation_with_nothing_running_is_idle():
    sess = mk()
    sess.job_continuation_active = True
    assert tuple(sess.shown_state()) == ("idle", 0)


def test_grace_with_an_agent_still_running_is_tasks():
    sess = mk(agents=1)
    sess.job_grace_until = time.monotonic() + 60
    assert tuple(sess.shown_state()) == ("tasks", 1)


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
