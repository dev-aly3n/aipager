"""Every Agent call remembers who made it (agents tree, 2026-10-14): a
SubagentStart is linked to its launcher at once when that is exact, and
never takes a description or a parent that may be another's.

State level for each row of the match table (design.md, Chosen approach
5), then the hook receiver recording an agent's own call."""

from __future__ import annotations

import pytest

from aipager.state import (
    AGENT_CALL_MAX_AGE,
    AGENT_CALLS_CAP,
    Status,
    TrackedSession,
)
from tests import answer_status_bb as bb

P, Q, C, D = "a505c081ca3926ed5", "a9e0d1c2b3a4f5e61", "ac1d54c85d78f59ea", "ad2e3f4a5b6c7d8e9"
T = "ship-tester"


def _sess() -> TrackedSession:
    return TrackedSession(name="claude-frontend", label="frontend", status=Status.BUSY)


def _call(sess, launcher, desc="", at=100.0, agent_type=T):
    sess.agent_call_launched(agent_type, desc, at, launcher=launcher)


def _running(sess, *ids):
    for agent_id in ids:
        sess.bg_agent_started(agent_id, "pipeline-runner", 99.0)


# ---- the match table -----------------------------------------------------------------

def test_no_call_waiting_gives_nothing():
    sess = _sess()
    assert sess.bg_agent_started(C, T, 101.0) is True
    assert sess.bg_agents[C]["desc"] == ""
    assert C not in sess.agent_parents


def test_one_call_of_the_session_gives_its_description_and_no_parent():
    sess = _sess()
    _call(sess, "", "main work")
    sess.bg_agent_started(C, T, 101.0)
    assert sess.bg_agents[C]["desc"] == "main work"
    assert C not in sess.agent_parents
    assert sess.pending_agent_calls == []


def test_one_call_of_an_agent_links_the_child_to_it():
    sess = _sess()
    _running(sess, P)
    _call(sess, P, "helper work")
    sess.bg_agent_started(C, T, 101.0)
    assert sess.agent_parents[C] == P
    assert sess.bg_agents[C]["desc"] == "helper work"
    assert sess.pending_agent_calls == []


def test_two_calls_of_one_agent_link_both_children_without_a_description():
    sess = _sess()
    _running(sess, P)
    _call(sess, P, "first", at=100.0)
    _call(sess, P, "second", at=100.5)
    sess.bg_agent_started(C, T, 101.0)
    assert (sess.agent_parents.get(C), sess.bg_agents[C]["desc"]) == (P, "")
    # The oldest went; the other stays, its description blanked.
    assert sess.pending_agent_calls == [
        {"type": T, "desc": "", "at": 100.5, "launcher": P}]
    sess.bg_agent_started(D, T, 101.5)
    assert (sess.agent_parents.get(D), sess.bg_agents[D]["desc"]) == (P, "")
    assert sess.pending_agent_calls == []


def test_two_calls_of_the_session_give_no_description_to_either():
    sess = _sess()
    _call(sess, "", "first", at=100.0)
    _call(sess, "", "second", at=100.5)
    sess.bg_agent_started(C, T, 101.0)
    sess.bg_agent_started(D, T, 101.5)
    assert (sess.bg_agents[C]["desc"], sess.bg_agents[D]["desc"]) == ("", "")
    assert sess.agent_parents == {}


@pytest.mark.parametrize("launchers", [("", P), (P, ""), (P, Q)],
                         ids=["main-then-agent", "agent-then-main", "two-agents"])
def test_calls_of_different_launchers_give_nothing_and_all_go(launchers):
    sess = _sess()
    _running(sess, P, Q)
    for i, launcher in enumerate(launchers):
        _call(sess, launcher, f"work {i}", at=100.0 + i / 10)
    _call(sess, P, "other type", agent_type="Explore")
    sess.bg_agent_started(C, T, 101.0)
    assert sess.bg_agents[C]["desc"] == ""
    assert C not in sess.agent_parents
    # Every call of the type went; another type's stays.
    assert [c["type"] for c in sess.pending_agent_calls] == ["Explore"]


def test_an_agents_call_never_gives_its_child_the_sessions_description():
    """The mis-match this replaces: a helper of the type took the session's
    own call's description."""
    sess = _sess()
    _running(sess, P)
    _call(sess, "", "main work", at=100.0)
    _call(sess, P, "helper work", at=100.5)
    sess.bg_agent_started(C, T, 101.0)
    assert sess.bg_agents[C]["desc"] != "main work"


def test_a_call_past_the_max_age_is_ignored():
    sess = _sess()
    _running(sess, P)
    _call(sess, P, "stale", at=100.0)
    sess.bg_agent_started(C, T, 100.0 + AGENT_CALL_MAX_AGE + 1)
    assert sess.bg_agents[C]["desc"] == ""
    assert C not in sess.agent_parents


def test_a_resumed_agent_takes_no_call():
    sess = _sess()
    _running(sess, P)
    sess.bg_agent_started(C, T, 100.0)
    _call(sess, P, "for someone else", at=100.5)
    sess.bg_agent_started(C, T, 101.0)
    assert C not in sess.agent_parents
    assert len(sess.pending_agent_calls) == 1


