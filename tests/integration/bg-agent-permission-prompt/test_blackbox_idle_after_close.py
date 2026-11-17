"""Black-box: the session after a background agent's prompt that opened
once Claude's own turn ended (Bug B), and the messages around it. Written
from design.md's success criteria and entrypoints.md's side effects only.

When the last such prompt closes (a tap, the agent's PostToolUse or
PostToolUseFailure, or its next PreToolUse) the session goes back to IDLE,
never BUSY, with the job still open. A Telegram message picked up after
that is a new turn with its card under it at once; a message picked up
while a turn of Claude's own runs is still queued with its line armed.
"""

from __future__ import annotations

import logging

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, GROUP, OTHER

REQ = "req-bg-1"
REQ2 = "req-other-1"
CMD = "touch marker-a"
FIRST, SECOND = 77, 78


def _bash(command):
    return {"command": command, "description": command}


def _after_stop(rig, *agents):
    for agent in agents or (BG,):
        rig.launch(agent)
    rig.hook("Stop", stop_hook_active=False,
             last_assistant_message="The agent runs in the background.")


def _ask(rig, agent=BG, command=CMD, request_id=REQ):
    before = len(rig.sent)
    rig.agent_bash(agent, command, request_id=request_id)
    new = [m for m in rig.sent[before:] if m.get("reply_markup") is not None]
    return new[-1]["msg_id"] if new else None


def _agent_pre(rig, agent, command, tool_use_id):
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer")


def _agent_post(rig, agent, command, tool_use_id, event="PostToolUse"):
    extra = ({"tool_response": {"stdout": "ok"}} if event == "PostToolUse"
             else {"error": "The user doesn't want to proceed with this tool use."})
    rig.hook(event, tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer",
             **extra)


def _close(rig, how):
    """Open an agent's prompt after the Stop and close it by *how*."""
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    msg = _ask(rig)
    if how == "tap-allow":
        rig.tap(msg, "allow")
    elif how == "tap-deny":
        rig.tap(msg, "deny")
    elif how == "keys-after-timeout":
        rig.timeout(REQ)
        rig.tap(msg, "allow")
    elif how == "terminal-tool-ran":
        rig.timeout(REQ)
        _agent_post(rig, BG, CMD, "toolu_bg1")
    elif how == "terminal-refused":
        rig.timeout(REQ)
        _agent_post(rig, BG, CMD, "toolu_bg1", event="PostToolUseFailure")
    elif how == "agent-next-call":
        rig.timeout(REQ)
        rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
                 tool_use_id="toolu_bg2", agent_id=BG, agent_type="ship-reviewer")
    else:
        raise AssertionError(how)
    return msg


CLOSES = ["tap-allow", "tap-deny", "keys-after-timeout", "terminal-tool-ran",
          "terminal-refused", "agent-next-call"]


def _pickup(rig, msg_id, text):
    rig.hook("queue_pickup", consumed=[{"msg_id": msg_id, "chat_id": GROUP,
                                        "raw_text": text}], expired=[])


def _submit(rig, text):
    rig.hook("UserPromptSubmit", prompt=f"[via Telegram @bob]\n{text}")


def _queued_ids(sess):
    out = []
    for t in sess.queued_targets or []:
        out.append(t["msg_id"] if isinstance(t, dict) else getattr(t, "msg_id", t))
    return out


def _armed(sess, msg_id):
    for t in sess.queued_targets or []:
        tid = t["msg_id"] if isinstance(t, dict) else getattr(t, "msg_id", None)
        if tid == msg_id:
            return t["line_armed"] if isinstance(t, dict) else t.line_armed
    return None


def _cancel_timers(sess):
    timers = getattr(sess, "queued_line_timers", None) or {}
    for t in (timers.values() if isinstance(timers, dict) else timers):
        try:
            t.cancel()
        except Exception:
            pass


# -- every way the prompt closes leaves it IDLE ------------------------------

