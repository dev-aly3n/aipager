"""Black-box: a prompt that names no agent (no agent_id; known only from
Claude Code's Notification, with or without a tool in its text) raised
while aipager shows the session IDLE. Written from design.md,
entrypoints.md and the iteration-3 brief only.

Promise under test (iteration 3):
- with NO background agent running, nobody but Claude's own turn can own
  the dialog: it is shown as its own message, and when it closes the
  session goes BUSY, so a Telegram message picked up next is queued;
- while a background agent runs, the prompt may be the agent's: it is
  still its own message, and it closes to IDLE (a message picked up next
  is a new turn with its card under it).

Boundaries: an agent that ended just before the prompt (after the Stop,
before the Stop, a foreground one), two agents where one or both ended,
an agent from an earlier turn still running, a phantom SubagentStop, and
an unnamed prompt followed by an agent's named one in the same wait.
"""

from __future__ import annotations

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, GROUP, OTHER

REQ_BG = "req-bg-1"
CMD_BG = "touch marker-a"
FIRST = 77
EARLIER = 60
M_TOOL = "Claude needs your permission to use Bash"
M_BARE = "Claude needs your permission"
PHANTOM = "ffffffffffffffff0"


def _stop(rig, text="Done for now."):
    rig.hook("Stop", stop_hook_active=False, last_assistant_message=text)


def _agent_stop(rig, agent, agent_type="ship-reviewer"):
    rig.hook("SubagentStop", agent_id=agent, agent_type=agent_type)


def _ctx(rig, ctx):
    """Bring the session to IDLE with the agents of *ctx*."""
    if ctx == "no-agent":
        _stop(rig)
    elif ctx == "ended-after-stop":
        rig.launch(BG)
        _stop(rig, "The agent runs in the background.")
        _agent_stop(rig, BG)
    elif ctx == "ended-before-stop":
        rig.launch(BG)
        _agent_stop(rig, BG)
        _stop(rig)
    elif ctx == "foreground-ended":
        rig.launch(FG, background=False)
        _agent_stop(rig, FG)
        rig.hook("PostToolUse", tool_name="Agent",
                 tool_input={"subagent_type": "ship-reviewer",
                             "description": "Review", "prompt": "Review it"},
                 tool_use_id=f"toolu_launch_{FG}",
                 tool_response={"status": "completed", "content": "ok"})
        _stop(rig)
    elif ctx == "both-of-two-ended":
        rig.launch(BG)
        rig.launch(OTHER)
        _stop(rig, "Two agents run in the background.")
        _agent_stop(rig, OTHER)
        _agent_stop(rig, BG)
    elif ctx == "running":
        rig.launch(BG)
        _stop(rig, "The agent runs in the background.")
    elif ctx == "one-of-two-ended":
        rig.launch(BG)
        rig.launch(OTHER)
        _stop(rig, "Two agents run in the background.")
        _agent_stop(rig, OTHER)
    elif ctx == "earlier-turn":
        # Turn 1 launches BG and ends; turn 2 (a Telegram message) ends
        # too; BG from turn 1 is still running.
        rig.launch(BG)
        _stop(rig, "The agent runs in the background.")
        _pickup(rig, EARLIER, "status?")
        rig.hook("UserPromptSubmit", prompt="[via Telegram @bob]\nstatus?")
        _stop(rig, "Still waiting on the agent.")
    elif ctx == "phantom-subagent-stop":
        rig.launch(BG)
        _stop(rig, "The agent runs in the background.")
        rig.hook("SubagentStop", agent_id=PHANTOM, agent_type="")
    else:
        raise AssertionError(ctx)


NO_AGENT = ["no-agent", "ended-after-stop", "ended-before-stop",
            "foreground-ended", "both-of-two-ended"]
AGENT_RUNS = ["running", "one-of-two-ended", "earlier-turn",
              "phantom-subagent-stop"]
ALL = NO_AGENT + AGENT_RUNS
MSGS = [M_TOOL, M_BARE]
MSG_IDS = ["tool-named", "no-tool-info"]


def _new_prompts(rig, before):
    return [m for m in rig.sent[before:] if m.get("reply_markup") is not None]


def _unnamed(rig, message=M_TOOL):
    before = len(rig.sent)
    rig.hook("Notification", notification_type="permission_prompt",
             message=message)
    return _new_prompts(rig, before)


def _only(msgs):
    assert len(msgs) == 1, msgs
    return msgs[0]["msg_id"]


def _pickup(rig, msg_id, text="hello"):
    rig.hook("queue_pickup", consumed=[{"msg_id": msg_id, "chat_id": GROUP,
                                        "raw_text": text}], expired=[])


