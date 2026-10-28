"""The pure rule (design.md success criteria 9, 11, 12, 14; entrypoints.md
``prompt_stack.countdown_candidate`` and ``keys_verdict``).

A countdown candidate is a main-loop (no owner) Bash prompt whose mode is
``bypassPermissions``, ``auto`` or ``MODE_UNKNOWN``. In every session, a
candidate open together with any other prompt makes the verdict "unknown"
for every one of them. Non-candidates keep the arrival-order rule
("today's" is checked against the same places with the new keys
removed)."""

from __future__ import annotations

import pytest

from aipager import prompt_stack
from aipager.prompt_stack import countdown_candidate, keys_verdict

VERDICTS = {"yes", "behind", "unknown"}


def place(owner="", launch="own", arrived=100.0, request_id="", timed_out_at=0.0,
          tool=None, mode=None):
    p = {"owner": owner, "launch": launch, "arrived": arrived,
         "request_id": request_id, "timed_out_at": timed_out_at}
    if tool is not None:
        p["tool"] = tool
    if mode is not None:
        p["mode"] = mode
    return p


def rm(arrived=105.0, mode="bypassPermissions", **kw):
    """Claude's own Bash prompt (the risky rm)."""
    return place(arrived=arrived, tool="Bash", mode=mode, **kw)


def bg_agent(arrived=100.0, request_id="r", timed_out_at=0.0, tool="Bash",
             mode="bypassPermissions", owner="a1"):
    return place(owner=owner, launch="background", arrived=arrived, request_id=request_id,
                 timed_out_at=timed_out_at, tool=tool, mode=mode)


def fg_agent(arrived=100.0, tool="Bash", mode="bypassPermissions"):
    return place(owner="f1", launch="foreground", arrived=arrived, tool=tool, mode=mode)


def question(arrived=100.0, mode=""):
    return place(arrived=arrived, tool="AskUserQuestion", mode=mode)


def stripped(p):
    return {k: v for k, v in p.items() if k not in ("tool", "mode")}


def today(target, others, now, **kw):
    """The arrival-order verdict, with no per-prompt tool/mode facts."""
    return keys_verdict(stripped(target), [stripped(o) for o in others], now, **kw)


# -- the constants -------------------------------------------------------------------

def test_candidate_modes():
    assert prompt_stack.CANDIDATE_MODES == frozenset({"bypassPermissions", "auto"})


def test_mode_unknown_is_a_question_mark():
    assert prompt_stack.MODE_UNKNOWN == "?"


def test_mode_unknown_is_not_a_claude_code_mode():
    assert prompt_stack.MODE_UNKNOWN not in {"default", "acceptEdits", "plan",
                                             "bypassPermissions", "auto", "dontAsk", ""}


# -- countdown_candidate: equivalence classes and boundaries ---------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto", "?"])
def test_main_loop_bash_in_a_candidate_mode_is_a_candidate(mode):
    assert countdown_candidate({"owner": "", "tool": "Bash", "mode": mode}) is True


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", "dontAsk", "",
                                  "BypassPermissions", "AUTO", "auto ", " auto",
                                  "bypass", "full", "??"])
def test_main_loop_bash_in_another_mode_is_not(mode):
    assert countdown_candidate({"owner": "", "tool": "Bash", "mode": mode}) is False


def test_main_loop_bash_with_no_mode_key_is_not():
    """A PermissionRequest without permission_mode is recorded as "" (a
    non-candidate); only the Notification fallback records MODE_UNKNOWN."""
    assert countdown_candidate({"owner": "", "tool": "Bash"}) is False


@pytest.mark.parametrize("tool", ["Edit", "Write", "Read", "AskUserQuestion",
                                  "WebFetch", "bash", "BASH", "Bash ", "", "mcp__x__Bash"])
def test_another_tool_in_bypass_is_not(tool):
    assert countdown_candidate({"owner": "", "tool": tool, "mode": "bypassPermissions"}) \
        is False


def test_no_tool_key_is_not():
    assert countdown_candidate({"owner": "", "mode": "bypassPermissions"}) is False


@pytest.mark.parametrize("owner", ["a1", "x", 1, True, ["a"]],
                         ids=["agent", "short", "int", "true", "list"])
def test_a_truthy_owner_is_not(owner):
    """An agent's dangerous rm has no countdown."""
    assert countdown_candidate({"owner": owner, "tool": "Bash", "mode": "auto"}) is False


