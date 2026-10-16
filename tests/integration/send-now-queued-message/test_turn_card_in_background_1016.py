"""A message that starts a turn never holds the next update back while its
busy card waits to be sent (fake-Telegram e2e, 2026-10-15).

PTB handles updates one at a time. The handler of a message that STARTED a
turn used to wait for the turn's busy card, a paced ornament: in a group
whose send window was full it waited 11 to 35 s for a slot, and the Allow
tap on the permission prompt Claude had already asked waited behind it.
Here the card's send is held (a seam on ``bot.send_busy``, released by an
``asyncio.Event``): the message's handler must return at once, and the
next update, the tap on Allow, must be handled while the card still waits.
The card itself still goes out, under the message, once its send can.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import session_parity
from aipager.state import Status
from tests.card_tasks import drain_card_tasks

CHAT = 256113222


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _held_card_send(bot):
    """Hold every busy-card send until the returned event is set. *reached*
    records the turn of each send that reached the seam, *sent* the card id
    each one returned."""
    gate = asyncio.Event()
    reached: list[int] = []
    sent: list[int] = []
    real = bot.send_busy

    async def held(sess, *args, **kwargs):
        reached.append(sess.turn_seq)
        await gate.wait()
        msg_id = await real(sess, *args, **kwargs)
        sent.append(msg_id)
        return msg_id

    bot.send_busy = held
    return gate, reached, sent


def _prompt_id(chat, command: str):
    """The id of the message showing the permission prompt for *command*
    (the double records every message sent with a keyboard)."""
    for msg_id, card in chat.cards.items():
        if command in (card["text"] or ""):
            return msg_id
    return None


def test_the_allow_tap_is_handled_while_the_turns_card_waits(
        replay, vloop, pty):
    r = replay
    gate, reached, sent = _held_card_send(r.bot)
    allow = session_parity.session_cb(r.bot, CHAT, r.sess, "allow")

    async def scenario():
        out = {}
        t = vloop.time()
        try:
            await asyncio.wait_for(r.bot._handle_message(
                r.mk_update("deploy it", message_id=1, user_id=12345,
                            chat_id=CHAT), MagicMock()), timeout=2.0)
            out["handler_s"] = round(vloop.time() - t, 3)
        except asyncio.TimeoutError:
            out["handler_s"] = None   # still waiting on the card after 2 s
        r.prompt_hooks(1, "deploy it")
        await asyncio.sleep(0.5)
        out["card_send_waiting"] = list(reached)
        # Claude asks before the card is out: the prompt is its own
        # message (roadmap 8.99), and the next update is the tap on it.
        await r.hook(hook_event_name="PermissionRequest", tool_name="Bash",
                     tool_input={"command": "make deploy"})
        await asyncio.sleep(0.5)
        out["status_asked"] = r.sess.status
        prompt_id = _prompt_id(r.chat, "make deploy")
        out["prompt_sent"] = prompt_id is not None
        writes_before = len(pty.writes)
        t = vloop.time()
        try:
            out["toast"] = await asyncio.wait_for(
                r.tap(prompt_id, data=allow), timeout=2.0)
            out["tap_s"] = round(vloop.time() - t, 3)
        except asyncio.TimeoutError:
            out["tap_s"] = None
        out["tap_typed"] = len(pty.writes) > writes_before
        out["cards_before_release"] = list(sent)
        gate.set()
        await drain_card_tasks()
        out["cards_after_release"] = [
            r.chat.cards[m]["reply_to"] for m in sent if m in r.chat.cards]
        return out

    out = vloop.run_until_complete(scenario())
    assert out["handler_s"] is not None and out["handler_s"] < 1.0, out
    assert out["card_send_waiting"] == [r.sess.turn_seq], out
    assert out["status_asked"] is Status.INTERACTIVE, out
    assert out["prompt_sent"], out
    assert out["tap_s"] is not None and out["tap_s"] < 1.0, out
    assert out["tap_typed"], out
    # The card was held the whole time, and went out under the message.
    assert out["cards_before_release"] == [], out
    assert out["cards_after_release"] == [1], out
