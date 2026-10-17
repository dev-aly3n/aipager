"""The moment Claude's Stop is delivered while a background agent's
prompt stays open (fixes-2, rev-iter1-002). For that moment the session
is IDLE (the turn's answer goes out) and the agent's prompt is carried
over to the wait that follows.

- ``dialog_is_open()`` stays true through it (entrypoints.md);
- a prompt arriving then is one more open prompt, shown with its buttons;
- a tap then refuses with the "finishing its turn" pop-up, types nothing,
  sends no hook decision and keeps its buttons; once the wait resumed the
  same button answers.

The moment is reached at the Telegram send boundary: the answer's
sendMessage (made while the session is IDLE) lets a concurrent tap or
hook datagram run, as a real network send would.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, BOB, CARD, GROUP, OTHER, S, answers

SETTLING = "Claude is finishing its turn, so tap this prompt again in a moment."
REQ = "req-bg"


def extra_ids(rig):
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def during_carry(rig, action):
    """Run *action()* (a coroutine factory) concurrently with the first
    Telegram send made while the session is IDLE. Returns a dict with
    ``fired``, ``open`` (dialog_is_open() then) and ``result``."""
    real = rig.bot._app.bot.send_message.side_effect
    box: dict = {}

    async def send(*args, **kwargs):
        if action is not None and "fired" not in box and rig.sess.status == Status.IDLE:
            box["fired"] = True
            box["open"] = rig.sess.dialog_is_open()
            task = asyncio.ensure_future(action())
            done, _ = await asyncio.wait({task}, timeout=2.0)
            box["result"] = task.result() if done else None
        elif action is None and "fired" not in box and rig.sess.status == Status.IDLE:
            box["fired"] = True
            box["open"] = rig.sess.dialog_is_open()
        return await real(*args, **kwargs)

    rig.bot._app.bot.send_message.side_effect = send
    return box


def tap_later(rig, msg_id, verb="allow"):
    q = MagicMock()
    q.data = f"{S}:{verb}"
    q.from_user = MagicMock(id=BOB)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=GROUP)
    q.message.chat_id = GROUP
    q.message.message_id = msg_id
    q.message.text = "🔐 api · Permission needed"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None

    async def go():
        await rig.bot._handle_callback(u, MagicMock())
        return q

    return go


def stop(rig):
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Waiting on the agent.")


def agent_on_card(rig):
    """agent-first: the agent's prompt inline on the card; Claude's
    question answered. Returns the agent prompt's surface (the card)."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ)
    rig.question()
    rig.tap(extra_ids(rig)[0], "opt0")
    return CARD


def agent_as_message(rig):
    """claude-first: the agent's prompt is its own message; Claude's
    question (on the card) answered. Returns the agent's message id."""
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make", request_id=REQ)
    agent_msg = extra_ids(rig)[0]
    rig.tap(CARD, "opt0")
    return agent_msg


SETUPS = {"on-card": agent_on_card, "own-message": agent_as_message}


# -- the moment itself ------------------------------------------------------------------------

def test_the_answer_goes_out_while_idle(rig):
    """Precondition for the rest of this file: the carry moment exists."""
    agent_on_card(rig)
    box = during_carry(rig, None)
    stop(rig)
    assert box.get("fired") is True


@pytest.mark.parametrize("setup", SETUPS)
def test_dialog_is_open_during_the_carry(rig, setup):
    SETUPS[setup](rig)
    box = during_carry(rig, None)
    stop(rig)
    assert box.get("open") is True


# -- a tap during the carry ------------------------------------------------------------------

@pytest.mark.parametrize("setup", SETUPS)
def test_tap_during_carry_shows_the_settling_popup(rig, setup):
    surface = SETUPS[setup](rig)
    box = during_carry(rig, tap_later(rig, surface))
    stop(rig)
    assert answers(box["result"]) == [SETTLING]


@pytest.mark.parametrize("setup", SETUPS)
def test_tap_during_carry_types_nothing(rig, setup):
    surface = SETUPS[setup](rig)
    typed = None

    def act():
        nonlocal typed
        typed = list(rig.keys)
        return tap_later(rig, surface)()

    during_carry(rig, act)
    stop(rig)
    assert rig.keys == typed


@pytest.mark.parametrize("setup", SETUPS)
def test_tap_during_carry_sends_no_hook_decision(rig, setup):
    surface = SETUPS[setup](rig)
    during_carry(rig, tap_later(rig, surface))
    stop(rig)
    assert rig.decisions == []


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_during_carry_keeps_its_buttons(rig, verb):
    surface = agent_as_message(rig)
    box = during_carry(rig, tap_later(rig, surface, verb))
    stop(rig)
    q = box["result"]
    assert (q.edit_message_reply_markup.await_count, q.edit_message_text.await_count) == (0, 0)


def test_tap_during_carry_keeps_the_prompt_open(rig):
    surface = agent_as_message(rig)
    during_carry(rig, tap_later(rig, surface))
    stop(rig)
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [BG]


def test_tap_during_carry_session_waits_after(rig):
    surface = agent_as_message(rig)
    during_carry(rig, tap_later(rig, surface))
    stop(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_same_button_answers_once_the_wait_resumed(rig):
    surface = agent_as_message(rig)
    during_carry(rig, tap_later(rig, surface))
    stop(rig)
    rig.tap(surface, "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]


def test_card_prompt_answerable_from_its_new_message_after_a_refused_tap(rig):
    agent_on_card(rig)
    during_carry(rig, tap_later(rig, CARD))
    stop(rig)
    rig.tap(rig.prompt_messages()[-1]["msg_id"], "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]


# -- a prompt arriving during the carry ---------------------------------------------------------

def other_asks(rig):
    async def go():
        rig.recv._recent_fingerprints.clear()
        payload = {"session": S, "hook_event_name": "PermissionRequest",
                   "transcript_path": "", "tool_name": "Bash",
                   "tool_input": {"command": "pytest", "description": "pytest"},
                   "agent_id": OTHER, "agent_type": "ship-tester",
                   "aipager_reply_addr": "/nonexistent/reply-other.sock",
                   "aipager_request_id": "req-other"}
        await rig.recv._on_datagram(json.dumps(payload).encode())

    return go


def two_agents_carry(rig, setup="on-card"):
    rig.launch(OTHER, agent_type="ship-tester")
    SETUPS[setup](rig)
    before = len(rig.prompt_messages())
    during_carry(rig, other_asks(rig))
    stop(rig)
    return before


@pytest.mark.parametrize("setup", SETUPS)
def test_prompt_arriving_during_carry_is_one_more(rig, setup):
    two_agents_carry(rig, setup)
    assert sorted(p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()) == sorted([BG, OTHER])


@pytest.mark.parametrize("setup", SETUPS)
def test_prompt_arriving_during_carry_session_waits(rig, setup):
    two_agents_carry(rig, setup)
    assert rig.sess.status == Status.INTERACTIVE


def test_prompt_arriving_during_carry_is_shown_with_buttons(rig):
    before = two_agents_carry(rig, "own-message")
    new = rig.prompt_messages()[before:]
    assert any("pytest" in (m.get("text") or "") and
               any("Allow" in b for b in rig.buttons(m)) for m in new)


def test_prompt_arriving_during_carry_answerable_by_hook(rig):
    before = two_agents_carry(rig, "own-message")
    new = [m for m in rig.prompt_messages()[before:] if "pytest" in (m.get("text") or "")]
    rig.tap(new[0]["msg_id"], "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [("req-other", "allow")]
