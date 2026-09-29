"""Black-box rows for the "⚡ Send now" tap and ``/now`` (design.md
SC20-SC34, plus the refusal rows of "Tap and /now flows").

Terminal writes are read off the ``pty`` recorder (the one subprocess seam
every terminal write shares). The chord is ``[b"\\x18", b"\\x13"]``;
Escape would be ``b"\\x1b"``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager.dtach import inject
from aipager.team import Role, Team, User as TeamUser

CHAT = 256113222
OTHER_CHAT = -1009990001
CHORD = [b"\x18", b"\x13"]
DELETE_WINDOW = 10.0

SENT_TOAST = "Sent to Claude now"
TAKEN_TOAST = "Already taken"
DIALOG = "Answer the open question first"
TYPING = "Busy typing a prompt, try again in a moment"
FAILED = "Could not reach Claude, try again"
NOW_SENT = "⚡ Sent to Claude now"
NOTHING = "Nothing is waiting in the queue"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _lined(r) -> dict:
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    return line


def _tap_row(r, vloop, before=None, *, after: float = DELETE_WINDOW,
             **tap_kw):
    """Line up message 2, run *before(r)* (sync or async), tap, wait
    *after* seconds. Returns ``(line_id, toast, writes)``; the writes are
    only those made from the tap on."""
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        if before is not None:
            out = before(r)
            if asyncio.iscoroutine(out):
                await out
        start = len(r_pty(r).writes)
        toast = await r.tap(line["id"], **tap_kw)
        await asyncio.sleep(after)
        w.cancel()
        return line["id"], toast, r_pty(r).writes[start:]
    return _run(vloop, scenario())


def r_pty(r):
    return r._pty


@pytest.fixture
def rp(replay, pty):
    """The replay with its terminal recorder attached."""
    replay._pty = pty
    return replay


# ── SC20: a held message's tap presses the chord ──────────────────────────

def test_sc20_tap_writes_ctrl_x_then_ctrl_s(rp, vloop):
    _, _, writes = _tap_row(rp, vloop)
    assert [b for _t, b in writes] == CHORD


def test_sc20_chord_bytes_are_separate_writes_at_least_gap_apart(rp, vloop):
    _, _, writes = _tap_row(rp, vloop)
    (t1, _), (t2, _) = writes
    assert t2 - t1 >= inject.SEND_NOW_CHORD_GAP - 1e-6


def test_sc20_chord_gap_constant_is_a_tenth_of_a_second():
    assert inject.SEND_NOW_CHORD_GAP == pytest.approx(0.1)


def test_sc20_tap_never_writes_escape(rp, vloop):
    _, _, writes = _tap_row(rp, vloop)
    assert not any(b"\x1b" in b for _t, b in writes)


def test_sc20_tap_toast_is_sent(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop)
    assert toast == SENT_TOAST


def test_sc20_tap_deletes_the_line(rp, vloop):
    line_id, _, _ = _tap_row(rp, vloop)
    assert rp.chat.lines[line_id]["deleted"] is True


def test_sc20_tap_does_not_stop_the_busy_card(rp, vloop):
    _tap_row(rp, vloop)
    texts = [t or "" for c in rp.chat.cards.values() for t in c["texts"]]
    assert not any("Stopped" in t for t in texts)


def test_sc20_tap_keeps_the_turn_card_live(rp, vloop):
    """Nothing is cancelled: the running turn's card keeps its Stop."""
    _tap_row(rp, vloop)
    assert len(rp.chat.live_cards()) == 1


# ── SC21-SC22: the message is no longer held ──────────────────────────────

def test_sc21_after_dequeue_tap_writes_nothing(rp, vloop):
    _, _, writes = _tap_row(rp, vloop, lambda r: r.fate("dequeue"))
    assert writes == []


def test_sc21_after_dequeue_tap_toasts_already_taken(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop, lambda r: r.fate("dequeue"))
    assert toast == TAKEN_TOAST


def test_sc21_after_dequeue_tap_deletes_the_line(rp, vloop):
    line_id, _, _ = _tap_row(rp, vloop, lambda r: r.fate("dequeue"))
    assert rp.chat.lines[line_id]["deleted"] is True


def _stale_tap(r, vloop):
    """The line is absorbed and deleted; its stored button is tapped."""
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.absorb("queued two")
        assert await r.wait_deleted(line["id"], DELETE_WINDOW), (
            "precondition: the absorbed line was not deleted")
        start = len(r._pty.writes)
        toast = await r.tap(line["id"], data=line["callback_data"])
        await asyncio.sleep(2)
        w.cancel()
        return toast, r._pty.writes[start:]
    return _run(vloop, scenario())


