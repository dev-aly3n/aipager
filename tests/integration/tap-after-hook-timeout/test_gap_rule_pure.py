"""The gap rule against its contract (entrypoints.md, "Exported
functions"): ``prompt_stack.dialog_gap``, ``gap_next_look`` and the
``"opening"`` verdict of ``keys_verdict``.

A background or unknown-launch agent's prompt is in its gap while its
hook is parked, and for ``open_grace`` (4.0 s) after the hook is gone,
shortened to ``dialog_seen_at + settle`` (2.0 s) once Claude Code's record
proved the dialog on screen. Half-open: at the end instant it is over.
"""

from __future__ import annotations

import pytest

from aipager import prompt_stack
from aipager.prompt_stack import dialog_gap, gap_next_look, keys_verdict


def order(launch="background", arrived=100.0, request_id="", timed_out_at=0.0,
          owner="a1", **extra):
    out = {"owner": owner, "launch": launch, "arrived": arrived,
           "request_id": request_id, "timed_out_at": timed_out_at}
    out.update(extra)
    return out


def own(arrived):
    return order(launch="own", arrived=arrived, owner="")


# -- constants ---------------------------------------------------------------------

def test_dialog_settle_is_two_seconds():
    assert prompt_stack.DIALOG_SETTLE == 2.0


def test_open_grace_is_four_seconds():
    assert prompt_stack.OPEN_GRACE == 4.0


def test_reply_deadline_unchanged():
    assert prompt_stack.REPLY_DEADLINE == 20.0


def test_grace_unchanged():
    assert prompt_stack.GRACE == 10.0


# -- who is gated (equivalence classes of the order) ---------------------------------

@pytest.mark.parametrize("o", [
    order(launch="background", request_id="r", timed_out_at=110.0),
    order(launch="unknown", request_id="r", timed_out_at=110.0),
], ids=["background", "unknown"])
def test_agent_launch_kinds_in_gap_are_gated(o):
    assert dialog_gap(o, 111.0) is not None


@pytest.mark.parametrize("o", [
    order(launch="own", owner="", request_id="r", timed_out_at=110.0),
    order(launch="foreground", request_id="r", timed_out_at=110.0),
    order(launch="own", request_id="r", timed_out_at=110.0),
    order(launch="background", owner="", request_id="r", timed_out_at=110.0),
    order(launch="background", owner=None, request_id="r", timed_out_at=110.0),
    order(launch="background", arrived=None, request_id="r", timed_out_at=110.0),
    order(launch="background", arrived="100", request_id="r", timed_out_at=110.0),
    order(launch="weird", request_id="r", timed_out_at=110.0),
], ids=["claude-own", "foreground", "own-with-owner", "owner-empty", "owner-none",
        "arrived-none", "arrived-str", "launch-unknown-word"])
def test_never_gated(o):
    assert dialog_gap(o, 111.0) is None


@pytest.mark.parametrize("o", [None, "order", 3, [], {}],
                         ids=["none", "str", "int", "list", "empty-dict"])
def test_non_order_is_never_gated(o):
    assert dialog_gap(o, 111.0) is None


@pytest.mark.parametrize("o", [None, "order", 3, [], {}],
                         ids=["none", "str", "int", "list", "empty-dict"])
def test_gap_next_look_of_non_order_is_none(o):
    assert gap_next_look(o, 111.0) is None


# -- gone = timed_out_at: boundary at timed_out_at + open_grace ----------------------

def test_gap_end_after_timeout_is_timeout_plus_grace():
    o = order(request_id="r", timed_out_at=110.0)
    assert dialog_gap(o, 110.0) == pytest.approx(114.0)


@pytest.mark.parametrize("now", [110.0, 112.0, 113.999], ids=["at-timeout", "mid", "just-inside"])
def test_in_gap_before_timeout_plus_grace(now):
    o = order(request_id="r", timed_out_at=110.0)
    assert dialog_gap(o, now) is not None


@pytest.mark.parametrize("now", [114.0, 114.001, 500.0], ids=["at-end", "just-after", "long-after"])
def test_out_of_gap_from_timeout_plus_grace(now):
    o = order(request_id="r", timed_out_at=110.0)
    assert dialog_gap(o, now) is None


def test_custom_open_grace_moves_the_end_inside():
    o = order(request_id="r", timed_out_at=110.0)
    assert dialog_gap(o, 110.999, open_grace=1.0) is not None


