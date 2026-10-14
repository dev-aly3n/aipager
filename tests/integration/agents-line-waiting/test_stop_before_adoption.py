"""A ``SubagentStop`` that reaches the new daemon after it loaded the state
file and before (or while) its startup recovery adopts the busy card
(design.md success criterion 9, "later stops close them correctly"; risk
"Adoption after a stop handled before recovery"; spec B).

The hook receiver runs before ``bot.recover_sessions()``, so a restored
agent can stop in that window. The adoption must not bring it back: no
running 🤖 row, no place in the job's table, nothing holding the job
open, and no later answer showing it as working.

Two timings are fed through the real hook receiver:
- *before*: after ``SessionRegistry.load()``, before ``recover_sessions()``.
- *during*: from inside ``recover_sessions()``, while it awaits
  ``inject.list_sessions()`` (its dtach scan, an await before the adoption).
"""

from __future__ import annotations

import json
import logging

import pytest

from aipager.dtach import inject
from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS
from aipager.state import Status
from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE
TURN = "[via Telegram · @alice]\nship the settings fix"


def _mid_wait(r):
    """A running turn; P launched by it; P's Agent call starts C; P waits."""
    h.append(r.transcript, h.user_line(TURN))
    r.prompt(TURN)
    r.agent_call(P, P_TYPE)
    r.agent_call(C, C_TYPE, by=P)
    r.stop(P, P_TYPE)
    r.sleep(40)
    assert r.sess.status is Status.BUSY and r.sess.busy_msg_id


def _working_rows(block: str, agent_type: str) -> list[str]:
    return [x for x in h.rows(block)
            if f"**{agent_type}**" in x and h.is_working_row(x)]


def _running_robot_rows(r, agent_type: str) -> list[str]:
    return [s for s, done in r.robot_rows(agent_type) if not done]


def _send_answer(r) -> str:
    """The turn's answer goes out; returns its whole text (a status block
    is there only when the answer has agents to show)."""
    r.sess.trigger_msg_id = h.TRIGGER
    r.sess.status = Status.IDLE
    r.run(r.bot.notify(r.sess, "idle_prompt",
                       {"summary": h.bb.ANSWER, "raw_md": h.bb.ANSWER}))
    r.drain()
    return h.bb.answer_text(r.wire, h.bb.ANSWER)


def _loaded(rig, mk_bot, monkeypatch):
    """The new daemon has loaded the state file; recovery has not run."""
    _mid_wait(rig)
    return h.restart(rig, mk_bot, monkeypatch, recover=False)


def _recover(r):
    r.run(r.bot.recover_sessions())
    r.drain()
    return r


def _during(rig, mk_bot, monkeypatch, events):
    """Recovery with *events* fed through the receiver while it awaits
    the dtach scan."""
    r = _loaded(rig, mk_bot, monkeypatch)
    names = [r.sess.name]
    fed = []

    async def _list_sessions():
        if not fed:
            fed.append(True)
            for i, (event, fields) in enumerate(events):
                payload = {"session": r.sess.name, "hook_event_name": event,
                           "transcript_path": r.transcript,
                           "cwd": "/home/alice/frontend", "prompt_id": f"pid-in-{i}"}
                payload.update(fields)
                await r.recv._on_datagram(json.dumps(payload).encode())
        return list(names)

    monkeypatch.setattr(inject, "list_sessions", _list_sessions)
    _recover(r)
    assert fed, "the recovery never scanned the dtach side"
    return r


C_STOP = ("SubagentStop", {"agent_id": C, "agent_type": C_TYPE,
                           "last_assistant_message": "review done"})
P_RESUME = ("SubagentStart", {"agent_id": P, "agent_type": P_TYPE})
P_TOOL = ("PreToolUse", {"agent_id": P, "agent_type": "x", "tool_name": "Read",
                         "tool_input": {"file_path": "/home/alice/a.py"}})
P_STOP = ("SubagentStop", {"agent_id": P, "agent_type": P_TYPE,
                           "last_assistant_message": "all done"})


# ---- the child's stop lands BEFORE the recovery -------------------------------------

@pytest.fixture
def c_stopped_before(rig, mk_bot, monkeypatch, caplog):
    r = _loaded(rig, mk_bot, monkeypatch)
    assert C in r.sess.bg_agents, "control: C came back from the state file"
    r.stop(C, C_TYPE)
    with caplog.at_level(logging.INFO):
        _recover(r)
    r.logs = list(caplog.messages)
    return r