def test_an_agent_back_after_its_stop_takes_no_call_and_keeps_its_description():
    """A finished agent resumed by SendMessage starts again with no Agent
    call of its own: it must not take another agent's waiting call (that
    would link it under that agent and leave the real child unlinked)."""
    sess = _sess()
    _running(sess, P)
    _call(sess, "", "its own work", at=99.5)
    sess.bg_agent_started(C, T, 100.0)
    assert sess.bg_agents[C]["desc"] == "its own work"
    sess.bg_agent_stopped(C, 100.2)
    assert C in sess.bg_agents_recent
    _call(sess, P, "helper work", at=100.5)
    sess.bg_agent_started(C, T, 101.0)
    assert C not in sess.agent_parents
    assert sess.bg_agents[C]["desc"] == "its own work"
    assert [(c["launcher"], c["desc"]) for c in sess.pending_agent_calls] == [
        (P, "helper work")]
    # P's real child still finds its call.
    sess.bg_agent_started(D, T, 101.5)
    assert sess.agent_parents[D] == P
    assert sess.bg_agents[D]["desc"] == "helper work"


def test_a_stopped_agents_calls_go_with_it():
    sess = _sess()
    _running(sess, P, Q)
    _call(sess, P, "p's", at=100.0)
    _call(sess, Q, "q's", at=100.0, agent_type="Explore")
    sess.bg_agent_stopped(P, 101.0)
    assert [c["launcher"] for c in sess.pending_agent_calls] == [Q]
    sess.bg_agent_started(C, T, 102.0)
    assert C not in sess.agent_parents


def test_the_calls_cap_holds_subagent_calls_too():
    assert AGENT_CALLS_CAP == 40
    sess = _sess()
    for i in range(AGENT_CALLS_CAP + 5):
        _call(sess, P, f"call {i}", at=100.0 + i / 100, agent_type=f"t{i}")
    assert len(sess.pending_agent_calls) == AGENT_CALLS_CAP
    assert sess.pending_agent_calls[0]["desc"] == "call 5"


def test_a_call_with_no_launcher_given_is_the_sessions():
    sess = _sess()
    sess.agent_call_launched(T, "main work", 100.0)
    assert sess.pending_agent_calls[0]["launcher"] == ""


# ---- the hook receiver ---------------------------------------------------------------

def _hook_sess(mk_bot):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="frontend")
    return bot, sess, bb.receiver(bot.registry)


def _pre(run_async, recv, sess, tool_input, agent_id="", tool_name="Agent"):
    own = {"agent_id": agent_id, "agent_type": "pipeline-runner"} if agent_id else {}
    bb.hook(run_async, recv, sess, "PreToolUse", tool_name=tool_name,
            tool_input=tool_input, **own)


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_an_agents_own_agent_call_is_recorded_with_it_as_launcher(mk_bot, run_async,
                                                                   tool_name):
    _bot, sess, recv = _hook_sess(mk_bot)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=P, agent_type="pipeline-runner")
    _pre(run_async, recv, sess, {"subagent_type": T, "description": "Write the tests"},
         agent_id=P, tool_name=tool_name)
    assert [(c["type"], c["desc"], c["launcher"]) for c in sess.pending_agent_calls] == [
        (T, "Write the tests", P)]


def test_the_sessions_own_agent_call_has_no_launcher(mk_bot, run_async):
    _bot, sess, recv = _hook_sess(mk_bot)
    _pre(run_async, recv, sess, {"subagent_type": T, "description": "Main work"})
    assert [(c["type"], c["launcher"]) for c in sess.pending_agent_calls] == [(T, "")]


def test_an_agents_other_tool_records_no_call(mk_bot, run_async):
    _bot, sess, recv = _hook_sess(mk_bot)
    _pre(run_async, recv, sess, {"file_path": "/home/alice/a.py"}, agent_id=P,
         tool_name="Read")
    _pre(run_async, recv, sess, "not a dict", agent_id=P)
    assert sess.pending_agent_calls == []


@pytest.mark.parametrize("call_type,start_type", [
    pytest.param("ship  tester", "ship \n\t tester", id="inner-whitespace"),
    pytest.param("x" * 90, "x" * 85, id="over-80-chars"),
])
def test_an_agents_call_type_is_normalised_like_the_start(mk_bot, run_async,
                                                          call_type, start_type):
    """Both sides go through the same clean-up, so they always match. The
    two types differ, and only that clean-up (not the state's own strip)
    makes them one, so a raw value on either side breaks the link."""
    _bot, sess, recv = _hook_sess(mk_bot)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=P, agent_type="pipeline-runner")
    _pre(run_async, recv, sess, {"subagent_type": call_type, "description": "x"},
         agent_id=P)
    assert call_type.strip() != start_type.strip()
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=C, agent_type=start_type)
    assert sess.agent_parents.get(C) == P


def test_a_helper_started_by_hooks_sits_under_its_launcher_at_once(mk_bot, run_async):
    _bot, sess, recv = _hook_sess(mk_bot)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=P, agent_type="pipeline-runner")
    _pre(run_async, recv, sess, {"subagent_type": T, "description": "Write the tests"},
         agent_id=P)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id=C, agent_type=T)
    assert sess.agent_parents.get(C) == P
    assert sess.bg_agents[C]["desc"] == "Write the tests"
