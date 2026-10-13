"""A background agent that ends its turn while an agent it started still
runs is waiting, not done (agents-line-waiting design.md 2-4): the state
bookkeeping, the hook receiver's pause/resume/finish decision, the card's
resume row, and the monitor's bounds. Time is passed explicitly."""

from __future__ import annotations

import time

from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS, SessionMonitor
from aipager.state import (
    AGENT_PARENTS_CAP, WAITING_PARENT_RESUME_SECONDS, Status,
)
from tests import answer_status_bb as bb

P, C, G = "a505c081ca3926ed5", "ac1d54c85d78f59ea", "a7e0d1c2b3a4f5e60"


def _rx(bot, sink=None):
    return bb.receiver(bot.registry, sink)


def _start(run_async, recv, sess, agent_id, agent_type, **extra):
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=agent_id,
            agent_type=agent_type, **extra)


def _link(run_async, recv, sess, parent, child):
    """The parent's own PostToolUse of the Agent call that started child."""
    bb.hook(run_async, recv, sess, "PostToolUse", agent_id=parent,
            tool_name="Agent",
            tool_input={"subagent_type": "ship-reviewer",
                        "description": "Review"},
            tool_response={"isAsync": True, "status": "async_launched",
                           "agentId": child})


def _stop(run_async, recv, sess, agent_id, agent_type, **extra):
    bb.hook(run_async, recv, sess, "SubagentStop", agent_id=agent_id,
            agent_type=agent_type, **extra)


def _tree(run_async, bot, recv=None, sink=None):
    """P running, C started by P (linked), as the 10:10:01 journal."""
    sess = bb.make_sess(bot, label="wait")
    recv = recv or _rx(bot, sink)
    _start(run_async, recv, sess, P, "pipeline-runner")
    _start(run_async, recv, sess, C, "ship-reviewer")
    _link(run_async, recv, sess, P, C)
    return sess, recv


def _line(sess, *ids):
    rec = {"chat_id": bb.CHAT, "msg_id": 9001, "ids": list(ids), "ends": {}}
    sess.add_agents_line(rec)
    return rec


# ── the pause ────────────────────────────────────────────────────────────────

def test_a_pause_with_a_running_child_keeps_the_parent_running(
        mk_bot, run_async):
    bot = mk_bot()
    sink: list = []
    sess, recv = _tree(run_async, bot, sink=sink)
    rec = _line(sess, P, C)
    _stop(run_async, recv, sess, P, "pipeline-runner",
          background_tasks=[{"id": C, "type": "local_agent"}])
    assert sess.agent_is_waiting(P)
    assert sess.agent_children_running(P) == [C]
    assert sorted(sess.bg_agent_labels()) == ["pipeline-runner", "ship-reviewer"]
    assert P in sess.active_subagents
    assert rec["ends"] == {}
    assert P not in sess.bg_agents_recent
    assert [e for e, _c in sink if e == "subagent_stop"] == []


def test_the_pause_logs_the_waiting_line(mk_bot, run_async, caplog):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    with caplog.at_level("INFO", logger="aipager.dtach.hook_receiver"):
        _stop(run_async, recv, sess, P, "pipeline-runner")
    assert any("waiting for 1 agent(s) of its own (bg_tasks=-)" in r.message
               for r in caplog.records)


def test_a_pause_keeps_the_job_open_and_arms_no_grace(mk_bot, run_async):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    sess.job_interim_seen = True
    sess.status = Status.IDLE
    # The child finishes while the parent waits: still no grace, the
    # parent is still the job's.
    _stop(run_async, recv, sess, P, "pipeline-runner")
    assert sess.job_grace_until == 0.0
    assert sess.job_background_open()
    _stop(run_async, recv, sess, C, "ship-reviewer")
    assert sess.job_grace_until == 0.0
    assert list(sess.active_subagents) == [P]
    assert sess.job_background_open()
    # The parent resumes and finishes for good: now the grace is armed.
    _start(run_async, recv, sess, P, "pipeline-runner", prompt_id="r1")
    _stop(run_async, recv, sess, P, "pipeline-runner", prompt_id="r1")
    assert sess.active_subagents == {}
    assert sess.job_grace_until > 0.0