@pytest.mark.parametrize("how", CLOSES)
def test_close_leaves_the_session_idle(rig, how):
    _close(rig, how)
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("how", CLOSES)
def test_close_leaves_no_prompt_open(rig, how):
    _close(rig, how)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("how", CLOSES)
def test_close_keeps_the_job_open(rig, how):
    _close(rig, how)
    assert rig.sess.job_background_open()


@pytest.mark.parametrize("how", ["terminal-tool-ran", "tap-allow"])
def test_close_never_logs_a_move_to_busy(rig, how, caplog):
    with caplog.at_level(logging.INFO):
        _close(rig, how)
    assert "→ BUSY" not in caplog.text


def test_close_logs_interactive_to_idle(rig, caplog):
    _after_stop(rig)
    # Minutes passed since the Stop (hours in the field): the IDLE is not
    # the debounced re-IDLE, so its transition is logged.
    rig.sess.last_idle_at = 0.0
    with caplog.at_level(logging.INFO):
        _agent_pre(rig, BG, CMD, "toolu_bg1")
        _ask(rig)
        rig.timeout(REQ)
        _agent_post(rig, BG, CMD, "toolu_bg1")
    assert rig.sess.status == Status.IDLE
    assert "INTERACTIVE → IDLE" in caplog.text


# -- two agents: IDLE only when the last one closes (boundary) ---------------

def test_two_agents_first_answered_stays_interactive(rig):
    _after_stop(rig, BG, OTHER)
    first = _ask(rig, BG, "make a", REQ)
    _ask(rig, OTHER, "make b", REQ2)
    rig.tap(first, "allow")
    assert rig.sess.status == Status.INTERACTIVE


def test_two_agents_last_answered_goes_idle(rig):
    _after_stop(rig, BG, OTHER)
    first = _ask(rig, BG, "make a", REQ)
    second = _ask(rig, OTHER, "make b", REQ2)
    rig.tap(first, "allow")
    rig.tap(second, "deny")
    assert rig.sess.status == Status.IDLE


def test_two_agents_one_in_terminal_one_by_tap_goes_idle(rig):
    _after_stop(rig, BG, OTHER)
    _agent_pre(rig, BG, "make a", "toolu_a")
    _ask(rig, BG, "make a", REQ)
    second = _ask(rig, OTHER, "make b", REQ2)
    _agent_post(rig, BG, "make a", "toolu_a")
    rig.tap(second, "allow")
    assert rig.sess.status == Status.IDLE


# -- a Telegram message after the close: a new turn with its card ------------

def _message_after_close(rig, how="terminal-tool-ran"):
    _close(rig, how)
    _pickup(rig, FIRST, "hello")


@pytest.mark.parametrize("how", ["terminal-tool-ran", "tap-allow"])
def test_message_after_the_close_is_not_queued(rig, how):
    _message_after_close(rig, how)
    assert FIRST not in _queued_ids(rig.sess)


def test_message_after_the_close_logs_no_queued_while_busy(rig, caplog):
    with caplog.at_level(logging.INFO):
        _message_after_close(rig)
        _submit(rig, "hello")
    assert f"queued while busy: [{FIRST}]" not in caplog.text


