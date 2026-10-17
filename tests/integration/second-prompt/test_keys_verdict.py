"""``aipager.prompt_stack.keys_verdict`` against its contract
(entrypoints.md): "yes" only when keystrokes are certain to reach the
target's dialog, "behind" when another prompt is certainly shown first,
"unknown" otherwise; "yes" whenever *others* is empty; never raises.

Claude Code keeps the earliest dialog on top. A foreground prompt enters
the terminal when it arrives; a background agent's enters only when its
parked hook gives up (``timed_out_at``), and is certainly absent until
``arrived + reply_deadline + grace`` while its hook still waits.
"""

from __future__ import annotations

import pytest

from aipager.prompt_stack import keys_verdict

VERDICTS = {"yes", "behind", "unknown"}


def place(owner="", launch="own", arrived=100.0, request_id="", timed_out_at=0.0):
    return {"owner": owner, "launch": launch, "arrived": arrived,
            "request_id": request_id, "timed_out_at": timed_out_at}


def claude(arrived):
    return place(arrived=arrived)


def agent(launch, arrived, request_id="", timed_out_at=0.0, owner="a1"):
    return place(owner=owner, launch=launch, arrived=arrived,
                 request_id=request_id, timed_out_at=timed_out_at)


# -- a single prompt: exactly today ---------------------------------------------

@pytest.mark.parametrize("target", [
    claude(100.0),
    agent("foreground", 100.0),
    agent("background", 100.0, request_id="r"),
    agent("background", 100.0),
    agent("unknown", 100.0, request_id="r"),
    agent("unknown", 100.0, timed_out_at=110.0),
], ids=["claude", "foreground", "bg-parked", "bg-no-hook", "unknown-parked",
        "unknown-timed-out"])
def test_no_others_is_always_yes(target):
    assert keys_verdict(target, [], 105.0) == "yes"


# -- main loop and foreground agents: arrival order ------------------------------

def test_earlier_claude_prompt_puts_target_behind():
    assert keys_verdict(claude(105.0), [claude(100.0)], 120.0) == "behind"


def test_target_that_arrived_first_is_on_top():
    assert keys_verdict(claude(100.0), [claude(105.0)], 120.0) == "yes"


def test_earlier_foreground_agent_puts_claude_behind():
    assert keys_verdict(claude(105.0), [agent("foreground", 100.0)], 120.0) == "behind"


def test_foreground_agent_target_first_is_on_top():
    assert keys_verdict(agent("foreground", 100.0), [claude(105.0)], 120.0) == "yes"


def test_same_arrival_instant_is_not_yes():
    """Boundary: equal arrival stamps give no order, so keys are not certain."""
    assert keys_verdict(claude(100.0), [claude(100.0)], 120.0) != "yes"


def test_one_behind_among_several_is_behind():
    others = [claude(110.0), claude(100.0)]
    assert keys_verdict(claude(105.0), others, 120.0) == "behind"


# -- a background agent whose hook still waits ------------------------------------

def test_parked_background_agent_is_absent_just_inside_the_deadline():
    other = agent("background", 100.0, request_id="r")
    assert keys_verdict(claude(105.0), [other], 129.999) == "yes"


def test_parked_background_agent_may_be_present_at_the_deadline():
    """Boundary: from arrived + 20 + 10 its dialog may have entered after
    the hook gave up with the timeout datagram lost. It entered after the
    question, though, so the question could still be on top; the rule calls
    that "unknown" (it overlaps the target's window)."""
    other = agent("background", 100.0, request_id="r")
    assert keys_verdict(claude(105.0), [other], 130.0) != "yes"


def test_parked_background_agent_window_follows_custom_deadline_inside():
    other = agent("background", 100.0, request_id="r")
    assert keys_verdict(claude(100.5), [other], 102.999,
                        reply_deadline=2.0, grace=1.0) == "yes"


