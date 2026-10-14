"""A daemon restart in the middle of a wait (design.md success criteria
9-11; entrypoints.md ``SessionRegistry.save/load``, ``bot.recover_sessions``,
``restore_agents``, ``agents_record``, ``restored_job_agents``, the state
file's ``"agents"`` key and the log lines).

The registry is saved as at shutdown into the test's redirected state
file, a new registry loads it, and a new bot runs its startup recovery
with the dtach side mocked. Time is the virtual loop's; both clocks the
state file uses move with it.
"""

from __future__ import annotations

import json
import logging
import math
import re

import pytest

from aipager import state as state_mod
from aipager.state import AGENTS_RESTORE_MAX_GAP_SECONDS, Status, TrackedSession
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


@pytest.fixture
def before(rig):
    _mid_wait(rig)
    return rig


@pytest.fixture
def adopted(before, mk_bot, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        after = h.restart(before, mk_bot, monkeypatch)
    after.logs = list(caplog.messages)
    return after


def test_the_state_file_holds_the_running_agents(before):
    before.bot.registry.save()
    saved = json.loads(state_mod.SESSION_STATE_FILE.read_text())
    rec = saved["sessions"][before.sess.name]["agents"]
    assert sorted(e["id"] for e in rec["running"]) == sorted([P, C])


def test_the_state_file_holds_the_waiting_flag_and_the_link(before):
    before.bot.registry.save()
    saved = json.loads(state_mod.SESSION_STATE_FILE.read_text())
    rec = saved["sessions"][before.sess.name]["agents"]
    by_id = {e["id"]: e for e in rec["running"]}
    assert (by_id[P]["waiting"], by_id[C]["parent"]) == (True, P)


def test_a_session_with_no_agents_saves_no_agents_key(rig):
    rig.bot.registry.save()
    saved = json.loads(state_mod.SESSION_STATE_FILE.read_text())
    assert "agents" not in saved["sessions"][rig.sess.name]


# ---- after the adoption ----------------------------------------------------------------

def test_the_card_was_adopted(adopted):
    assert any("busy card" in m and "adopted" in m for m in adopted.logs), adopted.logs


def test_both_agents_are_running_again(adopted):
    assert sorted(adopted.sess.bg_agents) == sorted([P, C])


def test_the_parent_is_still_waiting(adopted):
    assert adopted.sess.agent_is_waiting(P) is True


def test_the_link_is_back(adopted):
    assert adopted.sess.agent_children_running(P) == [C]


def test_the_jobs_table_is_back(adopted):
    assert sorted(adopted.sess.active_subagents) == sorted([P, C])


def test_the_restored_job_list_is_used_up(adopted):
    assert adopted.sess.restored_job_agents == []


@pytest.mark.parametrize("agent_type, name", [
    (P_TYPE, P_TYPE),
    # The restored link names the agent that started it (2026-10-14).
    (C_TYPE, f"{P_TYPE} › {C_TYPE}"),
], ids=["parent", "child"])
def test_the_adopted_card_has_one_running_row_per_job_agent(adopted, agent_type, name):
    assert adopted.robot_rows(agent_type) == [(f"{h.ROBOT} {name}", False)]


def test_the_pin_count_includes_both(adopted):
    assert len(adopted.sess.bg_agents) == 2


def test_the_job_is_open(adopted):
    adopted.sess.status = Status.IDLE
    assert adopted.sess.job_background_open() is True


def test_the_restore_is_logged(adopted):
    assert f"[{h.LABEL}] restored 2 running agents (1 waiting)" in adopted.logs


def test_the_adoption_of_the_jobs_agents_is_logged(adopted):
    assert any(f"[{h.LABEL}] adopted card: 2 agents of the job back on it" in m
               for m in adopted.logs), adopted.logs


def test_the_adoption_is_not_a_job_preserving_transition(before, mk_bot, monkeypatch):
    """The turn counter still advances at the adoption (as today): the
    job's agents are installed only after the transition."""
    loaded = h.restart(before, mk_bot, monkeypatch, recover=False)
    seq = loaded.sess.turn_seq
    loaded.run(loaded.bot.recover_sessions())
    assert loaded.sess.turn_seq > seq


def test_the_next_answer_lists_both(adopted):
    assert h.header(adopted.answer()) == "⏳ 2 agents working"


def test_the_next_answer_shows_the_child_working(adopted):
    assert h.is_working_row(h.row_of(adopted.answer(), C_TYPE))


def test_the_next_answer_shows_the_parent_waiting(adopted):
    assert h.is_waiting_row(h.row_of(adopted.answer(), P_TYPE))


# ---- later stops close them --------------------------------------------------------

def _seconds(text):
    m = re.search(r"(\d+):(\d\d)\)?$", text)
    assert m, text
    return int(m.group(1)) * 60 + int(m.group(2))


@pytest.fixture
def c_stopped(adopted):
    adopted.answer()
    adopted.sleep(20)
    adopted.stop(C, C_TYPE)
    return adopted


def test_the_childs_stop_after_the_restart_leaves_the_running_set(c_stopped):
    assert C not in c_stopped.sess.bg_agents


def test_the_childs_card_row_settles_with_its_real_elapsed_time(c_stopped):
    """C ran 40 s before the restart, 1 s across it and 20 s after: its
    row's time counts from its real start, not from the restart."""
    (summary, done), = c_stopped.robot_rows(C_TYPE)
    assert done and _seconds(summary) >= 60, summary


def test_the_childs_done_row_on_the_answer(c_stopped):
    c_stopped.tick(31)
    assert h.is_done_row(h.row_of(c_stopped.last_block(), C_TYPE))


def test_the_parent_waits_on_after_the_childs_stop(c_stopped):
    assert P in c_stopped.sess.bg_agents


def test_the_resume_after_the_restart_adds_no_row(c_stopped):
    c_stopped.start(P, P_TYPE)
    assert len(c_stopped.robot_rows(P_TYPE)) == 1


@pytest.fixture
def all_done(c_stopped):
    c_stopped.start(P, P_TYPE)
    c_stopped.tool(P)
    c_stopped.stop(P, P_TYPE)
    return c_stopped


def test_the_parents_final_stop_after_the_restart_removes_it(all_done):
    assert all_done.sess.bg_agents == {}


def test_both_done_on_the_answer(all_done):
    all_done.tick(6)
    all_done.tick(31)
    assert all_done.last_block().startswith("✅ 2 agents done (")


def test_the_parents_card_row_settles_with_its_real_elapsed_time(all_done):
    (summary, done), = all_done.robot_rows(P_TYPE)
    assert done and _seconds(summary) >= 60, summary


# ---- the variant without an adoption -----------------------------------------------

@pytest.fixture
def not_adopted(rig, mk_bot, monkeypatch, caplog):
    """The turn ended with its interim answer before the restart (the
    transcript shows the turn complete): nothing to adopt."""
    h.append(rig.transcript, h.user_line(TURN))
    rig.prompt(TURN)
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    h.append(rig.transcript, {"type": "assistant", "message": {
        "id": "m1", "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": h.bb.ANSWER}]}})
    rig.answer()
    with caplog.at_level(logging.INFO):
        after = h.restart(rig, mk_bot, monkeypatch)
    after.logs = list(caplog.messages)
    return after


