"""Rows for the live test of 2026-09-27 (22:40-22:41 UTC): WHEN the
"⏳ Queued" line goes up, and that it still goes up on a chat running at
its ban ceiling with a busy card animating.

- The line is due as soon as the message is queued with transcript
  evidence AND Claude's own current step (the parent's tool call, never a
  subagent's) has run ``QUEUED_LINE_TOOL_AGE`` (3 s); otherwise
  ``QUEUED_LINE_DELAY`` (10 s) after the pick-up. A message queued behind
  a step already that old gets its line at once; one queued earlier gets
  it when the step reaches that age.
- The line is BLOCKING: it waits for its token instead of being refused
  whenever the card has left the chat below the skip reserve (in the live
  test the second queued message never got a line). A session's first
  live line goes at answer priority; further lines while one shows are
  ornaments, so a burst of queued messages never delays an answer.
- A due line that is not sent says why at INFO.

Same harness as the other rows (conftest.py): the virtual loop, the real
limiter with the operator's ban history (0.5/s), the public surface only.
At that ceiling a due line still waits for the chat's next token (about
2 s), so rows allow ``BUDGET_SLACK`` for that, and every early row is still
well clear of the 10 s fallback.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager import config
from aipager.bot import send_now
from aipager import preferences as prefs
from aipager.bot.flood import MUTE
from aipager.state import Status

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


def test_constants():
    assert config.QUEUED_LINE_TOOL_AGE == 3.0
    assert config.QUEUED_LINE_DELAY == 10.0


# ── (a) queued behind a step already >= 3 s old: the line at once ────────

def _behind_old_step(r, vloop, step_age=7.0):
    async def scenario():
        w = r.worker()
        t0 = await _step_then_queue(r, vloop, step_age=step_age)
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, _line_t(r, 2)
    return _run(vloop, scenario())


def test_a_queued_behind_a_7s_step_gets_its_line_within_a_second(
        replay, vloop, pty):
    t0, t = _behind_old_step(replay, vloop)
    assert t is not None and t - t0 <= BUDGET_SLACK, (t0, t)


def test_a_queued_behind_a_step_just_past_3s_gets_its_line_at_once(
        replay, vloop, pty):
    t0, t = _behind_old_step(replay, vloop, step_age=3.0)
    assert t is not None and t - t0 <= BUDGET_SLACK, (t0, t)


# ── (b) queued at step age 1 s: the line when the step reaches 3 s ────────

def test_b_queued_at_step_age_1s_gets_its_line_when_the_step_is_3s_old(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        ts, t0 = await _young_step_then_queue(r, vloop)
        await _until(vloop, ts + 2.9)
        early = _line_t(r, 2)
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return ts, early, _line_t(r, 2)

    ts, early, t = _run(vloop, scenario())
    assert early is None
    assert t is not None and 3.0 <= t - ts <= 3.0 + BUDGET_SLACK, (ts, t)


def test_b_a_step_that_starts_after_the_queue_still_brings_the_line_early(
        replay, vloop, pty):
    """No step running at the pick-up; one starts 1 s later and runs on:
    the line goes up when that step is 3 s old, not at 10 s."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 1.0)
        await r.tool_start("long step")
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, _line_t(r, 2)

    t0, t = _run(vloop, scenario())
    assert t is not None and 3.9 <= t - t0 <= 4.0 + BUDGET_SLACK, (t0, t)


# ── (c) no step running: no line before 10 s, a line at 10 s ─────────────

def _no_step(r, vloop):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")          # its one step already ended
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 2)
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, at_9, _line_t(r, 2)
    return _run(vloop, scenario())


def test_c_no_step_running_no_line_before_10s(replay, vloop, pty):
    _t0, at_9, _t = _no_step(replay, vloop)
    assert at_9 is None


def test_c_no_step_running_line_at_10s(replay, vloop, pty):
    t0, _at_9, t = _no_step(replay, vloop)
    assert t is not None and 10.0 <= t - t0 <= 10.0 + BUDGET_SLACK, (t0, t)


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_c_a_step_that_ended_before_3s_does_not_bring_the_line_early(
        replay, vloop, pty, event):
    """Queued behind a young step that ends (or fails) at 2.5 s, nothing
    following: no line at the 3 s mark, the line at 10 s."""
    r = replay

    async def scenario():
        w = r.worker()
        ts, t0 = await _young_step_then_queue(r, vloop)
        await _until(vloop, ts + 2.5)
        await r.tool_end("long step", event=event)
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 2)
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, at_9, _line_t(r, 2)

    t0, at_9, t = _run(vloop, scenario())
    assert at_9 is None
    assert t is not None and 10.0 <= t - t0 <= 10.0 + BUDGET_SLACK, (t0, t)


