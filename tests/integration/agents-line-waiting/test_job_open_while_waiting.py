"""The job stays open while only a waiting parent (and its child) remain,
and no continuation grace is armed before the parent's final stop
(design.md success criterion 6; entrypoints.md ``job_background_open()``,
``job_work_running()``; the monitor's silence list never drops a waiting
parent whose child still runs)."""

from __future__ import annotations

import pytest

from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS
from aipager.state import Status
from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


@pytest.fixture
def interim(rig):
    """P waiting, C running, the turn's interim answer out (IDLE)."""
    rig.prompt("ship it")
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    rig.answer()
    assert rig.sess.status is Status.IDLE
    return rig


def test_the_job_is_open_with_the_parent_waiting(interim):
    assert interim.sess.job_background_open() is True


def test_work_is_running_with_the_parent_waiting(interim):
    assert interim.sess.job_work_running() is True


@pytest.fixture
def child_stopped(interim):
    interim.sleep(10)
    interim.stop(C, C_TYPE)
    return interim


def test_after_the_childs_stop_the_job_is_still_open(child_stopped):
    assert child_stopped.sess.job_background_open() is True


def test_after_the_childs_stop_work_still_runs_so_no_grace_is_needed(child_stopped):
    """A grace is armed only when no work runs; the waiting parent is
    work, so ``job_work_running()`` stays true."""
    assert child_stopped.sess.job_work_running() is True


def test_after_the_childs_stop_and_the_grace_window_the_job_is_open(child_stopped):
    """If the child's stop had armed the 60 s continuation grace, the job
    would close once it ran out. It must not: P is still waiting."""
    child_stopped.sleep(90)
    assert child_stopped.sess.job_background_open() is True


@pytest.fixture
def parent_final(child_stopped):
    child_stopped.start(P, P_TYPE)
    child_stopped.tool(P)
    child_stopped.stop(P, P_TYPE)
    return child_stopped


def test_after_the_final_stop_no_work_runs(parent_final):
    assert parent_final.sess.job_work_running() is False


def test_after_the_final_stop_the_grace_keeps_the_job_open(parent_final):
    """The final stop arms the continuation grace (today's behaviour)."""
    assert parent_final.sess.job_background_open() is True


def test_the_grace_from_the_final_stop_runs_out(parent_final):
    parent_final.sleep(120)
    assert parent_final.sess.job_background_open() is False


def test_the_continuation_after_the_final_stop_is_taken_as_the_jobs(parent_final):
    """The task notification Claude sends after P's final stop arrives
    inside the grace: the job is still open when it does."""
    parent_final.sleep(5)
    parent_final.prompt(
        f"<task-notification>\n<task-id>{P}</task-id>\n<status>completed</status>\n"
        "<note>A task-notification fires each time this agent stops with no live "
        "background children of its own.</note>\n</task-notification>")
    assert parent_final.sess.job_background_open() is True


# ---- the monitor's silence rules -------------------------------------------------------

def test_a_silent_waiting_parent_with_a_working_child_outlives_the_silence_window(
        interim, monkeypatch):
    """P sends no hook while it waits; C keeps working for longer than the
    silence window. The monitor keeps P in both tables."""
    mon = h.Monitor(interim, monkeypatch)
    step = 300.0
    for _ in range(int(SUBAGENT_SILENCE_SECONDS // step) + 2):
        interim.sleep(step)
        interim.tool(C, "Bash", "pytest")
        mon.scan()
    assert (P in interim.sess.bg_agents, P in interim.sess.active_subagents) == (True, True)


def test_the_waiting_parents_job_stays_open_past_the_silence_window(
        interim, monkeypatch):
    mon = h.Monitor(interim, monkeypatch)
    step = 300.0
    for _ in range(int(SUBAGENT_SILENCE_SECONDS // step) + 2):
        interim.sleep(step)
        interim.tool(C, "Bash", "pytest")
        mon.scan()
    assert "job_agents_lost" not in mon.events


def test_control_a_silent_agent_that_is_not_waiting_is_dropped(rig, monkeypatch):
    """Control for the two above: the silence window does drop a plain
    silent agent in this harness."""
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE)                  # siblings, no link
    mon = h.Monitor(rig, monkeypatch)
    step = 300.0
    for _ in range(int(SUBAGENT_SILENCE_SECONDS // step) + 2):
        rig.sleep(step)
        rig.tool(C, "Bash", "pytest")
        mon.scan()
    assert P not in rig.sess.bg_agents