def test_without_an_adoption_nothing_was_adopted(not_adopted):
    """Control: this variant really is the one without an adoption."""
    assert not any(re.search(r"busy card \d+ adopted|adopted card", m)
                   for m in not_adopted.logs), not_adopted.logs


def test_without_an_adoption_the_running_set_is_back(not_adopted):
    assert sorted(not_adopted.sess.bg_agents) == sorted([P, C])


def test_without_an_adoption_the_parent_still_waits(not_adopted):
    assert not_adopted.sess.agent_is_waiting(P) is True


def test_without_an_adoption_no_job_table(not_adopted):
    assert not_adopted.sess.active_subagents == {}


def test_without_an_adoption_the_restored_job_list_is_dropped(not_adopted):
    assert not_adopted.sess.restored_job_agents == []


def test_without_an_adoption_no_job_is_open(not_adopted):
    assert not_adopted.sess.job_background_open() is False


def test_without_an_adoption_the_next_answer_lists_both(not_adopted):
    not_adopted.prompt("and the docs")
    assert h.header(not_adopted.answer("Docs done.")) == "⏳ 2 agents working"


def test_without_an_adoption_later_stops_close_them(not_adopted):
    not_adopted.stop(C, C_TYPE)
    not_adopted.start(P, P_TYPE)
    not_adopted.stop(P, P_TYPE)
    assert not_adopted.sess.bg_agents == {}