def test_parked_background_agent_window_follows_custom_deadline_at_edge():
    other = agent("background", 100.0, request_id="r")
    assert keys_verdict(claude(100.5), [other], 103.0,
                        reply_deadline=2.0, grace=1.0) != "yes"


def test_background_agent_without_hook_overlapping_target_is_unknown():
    other = agent("background", 100.0)
    assert keys_verdict(claude(105.0), [other], 106.0) == "unknown"


def test_background_agent_that_arrived_after_target_never_covers_it():
    other = agent("background", 110.0)
    assert keys_verdict(claude(105.0), [other], 200.0) == "yes"


# -- a background agent whose hook gave up -------------------------------------------

def test_background_timed_out_before_target_puts_target_behind():
    other = agent("background", 100.0, request_id="r", timed_out_at=104.0)
    assert keys_verdict(claude(105.0), [other], 120.0) == "behind"


def test_background_timed_out_after_target_leaves_target_on_top():
    other = agent("background", 100.0, request_id="r", timed_out_at=106.0)
    assert keys_verdict(claude(105.0), [other], 120.0) == "yes"


def test_background_timed_out_at_the_target_instant_is_not_yes():
    other = agent("background", 100.0, request_id="r", timed_out_at=105.0)
    assert keys_verdict(claude(105.0), [other], 120.0) != "yes"


def test_timed_out_background_target_behind_earlier_question():
    target = agent("background", 100.0, request_id="r", timed_out_at=110.0)
    assert keys_verdict(target, [claude(105.0)], 120.0) == "behind"


def test_parked_background_target_with_others_is_never_yes():
    """Its dialog is not in the terminal yet: keys would land on another."""
    target = agent("background", 100.0, request_id="r")
    assert keys_verdict(target, [claude(105.0)], 106.0) != "yes"


def test_others_all_certainly_absent_is_yes():
    others = [agent("background", 100.0, request_id="r1", owner="a1"),
              agent("background", 101.0, request_id="r2", owner="a2")]
    assert keys_verdict(claude(105.0), others, 110.0) == "yes"


# -- an agent of unknown launch kind ---------------------------------------------------

def test_unknown_timed_out_window_covering_target_is_unknown():
    other = agent("unknown", 100.0, request_id="r", timed_out_at=110.0)
    assert keys_verdict(claude(105.0), [other], 120.0) == "unknown"


def test_unknown_timed_out_before_target_is_behind():
    other = agent("unknown", 100.0, request_id="r", timed_out_at=104.0)
    assert keys_verdict(claude(105.0), [other], 120.0) == "behind"


def test_unknown_parked_agent_that_arrived_first_is_not_yes():
    """Error guess: an unknown-kind agent's dialog is either absent (it is
    background) or on screen since it arrived (it is foreground). Arrived
    before the target, it may be on top, so keys are not certain."""
    other = agent("unknown", 100.0, request_id="r")
    assert keys_verdict(claude(105.0), [other], 106.0) != "yes"


def test_unknown_parked_agent_that_arrived_later_is_yes():
    other = agent("unknown", 110.0, request_id="r")
    assert keys_verdict(claude(105.0), [other], 111.0) == "yes"


def test_unknown_agent_without_hook_overlapping_is_unknown():
    other = agent("unknown", 100.0)
    assert keys_verdict(claude(105.0), [other], 106.0) == "unknown"


def test_unknown_target_with_other_open_is_never_yes():
    target = agent("unknown", 100.0)
    assert keys_verdict(target, [claude(105.0)], 106.0) != "yes"


# -- robustness ------------------------------------------------------------------------

@pytest.mark.parametrize("target,others", [
    ({}, [{}]),
    ({"owner": "a1"}, [{"arrived": "soon"}]),
    (place(launch="sideways"), [place(arrived=99.0)]),
    (place(arrived=None), [place(timed_out_at=None)]),
], ids=["empty", "missing-fields", "odd-launch", "none-values"])
def test_malformed_places_never_raise(target, others):
    assert keys_verdict(target, others, 120.0) in VERDICTS
