"""Linking a helper to its launcher at its SubagentStart (design.md
success criteria 7 and 8; entrypoints.md "Launcher match at
SubagentStart" and the hook datagram table).

The table is driven twice: through the public ``TrackedSession`` methods
with explicit clocks, and through hook datagrams."""

from __future__ import annotations

import time

import pytest

from aipager.state import TrackedSession

P = "a505c081ca3926ed5"     # pipeline-runner (main session's)
Q = "a77e10f00d1c2b3a9"     # r2-runner (main session's)
C = "ac1d54c85d78f59ea"     # the child being started
C2 = "ab0000000000000c2"
T = "ship-tester"


def _sess():
    s = TrackedSession(name="claude-frontend", label="frontend")
    now = time.monotonic()
    s.bg_agent_started(P, "pipeline-runner", now)
    s.bg_agent_started(Q, "r2-runner", now)
    return s, now


def _types(s):
    return [c["type"] for c in s.pending_agent_calls]


# ---- row: none --------------------------------------------------------------------------

def test_no_pending_call_no_link():
    s, now = _sess()
    s.bg_agent_started(C, T, now)
    assert C not in s.agent_parents


def test_no_pending_call_no_description():
    s, now = _sess()
    s.bg_agent_started(C, T, now)
    assert s.bg_agents[C]["desc"] == ""


def test_no_call_of_the_type_leaves_other_types_pending():
    s, now = _sess()
    s.agent_call_launched("Explore", "look", now, launcher=P)
    s.bg_agent_started(C, T, now)
    assert _types(s) == ["Explore"]


# ---- row: exactly one, main --------------------------------------------------------------

def test_one_main_call_gives_no_link():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now)
    s.bg_agent_started(C, T, now)
    assert C not in s.agent_parents


def test_one_main_call_gives_its_description():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now)
    s.bg_agent_started(C, T, now)
    assert s.bg_agents[C]["desc"] == "main work"


def test_one_main_call_is_consumed():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now)
    s.bg_agent_started(C, T, now)
    assert s.pending_agent_calls == []


def test_a_main_call_records_an_empty_launcher():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now)
    assert s.pending_agent_calls[0]["launcher"] == ""


# ---- row: exactly one, launcher P ------------------------------------------------------------

def test_one_call_by_p_links_to_p():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    assert s.agent_parents.get(C) == P


def test_one_call_by_p_gives_its_description():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    assert s.bg_agents[C]["desc"] == "helper work"


def test_one_call_by_p_is_consumed():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    assert s.pending_agent_calls == []


def test_a_call_by_p_records_its_launcher():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    assert s.pending_agent_calls[0]["launcher"] == P


# ---- row: two or more, all launcher P ---------------------------------------------------------

def _two_by_p():
    s, now = _sess()
    s.agent_call_launched(T, "first", now - 2, launcher=P)
    s.agent_call_launched(T, "second", now - 1, launcher=P)
    s.bg_agent_started(C, T, now)
    return s, now


def test_two_calls_by_p_link_to_p():
    s, _ = _two_by_p()
    assert s.agent_parents.get(C) == P


def test_two_calls_by_p_give_no_description():
    s, _ = _two_by_p()
    assert s.bg_agents[C]["desc"] == ""


def test_two_calls_by_p_leave_one_pending():
    s, _ = _two_by_p()
    assert len(s.pending_agent_calls) == 1


def test_two_calls_by_p_leave_the_rest_blank_and_by_p():
    s, _ = _two_by_p()
    (rest,) = s.pending_agent_calls
    assert (rest["launcher"], rest["desc"]) == (P, "")


def test_two_calls_by_p_both_children_link_to_p():
    s, now = _two_by_p()
    s.bg_agent_started(C2, T, now)
    assert (s.agent_parents.get(C), s.agent_parents.get(C2)) == (P, P)


def test_two_calls_by_p_second_child_never_takes_a_sibling_description():
    s, now = _two_by_p()
    s.bg_agent_started(C2, T, now)
    assert s.bg_agents[C2]["desc"] == ""


def test_three_calls_by_p_leave_two_pending():
    s, now = _sess()
    for i in range(3):
        s.agent_call_launched(T, f"d{i}", now - 3 + i, launcher=P)
    s.bg_agent_started(C, T, now)
    assert len(s.pending_agent_calls) == 2


