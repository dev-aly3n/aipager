"""Answering two open prompts (design.md success criteria 2, 3, 4 and 6):
each answer reaches its own prompt in either order; Allow/Deny on a prompt
whose hook still waits always answers it through the hook; a keystroke tap
whose dialog is not on top types nothing, shows one of the two pop-ups
and keeps its buttons; a closed prompt's button says "already answered".
"""

from __future__ import annotations

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, OTHER, answers

BEHIND = "Claude Code is showing another prompt first, so answer that one before this one."
UNKNOWN = "aipager can't tell which prompt Claude Code is showing first, so answer this one in the terminal."
REQ = "req-bg"


def extra_ids(rig) -> list[int]:
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def agent_first(rig):
    """Returns ``(agent's surface, question's surface)``."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ)
    rig.question()
    return CARD, extra_ids(rig)[0]


def claude_first(rig):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make", request_id=REQ)
    return extra_ids(rig)[0], CARD


ORDERS = {"agent-first": agent_first, "claude-first": claude_first}


def decided(rig):
    return [(r["request_id"], d["behavior"]) for r, d in rig.decisions]


# -- either order, each answer to its own prompt ---------------------------------------

@pytest.mark.parametrize("order", ORDERS)
def test_agent_allow_by_hook_reaches_its_request(rig, order):
    agent, _q = ORDERS[order](rig)
    rig.tap(agent, "allow")
    assert decided(rig) == [(REQ, "allow")]


@pytest.mark.parametrize("order", ORDERS)
def test_agent_allow_by_hook_types_no_keys(rig, order):
    agent, _q = ORDERS[order](rig)
    rig.tap(agent, "allow")
    assert rig.keys == []


@pytest.mark.parametrize("order", ORDERS)
def test_agent_deny_by_hook_reaches_its_request(rig, order):
    agent, _q = ORDERS[order](rig)
    rig.tap(agent, "deny")
    assert decided(rig) == [(REQ, "deny")]


@pytest.mark.parametrize("order", ORDERS)
def test_question_answer_first_types_keys(rig, order):
    """The agent's hook still waits, so its dialog is not in the terminal:
    the question is on top whichever arrived first."""
    _a, question = ORDERS[order](rig)
    rig.tap(question, "opt1")
    assert rig.keys != []


@pytest.mark.parametrize("order", ORDERS)
def test_question_answer_sends_no_hook_decision(rig, order):
    _a, question = ORDERS[order](rig)
    rig.tap(question, "opt1")
    assert rig.decisions == []


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("first", ["agent", "question"])
def test_after_first_answer_still_interactive(rig, order, first):
    agent, question = ORDERS[order](rig)
    rig.tap(agent if first == "agent" else question,
            "allow" if first == "agent" else "opt0")
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("first", ["agent", "question"])
def test_after_first_answer_one_prompt_left(rig, order, first):
    agent, question = ORDERS[order](rig)
    rig.tap(agent if first == "agent" else question,
            "allow" if first == "agent" else "opt0")
    assert len(rig.sess.open_prompts()) == 1


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("first", ["agent", "question"])
def test_after_both_answers_busy(rig, order, first):
    agent, question = ORDERS[order](rig)
    taps = [(agent, "allow"), (question, "opt0")]
    if first == "question":
        taps.reverse()
    for msg, verb in taps:
        rig.tap(msg, verb)
    assert rig.sess.status == Status.BUSY


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("first", ["agent", "question"])
def test_after_both_answers_nothing_open(rig, order, first):
    agent, question = ORDERS[order](rig)
    taps = [(agent, "allow"), (question, "opt0")]
    if first == "question":
        taps.reverse()
    for msg, verb in taps:
        rig.tap(msg, verb)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("order", ORDERS)
def test_question_still_answerable_after_agent_answer(rig, order):
    agent, question = ORDERS[order](rig)
    rig.tap(agent, "allow")
    rig.tap(question, "opt1")
    assert rig.keys != []


@pytest.mark.parametrize("order", ORDERS)
def test_agent_still_answerable_after_question_answer(rig, order):
    agent, question = ORDERS[order](rig)
    rig.tap(question, "opt1")
    rig.tap(agent, "allow")
    assert decided(rig) == [(REQ, "allow")]


# -- two of Claude's own parallel permissions (main loop is FIFO) -----------------------

def two_claude(rig, *, hook):
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1", hook=hook, request_id="req-p1")
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=hook, request_id="req-p2")
    return CARD, extra_ids(rig)[0]


def test_second_claude_permission_by_hook_reaches_its_request(rig):
    _first, second = two_claude(rig, hook=True)
    rig.tap(second, "allow")
    assert decided(rig) == [("req-p2", "allow")]


def test_second_claude_permission_without_hook_refused_behind(rig):
    _first, second = two_claude(rig, hook=False)
    q = rig.tap(second, "allow")
    assert answers(q) == [BEHIND]


def test_second_claude_permission_without_hook_types_nothing(rig):
    _first, second = two_claude(rig, hook=False)
    rig.tap(second, "allow")
    assert rig.keys == []


def test_first_claude_permission_without_hook_types_keys(rig):
    first, _second = two_claude(rig, hook=False)
    rig.tap(first, "allow")
    assert rig.keys != []


def test_second_claude_permission_typed_once_first_is_answered(rig):
    first, second = two_claude(rig, hook=False)
    rig.tap(first, "allow")
    typed = len(rig.keys)
    rig.tap(second, "allow")
    assert len(rig.keys) > typed


# -- refusals: the agent's hook gave up and its dialog sits under the question ----------

def timed_out_agent_first(rig):
    agent, question = agent_first(rig)
    rig.timeout(REQ)
    return agent, question


def test_timed_out_agent_behind_question_refused_with_behind_popup(rig):
    agent, _q = timed_out_agent_first(rig)
    q = rig.tap(agent, "allow")
    assert answers(q) == [BEHIND]


def test_timed_out_agent_behind_question_types_nothing(rig):
    agent, _q = timed_out_agent_first(rig)
    rig.tap(agent, "allow")
    assert rig.keys == []


def test_timed_out_agent_refusal_sends_no_decision(rig):
    agent, _q = timed_out_agent_first(rig)
    rig.tap(agent, "allow")
    assert rig.decisions == []


def test_refused_tap_keeps_its_buttons(rig):
    agent, _q = timed_out_agent_first(rig)
    q = rig.tap(agent, "allow")
    assert q.edit_message_reply_markup.await_count == 0 and q.edit_message_text.await_count == 0


def test_refused_tap_keeps_both_prompts_open(rig):
    agent, _q = timed_out_agent_first(rig)
    rig.tap(agent, "allow")
    assert len(rig.sess.open_prompts()) == 2


def test_refused_tap_keeps_session_interactive(rig):
    agent, _q = timed_out_agent_first(rig)
    rig.tap(agent, "deny")
    assert rig.sess.status == Status.INTERACTIVE


def test_question_on_top_answered_by_keys_after_agent_timed_out(rig):
    _agent, question = timed_out_agent_first(rig)
    rig.tap(question, "opt0")
    assert rig.keys != []


def test_timed_out_agent_typed_after_question_answered(rig):
    agent, question = timed_out_agent_first(rig)
    rig.tap(question, "opt0")
    typed = len(rig.keys)
    rig.tap(agent, "allow")
    assert len(rig.keys) > typed


def test_question_behind_timed_out_background_agent_refused(rig):
    """Claude's question arrives after the agent's dialog entered the
    terminal (its hook gave up first): the question is behind it."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ)
    rig.timeout(REQ)
    rig.question()
    q = rig.tap(extra_ids(rig)[0], "opt0")
    assert answers(q) == [BEHIND]


