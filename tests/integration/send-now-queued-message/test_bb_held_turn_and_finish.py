"""Black-box rows added in fix iteration 2 (review-1 rev-iter1-001/002).

1. A dialog Claude has opened but aipager has not seen yet: the newest
   turn's hook events (its PermissionRequest among them) are held behind an
   older turn's slowed finish (roadmap 8.62), so the session still reads
   BUSY. A tap and ``/now`` must write no key then (D3).
2. Finish site B: a message absorbed while a finish waits, at a Stop that
   popped nothing (an API-error Stop), with no animation tick to see it
   (the chat is in minimal mode, where the tick returns before its queue
   scan). Only the finish's second transcript read can drop its line.
3. The other places a line is dropped that no row isolated (review
   rev-iter1-004): finish site A (the Stop's pop), ``/clearqueue``, and a
   session entry reused by ``create_session`` or ``/resume``. Where the
   turn keeps running, the chat is in minimal mode so the animation tick
   (which would drop the line a few seconds later anyway) cannot stand in
   for the call under test; the line's delete is then refused by the
   gate and stays owed, so those rows read the session's line record.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222
BUSY = "Busy, try again in a moment"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _slow_finish(r, monkeypatch, seconds: float):
    real = r.bot._mark_ran_commands

    async def _slow(sess):
        await asyncio.sleep(seconds)
        await real(sess)

    monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow)


def _stand_at(vlimiter, vloop, used: int) -> None:
    """The chat's hour at *used* calls (one bucket 30 s old), its ban
    history kept."""
    vlimiter.reset()
    vlimiter.restore([{"chat_id": CHAT,
                       "ban_stamps": [vloop.wall() - 3 * 86400.0],
                       "hourly": [[vloop.wall() - 30.0, 0, used]]}])


# ── 1. a held permission prompt: no keys ──────────────────────────────────

def _held_dialog_row(r, vloop, monkeypatch, pty):
    """Turn 1 runs, message 2 is queued. Turn 1's finish is slowed 60 s;
    its Stop pops message 2 as turn 2, whose PermissionRequest is then
    held. Message 3 is queued and gets its line; then ``/now``, then a tap
    on message 3's line. Returns ``(preconditions, replies, toast,
    writes)``."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        _slow_finish(r, monkeypatch, 60.0)
        r.fate("dequeue")
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.hook(hook_event_name="PermissionRequest", tool_name="Bash",
               tool_input={"command": "rm -rf build"})
        await asyncio.sleep(0.5)
        await r.queue(3, "queued three")
        line = await r.wait_line(3, timeout=15)
        assert line is not None, "precondition: message 3's line never appeared"
        sess = r.sess
        pre = (sess.turn_state_held(), sess.status.name,
               sess.pending_permission)
        start = len(pty.writes)
        replies = await r.cmd("_handle_now_cmd", "/now")
        toast = await r.tap(line["id"])
        await asyncio.sleep(1)
        writes = pty.data()[start:]
        # Let the slowed finish run out, so the row ends settled.
        await asyncio.sleep(70)
        w.cancel()
        return pre, replies, toast, writes
    return _run(vloop, scenario())


def test_held_dialog_precondition(replay, vloop, monkeypatch, pty):
    """The state the refusal is about: the dialog is held, so the status
    alone shows no dialog."""
    pre, _, _, _ = _held_dialog_row(replay, vloop, monkeypatch, pty)
    assert pre == (True, "BUSY", None)


def test_held_dialog_now_and_tap_write_nothing(replay, vloop, monkeypatch,
                                               pty):
    _, _, _, writes = _held_dialog_row(replay, vloop, monkeypatch, pty)
    assert writes == []


def test_held_dialog_now_replies_busy(replay, vloop, monkeypatch, pty):
    _, replies, _, _ = _held_dialog_row(replay, vloop, monkeypatch, pty)
    assert replies == [BUSY]


def test_held_dialog_tap_toasts_busy(replay, vloop, monkeypatch, pty):
    _, _, toast, _ = _held_dialog_row(replay, vloop, monkeypatch, pty)
    assert toast == BUSY


# ── 2. finish site B: absorbed while an API-error finish waits ────────────

