"""A carried prompt that closes during the Stop-carry window (fixes-3,
rev-iter2-001).

Claude's Stop with a background agent's prompt open: for a moment the
session is IDLE (the turn's answer goes out) and the agent's prompt is
carried over to the wait that follows. If, in that moment, the prompt
closes (its tool ends, the agent makes its next tool call, or the agent
stops), aipager notices: it does not resume the wait when nothing is
open, it never sends the closed prompt again, and no button of it types
anything or sends a hook decision. A prompt that stays open is carried
and still answers.

Two shapes: the agent's prompt inline on the card (it came first) and
the agent's prompt as its own message (Claude's question came first).
The window is reached at the Telegram send boundary: the turn's answer
is sent while the session is IDLE, and the close is played then, as a
datagram arriving during a real network send would be.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, OTHER, S, answers

REQ = "req-bg"
BASH = {"command": "make", "description": "make"}
CLOSES = ["tool-end", "tool-failure", "next-tool", "agent-stop"]


def extra_ids(rig):
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def bg_asks(rig):
    """Claude Code's order for an agent's Bash: its PreToolUse, then its
    PermissionRequest (parked on a hook reply channel)."""
    rig.hook("PreToolUse", tool_name="Bash", tool_input=BASH, tool_use_id="toolu_bg_bash",
             agent_id=BG, agent_type="ship-reviewer")
    rig.hook("PermissionRequest", tool_name="Bash", tool_input=BASH, agent_id=BG,
             agent_type="ship-reviewer", aipager_reply_addr="/nonexistent/reply-bg.sock",
             aipager_request_id=REQ)


def on_card(rig, *, also=None):
    """The agent's prompt first (inline on the card), then Claude's
    question (its own message), answered. Returns the agent's surface."""
    rig.launch(BG)
    if also:
        rig.launch(also, agent_type="ship-tester")
    bg_asks(rig)
    rig.question()
    if also:
        rig.agent_bash(also, "pytest", request_id="req-other")
    question_msg = [m for m in rig.prompt_messages()
                    if any("Only the card" in b for b in rig.buttons(m))][0]["msg_id"]
    rig.tap(question_msg, "opt0")
    return CARD


def own_message(rig, *, also=None):
    """Claude's question first (on the card), then the agent's prompt as
    its own message; the question answered. Returns the agent's message."""
    rig.launch(BG)
    if also:
        rig.launch(also, agent_type="ship-tester")
    rig.question()
    bg_asks(rig)
    if also:
        rig.agent_bash(also, "pytest", request_id="req-other")
    agent_msg = [m for m in rig.prompt_messages() if "make" in (m.get("text") or "")][0]
    rig.tap(CARD, "opt0")
    return agent_msg["msg_id"]


SHAPES = {"card-prompt": on_card, "own-message": own_message}


def close_payload(kind):
    base = {"session": S, "transcript_path": "", "agent_id": BG,
            "agent_type": "ship-reviewer"}
    if kind == "tool-end":
        return dict(base, hook_event_name="PostToolUse", tool_name="Bash", tool_input=BASH,
                    tool_use_id="toolu_bg_bash", tool_response={"stdout": "ok"})
    if kind == "tool-failure":
        return dict(base, hook_event_name="PostToolUseFailure", tool_name="Bash",
                    tool_input=BASH, tool_use_id="toolu_bg_bash",
                    error="The user doesn't want to proceed with this tool use.")
    if kind == "next-tool":
        return dict(base, hook_event_name="PreToolUse", tool_name="Read",
                    tool_input={"file_path": "/src/a.py"}, tool_use_id="toolu_bg_read")
    return dict(base, hook_event_name="SubagentStop")


def in_window(rig, kind):
    """Play *kind* (or nothing for None) on the first Telegram send made
    while the session is IDLE. Returns a dict: ``fired`` and ``status``
    (the session status at that moment)."""
    real = rig.bot._app.bot.send_message.side_effect
    box: dict = {}

    async def send(*args, **kwargs):
        if "fired" not in box and rig.sess.status == Status.IDLE:
            box["fired"] = True
            box["status"] = rig.sess.status
            if kind is not None:
                rig.recv._recent_fingerprints.clear()
                payload = close_payload(kind)
                await asyncio.wait_for(
                    rig.recv._on_datagram(json.dumps(payload).encode()), 2.0)
        return await real(*args, **kwargs)

    rig.bot._app.bot.send_message.side_effect = send
    return box


def stop(rig):
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Waiting on the agent.")


def make_prompts(rig):
    """Messages carrying the agent's `make` prompt with buttons."""
    return [m for m in rig.prompt_messages() if "make" in (m.get("text") or "")]


def run(rig, shape, kind, **kw):
    surface = SHAPES[shape](rig, **kw)
    before = len(make_prompts(rig))
    box = in_window(rig, kind)
    stop(rig)
    rig.keys.clear()  # the question's answer typed keys before the window
    return surface, before, box


def all_surfaces(rig, surface):
    """Every surface the agent's prompt was ever shown on."""
    return [surface] + [m["msg_id"] for m in make_prompts(rig) if m["msg_id"] != surface]


# -- precondition: the close really lands inside the window ----------------------------------------

@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_close_is_played_while_the_session_is_idle(rig, shape, kind):
    _s, _b, box = run(rig, shape, kind)
    assert box.get("status") == Status.IDLE


# -- the closed carried prompt is noticed -------------------------------------------------------------

