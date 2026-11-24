"""A card withdrawn while it waits for the chat's pace (the late busy card).

A busy card queued behind a busy chat's pace used to go out 17 to 38 s
after its session had ended (fake-Telegram e2e logs, 2026-11-23): the
session's end then deleted it, and its tracking made the ended session
the chat's target, so the next plain message got "not found". The
limiter now asks the caller's ``wanted`` predicate before the call and
while it waits, and the busy card's predicate says no once its session
has ended. A message of an ended session that lands anyway is still a
reply target but no longer moves the chat's target.

No test here sleeps for real for more than a few poll slices (the poll is
shortened through the module constant, read at call time), and none
patches ``asyncio.sleep`` through a module path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import flood_budget
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    BudgetRateLimiter,
    FloodSkipped,
    SendNotWanted,
    rate_limit_args,
)
from aipager.state import SessionRegistry, Status, TrackedSession


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


@pytest.fixture
def run_async():
    def _run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
    return _run


def _call(limiter, *, chat_id=5, wanted=None, kind="blocking",
          priority=PRIORITY_ORNAMENT, calls=None):
    async def _cb():
        if calls is not None:
            calls.append(chat_id)
        return {"ok": True}
    return limiter.process_request(
        callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": chat_id},
        rate_limit_args=rate_limit_args(kind=kind, priority=priority, wanted=wanted))


def _tokens(limiter, chat_id=5) -> float:
    budget = limiter._budget_for(limiter._key(chat_id))
    limiter._decay(budget, limiter._clock())
    return budget.chat.tokens


# ── the limiter ─────────────────────────────────────────────────────────────────

def test_a_card_withdrawn_at_its_head_wait_is_dropped_within_a_poll(run_async):
    """The chat is deferred 30 s (a 429); the card's session ends 5 s in.
    It is dropped unsent within one poll of that, not after the 30 s, and
    spends no token. Mutations: the check at the top of the head loop
    removed (the card is sent at 30 s), or the head sleep not sliced (the
    drop comes only at 30 s)."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    calls: list = []
    start = clock.now
    limiter.note_retry_after(5, 30)
    before = _tokens(limiter)

    async def go():
        with pytest.raises(SendNotWanted):
            await _call(limiter, wanted=lambda: clock.now < start + 5, calls=calls)

    run_async(go())
    assert calls == []
    assert clock.now - start <= 5 + flood_budget.WANTED_POLL_SECONDS + 1e-6
    assert _tokens(limiter) == pytest.approx(before)
    assert limiter._budget_for(limiter._key(5)).calls == 0


def test_a_card_still_wanted_is_sent_after_its_wait(run_async):
    """Control: the same wait with the predicate always True sends it."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    calls: list = []
    start = clock.now
    limiter.note_retry_after(5, 30)
    run_async(_call(limiter, wanted=lambda: True, calls=calls))
    assert calls == [5]
    assert clock.now - start >= 30 - 1e-6


def test_a_withdrawn_skip_call_is_never_made(run_async):
    """A skip-kind call takes no wait, so only the check before any path
    can withdraw it. Mutation: that check removed (the call is made)."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    calls: list = []

    async def go():
        with pytest.raises(SendNotWanted):
            await _call(limiter, kind="skip", wanted=lambda: False, calls=calls)

    run_async(go())
    assert calls == []


def test_send_not_wanted_is_a_flood_skipped():
    """Every caller that reads FloodSkipped as "nothing sent" reads this so."""
    assert issubclass(SendNotWanted, FloodSkipped)


