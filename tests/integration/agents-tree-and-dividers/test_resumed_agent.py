"""A stopped agent that starts again is a resume (entrypoints.md
"Amendments (orchestrator, after review-1)", "Resumed agent"; design.md
success criteria 7 and 8).

A resume consumes no pending call, gets no new link from the launcher
match and keeps its description. Another launcher's pending call of its
type stays pending for that launcher's real child.

Driven through the public ``TrackedSession`` methods with explicit clocks,
and through hook datagrams (``SubagentStop`` then ``SubagentStart`` for the
same id)."""

from __future__ import annotations

import time

import pytest

from aipager.state import TrackedSession

P = "a505c081ca3926ed5"     # pipeline-runner (main session's)
Q = "a77e10f00d1c2b3a9"     # r2-runner (main session's)
C = "ac1d54c85d78f59ea"     # the helper that stops and resumes
C2 = "ab0000000000000c2"    # the next real child of the type
T = "ship-tester"
P_TYPE, Q_TYPE = "pipeline-runner", "r2-runner"
ROBOT = "\U0001f916"


# ---- through the state methods ---------------------------------------------------------

def _resumed(other_launcher: str):
    """C is P's helper (launcher match), stops, then *other_launcher*
    calls the same type, then C starts again."""
    s = TrackedSession(name="claude-frontend", label="frontend")
    now = time.monotonic()
    s.bg_agent_started(P, P_TYPE, now)
    s.bg_agent_started(Q, Q_TYPE, now)
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now + 1)
    s.bg_agent_stopped(C, now + 10)
    s.agent_call_launched(T, "other work", now + 11, launcher=other_launcher)
    s.bg_agent_started(C, T, now + 12)
    return s, now


LAUNCHERS = pytest.mark.parametrize("other", [Q, "", P], ids=["q", "main", "same-p"])


@LAUNCHERS
def test_state_a_resume_leaves_the_pending_call(other):
    s, _ = _resumed(other)
    assert [(c["desc"], c["launcher"]) for c in s.pending_agent_calls] == \
        [("other work", other)]


def test_state_a_resume_is_not_linked_to_the_other_launcher():
    s, _ = _resumed(Q)
    assert s.agent_parents.get(C) != Q


@LAUNCHERS
def test_state_a_resume_keeps_its_description(other):
    s, _ = _resumed(other)
    assert s.bg_agents[C]["desc"] == "helper work"


@pytest.mark.parametrize("other", [Q, P], ids=["q", "same-p"])
def test_state_the_real_child_takes_the_call_and_links(other):
    s, now = _resumed(other)
    s.bg_agent_started(C2, T, now + 13)
    assert s.agent_parents.get(C2) == other


@LAUNCHERS
def test_state_the_real_child_takes_the_calls_description(other):
    s, now = _resumed(other)
    s.bg_agent_started(C2, T, now + 13)
    assert s.bg_agents[C2]["desc"] == "other work"


def test_state_the_main_calls_real_child_stays_top_level():
    s, now = _resumed("")
    s.bg_agent_started(C2, T, now + 13)
    assert C2 not in s.agent_parents


def test_state_a_resume_leaves_mixed_pending_calls_alone():
    """Mixed launchers at a NEW start drop every call of the type; a
    resume must not apply that rule."""
    s = TrackedSession(name="claude-frontend", label="frontend")
    now = time.monotonic()
    s.bg_agent_started(P, P_TYPE, now)
    s.bg_agent_started(Q, Q_TYPE, now)
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now + 1)
    s.bg_agent_stopped(C, now + 10)
    s.agent_call_launched(T, "main work", now + 11)
    s.agent_call_launched(T, "q work", now + 11, launcher=Q)
    s.bg_agent_started(C, T, now + 12)
    assert len(s.pending_agent_calls) == 2


def test_state_a_resume_of_an_unlinked_agent_gets_no_link():
    """C had no call at its first start; it resumes while P's call of its
    type waits."""
    s = TrackedSession(name="claude-frontend", label="frontend")
    now = time.monotonic()
    s.bg_agent_started(P, P_TYPE, now)
    s.bg_agent_started(C, T, now + 1)
    s.bg_agent_stopped(C, now + 10)
    s.agent_call_launched(T, "helper work", now + 11, launcher=P)
    s.bg_agent_started(C, T, now + 12)
    assert C not in s.agent_parents