def test_sc22_stored_button_after_absorption_writes_nothing(rp, vloop):
    _, writes = _stale_tap(rp, vloop)
    assert writes == []


def test_sc22_stored_button_after_absorption_toasts_already_taken(rp, vloop):
    toast, _ = _stale_tap(rp, vloop)
    assert toast == TAKEN_TOAST


def test_error_guess_tap_after_pop_writes_nothing(rp, vloop):
    """The Stop popped message 2 as the next turn; an old tap still lands."""
    def _pop(r):
        r.fate("dequeue")
        r.stop("answer first")

    async def before(r):
        _pop(r)
        await asyncio.sleep(1)
    _, _, writes = _tap_row(rp, vloop, before)
    assert writes == []


# ── SC23-SC24: who may tap, and from where ────────────────────────────────

def _with_team(r):
    """12345 may prompt (admin), 999 is read_only."""
    r.bot.team = Team(group_id=CHAT, users={
        12345: TeamUser(id=12345, label="owner", role=Role.ADMIN),
        999: TeamUser(id=999, label="ro", role=Role.READ_ONLY),
    })


def test_sc23_read_only_tap_writes_nothing(rp, vloop):
    _, _, writes = _tap_row(rp, vloop, _with_team, user_id=999)
    assert writes == []


def test_sc23_read_only_tap_gets_a_refusal_toast(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop, _with_team, user_id=999)
    assert toast == "You can't send to this session"


def test_sc23_read_only_tap_keeps_the_line(rp, vloop):
    line_id, _, _ = _tap_row(rp, vloop, _with_team, user_id=999)
    assert rp.chat.lines[line_id]["deleted"] is False


def test_sc23_control_prompt_capable_team_member_can_tap(rp, vloop):
    """Same team, the admin taps: the gate is the role, not the team."""
    _, _, writes = _tap_row(rp, vloop, _with_team, user_id=12345)
    assert [b for _t, b in writes] == CHORD


def test_sc24_tap_from_another_chat_writes_nothing(rp, vloop):
    _, _, writes = _tap_row(rp, vloop, chat_id=OTHER_CHAT)
    assert writes == []


def test_sc24_tap_from_another_chat_toast(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop, chat_id=OTHER_CHAT)
    assert toast == "That session is no longer available"


# ── SC25-SC26: a dialog is open / aipager is typing ───────────────────────

async def _permission(r):
    await r.hook(hook_event_name="PermissionRequest", tool_name="Bash",
                 tool_input={"command": "rm -rf build"})
    await asyncio.sleep(0.5)


async def _question(r):
    await r.hook(hook_event_name="PreToolUse", tool_name="AskUserQuestion",
                 tool_input={"questions": [{
                     "question": "Which one?", "header": "Pick",
                     "multiSelect": False,
                     "options": [{"label": "A", "description": "a"},
                                 {"label": "B", "description": "b"}]}]})
    await asyncio.sleep(0.5)


@pytest.mark.parametrize("dialog", [_permission, _question],
                         ids=["permission", "question"])
def test_sc25_dialog_open_tap_writes_nothing(rp, vloop, dialog):
    _, _, writes = _tap_row(rp, vloop, dialog)
    assert writes == []


def test_sc25_dialog_open_tap_toast(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop, _permission)
    assert toast == DIALOG


def test_sc25_dialog_open_tap_keeps_the_line(rp, vloop):
    line_id, _, _ = _tap_row(rp, vloop, _permission)
    assert rp.chat.lines[line_id]["deleted"] is False


def _typing(monkeypatch, gate: asyncio.Event):
    async def _blocked(*_a, **_k):
        await gate.wait()
        return True
    monkeypatch.setattr(inject, "send_text_and_enter",
                        AsyncMock(side_effect=_blocked))


def _typing_row(r, vloop, monkeypatch):
    gate = asyncio.Event()

    async def before(r):
        _typing(monkeypatch, gate)
        r.say(4, "another message")
        await asyncio.sleep(0.5)  # its injection is now held open

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await before(r)
        start = len(r._pty.writes)
        toast = await r.tap(line["id"])
        await asyncio.sleep(DELETE_WINDOW)
        writes = r._pty.writes[start:]
        deleted = r.chat.lines[line["id"]]["deleted"]
        gate.set()
        await r.updates.join()
        w.cancel()
        return toast, writes, deleted
    return _run(vloop, scenario())