@pytest.mark.parametrize("owner", ["", None, 0, False], ids=["empty", "none", "zero",
                                                            "false"])
def test_a_falsy_owner_is_main_loop(owner):
    assert countdown_candidate({"owner": owner, "tool": "Bash", "mode": "auto"}) is True


def test_no_owner_key_is_main_loop():
    assert countdown_candidate({"tool": "Bash", "mode": "auto"}) is True


@pytest.mark.parametrize("order", [None, [], "Bash", 5, ("Bash", "auto"), object()],
                         ids=["none", "list", "str", "int", "tuple", "object"])
def test_a_non_dict_order_is_a_candidate(order):
    assert countdown_candidate(order) is True


class _Raising(dict):
    def get(self, *a, **k):
        raise RuntimeError("boom")

    def __getitem__(self, k):
        raise RuntimeError("boom")


class _BadEq:
    def __eq__(self, other):
        raise RuntimeError("boom")

    def __hash__(self):
        raise RuntimeError("boom")

    def __bool__(self):
        raise RuntimeError("boom")


def test_an_order_that_raises_on_access_is_a_candidate():
    assert countdown_candidate(_Raising(owner="", tool="Bash", mode="auto")) is True


def test_an_unhashable_mode_with_bash_is_a_candidate():
    """``{} in frozenset`` raises TypeError: any exception returns True."""
    assert countdown_candidate({"owner": "", "tool": "Bash", "mode": {}}) is True


def test_an_owner_whose_truth_raises_is_a_candidate():
    assert countdown_candidate({"owner": _BadEq(), "tool": "Bash", "mode": "auto"}) is True


def test_a_mode_that_raises_on_compare_is_a_candidate():
    assert countdown_candidate({"owner": "", "tool": "Bash", "mode": _BadEq()}) is True


@pytest.mark.parametrize("order", [
    {"owner": "", "tool": ["Bash"], "mode": "auto"},
    {"owner": "", "tool": 5, "mode": "auto"},
    {"owner": "", "tool": None, "mode": None},
    {"owner": "", "tool": "Bash", "mode": 7},
    {"owner": "", "tool": "Bash", "mode": None},
    {"owner": "", "tool": "Bash", "mode": ["auto"]},
], ids=["tool-list", "tool-int", "both-none", "mode-int", "mode-none", "mode-list"])
def test_odd_types_never_raise_and_give_a_bool(order):
    assert type(countdown_candidate(order)) is bool


# -- keys_verdict: a candidate in an overlap -------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto", "?"])
def test_candidate_target_with_an_earlier_claude_prompt_is_unknown(mode):
    assert keys_verdict(rm(105.0, mode=mode), [question(100.0)], 120.0) == "unknown"


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto", "?"])
def test_candidate_target_that_arrived_first_is_unknown(mode):
    """Today this would be "yes" (first in arrival order); a countdown
    prompt may have opened on top of the other, so not certain."""
    assert keys_verdict(rm(100.0, mode=mode), [question(105.0)], 120.0) == "unknown"


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_target_with_a_candidate_among_others_is_unknown(mode):
    assert keys_verdict(question(100.0), [rm(105.0, mode=mode)], 120.0) == "unknown"


def test_timed_out_agent_before_the_rm_is_unknown_not_yes():
    """The live harm: an older background agent's prompt whose hook gave up,
    then the rm. By arrival the agent is on top ("yes"), but the
    rm may have opened over it, so its Allow must not type Enter."""
    agent = bg_agent(100.0, timed_out_at=104.0)
    assert keys_verdict(agent, [rm(105.0)], 120.0) == "unknown"


def test_rm_behind_a_timed_out_agent_is_unknown_not_behind():
    agent = bg_agent(100.0, timed_out_at=104.0)
    assert keys_verdict(rm(105.0), [agent], 120.0) == "unknown"


def test_candidate_beside_a_parked_background_agent_is_unknown():
    """The other's dialog is certainly not in the terminal (its hook still
    waits): the rule counts it anyway, before that filter."""
    parked = bg_agent(100.0, request_id="r")
    assert keys_verdict(rm(105.0), [parked], 110.0) == "unknown"


def test_parked_background_agent_with_a_candidate_other_is_not_yes():
    parked = bg_agent(100.0, request_id="r")
    assert keys_verdict(parked, [rm(105.0)], 110.0) != "yes"


