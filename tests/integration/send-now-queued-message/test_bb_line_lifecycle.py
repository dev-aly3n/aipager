"""Black-box rows for the "⏳ Queued" reply line (design.md SC1-SC19).

Every row drives the daemon through its public surface only (Telegram
messages, hook datagrams, transcript queue-operation lines, commands and
taps; see entrypoints.md) on the virtual loop of conftest.py, and reads the
result off the fake chat. Message 1 starts turn 1; message 2 is typed while
it runs and lands in Claude's queue.

Harness timing (from the developer, harness only): a line delete is a
low-priority call, so in this rate-limited chat it lands about 6.5 s after
the event; rows allow 10 s (virtual) for "line deleted".
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs
from aipager.bot import session_parity

CHAT = 256113222
LINE_TEXT = "⏳ Queued - Claude will read it after the current step"
BUTTON_TEXT = "⚡ Send now"
DELETE_WINDOW = 10.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _queued(r, *, evidence: bool = True, pickups: int = 1) -> float:
    """Turn 1 running, message 2 queued behind it. Returns the pick-up
    time."""
    await r.turn(1, "first")
    return await r.queue(2, "queued two", evidence=evidence, pickups=pickups)


async def _lined(r) -> dict:
    """As :func:`_queued`, waited until message 2's line is up."""
    await _queued(r)
    line = await r.wait_line(2, timeout=15)
    assert line is not None, "precondition: the line never appeared"
    return line


async def _until(vloop, t: float) -> None:
    await asyncio.sleep(max(0.0, t - vloop.time()))


# ── SC1: the line appears after the delay, not before ──────────────────────

def _sc1(r, vloop):
    async def scenario():
        w = r.worker()
        t0 = await _queued(r)
        # INSTANT: up the moment Claude's queue shows the message.
        await _until(vloop, t0 + 0.5)
        at_9 = [dict(ln) for ln in r.chat.lines.values()]
        await _until(vloop, t0 + 12.5)
        at_11 = [dict(ln) for ln in r.chat.lines.values()]
        w.cancel()
        return at_9, at_11
    return _run(vloop, scenario())


def test_sc1_the_line_is_up_within_half_a_second(replay, vloop, pty):
    at_half, _ = _sc1(replay, vloop)
    assert len(at_half) == 1 and at_half[0]["reply_to"] == 2