def test_message_after_the_close_becomes_the_trigger(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    assert rig.sess.trigger_msg_id == FIRST


@pytest.mark.parametrize("how", ["terminal-tool-ran", "tap-allow"])
def test_message_after_the_close_gets_its_card_at_submit(rig, how):
    _message_after_close(rig, how)
    before = len(rig.sent)
    _submit(rig, "hello")
    assert any(m.get("reply_to_message_id") == FIRST for m in rig.sent[before:]), \
        rig.sent[before:]


def test_message_after_the_close_makes_the_session_busy(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    assert rig.sess.status == Status.BUSY


def test_message_after_the_close_starts_a_new_turn(rig):
    _message_after_close(rig)
    seq = rig.sess.turn_seq
    _submit(rig, "hello")
    assert rig.sess.turn_seq == seq + 1


def test_message_after_the_close_stop_logs_no_taken_inside_the_job(rig, caplog):
    with caplog.at_level(logging.INFO):
        _message_after_close(rig)
        _submit(rig, "hello")
        rig.hook("Stop", stop_hook_active=False, last_assistant_message="Hi.")
    assert "taken inside the open job" not in caplog.text


def test_message_after_the_close_answer_replies_to_it(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    before = len(rig.sent)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Hi there.")
    replies = [m for m in rig.sent[before:] if m.get("reply_to_message_id") == FIRST]
    assert replies, rig.sent[before:]


def test_message_after_the_close_its_stop_sends_no_second_card(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    cards_before = [m for m in rig.sent if m.get("reply_to_message_id") == FIRST
                    and not m.get("rich")]
    before = len(rig.sent)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Hi there.")
    late_cards = [m for m in rig.sent[before:] if m.get("reply_to_message_id") == FIRST
                  and not m.get("rich") and m.get("reply_markup") is not None]
    assert cards_before and late_cards == []


# -- the second message, while the first message's turn runs (control) -------

def test_second_message_during_the_first_turn_is_queued(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    _pickup(rig, SECOND, "and another")
    try:
        assert SECOND in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


def test_second_message_during_the_first_turn_has_its_line_armed(rig):
    _message_after_close(rig)
    _submit(rig, "hello")
    _pickup(rig, SECOND, "and another")
    try:
        assert _armed(rig.sess, SECOND)
    finally:
        _cancel_timers(rig.sess)


def test_second_message_during_the_first_turn_logs_queued_while_busy(rig, caplog):
    _message_after_close(rig)
    _submit(rig, "hello")
    try:
        with caplog.at_level(logging.INFO):
            _pickup(rig, SECOND, "and another")
        assert f"queued while busy: [{SECOND}]" in caplog.text
    finally:
        _cancel_timers(rig.sess)


# -- a message held while the prompt is open --------------------------------

def _held(rig, monkeypatch):
    injected = []

    async def _inject(sess, text, *a, **k):
        injected.append(text)
        return True

    monkeypatch.setattr(rig.bot, "_inject_prompt", _inject)
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    msg = _ask(rig)
    sess = rig.sess
    sess.queue_prompt("also check the docs", 41)
    sess.dialog_hold = {"entered_at": sess.interactive_entered_at,
                        "told": set(), "notices": []}
    return msg, injected


def test_held_message_is_marked_answered_when_a_tap_closes_the_prompt(rig, monkeypatch):
    msg, _ = _held(rig, monkeypatch)
    rig.tap(msg, "allow")
    assert rig.sess.dialog_hold["closed"] == "answered"


@pytest.mark.parametrize("how", ["tap", "terminal"])
def test_held_message_is_not_injected_while_the_prompt_is_open(rig, monkeypatch,
                                                               run_async, how):
    _msg, injected = _held(rig, monkeypatch)
    run_async(rig.bot.notify(rig.sess, "dialog_hold_settle", {}))
    assert injected == []


def test_held_message_goes_out_with_the_terminal_close_itself(rig, monkeypatch):
    """No settle call needed: the terminal answer releases it."""
    _msg, injected = _held(rig, monkeypatch)
    rig.timeout(REQ)
    _agent_post(rig, BG, CMD, "toolu_bg1")
    assert injected == ["also check the docs"]


@pytest.mark.parametrize("how", ["tap", "terminal"])
def test_held_message_is_injected_after_the_close(rig, monkeypatch, run_async, how):
    msg, injected = _held(rig, monkeypatch)
    if how == "tap":
        rig.tap(msg, "allow")
    else:
        rig.timeout(REQ)
        _agent_post(rig, BG, CMD, "toolu_bg1")
    run_async(rig.bot.notify(rig.sess, "dialog_hold_settle", {}))
    assert injected == ["also check the docs"]


# -- a Telegram message picked up while the prompt is still open -------------

def test_message_picked_up_during_the_open_prompt_keeps_the_prompt_open(rig):
    _after_stop(rig)
    _ask(rig)
    _pickup(rig, FIRST, "hello")
    assert len(rig.sess.open_prompts()) == 1


def test_message_during_the_open_prompt_then_submit_gets_a_card(rig):
    _after_stop(rig)
    _ask(rig)
    _pickup(rig, FIRST, "hello")
    before = len(rig.sent)
    _submit(rig, "hello")
    assert any(m.get("reply_to_message_id") == FIRST for m in rig.sent[before:]), \
        rig.sent[before:]


# -- a prompt typed at the terminal while the agent's prompt is open --------

def _terminal_prompt_during_wait(rig):
    _after_stop(rig)
    msg = _ask(rig)
    sess = rig.sess
    seq = sess.turn_seq
    seen = {}
    real = rig.recv.notify_fn

    async def _notify(s, event, context):
        if event == "user_prompt_submit":
            seen["status"] = s.status
            seen["open"] = len(s.open_prompts())
        await real(s, event, context)

    rig.recv.notify_fn = _notify
    rig.hook("UserPromptSubmit", prompt="and update the changelog")
    return msg, seq, seen


def test_terminal_prompt_during_the_wait_fires_user_prompt_submit(rig):
    _msg, _seq, seen = _terminal_prompt_during_wait(rig)
    assert seen == {"status": Status.BUSY, "open": 1}


def test_terminal_prompt_during_the_wait_starts_a_turn(rig):
    _msg, seq, _seen = _terminal_prompt_during_wait(rig)
    assert rig.sess.turn_seq == seq + 1


def test_terminal_prompt_during_the_wait_keeps_the_agents_prompt_open(rig):
    _terminal_prompt_during_wait(rig)
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [BG]


def test_terminal_prompt_during_the_wait_stays_interactive(rig):
    _terminal_prompt_during_wait(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_terminal_prompt_during_the_wait_then_tap_makes_it_busy(rig):
    msg, _seq, _seen = _terminal_prompt_during_wait(rig)
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


# -- the agent asks, then Claude itself resumes ------------------------------

def test_claudes_tool_call_during_the_wait_makes_the_close_busy(rig):
    """Control: Claude's own visible activity means its turn runs again."""
    _after_stop(rig)
    msg = _ask(rig)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_main")
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


def test_continuation_during_the_wait_makes_the_close_busy(rig):
    """Control: the agent's task-notification wakes Claude while its
    prompt is open; the close then belongs to a running turn."""
    _after_stop(rig)
    msg = _ask(rig)
    rig.hook("UserPromptSubmit",
             prompt="<task-notification><task-id>x</task-id></task-notification>")
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


def test_continuation_after_the_close_makes_the_session_busy(rig):
    _close(rig, "terminal-tool-ran")
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    rig.hook("UserPromptSubmit",
             prompt="<task-notification><task-id>x</task-id></task-notification>")
    assert rig.sess.status == Status.BUSY


def test_continuation_after_the_close_then_stop_ends_idle(rig):
    _close(rig, "terminal-tool-ran")
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    rig.hook("UserPromptSubmit",
             prompt="<task-notification><task-id>x</task-id></task-notification>")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="All done.")
    assert rig.sess.status == Status.IDLE


def test_continuation_after_the_close_then_stop_closes_the_job(rig):
    _close(rig, "terminal-tool-ran")
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    rig.hook("UserPromptSubmit",
             prompt="<task-notification><task-id>x</task-id></task-notification>")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="All done.")
    assert not rig.sess.job_background_open()


# -- the field sequence, end to end -----------------------------------------

def test_field_order_with_the_empty_notification_then_a_message_gets_its_card(rig):
    """Stop, the agent's PreToolUse and PermissionRequest, the hook's
    timeout, the Notification, the terminal answer (PostToolUse), then a
    Telegram message: its card replies to it at UserPromptSubmit."""
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    _ask(rig)
    rig.timeout(REQ)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission", agent_id=BG)
    _agent_post(rig, BG, CMD, "toolu_bg1")
    _pickup(rig, FIRST, "hello")
    before = len(rig.sent)
    _submit(rig, "hello")
    assert any(m.get("reply_to_message_id") == FIRST for m in rig.sent[before:])