def test_c_a_background_agents_long_tool_is_not_claudes_step(
        replay, vloop, pty):
    """A detached agent's tool call running 7 s is not the parent's step:
    the message still waits the 10 s."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("agent step", agent_id="agent-bg-1")
        await asyncio.sleep(7.0)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 2)
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, at_9, _line_t(r, 2)

    t0, at_9, t = _run(vloop, scenario())
    assert at_9 is None
    assert t is not None and 10.0 <= t - t0 <= 10.0 + BUDGET_SLACK, (t0, t)


def test_a_an_agents_tool_ending_does_not_end_claudes_step(
        replay, vloop, pty):
    """Claude's own step still running at 7 s while a detached agent's tool
    call ends: the step still counts, the line goes up at once."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        ts = vloop.time()
        await r.tool_start("long step")
        await r.tool_start("agent step", agent_id="agent-bg-1")
        await _until(vloop, ts + 6.0)
        await r.tool_end("agent step", agent_id="agent-bg-1")
        await _until(vloop, ts + 7.0)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 15.0)
        w.cancel()
        return t0, _line_t(r, 2)

    t0, t = _run(vloop, scenario())
    assert t is not None and t - t0 <= BUDGET_SLACK, (t0, t)


def test_c_an_interrupted_step_does_not_linger(replay, vloop, pty):
    """A step interrupted in the terminal ends with PostToolUseFailure
    (is_interrupt), not PostToolUse: a message queued after it, with no
    new step running, is not queued behind a step."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        await r.tool_start("interrupted step")
        await asyncio.sleep(3.0)
        await r.hook(hook_event_name="PostToolUseFailure", tool_name="Bash",
                     tool_input={"command": "interrupted step"},
                     error="interrupted", is_interrupt=True)
        await asyncio.sleep(1.0)
        t0 = await r.queue(3, "queued three")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 3)
        w.cancel()
        return at_9

    assert _run(vloop, scenario()) is None


def test_c_a_new_turn_starts_with_no_step(replay, vloop, pty):
    """A turn that ended with its Stop lost (the monitor recovered it to
    idle) left its step open: the next turn starts with no step, so a
    message queued in it gets no early line."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        await r.tool_start("step whose turn lost its stop")
        await asyncio.sleep(1.0)
        r.bot.registry.transition(r.sess.name, Status.IDLE)
        await asyncio.sleep(1.0)
        await r.turn(5, "next turn", tools=0)
        await asyncio.sleep(1.0)
        t0 = await r.queue(7, "queued seven")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 7)
        w.cancel()
        return at_9

    assert _run(vloop, scenario()) is None


def test_a_the_queued_messages_own_prompt_event_keeps_the_step(
        replay, vloop, pty):
    """Claude Code fires UserPromptSubmit when a message is QUEUED: that
    must not erase the step it is queued behind (live test 2026-09-27
    23:56, where the line then came at 10 s instead of at once)."""
    t0, t = _behind_old_step(replay, vloop)
    assert t is not None and t - t0 <= BUDGET_SLACK, (t0, t)

# ── (d) taken before its line: never a line, by either deadline ──────────

