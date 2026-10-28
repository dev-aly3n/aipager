"""The fail-closed rule for Claude Code's risky-rm countdown (roadmap 8.128;
design.md criteria 9, 11, 12, 13 and 14), on the pure verdict.

A countdown candidate is Claude's own (no agent owner) Bash prompt in
``bypassPermissions`` or ``auto``, or in a mode that is not known. aipager
leaves the countdown on, so in every session no open prompt gets "yes"
while a candidate is open beside another prompt."""

from __future__ import annotations

import pytest

from aipager import prompt_stack as ps

NOW = 1000.0
#: An old enough arrival: any hook of it has given up long ago.
OLD = NOW - 500.0


def own(arrived, *, tool="Bash", mode="bypassPermissions"):
    """Claude's own prompt as the hook receiver records it."""
    return {"owner": "", "launch": ps.OWN, "arrived": arrived, "request_id": "",
            "timed_out_at": 0.0, "tool": tool, "mode": mode}


def agent(arrived, *, launch=ps.BACKGROUND, request_id="", timed_out_at=0.0,
          tool="Bash", mode=""):
    return {"owner": "a1b2c3d4e5f60718a", "launch": launch, "arrived": arrived,
            "request_id": request_id, "timed_out_at": timed_out_at,
            "tool": tool, "mode": mode}


def timed_out_bg_agent(at):
    """A background agent's prompt whose hook gave up at *at*: its dialog
    entered the terminal then."""
    return agent(at - 20.0, request_id="req-bg", timed_out_at=at)


def parked_bg_agent():
    """A background agent's prompt whose hook still waits: certainly not
    in the terminal yet."""
    return agent(NOW - 1.0, request_id="req-bg")


def verdict(target, others):
    return ps.keys_verdict(target, others, NOW)


# -- the constants -------------------------------------------------------------------------

def test_candidate_modes_are_full_access_and_auto():
    assert ps.CANDIDATE_MODES == frozenset({"bypassPermissions", "auto"})


def test_mode_unknown_is_a_value_claude_code_never_sends():
    assert ps.MODE_UNKNOWN == "?"
    assert ps.MODE_UNKNOWN not in {"default", "acceptEdits", "plan", "bypassPermissions",
                                   "auto", "dontAsk", ""}


# -- countdown_candidate --------------------------------------------------------------------

@pytest.mark.parametrize("order, expected", [
    (own(1.0, mode="bypassPermissions"), True),
    (own(1.0, mode="auto"), True),
    (own(1.0, mode=ps.MODE_UNKNOWN), True),
    (own(1.0, mode="default"), False),
    (own(1.0, mode="acceptEdits"), False),
    (own(1.0, mode="plan"), False),
    (own(1.0, mode=""), False),
    ({"owner": "", "launch": ps.OWN, "arrived": 1.0, "tool": "Bash"}, False),
    (own(1.0, tool="Write"), False),
    (own(1.0, tool="AskUserQuestion"), False),
    (own(1.0, tool=""), False),
    (agent(1.0, mode="bypassPermissions"), False),
    (agent(1.0, mode="auto"), False),
    (agent(1.0, mode=ps.MODE_UNKNOWN), False),
    (agent(1.0, launch=ps.FOREGROUND, mode="bypassPermissions"), False),
    (None, True),
    ("order", True),
    (["Bash", "auto"], True),
    (7, True),
], ids=["own-bash-bypass", "own-bash-auto", "own-bash-mode-unknown", "own-bash-default",
        "own-bash-accept-edits", "own-bash-plan", "own-bash-empty-mode",
        "own-bash-no-mode-key", "own-write-bypass", "own-question-bypass",
        "own-no-tool-bypass", "bg-agent-bash-bypass", "bg-agent-bash-auto",
        "bg-agent-bash-unknown", "fg-agent-bash-bypass", "none", "str", "list", "int"])
def test_countdown_candidate(order, expected):
    assert ps.countdown_candidate(order) is expected


class _Exploding(dict):
    def get(self, *_a, **_k):
        raise RuntimeError("unreadable")


class _BadEq:
    def __eq__(self, other):
        raise RuntimeError("no compare")

    __hash__ = object.__hash__


@pytest.mark.parametrize("order", [
    _Exploding(owner=""),
    {"owner": "", "tool": "Bash", "mode": ["auto"]},
    {"owner": "", "tool": _BadEq(), "mode": "auto"},
], ids=["get-raises", "unhashable-mode", "tool-eq-raises"])
def test_an_unreadable_order_counts_as_a_candidate(order):
    assert ps.countdown_candidate(order) is True


@pytest.mark.parametrize("order", [
    {"owner": "", "tool": 5, "mode": "auto"},
    {"owner": "", "tool": "Bash", "mode": 5},
    {"owner": "", "tool": None, "mode": None},
], ids=["int-tool", "int-mode", "none-both"])
def test_odd_types_never_raise(order):
    assert ps.countdown_candidate(order) in (True, False)


# -- criterion 9: no "yes" in an overlap --------------------------------

