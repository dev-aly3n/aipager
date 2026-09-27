"""Black-box rows for iteration 3's two behaviour changes.

(1) Every terminal write for a session is serialized: a prompt's text and
    its Enter are never split by another write (a ``/stop`` Escape lands
    after the Enter), and a write that arrives while the chord's Ctrl+S is
    going out is delivered after it (the chord stays whole).
(2) A tap whose chord is still going out 0.8 s after the tap toasts
    ``Sending to Claude now`` (within the 1 s callback bound, exactly one
    answer); the line is then deleted on success, or edited to the
    "could not reach" text with its button kept on failure. The edited line
    can be tapped again. A second tap during a slow chord makes no second
    chord. ``/now`` still replies with the final outcome.

Every terminal write goes through the harness's recording seam
(``inject._run``); this file only wraps it to know when each write ENDS
(a ``dtach -p`` write is delivered when it returns) and to make chosen
writes slow or failing. Nothing in ``asyncio`` is patched.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from aipager import preferences as prefs
from aipager.dtach import inject

CHAT = 256113222
CTRL_X = b"\x18"
CTRL_S = b"\x13"
ESC = b"\x1b"
CHORD = [CTRL_X, CTRL_S]

SENT_TOAST = "Sent to Claude now"
SENDING = "Sending to Claude now"
NOW_SENT = "⚡ Sent to Claude now"
FAILED = "Could not reach Claude, try again"
LINE_TEXT = "⏳ Queued - Claude will read it after the current step"
LINE_UNREACHED = ("⏳ Queued - could not reach Claude, tap Send now to "
                  "try again")
BUTTON = "⚡ Send now"

#: The real typing path, captured at import, before the ``daemon`` fixture
#: replaces it with a mock: the prompt rows need its text and its Enter to
#: go through the recording seam as two writes.
_REAL_SEND_TEXT_AND_ENTER = inject.send_text_and_enter

#: A Ctrl+X ``dtach -p`` this slow makes the chord outlast the tap's 0.8 s
#: wait and the dispatcher's 1 s ack bound.
SLOW = 1.0
#: The callback ack bound (entrypoints.md: a toast after it is dropped).
ACK_BOUND = 1.0
#: How long a "the line stays / goes" row watches: longer than a line
#: delete takes to reach this rate-limited chat.
KEEP_WINDOW = 12.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


class Wire:
    """Wraps the recording seam: ``log`` holds ``[start, end, bytes, argv]``
    for every write (``end`` is when its ``dtach -p`` returned). *delay*
    maps a write's bytes to extra seconds it takes; *fail* to whether it
    exits with an error."""

    def __init__(self, monkeypatch, clock, *, delay=None, fail=None):
        self.log: list[list] = []
        self.delay = delay or (lambda b: 0.0)
        self.fail = fail or (lambda b: False)
        base = inject._run

        async def _run(args, stdin=b"", timeout=5):
            b = bytes(stdin)
            rec = [clock(), None, b, [str(a) for a in args]]
            self.log.append(rec)
            out = await base(args, stdin=stdin, timeout=timeout)
            seconds = self.delay(b)
            if seconds:
                await asyncio.sleep(seconds)
            rec[1] = clock()
            if self.fail(b):
                return False, ""
            return out

        monkeypatch.setattr(inject, "_run", _run)

    def data(self, since: int = 0) -> list[bytes]:
        return [rec[2] for rec in self.log[since:]]

    def chords(self) -> int:
        seq = self.data()
        return sum(1 for a, b in zip(seq, seq[1:])
                   if a == CTRL_X and b == CTRL_S)


async def _until(pred, loop, limit: float = 10.0) -> bool:
    end = loop.time() + limit
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def _lined(r) -> dict:
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    return line


def _text_answers(query) -> list[str]:
    out = []
    for c in query.answer.await_args_list:
        text = c.args[0] if c.args else c.kwargs.get("text")
        if text is not None:
            out.append(text)
    return out


async def _timed_tap(r, line_id: int) -> dict:
    """Tap *line_id*; returns the toast, how long after the tap its first
    answer with text came, and how many times the query was answered."""
    loop = r.loop
    t0 = loop.time()
    job = asyncio.ensure_future(r.tap(line_id))
    await asyncio.sleep(0)  # the tap has built its query
    query = r.last_query
    first = None
    while not job.done():
        if first is None and _text_answers(query):
            first = loop.time() - t0
        await asyncio.sleep(0.01)
    toast = await job
    if first is None and _text_answers(query):
        first = loop.time() - t0
    return {"toast": toast, "answered_at": first,
            "answers": len(query.answer.await_args_list)}


# ── (1) a /stop while a Telegram prompt is being typed ──────────────────────

def _stop_while_typing(r, vloop, monkeypatch):
    """Turn 1 runs; message 4 is typed into the session with a slow text
    write (0.4 s), and ``/stop`` is issued the moment that write starts.
    Returns ``(wire log, /stop's virtual time, index of the text write)``."""
    monkeypatch.setattr(inject, "send_text_and_enter",
                        _REAL_SEND_TEXT_AND_ENTER)

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: 0.4 if b"typed four" in b else 0.0)
        r.say(4, "typed four")
        ok = await _until(
            lambda: any(b"typed four" in rec[2] for rec in wire.log), r.loop)
        assert ok, "precondition: the prompt's text was never written"
        t_stop = r.loop.time()
        await r.cmd("_handle_stop_cmd", "/stop")
        await r.updates.join()
        await asyncio.sleep(3.0)
        w.cancel()
        i = next(k for k, rec in enumerate(wire.log)
                 if b"typed four" in rec[2])
        return wire.log, t_stop, i
    return _run(vloop, scenario())


