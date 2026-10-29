"""Roadmap 8.121 F5: what Stop does, decided by ``TrackedSession.stop_mode``.

- Claude's own turn runs (BUSY), or it waits on a prompt of its own (or
  one whose tool is not known): the two Escapes (``STOP_KEYS``).
- Claude's turn is over (IDLE, or INTERACTIVE on agents' prompts only,
  entered after the turn ended) and an agent runs or asks: Claude Code's
  kill chord (``STOP_KILL_AGENTS``).
- Its turn is over and nothing but the job's grace window or shells is
  left: no keys (``STOP_END_JOB``).

``wait_from_idle`` marks an INTERACTIVE wait entered straight from IDLE;
``SessionRegistry.transition`` sets and clears it. Also covered:
``end_killed_agents`` (SubagentStop's bookkeeping for agents a kill ended)
and ``note_bg_task_ids``.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import (
    BG_TASK_IDS_SEEN_CAP,
    STOP_END_JOB,
    STOP_KEYS,
    STOP_KILL_AGENTS,
    SessionRegistry,
    Status,
    TrackedSession,
)


def _sess(status: Status = Status.IDLE) -> TrackedSession:
    return TrackedSession(name="claude-x1", label="x1", status=status)


def _agent(sess: TrackedSession, agent_id: str = "a1") -> None:
    sess.active_subagents[agent_id] = {"type": "Explore",
                                       "started_at": time.monotonic() - 70.0,
                                       "history_idx": None}


def _prompt(owner: str | None) -> dict:
    """An open prompt's answer context: *owner* the asking agent's id,
    ``""`` Claude's own, None a tool not known."""
    if owner is None:
        return {"tool_summary": "Bash"}
    info = {"name": "Bash", "input": {"command": "rm -rf build"}}
    if owner:
        info["agent_id"] = owner
    return {"tool_info": info, "wait_started_at": time.monotonic()}


def _interactive(*owners, from_idle=True, after_stop=False) -> TrackedSession:
    sess = _sess(Status.INTERACTIVE)
    sess.wait_from_idle = from_idle
    sess.resumed_after_stop = after_stop
    first, *rest = owners
    sess.pending_permission = _prompt(first)
    sess.extra_prompts = [{"perm": _prompt(o), "msg_id": 900 + i}
                          for i, o in enumerate(rest)]
    return sess


# ---- own_turn_over ---------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (Status.IDLE, True), (Status.BUSY, False), (Status.GONE, False),
    (Status.UNKNOWN, False)], ids=["idle", "busy", "gone", "unknown"])
def test_own_turn_over_by_status(status, expected):
    assert _sess(status).own_turn_over() is expected


def test_agents_prompts_entered_from_idle_mean_the_turn_is_over():
    assert _interactive("a1", "a2").own_turn_over() is True


def test_agents_prompts_carried_over_a_stop_mean_the_turn_is_over():
    assert _interactive("a1", from_idle=False, after_stop=True).own_turn_over() is True


def test_an_agents_prompt_inside_a_running_turn_is_not():
    assert _interactive("a1", from_idle=False).own_turn_over() is False


@pytest.mark.parametrize("owners", [("",), ("a1", ""), (None,), ("a1", None)],
                         ids=["own", "agent+own", "unknown", "agent+unknown"])
def test_claudes_own_or_an_unknown_prompt_means_its_turn_may_run(owners):
    assert _interactive(*owners).own_turn_over() is False
    assert _interactive(*owners).own_dialog_open() is True


def test_interactive_with_no_prompt_open_is_not_over():
    sess = _sess(Status.INTERACTIVE)
    sess.wait_from_idle = True
    assert sess.own_turn_over() is False


# ---- own_dialog_open -------------------------------------------------------------

def test_agents_prompts_are_no_dialog_of_claudes_own():
    assert _interactive("a1", "a2").own_dialog_open() is False


def test_a_terminal_dialog_is_one():
    sess = _sess()
    sess.term_dialog = {"kind": "resume"}
    assert sess.own_dialog_open() is True


def test_an_idle_session_with_none_has_none():
    assert _sess().own_dialog_open() is False


# ---- stop_mode -------------------------------------------------------------------

def test_busy_with_agents_takes_the_escapes():
    sess = _sess(Status.BUSY)
    _agent(sess)
    assert sess.stop_mode() == STOP_KEYS


def test_claudes_own_prompt_takes_the_escapes_even_with_agents():
    sess = _interactive("")
    _agent(sess)
    assert sess.stop_mode() == STOP_KEYS