# ---- row: launchers differ ----------------------------------------------------------------------

@pytest.mark.parametrize("launchers", [("", P), (P, ""), (P, Q), ("", P, P)],
                         ids=["main-then-p", "p-then-main", "p-and-q", "main-p-p"])
def test_mixed_launchers_give_no_link(launchers):
    s, now = _sess()
    for i, who in enumerate(launchers):
        s.agent_call_launched(T, f"d{i}", now - 5 + i, launcher=who)
    s.bg_agent_started(C, T, now)
    assert C not in s.agent_parents


@pytest.mark.parametrize("launchers", [("", P), (P, ""), (P, Q)],
                         ids=["main-then-p", "p-then-main", "p-and-q"])
def test_mixed_launchers_give_no_description(launchers):
    s, now = _sess()
    for i, who in enumerate(launchers):
        s.agent_call_launched(T, f"d{i}", now - 5 + i, launcher=who)
    s.bg_agent_started(C, T, now)
    assert s.bg_agents[C]["desc"] == ""


def test_mixed_launchers_remove_every_call_of_the_type():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now - 2)
    s.agent_call_launched(T, "helper work", now - 1, launcher=P)
    s.agent_call_launched("Explore", "look", now - 1, launcher=P)
    s.bg_agent_started(C, T, now)
    assert _types(s) == ["Explore"]


def test_after_a_mixed_start_the_next_start_of_the_type_gets_nothing():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now - 2)
    s.agent_call_launched(T, "helper work", now - 1, launcher=P)
    s.bg_agent_started(C, T, now)
    s.bg_agent_started(C2, T, now)
    assert (C2 in s.agent_parents, s.bg_agents[C2]["desc"]) == (False, "")


def test_the_postooluse_link_follows_a_mixed_start():
    s, now = _sess()
    s.agent_call_launched(T, "main work", now - 2)
    s.agent_call_launched(T, "helper work", now - 1, launcher=P)
    s.bg_agent_started(C, T, now)
    s.agent_launched_child(P, C, now)
    assert s.agent_parents.get(C) == P


def test_the_postooluse_link_overrides_a_launcher_match():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    s.agent_launched_child(Q, C, now)
    assert s.agent_parents.get(C) == Q


# ---- expiry, resume, launcher stop ----------------------------------------------------------------

@pytest.mark.parametrize("age", [601.0, 3600.0], ids=["just-past", "hour"])
def test_an_expired_call_is_ignored(age):
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now - age, launcher=P)
    s.bg_agent_started(C, T, now)
    assert (C in s.agent_parents, s.bg_agents[C]["desc"]) == (False, "")


def test_a_call_just_inside_the_window_still_links():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now - 590.0, launcher=P)
    s.bg_agent_started(C, T, now)
    assert s.agent_parents.get(C) == P


def test_an_expired_call_does_not_make_a_fresh_one_ambiguous():
    s, now = _sess()
    s.agent_call_launched(T, "old main", now - 700.0)
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    assert s.agent_parents.get(C) == P


def test_a_resumed_agent_consumes_nothing():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    s.agent_call_launched(T, "another", now, launcher=Q)
    s.bg_agent_started(C, T, now + 1)
    assert [c["desc"] for c in s.pending_agent_calls] == ["another"]


def test_a_resumed_agent_keeps_its_link():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_started(C, T, now)
    s.agent_call_launched(T, "another", now, launcher=Q)
    s.bg_agent_started(C, T, now + 1)
    assert s.agent_parents.get(C) == P


def test_a_stopped_launcher_drops_its_pending_calls():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.agent_call_launched("Explore", "look", now, launcher=Q)
    s.bg_agent_stopped(P, now + 1)
    assert [c["launcher"] for c in s.pending_agent_calls] == [Q]


def test_a_stopped_launchers_call_never_links_later():
    s, now = _sess()
    s.agent_call_launched(T, "helper work", now, launcher=P)
    s.bg_agent_stopped(P, now + 1)
    s.bg_agent_started(C, T, now + 2)
    assert C not in s.agent_parents


# ---- through hook datagrams ---------------------------------------------------------------------

def _p_running(rig):
    rig.call("pipeline-runner", "run the pipeline")
    rig.start(P, "pipeline-runner")
    return rig