def test_stop_while_typing_precondition_stop_came_mid_text(
        rp, vloop, monkeypatch):
    log, t_stop, i = _stop_while_typing(rp, vloop, monkeypatch)
    assert t_stop < log[i][1]


def test_stop_while_typing_precondition_an_escape_was_written(
        rp, vloop, monkeypatch):
    log, _, _ = _stop_while_typing(rp, vloop, monkeypatch)
    assert any(rec[2] == ESC for rec in log)


def test_stop_while_typing_text_is_followed_by_its_enter_not_escape(
        rp, vloop, monkeypatch):
    log, _, i = _stop_while_typing(rp, vloop, monkeypatch)
    assert i + 1 < len(log) and log[i + 1][2] != ESC


def test_stop_while_typing_escape_starts_after_the_enter_is_delivered(
        rp, vloop, monkeypatch):
    """The Enter is the first write after the text that is not an Escape;
    every Escape after the text starts once that Enter has returned."""
    log, _, i = _stop_while_typing(rp, vloop, monkeypatch)
    j = next(k for k in range(i + 1, len(log)) if log[k][2] != ESC)
    escapes = [rec[0] for rec in log[i + 1:] if rec[2] == ESC]
    assert escapes and min(escapes) >= log[j][1]


def test_stop_while_typing_no_write_overlaps_another(rp, vloop, monkeypatch):
    """Serialized: no write starts before the one ahead of it returned."""
    log, _, _ = _stop_while_typing(rp, vloop, monkeypatch)
    assert all(b[0] >= a[1] for a, b in zip(log, log[1:]))


# ── (1) a /stop arriving while the chord's Ctrl+S is going out ─────────────

def _stop_during_ctrl_s(r, vloop, monkeypatch):
    """Tap a line; the Ctrl+S ``dtach -p`` takes 1 s and ``/stop`` is
    issued the moment it starts. Returns ``(wire log from the tap, /stop's
    virtual time)``."""
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: SLOW if b == CTRL_S else 0.0)
        job = asyncio.ensure_future(r.tap(line["id"]))
        ok = await _until(
            lambda: any(rec[2] == CTRL_S for rec in wire.log), r.loop)
        assert ok, "precondition: the chord's Ctrl+S was never written"
        t_stop = r.loop.time()
        await r.cmd("_handle_stop_cmd", "/stop")
        await job
        await asyncio.sleep(3.0)
        w.cancel()
        return wire.log, t_stop
    return _run(vloop, scenario())


def test_stop_during_ctrl_s_precondition_stop_came_mid_write(
        rp, vloop, monkeypatch):
    log, t_stop = _stop_during_ctrl_s(rp, vloop, monkeypatch)
    s = next(rec for rec in log if rec[2] == CTRL_S)
    assert t_stop < s[1]


def test_stop_during_ctrl_s_chord_stays_whole(rp, vloop, monkeypatch):
    log, _ = _stop_during_ctrl_s(rp, vloop, monkeypatch)
    assert [rec[2] for rec in log[:2]] == CHORD


def test_stop_during_ctrl_s_escape_after_ctrl_s_is_delivered(
        rp, vloop, monkeypatch):
    log, _ = _stop_during_ctrl_s(rp, vloop, monkeypatch)
    s_end = next(rec[1] for rec in log if rec[2] == CTRL_S)
    escapes = [rec[0] for rec in log if rec[2] == ESC]
    assert escapes and min(escapes) >= s_end


# ── (2) the slow tap: exactly one answer, within the bound ─────────────────