# ---- what is not restored ------------------------------------------------------------

@pytest.fixture
def ended(before):
    """The session's Claude ended while the daemon was down: no socket, a
    Claude session id and a transcript, so it loads as ended (GONE)."""
    import os
    before.sess.claude_session_id = "id-frontend"
    wall = before.loop.wall()
    os.utime(before.transcript, (wall, wall))   # ended just now, not aged out
    return before


def test_control_an_ended_session_loads_gone(ended, mk_bot, monkeypatch):
    after = h.restart(ended, mk_bot, monkeypatch, live=False, recover=False)
    assert after.sess.status is Status.GONE


def test_an_ended_session_loads_with_no_agents(ended, mk_bot, monkeypatch):
    """Straight after the load, before any recovery runs."""
    after = h.restart(ended, mk_bot, monkeypatch, live=False, recover=False)
    assert after.sess.bg_agents == {}


def test_an_ended_session_loads_with_no_job_agents(ended, mk_bot, monkeypatch):
    after = h.restart(ended, mk_bot, monkeypatch, live=False, recover=False)
    assert after.sess.restored_job_agents == []


def test_an_ended_session_has_no_agents_after_the_recovery(ended, mk_bot, monkeypatch):
    after = h.restart(ended, mk_bot, monkeypatch, live=False)
    assert after.sess.bg_agents == {}


def test_a_session_saved_as_ended_restores_no_agents(before, mk_bot, monkeypatch):
    """Error guess: an end stamp saved with the record (it ended just
    before the shutdown) wins even if a socket of that name is back."""
    before.sess.gone_at = before.loop.wall() - 5
    before.sess.status = Status.GONE
    after = h.restart(before, mk_bot, monkeypatch, live=True, recover=False)
    assert after.sess.bg_agents == {}


@pytest.mark.parametrize("gap,restored", [
    (AGENTS_RESTORE_MAX_GAP_SECONDS - 60, 2),       # just inside the window
    (AGENTS_RESTORE_MAX_GAP_SECONDS + 60, 0),       # just outside it
])
def test_the_restore_window_is_thirty_minutes(before, mk_bot, monkeypatch, gap, restored):
    after = h.restart(before, mk_bot, monkeypatch, gap=gap, recover=False)
    assert len(after.sess.bg_agents) == restored


def test_the_window_constant_is_thirty_minutes():
    assert AGENTS_RESTORE_MAX_GAP_SECONDS == 1800.0


def _load_with(rig, monkeypatch, mutate):
    from aipager.dtach import inject
    from aipager.state import SessionRegistry

    rig.bot.registry.save()
    data = json.loads(state_mod.SESSION_STATE_FILE.read_text())
    mutate(data["sessions"][rig.sess.name])
    state_mod.SESSION_STATE_FILE.write_text(json.dumps(data))
    monkeypatch.setattr(inject, "live_session_names", lambda: {rig.sess.name})
    reg = SessionRegistry()
    reg.load()
    return reg.get(rig.sess.name)


def _set(key, value):
    def _m(entry):
        entry["agents"][key] = value
    return _m


@pytest.mark.parametrize("mutate", [
    lambda e: e.__setitem__("agents", "garbage"),
    lambda e: e.__setitem__("agents", ["v", 1]),
    _set("v", 2),
    _set("v", "1"),
    _set("saved_wall", "yesterday"),
    _set("saved_wall", float("inf")),
    _set("saved_wall", 1e12),                       # in the future
    _set("running", "nope"),
], ids=["string", "list", "version-2", "version-str", "wall-str", "wall-inf",
        "wall-future", "running-str"])
