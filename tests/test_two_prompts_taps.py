"""Roadmap 8.120: answering one of several open prompts.

Claude Code shows one dialog at a time, and keystrokes reach whichever is
on top. A tap answers the prompt of the message it sits on; through its
parked hook whenever it can (safe wherever its dialog is), by keys only
when that prompt's dialog is certainly the one Claude Code shows first,
and otherwise it refuses, typing nothing and keeping its buttons.
"""

from __future__ import annotations

import pytest

from aipager.bot.callbacks import PROMPT_BEHIND, PROMPT_ORDER_UNKNOWN
from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, Rig, answers


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def _extra_id(rig, i=0):
    return rig.sess.extra_prompts[i]["msg_id"]


def _agent_then_question(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()


def _toast(q) -> str:
    return " ".join(answers(q))


# ---- the tapped message picks the prompt -------------------------------------

def test_tap_on_extra_answers_extra_not_card(rig):
    _agent_then_question(rig)
    q = rig.tap(_extra_id(rig), "opt1")
    # Typed into Claude's question (the agent's dialog waits for its hook).
    assert rig.keys == ["Down", "Enter"]
    assert rig.decisions == []
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    # The card's prompt is untouched and still answerable.
    assert sess.pending_permission["tool_info"]["agent_id"] == BG
    assert sess.pending_permission["hook_reply"] is not None
    assert sess.extra_prompts == []
    assert "Selected option 2" in _toast(q)


def test_closed_extra_button_already_answered(rig):
    _agent_then_question(rig)
    extra = _extra_id(rig)
    rig.tap(extra, "opt0")
    rig.keys.clear()
    q = rig.tap(extra, "opt1")                    # a double tap
    assert rig.keys == []
    assert _toast(q) == str(ALREADY_ANSWERED)


def test_question_first_then_agent(rig):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make", request_id="req-bg")
    # The question is on the card and on top: typed.
    rig.tap(CARD, "opt0")
    assert rig.keys == ["Enter"]
    assert rig.sess.status == Status.INTERACTIVE
    # The agent's prompt answers through its hook.
    rig.tap(_extra_id(rig) if rig.sess.extra_prompts
            else rig.sess.pending_prompt_msg["msg_id"], "allow")
    assert [d for _r, d in rig.decisions] == [{"behavior": "allow"}]
    assert rig.keys == ["Enter"]
    assert rig.sess.status == Status.BUSY


def test_agent_by_hook_then_question(rig):
    _agent_then_question(rig)
    question_msg = _extra_id(rig)
    rig.tap(CARD, "allow")
    assert [d for _r, d in rig.decisions] == [{"behavior": "allow"}]
    assert rig.keys == []
    assert rig.sess.status == Status.INTERACTIVE
    rig.tap(question_msg, "opt1")
    assert rig.keys == ["Down", "Enter"]
    assert rig.sess.status == Status.BUSY


def test_answer_one_other_stays_live(rig):
    _agent_then_question(rig)
    question_msg = _extra_id(rig)
    rig.tap(CARD, "deny")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert not sess.prompt_known_closed(question_msg)
    assert [p["tool_info"]["name"] for p in sess.open_prompts()] == ["AskUserQuestion"]
    # The question kept its message, token and registration.
    assert sess.pending_prompt_msg["msg_id"] == question_msg


def test_card_drops_buttons_when_inline_closes_first(rig, run_async):
    _agent_then_question(rig)
    rig.card_edits.clear()
    rig.frames.clear()
    rig.bot._start_animation.reset_mock()
    rig.tap(CARD, "allow")
    mid, text, markup = rig.card_edits[-1]
    assert mid == CARD
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert not any("Allow" in b or "Deny" in b for b in labels)
    assert any("Stop" in b for b in labels)
    rig.bot._start_animation.assert_not_called()
    # And no busy frame is drawn over the wait (an agent's tool call, the
    # animator): the card says Waiting, not Working.
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/x"},
             tool_use_id="toolu_x", agent_id=FG, agent_type="ship-tester")
    rig.sess.record_tool("Read: /x", False)          # something new to draw
    assert run_async(rig.bot._edit_busy_rich(rig.sess, "Working")) is False
    assert rig.frames == []


# ---- which dialog is on top -------------------------------------------------

