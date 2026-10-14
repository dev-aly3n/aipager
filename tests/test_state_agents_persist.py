"""The running agents survive a daemon restart (agents-line-waiting design.md
5): the "agents" record save() writes and load() restores, and the startup
adoption that puts the job's agents back on a still-running card."""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager.state import (
    AGENTS_RESTORE_MAX_GAP_SECONDS, SessionRegistry, Status,
)

P, C = "a505c081ca3926ed5", "ac1d54c85d78f59ea"
NAME = "claude-frontend"
# Ages that fit this boot's clock: on a freshly booted runner a start older
# than the clock is (rightly) restarted at the load, which test_agent_tree
# covers on its own.
AGE_P = min(600.0, time.monotonic() / 2)
AGE_C = AGE_P / 20


def _running_tree(reg: SessionRegistry, *, age_p=AGE_P, age_c=AGE_C):
    """P waiting for C, both in the job's table, as before the restart."""
    sess = reg.get_or_create(NAME)
    sess.label = "frontend"
    now = time.monotonic()
    sess.bg_agent_started(P, "pipeline-runner", now - age_p)
    sess.bg_agents[P]["started"] = now - age_p
    sess.bg_agent_prose(P, "m1", "Waiting for the reviewer.", now)
    sess.bg_agent_tool(P, "Agent", "ship-reviewer", now)
    sess.bg_agent_started(C, "ship-reviewer", now - age_c)
    sess.agent_launched_child(P, C, now)
    assert sess.bg_agent_pause(P, now) == [C]
    sess.add_subagent(P, {"type": "pipeline-runner",
                          "started_at": now - age_p, "tool_count": 4,
                          "activity": "Agent: ship-reviewer"})
    sess.add_subagent(C, {"type": "ship-reviewer", "started_at": now - age_c})
    return sess


def _reload() -> SessionRegistry:
    again = SessionRegistry()
    again.load()
    return again


def test_a_round_trip_keeps_the_running_set_links_and_clocks(tmp_state_file):
    reg = SessionRegistry()
    _running_tree(reg)
    reg.save()
    loaded = _reload().get(NAME)
    now = time.monotonic()
    assert loaded.status is Status.UNKNOWN
    assert sorted(loaded.bg_agent_labels()) == ["pipeline-runner",
                                                "ship-reviewer"]
    assert loaded.agent_is_waiting(P) and not loaded.agent_is_waiting(C)
    assert loaded.agent_parents == {C: P}
    assert loaded.agent_children_running(P) == [C]
    assert abs((now - loaded.bg_agents[P]["started"]) - AGE_P) < 3
    assert abs((now - loaded.bg_agents[C]["started"]) - AGE_C) < 3
    # One honest silence window from the load.
    assert now - loaded.bg_agents[P]["last_seen"] < 3
    assert loaded.bg_agents[P]["prose"] == "Waiting for the reviewer."
    assert loaded.bg_agents[P]["tool_arg"] == "ship-reviewer"
    # The job's table waits for the adoption: never straight in.
    assert loaded.active_subagents == {}
    job = {e["id"]: e for e in loaded.restored_job_agents}
    assert set(job) == {P, C}
    assert job[P]["tool_count"] == 4
    assert job[P]["activity"] == "Agent: ship-reviewer"
    assert abs((now - job[P]["started"]) - AGE_P) < 3


def test_the_record_logs_what_it_restored(tmp_state_file, caplog):
    reg = SessionRegistry()
    _running_tree(reg)
    reg.save()
    with caplog.at_level(logging.INFO, logger="aipager.state"):
        _reload()
    assert any("[frontend] restored 2 running agents (1 waiting)" in m
               for m in caplog.messages)


def test_a_gone_session_restores_no_agents(tmp_state_file):
    reg = SessionRegistry()
    sess = _running_tree(reg)
    sess.gone_at = time.time() - 5
    reg.save()
    assert "agents" in json.loads(tmp_state_file.read_text())[
        "sessions"][NAME]
    loaded = _reload().get(NAME)
    assert loaded.status is Status.GONE
    assert loaded.bg_agents == {}
    assert loaded.agent_parents == {}
    assert loaded.restored_job_agents == []