def test_d_taken_before_the_step_reaches_3s_never_gets_a_line(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        ts, t0 = await _young_step_then_queue(r, vloop)
        await _until(vloop, ts + 2.5)
        r.absorb("queued two")
        await _until(vloop, t0 + 25.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}


def test_d_taken_before_10s_with_no_step_never_gets_a_line(
        replay, vloop, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 8.0)
        r.absorb("queued two")
        await _until(vloop, t0 + 25.0)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.lines == {}


def _drain_before_due(r, t0):
    """Two answers queued at the chat's gate just before the line is due
    (like the live test's other traffic): even at answer priority the line
    then waits for its token."""
    return [asyncio.ensure_future(
        r.bot._app.bot.send_message(chat_id=CHAT, text=f"answer {n}"))
        for n in range(2)]


def test_d_taken_while_its_line_waits_for_a_token_is_dropped_at_once(
        replay, vloop, pty):
    """Due at 10 s, the line waits for its token behind two answers; the
    message is taken in that wait. The line that then lands is dropped the moment it does (the
    send's own re-check), not at some later tick: it is never recorded as
    this message's line, and its delete is owed from that instant."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.8)
        _drain_before_due(r, t0)
        await _until(vloop, t0 + 10.3)
        waiting = (_line_t(r, 2) is None and 2 in r.sess.queued_line_timers)
        r.absorb("queued two")
        seen = []
        while vloop.time() < t0 + 25.0:
            await asyncio.sleep(0.01)
            if _line_t(r, 2) is not None and not seen:
                seen.append(dict(r.sess.queued_lines))
        w.cancel()
        return waiting, seen

    waiting, seen = _run(vloop, scenario())
    assert waiting is True, "precondition: the line was waiting at 10.3 s"
    assert seen, "precondition: the line landed after the message was taken"
    assert seen == [{}]
    assert r.chat.live_lines() == []


def test_d_cleared_while_its_line_waits_for_a_token_is_dropped_at_once(
        replay, vloop, pty):
    """As above, the target leaving the queue by /clearqueue during the
    wait (the transcript still shows it enqueued)."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.8)
        _drain_before_due(r, t0)
        await _until(vloop, t0 + 10.3)
        waiting = (_line_t(r, 2) is None and 2 in r.sess.queued_line_timers)
        await r.cmd("_handle_clearqueue_cmd", "/clearqueue")
        seen = []
        while vloop.time() < t0 + 25.0:
            await asyncio.sleep(0.01)
            if _line_t(r, 2) is not None and not seen:
                seen.append(dict(r.sess.queued_lines))
        # The line's delete is owed from the instant it lands; it goes out
        # behind /clearqueue's own reply and reactions at this ceiling
        # (measured: lands at +11.5 s, deleted at +25.5 s).
        await _until(vloop, t0 + 35.0)
        w.cancel()
        return waiting, seen

    waiting, seen = _run(vloop, scenario())
    assert waiting is True, "precondition: the line was waiting at 10.3 s"
    assert seen, "precondition: the line landed after the queue was cleared"
    assert seen == [{}]
    assert r.chat.live_lines() == []


# ── (e) two messages 9 s apart at the ceiling, card animating: both lined ─

def _two_queued(r, vloop):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        t2 = await r.queue(2, "queued two")
        await _until(vloop, t2 + 9.0)
        t3 = await r.queue(3, "queued three")
        await _until(vloop, t3 + 20.0)
        live = r.bot.registry.get(r.sess.name).busy_card_should_animate()
        w.cancel()
        return t2, t3, _line_t(r, 2), _line_t(r, 3), live
    return _run(vloop, scenario())


def test_e_precondition_the_busy_card_is_animating(replay, vloop, pty):
    *_rest, live = _two_queued(replay, vloop)
    assert live is True


def test_e_the_first_of_two_queued_messages_gets_a_line(replay, vloop, pty):
    t2, _t3, l2, _l3, _live = _two_queued(replay, vloop)
    assert l2 is not None and l2 - t2 <= 5.0, (t2, l2)


def test_e_the_second_of_two_queued_messages_gets_a_line(replay, vloop, pty):
    """The live test's shape (step 7 s old, a second message 9 s later).
    The harness chat has room here, so this row pins the timing; the
    refusal itself is pinned by ``test_e_a_chat_short_of_the_reserve...``."""
    _t2, t3, _l2, l3, _live = _two_queued(replay, vloop)
    assert l3 is not None and l3 - t3 <= 5.0, (t3, l3)


def _short_at_due(r, vloop):
    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.8)
        # Other traffic just before the line is due (two answers, as the
        # first queued message's line and card were in the live test):
        # the chat is left under the skip reserve at the 10 s mark.
        for n in range(2):
            await r.bot._app.bot.send_message(chat_id=CHAT, text=f"answer {n}")
        await _until(vloop, t0 + 30.0)
        w.cancel()
        return t0, _line_t(r, 2)
    return _run(vloop, scenario())


def test_e_a_chat_short_of_the_reserve_when_due_still_gets_the_line(
        replay, vloop, pty):
    """The line waits for its token instead of being refused and never
    retried (the second queued message of the live test). At this ceiling
    a waiting ornament needs the chat's whole burst while the card's own
    skip-kind edits and the typing bubble take a token at two, so it can
    land well after it was due; it lands while the message still waits."""
    t0, t = _short_at_due(replay, vloop)
    assert t is not None, "the line was refused and never retried"
    assert t - t0 >= 10.0


# ── (f) muted / minimal: no line, early path included ────────────────────

def test_f_muted_chat_gets_no_early_line(replay, vloop, pty):
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


def test_f_minimal_mode_gets_no_early_line(replay, vloop, vlimiter, pty):
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await r.tool_start("long step")
        await asyncio.sleep(7.0)
        _stand_minimal(vlimiter, vloop)
        minimal = vlimiter.minimal_mode(CHAT)
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 20.0)
        w.cancel()
        return minimal

    assert _run(vloop, scenario()) is True
    assert r.chat.lines == {}