def test_idle_with_job_agents_takes_the_chord():
    sess = _sess()
    _agent(sess)
    assert sess.stop_mode() == STOP_KILL_AGENTS


def test_agents_prompts_after_the_turn_take_the_chord():
    assert _interactive("a1").stop_mode() == STOP_KILL_AGENTS


def test_an_agents_prompt_in_a_running_turn_takes_the_escapes():
    sess = _interactive("a1", from_idle=False)
    _agent(sess, "a1")
    assert sess.stop_mode() == STOP_KEYS


def test_grace_window_only_ends_the_job_with_no_keys():
    sess = _sess()
    sess.job_interim_seen = True
    sess.job_grace_until = time.monotonic() + 30.0
    assert sess.can_be_stopped() is True
    assert sess.stop_mode() == STOP_END_JOB


def test_job_shells_only_end_the_job_with_no_keys():
    sess = _sess()
    sess.bg_shells["s1"] = {"label": "npm run dev", "in_job": True,
                            "started_at": time.monotonic()}
    assert sess.stop_mode() == STOP_END_JOB


# ---- wait_from_idle in transition() --------------------------------------------------

def _registry_with(status: Status) -> tuple[SessionRegistry, TrackedSession]:
    reg = SessionRegistry()
    sess = reg.get_or_create("claude-x1")
    sess.status = status
    return reg, sess


def test_idle_to_interactive_sets_it():
    reg, sess = _registry_with(Status.IDLE)
    reg.transition("claude-x1", Status.INTERACTIVE)
    assert sess.wait_from_idle is True


def test_busy_to_interactive_does_not():
    reg, sess = _registry_with(Status.BUSY)
    reg.transition("claude-x1", Status.INTERACTIVE)
    assert sess.wait_from_idle is False


@pytest.mark.parametrize("to", [Status.BUSY, Status.IDLE, Status.GONE],
                         ids=["busy", "idle", "gone"])
def test_any_other_change_clears_it(to):
    reg, sess = _registry_with(Status.IDLE)
    reg.transition("claude-x1", Status.INTERACTIVE)
    reg.transition("claude-x1", to)
    assert sess.wait_from_idle is False


# ---- end_killed_agents --------------------------------------------------------------

def test_end_killed_agents_empties_both_tables_and_marks_the_kill():
    sess = _sess()
    _agent(sess, "a1")
    now = time.monotonic()
    sess.bg_agent_started("a1", "Explore", now)
    sess.bg_agent_started("a9", "Plan", now)  # outside the job: the chord ends it too
    sess.agent_tool_calls["a1"] = {"t1": {}}
    sess.agents_lines.append({"ids": ["a1"], "ends": {}})
    ended = sess.end_killed_agents(now)
    assert sorted(i for i, _ in ended) == ["a1", "a9"]
    assert dict(ended)["a1"] is not None and dict(ended)["a9"] is None
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert sess.agent_tool_calls == {}
    assert sess.bg_agents_recent["a1"]["ended_by"] == "killed"
    assert sess.bg_agents_recent["a9"]["ended_by"] == "killed"
    assert sess.agents_lines[0]["ends"]["a1"]["outcome"] == "stopped"
    assert [f["type"] for f in sess.finished_subagents] == ["Explore"]
    assert sess.finished_subagents[0]["elapsed"] >= 69.0


def test_end_killed_agents_takes_only_the_ids_given():
    sess = _sess()
    _agent(sess, "a1")
    _agent(sess, "a2")
    ended = sess.end_killed_agents(time.monotonic(), ["a2", "nobody"], outcome="done")
    assert [i for i, _ in ended] == ["a2"]
    assert list(sess.active_subagents) == ["a1"]


def test_bg_agent_stopped_keeps_done_by_default():
    sess = _sess()
    now = time.monotonic()
    sess.bg_agent_started("a1", "Explore", now)
    sess.agents_lines.append({"ids": ["a1"], "ends": {}})
    sess.bg_agent_stopped("a1", now)
    assert sess.agents_lines[0]["ends"]["a1"]["outcome"] == "done"
    assert "ended_by" not in sess.bg_agents_recent["a1"]


# ---- note_bg_task_ids -----------------------------------------------------------------