def test_a_main_pretooluse_is_recorded_with_an_empty_launcher(rig):
    rig.call(T, "main work")
    assert [(c["type"], c["desc"], c["launcher"]) for c in rig.sess.pending_agent_calls] == \
        [(T, "main work", "")]


def test_a_subagent_pretooluse_is_recorded_with_its_launcher(rig):
    _p_running(rig)
    rig.call(T, "helper work", by=P)
    assert [(c["type"], c["desc"], c["launcher"]) for c in rig.sess.pending_agent_calls] == \
        [(T, "helper work", P)]


def test_a_subagent_task_pretooluse_is_recorded_too(rig):
    _p_running(rig)
    rig.call(T, "helper work", by=P, tool_name="Task")
    assert [c["launcher"] for c in rig.sess.pending_agent_calls] == [P]


def test_a_subagent_pretooluse_still_updates_its_tool(rig):
    _p_running(rig)
    before = rig.sess.bg_agents[P].get("tool")
    rig.call(T, "helper work", by=P)
    assert rig.sess.bg_agents[P].get("tool") not in (None, "", before)


def test_a_helper_links_at_its_start(rig):
    _p_running(rig)
    rig.launch(C, T, "helper work", by=P)
    assert rig.sess.agent_parents.get(C) == P


def test_a_helper_gets_its_own_description_at_its_start(rig):
    _p_running(rig)
    rig.launch(C, T, "helper work", by=P)
    assert rig.sess.bg_agents[C]["desc"] == "helper work"


def test_the_spec_example_main_and_helper_calls_of_one_type(rig):
    _p_running(rig)
    rig.call(T, "main work")
    rig.call(T, "helper work", by=P)
    rig.start(C, T)
    assert (C in rig.sess.agent_parents, rig.sess.bg_agents[C]["desc"]) == (False, "")


def test_the_spec_example_helper_and_main_calls_reversed(rig):
    _p_running(rig)
    rig.call(T, "helper work", by=P)
    rig.call(T, "main work")
    rig.start(C, T)
    assert rig.sess.bg_agents[C]["desc"] != "main work"


def test_the_spec_example_two_calls_by_p(rig):
    _p_running(rig)
    rig.call(T, "first", by=P)
    rig.call(T, "second", by=P)
    rig.start(C, T)
    rig.start(C2, T)
    s = rig.sess
    assert [(s.agent_parents.get(x), s.bg_agents[x]["desc"]) for x in (C, C2)] == \
        [(P, ""), (P, "")]


def test_a_postooluse_link_moves_an_unlinked_helper_under_p(rig):
    _p_running(rig)
    rig.call(T, "main work")
    rig.call(T, "helper work", by=P)
    rig.start(C, T)
    rig.link(C, by=P)
    assert rig.sess.agent_parents.get(C) == P


def test_a_helpers_stop_drops_its_own_unconsumed_calls(rig):
    _p_running(rig)
    rig.launch(C, T, "helper work", by=P)
    rig.call("Explore", "look around", by=C)
    rig.stop(C, T)
    assert [c["launcher"] for c in rig.sess.pending_agent_calls] == []


def test_a_foreground_helper_nests_while_it_runs(rig):
    """Spec A: a foreground helper nests under its parent while it runs,
    not only after it ends (the PostToolUse only comes at its end)."""
    _p_running(rig)
    tool_input = {"subagent_type": T, "description": "helper work", "prompt": "go"}
    rig.hook("PreToolUse", tool_name="Agent", tool_input=tool_input,
             agent_id=P, agent_type="pipeline-runner")
    rig.start(C, T)
    assert rig.sess.agent_parents.get(C) == P


@pytest.mark.parametrize("tool_input", ["a string", None, ["x"]], ids=["str", "none", "list"])
def test_a_subagent_agent_call_without_a_dict_input_records_nothing(rig, tool_input):
    _p_running(rig)
    rig.hook("PreToolUse", tool_name="Agent", tool_input=tool_input,
             agent_id=P, agent_type="pipeline-runner")
    assert rig.sess.pending_agent_calls == []


def test_a_subagent_non_agent_tool_records_no_call(rig):
    _p_running(rig)
    rig.tool(P, "Bash", "ls", post=False)
    assert rig.sess.pending_agent_calls == []