def test_a_parent_whose_child_already_finished_is_done_at_once(
        mk_bot, run_async):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    rec = _line(sess, P, C)
    _stop(run_async, recv, sess, C, "ship-reviewer")
    _stop(run_async, recv, sess, P, "pipeline-runner")
    assert sess.bg_agent_labels() == []
    assert P in sess.bg_agents_recent
    assert P not in sess.active_subagents
    assert set(rec["ends"]) == {P, C}


def test_a_stop_for_an_unstarted_id_with_a_linked_child_creates_nothing(
        mk_bot, run_async):
    """A phantom-shaped id that a link names as a parent is still not an
    agent this session saw start."""
    bot = mk_bot()
    sess = bb.make_sess(bot, label="ghost")
    recv = _rx(bot)
    _start(run_async, recv, sess, C, "ship-reviewer")
    _link(run_async, recv, sess, "aghost0000000001", C)
    _stop(run_async, recv, sess, "aghost0000000001", "")
    assert "aghost0000000001" not in sess.bg_agents
    assert "aghost0000000001" not in sess.active_subagents
    assert sess.bg_agent_labels() == ["ship-reviewer"]


def test_phantom_stops_change_nothing_while_a_parent_waits(mk_bot, run_async):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    before = (dict(sess.bg_agents), dict(sess.active_subagents),
              dict(sess.agent_parents), list(sess.tool_history))
    for i in range(3):
        _stop(run_async, recv, sess, f"a8ce656ff72db070{i}", "")
    assert (dict(sess.bg_agents), dict(sess.active_subagents),
            dict(sess.agent_parents), list(sess.tool_history)) == before
    assert sess.agent_is_waiting(P)


def test_a_grandchild_keeps_both_ancestors_waiting(mk_bot, run_async):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    _start(run_async, recv, sess, G, "Explore")
    _link(run_async, recv, sess, C, G)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    _stop(run_async, recv, sess, C, "ship-reviewer")
    assert sess.agent_is_waiting(P) and sess.agent_is_waiting(C)
    _stop(run_async, recv, sess, G, "Explore")
    # C resumes and finishes: P still waits only until its own stop.
    _start(run_async, recv, sess, C, "ship-reviewer", prompt_id="r")
    _stop(run_async, recv, sess, C, "ship-reviewer", prompt_id="r")
    assert sess.bg_agent_labels() == ["pipeline-runner"]
    _start(run_async, recv, sess, P, "pipeline-runner", prompt_id="r")
    _stop(run_async, recv, sess, P, "pipeline-runner", prompt_id="r")
    assert sess.bg_agent_labels() == []


# ── the resume ───────────────────────────────────────────────────────────────

def test_a_resume_start_clears_waiting_and_keeps_the_job_entry(
        mk_bot, run_async):
    bot = mk_bot()
    sink: list = []
    sess, recv = _tree(run_async, bot, sink=sink)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    entry = sess.active_subagents[P]
    started = entry["started_at"]
    entry["history_idx"] = 4
    sink.clear()
    _start(run_async, recv, sess, P, "pipeline-runner", prompt_id="again")
    assert not sess.agent_is_waiting(P)
    assert sess.active_subagents[P] is entry
    assert entry["started_at"] == started and entry["history_idx"] == 4
    assert sink == [("subagent_start", {"agent_id": P,
                                        "agent_type": "pipeline-runner",
                                        "resumed": True})]


def test_a_resume_adds_no_row_and_no_count_on_the_card(mk_bot, run_async):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="card")
    sess.active_subagents[P] = {"type": "pipeline-runner",
                                "started_at": time.monotonic(),
                                "history_idx": 0}
    rows, count = list(sess.tool_history), sess.subagent_count_this_turn
    run_async(bb.bounded(bot.notify(sess, "subagent_start", {
        "agent_id": P, "agent_type": "pipeline-runner", "resumed": True})))
    assert sess.tool_history == rows
    assert sess.subagent_count_this_turn == count
    run_async(bb.bounded(bot.notify(sess, "subagent_start", {
        "agent_id": C, "agent_type": "ship-reviewer"})))
    assert len(sess.tool_history) == len(rows) + 1
    assert sess.subagent_count_this_turn == count + 1


def test_a_tool_event_of_its_own_clears_waiting(mk_bot, run_async):
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id=P, tool_name="Read",
            tool_input={"file_path": "/home/alice/x.py"})
    assert not sess.agent_is_waiting(P)
    assert P in sess.bg_agents