def _submit(rig, text="hello"):
    rig.hook("UserPromptSubmit", prompt=f"[via Telegram @bob]\n{text}")


def _queued_ids(sess):
    return [t["msg_id"] if isinstance(t, dict) else getattr(t, "msg_id", t)
            for t in (sess.queued_targets or [])]


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


def _card_edits_with_buttons(rig):
    return [e for e in rig.card_edits if e[0] == CARD and e[2] is not None]


def _closed(rig, ctx, verb="allow", message=M_TOOL):
    _ctx(rig, ctx)
    msg = _only(_unnamed(rig, message))
    rig.tap(msg, verb)
    return msg


# -- preconditions ------------------------------------------------------------

@pytest.mark.parametrize("ctx", ALL)
def test_precondition_the_session_is_idle_before_the_prompt(rig, ctx):
    _ctx(rig, ctx)
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("ctx", AGENT_RUNS)
def test_precondition_the_job_still_waits_on_an_agent(rig, ctx):
    _ctx(rig, ctx)
    assert rig.sess.job_background_open()


# -- visible as its own message in every context --------------------------------

@pytest.mark.parametrize("message", MSGS, ids=MSG_IDS)
@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_from_idle_is_one_new_message(rig, ctx, message):
    _ctx(rig, ctx)
    assert len(_unnamed(rig, message)) == 1


@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_from_idle_goes_to_the_sessions_chat(rig, ctx):
    _ctx(rig, ctx)
    assert [m["chat_id"] for m in _unnamed(rig)] == [GROUP]


@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_from_idle_has_allow_and_deny(rig, ctx):
    _ctx(rig, ctx)
    names = rig.buttons(_unnamed(rig)[0])
    assert any("Allow" in n for n in names) and any("Deny" in n for n in names), names


@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_from_idle_puts_no_buttons_on_the_card(rig, ctx):
    _ctx(rig, ctx)
    _unnamed(rig)
    assert _card_edits_with_buttons(rig) == []


@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_from_idle_makes_the_session_interactive(rig, ctx):
    _ctx(rig, ctx)
    _unnamed(rig)
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("ctx", ALL)
def test_unnamed_prompt_tap_types_the_answer(rig, ctx):
    """No hook reply channel: the answer can only be typed."""
    _closed(rig, ctx)
    assert rig.keys != []


# -- no agent running: Claude's own, closes to BUSY -------------------------------

@pytest.mark.parametrize("message", MSGS, ids=MSG_IDS)
@pytest.mark.parametrize("verb", ["allow", "deny"])
@pytest.mark.parametrize("ctx", NO_AGENT)
def test_no_agent_running_unnamed_prompt_closes_to_busy(rig, ctx, verb, message):
    _closed(rig, ctx, verb, message)
    assert rig.sess.status == Status.BUSY


@pytest.mark.parametrize("ctx", NO_AGENT)
def test_no_agent_running_close_leaves_no_prompt_open(rig, ctx):
    _closed(rig, ctx)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("ctx", NO_AGENT)
def test_no_agent_running_message_after_close_is_queued(rig, ctx):
    _closed(rig, ctx)
    _pickup(rig, FIRST)
    try:
        assert FIRST in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("ctx", NO_AGENT)
def test_no_agent_running_message_after_close_has_its_line_armed(rig, ctx):
    _closed(rig, ctx)
    _pickup(rig, FIRST)
    try:
        assert _armed(rig.sess, FIRST)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("ctx", NO_AGENT)
def test_no_agent_running_message_after_close_logs_queued_while_busy(rig, ctx, caplog):
    _closed(rig, ctx)
    try:
        with caplog.at_level("INFO"):
            _pickup(rig, FIRST)
        assert f"queued while busy: [{FIRST}]" in caplog.text
    finally:
        _cancel_timers(rig.sess)


# -- an agent running: may be the agent's, closes to IDLE -------------------------

@pytest.mark.parametrize("message", MSGS, ids=MSG_IDS)
@pytest.mark.parametrize("verb", ["allow", "deny"])
@pytest.mark.parametrize("ctx", AGENT_RUNS)
def test_agent_running_unnamed_prompt_closes_to_idle(rig, ctx, verb, message):
    _closed(rig, ctx, verb, message)
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("ctx", AGENT_RUNS)
def test_agent_running_close_keeps_the_job_open(rig, ctx):
    _closed(rig, ctx)
    assert rig.sess.job_background_open()


