"""Roadmap 8.120: a prompt that opens while another is open is shown.

A background agent asked for a permission, then Claude asked a question
(or the other way round). Only the first was drawn: the second was lost at
``transition()``'s same-state no-op, and a tool call of the agent could
move the session to BUSY while Claude's question was still open in the
terminal. Pinned here through the real hook receiver and notify path:

- every prompt is drawn, the later ones as their own messages;
- one dialog is never drawn twice (the Notification fallback, the
  PermissionRequest Claude Code fires for an AskUserQuestion, a repeat);
- each close path closes only the prompts it proves closed, and the
  session leaves INTERACTIVE only when none is left;
- Claude's Stop with a running agent's prompt open finishes the turn and
  keeps waiting on that prompt.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, OTHER, QUESTION, Rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def _summaries(sess):
    return [(p.get("tool_info") or {}).get("summary") for p in sess.open_prompts()]


def _agent_then_question(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.question()


# ---- arrival ----------------------------------------------------------------

def test_agent_permission_then_claude_question_both_shown(rig):
    _agent_then_question(rig)
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert len(sess.open_prompts()) == 2
    # The agent's prompt is the card's, with Allow/Deny.
    assert sess.pending_permission["tool_info"]["agent_id"] == BG
    assert rig.card_edits and rig.card_edits[-1][0] == CARD
    # Claude's question is its own message under the card, with its options.
    [msg] = rig.prompt_messages()
    assert msg["reply_to_message_id"] == CARD
    assert rig.buttons(msg)[:2] == ["Only the card", "The card and the bar"]
    assert sess.extra_prompts[0]["msg_id"] == msg["msg_id"]
    assert sess.extra_prompts[0]["perm"]["tool_info"]["name"] == "AskUserQuestion"


def test_claude_question_then_agent_permission_both_shown(rig):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make")
    sess = rig.sess
    assert sess.pending_permission["tool_info"]["name"] == "AskUserQuestion"
    [msg] = rig.prompt_messages()
    assert [b.split()[-1] for b in rig.buttons(msg)] == ["Allow", "Deny"]
    assert "make" in msg["text"]


def test_notification_fallback_not_a_second_prompt(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash",
             agent_id=BG, agent_type="ship-reviewer")
    assert len(rig.sess.open_prompts()) == 1
    assert rig.prompt_messages() == []


def test_question_permission_request_not_drawn_twice(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.question()
    rig.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input=QUESTION,
             aipager_reply_addr="/nonexistent/reply-q.sock",
             aipager_request_id="req-q")
    assert len(rig.sess.open_prompts()) == 2
    assert len(rig.prompt_messages()) == 1


def test_question_request_id_orders_background_question(rig):
    """A background agent's question: its PermissionRequest's request id is
    kept on it, so its dialog counts as not in the terminal while that
    hook is parked (aipager/prompt_stack.py)."""
    rig.launch(BG)
    rig.claude_bash()
    rig.question(agent=BG, tool_use_id="toolu_bgq")
    rig.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input=QUESTION,
             agent_id=BG, agent_type="ship-reviewer",
             aipager_reply_addr="/nonexistent/reply-q.sock",
             aipager_request_id="req-bgq")
    question = rig.sess.extra_prompts[0]["perm"]
    assert question["tool_info"]["order"]["request_id"] == "req-bgq"
    assert question["tool_info"]["order"]["launch"] == "background"
    # Claude's own permission is on top: the question's keys would land there.
    assert rig.sess.keys_verdict(question, time.monotonic()) == "behind"
    assert rig.sess.keys_verdict(rig.sess.pending_permission,
                                 time.monotonic()) == "yes"


def test_same_permission_twice_drawn_once(rig):
    rig.launch(BG)
    rig.claude_bash()
    rig.agent_bash(BG, "make", request_id="req-1")
    rig.agent_bash(BG, "make", request_id="req-2")   # the same dialog again
    assert len(rig.sess.open_prompts()) == 2
    assert len(rig.prompt_messages()) == 1


def test_parallel_calls_same_tool_both_drawn(rig):
    rig.launch(BG)
    rig.claude_bash()
    # One summary (the description), two calls: told apart by their input.
    rig.agent_bash(BG, "make build", description="Run make")
    rig.agent_bash(BG, "make test", description="Run make")
    assert len(rig.sess.open_prompts()) == 3
    assert len(rig.prompt_messages()) == 2


def test_helper_question_while_prompt_open_not_shown(rig):
    rig.claude_bash()
    rig.question(agent=OTHER, tool_use_id="toolu_helper")   # an unknown agent
    assert len(rig.sess.open_prompts()) == 1
    assert rig.prompt_messages() == []


# ---- what closes what -------------------------------------------------------

def test_agent_tool_call_keeps_claude_question_open(rig):
    _agent_then_question(rig)
    # The agent working again: its own prompt was answered in the terminal.
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a.py"},
             tool_use_id="toolu_r1", agent_id=BG, agent_type="ship-reviewer")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"]["name"] for p in sess.open_prompts()] == ["AskUserQuestion"]
    # The card's prompt went: its buttons type nothing, the question's stay.
    assert sess.prompt_known_closed(CARD)
    assert not sess.prompt_known_closed(sess.pending_prompt_msg["msg_id"])


def test_claude_tool_call_keeps_agent_prompt_open(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/b.py"},
             tool_use_id="toolu_c1")
    assert rig.sess.status == Status.INTERACTIVE
    assert len(rig.sess.open_prompts()) == 1


def test_post_tool_use_closes_extra_only(rig):
    _agent_then_question(rig)
    rig.hook("PostToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
             tool_use_id="toolu_q1", tool_response={"answers": {}})
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"].get("agent_id") for p in sess.open_prompts()] == [BG]
    assert sess.extra_prompts == []
    assert sess.prompt_known_closed(rig.prompt_messages()[0]["msg_id"])
    # The last one closing: the session works again.
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a.py"},
             tool_use_id="toolu_r2", agent_id=BG, agent_type="ship-reviewer")
    assert sess.status == Status.BUSY


def test_subagent_stop_closes_its_prompts_only(rig):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make")
    rig.agent_bash(FG, "pytest")
    rig.hook("SubagentStop", agent_id=FG, agent_type="ship-tester")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"]["agent_id"] for p in sess.open_prompts()] == [BG]
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert sess.status == Status.BUSY


def test_phantom_stop_closes_nothing(rig):
    _agent_then_question(rig)
    rig.hook("SubagentStop", agent_id="a00000000000000ff", agent_type="")
    assert len(rig.sess.open_prompts()) == 2
    assert rig.sess.status == Status.INTERACTIVE


def test_continuation_stays_waiting_with_extra(rig):
    """A finished agent's wake-up while Claude's question (the second
    prompt) is open: the question stays."""
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(FG, "pytest")
    rig.question()
    rig.sess.active_subagents.pop(FG, None)      # it ended with no stop seen
    rig.hook("UserPromptSubmit", prompt="<task-notification>\n<task-id>" + FG
             + "</task-id>\n<status>completed</status>\n</task-notification>")
    assert rig.sess.status == Status.INTERACTIVE


def test_terminal_prompt_keeps_agent_prompt(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.claude_bash()
    rig.hook("UserPromptSubmit", prompt="go on")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"].get("agent_id") for p in sess.open_prompts()] == [BG]


def test_reply_timeout_reaches_extra(rig):
    rig.launch(BG)
    rig.claude_bash()
    rig.agent_bash(BG, "make", request_id="req-bg")
    perm = rig.sess.extra_prompts[0]["perm"]
    assert perm["hook_reply"]["request_id"] == "req-bg"
    rig.timeout("req-bg")
    assert perm["hook_reply"] is None
    assert perm["tool_info"]["order"]["timed_out_at"] > 0
    # The card's prompt keeps its own channel.
    assert rig.sess.pending_permission["hook_reply"] is not None


# ---- Claude's turn ends while an agent's prompt is open ----------------------

def test_stop_with_background_prompt_keeps_waiting(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.hook("Stop", stop_hook_active=False,
             last_assistant_message="The reviewer runs in the background.")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert not sess.prompt_wait_carry
    [perm] = sess.open_prompts()
    assert perm["tool_info"]["agent_id"] == BG
    # The card's prompt moved to its own message, with its hook channel.
    assert sess.pending_permission is None
    assert perm["hook_reply"] is not None
    msg = rig.prompt_messages()[-1]
    assert sess.pending_prompt_msg["msg_id"] == msg["msg_id"]
    assert [b.split()[-1] for b in rig.buttons(msg)] == ["Allow", "Deny"]
    assert sess.prompt_known_closed(CARD)


def test_stop_closes_claudes_own_prompt(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.question()
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done.")
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [BG]


def test_held_message_not_released_at_carried_stop(rig, monkeypatch):
    """A message held behind the agent's dialog is not typed at Claude's
    Stop: it would land in that dialog."""
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    sess = rig.sess
    seen_open = []
    real_drain = rig.bot._drain_next_queued

    async def _drain(s):
        seen_open.append(s.dialog_is_open())
        await real_drain(s)

    monkeypatch.setattr(rig.bot, "_drain_next_queued", _drain)
    injected = []

    async def _inject(*a, **k):
        injected.append(a)
        return True

    monkeypatch.setattr(rig.bot, "_inject_prompt", _inject)
    sess.queue_prompt("also check the docs", 41)
    sess.dialog_hold = {"entered_at": sess.interactive_entered_at,
                        "told": set(), "notices": []}
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Waiting.")
    assert injected == []
    assert sess.status == Status.INTERACTIVE
    assert [t[0] for t in sess.pending_queue] == ["also check the docs"]
    assert all(seen_open)


def test_terminal_no_on_claude_keeps_agent_prompt(rig, run_async):
    """Claude's own dialog refused at the terminal (its interrupt marker)
    while a running agent's prompt is open: the session keeps waiting on
    the agent's."""
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.claude_bash()
    sess = rig.sess
    run_async(rig.bot.notify(sess, "dialog_ended_in_terminal",
                             {"entered_at": sess.interactive_entered_at}))
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"].get("agent_id") for p in sess.open_prompts()] == [BG]