def test_a_session_with_no_agents_writes_no_record(tmp_state_file):
    reg = SessionRegistry()
    reg.get_or_create(NAME)
    reg.save()
    assert "agents" not in json.loads(tmp_state_file.read_text())[
        "sessions"][NAME]


def test_a_record_saved_too_long_ago_restores_nothing(tmp_state_file, caplog):
    reg = SessionRegistry()
    _running_tree(reg)
    reg.save()
    data = json.loads(tmp_state_file.read_text())
    data["sessions"][NAME]["agents"]["saved_wall"] = (
        time.time() - AGENTS_RESTORE_MAX_GAP_SECONDS - 60)
    tmp_state_file.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING, logger="aipager.state"):
        loaded = _reload().get(NAME)
    assert loaded.bg_agents == {} and loaded.restored_job_agents == []
    assert any("saved running agents ignored" in m for m in caplog.messages)


@pytest.mark.parametrize("bad", [
    "agents", [1, 2], {"v": 1, "saved_wall": "x"},
    {"v": 1, "saved_wall": 1.0e12, "running": [], "job": []},
], ids=["str", "list", "bad-wall", "future"])
def test_a_malformed_record_never_stops_the_load(tmp_state_file, bad):
    reg = SessionRegistry()
    _running_tree(reg)
    reg.get_or_create("claude-other")
    reg.save()
    data = json.loads(tmp_state_file.read_text())
    data["sessions"][NAME]["agents"] = bad
    tmp_state_file.write_text(json.dumps(data))
    again = _reload()
    assert again.get(NAME).bg_agents == {}
    assert again.get("claude-other") is not None


def test_the_record_uses_the_clocks_it_is_given():
    reg = SessionRegistry()
    sess = reg.get_or_create(NAME)
    sess.bg_agent_started(P, "pipeline-runner", 400.0)
    sess.add_subagent(P, {"type": "pipeline-runner", "started_at": 300.0})
    rec = sess.agents_record(1000.0, 2_000_000.0)
    assert rec["saved_wall"] == 2_000_000.0
    assert rec["running"][0]["started_wall"] == 2_000_000.0 - 600.0
    assert rec["job"][0]["started_wall"] == 2_000_000.0 - 700.0
    assert sess.agents_record(1000.0, 2_000_000.0) is not None
    sess.bg_agents.clear()
    sess.active_subagents.clear()
    assert sess.agents_record(1000.0, 2_000_000.0) is None


def test_a_session_end_clears_links_and_restored_job_agents():
    reg = SessionRegistry()
    sess = _running_tree(reg)
    sess.restored_job_agents = [{"id": P}]
    sess.status = Status.IDLE
    reg.transition(NAME, Status.GONE)
    assert sess.agent_parents == {} and sess.restored_job_agents == []


# ── the startup adoption ─────────────────────────────────────────────────────

def _restarted(mk_bot, tmp_state_file, monkeypatch, *, card=True):
    reg = SessionRegistry()
    sess = _running_tree(reg)
    if card:
        sess.busy_msg_id = 6787
    reg.save()
    again = _reload()
    bot = mk_bot(again)
    loaded = again.get(NAME)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[NAME]))
    bot._resume_animation_if_dead = lambda *_a, **_kw: None
    return bot, loaded


def test_an_adopted_card_gets_the_job_agents_back(
        mk_bot, run_async, tmp_state_file, monkeypatch, caplog):
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    seq = sess.turn_seq
    with caplog.at_level(logging.INFO, logger="aipager.bot.lifecycle"):
        run_async(bot.recover_sessions())
    assert sess.status is Status.BUSY
    # The transition still saw no job: a turn of its own.
    assert sess.turn_seq == seq + 1
    assert set(sess.active_subagents) == {P, C}
    now = time.monotonic()
    assert abs((now - sess.active_subagents[P]["started_at"]) - AGE_P) < 3
    assert now - sess.active_subagents[P]["last_seen"] < 3
    rows = [sess.tool_history[sess.active_subagents[a]["history_idx"]]
            for a in (P, C)]
    # The restored link names the agent that started C (2026-10-14).
    assert rows == [("\U0001f916 pipeline-runner", False),
                    ("\U0001f916 pipeline-runner › ship-reviewer", False)]
    assert sess.subagent_count_this_turn == 2
    assert sess.restored_job_agents == []
    assert sess.job_background_open()
    assert any("adopted card: 2 agents of the job back on it" in m
               for m in caplog.messages)