def test_state_a_resume_of_an_unlinked_agent_gets_no_description():
    s = TrackedSession(name="claude-frontend", label="frontend")
    now = time.monotonic()
    s.bg_agent_started(P, P_TYPE, now)
    s.bg_agent_started(C, T, now + 1)
    s.bg_agent_stopped(C, now + 10)
    s.agent_call_launched(T, "helper work", now + 11, launcher=P)
    s.bg_agent_started(C, T, now + 12)
    assert s.bg_agents[C]["desc"] == ""


# ---- through hook datagrams ----------------------------------------------------------------

@pytest.fixture
def stopped(rig):
    """P and Q (the session's own) run; P's helper C ran and stopped."""
    rig.call(P_TYPE, "run the pipeline")
    rig.start(P, P_TYPE)
    rig.link(P, by="")
    rig.call(Q_TYPE, "second runner")
    rig.start(Q, Q_TYPE)
    rig.link(Q, by="")
    rig.launch(C, T, "helper work", by=P)
    rig.sleep(10)
    rig.stop(C, T)
    return rig


@pytest.fixture
def resumed(stopped):
    """Q calls ship-tester; before Q's child starts, C resumes."""
    stopped.call(T, "other work", by=Q)
    stopped.start(C, T)
    return stopped


def test_hook_a_resume_leaves_qs_call_pending(resumed):
    assert [(c["desc"], c["launcher"]) for c in resumed.sess.pending_agent_calls] == \
        [("other work", Q)]


def test_hook_a_resume_is_not_linked_to_q(resumed):
    assert resumed.sess.agent_parents.get(C) != Q


def test_hook_a_resume_keeps_its_description(resumed):
    assert resumed.sess.bg_agents[C]["desc"] == "helper work"


def test_hook_qs_real_child_links_to_q(resumed):
    resumed.start(C2, T)
    assert resumed.sess.agent_parents.get(C2) == Q


def test_hook_qs_real_child_gets_qs_description(resumed):
    resumed.start(C2, T)
    assert resumed.sess.bg_agents[C2]["desc"] == "other work"


def test_hook_qs_real_child_card_row_names_q(resumed):
    resumed.start(C2, T)
    rows = [s for s, _d in resumed.sess.tool_history if s.startswith(ROBOT)]
    assert rows.count(f"{ROBOT} {Q_TYPE} › {T}") == 1


def test_hook_a_resume_adds_no_second_card_row(resumed):
    rows = [s for s, _d in resumed.sess.tool_history
            if s.startswith(f"{ROBOT} {P_TYPE} › {T}")]
    assert len(rows) == 1


def test_hook_a_resume_leaves_the_main_calls_pending(stopped):
    stopped.call(T, "main work")
    stopped.start(C, T)
    assert [c["desc"] for c in stopped.sess.pending_agent_calls] == ["main work"]


def test_hook_the_main_calls_real_child_gets_main_work(stopped):
    stopped.call(T, "main work")
    stopped.start(C, T)
    stopped.start(C2, T)
    assert stopped.sess.bg_agents[C2]["desc"] == "main work"


# ---- a resume under an answer's block -------------------------------------------------------

def test_hook_a_resumed_row_stays_under_its_parent_in_the_block(rig, shapes):
    """C was sent under P; it stops, Q calls its type, C resumes: C's row
    must not move (a link to the unlisted Q would make it a top row)."""
    rig.call(P_TYPE, "run the pipeline")
    rig.start(P, P_TYPE)
    rig.link(P, by="")
    rig.launch(C, T, "helper work", by=P)
    rig.answer()
    rig.sleep(10)
    rig.stop(C, T)
    rig.tick(31)
    rig.call(Q_TYPE, "second runner")
    rig.start(Q, Q_TYPE)
    rig.link(Q, by="")
    rig.call(T, "other work", by=Q)
    rig.start(C, T)
    rig.tick(31)
    assert shapes.tree(rig.last_block()) == [(1, P_TYPE), (2, T)]
