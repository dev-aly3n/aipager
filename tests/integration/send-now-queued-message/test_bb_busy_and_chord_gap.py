"""Black-box rows for iteration 2's two behaviour changes.

(a) Send-now refuses with "Busy, try again in a moment" (the toast and the
    ``/now`` reply), keeps the line and writes no keys while a new turn's
    hook events are held behind an older turn's finish (roadmap 8.62). The
    held event here is a plain tool call of the popped turn, not a dialog,
    so the refusal is not the dialog one.
(b) If any other write reaches the session's terminal between the chord's
    Ctrl+X and its Ctrl+S, the Ctrl+S is skipped: a lone 0x13 must never
    follow a foreign write. The Ctrl+X ``dtach -p`` write is made slow (the
    subprocess boundary, through the harness's recording seam), and a
    ``/stop`` or a Telegram prompt lands while it runs.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs
from aipager.dtach import inject

CHAT = 256113222
CHORD = [b"\x18", b"\x13"]
CTRL_X = b"\x18"
CTRL_S = b"\x13"

BUSY = "Busy, try again in a moment"
DIALOG = "Answer the open question first"
SENT_TOAST = "Sent to Claude now"
NOW_SENT = "⚡ Sent to Claude now"
FAILED = "Could not reach Claude, try again"

#: The real typing path, captured at import, before the harness's
#: ``daemon`` fixture replaces it with a mock: the prompt rows need its
#: writes to go through the recording seam like every other write.
_REAL_SEND_TEXT_AND_ENTER = inject.send_text_and_enter

#: How long the slow Ctrl+X ``dtach -p`` takes (virtual seconds): past the
#: other writers' wait for a chord in flight (so a foreign write can land in
#: the gap), and short enough that the whole tap stays under a second.
SLOW_CTRL_X = 0.7
#: A Ctrl+X slow enough that the tap as a whole passes one second.
SLOWER_CTRL_X = 1.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _lined(r) -> dict:
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    return line


# ── (a) the 8.62 turn-state hold ───────────────────────────────────────────

def _slow_finish(r, monkeypatch, seconds: float):
    """Hold turn 1's finish open (same seam as the SC8/SC9 rows)."""
    real = r.bot._mark_ran_commands

    async def _slow(sess):
        await asyncio.sleep(seconds)
        await real(sess)

    monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow)


#: Turn 1's finish is held this long: below the 30 s after which a new
#: turn goes ahead without the old finish, so the hold lasts the whole of it.
FINISH = 20.0

#: How long a "the line stays" row watches: longer than a line delete takes
#: to reach this rate-limited chat (about 7 s), so a delete would be seen.
KEEP_WINDOW = 12.0


async def _held(r, monkeypatch) -> float:
    """Turn 1 runs, message 2 is queued. Turn 1's finish is slowed; its
    Stop pops message 2 as turn 2, whose first tool call arrives while the
    finish is still out (so it is held). Message 3 is then queued with
    evidence. Returns the virtual time of the Stop."""
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    _slow_finish(r, monkeypatch, FINISH)
    r.fate("dequeue")
    t_stop = r.loop.time()
    r.stop("answer first")
    await asyncio.sleep(0.5)
    r.tool("the popped turn's first step")
    await asyncio.sleep(0.5)
    await r.queue(3, "queued three")
    return t_stop


def _held_now(r, vloop, monkeypatch, *, at: float):
    """``/now`` *at* seconds after turn 1's Stop. Returns ``(replies,
    writes made by the /now)``."""
    async def scenario():
        w = r.worker()
        t_stop = await _held(r, monkeypatch)
        await asyncio.sleep(max(0.0, t_stop + at - r.loop.time()))
        start = len(r._pty.writes)
        replies = await r.cmd("_handle_now_cmd", "/now")
        await asyncio.sleep(1.0)
        w.cancel()
        return replies, r._pty.data()[start:]
    return _run(vloop, scenario())


def _held_tap(r, vloop, monkeypatch):
    """Tap message 3's line while turn 1's finish is still out. Returns
    ``(line, toast, writes made by the tap, line deleted KEEP_WINDOW s
    later)``. Message 3 has no fate, so nothing else may delete it."""
    async def scenario():
        w = r.worker()
        t_stop = await _held(r, monkeypatch)
        line = await r.wait_line(3, timeout=15)
        assert line is not None, "precondition: message 3 got no line"
        assert r.loop.time() < t_stop + FINISH - 2, (
            "precondition: the line came after the finish")
        start = len(r._pty.writes)
        toast = await r.tap(line["id"])
        await asyncio.sleep(KEEP_WINDOW)
        deleted = r.chat.lines[line["id"]]["deleted"]
        w.cancel()
        return line, toast, r._pty.data()[start:], deleted
    return _run(vloop, scenario())