def test_custom_open_grace_moves_the_end_at_edge():
    o = order(request_id="r", timed_out_at=110.0)
    assert dialog_gap(o, 111.0, open_grace=1.0) is None


# -- dialog evidence: boundary at dialog_seen_at + settle ----------------------------

def test_seen_shortens_end_to_seen_plus_settle():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert dialog_gap(o, 111.0) == pytest.approx(112.3)


def test_seen_just_inside_settle_is_in_gap():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert dialog_gap(o, 112.299) is not None


def test_seen_at_settle_end_is_out():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert dialog_gap(o, 112.3) is None


def test_seen_equal_to_gone_counts():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.0)
    assert dialog_gap(o, 112.0) is None


def test_seen_before_gone_is_ignored():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=109.0)
    assert dialog_gap(o, 112.0) is not None


@pytest.mark.parametrize("seen", [0, 0.0, None, "110.1", -1.0], ids=["zero-int", "zero", "none", "str", "negative"])
def test_no_evidence_values_keep_the_full_grace(seen):
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=seen)
    assert dialog_gap(o, 113.5) is not None


def test_seen_late_never_extends_past_grace():
    """A stamp after gone+grace-settle cannot push the end beyond gone+grace."""
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=113.5)
    assert dialog_gap(o, 114.0) is None


def test_custom_settle_is_honoured():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.0)
    assert dialog_gap(o, 110.6, settle=0.5) is None


# -- gone = arrived: no reply channel (the hook left at once) -------------------------

def test_no_request_id_in_gap_just_inside():
    assert dialog_gap(order(arrived=100.0), 103.999) is not None


def test_no_request_id_out_at_arrived_plus_grace():
    assert dialog_gap(order(arrived=100.0), 104.0) is None


# -- parked: socket gone before the datagram (timed_out_at == 0) ----------------------

@pytest.mark.parametrize("now", [100.0, 120.0, 129.999], ids=["arrival", "deadline", "just-inside"])
def test_parked_is_in_gap(now):
    o = order(arrived=100.0, request_id="r")
    assert dialog_gap(o, now) == pytest.approx(130.0)


def test_parked_follows_custom_deadline():
    o = order(arrived=100.0, request_id="r")
    assert dialog_gap(o, 102.999, reply_deadline=2.0, grace=1.0) is not None


@pytest.mark.parametrize("now", [130.0, 130.001, 200.0], ids=["at-end", "just-after", "long-after"])
def test_lost_datagram_gap_is_over_by_parked_end(now):
    """From arrived + 30 the hook is assumed gone at arrived + 20; its
    grace ended at arrived + 24, so nothing is gated any more."""
    o = order(arrived=100.0, request_id="r")
    assert dialog_gap(o, now) is None


# -- gap_next_look -----------------------------------------------------------------------

def test_next_look_probes_at_timeout_plus_settle():
    o = order(request_id="r", timed_out_at=110.0)
    assert gap_next_look(o, 110.5) == pytest.approx(112.0)


def test_next_look_after_probe_instant_is_gap_end():
    o = order(request_id="r", timed_out_at=110.0)
    assert gap_next_look(o, 112.5) == pytest.approx(114.0)


def test_next_look_at_probe_instant_is_gap_end():
    """Boundary: the probe instant is no longer ahead."""
    o = order(request_id="r", timed_out_at=110.0)
    assert gap_next_look(o, 112.0) == pytest.approx(114.0)


def test_next_look_with_evidence_is_gap_end():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert gap_next_look(o, 110.5) == pytest.approx(112.3)


def test_next_look_when_parked_is_parked_end():
    o = order(arrived=100.0, request_id="r")
    assert gap_next_look(o, 105.0) == pytest.approx(130.0)


def test_next_look_out_of_gap_is_none():
    o = order(request_id="r", timed_out_at=110.0)
    assert gap_next_look(o, 114.0) is None


def test_next_look_for_own_is_none():
    assert gap_next_look(own(100.0), 101.0) is None


# -- keys_verdict: a lone prompt -------------------------------------------------------

def test_lone_background_timed_out_in_gap_is_opening():
    o = order(request_id="r", timed_out_at=110.0)
    assert keys_verdict(o, [], 111.0) == "opening"