def test_a_predicate_that_raises_counts_as_wanted(run_async):
    """Fail-open: a broken predicate never drops a message. Mutation:
    ``_still_wanted`` returning False on an exception."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    calls: list = []

    def _broken():
        raise RuntimeError("boom")

    run_async(_call(limiter, wanted=_broken, calls=calls))
    assert calls == [5]


def test_a_card_withdrawn_while_queued_behind_another_leaves_the_queue(
        run_async, monkeypatch):
    """A card queued behind another call (not at the head) is withdrawn
    within a poll while the head still waits, and the queue goes on to the
    next caller. Mutation: the non-head poll removed (the card waits on its
    ticket until the head is done and only then drops)."""
    monkeypatch.setattr(flood_budget, "WANTED_POLL_SECONDS", 0.02)
    release = None
    clock = FakeClock()

    async def _held_sleep(seconds):
        await release.wait()
        clock.now += max(seconds, 0.0)

    limiter = BudgetRateLimiter(clock=clock, sleep=_held_sleep)
    calls: list = []
    limiter.note_retry_after(5, 30)
    wanted = {"card": True}
    seen = {}

    async def go():
        nonlocal release
        release = asyncio.Event()
        head = asyncio.ensure_future(_call(limiter, priority="essential", calls=calls))
        await asyncio.sleep(0)
        card = asyncio.ensure_future(_call(limiter, wanted=lambda: wanted["card"],
                                           calls=calls))
        await asyncio.sleep(0.05)
        budget = limiter._budget_for(limiter._key(5))
        seen["queued"] = len(budget.waiters)
        wanted["card"] = False
        await asyncio.sleep(0.2)
        seen["card_done_before_head"] = card.done() and not head.done()
        seen["card_exc"] = type(card.exception()) if card.done() else None
        seen["left"] = len(budget.waiters)
        release.set()
        await head

    run_async(go())
    assert seen["queued"] == 2
    assert seen["card_done_before_head"] is True
    assert seen["card_exc"] is SendNotWanted
    assert seen["left"] == 1
    assert calls == [5]


# ── the busy card's predicate ───────────────────────────────────────────────────

def _bot_with_session(mk_bot, status=Status.BUSY):
    bot = mk_bot()
    sess = TrackedSession(name="claude-x1", label="x1", status=status)
    sess.trigger_msg_id = 40
    bot.registry._sessions[sess.name] = sess
    bot._start_animation = MagicMock()
    return bot, sess


def test_the_busy_cards_predicate_is_false_once_its_session_ended(
        mk_bot, run_async):
    """``_open_busy_card`` hands the send a predicate: True while the
    session lives, False once it is GONE or no longer the registry's entry.
    Mutations: the GONE clause or the entry clause removed."""
    bot, sess = _bot_with_session(mk_bot)
    seen = {}

    async def _send(*a, **kw):
        wanted = kw["rate_limit_args"]["wanted"]
        seen["live"] = wanted()
        sess.status = Status.GONE
        seen["gone"] = wanted()
        sess.status = Status.BUSY
        bot.registry._sessions[sess.name] = TrackedSession(name=sess.name, label="x1")
        seen["replaced"] = wanted()
        bot.registry._sessions[sess.name] = sess
        return MagicMock(message_id=77)

    bot._app.bot.send_message = AsyncMock(side_effect=_send)
    sess.busy_msg_id = -1
    run_async(bot._open_busy_card(sess))
    assert seen == {"live": True, "gone": False, "replaced": False}


def test_a_withdrawn_busy_card_is_not_tracked_owed_or_kept(mk_bot, run_async, caplog):
    """The send says "not wanted": no card, no tracked message (the chat's
    target stays where it was), no animation and nothing owed, and the log
    says why (not the minimal-mode line)."""
    caplog.set_level("INFO", logger="aipager.bot.animation")
    bot, sess = _bot_with_session(mk_bot)
    bot.registry.last_active_session = ""
    bot._app.bot.send_message = AsyncMock(side_effect=SendNotWanted(5, "sendMessage"))
    sess.busy_msg_id = -1
    assert run_async(bot._open_busy_card(sess)) is None
    assert sess.busy_msg_id is None
    assert sess.busy_card_owed is False
    assert bot.registry.last_active_session == ""
    bot._start_animation.assert_not_called()
    assert "busy card dropped unsent" in caplog.text


# ── the chat's target ───────────────────────────────────────────────────────────

def test_a_late_message_of_an_ended_session_keeps_the_chats_target():
    """A message tracked for a GONE session is a reply target but does not
    make that session the chat's target. Mutation: the GONE check removed
    from ``track_message`` (the ended session becomes the target and the
    next plain message gets "not found")."""
    reg = SessionRegistry()
    live = reg.get_or_create("claude-live")
    live.label = "live"
    ended = reg.get_or_create("claude-ended")
    ended.label = "ended"
    reg.track_message(10, "claude-live", 0)
    reg.transition("claude-ended", Status.GONE)
    reg.track_message(11, "claude-ended", 0)
    assert reg.last_active_session == "claude-live"
    assert reg.target_for(0).name == "claude-live"
    assert reg.get_session_by_msg(11, 0) is ended


def test_a_message_of_a_live_session_still_moves_the_target():
    """Control: output of a live session moves the target, as before."""
    reg = SessionRegistry()
    reg.get_or_create("claude-a")
    reg.get_or_create("claude-b")
    reg.track_message(10, "claude-a", 0)
    reg.track_message(11, "claude-b", 0)
    assert reg.last_active_session == "claude-b"


# ── the queue's edges ───────────────────────────────────────────────────────────

def _held_limiter():
    """A limiter whose sleeps wait until released (real waiting), so a
    second call stays queued behind the head for as long as the test
    wants."""
    clock = FakeClock()
    state = {"release": None}

    async def _held_sleep(seconds):
        await state["release"].wait()
        clock.now += max(seconds, 0.0)

    limiter = BudgetRateLimiter(clock=clock, sleep=_held_sleep)
    limiter.note_retry_after(5, 30)
    return limiter, state


def test_cancelling_a_queued_card_leaves_the_queue_clean(run_async, monkeypatch):
    """A card task cancelled while queued (a session torn down) leaves no
    ticket behind, and the head still runs."""
    monkeypatch.setattr(flood_budget, "WANTED_POLL_SECONDS", 0.02)
    limiter, state = _held_limiter()
    calls: list = []
    seen = {}

    async def go():
        state["release"] = asyncio.Event()
        head = asyncio.ensure_future(_call(limiter, priority="essential", calls=calls))
        await asyncio.sleep(0)
        card = asyncio.ensure_future(_call(limiter, wanted=lambda: True, calls=calls))
        await asyncio.sleep(0.05)
        card.cancel()
        await asyncio.sleep(0.05)
        seen["left"] = len(limiter._budget_for(limiter._key(5)).waiters)
        state["release"].set()
        await head

    run_async(go())
    assert seen["left"] == 1
    assert calls == [5]


def test_a_card_withdrawn_as_it_reaches_the_head_hands_the_queue_on(
        run_async, monkeypatch):
    """The head goes out and the card's session ends in the same instant:
    the card, now at the head, is dropped there and hands the queue on to
    the call behind it, which is sent."""
    monkeypatch.setattr(flood_budget, "WANTED_POLL_SECONDS", 0.02)
    limiter, state = _held_limiter()
    calls: list = []
    wanted = {"card": True}

    async def go():
        state["release"] = asyncio.Event()
        head = asyncio.ensure_future(_call(limiter, priority="essential", calls=calls))
        await asyncio.sleep(0)
        card = asyncio.ensure_future(_call(limiter, chat_id=5, wanted=lambda: wanted["card"],
                                           calls=calls))
        await asyncio.sleep(0)
        last = asyncio.ensure_future(_call(limiter, calls=calls))
        await asyncio.sleep(0.05)
        wanted["card"] = False
        state["release"].set()   # the head leaves in the same step
        await head
        with pytest.raises(SendNotWanted):
            await card
        await asyncio.wait_for(last, 5)

    run_async(go())
    assert calls == [5, 5]


def test_a_predicate_that_raises_while_waiting_still_sends(run_async):
    """Fail-open at every check, not only the first: the predicate works
    at first and raises once the card waits."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    calls: list = []
    asked = {"n": 0}
    limiter.note_retry_after(5, 10)

    def _flaky():
        asked["n"] += 1
        if asked["n"] > 1:
            raise RuntimeError("boom")
        return True

    run_async(_call(limiter, wanted=_flaky, calls=calls))
    assert calls == [5]
    assert asked["n"] > 2