# ── (g) a due line not sent says why, at INFO ────────────────────────────

def _reasons(caplog) -> list[str]:
    return [rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.INFO
            and "queued line for 2 not sent" in rec.getMessage()]


def test_g_no_evidence_is_logged_at_info(replay, vloop, pty, caplog):
    r = replay
    caplog.set_level(logging.INFO, logger="aipager.bot.send_now")

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two", evidence=False)
        # Due at 10 s; it gives up only after the evidence re-checks.
        await _until(vloop, t0 + 12.0
                     + sum(send_now._QUEUED_LINE_EVIDENCE_PAUSES))
        w.cancel()

    _run(vloop, scenario())
    assert any("no queue evidence" in m for m in _reasons(caplog))


def test_g_suppressed_is_logged_at_info(replay, vloop, pty, caplog):
    r = replay
    caplog.set_level(logging.INFO, logger="aipager.bot.send_now")

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        MUTE.mute(CHAT, 600)
        await _until(vloop, t0 + 12.0)
        w.cancel()

    _run(vloop, scenario())
    assert any("suppressed" in m for m in _reasons(caplog))


def test_g_muted_during_the_lines_wait_is_logged_at_info(
        replay, vloop, pty, caplog):
    """The chat is muted while the line waits for its token behind two
    answers: no line, and the reason is logged at INFO."""
    r = replay
    caplog.set_level(logging.INFO, logger="aipager.bot.send_now")

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        t0 = await r.queue(2, "queued two")
        await _until(vloop, t0 + 9.8)
        _drain_before_due(r, t0)
        await _until(vloop, t0 + 10.3)
        waiting = (_line_t(r, 2) is None and 2 in r.sess.queued_line_timers)
        MUTE.mute(CHAT, 600)
        await _until(vloop, t0 + 25.0)
        w.cancel()
        return waiting

    assert _run(vloop, scenario()) is True, "precondition: the line waited"
    assert r.chat.lines == {}
    assert any("chat muted" in m for m in _reasons(caplog))


# ── follow-ups: answer priority, and a step that can no longer be running ──

def test_e_a_line_due_while_the_chat_is_short_lands_within_seconds(
        replay, vloop, pty):
    """At answer priority the line only waits for the chat's next token
    (about 2 s at this chat's 0.5/s), instead of losing every token to the
    busy card's own edits and landing many seconds after it was due."""
    t0, t = _short_at_due(replay, vloop)
    assert t is not None
    assert t - t0 <= 10.0 + 4.0, (t0, t)


def test_a_message_after_a_safety_halt_gets_no_early_line(replay, vloop, pty):
    """A PreToolUse the safety policy denied never runs and gets no
    PostToolUse: a message sent after it is not treated as queued behind a
    step. (The first block of a turn halts the session back to idle, so
    the next message starts a turn of its own and its prompt clears the
    step; this row pins that end result.)"""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        await r.tool_start("blocked step")
        await asyncio.sleep(0.5)
        await r.hook(type="safety_blocked", tool="Bash", reason="denied")
        await asyncio.sleep(4.0)
        t0 = await r.queue(3, "queued three")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 3)
        w.cancel()
        return at_9

    assert _run(vloop, scenario()) is None


def test_a_turn_end_clears_the_step_before_a_popped_turn(replay, vloop, pty):
    """Turn 1 ends (Stop) with its step never closed and pops message 3 as
    turn 2, which fires no prompt hook. A message queued in turn 2 with no
    step running is not behind turn 1's old step: no early line."""
    r = replay

    async def scenario():
        w = r.worker()
        await r.turn(1, "first")
        await asyncio.sleep(SETTLE)
        await r.tool_start("step with no end")
        await r.queue(3, "queued three")
        await r.stop("answer one")
        await asyncio.sleep(3.0)
        t0 = await r.queue(5, "queued five")
        await _until(vloop, t0 + 9.5)
        at_9 = _line_t(r, 5)
        w.cancel()
        return at_9

    assert _run(vloop, scenario()) is None


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


def test_a_burst_of_queued_messages_does_not_hold_back_an_answer(
        replay, vloop, pty):
    """Six messages queued at once behind an old step: only the first line
    goes at answer priority (one tap sends everything), so an answer sent
    right after is not held behind six lines (measured before: +10 s)."""
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
        await asyncio.sleep(30.0)
        w.cancel()
        return waited

    waited = _run(vloop, scenario())
    assert waited <= 4.0, waited
    assert r.chat.line_for(10) is not None  # the first line still went out