def test_candidate_beside_a_foreground_agent_is_unknown():
    assert keys_verdict(rm(105.0), [fg_agent(100.0)], 120.0) == "unknown"


def test_foreground_agent_beside_a_later_candidate_is_unknown():
    assert keys_verdict(fg_agent(100.0), [rm(105.0)], 120.0) == "unknown"


def test_candidate_beside_a_question_is_unknown():
    assert keys_verdict(rm(105.0, mode="auto"), [question(100.0, mode="auto")],
                        120.0) == "unknown"


def test_candidate_last_among_several_others_is_unknown():
    others = [question(100.0), fg_agent(101.0), rm(110.0)]
    assert keys_verdict(question(99.0), others, 120.0) == "unknown"


def test_two_candidates_are_both_unknown():
    assert keys_verdict(rm(100.0), [rm(105.0, mode="auto")], 120.0) == "unknown"


# -- keys_verdict: lone candidate --------------------------------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto", "?"])
def test_a_lone_candidate_is_yes(mode):
    assert keys_verdict(rm(105.0, mode=mode), [], 120.0) == "yes"


# -- keys_verdict: non-candidates keep today -----------------------------------------

NON_CANDIDATES = {
    "default": rm(105.0, mode="default"),
    "acceptEdits": rm(105.0, mode="acceptEdits"),
    "plan": rm(105.0, mode="plan"),
    "empty-mode": rm(105.0, mode=""),
    "missing-mode": place(arrived=105.0, tool="Bash"),
    "edit-in-bypass": place(arrived=105.0, tool="Edit", mode="bypassPermissions"),
    "write-in-auto": place(arrived=105.0, tool="Write", mode="auto"),
    "agent-bash-bypass": bg_agent(105.0, request_id="r2", timed_out_at=106.0, owner="a2"),
    "fg-agent-bash-auto": place(owner="f2", launch="foreground", arrived=105.0,
                                tool="Bash", mode="auto"),
}


@pytest.mark.parametrize("name", NON_CANDIDATES)
def test_a_non_candidate_as_later_prompt_keeps_today(name):
    agent = bg_agent(100.0, timed_out_at=104.0, mode="default")
    target, others = agent, [NON_CANDIDATES[name]]
    assert keys_verdict(target, others, 120.0) == today(target, others, 120.0)


@pytest.mark.parametrize("name", NON_CANDIDATES)
def test_a_non_candidate_target_keeps_today(name):
    agent = bg_agent(100.0, timed_out_at=104.0, mode="default")
    target, others = NON_CANDIDATES[name], [agent]
    assert keys_verdict(target, others, 120.0) == today(target, others, 120.0)


def test_agent_bypass_bash_first_stays_yes():
    """A concrete row: an agent's Bash in bypass that timed out before a
    later default-mode Claude Bash is still "yes"."""
    agent = bg_agent(100.0, timed_out_at=104.0, mode="bypassPermissions")
    assert keys_verdict(agent, [rm(105.0, mode="default")], 120.0) == "yes"


def test_default_bash_behind_an_agent_stays_behind():
    agent = bg_agent(100.0, timed_out_at=104.0)
    assert keys_verdict(rm(105.0, mode="default"), [agent], 120.0) == "behind"


# -- criterion 14: never raises -----------------------------------------------------------------

BAD = [None, [], "x", 5, _Raising(owner="", tool="Bash", mode="auto"),
       {"owner": "", "tool": "Bash", "mode": {}}, {"arrived": "soon", "tool": "Bash"},
       {"owner": _BadEq()}]
BAD_IDS = ["none", "list", "str", "int", "raising", "unhashable-mode", "odd-arrived",
           "bad-owner"]


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_a_bad_target_never_raises(bad):
    assert keys_verdict(bad, [question(100.0)], 120.0) in VERDICTS


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_a_bad_other_never_raises(bad):
    assert keys_verdict(question(100.0), [bad], 120.0) in VERDICTS


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_a_bad_target_is_unknown(bad):
    assert keys_verdict(bad, [question(100.0)], 120.0) == "unknown"


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_a_bad_other_is_unknown(bad):
    assert keys_verdict(question(100.0), [bad], 120.0) == "unknown"


@pytest.mark.parametrize("now", [None, "later", float("nan")], ids=["none", "str", "nan"])
def test_an_odd_now_never_raises(now):
    assert keys_verdict(rm(105.0), [question(100.0)], now) in VERDICTS