def test_control_the_card_was_adopted(c_stopped_before):
    assert any("busy card" in m and "adopted" in m for m in c_stopped_before.logs), \
        c_stopped_before.logs


def test_a_child_stopped_before_the_adoption_is_not_running(c_stopped_before):
    assert C not in c_stopped_before.sess.bg_agents


def test_a_child_stopped_before_the_adoption_is_not_in_the_job(c_stopped_before):
    assert C not in c_stopped_before.sess.active_subagents


def test_a_child_stopped_before_the_adoption_has_no_running_card_row(c_stopped_before):
    assert _running_robot_rows(c_stopped_before, C_TYPE) == []


def test_the_waiting_parent_is_still_put_back_on_the_card(c_stopped_before):
    """Control: the adoption still restores the agent that runs on."""
    assert P in c_stopped_before.sess.active_subagents


def test_the_waiting_parent_keeps_one_running_card_row(c_stopped_before):
    assert len(_running_robot_rows(c_stopped_before, P_TYPE)) == 1


def test_the_adoption_counts_only_the_agent_still_running(c_stopped_before):
    assert any(f"[{h.LABEL}] adopted card: 1 agents of the job back on it" in m
               or f"[{h.LABEL}] adopted card: 1 agent of the job back on it" in m
               for m in c_stopped_before.logs), c_stopped_before.logs


def test_the_restored_job_list_is_used_up(c_stopped_before):
    assert c_stopped_before.sess.restored_job_agents == []


def test_the_next_answer_does_not_show_the_child_working(c_stopped_before):
    block = c_stopped_before.answer()
    assert _working_rows(block, C_TYPE) == [], block


def test_the_next_answer_still_shows_the_parent_waiting(c_stopped_before):
    """Not done: it waits to resume (a waiting agent's row)."""
    block = c_stopped_before.answer()
    assert h.is_waiting_row(h.row_of(block, P_TYPE)), block


def test_later_edits_of_the_answer_never_show_the_child_working(c_stopped_before):
    c_stopped_before.answer()
    c_stopped_before.tick(6)
    c_stopped_before.tick(31)
    block = c_stopped_before.last_block()
    assert _working_rows(block, C_TYPE) == [], block


@pytest.fixture
def then_parent_finishes(c_stopped_before):
    r = c_stopped_before
    r.answer()
    r.start(P, P_TYPE)
    r.tool(P)
    r.stop(P, P_TYPE)
    return r


def test_after_the_parents_final_stop_nothing_runs(then_parent_finishes):
    assert then_parent_finishes.sess.bg_agents == {}


def test_after_the_parents_final_stop_the_job_table_is_empty(then_parent_finishes):
    assert then_parent_finishes.sess.active_subagents == {}


def test_after_the_parents_final_stop_no_work_runs(then_parent_finishes):
    assert then_parent_finishes.sess.job_work_running() is False


def test_after_the_parents_final_stop_no_card_row_runs(then_parent_finishes):
    assert (_running_robot_rows(then_parent_finishes, P_TYPE),
            _running_robot_rows(then_parent_finishes, C_TYPE)) == ([], [])


def test_after_the_parents_final_stop_the_block_shows_no_one_working(then_parent_finishes):
    r = then_parent_finishes
    r.tick(6)
    r.tick(31)
    block = r.last_block()
    assert (_working_rows(block, P_TYPE), _working_rows(block, C_TYPE)) == ([], []), block


# ---- everything stopped before the recovery ------------------------------------------

@pytest.fixture
def all_stopped_before(rig, mk_bot, monkeypatch):
    r = _loaded(rig, mk_bot, monkeypatch)
    r.stop(C, C_TYPE)
    r.start(P, P_TYPE)
    r.tool(P)
    r.stop(P, P_TYPE)
    return _recover(r)


def test_all_stopped_before_the_adoption_nothing_runs(all_stopped_before):
    assert all_stopped_before.sess.bg_agents == {}


def test_all_stopped_before_the_adoption_the_job_table_is_empty(all_stopped_before):
    assert all_stopped_before.sess.active_subagents == {}


def test_all_stopped_before_the_adoption_no_running_card_row(all_stopped_before):
    r = all_stopped_before
    assert (_running_robot_rows(r, P_TYPE), _running_robot_rows(r, C_TYPE)) == ([], [])


def test_all_stopped_before_the_adoption_no_work_holds_the_job(all_stopped_before):
    assert all_stopped_before.sess.job_work_running() is False


def test_all_stopped_before_the_adoption_the_job_closes_after_the_grace(all_stopped_before):
    """The interim answer goes out; once the continuation grace has run
    out nothing keeps the job open."""
    r = all_stopped_before
    _send_answer(r)
    r.sleep(180)
    assert r.sess.job_background_open() is False


