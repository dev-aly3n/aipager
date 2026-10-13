"""A waiting parent that never resumes leaves 120 s after its last child
ended, as "no longer counted" (its answer block left as last shown),
never as done (design.md success criterion 7; entrypoints.md
``sweep_unresumed_parents``, ``WAITING_PARENT_RESUME_SECONDS``)."""

from __future__ import annotations

import logging

import pytest

from aipager.state import WAITING_PARENT_RESUME_SECONDS, Status
from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


def test_the_resume_deadline_is_two_minutes():
    assert WAITING_PARENT_RESUME_SECONDS == 120.0


@pytest.fixture
def orphan(rig, monkeypatch):
    """P waiting, interim answer out, C stops, the monitor sees no child."""
    rig.prompt("ship it")
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    rig.answer()
    rig.sleep(5)
    rig.stop(C, C_TYPE)
    rig.tick(31)                     # C's done row is edited in
    mon = h.Monitor(rig, monkeypatch)
    mon.scan()                       # first tick with no child: the clock starts
    rig.mon = mon
    return rig


def test_just_inside_the_deadline_the_parent_stays(orphan):
    orphan.sleep(WAITING_PARENT_RESUME_SECONDS - 1)
    orphan.mon.scan()
    assert P in orphan.sess.bg_agents


@pytest.fixture
def dropped(orphan, caplog):
    orphan.shown = orphan.last_block()
    with caplog.at_level(logging.INFO):
        orphan.sleep(WAITING_PARENT_RESUME_SECONDS + 1)
        orphan.mon.scan()
    orphan.logs = list(caplog.messages)
    return orphan


def test_past_the_deadline_the_parent_leaves_the_running_set(dropped):
    assert P not in dropped.sess.bg_agents


def test_past_the_deadline_the_parent_leaves_the_job(dropped):
    assert P not in dropped.sess.active_subagents


def test_the_block_is_left_as_last_shown(dropped):
    dropped.tick(6)
    dropped.tick(31)
    assert dropped.last_block() == dropped.shown


def test_the_block_never_says_done(dropped):
    dropped.tick(6)
    dropped.tick(31)
    dropped.tick(600)
    assert "✅" not in dropped.last_block()


def test_the_idle_job_is_told_its_agents_are_lost(dropped):
    assert dropped.mon.events.count("job_agents_lost") == 1


def test_the_drop_is_logged(dropped):
    assert any(f"waiting agent {P} did not resume within 120 s after its own agents "
               "ended - no longer counted as running" in m for m in dropped.logs), dropped.logs


def test_sweep_unresumed_parents_returns_the_dropped_id(rig):
    """The state method on its own, with ``now`` passed in."""
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    rig.stop(C, C_TYPE)
    t = rig.now()
    rig.sess.sweep_unresumed_parents(t)
    assert rig.sess.sweep_unresumed_parents(t + WAITING_PARENT_RESUME_SECONDS + 1) == [P]


def test_sweep_unresumed_parents_keeps_a_parent_with_a_running_child(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    t = rig.now()
    rig.sess.sweep_unresumed_parents(t)
    assert rig.sess.sweep_unresumed_parents(t + 10 * WAITING_PARENT_RESUME_SECONDS) == []


def test_sweep_bg_agents_keeps_a_waiting_parent_with_a_running_child(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    rig.sess.sweep_bg_agents(rig.now() + 7200, 1800)
    assert P in rig.sess.bg_agents


def test_a_resumed_parent_is_not_dropped_by_the_resume_deadline(orphan):
    """Its SubagentStart ends the wait; then it works quietly for longer
    than 120 s (one long tool call)."""
    orphan.start(P, P_TYPE)
    orphan.hook("PreToolUse", tool_name="Bash", tool_input={"command": "pytest"},
                agent_id=P, agent_type=P_TYPE)
    orphan.sleep(WAITING_PARENT_RESUME_SECONDS + 60)
    orphan.mon.scan()
    assert P in orphan.sess.bg_agents


def test_a_busy_session_is_not_told_its_agents_are_lost(rig, monkeypatch):
    """The foreground turn owns the card while BUSY (today's rule)."""
    rig.prompt("ship it")
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    rig.stop(C, C_TYPE)
    assert rig.sess.status is Status.BUSY
    mon = h.Monitor(rig, monkeypatch)
    mon.scan()
    rig.sleep(WAITING_PARENT_RESUME_SECONDS + 1)
    mon.scan()
    assert "job_agents_lost" not in mon.events