@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_wait_not_resumed_when_nothing_is_open(rig, shape, kind):
    run(rig, shape, kind)
    assert rig.sess.status != Status.INTERACTIVE


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_session_idle_after_the_window(rig, shape, kind):
    run(rig, shape, kind)
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_nothing_open_after_the_window(rig, shape, kind):
    run(rig, shape, kind)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_no_dialog_open_after_the_window(rig, shape, kind):
    run(rig, shape, kind)
    assert rig.sess.dialog_is_open() is False


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_carried_prompt_is_never_sent_again(rig, shape, kind):
    _s, before, _box = run(rig, shape, kind)
    assert len(make_prompts(rig)) == before


# -- no button of the closed prompt types anything ----------------------------------------------------

@pytest.mark.parametrize("verb", ["allow", "deny"])
@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_carried_prompt_button_types_nothing(rig, shape, kind, verb):
    surface, _b, _box = run(rig, shape, kind)
    for mid in all_surfaces(rig, surface):
        rig.tap(mid, verb)
    assert rig.keys == []


@pytest.mark.parametrize("verb", ["allow", "deny"])
@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_carried_prompt_button_sends_no_hook_decision(rig, shape, kind, verb):
    surface, _b, _box = run(rig, shape, kind)
    for mid in all_surfaces(rig, surface):
        rig.tap(mid, verb)
    assert rig.decisions == []


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_carried_prompt_button_already_answered(rig, shape, kind):
    surface, _b, _box = run(rig, shape, kind)
    q = rig.tap(surface, "allow")
    assert answers(q) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_carried_prompt_types_nothing_after_its_hook_gave_up(rig, shape, kind):
    """Error guessing: the agent's hook gave up before the Stop, so a tap
    could only answer by keys. The prompt closed in the window, so keys
    would land in Claude's input line or another dialog."""
    surface = SHAPES[shape](rig)
    rig.timeout(REQ)
    in_window(rig, kind)
    stop(rig)
    rig.keys.clear()
    rig.hook_ok = False
    for mid in all_surfaces(rig, surface):
        rig.tap(mid, "allow")
    assert rig.keys == []


# -- control: a prompt still open is carried and answers ----------------------------------------------

@pytest.mark.parametrize("shape", SHAPES)
def test_open_carried_prompt_resumes_the_wait(rig, shape):
    run(rig, shape, None)
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("shape", SHAPES)
def test_open_carried_prompt_stays_open(rig, shape):
    run(rig, shape, None)
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [BG]


def test_open_card_prompt_is_sent_again(rig):
    _s, before, _box = run(rig, "card-prompt", None)
    assert len(make_prompts(rig)) == before + 1


def test_open_own_message_prompt_is_not_sent_again(rig):
    _s, before, _box = run(rig, "own-message", None)
    assert len(make_prompts(rig)) == before


@pytest.mark.parametrize("shape", SHAPES)
def test_open_carried_prompt_answers_by_hook(rig, shape):
    surface, _b, _box = run(rig, shape, None)
    target = make_prompts(rig)[-1]["msg_id"] if shape == "card-prompt" else surface
    rig.tap(target, "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]


@pytest.mark.parametrize("shape", SHAPES)
def test_open_carried_prompt_typed_once_its_hook_gave_up(rig, shape):
    """The only open dialog, its hook gone: keys may answer it."""
    surface = SHAPES[shape](rig)
    rig.timeout(REQ)
    in_window(rig, None)
    stop(rig)
    rig.keys.clear()
    rig.hook_ok = False
    target = make_prompts(rig)[-1]["msg_id"] if shape == "card-prompt" else surface
    rig.tap(target, "allow")
    assert rig.keys != []


# -- one of two carried prompts closes in the window ----------------------------------------------------

def other_msg(rig):
    return [m for m in rig.prompt_messages() if "pytest" in (m.get("text") or "")][-1]["msg_id"]


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_other_carried_prompt_keeps_the_wait(rig, shape, kind):
    run(rig, shape, kind, also=OTHER)
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_only_the_other_prompt_stays_open(rig, shape, kind):
    run(rig, shape, kind, also=OTHER)
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [OTHER]


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_one_not_sent_again_beside_an_open_one(rig, shape, kind):
    _s, before, _box = run(rig, shape, kind, also=OTHER)
    assert len(make_prompts(rig)) == before


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_closed_one_types_nothing_beside_an_open_one(rig, shape, kind):
    surface, _b, _box = run(rig, shape, kind, also=OTHER)
    rig.hook_ok = False
    for mid in all_surfaces(rig, surface):
        rig.tap(mid, "allow")
    assert (rig.keys, rig.decisions) == ([], [])


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_other_prompt_answers_by_hook(rig, shape, kind):
    run(rig, shape, kind, also=OTHER)
    rig.tap(other_msg(rig), "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [("req-other", "allow")]


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_other_prompt_typed_once_its_hook_gave_up(rig, shape, kind):
    """The closed one is gone from the terminal, so the other one is the
    only dialog left: its keys are safe."""
    SHAPES[shape](rig, also=OTHER)
    rig.timeout("req-other")
    in_window(rig, kind)
    stop(rig)
    rig.keys.clear()
    rig.hook_ok = False
    rig.tap(other_msg(rig), "allow")
    assert rig.keys != []


@pytest.mark.parametrize("kind", CLOSES)
@pytest.mark.parametrize("shape", SHAPES)
def test_other_prompt_answer_leaves_nothing_open(rig, shape, kind):
    run(rig, shape, kind, also=OTHER)
    rig.tap(other_msg(rig), "allow")
    assert rig.sess.dialog_is_open() is False
