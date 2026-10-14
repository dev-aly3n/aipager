"""A foreground (synchronous) agent that stops while an agent of its own
still runs waits like a background one (design.md risk "A foreground
(synchronous) agent is paused the same way", decided in fix iteration 2:
Claude Code backgrounds such an agent and its caller's ``Agent`` call
returns ``async_launched`` only after the agent's stop).

F is started by the main loop's ``Agent`` call WITHOUT
``run_in_background``; no ``PostToolUse`` for that call has arrived when
F starts its own agent C and stops. Success criteria 1, 2, 3 and 6 apply
to F as they do to a background parent.
"""

from __future__ import annotations

import pytest

from tests import agents_line_waiting_bb as h

F = "a0f0e9d8c7b6a5f40"
F_TYPE = "general-purpose"
C, C_TYPE = h.C, h.C_TYPE
FG_INPUT = {"subagent_type": F_TYPE, "description": "fix the settings page",
            "prompt": "go"}


def _foreground_start(r):
    r.prompt("fix the settings page")
    r.hook("PreToolUse", tool_name="Agent", tool_input=FG_INPUT)
    r.start(F, F_TYPE)


def _working_rows(block: str, agent_type: str) -> list[str]:
    return [x for x in h.rows(block)
            if f"**{agent_type}**" in x and h.is_working_row(x)]


@pytest.fixture
def fg_paused(rig):
    """F (foreground) starts C and stops; then the main loop's Agent call
    returns as backgrounded (``async_launched``)."""
    _foreground_start(rig)
    rig.agent_call(C, C_TYPE, by=F)
    rig.stop(F, F_TYPE)
    rig.link(F, by="", tool_input=FG_INPUT)
    return rig


def test_a_foreground_agent_with_a_running_child_stays_running(fg_paused):
    assert F in fg_paused.sess.bg_agents


def test_a_foreground_agent_with_a_running_child_is_waiting(fg_paused):
    assert fg_paused.sess.agent_is_waiting(F) is True


def test_a_foreground_agent_waiting_stays_in_the_job(fg_paused):
    assert F in fg_paused.sess.active_subagents


def test_a_foreground_agent_waiting_lists_its_child(fg_paused):
    assert fg_paused.sess.agent_children_running(F) == [C]


def test_a_foreground_agent_waiting_keeps_its_card_row_running(fg_paused):
    assert [d for _s, d in fg_paused.robot_rows(F_TYPE)] == [False]


def test_the_interim_answer_counts_the_waiting_foreground_agent(fg_paused):
    assert h.header(fg_paused.answer()) == "⏳ 2 agents working"


def test_the_waiting_foreground_agent_is_waiting_past_the_settle_and_edit(fg_paused):
    """Not done: its row says it waits for its own agents (a waiting
    agent's row since the agents tree, 2026-10-14)."""
    fg_paused.answer()
    fg_paused.tick(6)
    fg_paused.tick(31)
    assert h.is_waiting_row(h.row_of(fg_paused.last_block(), F_TYPE))


def test_the_pause_is_independent_of_the_callers_return(rig):
    """Partition: the caller's Agent call has NOT returned yet when F
    stops (``async_launched`` comes later or never): F waits all the same."""
    _foreground_start(rig)
    rig.agent_call(C, C_TYPE, by=F)
    rig.stop(F, F_TYPE)
    assert rig.sess.agent_is_waiting(F) is True


def test_a_synchronous_return_after_the_stop_does_not_end_the_wait(rig):
    """Error guess: the caller's call returns a plain (non-async) result
    after F's stop; F's own agent still runs, so F still waits."""
    _foreground_start(rig)
    rig.agent_call(C, C_TYPE, by=F)
    rig.stop(F, F_TYPE)
    rig.link(F, by="", tool_input=FG_INPUT,
             response={"status": "completed", "content": [{"type": "text", "text": "ok"}]})
    assert F in rig.sess.bg_agents


@pytest.fixture
def fg_child_stopped(fg_paused):
    fg_paused.answer()
    fg_paused.sleep(10)
    fg_paused.stop(C, C_TYPE)
    return fg_paused


def test_the_job_stays_open_while_the_foreground_agent_waits(fg_child_stopped):
    fg_child_stopped.sleep(90)
    assert fg_child_stopped.sess.job_background_open() is True


def test_work_runs_while_the_foreground_agent_waits(fg_child_stopped):
    assert fg_child_stopped.sess.job_work_running() is True


def test_the_foreground_agents_resume_adds_no_row(fg_child_stopped):
    fg_child_stopped.start(F, F_TYPE)
    assert len(fg_child_stopped.robot_rows(F_TYPE)) == 1


def test_the_foreground_agents_resume_ends_its_wait(fg_child_stopped):
    fg_child_stopped.start(F, F_TYPE)
    assert fg_child_stopped.sess.agent_is_waiting(F) is False


@pytest.fixture
def fg_done(fg_child_stopped):
    fg_child_stopped.start(F, F_TYPE)
    fg_child_stopped.tool(F)
    fg_child_stopped.stop(F, F_TYPE)
    return fg_child_stopped


def test_the_foreground_agents_final_stop_removes_it(fg_done):
    assert fg_done.sess.bg_agents == {}


def test_the_foreground_agents_final_stop_shows_both_done(fg_done):
    fg_done.tick(6)
    fg_done.tick(31)
    assert fg_done.last_block().startswith("✅ 2 agents done (")


def test_the_foreground_agents_final_stop_leaves_no_work(fg_done):
    assert fg_done.sess.job_work_running() is False


# ---- a foreground agent whose own agent already ended --------------------------------

def test_a_foreground_agent_whose_child_ended_first_is_done_at_once(rig):
    _foreground_start(rig)
    rig.agent_call(C, C_TYPE, by=F)
    rig.stop(C, C_TYPE)
    rig.stop(F, F_TYPE)
    assert F not in rig.sess.bg_agents


def test_a_foreground_agent_with_no_child_is_done_at_once(rig):
    """Control: today's foreground agent, no agent of its own."""
    _foreground_start(rig)
    rig.tool(F)
    rig.stop(F, F_TYPE)
    assert F not in rig.sess.bg_agents
