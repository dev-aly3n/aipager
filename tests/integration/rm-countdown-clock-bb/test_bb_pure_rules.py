"""The pure rules from the outside (design.md success criteria 4, 6, 9, 10
and the window edges of 13/18): ``prompt_stack.countdown_candidate`` and
``keys_verdict``, ``rm_countdown.clock_eligible`` with each condition
flipped alone, and ``rm_countdown.refusal_window_open`` on a real prompt.
None of them may raise."""

from __future__ import annotations

import pytest

from aipager import prompt_stack, rm_countdown
from aipager.prompt_stack import CANDIDATE_MODES, MODE_UNKNOWN, countdown_candidate, keys_verdict
from tests.rm_clock_bb import REFUSE_AT, RM, WINDOW_AT


# -- the published constants ---------------------------------------------------------------------

def test_candidate_modes():
    assert CANDIDATE_MODES == frozenset({"bypassPermissions", "auto"})


def test_mode_unknown():
    assert MODE_UNKNOWN == "?"


def test_refusal_constants():
    assert (rm_countdown.REFUSE_AFTER_SECONDS, rm_countdown.WINDOW_LEAD_SECONDS,
            rm_countdown.CHECK_GRACE_SECONDS) == (120.0, 15.0, 3.0)


def test_keys_verdict_has_no_switch_keyword():
    with pytest.raises(TypeError):
        keys_verdict({}, [], 0.0, rm_countdown_off=True)


# -- countdown_candidate partitions --------------------------------------------------------------

def order(**kw):
    base = {"owner": "", "tool": "Bash", "mode": "bypassPermissions"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()

CANDIDATES = {
    "bypass": order(), "auto": order(mode="auto"), "unknown-mode": order(mode="?"),
    "no-owner-key": order(owner=_DROP), "owner-none": order(owner=None),
}
NOT_CANDIDATES = {
    "agent-owner": order(owner="a1b2c3d4e5f60718a"),
    "mode-default": order(mode="default"), "mode-acceptEdits": order(mode="acceptEdits"),
    "mode-plan": order(mode="plan"), "mode-empty": order(mode=""),
    "mode-missing": order(mode=_DROP), "mode-case": order(mode="Auto"),
    "tool-edit": order(tool="Edit"), "tool-question": order(tool="AskUserQuestion"),
    "tool-missing": order(tool=_DROP), "tool-lower": order(tool="bash"),
}


@pytest.mark.parametrize("name", CANDIDATES)
def test_candidate(name):
    assert countdown_candidate(CANDIDATES[name]) is True


@pytest.mark.parametrize("name", NOT_CANDIDATES)
def test_not_candidate(name):
    assert countdown_candidate(NOT_CANDIDATES[name]) is False


class Raising(dict):
    def get(self, *a, **k):
        raise RuntimeError("boom")

    def __getitem__(self, k):
        raise RuntimeError("boom")

    def __contains__(self, k):
        raise RuntimeError("boom")


@pytest.mark.parametrize("bad", [None, 0, "x", [], ("Bash",), object()],
                         ids=["none", "int", "str", "list", "tuple", "object"])
def test_a_non_dict_order_is_a_candidate(bad):
    assert countdown_candidate(bad) is True


def test_an_order_that_raises_is_a_candidate():
    assert countdown_candidate(Raising(owner="", tool="Bash", mode="default")) is True


@pytest.mark.parametrize("o", [order(tool=5), order(mode=["auto"]), order(mode={"auto": 1}),
                               order(owner=["x"])],
                         ids=["tool-int", "mode-list", "mode-dict", "owner-list"])
def test_odd_value_types_never_raise(o):
    try:
        countdown_candidate(o)
    except Exception as exc:  # noqa: BLE001 - "never raises"
        pytest.fail(f"raised {exc!r}")


# -- keys_verdict ------------------------------------------------------------------------------------

def test_a_lone_candidate_is_yes():
    assert keys_verdict(order(), [], 0.0) == "yes"


def test_a_lone_non_candidate_is_yes():
    assert keys_verdict(order(mode="default"), [], 0.0) == "yes"


def test_candidate_target_beside_another_is_unknown():
    assert keys_verdict(order(), [order(owner="a1b2c3d4e5f60718a")], 0.0) == "unknown"


def test_candidate_other_is_unknown_for_the_target():
    assert keys_verdict(order(owner="a1b2c3d4e5f60718a"), [order(mode="auto")], 0.0) == "unknown"


def test_a_candidate_among_many_others_is_unknown():
    others = [order(mode="default"), order(owner="x"), order(mode="?")]
    assert keys_verdict(order(mode="plan"), others, 0.0) == "unknown"


@pytest.mark.parametrize("bad", [None, "x", 3, Raising()], ids=["none", "str", "int", "raising"])
def test_a_malformed_other_is_unknown(bad):
    assert keys_verdict(order(mode="default"), [bad], 0.0) == "unknown"


@pytest.mark.parametrize("bad", [None, "x", Raising()], ids=["none", "str", "raising"])
def test_a_malformed_target_beside_another_is_unknown(bad):
    assert keys_verdict(bad, [order(mode="default")], 0.0) == "unknown"


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1.0, None],
                         ids=["nan", "inf", "negative", "none"])