def test_sc1_exactly_one_line_by_12_5_seconds(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert len(at_11) == 1


def test_sc1_line_replies_to_the_queued_message(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert [ln["reply_to"] for ln in at_11] == [2]


def test_sc1_line_text_is_exact(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert [ln["text"] for ln in at_11] == [LINE_TEXT]


def test_sc1_line_has_exactly_one_button(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert [ln["buttons"] for ln in at_11] == [1]


def test_sc1_button_text_is_exact(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert [ln["button_text"] for ln in at_11] == [BUTTON_TEXT]


def test_sc1_callback_data_fits_telegrams_64_byte_cap(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    assert all(len(ln["callback_data"].encode()) <= 64 for ln in at_11)


def test_sc1_callback_data_names_the_session_and_target(replay, vloop, pty):
    _, at_11 = _sc1(replay, vloop)
    (data,) = [ln["callback_data"] for ln in at_11]
    assert data.startswith("_:sx:") and data.endswith(":now:2")


# ── SC2-SC5: taken or not held before the delay -> never a line ────────────

def test_sc2_absorbed_at_5s_deletes_its_line_at_once(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r)
        await _until(vloop, t0 + 5.0)
        r.absorb("queued two")
        await _until(vloop, t0 + 5.5)
        w.cancel()

    _run(vloop, scenario())
    assert len(r.chat.lines) == 1 and r.chat.live_lines() == []


def test_sc2_absorbed_at_5s_shows_thumbs_up(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r)
        await _until(vloop, t0 + 5.0)
        r.absorb("queued two")
        await _until(vloop, t0 + 25.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.reactions.get(2, [])[-1:] == ["👍"]


def test_sc3_popped_by_a_stop_at_5s_deletes_its_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r)
        await _until(vloop, t0 + 5.0)
        r.fate("dequeue")
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("the popped turn's step")
        await _until(vloop, t0 + 30.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.live_lines() == []


def test_sc4_no_enqueue_evidence_never_gets_a_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r, evidence=False)
        await _until(vloop, t0 + 25.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}


def test_sc5_the_turns_own_trigger_never_gets_a_line(replay, vloop, pty):
    """Sent while idle: it is the turn's trigger, never a queued target,
    even with an ``enqueue`` line for it in the transcript."""
    r = replay

    async def scenario():
        w = r.worker()
        r.enqueue("first")
        await r.turn(1, "first")
        await asyncio.sleep(25)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}


# ── SC6-SC11: taken after the line appeared -> the line goes ───────────────

def test_sc6_absorbed_after_the_line_deletes_it(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.absorb("queued two")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_sc6_absorbed_after_the_line_shows_thumbs_up(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.absorb("queued two")
        await r.wait_deleted(line["id"], DELETE_WINDOW)
        await asyncio.sleep(2)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.reactions.get(2, [])[-1:] == ["👍"]


def test_sc7_absorption_right_before_the_stop_deletes_the_line(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.absorb("queued two")
        r.stop("answer first")  # same step: no tick in between
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def _slow_finish(r, monkeypatch, seconds: float):
    real = r.bot._mark_ran_commands

    async def _slow(sess):
        await asyncio.sleep(seconds)
        await real(sess)

    monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow)


def test_sc8_absorption_during_a_slowed_finish_deletes_before_it_ends(
        replay, vloop, pty, monkeypatch):
    """The finish is held 20 s; the absorption lands 1 s after the Stop.
    The line's delete reaches the chat before the finish's answer does."""
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        _slow_finish(r, monkeypatch, 20.0)
        t_stop = vloop.time()
        r.stop("answer first")
        await asyncio.sleep(1.0)
        r.absorb("queued two")
        await asyncio.sleep(40)
        w.cancel()
        answers = [t for e, t in r.chat.calls
                   if e == "sendRichMessage" and t > t_stop]
        return r.chat.lines[line["id"]].get("deleted_t"), answers

    deleted_t, answers = _run(vloop, scenario())
    assert deleted_t is not None and answers and deleted_t < min(answers)


def test_sc9_popped_line_is_deleted_before_the_popped_turns_card(
        replay, vloop, pty, monkeypatch):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        _slow_finish(r, monkeypatch, 20.0)
        r.fate("dequeue")
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("the popped turn's first step")
        await asyncio.sleep(45)
        w.cancel()
        cards = [c["t"] for c in r.chat.cards.values() if c["reply_to"] == 2]
        return r.chat.lines[line["id"]].get("deleted_t"), cards

    deleted_t, cards = _run(vloop, scenario())
    assert deleted_t is not None and cards and deleted_t < min(cards)


def test_sc10_pop_all_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.fate("popAll", "queued two")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_sc10_pop_all_keeps_the_eyes_reaction(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.fate("popAll", "queued two")
        await r.wait_deleted(line["id"], DELETE_WINDOW)
        await asyncio.sleep(2)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.reactions.get(2, [])[-1:] == ["👀"]


def test_sc11_delivered_to_a_running_agent_deletes_the_line(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="general-purpose")
        r.tool("agent step", agent_id="a1")
        await asyncio.sleep(1)
        await r.queue(2, "queued two")
        line = await r.wait_line(2, timeout=15)
        assert line is not None, "precondition: the line never appeared"
        r.fate("remove", "queued two", reason="delivered_to_agent")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


# ── SC12-SC17: discarded -> the line goes ──────────────────────────────────

def test_sc12_stop_command_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await r.cmd("_handle_stop_cmd", "/stop")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_sc12_stop_command_marks_the_message_shrug(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await r.cmd("_handle_stop_cmd", "/stop")
        await r.wait_deleted(line["id"], DELETE_WINDOW)
        await asyncio.sleep(2)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.reactions.get(2, [])[-1:] == ["🤷"]


def test_sc13_clearqueue_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await r.cmd("_handle_clearqueue_cmd", "/clearqueue")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def _confirm_data(r, verb: str) -> str:
    """The confirm button whose verb starts with *verb*: since P4 the
    End and Restart confirms carry the turn they were shown for
    (`endok<key>`, `restartok<key>`)."""
    markup = r.last_reply_kwargs[0]["reply_markup"]
    return next(b.callback_data for row in markup.inline_keyboard
                for b in row if b.callback_data.rsplit(":", 1)[-1].startswith(verb))


def test_sc14_confirmed_kill_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await r.cmd("_handle_kill_cmd", "/kill sendnow_harness")
        await r.tap(9901, data=_confirm_data(r, "endok"))
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_sc15_safety_halt_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.hook(type="safety_blocked", tool="Bash", reason="blocked by policy")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


def test_sc16_session_end_other_deletes_the_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.hook(hook_event_name="SessionEnd", reason="other")
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


@pytest.mark.parametrize("reason", ["clear", "resume"])
def test_sc16_session_end_clear_or_resume_keeps_the_line(
        replay, vloop, pty, reason):
    r = replay

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        r.hook(hook_event_name="SessionEnd", reason=reason)
        await asyncio.sleep(15)
        w.cancel()
        return r.chat.lines[line["id"]]["deleted"]

    assert _run(vloop, scenario()) is False


def test_sc17_restart_deletes_the_line(replay, vloop, pty):
    r = replay

    async def _restart_cmd(update, ctx):
        await session_parity.handle_restart_cmd(r.bot, update, ctx)

    r.bot._t_restart_cmd = _restart_cmd

    async def scenario():
        w = r.worker()
        line = await _lined(r)
        await r.cmd("_t_restart_cmd", "/restart sendnow_harness")
        await r.tap(9902, data=_confirm_data(r, "restartok"))
        ok = await r.wait_deleted(line["id"], DELETE_WINDOW)
        w.cancel()
        return ok

    assert _run(vloop, scenario()) is True


# ── SC18-SC19: one line per message, and only its own ──────────────────────

def test_sc18_duplicate_pickup_still_one_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r, pickups=2)
        await _until(vloop, t0 + 25.0)
        w.cancel()

    _run(vloop, scenario())
    assert sum(1 for ln in r.chat.lines.values() if ln["reply_to"] == 2) == 1


def test_sc18_pickup_repeated_later_still_one_line(replay, vloop, pty):
    """The duplicate arrives 5 s after the first: still one timer."""
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await _queued(r)
        await _until(vloop, t0 + 5.0)
        await r.hook(type="queue_pickup", consumed=[
            {"msg_id": 2, "chat_id": CHAT, "raw_text": "queued two"}],
            expired=[])
        await _until(vloop, t0 + 30.0)
        w.cancel()

    _run(vloop, scenario())
    assert sum(1 for ln in r.chat.lines.values() if ln["reply_to"] == 2) == 1


def _sc19(r, vloop):
    """Two messages queued: each gets its own line (operator, 2026-09-29).
    When 2 is absorbed, only 2's line goes; 3's stays."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        await asyncio.sleep(6)
        await r.queue(3, "queued three")
        l2 = await r.wait_line(2, timeout=2)
        l3 = await r.wait_line(3, timeout=2)
        assert l2 is not None and l3 is not None, "precondition: two lines"
        r.absorb("queued two")
        await asyncio.sleep(1.0)
        w.cancel()
        return (r.chat.lines[l2["id"]]["deleted"],
                r.chat.lines[l3["id"]]["deleted"])
    return _run(vloop, scenario())


def test_sc19_absorbing_two_deletes_twos_line(replay, vloop, pty):
    two_gone, _ = _sc19(replay, vloop)
    assert two_gone is True


def test_sc19_absorbing_two_keeps_threes_line(replay, vloop, pty):
    _, three_gone = _sc19(replay, vloop)
    assert three_gone is False


def test_ui_text_has_no_em_dash(replay, vloop, pty):
    """The line and button as sent (SC41's em-dash rule, observed)."""
    _, at_11 = _sc1(replay, vloop)
    assert not any("—" in (ln["text"] + ln["button_text"])
                   for ln in at_11)
