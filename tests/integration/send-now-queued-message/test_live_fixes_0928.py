"""The Send now live test of 2026-09-28 12:44-12:46 (aipager_boss, Claude
Code 2.1.283), replayed, plus the rows around each fix.

Live: three messages queued behind a running step, Send now tapped on the
LAST line. Claude Code cancelled the step instead of backgrounding it and
took only the OLDEST queued message there and then: a nameless ``dequeue``
queue-operation and no hook at all. The other two were absorbed at the next
tool round. Two things went wrong:

- only the tapped line went at the tap, and every delete was an ornament
  paced behind card edits (lines stayed up 7-23 s);
- the dequeued message stayed a queued target, so the next Stop started it
  again as a new turn (roadmap 8.64): a second card for a message already
  answered, left live.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs
from aipager import state as state_mod
from aipager.bot.flood import MUTE

CHAT = 256113222
TAP_SETTLE = 2.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _three_lined(r) -> dict[int, dict]:
    """Messages 2, 3 and 4 queued behind turn 1, the session's one line up
    under 2 (the oldest): ``{2: line}``."""
    await r.turn(1, "first")
    await r.queue(2, "hello?")
    line = await r.wait_line(2, timeout=60)
    assert line is not None, "precondition: no line under 2"
    for mid, text in ((3, "hi"), (4, "hmmm")):
        await r.queue(mid, text)
        await asyncio.sleep(2)
    assert r.chat.line_for(3) is None and r.chat.line_for(4) is None, (
        "precondition: one line per session")
    return {2: line}


def _live_ids(r) -> set[int]:
    return set(r.chat.live_lines())


def _live_card_for(r, mid: int) -> bool:
    """A card replying to *mid* still showing its Stop button: a turn
    running for that message. (A card that moved under an absorbed message
    and was then settled or deleted is not one.)"""
    return any(c["reply_to"] == mid and c["stop"] and not c["deleted"]
               for c in r.chat.cards.values())


def _stand_minimal(vlimiter, vloop) -> None:
    """The chat's hour at 540 calls with minimal mode latched: ORNAMENT
    calls are refused from here on."""
    vlimiter.reset()
    vlimiter.restore([{"chat_id": CHAT,
                       "ban_stamps": [vloop.wall() - 3 * 86400.0],
                       "hourly": [[vloop.wall() - 30.0, 0, 540]],
                       "hourly_minimal": True}])


# ── the live sequence ──────────────────────────────────────────────────────

def _live_replay(r, vloop, *, tap: bool = True, dequeues: int = 1,
                 dequeue_delay: float = 0.2):
    """Returns ``(lines, live_after_tap)``; the chat is inspected after."""
    async def scenario():
        w = r.worker()
        lines = await _three_lined(r)
        if tap:
            await r.tap(lines[2]["id"])
        # "At once": within the settle, while the step still runs (the
        # deletes are background tasks, each through the outbound gate).
        await asyncio.sleep(TAP_SETTLE)
        live_after_tap = _live_ids(r)
        await asyncio.sleep(dequeue_delay)
        for _ in range(dequeues):
            r.fate("dequeue")
        r.append({"type": "user", "message": {
            "role": "user", "content": "[via Telegram · @owner]\nhello?"}})
        await asyncio.sleep(5)
        r.absorb("hi")
        r.absorb("hmmm")
        await asyncio.sleep(3)
        await r.stop("answer to all")
        await asyncio.sleep(15)
        w.cancel()
        return lines, live_after_tap
    return _run(vloop, scenario())


def test_live_tap_removes_the_line_at_once_and_no_other_comes(rp, vloop):
    lines, live_after_tap = _live_replay(rp, vloop)
    assert not live_after_tap & {ln["id"] for ln in lines.values()}
    assert rp.chat.line_for(3) is None and rp.chat.line_for(4) is None


def test_live_dequeued_message_is_not_run_again_as_a_new_turn(rp, vloop):
    _live_replay(rp, vloop)
    assert (_live_card_for(rp, 2), rp.sess.turn_seq, rp.sess.status) == (
        False, 1, state_mod.Status.IDLE)


def test_live_no_card_is_left_live_after_the_stop(rp, vloop):
    _live_replay(rp, vloop)
    assert rp.chat.live_cards() == []


def test_live_dequeued_message_gets_its_thumbs_up(rp, vloop):
    _live_replay(rp, vloop)
    assert "👍" in rp.chat.reactions.get(2, [])


# ── R3's gate: a dequeue is a Send now take only right after a press ──────

def test_dequeue_with_no_press_is_still_the_next_turn(rp, vloop):
    """Without a press, a mid-turn dequeue stays ignored (R8): the finish
    path still starts message 2 as the next turn."""
    _live_replay(rp, vloop, tap=False)
    assert _live_card_for(rp, 2) is True


def test_dequeue_read_after_the_stop_is_the_ordinary_pop(rp, vloop):
    """Claude backgrounded the step (no take); its turn ended and the pop's
    dequeue is already on disk when the Stop is handled, so the finish
    path's own scan reads it with the session IDLE: the press no longer
    counts, and message 2 still starts as the next turn, with its card."""
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        await rp.tap(lines[2]["id"])
        await asyncio.sleep(TAP_SETTLE)
        rp.fate("dequeue")           # no await before the Stop is handled
        await rp.stop("answer one")
        await asyncio.sleep(10)
        w.cancel()
    _run(vloop, scenario())
    assert _live_card_for(rp, 2) is True
    assert [t["msg_id"] for t in rp.sess.queued_targets] == [3, 4]


def test_dequeue_during_a_slow_ctrl_s_is_still_a_take(rp, vloop, monkeypatch):
    """Claude writes its take's dequeue as soon as the Ctrl+S lands, while
    the chord's ``dtach -p`` may still be running: a tick reading it then
    must already see the press."""
    from aipager.dtach import inject

    recorded = inject._run  # the pty recorder's, which stubs every write

    async def _slow_ctrl_s(args, stdin=b"", timeout=5):
        if stdin == b"\x13":
            rp.fate("dequeue")
            await asyncio.sleep(3.0)  # a slow dtach -p, ticks run meanwhile
        return await recorded(args, stdin=stdin, timeout=timeout)

    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        monkeypatch.setattr(inject, "_run", _slow_ctrl_s)
        await rp.tap(lines[2]["id"])
        await asyncio.sleep(5)
        rp.absorb("hi")
        rp.absorb("hmmm")
        await asyncio.sleep(3)
        await rp.stop("answer to all")
        await asyncio.sleep(15)
        w.cancel()
    _run(vloop, scenario())
    assert (_live_card_for(rp, 2), rp.sess.status) == (
        False, state_mod.Status.IDLE)


def test_dequeue_past_the_window_is_ignored(rp, vloop):
    _live_replay(rp, vloop,
                 dequeue_delay=state_mod.SEND_NOW_DEQUEUE_WINDOW + 5.0)
    assert _live_card_for(rp, 2) is True


def test_two_dequeues_take_the_two_oldest(rp, vloop):
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        await rp.tap(lines[2]["id"])
        await asyncio.sleep(0.2)
        rp.fate("dequeue")
        rp.fate("dequeue")
        await asyncio.sleep(5)
        await rp.stop("answer")
        await asyncio.sleep(15)
        w.cancel()
    _run(vloop, scenario())
    assert (_live_card_for(rp, 2), _live_card_for(rp, 3),
            _live_card_for(rp, 4)) == (False, False, True)


def test_press_stamp_clears_when_the_session_goes_idle(rp, vloop):
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        await rp.tap(lines[2]["id"])
        pressed = rp.sess.send_now_take_open()
        await rp.stop("answer")
        await asyncio.sleep(1)
        w.cancel()
        return pressed, rp.sess.send_now_take_open()
    assert _run(vloop, scenario()) == (True, False)


# ── R1: every line goes on a Send now ──────────────────────────────────────

def test_now_command_removes_every_line(rp, vloop):
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        await rp.cmd("_handle_now_cmd", "/now")
        await asyncio.sleep(TAP_SETTLE)
        w.cancel()
        return lines
    lines = _run(vloop, scenario())
    assert not _live_ids(rp) & {ln["id"] for ln in lines.values()}


def test_already_taken_tap_ends_the_lines_for_that_queue(rp, vloop):
    """Message 2 was absorbed; its stored button is tapped while 3 and 4
    are still queued: nothing is pressed, and no line comes for them."""
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        rp.absorb("hello?")
        await asyncio.sleep(0.05)
        toast = await rp.tap(lines[2]["id"])
        await asyncio.sleep(15)
        w.cancel()
        return toast
    toast = _run(vloop, scenario())
    assert toast == "Already taken"
    assert _live_ids(rp) == set()
    assert rp.chat.line_for(3) is None and rp.chat.line_for(4) is None


def test_absorbing_the_lines_message_moves_the_line_to_the_next(rp, vloop):
    """Without a Send now, the line follows the queue: 2 absorbed while 3
    and 4 wait, the line goes and comes back under 3 (the oldest)."""
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        rp.absorb("hello?")
        l3 = await rp.wait_line(3, timeout=15)
        w.cancel()
        return lines, l3
    lines, l3 = _run(vloop, scenario())
    assert lines[2]["id"] not in _live_ids(rp)
    assert l3 is not None and l3["id"] in _live_ids(rp)
    assert rp.chat.line_for(4) is None


# ── R2: a line's delete goes at answer priority ────────────────────────────

def test_line_deletes_go_through_in_minimal_mode(rp, vloop, vlimiter):
    """Minimal mode refuses ORNAMENT calls (they stay owed): the lines a tap
    removes are deleted at answer priority, so they really go."""
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        _stand_minimal(vlimiter, vloop)
        assert vlimiter.minimal_mode(CHAT) is True
        await rp.tap(lines[2]["id"])
        await asyncio.sleep(TAP_SETTLE)
        w.cancel()
        return lines
    lines = _run(vloop, scenario())
    ids = {ln["id"] for ln in lines.values()}
    assert not _live_ids(rp) & ids
    assert not [pair for pair in rp.bot.registry.queued_line_deletes
                if pair[1] in ids]


def test_muted_chat_still_owes_the_delete(rp, vloop):
    async def scenario():
        w = rp.worker()
        lines = await _three_lined(rp)
        MUTE.mute(CHAT, 600)
        try:
            rp.absorb("hello?")
            await asyncio.sleep(TAP_SETTLE + 2)
        finally:
            MUTE.clear()
        w.cancel()
        return lines
    lines = _run(vloop, scenario())
    assert [CHAT, lines[2]["id"]] in rp.bot.registry.queued_line_deletes
