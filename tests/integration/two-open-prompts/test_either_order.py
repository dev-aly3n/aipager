"""Two prompts open at once, asked and answered in either order (roadmap
8.120): a background agent's Bash permission and Claude's own question.

Each row plays the hooks Claude Code sends, taps the buttons the chat
shows, and checks that every answer reached its own prompt (the agent's by
its hook's request id, the question by keys only), that the session waits
until the last one is answered, and that Claude's Stop then ends the turn
with nothing left open.
"""

from __future__ import annotations

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD

REQ = "req-bg-make"


def _ask(rig, order):
    rig.launch(BG)
    if order == "agent-first":
        rig.agent_bash(BG, "make", request_id=REQ)
        rig.question()
    else:
        rig.question()
        rig.agent_bash(BG, "make", request_id=REQ)


def _where(rig):
    """``(agent's message, question's message)``: the card holds the first."""
    sess = rig.sess
    first = sess.pending_permission
    extra = sess.extra_prompts[0]["msg_id"]
    if first["tool_info"].get("agent_id") == BG:
        return CARD, extra
    return extra, CARD


@pytest.mark.parametrize("order", ["agent-first", "claude-first"])
@pytest.mark.parametrize("answer", ["agent-then-question", "question-then-agent"])
def test_both_answers_reach_their_own_prompt(rig, order, answer):
    _ask(rig, order)
    sess = rig.sess
    assert len(sess.open_prompts()) == 2
    agent_msg, question_msg = _where(rig)

    taps = ([(agent_msg, "allow"), (question_msg, "opt1")] if answer == "agent-then-question"
            else [(question_msg, "opt1"), (agent_msg, "allow")])
    for i, (msg_id, verb) in enumerate(taps):
        rig.tap(msg_id, verb)  # a promoted prompt keeps its message
        if i == 0:
            assert sess.status == Status.INTERACTIVE
            assert len(sess.open_prompts()) == 1
    assert sess.status == Status.BUSY
    # The agent's answer went through its own parked hook, never by keys;
    # the question's by keys only.
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]
    assert rig.keys == ["Down", "Enter"]
    # Both messages refuse a second tap now.
    assert sess.prompt_known_closed(agent_msg) and sess.prompt_known_closed(question_msg)

    rig.hook("PostToolUse", tool_name="AskUserQuestion", tool_input={}, tool_use_id="toolu_q1",
             tool_response={})
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Both done.")
    assert sess.status == Status.IDLE
    assert sess.open_prompts() == [] and sess.extra_prompts == []


def test_typed_answers_follow_claude_codes_order(rig):
    """The agent's hook gave up: its dialog is under the question. Its
    Allow types nothing until the question is answered."""
    _ask(rig, "agent-first")
    agent_msg, question_msg = _where(rig)
    rig.timeout(REQ)
    q = rig.tap(agent_msg, "allow")
    assert rig.keys == [] and rig.decisions == []
    assert "another prompt first" in " ".join(c.args[0] for c in q.answer.await_args_list)
    rig.tap(question_msg, "opt0")
    rig.tap(agent_msg, "allow")
    assert rig.keys == ["Enter", "Enter"]
    assert rig.sess.status == Status.BUSY


def test_the_agents_own_tool_call_answers_only_its_prompt(rig):
    """Answered in the terminal: the agent works again; Claude's question
    stays on Telegram with its buttons."""
    _ask(rig, "claude-first")
    agent_msg, question_msg = _where(rig)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
             tool_use_id="toolu_r", agent_id=BG, agent_type="ship-reviewer")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert sess.prompt_known_closed(agent_msg)
    assert not sess.prompt_known_closed(question_msg)
    rig.tap(question_msg, "opt1")
    assert rig.keys == ["Down", "Enter"]
    assert sess.status == Status.BUSY