def test_odd_now_never_raises(now):
    try:
        out = keys_verdict(order(), [order(mode="default")], now)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"raised {exc!r}")
    assert out in {"yes", "behind", "unknown"}


def test_reply_deadline_and_grace_keywords_still_accepted():
    assert keys_verdict(order(), [], 0.0, reply_deadline=20.0, grace=10.0) == "yes"


def test_prompt_stack_exports_the_rule():
    assert callable(prompt_stack.countdown_candidate)


# -- clock_eligible: each condition flipped alone ---------------------------------------------------

def eligible(**kw):
    args = {"tool_name": "Bash", "mode": "bypassPermissions", "suggestions": [],
            "agent_id": None, "tool_input": {"command": RM, "description": RM}}
    args.update(kw)
    return rm_countdown.clock_eligible(**args)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_eligible(mode):
    assert eligible(mode=mode) is True


@pytest.mark.parametrize("kw", [
    {"agent_id": "a1b2c3d4e5f60718a"}, {"tool_name": "Write"}, {"tool_name": None},
    {"mode": "default"}, {"mode": "acceptEdits"}, {"mode": "plan"}, {"mode": ""},
    {"mode": None}, {"mode": "?"}, {"suggestions": None},
    {"suggestions": [{"type": "setMode", "mode": "acceptEdits"}]}, {"suggestions": ()},
    {"suggestions": "[]"}, {"tool_input": {"command": "ls"}}, {"tool_input": {}},
    {"tool_input": None}, {"tool_input": {"command": 5}}, {"tool_input": "rm x"},
], ids=["agent", "tool-write", "tool-none", "default", "acceptEdits", "plan", "mode-empty",
        "mode-none", "mode-unknown", "sugg-none", "sugg-nonempty", "sugg-tuple", "sugg-str",
        "no-removal", "no-command", "input-none", "command-int", "input-str"])
def test_not_eligible(kw):
    assert eligible(**kw) is False


# -- refusal_window_open on a real prompt ---------------------------------------------------------

def perm_of(env):
    return env.rig.sess.open_prompts()[0]


@pytest.mark.parametrize("now,expected", [
    (WINDOW_AT - 0.001, False), (WINDOW_AT, True), (REFUSE_AT, True), (REFUSE_AT + 3600.0, True),
], ids=["just-before", "edge", "deadline", "long-after"])
def test_window_edges(env, now, expected):
    env.rm()
    assert rm_countdown.refusal_window_open(perm_of(env), now) is expected


def test_no_window_for_a_prompt_without_a_clock(env):
    env.rm(mode="default")
    assert rm_countdown.refusal_window_open(perm_of(env), REFUSE_AT + 3600.0) is False


@pytest.mark.parametrize("perm", [None, {}, "x", 5, {"tool_info": None},
                                  {"tool_info": {"rm_clock": "x"}}, Raising()],
                         ids=["none", "empty", "str", "int", "info-none", "clock-str",
                              "raising"])
def test_a_malformed_perm_has_no_window(perm):
    assert rm_countdown.refusal_window_open(perm, REFUSE_AT) is False


@pytest.mark.parametrize("now", [float("nan"), None, "x"], ids=["nan", "none", "str"])
def test_an_odd_now_never_raises(env, now):
    env.rm()
    try:
        rm_countdown.refusal_window_open(perm_of(env), now)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"raised {exc!r}")