def test_background_prompt_after_timeout_refuses_while_question_open(rig):
    _agent_then_question(rig)
    # The agent's hook gives up: its dialog enters the terminal now, under
    # Claude's question.
    rig.timeout("req-bg")
    q = rig.tap(CARD, "allow")
    assert rig.keys == []
    assert rig.decisions == []
    assert _toast(q) == str(PROMPT_BEHIND)
    q.edit_message_reply_markup.assert_not_awaited()
    assert rig.sess.pending_permission is not None
    # Once the question is answered the agent's dialog is on top.
    rig.tap(_extra_id(rig), "opt0")
    rig.keys.clear()
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]
    assert rig.sess.status == Status.BUSY


def test_unknown_agent_prompt_refuses_both(rig):
    # An agent with no matched Agent call: how its dialogs open is not known.
    rig.hook("SubagentStart", agent_id=BG, agent_type="ship-reviewer")
    rig.agent_bash(BG, "make", hook=False)
    rig.question()
    q1 = rig.tap(_extra_id(rig), "opt0")
    q2 = rig.tap(CARD, "allow")
    assert rig.keys == []
    assert _toast(q1) == str(PROMPT_ORDER_UNKNOWN)
    assert _toast(q2) == str(PROMPT_ORDER_UNKNOWN)
    assert len(rig.sess.open_prompts()) == 2


def test_hook_allow_on_prompt_behind_types_nothing(rig):
    rig.launch(BG)
    rig.claude_bash(hook=False)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.tap(_extra_id(rig), "allow")
    assert [d for _r, d in rig.decisions] == [{"behavior": "allow"}]
    assert rig.keys == []
    assert rig.sess.extra_prompts == []
    assert rig.sess.status == Status.INTERACTIVE


def test_failed_hook_send_then_refused(rig):
    rig.launch(BG)
    rig.claude_bash(hook=False)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.hook_ok = False                        # the hook's socket is gone
    rig.timeout("req-bg")
    q = rig.tap(_extra_id(rig), "deny")
    assert rig.keys == []
    assert _toast(q) == str(PROMPT_BEHIND)
    assert len(rig.sess.open_prompts()) == 2


def test_second_claude_permission_refused_until_first_answered(rig):
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1", hook=False)
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=False)
    second = _extra_id(rig)
    q = rig.tap(second, "allow")
    assert rig.keys == []
    assert _toast(q) == str(PROMPT_BEHIND)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]
    rig.tap(second, "allow")
    assert rig.keys == ["Enter", "Enter"]
    assert rig.sess.status == Status.BUSY


# ---- auto-deny of a second prompt -------------------------------------------

def _deny_bash(rig, monkeypatch):
    monkeypatch.setattr(rig.bot, "_rule_denial",
                        lambda sess, tool: (tool == "Bash", None))


def test_extra_auto_deny_by_hook(rig, monkeypatch):
    rig.launch(BG)
    rig.question()
    _deny_bash(rig, monkeypatch)
    rig.agent_bash(BG, "make", request_id="req-bg")
    assert [d["behavior"] for _r, d in rig.decisions] == ["deny"]
    assert rig.keys == []
    assert len(rig.sess.open_prompts()) == 1
    assert any("blocked" in (m["text"] or "") for m in rig.sent)


def test_extra_auto_deny_waits_until_on_top(rig, monkeypatch):
    rig.question()
    _deny_bash(rig, monkeypatch)
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=False)
    sess = rig.sess
    # Behind the question: nothing typed, nothing shown, still open.
    assert rig.keys == []
    assert len(sess.open_prompts()) == 2
    assert rig.prompt_messages() == []
    rig.tap(CARD, "opt0")
    # The question's Enter, then the refusal once its dialog is on top:
    # the Deny button's own keys, down to the last row.
    assert rig.keys == ["Enter"] + ["Down"] * 5 + ["Enter"]
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


# ---- /stop ------------------------------------------------------------------

def test_stop_strips_every_prompt(rig, run_async):
    _agent_then_question(rig)
    question_msg = _extra_id(rig)
    run_async(rig.bot._stop_session_core(rig.sess))
    sess = rig.sess
    assert sess.status == Status.IDLE
    assert sess.open_prompts() == []
    assert sess.extra_prompts == []
    assert sess.prompt_known_closed(question_msg)
    rig.keys.clear()
    q = rig.tap(question_msg, "opt0")
    assert rig.keys == []
    assert _toast(q) == str(ALREADY_ANSWERED)
