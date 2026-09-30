"""Black-box rows for the line under flood control, across a daemon restart,
and on the command surface (design.md SC35-SC41).

The chat carries this operator's ban history (ceiling 0.5/s, hourly budget
600 with a 540 ornament share, see conftest's ``vlimiter``). At that budget
the ornament share and the minimal-mode threshold coincide, so "ornament
budget short" is driven by standing the chat's hour at the share.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aipager import preferences as prefs
from aipager import state as state_mod
from aipager.bot.flood import MUTE
from aipager.bot.lifecycle import LifecycleMixin
from aipager.dtach.launcher import _validate_name
from aipager.miniapp.launch import validate_session_name

CHAT = 256113222
CHORD = [b"\x18", b"\x13"]
DELETE_WINDOW = 10.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _until(vloop, t: float) -> None:
    await asyncio.sleep(max(0.0, t - vloop.time()))


def _stand_at(vlimiter, vloop, used: int, **latches) -> None:
    """The chat's hour at *used* calls (one bucket 30 s old), its ban
    history kept."""
    vlimiter.reset()
    row = {"chat_id": CHAT, "ban_stamps": [vloop.wall() - 3 * 86400.0]}
    if used:
        row["hourly"] = [[vloop.wall() - 30.0, 0, used]]
    row.update(latches)
    vlimiter.restore([row])


async def _lined(r) -> dict:
    await r.turn(1, "first")
    await r.queue(2, "queued two")
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    return line


def _replies_to_2(r) -> int:
    return sum(1 for _text, kw in r.chat.sent
               if kw.get("reply_to_message_id") == 2)


# ── control: a line with no trigger stays ─────────────────────────────────

def test_control_line_stays_without_a_trigger(replay, vloop, pty):
    """Every "deleted" row is caused by its trigger: left alone, the line
    is still up 30 s later."""
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await asyncio.sleep(30)
        w.cancel()
        return r.chat.lines[line["id"]]["deleted"]

    assert _run(vloop, scenario()) is False


# ── SC35: flood-muted ─────────────────────────────────────────────────────

def _muted_row(r, vloop):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        MUTE.mute(CHAT, 600)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 20.0)
        sent_to_2 = _replies_to_2(r)
        start = len(pty_of(r).writes)
        await r.cmd("_handle_now_cmd", "/now")
        await asyncio.sleep(1)
        w.cancel()
        return sent_to_2, pty_of(r).writes[start:]
    return _run(vloop, scenario())


def pty_of(r):
    return r._pty


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def test_sc35_muted_chat_gets_no_line(rp, vloop):
    sent_to_2, _ = _muted_row(rp, vloop)
    assert sent_to_2 == 0


def test_sc35_muted_chat_now_still_writes_the_chord(rp, vloop):
    _, writes = _muted_row(rp, vloop)
    assert [b for _t, b in writes] == CHORD


# ── SC36-SC37: minimal mode / ornament budget short ───────────────────────

def _budget_row(r, vloop, vlimiter, used: int, *, recover_at=None,
                until: float = 25.0, **latches):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        _stand_at(vlimiter, vloop, used, **latches)
        minimal_at_5 = vlimiter.minimal_mode(CHAT)
        t0 = await r.queue(2, "queued two")
        if recover_at is not None:
            await _until(vloop, t0 + recover_at)
            _stand_at(vlimiter, vloop, 0)
        await _until(vloop, t0 + until)
        w.cancel()
        return minimal_at_5, [ln for ln in r.chat.lines.values()
                              if ln["reply_to"] == 2]
    return _run(vloop, scenario())


def test_sc36_minimal_mode_precondition(replay, vloop, vlimiter, pty):
    minimal, _ = _budget_row(replay, vloop, vlimiter, 540,
                             hourly_minimal=True)
    assert minimal is True


def test_sc36_minimal_mode_still_gets_the_line(replay, vloop, vlimiter, pty):
    """The line is INSTANT: minimal mode suspends the card's decoration,
    not this line (operator, 2026-09-29)."""
    _, lines = _budget_row(replay, vloop, vlimiter, 540, hourly_minimal=True)
    assert len(lines) == 1


def test_sc36_control_below_minimal_still_gets_a_line(
        replay, vloop, vlimiter, pty):
    """Boundary partner: the hour at 300 calls (under the share)."""
    _, lines = _budget_row(replay, vloop, vlimiter, 300)
    assert len(lines) == 1


def test_sc37_budget_short_still_gets_the_line(replay, vloop, vlimiter, pty):
    """The hour stands exactly at the ornament share (540), no latch: the
    INSTANT line still goes."""
    _, lines = _budget_row(replay, vloop, vlimiter, 540)
    assert len(lines) == 1


# ── SC38-SC39: a restart never leaves an orphan line ──────────────────────

def _saved_owed() -> list:
    data = json.loads(state_mod.SESSION_STATE_FILE.read_text())
    return data.get("queued_line_deletes", [])


def test_sc38_saved_state_owes_the_live_lines_delete(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        w.cancel()
        r.bot.registry.save()
        return line["id"]

    line_id = _run(vloop, scenario())
    assert [CHAT, line_id] in _saved_owed()


def test_sc38_startup_deletes_the_owed_line(replay, vloop, pty, mk_bot):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        w.cancel()
        before = r.chat.lines[line["id"]]["deleted"]
        fresh = r.restarted_bot(mk_bot)
        await fresh.recover_sessions()
        await asyncio.sleep(5)
        return before, line["id"]

    before, line_id = _run(vloop, scenario())
    assert (before, (CHAT, line_id) in r.chat.deletes) == (False, True)


def test_sc38_startup_deletes_the_owed_line_once(replay, vloop, pty, mk_bot):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        w.cancel()
        fresh = r.restarted_bot(mk_bot)
        await fresh.recover_sessions()
        await asyncio.sleep(5)
        return line["id"]

    line_id = _run(vloop, scenario())
    assert r.chat.deletes.count((CHAT, line_id)) == 1


def test_boundary_a_line_already_deleted_is_not_owed(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.absorb("queued two")
        assert await r.wait_deleted(line["id"], DELETE_WINDOW), (
            "precondition: the absorbed line was not deleted")
        await asyncio.sleep(1)
        w.cancel()
        r.bot.registry.save()
        return line["id"]

    line_id = _run(vloop, scenario())
    assert [CHAT, line_id] not in _saved_owed()


def _muted_absorption(r, vloop):
    async def _go():
        w = r.worker()
        line = await _lined(r)
        MUTE.mute(CHAT, 600)
        r.absorb("queued two")
        await asyncio.sleep(20)
        w.cancel()
        return line["id"]
    return _go()


def test_sc39_muted_absorption_sends_no_delete(replay, vloop, pty):
    r = replay
    line_id = _run(vloop, _muted_absorption(r, vloop))
    assert (CHAT, line_id) not in r.chat.deletes


def test_sc39_muted_absorption_is_deleted_at_startup(
        replay, vloop, pty, mk_bot):
    r = replay

    async def scenario():
        line_id = await _muted_absorption(r, vloop)
        MUTE.clear()
        fresh = r.restarted_bot(mk_bot)
        await fresh.recover_sessions()
        await asyncio.sleep(5)
        return line_id

    line_id = _run(vloop, scenario())
    assert (CHAT, line_id) in r.chat.deletes


def test_error_guess_startup_while_still_muted_keeps_it_owed(
        replay, vloop, pty, mk_bot):
    """Still muted at startup: no delete, and the next save still owes it."""
    r = replay

    async def scenario():
        line_id = await _muted_absorption(r, vloop)
        fresh = r.restarted_bot(mk_bot)
        await fresh.recover_sessions()
        await asyncio.sleep(5)
        fresh.registry.save()
        return line_id

    line_id = _run(vloop, scenario())
    assert ((CHAT, line_id) in r.chat.deletes,
            [CHAT, line_id] in _saved_owed()) == (False, True)


# ── SC40: the command surface ─────────────────────────────────────────────

def test_sc40_now_is_refused_as_a_session_name_from_telegram():
    clean, err = validate_session_name("now")
    assert (clean, bool(err)) == ("", True)


def test_sc40_now_is_refused_as_a_session_name_from_the_cli():
    assert _validate_name("now") is not None


def test_sc40_a_name_near_now_is_still_allowed():
    """Boundary partner: only the exact word is reserved."""
    assert validate_session_name("nowish") == ("nowish", "")


def test_sc40_now_is_in_the_bot_command_list():
    assert "now" in {c.command for c in LifecycleMixin._command_list(set())}


def test_sc40_now_is_in_help(replay, vloop, pty):
    r = replay

    async def scenario():
        replies = await r.cmd("_handle_help_cmd", "/help")
        await asyncio.sleep(1)
        return replies + [t for t, _kw in r.chat.sent]

    texts = _run(vloop, scenario())
    assert any("/now" in (t or "") for t in texts)


# ── SC41 (observed part): UI strings carry no em dash ─────────────────────

def test_sc41_observed_line_and_replies_have_no_em_dash(rp, vloop):
    r = rp

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        replies = await r.cmd("_handle_now_cmd", "/now")
        toast = await r.tap(line["id"])
        w.cancel()
        return [line["text"], line["button_text"], toast or "", *replies]

    texts = _run(vloop, scenario())
    assert not any("—" in t for t in texts)