def test_lone_background_at_grace_end_is_yes():
    o = order(request_id="r", timed_out_at=110.0)
    assert keys_verdict(o, [], 114.0) == "yes"


def test_lone_background_just_inside_grace_is_opening():
    o = order(request_id="r", timed_out_at=110.0)
    assert keys_verdict(o, [], 113.999) == "opening"


def test_lone_seen_at_settle_end_is_yes():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert keys_verdict(o, [], 112.3) == "yes"


def test_lone_seen_just_inside_settle_is_opening():
    o = order(request_id="r", timed_out_at=110.0, dialog_seen_at=110.3)
    assert keys_verdict(o, [], 112.299) == "opening"


@pytest.mark.parametrize("o", [
    own(100.0),
    order(launch="foreground", request_id="r", timed_out_at=110.0),
], ids=["claude-own", "foreground"])
def test_lone_own_and_foreground_after_timeout_are_yes(o):
    assert keys_verdict(o, [], 110.5) == "yes"


def test_lone_parked_background_is_opening():
    assert keys_verdict(order(request_id="r"), [], 105.0) == "opening"


@pytest.mark.parametrize("o", [None, "x", 3, [], {"launch": object()}],
                         ids=["none", "str", "int", "list", "odd-launch"])
def test_verdict_never_raises(o):
    assert keys_verdict(o, [o], 105.0) in {"yes", "behind", "unknown", "opening"}


# -- keys_verdict: several prompts ----------------------------------------------------

def test_would_be_yes_with_others_becomes_opening():
    """The target's dialog entered at its timeout (104); Claude's own
    prompt came later (106): the target is on top, but still opening."""
    target = order(arrived=90.0, request_id="r", timed_out_at=104.0)
    assert keys_verdict(target, [own(106.0)], 107.0) == "opening"


def test_would_be_yes_with_others_after_grace_is_yes():
    target = order(arrived=90.0, request_id="r", timed_out_at=104.0)
    assert keys_verdict(target, [own(106.0)], 108.0) == "yes"


def test_behind_wins_over_opening():
    """Claude's own prompt (100) was on screen before the target's dialog
    entered (104): the target is behind, whatever its gap."""
    target = order(arrived=90.0, request_id="r", timed_out_at=104.0)
    assert keys_verdict(target, [own(100.0)], 105.0) == "behind"


SEVERAL = [
    (order(arrived=90.0, request_id="r", timed_out_at=104.0), [own(100.0)], 105.0),
    (order(arrived=90.0, request_id="r", timed_out_at=104.0), [own(104.0)], 105.0),
    (order(arrived=90.0, request_id="r", timed_out_at=104.0), [own(106.0)], 107.0),
    (order(arrived=104.0), [order(arrived=100.0, owner="a2")], 105.0),
    (order(arrived=100.0, owner="a2"), [order(arrived=104.0)], 105.0),
    (order(launch="unknown", arrived=104.0), [own(100.0)], 105.0),
    (order(launch="unknown", arrived=100.0), [own(104.0)], 105.0),
    (order(arrived=100.0, request_id="r", timed_out_at=104.0),
     [order(arrived=101.0, owner="a2", request_id="r2", timed_out_at=103.9)], 105.0),
    (order(arrived=100.0, request_id="r"), [own(105.0)], 106.0),
]
SEVERAL_IDS = ["own-first", "own-same-instant", "own-later", "bg-nohook-after-bg",
               "bg-nohook-before-bg", "unknown-after-own", "unknown-before-own",
               "two-timed-out", "parked-with-own"]


@pytest.mark.parametrize("target,others,now", SEVERAL, ids=SEVERAL_IDS)
def test_opening_only_replaces_a_would_be_yes(target, others, now):
    """Oracle: the same orders with no grace at all (the target's gap
    closed) give today's verdict; "behind"/"unknown" stay, "yes" becomes
    "opening" while the target is in its gap."""
    before = keys_verdict(target, others, now, open_grace=0.0, settle=0.0)
    in_gap = dialog_gap(target, now) is not None
    expected = "opening" if (before == "yes" and in_gap) else before
    if target.get("request_id") and not target.get("timed_out_at"):
        # A parked target is gated whatever the grace; only its precedence
        # is checked: never "opening" when another verdict says more.
        got = keys_verdict(target, others, now)
        assert got in {"behind", "unknown", "opening"}
        return
    assert keys_verdict(target, others, now) == expected
