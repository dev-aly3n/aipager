"""WHEN the "⏳ Queued" line goes up, on a chat running at its ban ceiling
with a busy card animating (operator, live test 2026-09-29: "I dont want
any delay for send now messages").

- The line goes up the moment Claude's transcript shows the message in its
  queue, whatever Claude's current step is doing: no 10 s / 3 s due rule
  (removed 2026-09-29).
- It is INSTANT (``flood_budget.PRIORITY_INSTANT``): it never waits for a
  chat token and is not suspended by minimal mode; only a Telegram mute
  refuses it.
- A due line that is not sent says why at INFO.

Same harness as the other rows (conftest.py): the virtual loop, the real
limiter with the operator's ban history (0.5/s), the public surface only.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager import config
from aipager.bot import send_now
from aipager import preferences as prefs
from aipager.bot.flood import MUTE

CHAT = 256113222
#: How late a due line may land because it waits for its token.
BUDGET_SLACK = 2.5
#: Quiet time after turn 1 starts, so the card's first calls have left the
#: chat's burst to refill before the row's own clock starts.
SETTLE = 8.0


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _until(vloop, t: float) -> None:
    await asyncio.sleep(max(0.0, t - vloop.time()))


def _line_t(r, reply_to):
    line = r.chat.line_for(reply_to)
    return None if line is None else line["t"]


async def _step_then_queue(r, vloop, *, step_age: float, mid: int = 2,
                           text: str = "queued two") -> float:
    """Turn 1 running a step that has run *step_age* seconds when message
    *mid* is typed behind it. Returns the pick-up time."""
    await r.turn(1, "first")
    await asyncio.sleep(SETTLE)
    ts = vloop.time()
    await r.tool_start("long step")
    await _until(vloop, ts + step_age)
    return await r.queue(mid, text)


async def _young_step_then_queue(r, vloop) -> tuple[float, float]:
    """Turn 1 running a step that is under 3 s old when message 2 is
    picked up behind it. Returns ``(step start, pick-up time)``. (Typing a
    message into the session takes the harness about a second, so the
    step's age at the pick-up is read, not assumed.)"""
    await r.turn(1, "first")
    await asyncio.sleep(SETTLE)
    ts = vloop.time()
    await r.tool_start("long step")
    t0 = await r.queue(2, "queued two")
    assert r.sess.parent_tool_started_at == pytest.approx(ts, abs=0.01), (
        "precondition: the step starts when its PreToolUse arrives")
    assert t0 - ts < 2.0, "precondition: the step is still young"
    return ts, t0




#: How late an INSTANT line may land after its pick-up: it waits for the
#: 30/s overall bucket only, never for the chat.
INSTANT_SLACK = 0.5


def test_the_line_has_no_due_time():
    """The due rule is gone; the evidence re-checks and the watcher's pace
    are what remain."""
    assert not hasattr(config, "QUEUED_LINE_DELAY")
    assert not hasattr(config, "QUEUED_LINE_TOOL_AGE")
    assert send_now._QUEUED_LINE_EVIDENCE_PAUSES == (0.2, 0.3, 0.5, 1.0)
    assert send_now.QUEUE_WATCH_INTERVAL == 0.25


# ── the line at once, whatever Claude's step is doing ─────────────────────

async def _old_step(r, vloop):
    return await _step_then_queue(r, vloop, step_age=7.0)


async def _young_step(r, vloop):
    _ts, t0 = await _young_step_then_queue(r, vloop)
    return t0


async def _no_step(r, vloop):
    await r.turn(1, "first")          # its one step already ended
    await asyncio.sleep(SETTLE)
    return await r.queue(2, "queued two")


async def _agents_tool_only(r, vloop):
    await r.turn(1, "first")
    await r.tool_start("agent step", agent_id="agent-bg-1")
    await asyncio.sleep(7.0)
    return await r.queue(2, "queued two")


async def _after_an_interrupted_step(r, vloop):
    await r.turn(1, "first")
    await asyncio.sleep(SETTLE)
    await r.tool_start("interrupted step")
    await asyncio.sleep(3.0)
    await r.hook(hook_event_name="PostToolUseFailure", tool_name="Bash",
                 tool_input={"command": "interrupted step"},
                 error="interrupted", is_interrupt=True)
    await asyncio.sleep(1.0)
    return await r.queue(2, "queued two")


@pytest.mark.parametrize("setup", [
    _old_step, _young_step, _no_step, _agents_tool_only,
    _after_an_interrupted_step,
], ids=["old-step", "young-step", "no-step", "agent-tool-only",
        "after-interrupted-step"])
def test_the_line_goes_up_at_once(replay, vloop, pty, setup):
    r = replay

    async def scenario():
        w = r.worker()
        t0 = await setup(r, vloop)
        await _until(vloop, t0 + 3.0)
        w.cancel()
        return t0, _line_t(r, 2)

    t0, t = _run(vloop, scenario())
    assert t is not None and t - t0 <= INSTANT_SLACK, (t0, t)


# ── taken first: no line; dropped: the line goes at once ─────────────────

def test_taken_before_its_queue_record_is_seen_never_gets_a_line(
        replay, vloop, pty):
    """Claude takes the message before its enqueue record is ever read:
    no line."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two", evidence=False)
        r.absorb("queued two")
        await asyncio.sleep(10.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.line_for(2) is None


def test_cleared_while_its_line_is_up_is_deleted_at_once(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.queue(2, "queued two")
        line = await r.wait_line(2, timeout=2)
        assert line is not None, "precondition: the line"
        t_clear = vloop.time()
        await r.cmd("_handle_clearqueue_cmd", "/clearqueue")
        gone = await r.wait_deleted(line["id"], timeout=2.0)
        w.cancel()
        return gone, r.chat.lines[line["id"]].get("deleted_t", 1e9) - t_clear

    gone, after = _run(vloop, scenario())
    assert gone is True and after <= INSTANT_SLACK, after


# ── the chat's budget does not hold the line back ────────────────────────

def test_two_messages_each_get_their_own_line_at_once(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        t2 = await r.queue(2, "queued two")
        await _until(vloop, t2 + 9.0)
        t3 = await r.queue(3, "queued three")
        await _until(vloop, t3 + 3.0)
        live = r.bot.registry.get(r.sess.name).busy_card_should_animate()
        w.cancel()
        return t2, t3, _line_t(r, 2), _line_t(r, 3), live

    t2, t3, l2, l3, live = _run(vloop, scenario())
    assert live is True, "precondition: the busy card is animating"
    assert l2 is not None and l2 - t2 <= INSTANT_SLACK, (t2, l2)
    assert l3 is not None and l3 - t3 <= INSTANT_SLACK, (t3, l3)


def test_a_chat_out_of_tokens_still_gets_the_line_at_once(replay, vloop,
                                                         vlimiter, pty):
    """Other traffic has just taken every one of the chat's tokens: the
    line still goes out at once instead of waiting for the next token."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        t0 = await r.queue(2, "queued two", evidence=False)
        budget = vlimiter._budget_for(CHAT)
        while budget.chat.take(1.0):      # the chat has no token left
            pass
        r.enqueue("queued two")           # Claude's record lands now
        await _until(vloop, t0 + 3.0)
        w.cancel()
        return t0, _line_t(r, 2)

    t0, t = _run(vloop, scenario())
    assert t is not None and t - t0 <= INSTANT_SLACK, (t0, t)


def test_a_burst_of_queued_messages_does_not_hold_back_an_answer(
        replay, vloop, pty):
    """Six messages queued at once behind an old step each get their line
    at once; an INSTANT line takes a token only when one is free, so an
    answer sent right after waits no more than the chat's next token."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        await r.tool_start("long step")
        await asyncio.sleep(5.0)
        for i in range(6):
            await r.queue(10 + i, f"burst {i}")
        await asyncio.sleep(0.5)
        t_ans = vloop.time()
        await r.bot._app.bot.send_message(chat_id=CHAT, text="an answer")
        waited = vloop.time() - t_ans
        await asyncio.sleep(5.0)
        w.cancel()
        return waited

    waited = _run(vloop, scenario())
    assert waited <= 4.0, waited
    assert all(r.chat.line_for(10 + i) is not None for i in range(6))


# ── muted / minimal ──────────────────────────────────────────────────────

def test_a_muted_chat_gets_no_line(replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        MUTE.mute(CHAT, 600)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 20.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}


def _stand_minimal(vlimiter, vloop) -> None:
    vlimiter.reset()
    vlimiter.restore([{"chat_id": CHAT,
                       "ban_stamps": [vloop.wall() - 3 * 86400.0],
                       "hourly": [[vloop.wall() - 30.0, 0, 540]],
                       "hourly_minimal": True}])


def test_minimal_mode_still_gets_the_line_at_once(replay, vloop, vlimiter,
                                                  pty):
    """Minimal mode suspends the card's decoration, not this line."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        _stand_minimal(vlimiter, vloop)
        minimal = vlimiter.minimal_mode(CHAT)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 3.0)
        w.cancel()
        return minimal, t0, _line_t(r, 2)

    minimal, t0, t = _run(vloop, scenario())
    assert minimal is True, "precondition: minimal mode"
    assert t is not None and t - t0 <= INSTANT_SLACK, (t0, t)


