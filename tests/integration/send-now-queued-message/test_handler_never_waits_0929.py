"""aipager never holds a message back while a turn runs (operator, live
test 2026-09-29: "we must not hold any message we pass them into claude
the moment they sent").

Live 20:57: a queued message's handler waited 18 s for the card's lock
(the card's tick held it while its edit waited for a flood token), and PTB
handles updates one at a time, so the next two messages reached Claude 18
s late. Here the card's lock is held for the whole row: each message must
still be typed into Claude, and its handler return, at once.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _typed(text: str) -> bool:
    """Whether aipager typed *text* into Claude (the harness's
    ``inject.send_text_and_enter`` double records every call)."""
    from aipager.dtach import inject
    return any(text in str(call.args) + str(call.kwargs)
               for call in inject.send_text_and_enter.await_args_list)


def test_queued_messages_reach_claude_while_the_card_is_stuck(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        r.tool_start("long step")
        await asyncio.sleep(5)
        await r.sess.animate_lock.acquire()   # the card stuck on its edit
        took = []
        try:
            for mid, text in ((2, "one"), (3, "two"), (4, "three")):
                t = vloop.time()
                try:
                    await asyncio.wait_for(r.queue(mid, text), timeout=2.0)
                    took.append((text, round(vloop.time() - t, 3),
                                 _typed(text)))
                except asyncio.TimeoutError:
                    took.append((text, None, _typed(text)))
                    break
        finally:
            r.sess.animate_lock.release()
        await asyncio.sleep(1)
        w.cancel()
        return took

    took = _run(vloop, scenario())
    # Each handler returned within a second (None: it was still waiting on
    # the card after 2 s) and each message was typed into Claude.
    assert [(text, s is not None and s < 1.0) for text, s, _t in took] == [
        ("one", True), ("two", True), ("three", True)], took
    assert [text for text, _s, typed in took if typed] == ["one", "two",
                                                           "three"]


def test_a_message_that_starts_a_turn_still_gets_its_card(replay, vloop,
                                                         pty):
    """The first message (no turn running) still waits for its card, as
    before: the card is up when its handler returns."""
    r = replay

    async def scenario():
        w = r.worker()
        r.say(1, "first")
        await r.updates.join()
        cards = [c for c in r.chat.cards.values() if c["reply_to"] == 1]
        w.cancel()
        return cards

    assert len(_run(vloop, scenario())) == 1
