"""The busy card's move looks instant (operator, 2026-09-29: "when the copy
went below, the old bussy card was still there for few second ... also
check and see if that one was complete copy and didnt remove anything").

Three queued messages are absorbed mid-turn with the chat's token bucket
empty, the state the live test was in right after five Send now lines. The
card moves under the last one: ONE send carrying the complete card (its
timeline and Stop button, no bare "Thinking…" frame to fill in later), and
the old card deleted right after it, neither waiting for a chat token.
Real limiter, virtual loop (``conftest.py``).
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs

CHAT = 256113222
PREFIX = "[via Telegram · @owner]\n"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _move_under_an_empty_bucket(r, vlimiter):
    """Turn 1 with two steps on its card; 2, 3 and 4 queued, then absorbed
    together with the chat out of tokens. Returns the old card's id."""
    prefs.set_preference(CHAT, "layout", "card")
    w = asyncio.ensure_future(r._updates())
    r.say(1, "run the checks")
    await r.updates.join()
    r.prompt_hooks(1, "run the checks")
    await asyncio.sleep(1)
    r.tool("ping -c 60 127.0.0.1")
    await asyncio.sleep(1)
    r.tool("grep -rn bazaar notes")
    await asyncio.sleep(3)
    old = r.sess.busy_msg_id
    for mid, text in ((2, "hello?"), (3, "x1"), (4, "2")):
        r.say(mid, text)
        await r.updates.join()
        r.prompt_hooks(mid, text)
        await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    budget = vlimiter._budget_for(CHAT)
    while budget.chat.take(1.0):   # the chat is out of tokens
        pass
    r.append(*({"type": "queue-operation", "operation": "remove",
                "reason": "absorbed_mid_turn", "content": PREFIX + text}
               for text in ("hello?", "x1", "2")))
    await asyncio.sleep(20)
    w.cancel()
    return old


def _moved_card(r):
    moved = [m for m, c in r.chat.cards.items() if c["reply_to"] == 4]
    assert len(moved) == 1, f"no single card under 4: {r.chat.cards}"
    return moved[0], r.chat.cards[moved[0]]


def test_the_old_card_goes_the_moment_the_copy_lands(replay, vloop, vlimiter):
    r = replay
    old = _run(vloop, _move_under_an_empty_bucket(r, vlimiter))

    _card_id, copy = _moved_card(r)
    assert r.chat.cards[old]["deleted"] is True
    gap = r.chat.cards[old]["deleted_at"] - copy["t"]
    assert 0 <= gap <= 0.5, (copy["t"], r.chat.cards[old]["deleted_at"])


def test_the_copy_is_complete_from_its_first_frame(replay, vloop, vlimiter):
    """No bare "Thinking…" frame is ever shown under the new message: the
    copy's FIRST version already carries both steps and the Stop button."""
    r = replay
    _run(vloop, _move_under_an_empty_bucket(r, vlimiter))

    card_id, card = _moved_card(r)
    first = card["texts"][0]
    assert "ping -c 60 127.0.0.1" in first and "grep -rn bazaar notes" in first
    assert "Thinking" not in first
    assert card["stop"] is True and card["deleted"] is False
    assert r.sess.busy_msg_id == card_id


def test_one_card_ends_up_live_under_the_last_absorbed_message(replay, vloop,
                                                               vlimiter):
    r = replay
    _run(vloop, _move_under_an_empty_bucket(r, vlimiter))

    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 4
    assert r.chat.card_sends_for(4) == 1


def _stand_minimal(vlimiter, vloop) -> None:
    """The chat in minimal mode (as tests/integration/send-now-queued-
    message does it): the card shows "working (updates paused)"."""
    vlimiter.reset()
    vlimiter.restore([{"chat_id": CHAT,
                       "ban_stamps": [vloop.wall() - 3 * 86400.0],
                       "hourly": [[vloop.wall() - 30.0, 0, 540]],
                       "hourly_minimal": True}])


def test_minimal_mode_still_moves_the_card_at_once(replay, vloop, vlimiter):
    """Minimal mode pauses the card's animation, not its move (review
    rev-iter1-001): the paused card moves under the message Claude took,
    as the same paused line, and the old one goes with it."""
    prefs.set_preference(CHAT, "layout", "card")
    r = replay

    async def scenario():
        w = asyncio.ensure_future(r._updates())
        r.say(1, "run the checks")
        await r.updates.join()
        r.prompt_hooks(1, "run the checks")
        await asyncio.sleep(1)
        r.tool("ping -c 60 127.0.0.1")
        await asyncio.sleep(3)
        _stand_minimal(vlimiter, vloop)
        await asyncio.sleep(40)          # the paused line goes up
        old = r.sess.busy_msg_id
        paused_shown = r.chat.cards[old]["text"]
        r.say(2, "hello?")
        await r.updates.join()
        r.prompt_hooks(2, "hello?")
        await asyncio.sleep(1)
        taken_at = vloop.time()
        r.append({"type": "queue-operation", "operation": "remove",
                  "reason": "absorbed_mid_turn", "content": PREFIX + "hello?"})
        await asyncio.sleep(60)
        w.cancel()
        return old, paused_shown, taken_at, vlimiter.minimal_mode(CHAT)

    old, paused_shown, taken_at, still_minimal = _run(vloop, scenario())
    assert still_minimal is True, "precondition: minimal mode throughout"
    assert "updates paused" in paused_shown, "precondition: the paused line"
    moved = [m for m, c in r.chat.cards.items() if c["reply_to"] == 2]
    assert len(moved) == 1, f"the card did not move under 2: {r.chat.cards}"
    card_id = moved[0]
    copy = r.chat.cards[card_id]
    assert copy["t"] - taken_at <= 5.0, (taken_at, copy["t"])
    assert "updates paused" in copy["texts"][0]
    assert r.chat.cards[old]["deleted"] is True
    assert r.chat.cards[old]["deleted_at"] - copy["t"] <= 0.5
    assert r.chat.live_cards() == [card_id]
