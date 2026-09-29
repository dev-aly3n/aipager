"""The INSTANT flood class (flood_budget.PRIORITY_INSTANT): the Send now line
and its deletes (operator, 2026-09-29: "I dont want the flood manager block
the sending of these kind of messages").

It never waits for a chat token and is not refused by minimal mode; it takes
a token when one is free, counts in the chat's hour, and is refused only by
the mute. A 429 on it is not retried (no wait), and a ban-sized one arms the
mute like any other call. No real sleep: the limiter runs on a fake clock.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_INSTANT,
    BudgetRateLimiter,
    rate_limit_args,
)

CHAT = 424242
BAN = 19289  # a ban-sized retry_after (the 2026-09-11 incident's)


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
    """A fresh loop per row, closed afterwards (limiter waiters left behind
    must not leak into later tests)."""
    loops = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run
    for loop in loops:
        loop.close()


@pytest.fixture(autouse=True)
def _clear_mute():
    MUTE.clear()
    yield
    MUTE.clear()


def _limiter(clock) -> BudgetRateLimiter:
    return BudgetRateLimiter(start_rate=config.TELEGRAM_PRIVATE_MAX_RATE,
                             clock=clock, sleep=clock.sleep)


async def _call(limiter, callback, *, priority=None):
    return await limiter.process_request(
        callback=callback, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": CHAT},
        rate_limit_args=(rate_limit_args(priority=priority)
                         if priority else None),
    )


def _tokens(limiter) -> float:
    return limiter._budget_for(CHAT).chat.tokens()


def test_instant_never_waits_with_the_chat_out_of_tokens(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return clock.now

    async def scenario():
        budget = limiter._budget_for(CHAT)
        while budget.chat.take(1.0):   # empty the chat's bucket
            pass
        before = clock.now
        at = await _call(limiter, ok, priority=PRIORITY_INSTANT)
        return before, at

    before, at = run_async(scenario())
    assert at == before


def test_control_an_answer_waits_with_the_chat_out_of_tokens(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return clock.now

    async def scenario():
        budget = limiter._budget_for(CHAT)
        while budget.chat.take(1.0):
            pass
        before = clock.now
        at = await _call(limiter, ok)
        return before, at

    before, at = run_async(scenario())
    assert at > before


def test_instant_takes_a_token_when_one_is_free(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return True

    async def scenario():
        before = _tokens(limiter)
        await _call(limiter, ok, priority=PRIORITY_INSTANT)
        return before, _tokens(limiter)

    before, after = run_async(scenario())
    assert before >= 1.0 and after == pytest.approx(before - 1.0)


def test_instant_counts_in_the_chats_hour(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return True

    async def scenario():
        await _call(limiter, ok, priority=PRIORITY_INSTANT)
        return limiter.hourly_usage(CHAT)

    usage = run_async(scenario())
    assert (usage["used"], usage["essential_used"]) == (1, 1)


def test_instant_is_refused_while_the_chat_is_muted(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)
    called = []

    async def ok():
        called.append(True)

    MUTE.mute(CHAT, 600)
    with pytest.raises(FloodMuted):
        run_async(_call(limiter, ok, priority=PRIORITY_INSTANT))
    assert called == []


def test_instant_goes_in_minimal_mode(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)
    wall = limiter._wall_now()   # the durable state is in wall time
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [wall - 3 * 86400.0],
                      "hourly": [[wall - 30.0, 0, 540]],
                      "hourly_minimal": True}])

    async def ok():
        return "sent"

    assert limiter.minimal_mode(CHAT) is True
    assert run_async(_call(limiter, ok, priority=PRIORITY_INSTANT)) == "sent"


def test_a_small_429_on_instant_is_not_retried(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)
    attempts = []

    async def once_429():
        attempts.append(clock.now)
        raise RetryAfter(5)

    with pytest.raises(RetryAfter):
        run_async(_call(limiter, once_429, priority=PRIORITY_INSTANT))
    assert len(attempts) == 1


def test_a_ban_sized_429_on_instant_arms_the_mute(run_async):
    clock = FakeClock()
    limiter = _limiter(clock)

    async def banned():
        raise RetryAfter(BAN)

    with pytest.raises(RetryAfter):
        run_async(_call(limiter, banned, priority=PRIORITY_INSTANT))
    assert MUTE.is_muted(CHAT)


def test_instant_waits_out_a_deferral_telegram_set(run_async):
    """Telegram answered a 429 with retry_after: a call inside that window
    is a certain 429 again, so INSTANT waits it out (and only that)."""
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return clock.now

    async def scenario():
        budget = limiter._budget_for(CHAT)
        budget.retry_until = clock.now + 4.0
        start = clock.now
        at = await _call(limiter, ok, priority=PRIORITY_INSTANT)
        return start, at

    start, at = run_async(scenario())
    assert at == pytest.approx(start + 4.0)


def test_instant_is_stamped_into_a_full_window(run_async):
    """The rolling window is full: the INSTANT call still goes, and is
    recorded there, so the paced traffic behind it yields."""
    clock = FakeClock()
    limiter = _limiter(clock)

    async def ok():
        return True

    async def scenario():
        budget = limiter._budget_for(CHAT)
        while budget.window.take(1.0):
            pass
        full = budget.window.used()
        await _call(limiter, ok, priority=PRIORITY_INSTANT)
        return full, budget.window.used()

    full, after = run_async(scenario())
    assert after == full + 1


def test_a_mute_armed_during_the_deferral_refuses_instant(run_async):
    """Telegram set a deferral, and a ban lands on the chat while INSTANT
    waits it out: the call is refused, never sent into the ban (`_run`
    does not ask the mute itself; review rev-iter2-001)."""
    clock = FakeClock()
    limiter = _limiter(clock)
    called = []

    async def sleep_into_a_ban(seconds):
        MUTE.mute(CHAT, 600)
        await clock.sleep(seconds)

    limiter._sleep = sleep_into_a_ban

    async def ok():
        called.append(True)

    async def scenario():
        limiter._budget_for(CHAT).retry_until = clock.now + 4.0
        try:
            await _call(limiter, ok, priority=PRIORITY_INSTANT)
        except FloodMuted:
            return "refused"
        return "sent"

    assert (run_async(scenario()), called) == ("refused", [])


def test_instant_waits_out_a_deferral_moved_on_during_the_wait(run_async):
    """Another 429 during the wait moves the deferral on: INSTANT waits
    until the new end, not the one it first read."""
    clock = FakeClock()
    limiter = _limiter(clock)
    budget = limiter._budget_for(CHAT)
    moved = []

    async def sleep_and_move(seconds):
        await clock.sleep(seconds)
        if not moved:
            moved.append(True)
            budget.retry_until = clock.now + 2.0

    limiter._sleep = sleep_and_move

    async def ok():
        return clock.now

    async def scenario():
        start = clock.now
        budget.retry_until = start + 4.0
        return start, await _call(limiter, ok, priority=PRIORITY_INSTANT)

    start, at = run_async(scenario())
    assert at == pytest.approx(start + 6.0)