def test_a_message_display_of_its_own_does_not_clear_waiting(
        mk_bot, run_async):
    """Claude Code sends a message's display at its end: the parent's last
    message can land after its pause."""
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    bb.hook(run_async, recv, sess, "MessageDisplay", agent_id=P,
            delta="Waiting for the reviewer.", message_id="m9")
    assert sess.agent_is_waiting(P)


def test_a_late_link_post_does_not_count_as_a_resume(mk_bot, run_async):
    """A second link from an already waiting parent keeps it waiting."""
    bot = mk_bot()
    sess, recv = _tree(run_async, bot)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    _start(run_async, recv, sess, G, "Explore")
    _link(run_async, recv, sess, P, G)
    assert sess.agent_is_waiting(P)
    assert sorted(sess.agent_children_running(P)) == sorted([C, G])


# ── the late link ────────────────────────────────────────────────────────────

def test_a_link_after_the_pause_brings_the_parent_back_waiting(
        mk_bot, run_async):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="late")
    recv = _rx(bot)
    _start(run_async, recv, sess, P, "pipeline-runner")
    _start(run_async, recv, sess, C, "ship-reviewer")
    rec = _line(sess, P, C)
    _stop(run_async, recv, sess, P, "pipeline-runner")
    assert P not in sess.bg_agents and rec["ends"][P]["outcome"] == "done"
    _link(run_async, recv, sess, P, C)
    assert sess.agent_is_waiting(P)
    assert P not in rec["ends"]


def test_a_link_for_a_finished_child_brings_nothing_back(mk_bot, run_async):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="late2")
    recv = _rx(bot)
    _start(run_async, recv, sess, P, "pipeline-runner")
    _start(run_async, recv, sess, C, "ship-reviewer")
    _stop(run_async, recv, sess, C, "ship-reviewer")
    _stop(run_async, recv, sess, P, "pipeline-runner")
    _link(run_async, recv, sess, P, C)
    assert sess.bg_agent_labels() == []


def test_a_finished_parents_old_child_is_not_its_child_again(mk_bot):
    """Links go when the parent finishes: an id used again does not wait
    for a child of its earlier run."""
    bot = mk_bot()
    sess = bb.make_sess(bot, label="reuse")
    now = 1000.0
    sess.bg_agent_started(P, "pipeline-runner", now)
    sess.bg_agent_started(C, "ship-reviewer", now)
    sess.agent_launched_child(P, C, now)
    sess.bg_agent_stopped(C, now + 1)
    assert sess.bg_agent_pause(P, now + 2) == []
    sess.bg_agent_stopped(P, now + 2)
    # The child comes back (its still-running note), the parent id again.
    assert sess.bg_agent_still_running(C, now + 3)
    sess.bg_agent_started(P, "pipeline-runner", now + 4)
    assert sess.bg_agent_pause(P, now + 5) == []


def test_a_finished_child_forgets_its_link(mk_bot):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="forget")
    sess.bg_agent_started(P, "pipeline-runner", 10.0)
    sess.bg_agent_started(C, "ship-reviewer", 10.0)
    sess.agent_launched_child(P, C, 10.0)
    assert sess.agent_parents == {C: P}
    sess.bg_agent_stopped(C, 11.0)
    assert sess.agent_parents == {}


def test_the_links_are_capped(mk_bot):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="cap")
    for i in range(AGENT_PARENTS_CAP + 5):
        sess.agent_launched_child(P, f"b{i:016d}", 1.0)
    assert len(sess.agent_parents) == AGENT_PARENTS_CAP
    assert "b0000000000000000" not in sess.agent_parents


# ── the bounds ───────────────────────────────────────────────────────────────

def _waiting(bot, now, label="bound"):
    sess = bb.make_sess(bot, label=label)
    sess.bg_agent_started(P, "pipeline-runner", now)
    sess.bg_agent_started(C, "ship-reviewer", now)
    sess.agent_launched_child(P, C, now)
    assert sess.bg_agent_pause(P, now) == [C]
    return sess


def test_a_waiting_parent_outlives_the_silence_window(mk_bot):
    bot = mk_bot()
    now = 5000.0
    sess = _waiting(bot, now)
    later = now + SUBAGENT_SILENCE_SECONDS + 60
    sess.bg_agent_touch(C, later - 10)  # the child works on
    assert sess.sweep_bg_agents(later, SUBAGENT_SILENCE_SECONDS) == []
    assert sess.agent_is_waiting(P)