# ── a line not sent says why, at INFO ────────────────────────────────────

def _reasons(caplog) -> list[str]:
    return [rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.INFO
            and "queued line for 2 not sent" in rec.getMessage()]


def test_no_evidence_is_logged_at_info(replay, vloop, pty, caplog):
    r = replay
    caplog.set_level(logging.INFO, logger="aipager.bot.send_now")

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two", evidence=False)
        await _until(vloop, t0 + 1.0
                     + sum(send_now._QUEUED_LINE_EVIDENCE_PAUSES))
        w.cancel()

    _run(vloop, scenario())
    assert any("no queue evidence" in m for m in _reasons(caplog))


def test_muted_is_logged_at_info(replay, vloop, pty, caplog):
    r = replay
    caplog.set_level(logging.INFO, logger="aipager.bot.send_now")

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        MUTE.mute(CHAT, 600)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 3.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}
    assert any("chat muted" in m for m in _reasons(caplog))


# ── the step bookkeeping (no longer read by the line; kept until the dead
#    state is removed on its own) ─────────────────────────────────────────

@pytest.mark.parametrize("ending", [
    {"hook_event_name": "StopFailure", "error": "api_error"},
    {"hook_event_name": "SessionEnd", "reason": "other"},
])
def test_a_turn_or_session_end_clears_the_step(replay, vloop, pty, ending):
    """A failed turn end or the session ending: whatever step was open is
    over, so nothing later reads it as a step Claude is still in."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("step with no end")
        await asyncio.sleep(0.5)
        before = r.sess.parent_tool_started_at
        await r.hook(**ending)
        await asyncio.sleep(0.5)
        w.cancel()
        return before, r.sess.parent_tool_started_at

    before, after = _run(vloop, scenario())
    assert before is not None, "precondition: the step was open"
    assert after is None