def test_listed_ids_are_remembered_newest_last_and_capped():
    sess = _sess()
    sess.note_bg_task_ids(["a", "b"])
    sess.note_bg_task_ids(["a", "c"])
    assert sess.bg_task_ids_seen == ["b", "a", "c"]
    sess.note_bg_task_ids([f"x{i}" for i in range(BG_TASK_IDS_SEEN_CAP + 5)])
    assert len(sess.bg_task_ids_seen) == BG_TASK_IDS_SEEN_CAP
    assert sess.bg_task_ids_seen[-1] == f"x{BG_TASK_IDS_SEEN_CAP + 4}"


def test_none_of_the_new_fields_is_saved():
    from aipager import state
    names = {"wait_from_idle", "kill_expected_until", "agents_killed_seen_wall",
             "bg_task_ids_seen", "stop_gate", "kill_wake_turn"}
    assert not names & set(state.SessionRegistry._PERSIST_FIELDS)



# ---- a prompt nobody can name after Claude's turn ended (rev-iter6-002) ---------------------

@pytest.mark.parametrize("owners,from_idle,after_stop", [
    ((None,), True, False), (("a1", None), True, False), ((None,), False, True)],
    ids=["unknown-from-idle", "agent+unknown-from-idle", "unknown-after-stop"])
def test_an_unnamed_prompt_after_the_turn_never_takes_the_escapes(owners, from_idle,
                                                                   after_stop):
    sess = _interactive(*owners, from_idle=from_idle, after_stop=after_stop)
    assert sess.unnamed_prompt_after_turn() is True
    assert sess.no_escape() is True
    # Routed to the chord's path, whose own checks refuse it with no key.
    assert sess.stop_mode() == STOP_KILL_AGENTS
    assert sess.own_turn_over() is False and sess.own_dialog_open() is True


@pytest.mark.parametrize("owners,from_idle", [
    ((None,), False), (("", None), True), (("",), True)],
    ids=["unknown-in-a-running-turn", "own+unknown-from-idle", "own-from-idle"])
def test_otherwise_an_unnamed_or_own_prompt_keeps_the_escapes(owners, from_idle):
    sess = _interactive(*owners, from_idle=from_idle)
    assert sess.unnamed_prompt_after_turn() is False
    assert sess.no_escape() is False
    assert sess.stop_mode() == STOP_KEYS


def test_busy_never_counts_as_an_unnamed_wait():
    sess = _sess(Status.BUSY)
    sess.wait_from_idle = True
    sess.pending_permission = _prompt(None)
    assert sess.unnamed_prompt_after_turn() is False
    assert sess.stop_mode() == STOP_KEYS


# ---- a killed agent's row, and the newest start (rev-iter6-005, tester note) ----------------

def test_a_killed_agents_row_is_marked_stopped_with_the_given_text():
    from aipager import bg_shells
    sess = _sess()
    idx = sess.record_tool("\U0001f916 Explore", False)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": time.monotonic() - 70.0,
                                   "history_idx": idx}
    sess.end_killed_agents(time.monotonic(), row=lambda name, secs: f"{name}|{int(secs)}")
    assert sess.tool_history[idx] == ("Explore|70", bg_shells.STOPPED)


def test_a_done_end_leaves_the_row_alone():
    sess = _sess()
    idx = sess.record_tool("\U0001f916 Explore", False)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": time.monotonic(),
                                   "history_idx": idx}
    sess.end_killed_agents(time.monotonic(), outcome="done", row=lambda n, s: "x")
    assert sess.tool_history[idx] == ("\U0001f916 Explore", False)


def test_the_newest_agent_start_in_wall_time():
    sess = _sess()
    now_mono, now_wall = 1000.0, 50_000.0
    assert sess.newest_agent_start_wall(now_mono, now_wall) == 0.0
    sess.active_subagents["a1"] = {"started_at": 900.0}
    sess.bg_agents["a2"] = {"started": 990.0}
    assert sess.newest_agent_start_wall(now_mono, now_wall) == 49_990.0
    assert sess.newest_agent_start_wall(now_mono, now_wall, ["a1"]) == 49_900.0


def test_a_new_turn_from_idle_clears_the_wake_mark_and_a_prompt_does_not():
    registry = SessionRegistry()
    sess = _sess(Status.BUSY)
    registry._sessions[sess.name] = sess
    sess.kill_wake_turn = True
    registry.transition(sess.name, Status.INTERACTIVE)
    registry.transition(sess.name, Status.BUSY)
    assert sess.kill_wake_turn is True  # the same turn, answered
    sess.status = Status.IDLE
    sess.last_idle_at = 0.0
    registry.transition(sess.name, Status.BUSY)
    assert sess.kill_wake_turn is False