def test_a_parent_that_is_not_waiting_is_still_swept(mk_bot):
    bot = mk_bot()
    now = 5000.0
    sess = _waiting(bot, now)
    assert sess.agent_resumed(P)
    later = now + SUBAGENT_SILENCE_SECONDS + 60
    sess.bg_agent_touch(C, later - 10)
    assert sess.sweep_bg_agents(later, SUBAGENT_SILENCE_SECONDS) == [P]


def test_an_unresumed_parent_leaves_after_the_deadline_as_shown(mk_bot):
    bot = mk_bot()
    now = 5000.0
    sess = _waiting(bot, now)
    sess.active_subagents[P] = {"type": "pipeline-runner", "started_at": now}
    rec = _line(sess, P, C)
    sess.bg_agent_stopped(C, now + 10)
    assert sess.sweep_unresumed_parents(now + 10) == []  # stamps
    assert sess.agent_shielded(P, now + 10 + WAITING_PARENT_RESUME_SECONDS)
    assert sess.sweep_unresumed_parents(
        now + 10 + WAITING_PARENT_RESUME_SECONDS) == []
    assert sess.sweep_unresumed_parents(
        now + 11 + WAITING_PARENT_RESUME_SECONDS) == [P]
    assert P not in sess.bg_agents and P not in sess.active_subagents
    assert P not in sess.bg_agents_recent
    # Left as last shown: the block is no longer kept current, never
    # stamped done.
    assert rec not in sess.agents_lines
    assert P not in rec["ends"]


def test_a_child_running_again_resets_the_deadline(mk_bot):
    bot = mk_bot()
    now = 5000.0
    sess = _waiting(bot, now)
    sess.bg_agent_stopped(C, now + 1)
    sess.sweep_unresumed_parents(now + 1)
    assert sess.bg_agents[P]["children_gone_at"] == now + 1
    # A child of its own running again (a finished child's link is gone
    # with it, so it is linked anew).
    assert sess.bg_agent_still_running(C, now + 2)
    sess.agent_launched_child(P, C, now + 2)
    sess.sweep_unresumed_parents(now + 2)
    assert sess.bg_agents[P]["children_gone_at"] is None
    assert sess.sweep_unresumed_parents(now + 500) == []


def test_a_resumed_parent_is_never_dropped_by_the_deadline(mk_bot, run_async):
    bot = mk_bot()
    now = 5000.0
    sess = _waiting(bot, now)
    sess.bg_agent_stopped(C, now + 1)
    sess.bg_agent_started(P, "pipeline-runner", now + 1)
    assert not sess.agent_is_waiting(P)
    sess.sweep_unresumed_parents(now + 2)
    assert sess.sweep_unresumed_parents(now + 1000) == []
    assert P in sess.bg_agents


def _scan(run_async, bot, sess, monkeypatch, sink=None):
    async def _names():
        return [sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

    async def _notify(_s, event, ctx):
        if sink is not None:
            sink.append(event)

    run_async(bb.bounded(SessionMonitor(bot.registry, _notify)._scan()))


def test_the_monitor_keeps_a_waiting_parent_in_the_job(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="mon")
    now = time.monotonic()
    old = now - SUBAGENT_SILENCE_SECONDS - 60
    sess.bg_agent_started(P, "pipeline-runner", old)
    sess.bg_agent_started(C, "ship-reviewer", now)
    sess.agent_launched_child(P, C, old)
    sess.bg_agent_pause(P, old)
    sess.add_subagent(P, {"type": "pipeline-runner", "started_at": old,
                          "last_seen": old})
    sess.add_subagent(C, {"type": "ship-reviewer", "started_at": now,
                          "last_seen": now})
    _scan(run_async, bot, sess, monkeypatch)
    assert P in sess.active_subagents
    assert P in sess.bg_agents


def test_the_monitor_drops_an_unresumed_parent_and_closes_the_job(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="lost")
    sess.status = Status.IDLE
    sess.job_interim_seen = True
    now = time.monotonic()
    sess.bg_agent_started(P, "pipeline-runner", now)
    sess.add_subagent(P, {"type": "pipeline-runner", "started_at": now})
    sess.bg_agents[P].update(waiting=True, paused=now,
                             children_gone_at=now - 200)
    sink: list = []
    _scan(run_async, bot, sess, monkeypatch, sink)
    assert P not in sess.bg_agents and P not in sess.active_subagents
    assert "job_agents_lost" in sink
    assert sess.job_interim_seen is False