def test_question_behind_timed_out_agent_types_nothing(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ)
    rig.timeout(REQ)
    rig.question()
    rig.tap(extra_ids(rig)[0], "opt0")
    assert rig.keys == []


# -- refusals: an agent of unknown launch kind --------------------------------------------

def unknown_agent_first(rig):
    """No Agent call matched: launch kind unknown. Its hook gives up after
    the question arrived, so it entered sometime around the question."""
    rig.agent_bash(OTHER, "make", request_id="req-u")
    rig.question()
    rig.timeout("req-u")
    return CARD, extra_ids(rig)[0]


def test_unknown_agent_allow_refused_with_unknown_popup(rig):
    agent, _q = unknown_agent_first(rig)
    q = rig.tap(agent, "allow")
    assert answers(q) == [UNKNOWN]


def test_unknown_agent_question_refused_with_unknown_popup(rig):
    _agent, question = unknown_agent_first(rig)
    q = rig.tap(question, "opt0")
    assert answers(q) == [UNKNOWN]


def test_unknown_agent_refusals_type_nothing(rig):
    agent, question = unknown_agent_first(rig)
    rig.tap(agent, "allow")
    rig.tap(question, "opt0")
    assert rig.keys == []


# -- hook reply always safe; a failed send falls back to the order rule ------------------