def test_sc26_tap_while_typing_writes_nothing(rp, vloop, monkeypatch):
    _, writes, _ = _typing_row(rp, vloop, monkeypatch)
    assert writes == []


def test_sc26_tap_while_typing_toast(rp, vloop, monkeypatch):
    toast, _, _ = _typing_row(rp, vloop, monkeypatch)
    assert toast == TYPING


def test_sc26_tap_while_typing_keeps_the_line(rp, vloop, monkeypatch):
    _, _, deleted = _typing_row(rp, vloop, monkeypatch)
    assert deleted is False


# ── SC27-SC28: several lines, repeated taps ───────────────────────────────

def _sc27(r, vloop):
    """Two messages queued, the session's one line under 2, tapped. The
    tap answered for the whole queue: no line comes for 3 afterwards, even
    once 2 is taken."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        await asyncio.sleep(6)
        await r.queue(3, "queued three")
        l2 = await r.wait_line(2, timeout=15)
        assert l2 is not None, "precondition: the session's line, under 2"
        await r.tap(l2["id"])
        await asyncio.sleep(15)
        two_gone = r.chat.lines[l2["id"]]["deleted"]
        r.absorb("queued two")
        await asyncio.sleep(15)
        w.cancel()
        return two_gone, r.chat.line_for(3)
    return _run(vloop, scenario())


def test_sc27_tap_deletes_the_tapped_line(rp, vloop):
    two_gone, _ = _sc27(rp, vloop)
    assert two_gone is True


def test_sc27_no_line_comes_for_the_rest_of_the_tapped_queue(rp, vloop):
    """A Send now hands Claude everything it holds (operator's rule, live
    tests 2026-09-28/29): the line does not move on to 3."""
    _, three_line = _sc27(rp, vloop)
    assert three_line is None


def _double_tap(r, vloop, gap: float):
    async def scenario():
        w = r.worker()
        line = await _lined(r)
        start = len(r._pty.writes)
        first = asyncio.ensure_future(r.tap(line["id"]))
        await asyncio.sleep(gap)
        second = asyncio.ensure_future(r.tap(line["id"]))
        toasts = await asyncio.gather(first, second)
        await asyncio.sleep(2)
        w.cancel()
        return toasts, r._pty.writes[start:]
    return _run(vloop, scenario())


@pytest.mark.parametrize("gap", [0.0, 0.05, 0.099])
def test_sc28_two_taps_within_the_gap_write_one_chord(rp, vloop, gap):
    _, writes = _double_tap(rp, vloop, gap)
    assert [b for _t, b in writes] == CHORD


# ── SC29-SC34: /now ───────────────────────────────────────────────────────

def _now_row(r, vloop, before=None, *, queue: bool = True,
             evidence: bool = True, wait: float = 3.0, **cmd_kw):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        if queue:
            await r.queue(2, "queued two", evidence=evidence)
        await asyncio.sleep(wait)
        if before is not None:
            out = before(r)
            if asyncio.iscoroutine(out):
                await out
        start = len(r._pty.writes)
        replies = await r.cmd("_handle_now_cmd", "/now", **cmd_kw)
        await asyncio.sleep(1)
        w.cancel()
        return replies, r._pty.writes[start:]
    return _run(vloop, scenario())


def test_sc29_now_before_the_line_writes_the_chord_once(rp, vloop):
    _, writes = _now_row(rp, vloop)
    assert [b for _t, b in writes] == CHORD


def test_sc29_now_before_the_line_replies_sent(rp, vloop):
    replies, _ = _now_row(rp, vloop)
    assert replies == [NOW_SENT]


def test_sc29_now_is_sent_before_any_line_exists(rp, vloop):
    _now_row(rp, vloop)
    assert rp.chat.lines == {}


def test_sc30_now_with_nothing_queued_writes_nothing(rp, vloop):
    _, writes = _now_row(rp, vloop, queue=False)
    assert writes == []


def test_sc30_now_with_nothing_queued_replies_nothing(rp, vloop):
    replies, _ = _now_row(rp, vloop, queue=False)
    assert replies == [NOTHING]


def test_sc30_now_on_an_idle_session_replies_nothing(rp, vloop):
    """No turn at all: an idle session with an empty queue."""
    async def scenario():
        start = len(rp._pty.writes)
        replies = await rp.cmd("_handle_now_cmd", "/now")
        return replies, rp._pty.writes[start:]
    assert _run(vloop, scenario()) == ([NOTHING], [])


def test_sc31_now_without_enqueue_evidence_writes_nothing(rp, vloop):
    _, writes = _now_row(rp, vloop, evidence=False)
    assert writes == []


def test_sc31_now_without_enqueue_evidence_replies_nothing(rp, vloop):
    replies, _ = _now_row(rp, vloop, evidence=False)
    assert replies == [NOTHING]


def test_boundary_now_after_evidence_ended_replies_nothing(rp, vloop):
    replies, _ = _now_row(rp, vloop, lambda r: r.fate("dequeue"))
    assert replies == [NOTHING]


def test_sc32_now_from_read_only_writes_nothing(rp, vloop):
    _, writes = _now_row(rp, vloop, _with_team, user_id=999)
    assert writes == []


def test_sc32_now_from_read_only_is_refused(rp, vloop):
    replies, _ = _now_row(rp, vloop, _with_team, user_id=999)
    assert NOW_SENT not in replies


def test_sc33_now_with_a_dialog_open_writes_nothing(rp, vloop):
    _, writes = _now_row(rp, vloop, _permission)
    assert writes == []


def test_sc33_now_with_a_dialog_open_replies_dialog(rp, vloop):
    replies, _ = _now_row(rp, vloop, _permission)
    assert replies == [DIALOG]


def test_sc34_now_from_another_chat_writes_nothing(rp, vloop):
    _, writes = _now_row(rp, vloop, chat_id=OTHER_CHAT)
    assert writes == []


def test_sc34_now_from_another_chat_replies_no_session_here(rp, vloop):
    replies, _ = _now_row(rp, vloop, chat_id=OTHER_CHAT)
    assert replies == ["No active session in this chat."]


def test_now_with_no_active_session_replies_so(rp, vloop):
    async def scenario():
        rp.bot.registry.last_active_session = None
        return await rp.cmd("_handle_now_cmd", "/now")
    assert _run(vloop, scenario()) == ["No active session."]


def test_now_while_typing_replies_busy_typing(rp, vloop, monkeypatch):
    gate = asyncio.Event()

    async def before(r):
        _typing(monkeypatch, gate)
        r.say(4, "another message")
        await asyncio.sleep(0.5)

    async def scenario():
        w = rp.worker()
        await rp.turn(1, "first")
        await rp.queue(2, "queued two")
        await before(rp)
        start = len(rp._pty.writes)
        replies = await rp.cmd("_handle_now_cmd", "/now")
        writes = rp._pty.writes[start:]
        gate.set()
        await rp.updates.join()
        w.cancel()
        return replies, writes
    assert _run(vloop, scenario()) == ([TYPING], [])


# ── error guessing: the terminal write fails ──────────────────────────────

def _failing(r):
    r._pty.ok = False


def test_error_guess_failed_write_never_sends_a_lone_ctrl_s(rp, vloop):
    """A failed Ctrl+X must not be followed by Ctrl+S (a lone 0x13 is
    Claude Code's stash key)."""
    _, _, writes = _tap_row(rp, vloop, _failing)
    assert b"\x13" not in [b for _t, b in writes]


def test_error_guess_failed_write_tap_toast(rp, vloop):
    _, toast, _ = _tap_row(rp, vloop, _failing)
    assert toast == FAILED


def test_error_guess_failed_write_keeps_the_line(rp, vloop):
    line_id, _, _ = _tap_row(rp, vloop, _failing)
    assert rp.chat.lines[line_id]["deleted"] is False


def test_error_guess_failed_write_now_reply(rp, vloop):
    replies, _ = _now_row(rp, vloop, _failing)
    assert replies == [FAILED]


def test_error_guess_malformed_target_is_an_invalid_callback(rp, vloop):
    async def scenario():
        w = rp.worker()
        line = await _lined(rp)
        data = line["callback_data"].rsplit(":", 1)[0] + ":abc"
        start = len(rp._pty.writes)
        toast = await rp.tap(line["id"], data=data)
        w.cancel()
        return toast, rp._pty.writes[start:]
    assert _run(vloop, scenario()) == ("Invalid callback", [])


def test_error_guess_tap_for_another_target_id_is_already_taken(rp, vloop):
    """A forged/stale button naming a message never queued."""
    async def scenario():
        w = rp.worker()
        line = await _lined(rp)
        data = line["callback_data"].rsplit(":", 1)[0] + ":424242"
        start = len(rp._pty.writes)
        toast = await rp.tap(line["id"], data=data)
        w.cancel()
        return toast, rp._pty.writes[start:]
    assert _run(vloop, scenario()) == (TAKEN_TOAST, [])