def test_held_turn_now_writes_nothing(rp, vloop, monkeypatch):
    _, writes = _held_now(rp, vloop, monkeypatch, at=3.0)
    assert writes == []


def test_held_turn_now_replies_busy(rp, vloop, monkeypatch):
    replies, _ = _held_now(rp, vloop, monkeypatch, at=3.0)
    assert replies == [BUSY]


def test_held_turn_now_is_not_the_dialog_refusal(rp, vloop, monkeypatch):
    """The held event is a plain tool call: claiming a dialog is open
    would be false."""
    replies, _ = _held_now(rp, vloop, monkeypatch, at=3.0)
    assert DIALOG not in replies


def test_held_turn_now_late_in_the_hold_still_refuses(rp, vloop, monkeypatch):
    """Boundary: 2 s before the held finish completes."""
    _, writes = _held_now(rp, vloop, monkeypatch, at=FINISH - 2.0)
    assert writes == []


def test_hold_released_now_writes_the_chord(rp, vloop, monkeypatch):
    """Partner row: the same scenario once turn 1's finish has completed
    (the hold is over, message 3 still held by Claude) presses the chord.
    The refusal above is the hold's, not a lost target's."""
    _, writes = _held_now(rp, vloop, monkeypatch, at=FINISH + 10.0)
    assert writes == CHORD


def test_hold_released_now_replies_sent(rp, vloop, monkeypatch):
    replies, _ = _held_now(rp, vloop, monkeypatch, at=FINISH + 10.0)
    assert replies == [NOW_SENT]


def test_held_turn_tap_writes_nothing(rp, vloop, monkeypatch):
    _, _, writes, _ = _held_tap(rp, vloop, monkeypatch)
    assert writes == []


def test_held_turn_tap_toasts_busy(rp, vloop, monkeypatch):
    _, toast, _, _ = _held_tap(rp, vloop, monkeypatch)
    assert toast == BUSY


def test_held_turn_tap_keeps_the_line(rp, vloop, monkeypatch):
    _, _, _, deleted = _held_tap(rp, vloop, monkeypatch)
    assert deleted is False


def test_held_turn_busy_text_has_no_em_dash():
    assert "\u2014" not in BUSY and "\u2013" not in BUSY


# ── (b) a foreign write inside the chord's gap ─────────────────────────────

def _slow_ctrl_x(monkeypatch, seconds: float = SLOW_CTRL_X):
    """The Ctrl+X ``dtach -p`` takes *seconds* to return (a loaded box).
    Every write is still recorded, when it starts, by the harness."""
    recorder = inject._run

    async def _run(args, stdin=b"", timeout=5):
        out = await recorder(args, stdin=stdin, timeout=timeout)
        if bytes(stdin) == CTRL_X:
            await asyncio.sleep(seconds)
        return out

    monkeypatch.setattr(inject, "_run", _run)


def _real_typing(monkeypatch):
    monkeypatch.setattr(inject, "send_text_and_enter",
                        _REAL_SEND_TEXT_AND_ENTER)


def _first_foreign(writes: list[bytes]) -> int | None:
    for i, b in enumerate(writes):
        if b not in (CTRL_X, CTRL_S):
            return i
    return None


def _no_ctrl_s_after_foreign(writes: list[bytes]) -> bool:
    i = _first_foreign(writes)
    assert i is not None, "precondition: no foreign write was recorded"
    return not any(CTRL_S in b for b in writes[i:])


def _foreign_inside(writes: list[bytes]) -> bool:
    """The first write is the chord's Ctrl+X and the next one is foreign:
    the scenario really put a write inside the chord's gap."""
    return len(writes) >= 2 and writes[0] == CTRL_X \
        and _first_foreign(writes) == 1


async def _stop_cmd(r):
    await r.cmd("_handle_stop_cmd", "/stop")


async def _prompt(r):
    r.say(4, "typed four")
    await r.updates.join()