def test_hook_allow_on_prompt_behind_types_nothing(rig):
    """Claude's question is on top; Claude's own parked permission behind
    it is still answered, through its hook."""
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    rig.tap(extra_ids(rig)[0], "allow")
    assert rig.keys == []


def test_hook_allow_on_prompt_behind_reaches_its_request(rig):
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    rig.tap(extra_ids(rig)[0], "allow")
    assert decided(rig) == [("req-p2", "allow")]


def test_hook_deny_on_prompt_behind_reaches_its_request(rig):
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    rig.tap(extra_ids(rig)[0], "deny")
    assert decided(rig) == [("req-p2", "deny")]


def test_failed_hook_send_behind_types_nothing(rig):
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    rig.hook_ok = False
    rig.tap(extra_ids(rig)[0], "allow")
    assert rig.keys == []


def test_failed_hook_send_behind_shows_a_refusal(rig):
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    rig.hook_ok = False
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert answers(q) and answers(q)[-1] in (BEHIND, UNKNOWN)


def test_failed_hook_send_on_top_falls_back_to_keys(rig):
    """Partition: the send fails but the prompt is on top: keys are safe."""
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1", request_id="req-p1")
    rig.question()
    rig.hook_ok = False
    rig.tap(CARD, "allow")
    assert rig.keys != []


# -- stale buttons ------------------------------------------------------------------------

@pytest.mark.parametrize("order", ORDERS)
def test_answered_agent_button_says_already_answered(rig, order):
    agent, _q = ORDERS[order](rig)
    rig.tap(agent, "allow")
    q = rig.tap(agent, "allow")
    assert answers(q) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("order", ORDERS)
def test_answered_question_button_says_already_answered(rig, order):
    _a, question = ORDERS[order](rig)
    rig.tap(question, "opt0")
    q = rig.tap(question, "opt1")
    assert answers(q) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("order", ORDERS)
def test_stale_question_tap_types_nothing_more(rig, order):
    _a, question = ORDERS[order](rig)
    rig.tap(question, "opt0")
    typed = list(rig.keys)
    rig.tap(question, "opt1")
    assert rig.keys == typed


@pytest.mark.parametrize("order", ORDERS)
def test_stale_agent_tap_sends_no_second_decision(rig, order):
    agent, _q = ORDERS[order](rig)
    rig.tap(agent, "allow")
    rig.tap(agent, "deny")
    assert decided(rig) == [(REQ, "allow")]


def test_card_prompt_closed_by_terminal_says_already_answered(rig):
    """The agent's prompt (on the card) is answered at the terminal, its
    tool runs: the card's Allow is stale; the question's message lives."""
    agent, _q = agent_first(rig)
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": "make"},
             tool_use_id="toolu_bg_bash", agent_id=BG, agent_type="ship-reviewer")
    q = rig.tap(agent, "allow")
    assert answers(q) == [str(ALREADY_ANSWERED)]


def test_card_prompt_closed_question_message_still_answers(rig):
    _agent, question = agent_first(rig)
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": "make"},
             tool_use_id="toolu_bg_bash", agent_id=BG, agent_type="ship-reviewer")
    rig.tap(question, "opt0")
    assert rig.keys != []


def test_card_drops_answer_buttons_when_its_prompt_closes_first(rig):
    agent, _q = agent_first(rig)
    rig.tap(agent, "allow")
    _mid, _text, markup = [e for e in rig.card_edits if e[0] == CARD][-1]
    texts = [b.text for row in (markup.inline_keyboard if markup else []) for b in row]
    assert not any("Allow" in t or "Deny" in t for t in texts)
