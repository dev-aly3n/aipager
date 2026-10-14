"""The operator's 2026-10-12 sequence (10:09:58-10:10:03), replayed as hook
datagrams (design.md success criteria 1, 2 and 5; entrypoints.md "Hook
datagrams").

P (pipeline-runner) was launched by the main loop in an earlier turn. In
a new turn P's own Agent call starts C (ship-reviewer) and P ends its own
turn to wait for C: an ordinary ``SubagentStop`` for P. P is waiting, not
done, until C stops, P resumes with a fresh ``SubagentStart`` under the
same id, works, and stops with no agent of its own running.
"""

from __future__ import annotations

import logging
import re

import pytest

from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


def _to_pause(r, **stop_extra):
    """P running from an earlier turn; the new turn; P launches C and
    pauses; phantoms around it."""
    r.agent_call(P, P_TYPE)              # an earlier turn launched P
    r.prompt("check the reviewer")      # 10:09:58 new turn inside the job
    r.agent_call(C, C_TYPE, by=P)       # 10:10:01 P's own Agent call starts C
    r.phantom()
    r.stop(P, P_TYPE, **stop_extra)     # 10:10:03 P ends its turn to wait
    r.phantom(2)


@pytest.fixture
def paused(rig):
    _to_pause(rig)
    return rig


# ---- the pause keeps P --------------------------------------------------------------

def test_the_paused_parent_stays_in_the_running_set(paused):
    assert P in paused.sess.bg_agents


def test_the_paused_parent_is_waiting(paused):
    assert paused.sess.agent_is_waiting(P) is True


def test_the_waiting_flag_is_on_its_running_entry(paused):
    assert paused.sess.bg_agents[P].get("waiting") is True


def test_the_child_is_listed_as_the_parents_running_agent(paused):
    assert paused.sess.agent_children_running(P) == [C]


def test_the_child_is_not_waiting(paused):
    assert paused.sess.agent_is_waiting(C) is False


def test_the_paused_parent_stays_in_the_jobs_table(paused):
    assert P in paused.sess.active_subagents


def test_the_paused_parents_card_row_keeps_running(paused):
    assert paused.robot_rows(P_TYPE) == [(f"{h.ROBOT} {P_TYPE}", False)]


def test_the_pinned_bars_count_includes_the_paused_parent(paused):
    """The pinned bar counts the running set (entrypoints.md)."""
    assert len(paused.sess.bg_agents) == 2


def test_the_job_stays_open(paused):
    paused.sess.status = h.Status.IDLE
    assert paused.sess.job_background_open() is True


def test_the_pause_is_logged_with_its_child_count(rig, caplog):
    with caplog.at_level(logging.INFO):
        _to_pause(rig)
    assert any(f"SubagentStop: {P_TYPE} ({P}, " in m
               and "waiting for 1 agent(s) of its own (bg_tasks=-)" in m
               for m in caplog.messages), caplog.messages


def test_the_pause_logs_the_payloads_agent_tasks(rig, caplog):
    tasks = [{"id": C, "type": "local_agent", "status": "running",
              "description": "review", "agent_type": C_TYPE}]
    with caplog.at_level(logging.INFO):
        _to_pause(rig, background_tasks=tasks)
    assert any("waiting for 1 agent(s) of its own (bg_tasks=1)" in m
               for m in caplog.messages), caplog.messages


# ---- the interim answer -------------------------------------------------------------

def test_the_interim_answer_counts_both_agents(paused):
    assert h.header(paused.answer()) == "⏳ 2 agents working"


def test_the_interim_answer_lists_the_child_as_working(paused):
    assert h.is_working_row(h.row_of(paused.answer(), C_TYPE))


def test_the_interim_answer_lists_the_parent_as_waiting(paused):
    """Not done, and not its stale last tool: it waits for its own agents
    (a waiting agent's row since the agents tree, 2026-10-14)."""
    assert h.is_waiting_row(h.row_of(paused.answer(), P_TYPE))


@pytest.mark.parametrize("seconds", [5.1, 30.1, 600.0])
def test_past_the_settle_and_the_edit_interval_the_parent_still_waits(paused, seconds):
    paused.answer()
    paused.tick(seconds)
    paused.tick(31)
    assert h.is_waiting_row(h.row_of(paused.last_block(), P_TYPE))


def test_past_the_settle_the_header_is_unchanged(paused):
    paused.answer()
    paused.tick(6)
    paused.tick(31)
    assert h.header(paused.last_block()) == "⏳ 2 agents working"


# ---- C stops; P resumes; P stops for good -------------------------------------------

@pytest.fixture
def child_done(paused):
    paused.answer()
    paused.sleep(20)
    paused.stop(C, C_TYPE)
    return paused


def test_the_childs_stop_shows_the_child_done(child_done):
    child_done.tick(31)
    assert h.is_done_row(h.row_of(child_done.last_block(), C_TYPE))


def test_the_childs_stop_leaves_the_parent_waiting(child_done):
    """Not done: it waits to resume (a waiting agent's row)."""
    child_done.tick(31)
    assert h.is_waiting_row(h.row_of(child_done.last_block(), P_TYPE))


def test_the_childs_stop_does_not_end_the_parent(child_done):
    assert P in child_done.sess.bg_agents


@pytest.fixture
def resumed(child_done):
    child_done.sleep(1)
    child_done.start(P, P_TYPE)
    return child_done


def test_the_resume_adds_no_second_card_row(resumed):
    assert len(resumed.robot_rows(P_TYPE)) == 1


def test_the_resume_keeps_the_card_row_running(resumed):
    assert resumed.robot_rows(P_TYPE)[0][1] is False


def test_the_resume_adds_no_second_count(resumed):
    assert list(resumed.sess.active_subagents) == [P]


def test_the_resumed_parent_is_no_longer_waiting(resumed):
    assert resumed.sess.agent_is_waiting(P) is False


def test_the_resume_is_logged(child_done, caplog):
    with caplog.at_level(logging.INFO):
        child_done.start(P, P_TYPE)
    assert f"[{h.LABEL}] SubagentStart: {P_TYPE} ({P}) - resumed" in caplog.messages


@pytest.fixture
def finished(resumed):
    resumed.tool(P)
    resumed.sleep(30)
    resumed.stop(P, P_TYPE, background_tasks=[])
    return resumed


def test_the_final_stop_removes_the_parent(finished):
    assert P not in finished.sess.bg_agents


def test_the_final_stop_leaves_the_jobs_table(finished):
    assert P not in finished.sess.active_subagents


def test_the_final_stop_settles_the_parents_card_row(finished):
    assert [done for _s, done in finished.robot_rows(P_TYPE)] == [True]


def test_the_final_stop_shows_both_done(finished):
    finished.tick(6)
    finished.tick(31)
    assert finished.last_block().startswith("✅ 2 agents done (")


def test_the_done_time_covers_the_whole_wait(finished):
    """The header's time runs from the answer to the last agent's end
    (docs/commands.md): 20 + 1 + 30 s of virtual time here, the wait
    included, not only the time since the resume."""
    finished.tick(6)
    finished.tick(31)
    m = re.match(r"✅ 2 agents done \((\d+):(\d\d)\)", finished.last_block())
    assert m and int(m.group(1)) * 60 + int(m.group(2)) >= 51, finished.last_block()
