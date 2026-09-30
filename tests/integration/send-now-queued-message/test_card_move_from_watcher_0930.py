"""The card moves the moment Claude takes the message (live 2026-09-30,
test2: Claude absorbed six queued messages at 00:33:31.3, the card moved
only at 00:33:34.1, on the card's next paced tick). The queue watcher,
which already deletes the Send now lines and sends the 👍, now moves the
card too. The card's tick is held stuck here, so a move can only come from
the watcher; when the tick wakes up the notes are already taken, so it
sends no second copy. The watcher's move does not wait for the 👍 round
trips (review rev-iter1-002), shown with a slow reaction below.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222
MESSAGES = [(2, "hello?"), (3, "hi?"), (4, "hmmm?")]


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _stick_the_tick(r, monkeypatch) -> asyncio.Event:
    stuck = asyncio.Event()
    real_tick = r.bot._animate_tick

    async def _stuck_tick(*args, **kwargs):
        await stuck.wait()
        return await real_tick(*args, **kwargs)

    monkeypatch.setattr(r.bot, "_animate_tick", _stuck_tick)
    return stuck


async def _absorbed_with_the_tick_stuck(r, vloop, monkeypatch):
    """Turn 1 with its card; three messages queued, then absorbed together
    while the card's tick is stuck. Returns (old card id, absorbed at)."""
    w = r.worker()
    await r.turn(1, "run the ping inline")
    await asyncio.sleep(8)
    r.tool_start("ping -c 60 127.0.0.1")
    await asyncio.sleep(3)
    for mid, text in MESSAGES:
        await r.queue(mid, text)
        await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    stuck = _stick_the_tick(r, monkeypatch)
    await asyncio.sleep(3)            # any tick in flight is now stuck
    old = r.sess.busy_msg_id
    absorbed_at = vloop.time()
    for _mid, text in MESSAGES:
        r.absorb(text)
    await asyncio.sleep(3)
    stuck.set()
    await asyncio.sleep(3)
    w.cancel()
    return old, absorbed_at


def _cards_under(r, mid):
    return [m for m, c in r.chat.cards.items() if c["reply_to"] == mid]


def test_the_card_moves_at_once_while_the_tick_is_stuck(rp, vloop,
                                                         monkeypatch):
    r = rp
    old, absorbed_at = _run(vloop, _absorbed_with_the_tick_stuck(
        r, vloop, monkeypatch))

    moved = _cards_under(r, 4)
    assert len(moved) == 1, f"no single card under 4: {r.chat.cards}"
    copy = r.chat.cards[moved[0]]
    assert copy["t"] - absorbed_at <= 0.5, (absorbed_at, copy["t"])
    assert r.chat.cards[old]["deleted"] is True
    assert r.chat.cards[old]["deleted_t"] - copy["t"] <= 0.5
    # One complete copy: the running step is on it from its first frame.
    assert "ping -c 60 127.0.0.1" in copy["texts"][0]


def test_the_tick_waking_up_after_the_watcher_moved_it_moves_nothing(
        rp, vloop, monkeypatch):
    """The stuck tick is released after the watcher's move: exactly one
    copy went out, and one card is live, under the last taken message."""
    r = rp
    _run(vloop, _absorbed_with_the_tick_stuck(r, vloop, monkeypatch))

    copies = [c for c in r.chat.cards.values() if c["reply_to"] != 1]
    assert len(copies) == 1, r.chat.cards
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 4


def test_slow_thumbs_up_do_not_hold_the_card_back(rp, vloop, monkeypatch):
    """Each 👍 takes 0.3 s to come back (the harness otherwise answers
    at once): the card still moves within 0.5 s of the absorption."""
    r = rp
    real_reaction = r.bot._app.bot.set_message_reaction

    async def _slow_reaction(*args, **kwargs):
        await asyncio.sleep(0.3)
        return await real_reaction(*args, **kwargs)

    r.bot._app.bot.set_message_reaction = _slow_reaction
    old, absorbed_at = _run(vloop, _absorbed_with_the_tick_stuck(
        r, vloop, monkeypatch))

    moved = _cards_under(r, 4)
    assert len(moved) == 1, f"no single card under 4: {r.chat.cards}"
    assert r.chat.cards[moved[0]]["t"] - absorbed_at <= 0.5
    assert r.chat.cards[old]["deleted"] is True