def test_a_malformed_record_restores_nothing_and_never_raises(before, monkeypatch, mutate):
    sess = _load_with(before, monkeypatch, mutate)
    assert sess.bg_agents == {}


def test_a_malformed_record_is_warned_about(before, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _load_with(before, monkeypatch, _set("v", 2))
    assert any(r.levelno == logging.WARNING for r in caplog.records), caplog.text


def _bad_entry(field, value):
    def _m(entry):
        for e in entry["agents"]["running"]:
            if e["id"] == C:
                e[field] = value
    return _m


@pytest.mark.parametrize("field,value", [
    ("id", "bad id"), ("id", ""), ("id", "x" * 65), ("id", 7),
    ("label", ""), ("started_wall", "soon"), ("started_wall", 1e12),
    ("started_wall", float("nan")), ("waiting", "yes"),
])
def test_a_bad_entry_is_dropped_on_its_own(before, monkeypatch, field, value):
    sess = _load_with(before, monkeypatch, _bad_entry(field, value))
    assert list(sess.bg_agents) == [P]


def test_a_duplicate_entry_is_dropped(before, monkeypatch):
    def _dup(entry):
        entry["agents"]["running"].append(dict(entry["agents"]["running"][0]))
    sess = _load_with(before, monkeypatch, _dup)
    assert sorted(sess.bg_agents) == sorted([P, C])


# ---- the record API with pinned clocks ---------------------------------------------

def _record_session(now_mono=5000.0):
    s = TrackedSession(name="claude-x", label="x")
    s.bg_agent_started(P, P_TYPE, now_mono - 300)
    s.bg_agent_started(C, C_TYPE, now_mono - 100)
    return s


def test_agents_record_places_starts_on_the_wall_clock():
    s = _record_session()
    rec = s.agents_record(5000.0, 2_000_000.0)
    walls = {e["id"]: e["started_wall"] for e in rec["running"]}
    assert (walls[P], walls[C]) == (2_000_000.0 - 300, 2_000_000.0 - 100)


def test_agents_record_is_none_with_no_agents():
    assert TrackedSession(name="claude-x", label="x").agents_record(5000.0, 2e6) is None


def test_restore_agents_round_trip_keeps_each_age():
    rec = _record_session().agents_record(5000.0, 2_000_000.0)
    again = TrackedSession(name="claude-x", label="x")
    again.restore_agents(rec, 9000.0, 2_000_010.0)
    back = again.agents_record(9000.0, 2_000_010.0)
    walls = {e["id"]: e["started_wall"] for e in back["running"]}
    assert (math.isclose(walls[P], 2_000_000.0 - 300),
            math.isclose(walls[C], 2_000_000.0 - 100)) == (True, True)


def test_restore_agents_returns_running_and_waiting_counts():
    s = _record_session()
    rec = s.agents_record(5000.0, 2_000_000.0)
    for e in rec["running"]:
        e["waiting"] = e["id"] == P
    again = TrackedSession(name="claude-x", label="x")
    assert again.restore_agents(rec, 9000.0, 2_000_010.0) == (2, 1)


def test_a_low_boot_clock_restarts_the_agents_clock_at_the_restore():
    """``now_mono`` smaller than the agent's age (a machine rebooted, or a
    CI runner's young monotonic clock): the clock restarts at the restore
    rather than going negative."""
    rec = _record_session().agents_record(5000.0, 2_000_000.0)
    again = TrackedSession(name="claude-x", label="x")
    again.restore_agents(rec, 50.0, 2_000_010.0)
    back = again.agents_record(50.0, 2_000_010.0)
    assert {e["started_wall"] for e in back["running"]} == {2_000_010.0}


@pytest.mark.parametrize("raw", [None, 0, "x", [], {}, {"v": 1}, {"v": 1, "saved_wall": 2e6,
                                                                   "running": [{"id": 3}]}])
def test_restore_agents_never_raises_on_junk(raw):
    s = TrackedSession(name="claude-x", label="x")
    assert s.restore_agents(raw, 9000.0, 2_000_010.0) == (0, 0)