def _slow_tap(r, vloop, monkeypatch, *, fails: bool = False):
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: SLOW if b == CTRL_X else 0.0,
                    fail=lambda b: fails and b == CTRL_S)
        tap = await _timed_tap(r, line["id"])
        await asyncio.sleep(KEEP_WINDOW)
        w.cancel()
        return tap, wire.data(), dict(r.chat.lines[line["id"]])
    return _run(vloop, scenario())


def test_slow_tap_is_answered_exactly_once(rp, vloop, monkeypatch):
    """entrypoints.md: exactly one ``query.answer(text)``; a later blank
    ack or outcome toast would be a second answer."""
    tap, _, _ = _slow_tap(rp, vloop, monkeypatch)
    assert tap["answers"] == 1


def test_slow_failed_tap_is_answered_exactly_once(rp, vloop, monkeypatch):
    tap, _, _ = _slow_tap(rp, vloop, monkeypatch, fails=True)
    assert tap["answers"] == 1


def test_slow_failed_tap_toast_within_the_bound(rp, vloop, monkeypatch):
    tap, _, _ = _slow_tap(rp, vloop, monkeypatch, fails=True)
    assert tap["answered_at"] is not None and tap["answered_at"] < ACK_BOUND


def test_slow_failed_tap_never_toasts_sent(rp, vloop, monkeypatch):
    """The Ctrl+S failed: "Sent to Claude now" would be false."""
    _slow_tap(rp, vloop, monkeypatch, fails=True)
    assert SENT_TOAST not in rp.chat.toasts


def test_fast_tap_still_toasts_its_outcome(rp, vloop, monkeypatch):
    """Partner row (boundary, the fast side of 0.8 s): an unhurried chord
    is toasted with its own outcome, not "Sending"."""
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        Wire(monkeypatch, vloop.time)
        tap = await _timed_tap(r, line["id"])
        w.cancel()
        return tap
    r = rp
    assert _run(vloop, scenario())["toast"] == SENT_TOAST


# ── (2) a second tap while a slow chord is going out ───────────────────────

def _double_tap(r, vloop, monkeypatch, *, second_at: float):
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: SLOW if b == CTRL_X else 0.0)
        first = asyncio.ensure_future(_timed_tap(r, line["id"]))
        await asyncio.sleep(second_at)
        second = await _timed_tap(r, line["id"])
        first = await first
        await asyncio.sleep(KEEP_WINDOW)
        w.cancel()
        return first, second, wire, dict(r.chat.lines[line["id"]])
    return _run(vloop, scenario())


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_during_a_slow_chord_writes_one_chord(
        rp, vloop, monkeypatch, second_at):
    """0.3 s: before the first tap's toast; 0.9 s: after it, the Ctrl+X
    still going out."""
    _, _, wire, _ = _double_tap(rp, vloop, monkeypatch, second_at=second_at)
    assert wire.data() == CHORD


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_first_toast_is_sending(rp, vloop, monkeypatch, second_at):
    first, _, _, _ = _double_tap(rp, vloop, monkeypatch, second_at=second_at)
    assert first["toast"] == SENDING


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_second_toast_is_a_sending_one(
        rp, vloop, monkeypatch, second_at):
    """Design, tap flow 10: a chord already in flight answers as sent;
    never a refusal, a failure or silence."""
    _, second, _, _ = _double_tap(rp, vloop, monkeypatch, second_at=second_at)
    assert second["toast"] in (SENT_TOAST, SENDING)


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_second_answered_within_the_bound(
        rp, vloop, monkeypatch, second_at):
    _, second, _, _ = _double_tap(rp, vloop, monkeypatch, second_at=second_at)
    assert (second["answered_at"] is not None
            and second["answered_at"] < ACK_BOUND)


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_each_query_answered_once(
        rp, vloop, monkeypatch, second_at):
    first, second, _, _ = _double_tap(rp, vloop, monkeypatch,
                                      second_at=second_at)
    assert (first["answers"], second["answers"]) == (1, 1)


@pytest.mark.parametrize("second_at", [0.3, 0.9])
def test_double_tap_line_deleted_and_never_edited(
        rp, vloop, monkeypatch, second_at):
    _, _, _, line = _double_tap(rp, vloop, monkeypatch, second_at=second_at)
    assert (line["deleted"], line.get("edits", 0)) == (True, 0)


# ── (2) the failure-edited line, tapped again ──────────────────────────────

