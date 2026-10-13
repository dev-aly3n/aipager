"""What links a child to the agent that launched it, and what a stop then
decides (entrypoints.md "Hook datagrams": the PostToolUse row and the
SubagentStop row). Equivalence classes of the linking PostToolUse, with
the boundaries of the id pattern ``[A-Za-z0-9_-]{1,64}``.

Each test: P runs, a child runs, the PostToolUse under test arrives, P
stops. Waiting = the link was taken; done = it was ignored.
"""

from __future__ import annotations

import pytest

from tests import agents_line_waiting_bb as h

P, P_TYPE = h.P, h.P_TYPE


def _scenario(r, child, response, *, by=P, tool_name="Agent"):
    r.agent_call(P, P_TYPE)
    r.start(child, "ship-reviewer")
    r.link(child, by=by, response=response, tool_name=tool_name)
    r.stop(P, P_TYPE)


def _resp(child, **over):
    base = {"isAsync": True, "status": "async_launched", "agentId": child,
            "description": "review"}
    base.update(over)
    return base


# ---- valid classes: P waits ---------------------------------------------------------

@pytest.mark.parametrize("child", [
    "c",                                   # 1 character: the lower boundary
    "C" * 64,                              # 64 characters: the upper boundary
    "agent_child-01",                      # every allowed character class
    h.C,
])
def test_a_valid_child_id_makes_the_stop_a_pause(rig, child):
    _scenario(rig, child, _resp(child))
    assert rig.sess.agent_is_waiting(P) is True


def test_the_task_tool_links_like_the_agent_tool(rig):
    _scenario(rig, h.C, _resp(h.C), tool_name="Task")
    assert rig.sess.agent_is_waiting(P) is True


@pytest.mark.parametrize("status", ["completed", "renamed_status", ""])
def test_a_link_is_kept_whatever_the_status(rig, status):
    _scenario(rig, h.C, _resp(h.C, status=status))
    assert rig.sess.agent_is_waiting(P) is True


def test_extra_fields_in_the_response_change_nothing(rig):
    _scenario(rig, h.C, _resp(h.C, prompt="x" * 5000, outputFile="/home/alice/o"))
    assert rig.sess.agent_is_waiting(P) is True


# ---- invalid classes: today's behaviour, P is done -----------------------------------

@pytest.mark.parametrize("child,response", [
    ("C" * 65, _resp("C" * 65)),                         # one past the upper boundary
    ("bad id", _resp("bad id")),                         # a character outside the class
    ("ok", _resp("")),                                   # empty id (zero characters)
    ("ok", {"isAsync": True, "status": "async_launched"}),  # no agentId
    ("ok", _resp(12345)),                                # not a string
    ("ok", "agentId: ok"),                               # tool_response not an object
    ("ok", "ok"),                                        # the bare id as a string
    ("ok", ["ok"]),                                      # a list
    ("ok", None),                                        # null
], ids=["65-chars", "space", "empty", "missing", "number", "string", "bare-id",
     "list", "null"])
def test_an_unusable_link_leaves_the_stop_a_finish(rig, child, response):
    rig.agent_call(P, P_TYPE)
    rig.start(child, "ship-reviewer")
    rig.hook("PostToolUse", tool_name="Agent", tool_input={"prompt": "go"},
             tool_response=response, agent_id=P, agent_type=P_TYPE)
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


def test_a_self_link_is_ignored(rig):
    rig.agent_call(P, P_TYPE)
    rig.start(h.C, "ship-reviewer")
    rig.link(P, by=P)
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


@pytest.mark.parametrize("tool_name", ["Bash", "Read", "agent", ""])
def test_another_tool_never_links(rig, tool_name):
    _scenario(rig, h.C, _resp(h.C), tool_name=tool_name)
    assert P not in rig.sess.bg_agents


def test_the_main_loops_agent_call_makes_no_parent(rig):
    """A sibling launched by the main loop (no agent_id on its
    PostToolUse) is nobody's child: P's stop finishes P."""
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer")         # by the main loop
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


def test_a_link_to_a_child_that_never_started_is_no_pause(rig):
    """The child's SubagentStart was lost: P finishes (degradation table)."""
    rig.agent_call(P, P_TYPE)
    rig.link(h.C, by=P)
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


def test_a_link_to_a_child_that_already_stopped_is_no_pause(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer", by=P)
    rig.stop(h.C, "ship-reviewer")
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


# ---- background_tasks never decides ---------------------------------------------------

def test_an_empty_background_tasks_list_does_not_finish_a_linked_parent(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer", by=P)
    rig.stop(P, P_TYPE, background_tasks=[])
    assert rig.sess.agent_is_waiting(P) is True


def test_background_tasks_without_a_link_does_not_make_a_pause(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer")         # by the main loop: no link
    rig.stop(P, P_TYPE, background_tasks=[
        {"id": h.C, "type": "local_agent", "status": "running",
         "description": "review", "agent_type": "ship-reviewer"}])
    assert P not in rig.sess.bg_agents


@pytest.mark.parametrize("junk", ["running", 7, {"a": 1}, [None, 3, "x"]])
def test_a_malformed_background_tasks_field_changes_nothing(rig, junk):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer", by=P)
    rig.stop(P, P_TYPE, background_tasks=junk)
    assert rig.sess.agent_is_waiting(P) is True


# ---- a stop for an agent that never started -------------------------------------------

def test_a_stop_for_an_unstarted_id_with_a_live_linked_child_creates_nothing(rig):
    rig.agent_call(h.C, "ship-reviewer")
    rig.link(h.C, by="a0000000000000000")
    rig.stop("a0000000000000000", P_TYPE)
    assert "a0000000000000000" not in rig.sess.bg_agents


def test_a_reused_child_id_does_not_tie_the_old_parent(rig):
    """C, launched by P, finishes; later an unrelated agent starts under
    the same id (the main loop's). P's stop must not read it as P's
    child: the finished agent's link is forgotten at its finish."""
    rig.agent_call(P, P_TYPE)
    rig.agent_call(h.C, "ship-reviewer", by=P)
    rig.stop(h.C, "ship-reviewer")
    rig.start(h.C, "ship-reviewer")              # a new agent, same id
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


# ---- a link that arrives after the pause stop (degradation table) ----------------------

@pytest.fixture
def late_link(rig):
    rig.agent_call(P, P_TYPE)
    rig.start(h.C, "ship-reviewer")
    rig.stop(P, P_TYPE)                         # no link yet: a finish
    rig.link(h.C, by=P)                         # the link lands late
    return rig


def test_a_late_link_brings_the_parent_back(late_link):
    assert P in late_link.sess.bg_agents


def test_a_late_link_brings_it_back_as_waiting(late_link):
    assert late_link.sess.agent_is_waiting(P) is True


def test_a_late_link_leaves_the_job_table_finished(late_link):
    assert P not in late_link.sess.active_subagents


def test_a_late_link_to_a_stopped_child_brings_nothing_back(rig):
    rig.agent_call(P, P_TYPE)
    rig.start(h.C, "ship-reviewer")
    rig.stop(h.C, "ship-reviewer")
    rig.stop(P, P_TYPE)
    rig.link(h.C, by=P)
    assert P not in rig.sess.bg_agents