@pytest.mark.parametrize("mode", sorted(ps.CANDIDATE_MODES))
def test_candidate_target_beside_older_timed_out_agent_is_unknown(mode):
    rm = own(NOW - 5.0, mode=mode)
    assert verdict(rm, [timed_out_bg_agent(NOW - 10.0)]) == "unknown"


@pytest.mark.parametrize("mode", sorted(ps.CANDIDATE_MODES))
def test_older_agent_beside_candidate_is_unknown(mode):
    """The harm: Allow on the agent's card typed into the rm dialog."""
    rm = own(NOW - 5.0, mode=mode)
    assert verdict(timed_out_bg_agent(NOW - 10.0), [rm]) == "unknown"


def test_candidate_target_beside_parked_agent_is_unknown():
    """Even an other prompt certainly not in the terminal yet counts."""
    assert verdict(own(NOW - 5.0), [parked_bg_agent()]) == "unknown"


def test_parked_agent_beside_candidate_is_unknown():
    assert verdict(parked_bg_agent(), [own(NOW - 5.0)]) == "unknown"


def test_candidate_beside_question_is_unknown():
    question = own(NOW - 10.0, tool="AskUserQuestion", mode="bypassPermissions")
    assert verdict(question, [own(NOW - 5.0)]) == "unknown"
    assert verdict(own(NOW - 5.0), [question]) == "unknown"


def test_candidate_beside_foreground_agent_is_unknown():
    fg = agent(NOW - 10.0, launch=ps.FOREGROUND)
    assert verdict(fg, [own(NOW - 5.0)]) == "unknown"


def test_candidate_among_several_others_is_unknown():
    others = [timed_out_bg_agent(NOW - 30.0), own(NOW - 5.0),
              own(NOW - 2.0, tool="Write", mode="default")]
    assert verdict(own(NOW - 40.0, tool="Edit", mode="default"), others) == "unknown"


# -- criterion 12: non-candidates keep today's verdicts --------------------------------------

@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", ""],
                         ids=["default", "accept-edits", "plan", "missing"])
def test_non_candidate_mode_first_own_prompt_is_on_top(mode):
    first, second = own(NOW - 10.0, mode=mode), own(NOW - 5.0, mode=mode)
    assert verdict(first, [second]) == "yes"
    assert verdict(second, [first]) == "behind"


def test_non_bash_tool_in_bypass_keeps_arrival_order():
    first = own(NOW - 10.0, tool="Write")
    second = own(NOW - 5.0, tool="Edit")
    assert verdict(first, [second]) == "yes"
    assert verdict(second, [first]) == "behind"


def test_agents_bypass_bash_is_no_candidate():
    """An agent's dangerous rm has no countdown (live-main Q6)."""
    fg = agent(NOW - 10.0, launch=ps.FOREGROUND, mode="bypassPermissions")
    question = own(NOW - 5.0, tool="AskUserQuestion", mode="bypassPermissions")
    assert verdict(fg, [question]) == "yes"
    assert verdict(question, [fg]) == "behind"


def test_agents_bypass_bash_timed_out_beside_question():
    bg = agent(NOW - 30.0, request_id="req-bg", timed_out_at=NOW - 10.0,
               mode="bypassPermissions")
    question = own(NOW - 5.0, tool="AskUserQuestion", mode="bypassPermissions")
    assert verdict(bg, [question]) == "yes"


@pytest.mark.parametrize("mode", sorted(ps.CANDIDATE_MODES | {ps.MODE_UNKNOWN}))
def test_a_lone_candidate_is_yes(mode):
    assert verdict(own(NOW - 5.0, mode=mode), []) == "yes"


# -- criterion 13: a mode that is not known --------------------------------------------------

def test_mode_unknown_bash_beside_agent_is_unknown():
    prompt = own(NOW - 5.0, mode=ps.MODE_UNKNOWN)
    assert verdict(timed_out_bg_agent(NOW - 10.0), [prompt]) == "unknown"


def test_non_dict_order_beside_another_is_unknown():
    assert verdict(own(NOW - 10.0, mode="default"), [None]) == "unknown"


# -- criterion 14: never raises ----------------------------------------------------------------

@pytest.mark.parametrize("target, others", [
    (None, [None]),
    (_Exploding(), [own(NOW - 1.0, mode="default")]),
    (own(NOW - 1.0, mode="default"), [_Exploding()]),
    ({"owner": "", "tool": "Bash", "mode": ["x"]}, [own(1.0, mode="default")]),
    ("x", ["y"]),
], ids=["none", "target-raises", "other-raises", "unhashable-mode", "strings"])
def test_keys_verdict_never_raises_on_malformed_orders(target, others):
    assert verdict(target, others) in ("yes", "behind", "unknown")


@pytest.mark.parametrize("target, others", [
    (_Exploding(), [own(NOW - 1.0, mode="default")]),
    (own(NOW - 1.0, mode="default"), [_Exploding()]),
], ids=["target-raises", "other-raises"])
def test_an_unreadable_order_gives_unknown(target, others):
    assert verdict(target, others) == "unknown"