def _site_b_row(r, vloop, vlimiter, monkeypatch):
    """Turn 1 with a tool, message 2 queued and its line up. The chat
    goes into minimal mode (no tick scans the queue from here on). An
    API-error Stop pops nothing; its finish is slowed 6 s, and message 2's
    absorption is written 0.5 s into it. Returns ``(minimal, before,
    after)``: minimal mode's state, and message 2's line record just
    before and 20 s after the Stop."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        line = await r.wait_line(2, timeout=15)
        assert line is not None, "precondition: the line never appeared"
        _stand_at(vlimiter, vloop, 540)
        minimal = vlimiter.minimal_mode(CHAT)
        _slow_finish(r, monkeypatch, 6.0)
        r.stop("API Error: 500 internal server error")
        await asyncio.sleep(0.5)
        before = r.sess.queued_lines.get(2)
        r.absorb("queued two")
        await asyncio.sleep(20)
        after = r.sess.queued_lines.get(2)
        w.cancel()
        return minimal, before, after
    return _run(vloop, scenario())


def test_site_b_precondition(replay, vloop, vlimiter, monkeypatch, pty):
    """Minimal mode is on, and the Stop left the line in place (it popped
    nothing, and the absorption was not written yet)."""
    minimal, before, _ = _site_b_row(replay, vloop, vlimiter, monkeypatch)
    assert minimal is True
    assert before is not None


def test_site_b_absorbed_during_the_finish_drops_the_line(
        replay, vloop, vlimiter, monkeypatch, pty):
    _, _, after = _site_b_row(replay, vloop, vlimiter, monkeypatch)
    assert after is None


# ── 3. the remaining drop sites, each isolated ────────────────────────────

DELETE_WINDOW = 10.0


def _site_a_row(r, vloop, vlimiter, monkeypatch):
    """Message 2's line up, then minimal mode. Turn 1's finish is slowed
    20 s; its Stop pops message 2. Returns message 2's line record just
    before the Stop and 1 s after it (the finish still waiting)."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        line = await r.wait_line(2, timeout=15)
        assert line is not None, "precondition: the line never appeared"
        _stand_at(vlimiter, vloop, 540)
        assert vlimiter.minimal_mode(CHAT), "precondition: minimal mode"
        _slow_finish(r, monkeypatch, 20.0)
        before = r.sess.queued_lines.get(2)
        r.fate("dequeue")
        r.stop("answer first")
        await asyncio.sleep(1.0)
        after = r.sess.queued_lines.get(2)
        await asyncio.sleep(30)  # the finish runs out
        w.cancel()
        return before, after
    return _run(vloop, scenario())


def test_site_a_popped_line_is_dropped_at_the_stop(
        replay, vloop, vlimiter, monkeypatch, pty):
    before, after = _site_a_row(replay, vloop, vlimiter, monkeypatch)
    assert before is not None
    assert after is None


def test_clearqueue_drops_the_line_with_no_tick(
        replay, vloop, vlimiter, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        line = await r.wait_line(2, timeout=15)
        assert line is not None, "precondition: the line never appeared"
        _stand_at(vlimiter, vloop, 540)
        assert vlimiter.minimal_mode(CHAT), "precondition: minimal mode"
        before = r.sess.queued_lines.get(2)
        await r.cmd("_handle_clearqueue_cmd", "/clearqueue")
        after = r.sess.queued_lines.get(2)
        w.cancel()
        return before, after

    before, after = _run(vloop, scenario())
    assert before is not None
    assert after is None


async def _gone_with_its_line(r) -> dict:
    """Message 2's line up, then Claude Code's ``SessionEnd`` for
    ``/clear``: the session reads GONE, its queue and line are kept
    (SC16), and the datagram's transcript path gives it a resumable id."""
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    await r.hook(hook_event_name="SessionEnd", reason="clear",
                 transcript_path=r.transcript)
    await asyncio.sleep(1)
    assert r.sess.status.name == "GONE", "precondition: GONE"
    assert not r.chat.lines[line["id"]]["deleted"], "precondition: kept"
    return line


def test_create_session_reusing_the_entry_deletes_the_line(
        replay, vloop, pty):
    """``create_session`` (the seam /new and the Mini App's create route
    share) registering a new process under this session's name."""
    r = replay

    async def scenario():
        w = r.worker()
        line = await _gone_with_its_line(r)
        name, err = await r.bot.create_session("sendnow_harness",
                                               scope_chat_id=None)
        assert (name, err) == (r.sess.name, ""), "precondition: same entry"
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_resume_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _gone_with_its_line(r)
        await r.cmd("_handle_resume_cmd", "/resume sendnow_harness")
        assert r.sess.status.name == "IDLE", "precondition: resumed"
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True
