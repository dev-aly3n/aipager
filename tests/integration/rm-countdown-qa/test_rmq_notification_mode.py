"""Criterion 13 at its boundary: a Notification ``permission_prompt`` whose
``permission_mode`` is empty, null, missing or not a string carries no
mode, so the prompt counts as a countdown candidate (design.md "the
Notification fallback passes the mode if it is a non-empty str, else
MODE_UNKNOWN"; entrypoints.md order dict). A PermissionRequest with the
same empty values is the contrast: there an absent mode is ``""`` and the
prompt is not a candidate (criterion 12, "missing").

Driven through the shared two-prompts rig: the real hook receiver, notify
path and tap handler."""

from __future__ import annotations

import time

import pytest

from aipager import prompt_stack
from aipager.bot.callbacks import PROMPT_ORDER_UNKNOWN
from tests.two_prompts_rig import CARD, answers

UNKNOWN = str(PROMPT_ORDER_UNKNOWN)
MISSING = object()

#: the empty values: id -> what the datagram carries (MISSING: no key at all)
EMPTY = {"empty": "", "null": None, "missing": MISSING, "number": 0, "list": [],
         "false": False}


def fields(value):
    return {} if value is MISSING else {"permission_mode": value}


def notification_bash(r, value):
    r.hook("Notification", notification_type="permission_prompt",
           message="Claude needs your permission to use Bash", **fields(value))


def claude_bash_with(r, value):
    r.hook("PermissionRequest", tool_name="Bash",
           tool_input={"command": "rm -rf /home/alice/app",
                       "description": "rm -rf /home/alice/app"},
           tool_use_id="toolu_p1", **fields(value))


def verdicts(r) -> list[str]:
    now = time.time()
    return [r.sess.keys_verdict(p, now) for p in r.sess.open_prompts()]


def first_order(r) -> dict:
    return r.sess.open_prompts()[0]["tool_info"]["order"]


# -- the Notification fallback: no mode means unknown --------------------------------

@pytest.mark.parametrize("value", EMPTY, ids=list(EMPTY))
def test_notification_without_a_usable_mode_records_mode_unknown(rig, value):
    notification_bash(rig, EMPTY[value])
    assert first_order(rig)["mode"] == prompt_stack.MODE_UNKNOWN


@pytest.mark.parametrize("value", EMPTY, ids=list(EMPTY))
def test_notification_without_a_usable_mode_is_a_candidate(rig, value):
    notification_bash(rig, EMPTY[value])
    assert prompt_stack.countdown_candidate(first_order(rig)) is True


@pytest.mark.parametrize("value", EMPTY, ids=list(EMPTY))
def test_notification_without_a_usable_mode_beside_a_question_is_unknown(rig, value):
    notification_bash(rig, EMPTY[value])
    rig.question()
    assert verdicts(rig) == ["unknown", "unknown"]


@pytest.mark.parametrize("value", ["", None], ids=["empty", "null"])
def test_notification_without_a_usable_mode_types_nothing_on_allow(rig, value):
    notification_bash(rig, value)
    rig.question()
    rig.tap(CARD, "allow")
    assert rig.keys == []


@pytest.mark.parametrize("value", ["", None], ids=["empty", "null"])
def test_notification_without_a_usable_mode_says_it_cannot_tell(rig, value):
    notification_bash(rig, value)
    rig.question()
    q = rig.tap(CARD, "allow")
    assert answers(q) == [UNKNOWN]


@pytest.mark.parametrize("value", ["", None], ids=["empty", "null"])
def test_notification_without_a_usable_mode_alone_is_typed(rig, value):
    """Boundary: a lone candidate is "yes" (criterion 12)."""
    notification_bash(rig, value)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
def test_notification_with_a_non_candidate_mode_records_it(rig, mode):
    """Control: a real mode string is kept as is."""
    notification_bash(rig, mode)
    assert first_order(rig)["mode"] == mode


# -- the contrast: PermissionRequest with the same empty values ----------------------

@pytest.mark.parametrize("value", ["", None, MISSING], ids=["empty", "null", "missing"])
def test_permission_request_without_a_mode_records_empty(rig, value):
    """entrypoints.md: the payload's mode, or "" when absent."""
    claude_bash_with(rig, value)
    assert first_order(rig)["mode"] == ""


@pytest.mark.parametrize("value", ["", None, MISSING], ids=["empty", "null", "missing"])
def test_permission_request_without_a_mode_is_no_candidate(rig, value):
    claude_bash_with(rig, value)
    assert prompt_stack.countdown_candidate(first_order(rig)) is False


@pytest.mark.parametrize("value", ["", None, MISSING], ids=["empty", "null", "missing"])
def test_permission_request_without_a_mode_keeps_arrival_order(rig, value):
    """The Bash came first: it is on top and Allow types Enter."""
    claude_bash_with(rig, value)
    rig.question()
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]