def test_all_stopped_before_the_adoption_the_answer_shows_no_one_working(all_stopped_before):
    text = _send_answer(all_stopped_before)
    assert "○ 🤖 **" not in text, text


def test_all_stopped_before_the_adoption_no_lost_agents_notice_later(
        all_stopped_before, monkeypatch):
    """Error guess (review-1): a reinstalled finished agent would sit in
    the job until the silence sweep and then send a false
    ``job_agents_lost``."""
    r = all_stopped_before
    _send_answer(r)
    mon = h.Monitor(r, monkeypatch)
    step = 300.0
    for _ in range(int(SUBAGENT_SILENCE_SECONDS // step) + 2):
        r.sleep(step)
        mon.scan()
    assert "job_agents_lost" not in mon.events


# ---- the child's stop lands WHILE the recovery runs ---------------------------------

@pytest.fixture
def c_stopped_during(rig, mk_bot, monkeypatch):
    return _during(rig, mk_bot, monkeypatch, [C_STOP])


def test_a_child_stopped_during_the_recovery_is_not_running(c_stopped_during):
    assert C not in c_stopped_during.sess.bg_agents


def test_a_child_stopped_during_the_recovery_is_not_in_the_job(c_stopped_during):
    assert C not in c_stopped_during.sess.active_subagents


def test_a_child_stopped_during_the_recovery_has_no_running_card_row(c_stopped_during):
    assert _running_robot_rows(c_stopped_during, C_TYPE) == []


def test_a_child_stopped_during_the_recovery_leaves_the_parent_on_the_card(c_stopped_during):
    assert (P in c_stopped_during.sess.active_subagents,
            len(_running_robot_rows(c_stopped_during, P_TYPE))) == (True, 1)


def test_a_child_stopped_during_the_recovery_is_not_shown_working(c_stopped_during):
    block = c_stopped_during.answer()
    assert _working_rows(block, C_TYPE) == [], block


@pytest.fixture
def all_stopped_during(rig, mk_bot, monkeypatch):
    return _during(rig, mk_bot, monkeypatch, [C_STOP, P_RESUME, P_TOOL, P_STOP])


def test_all_stopped_during_the_recovery_nothing_runs(all_stopped_during):
    assert all_stopped_during.sess.bg_agents == {}


def test_all_stopped_during_the_recovery_the_job_table_is_empty(all_stopped_during):
    assert all_stopped_during.sess.active_subagents == {}


def test_all_stopped_during_the_recovery_no_work_holds_the_job(all_stopped_during):
    assert all_stopped_during.sess.job_work_running() is False


def test_all_stopped_during_the_recovery_no_running_card_row(all_stopped_during):
    r = all_stopped_during
    assert (_running_robot_rows(r, P_TYPE), _running_robot_rows(r, C_TYPE)) == ([], [])


# ---- neighbouring timings (partitions) -----------------------------------------------

def test_a_stop_for_an_unknown_id_before_the_recovery_changes_nothing(
        rig, mk_bot, monkeypatch):
    """A phantom stop in the window: both agents are still put back."""
    r = _loaded(rig, mk_bot, monkeypatch)
    r.phantom()
    _recover(r)
    assert sorted(r.sess.active_subagents) == sorted([P, C])


def test_a_resume_start_before_the_recovery_keeps_one_parent_row(rig, mk_bot, monkeypatch):
    """P resumes (its start, same id) in the window: the adoption still
    gives P exactly one running row."""
    r = _loaded(rig, mk_bot, monkeypatch)
    r.stop(C, C_TYPE)
    r.start(P, P_TYPE)
    _recover(r)
    assert len(_running_robot_rows(r, P_TYPE)) == 1


def test_a_resume_start_before_the_recovery_counts_the_parent_once(rig, mk_bot, monkeypatch):
    r = _loaded(rig, mk_bot, monkeypatch)
    r.stop(C, C_TYPE)
    r.start(P, P_TYPE)
    _recover(r)
    assert list(r.sess.bg_agents) == [P]


def test_a_tool_call_of_the_child_before_the_recovery_keeps_it(rig, mk_bot, monkeypatch):
    """Control partition: activity (not a stop) in the window leaves C
    running and put back on the card."""
    r = _loaded(rig, mk_bot, monkeypatch)
    r.tool(C, "Bash", "pytest")
    _recover(r)
    assert len(_running_robot_rows(r, C_TYPE)) == 1
