"""Send now lines that are instant, per message, and removed the moment
Claude takes the message (operator, live tests 2026-09-29; design
/home/aly/researches/deliver/send-now-instant-lines/design.md).

Live: seven messages queued behind a running ping. The first line came in
0.3 s; later lines came 12-39 s late (paced as ornaments at the chat's
0.5/s ceiling), three landed after the answer, and removal and 👍 rode the
card's paced tick. Now every queued message gets its own line at once, a
line goes (with its 👍) within a moment of Claude's transcript saying its
message was taken, even while the card is stuck on its own edit, and lines
that go together go in one delete.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222
#: An INSTANT line waits for the 30/s overall bucket only; the watcher reads
#: Claude's queue every 0.25 s.
INSTANT_SLACK = 0.5
MESSAGES = [(2, "hello?"), (3, "hi?"), (4, "hmmm?"), (5, "how are you?"),
            (6, "wtf?"), (7, "ok?"), (8, "hhh")]
#: Gaps between the live messages, in seconds (10:19:14.9 .. 10:19:50.6).
GAPS = [3.6, 4.2, 5.2, 2.0, 11.5, 9.1]


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _long_step(r):
    await r.turn(1, "run the ping inline")
    await asyncio.sleep(8)
    r.tool_start("ping -c 60 127.0.0.1")
    await asyncio.sleep(10)


# ── T2: the live burst ───────────────────────────────────────────────────

def _burst(r, vloop):
    """Returns ``({mid: pick-up time}, tap time)``."""
    async def scenario():
        w = r.worker()
        await _long_step(r)
        picked = {}
        for i, (mid, text) in enumerate(MESSAGES):
            picked[mid] = await r.queue(mid, text)
            if i < len(GAPS):
                await asyncio.sleep(GAPS[i])
        await asyncio.sleep(2)
        tap_t = vloop.time()
        await r.tap(r.chat.line_for(2)["id"])
        await asyncio.sleep(2)
        for _mid, text in MESSAGES:
            r.absorb(text)
        await asyncio.sleep(3)
        await r.stop("I'm here; the ping keeps going in the background.")
        await asyncio.sleep(10)
        w.cancel()
        return picked, tap_t
    return _run(vloop, scenario())


def test_burst_every_message_gets_its_own_line_at_once(rp, vloop):
    picked, _tap = _burst(rp, vloop)
    late = {mid: round(rp.chat.line_for(mid)["t"] - t0, 3)
            for mid, t0 in picked.items()
            if rp.chat.line_for(mid) is None
            or rp.chat.line_for(mid)["t"] - t0 > INSTANT_SLACK}
    assert late == {}
    assert len(rp.chat.lines) == len(MESSAGES)


def test_burst_tap_removes_every_line_in_one_call(rp, vloop):
    _picked, _tap = _burst(rp, vloop)
    ids = sorted(rp.chat.lines)
    assert ids in [sorted(c) for c in rp.chat.delete_calls]
    assert rp.chat.live_lines() == []


def test_burst_no_line_is_sent_after_the_tap(rp, vloop):
    _picked, tap_t = _burst(rp, vloop)
    assert [ln["reply_to"] for ln in rp.chat.lines.values()
            if ln["t"] >= tap_t] == []


# ── T3: removal the moment Claude takes the message, card or no card ─────

def _taken_while_the_card_is_stuck(r, vloop, monkeypatch):
    """The card's tick is stuck (as while its edit waits for a flood token:
    the tick itself is held, so it reads no queue record and sends no 👍);
    Claude absorbs 3 and 4 together. Returns ``(absorb time, reactions
    seen while stuck)``."""
    stuck = asyncio.Event()
    real_tick = r.bot._animate_tick

    async def _stuck_tick(*args, **kwargs):
        await stuck.wait()
        return await real_tick(*args, **kwargs)

    async def scenario():
        w = r.worker()
        await _long_step(r)
        for mid, text in MESSAGES[:3]:
            await r.queue(mid, text)
            await asyncio.sleep(0.5)
        monkeypatch.setattr(r.bot, "_animate_tick", _stuck_tick)
        await asyncio.sleep(2)            # any tick in flight is now stuck
        t_abs = vloop.time()
        r.absorb("hi?")
        r.absorb("hmmm?")
        await asyncio.sleep(2)
        # Read while the card is still stuck: nothing here came from it.
        thumbs = {m: list(r.chat.reactions.get(m, [])) for m in (2, 3, 4)}
        stuck.set()
        await asyncio.sleep(2)
        w.cancel()
        return t_abs, thumbs
    return _run(vloop, scenario())


def test_taken_lines_go_within_a_moment_while_the_card_is_stuck(
        rp, vloop, monkeypatch):
    t_abs, _ = _taken_while_the_card_is_stuck(rp, vloop, monkeypatch)
    for mid in (3, 4):
        line = rp.chat.line_for(mid)
        assert line["deleted"] is True
        assert line["deleted_t"] - t_abs <= INSTANT_SLACK, (mid, line)
    assert rp.chat.line_for(2)["deleted"] is False


def test_lines_taken_together_go_in_one_delete(rp, vloop, monkeypatch):
    _taken_while_the_card_is_stuck(rp, vloop, monkeypatch)
    ids = sorted([rp.chat.line_for(3)["id"], rp.chat.line_for(4)["id"]])
    assert ids in [sorted(c) for c in rp.chat.delete_calls]


def test_taken_messages_get_their_thumbs_up_at_once_while_the_card_is_stuck(
        rp, vloop, monkeypatch):
    _, thumbs = _taken_while_the_card_is_stuck(rp, vloop, monkeypatch)
    assert "👍" in thumbs[3] and "👍" in thumbs[4]
    assert "👍" not in thumbs[2]


def test_taken_messages_get_their_thumbs_up_only_once(rp, vloop, monkeypatch):
    """The card's tick drains the same notes after the watcher: the reaction
    ledger keeps it to one 👍."""
    _taken_while_the_card_is_stuck(rp, vloop, monkeypatch)
    assert rp.chat.reactions.get(3, []).count("👍") == 1
    assert rp.chat.reactions.get(4, []).count("👍") == 1


# ── T6: Send now ─────────────────────────────────────────────────────────

def test_a_message_after_the_tap_gets_its_own_line(rp, vloop):
    async def scenario():
        w = rp.worker()
        await _long_step(rp)
        await rp.queue(2, "hello?")
        await rp.queue(3, "hi?")
        line = await rp.wait_line(2, timeout=2)
        await rp.tap(line["id"])
        await asyncio.sleep(2)
        t5 = await rp.queue(5, "one more")
        await asyncio.sleep(3)
        w.cancel()
        return t5
    t5 = _run(vloop, scenario())
    assert sum(1 for ln in rp.chat.lines.values() if ln["reply_to"] == 3) == 1
    line5 = rp.chat.line_for(5)
    assert line5 is not None and line5["t"] - t5 <= INSTANT_SLACK
    assert line5["deleted"] is False


def test_no_line_comes_for_the_tapped_queue_during_a_slow_chord(
        rp, vloop, monkeypatch):
    """The keys take seconds to go out and Claude takes the first message
    while they do: the messages the press answered for never get a new
    line (the watcher and the tick run meanwhile)."""
    from aipager.dtach import inject

    recorded = inject._run  # the pty recorder's

    async def _slow_ctrl_s(args, stdin=b"", timeout=5):
        if stdin == b"\x13":
            rp.absorb("hello?")
            await asyncio.sleep(6.0)
        return await recorded(args, stdin=stdin, timeout=timeout)

    async def scenario():
        w = rp.worker()
        await _long_step(rp)
        for mid, text in MESSAGES[:3]:
            await rp.queue(mid, text)
        await asyncio.sleep(1)
        monkeypatch.setattr(inject, "_run", _slow_ctrl_s)
        tap_t = vloop.time()
        await rp.tap(rp.chat.line_for(2)["id"])
        await asyncio.sleep(10)
        w.cancel()
        return tap_t
    tap_t = _run(vloop, scenario())
    assert [ln["reply_to"] for ln in rp.chat.lines.values()
            if ln["t"] >= tap_t] == []
    assert rp.chat.live_lines() == []


def test_a_repeated_pick_up_after_the_tap_brings_no_new_line(rp, vloop):
    """Claude Code can report a message's pick-up twice; after a tap has
    answered for it, the second report must not put a line up again."""
    async def scenario():
        w = rp.worker()
        await _long_step(rp)
        await rp.queue(2, "hello?")
        line = await rp.wait_line(2, timeout=2)
        await rp.tap(line["id"])
        await asyncio.sleep(1)
        await rp.hook(type="queue_pickup", consumed=[
            {"msg_id": 2, "chat_id": CHAT, "raw_text": "hello?"}],
            expired=[])
        await asyncio.sleep(3)
        w.cancel()
    _run(vloop, scenario())
    assert sum(1 for ln in rp.chat.lines.values() if ln["reply_to"] == 2) == 1


def test_the_watcher_stops_once_nothing_is_queued(rp, vloop):
    """It reads Claude's queue four times a second only while a message is
    queued: once the queue is empty (and the turn is over) it ends."""
    async def scenario():
        w = rp.worker()
        await _long_step(rp)
        await rp.queue(2, "hello?")
        await rp.wait_line(2, timeout=2)
        task = rp.bot._queue_watches[rp.sess.name]
        running = not task.done()
        rp.absorb("hello?")
        await asyncio.sleep(1)
        await rp.stop("answer")
        await asyncio.sleep(2)
        w.cancel()
        return running, task.done()
    assert _run(vloop, scenario()) == (True, True)


def test_the_watcher_stops_when_idle_with_only_a_stale_target(rp, vloop):
    """The session went idle but its finish has not cleared the turn's
    transcript path (the finish waits for the card's lock, which a paced
    card edit can hold for many seconds, or it failed), leaving a message
    that never got a line (no queue record): nothing to show, so the
    watcher must not keep reading the transcript four times a second."""
    from aipager.state import Status

    async def scenario():
        w = rp.worker()
        await _long_step(rp)
        await rp.queue(2, "hello?", evidence=False)
        await asyncio.sleep(3)             # its line gave up: no record
        task = rp.bot._queue_watches[rp.sess.name]
        running = not task.done()
        rp.bot.registry.transition(rp.sess.name, Status.IDLE)
        await asyncio.sleep(2)
        w.cancel()
        return (running, [t["msg_id"] for t in rp.sess.queued_targets],
                bool(rp.sess.stream_transcript_path), task.done())
    running, left, path_set, done = _run(vloop, scenario())
    assert running is True, "precondition: the watcher ran"
    assert (left, path_set) == ([2], True), "precondition: stale, path set"
    assert done is True