def test_a_card_withdrawn_during_its_429_retry_wait_is_not_sent_again(run_async):
    """A small 429 on the card's send defers the chat; its one retry waits
    and asks again, so a session that ended meanwhile gets no second send.
    Mutation: the retry's acquire not given the predicate."""
    from telegram.error import RetryAfter

    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    sent = {"n": 0}
    start = clock.now

    async def _cb():
        sent["n"] += 1
        if sent["n"] == 1:
            raise RetryAfter(10)
        return {"ok": True}

    async def go():
        with pytest.raises(SendNotWanted):
            await limiter.process_request(
                callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": 5},
                rate_limit_args=rate_limit_args(
                    priority=PRIORITY_ORNAMENT,
                    wanted=lambda: clock.now < start + 3))

    run_async(go())
    assert sent["n"] == 1


def test_a_dropped_card_of_an_ended_session_is_not_owed(mk_bot, run_async):
    """Even in a chat whose ornaments are suspended, a card dropped because
    its session ended is not owed (it would only be forgotten later, after
    a misleading "held back" line). Mutation: the GONE check removed."""
    bot, sess = _bot_with_session(mk_bot)
    bot._card_refused_now = lambda _s: True

    async def _gone_send(*a, **kw):
        sess.status = Status.GONE
        raise SendNotWanted(5, "sendMessage")

    bot._app.bot.send_message = AsyncMock(side_effect=_gone_send)
    sess.busy_msg_id = -1
    run_async(bot._open_busy_card(sess))
    assert sess.busy_card_owed is False