@pytest.mark.parametrize("ctx", AGENT_RUNS)
def test_agent_running_message_after_close_is_not_queued(rig, ctx):
    _closed(rig, ctx)
    _pickup(rig, FIRST)
    try:
        assert FIRST not in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("ctx", AGENT_RUNS)
def test_agent_running_message_after_close_gets_its_card(rig, ctx):
    _closed(rig, ctx)
    _pickup(rig, FIRST)
    before = len(rig.sent)
    try:
        _submit(rig)
        assert any(m.get("reply_to_message_id") == FIRST for m in rig.sent[before:])
    finally:
        _cancel_timers(rig.sess)


# -- Claude's own activity during an unnamed wait: its turn runs ------------------

@pytest.mark.parametrize("ctx", ["no-agent", "running"])
def test_claudes_tool_call_after_an_unnamed_prompt_is_not_idle(rig, ctx):
    _ctx(rig, ctx)
    _unnamed(rig)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_main2")
    rig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_main2", tool_response={"ok": True})
    assert rig.sess.status != Status.IDLE


# -- unnamed, then an agent's named prompt in the same wait -----------------------

def _unnamed_then_named(rig, ctx):
    _ctx(rig, ctx)
    first = _unnamed(rig)
    before = len(rig.sent)
    rig.agent_bash(BG, CMD_BG, request_id=REQ_BG)
    return first, _new_prompts(rig, before)


MIXED = ["running", "one-of-two-ended"]


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_both_raised_is_interactive(rig, ctx):
    _unnamed_then_named(rig, ctx)
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_puts_no_buttons_on_the_card(rig, ctx):
    _unnamed_then_named(rig, ctx)
    assert _card_edits_with_buttons(rig) == []


def _answer_all(rig, first, second, order):
    """Tap every prompt message. With two open, a tap on the one aipager
    cannot place on top may be refused (answer in the terminal); "sent"
    taps the unnamed one first, then the named one, then the unnamed one
    again once it is alone."""
    msgs = [m["msg_id"] for m in first + second]
    taps = msgs + msgs[:1] if order == "sent" else msgs[::-1]
    for msg in taps:
        rig.tap(msg, "allow")


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_two_prompts_are_open(rig, ctx):
    _unnamed_then_named(rig, ctx)
    assert len(rig.sess.open_prompts()) == 2


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_each_is_its_own_message(rig, ctx):
    first, second = _unnamed_then_named(rig, ctx)
    assert len(first) == 1 and len(second) == 1 and first[0]["msg_id"] != second[0]["msg_id"]


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_answering_the_named_one_keeps_the_unnamed_open(rig, ctx):
    _first, second = _unnamed_then_named(rig, ctx)
    rig.tap(second[0]["msg_id"], "allow")
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("order", ["sent", "reversed"])
@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_all_answered_ends_idle(rig, ctx, order):
    first, second = _unnamed_then_named(rig, ctx)
    _answer_all(rig, first, second, order)
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("order", ["sent", "reversed"])
@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_all_answered_leaves_none_open(rig, ctx, order):
    first, second = _unnamed_then_named(rig, ctx)
    _answer_all(rig, first, second, order)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_the_agents_request_is_answered(rig, ctx):
    """The agent's dialog gets an answer: by its hook (a decision for its
    request id) or, if it shares the unnamed prompt's message, by keys."""
    first, second = _unnamed_then_named(rig, ctx)
    _answer_all(rig, first, second, "reversed")
    ids = [r.get("aipager_request_id") or r.get("request_id") for r, _d in rig.decisions]
    assert REQ_BG in ids or rig.keys != [], (rig.decisions, rig.keys)


@pytest.mark.parametrize("ctx", MIXED)
def test_unnamed_then_named_message_after_both_closed_is_not_queued(rig, ctx):
    first, second = _unnamed_then_named(rig, ctx)
    _answer_all(rig, first, second, "reversed")
    _pickup(rig, FIRST)
    try:
        assert FIRST not in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


def test_named_then_unnamed_notification_draws_no_second_prompt(rig):
    """The agent's PermissionRequest, the hook gives up, then a Notification
    that names no agent: the same dialog, so no second prompt message."""
    _ctx(rig, "running")
    rig.agent_bash(BG, CMD_BG, request_id=REQ_BG)
    rig.timeout(REQ_BG)
    assert _unnamed(rig) == []


def test_named_then_unnamed_notification_tap_ends_idle(rig):
    _ctx(rig, "running")
    before = len(rig.sent)
    rig.agent_bash(BG, CMD_BG, request_id=REQ_BG)
    msg = _only(_new_prompts(rig, before))
    rig.timeout(REQ_BG)
    _unnamed(rig)
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.IDLE