def test_a_restored_agents_stop_settles_its_row_with_real_time(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    from tests import answer_status_bb as bb

    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    run_async(bot.recover_sessions())
    sink: list = []
    recv = bb.receiver(bot.registry, sink)
    bb.hook(run_async, recv, sess, "SubagentStop", agent_id=C,
            agent_type="ship-reviewer")
    stop = [ctx for e, ctx in sink if e == "subagent_stop"]
    assert stop and stop[0]["elapsed"] > AGE_C - 3
    assert stop[0]["history_idx"] is not None
    # P waits no more on a running child: its own stop is its finish.
    assert sess.agent_children_running(P) == []


def test_a_card_not_adopted_reopens_no_job(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: False
    bot._recover_busy_message = AsyncMock(return_value="closed")
    run_async(bot.recover_sessions())
    assert sess.restored_job_agents == []
    assert sess.active_subagents == {}
    # Its running agents stay on the line and the pin.
    assert sorted(sess.bg_agent_labels()) == ["pipeline-runner",
                                              "ship-reviewer"]
    sess.status = Status.IDLE
    assert not sess.job_background_open()


def test_a_session_with_no_card_reopens_no_job(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch, card=False)
    run_async(bot.recover_sessions())
    assert sess.restored_job_agents == []
    assert sess.active_subagents == {}


def test_an_adoption_that_leaves_the_session_waiting_installs_nothing(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    """A hook before the recovery left it on a permission prompt: its card
    is the prompt's, and gets no rows painted onto it."""
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    sess.status = Status.INTERACTIVE
    run_async(bot.recover_sessions())
    assert sess.status is Status.INTERACTIVE
    assert sess.active_subagents == {} and sess.tool_history == []
    assert sess.restored_job_agents == []


def test_an_agent_already_back_in_the_job_keeps_its_entry(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    """Its SubagentStart came in before the recovery: no second row."""
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    mine = {"type": "pipeline-runner", "started_at": time.monotonic(),
            "history_idx": None}
    sess.add_subagent(P, mine)
    run_async(bot.recover_sessions())
    assert sess.active_subagents[P] is mine
    assert sess.tool_history == [("\U0001f916 pipeline-runner › ship-reviewer", False)]
    assert sess.subagent_count_this_turn == 1


def test_an_agent_that_stopped_before_the_adoption_is_not_put_back(
        mk_bot, run_async, tmp_state_file, monkeypatch, caplog):
    """The hook receiver runs before the recovery: C's SubagentStop lands
    while recover_sessions lists the sessions. C is done, so the adoption
    puts back only P (still waiting), never a running row for C."""
    from tests import answer_status_bb as bb

    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    recv = bb.receiver(bot.registry, [])

    async def _list_sessions():
        recv._recent_fingerprints.clear()
        await recv._on_datagram(json.dumps({
            "session": NAME, "hook_event_name": "SubagentStop",
            "transcript_path": "", "agent_id": C,
            "agent_type": "ship-reviewer"}).encode())
        return [NAME]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _list_sessions)
    with caplog.at_level(logging.INFO, logger="aipager.bot.lifecycle"):
        run_async(bot.recover_sessions())
    assert sess.status is Status.BUSY
    assert C not in sess.bg_agents and C in sess.bg_agents_recent
    assert list(sess.active_subagents) == [P]
    assert sess.tool_history == [("\U0001f916 pipeline-runner", False)]
    assert sess.subagent_count_this_turn == 1
    assert sess.job_background_open()
    assert any("adopted card: 1 agents of the job back on it" in m
               for m in caplog.messages)


def test_an_agent_evicted_at_the_cap_gets_no_row(
        mk_bot, run_async, tmp_state_file, monkeypatch):
    bot, sess = _restarted(mk_bot, tmp_state_file, monkeypatch)
    bot._turn_still_running = lambda _s: True
    monkeypatch.setattr("aipager.state.ACTIVE_SUBAGENTS_CAP", 1)
    newest = {"type": "Explore", "started_at": time.monotonic(),
              "history_idx": None}
    sess.add_subagent("anewest000000001", newest)
    run_async(bot.recover_sessions())
    assert list(sess.active_subagents) == ["anewest000000001"]
    assert sess.tool_history == []
    assert sess.subagent_count_this_turn == 0