def _retap(r, vloop, monkeypatch):
    """A slow chord whose Ctrl+S fails edits the line; the terminal then
    recovers and the edited line is tapped again. Returns ``(the line after
    the first tap, the second toast, the second tap's writes, the line
    KEEP_WINDOW s after the second tap)``."""
    state = {"slow": True}

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: SLOW if state["slow"] and b == CTRL_X
                    else 0.0,
                    fail=lambda b: state["slow"] and b == CTRL_S)
        await r.tap(line["id"])
        ok = await _until(
            lambda: r.chat.lines[line["id"]].get("edits", 0) > 0, r.loop,
            limit=KEEP_WINDOW)
        assert ok, "precondition: the failed slow chord never edited the line"
        edited = dict(r.chat.lines[line["id"]])
        state["slow"] = False
        start = len(wire.log)
        toast = await r.tap(line["id"])
        await asyncio.sleep(KEEP_WINDOW)
        w.cancel()
        return edited, toast, wire.data(start), dict(r.chat.lines[line["id"]])
    return _run(vloop, scenario())


def test_retap_precondition_line_was_edited_with_its_button(
        rp, vloop, monkeypatch):
    edited, _, _, _ = _retap(rp, vloop, monkeypatch)
    assert (edited["deleted"], edited["text"], edited["button_text"]) == (
        False, LINE_UNREACHED, BUTTON)


def test_retap_of_the_edited_line_writes_the_chord(rp, vloop, monkeypatch):
    _, _, writes, _ = _retap(rp, vloop, monkeypatch)
    assert writes == CHORD


def test_retap_of_the_edited_line_toasts_sent(rp, vloop, monkeypatch):
    _, toast, _, _ = _retap(rp, vloop, monkeypatch)
    assert toast == SENT_TOAST


def test_retap_of_the_edited_line_deletes_it(rp, vloop, monkeypatch):
    _, _, _, line = _retap(rp, vloop, monkeypatch)
    assert line["deleted"] is True


# ── (2) a slow chord that fails after its line already went ────────────────

def test_slow_failure_after_the_line_went_edits_nothing(rp, vloop, monkeypatch):
    """Message 2 is absorbed while a 20 s Ctrl+X is going out: its line is
    deleted by that absorption; the chord's later failure must not edit
    (resurrect) it."""
    r = rp

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        Wire(monkeypatch, vloop.time,
             delay=lambda b: 20.0 if b == CTRL_X else 0.0,
             fail=lambda b: b == CTRL_S)
        job = asyncio.ensure_future(r.tap(line["id"]))
        await asyncio.sleep(0.9)
        r.absorb("queued two")
        gone = await r.wait_deleted(line["id"], timeout=15.0)
        assert gone, "precondition: the absorbed line was not deleted in time"
        assert not job.done(), "precondition: the chord ended before"
        await job
        await asyncio.sleep(KEEP_WINDOW)
        w.cancel()
        return dict(r.chat.lines[line["id"]])
    assert _run(vloop, scenario()).get("edits", 0) == 0


# ── (2) /now still replies with the final outcome ──────────────────────────

def _slow_now(r, vloop, monkeypatch, *, fails: bool = False):
    async def scenario():
        w = r.worker()
        await _lined(r)
        wire = Wire(monkeypatch, vloop.time,
                    delay=lambda b: SLOW if b == CTRL_X else 0.0,
                    fail=lambda b: fails and b == CTRL_S)
        replies = await r.cmd("_handle_now_cmd", "/now")
        await asyncio.sleep(1.0)
        w.cancel()
        return replies, wire.data()
    return _run(vloop, scenario())


def test_slow_now_replies_sent(rp, vloop, monkeypatch):
    replies, _ = _slow_now(rp, vloop, monkeypatch)
    assert replies == [NOW_SENT]


def test_slow_now_writes_the_chord(rp, vloop, monkeypatch):
    _, writes = _slow_now(rp, vloop, monkeypatch)
    assert writes == CHORD


def test_slow_failed_now_replies_could_not_reach(rp, vloop, monkeypatch):
    replies, _ = _slow_now(rp, vloop, monkeypatch, fails=True)
    assert replies == [FAILED]


def test_slow_now_never_says_sending(rp, vloop, monkeypatch):
    replies, _ = _slow_now(rp, vloop, monkeypatch)
    assert SENDING not in replies


# ── isolation ─────────────────────────────────────────────────────────────

def test_no_write_targets_a_real_socket_or_claude_dir(rp, vloop, monkeypatch):
    """Every terminal write in these scenarios names only this test's own
    socket directory: never a /tmp/claude-dtach-* socket or ~/.claude."""
    log, _, _ = _stop_while_typing(rp, vloop, monkeypatch)
    real_claude = os.path.expanduser("~/.claude")
    argv = [a for rec in log for a in rec[3]]
    assert argv and not any(a.startswith("/tmp/claude-dtach-")
                            or a.startswith(real_claude) for a in argv)