def _gap_row(r, vloop, monkeypatch, foreign, *, via: str = "tap",
             slow: bool = True, foreign_first: bool = False):
    """Line up message 2; with a slow Ctrl+X, start send-now (*via* a tap
    or ``/now``) and run *foreign* 0.05 s later (or, with
    *foreign_first*, before send-now). Returns ``(line_id, answer, writes
    from send-now's start (or the foreign write's, if first), line deleted
    KEEP_WINDOW s later)``."""
    _real_typing(monkeypatch)

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        if slow:
            _slow_ctrl_x(monkeypatch)
        start = len(r._pty.writes)
        if foreign is not None and foreign_first:
            await foreign(r)
            await asyncio.sleep(0.5)
        if via == "tap":
            job = asyncio.ensure_future(r.tap(line["id"]))
        else:
            job = asyncio.ensure_future(r.cmd("_handle_now_cmd", "/now"))
        if foreign is not None and not foreign_first:
            await asyncio.sleep(0.05)
            await foreign(r)
        answer = await job
        await asyncio.sleep(KEEP_WINDOW)
        deleted = r.chat.lines[line["id"]]["deleted"]
        w.cancel()
        return line["id"], answer, r._pty.data()[start:], deleted
    return _run(vloop, scenario())


# /stop (Escape) lands inside a tap's chord

def test_gap_stop_precondition_escape_lands_inside_the_chord(
        rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd)
    assert _foreign_inside(writes)


def test_gap_stop_no_ctrl_s_after_the_foreign_write(rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd)
    assert _no_ctrl_s_after_foreign(writes)


def test_gap_stop_no_ctrl_s_written_at_all(rp, vloop, monkeypatch):
    """The Ctrl+X came first, so any Ctrl+S would be a lone one."""
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd)
    assert not any(CTRL_S in b for b in writes)


def test_gap_stop_tap_answers_could_not_reach(rp, vloop, monkeypatch):
    _, toast, _, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd)
    assert toast == FAILED


# A Telegram prompt is typed inside a tap's chord

def test_gap_prompt_precondition_text_lands_inside_the_chord(
        rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _prompt)
    assert _foreign_inside(writes)


def test_gap_prompt_no_ctrl_s_after_the_typed_text(rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _prompt)
    assert _no_ctrl_s_after_foreign(writes)


def test_gap_prompt_tap_answers_could_not_reach(rp, vloop, monkeypatch):
    _, toast, _, _ = _gap_row(rp, vloop, monkeypatch, _prompt)
    assert toast == FAILED


def test_gap_prompt_tap_keeps_the_line(rp, vloop, monkeypatch):
    """The chord did not go through and message 2 is still held."""
    _, _, _, deleted = _gap_row(rp, vloop, monkeypatch, _prompt)
    assert deleted is False


def test_gap_prompt_never_writes_escape(rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _prompt)
    assert not any(b"\x1b" in b for b in writes)


# /now's chord, with /stop inside it

def test_gap_now_no_ctrl_s_after_the_foreign_write(rp, vloop, monkeypatch):
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd, via="now")
    assert _no_ctrl_s_after_foreign(writes)


def test_gap_now_replies_could_not_reach(rp, vloop, monkeypatch):
    _, replies, _, _ = _gap_row(rp, vloop, monkeypatch, _stop_cmd, via="now")
    assert replies == [FAILED]


# Partner rows: what does NOT skip the Ctrl+S

def test_gap_slow_ctrl_x_alone_still_writes_the_chord(rp, vloop, monkeypatch):
    """No foreign write: slowness alone never drops the Ctrl+S."""
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, None)
    assert writes == CHORD


def test_gap_slow_ctrl_x_alone_toasts_sent(rp, vloop, monkeypatch):
    _, toast, _, _ = _gap_row(rp, vloop, monkeypatch, None)
    assert toast == SENT_TOAST


def test_gap_prompt_before_the_chord_keeps_its_ctrl_s(rp, vloop, monkeypatch):
    """A write that lands BEFORE the Ctrl+X is not inside the chord."""
    _, _, writes, _ = _gap_row(rp, vloop, monkeypatch, _prompt,
                               foreign_first=True)
    assert writes[-2:] == CHORD


def test_slow_chord_tap_still_toasts_its_outcome(rp, vloop, monkeypatch):
    """Error guessing (a loaded box): a Ctrl+X ``dtach -p`` of 1 s. The
    chord goes through, and the tap must still answer with its outcome
    toast (entrypoints.md: exactly one ``query.answer(text)``)."""
    r = rp

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        _slow_ctrl_x(monkeypatch, SLOWER_CTRL_X)
        toast = await r.tap(line["id"])
        await asyncio.sleep(3.0)
        w.cancel()
        return toast

    assert _run(vloop, scenario()) == SENT_TOAST


def test_gap_prompt_before_the_chord_toasts_sent(rp, vloop, monkeypatch):
    _, toast, _, _ = _gap_row(rp, vloop, monkeypatch, _prompt,
                              foreign_first=True)
    assert toast == SENT_TOAST
