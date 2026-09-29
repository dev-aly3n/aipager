"""One "⏳ Queued" line per session (live test 2026-09-29 10:19-10:20,
session sendtest2, build 58839c3).

Live: seven messages queued behind a running ping. The first message's line
came in 0.3 s; every further line went as a blocking ornament and came
12-39 s late at the chat's 0.5/s ceiling; three of them were still waiting
for their token when Send now was tapped and landed after the answer. Now a
session has one line, under the oldest waiting message, at answer priority,
and a line send that lands after a Send now press never stays up.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222
#: How late a due line may land because it waits for its token.
BUDGET_SLACK = 2.5
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


def _burst(r, vloop):
    """Returns ``(first pick-up, tap time, deleted within 1.5 s of the
    tap)``; the chat is inspected after."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "run the ping inline")
        await asyncio.sleep(8)
        r.tool_start("ping -c 60 127.0.0.1")
        await asyncio.sleep(10)
        t0 = None
        for i, (mid, text) in enumerate(MESSAGES):
            t = await r.queue(mid, text)
            t0 = t if t0 is None else t0
            if i < len(GAPS):
                await asyncio.sleep(GAPS[i])
        await asyncio.sleep(5)
        line = r.chat.line_for(2)
        assert line is not None, "precondition: the session's line"
        tap_t = vloop.time()
        await r.tap(line["id"])
        deleted_fast = await r.wait_deleted(line["id"], timeout=1.5)
        for _mid, text in MESSAGES:
            r.absorb(text)
        await asyncio.sleep(3)
        await r.stop("I'm here; the ping keeps going in the background.")
        await asyncio.sleep(20)
        w.cancel()
        return t0, tap_t, deleted_fast
    return _run(vloop, scenario())


def test_burst_sends_exactly_one_line(rp, vloop):
    _burst(rp, vloop)
    assert len(rp.chat.lines) == 1, sorted(
        (ln["reply_to"], ln["t"]) for ln in rp.chat.lines.values())


def test_burst_line_goes_under_the_first_message_at_once(rp, vloop):
    t0, _tap, _fast = _burst(rp, vloop)
    line = rp.chat.line_for(2)
    assert line is not None and line["t"] - t0 <= BUDGET_SLACK


def test_burst_line_is_deleted_within_a_moment_of_the_tap(rp, vloop):
    _t0, _tap, deleted_fast = _burst(rp, vloop)
    assert deleted_fast is True


def test_burst_no_line_is_sent_after_the_tap(rp, vloop):
    _t0, tap_t, _fast = _burst(rp, vloop)
    late = [(ln["reply_to"], ln["t"]) for ln in rp.chat.lines.values()
            if ln["t"] >= tap_t]
    assert late == []
    assert rp.chat.live_lines() == []


def test_a_message_after_the_tap_gets_the_line_not_an_older_one(rp, vloop):
    """After a tap the queue it answered for gets no line, even while its
    messages are still on their way into Claude; a message sent after the
    tap gets its own line, under itself."""
    async def scenario():
        w = rp.worker()
        await rp.turn(1, "run the ping inline")
        await asyncio.sleep(8)
        rp.tool_start("ping -c 60 127.0.0.1")
        await asyncio.sleep(10)
        await rp.queue(2, "hello?")
        line = await rp.wait_line(2, timeout=10)
        assert line is not None, "precondition: the session's line"
        await rp.queue(3, "hi?")
        await asyncio.sleep(2)
        await rp.tap(line["id"])
        await asyncio.sleep(2)
        await rp.queue(5, "one more")
        await asyncio.sleep(8)
        w.cancel()
    _run(vloop, scenario())
    assert rp.chat.line_for(3) is None
    assert rp.chat.line_for(5) is not None


def test_no_line_flashes_under_the_next_message_during_a_slow_chord(
        rp, vloop, monkeypatch):
    """Review rev-iter1-002: the keys take seconds to go out and Claude
    takes the first message while they do; the line must not move under
    the next one for a moment."""
    from aipager.dtach import inject

    recorded = inject._run  # the pty recorder's

    async def _slow_ctrl_s(args, stdin=b"", timeout=5):
        if stdin == b"\x13":
            rp.absorb("hello?")
            await asyncio.sleep(6.0)  # long enough for card ticks to run
        return await recorded(args, stdin=stdin, timeout=timeout)

    async def scenario():
        w = rp.worker()
        await rp.turn(1, "run the ping inline")
        await asyncio.sleep(8)
        rp.tool_start("ping -c 60 127.0.0.1")
        await asyncio.sleep(10)
        await rp.queue(2, "hello?")
        line = await rp.wait_line(2, timeout=10)
        assert line is not None, "precondition: the session's line"
        await rp.queue(3, "hi?")
        await rp.queue(4, "hmmm?")
        await asyncio.sleep(2)
        monkeypatch.setattr(inject, "_run", _slow_ctrl_s)
        await rp.tap(line["id"])
        await asyncio.sleep(10)
        w.cancel()
    _run(vloop, scenario())
    assert rp.chat.line_for(3) is None and rp.chat.line_for(4) is None
