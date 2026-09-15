"""The per-chat budget itself (roadmap 8.21): buckets, FIFO, skip, backoff.

Rows C1, E3, E4, G, H, J, N and P of the design's case table. Everything
here drives ``BudgetRateLimiter`` directly, on an injected clock whose
``sleep`` advances that clock and yields once — no test in this file
sleeps for real, and none patches ``asyncio.sleep`` through a module path
(``aipager.bot.notify.asyncio`` IS the global module; CLAUDE.md).

Every test's docstring names the mutation that makes it fail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time
from pathlib import Path

import pytest
from telegram.error import RetryAfter

from aipager import config, status
from aipager.bot.flood_budget import (
    BudgetRateLimiter,
    FloodSkipped,
    SlidingWindow,
    TokenBucket,
    card_interval,
    clear_backoff_signal,
    is_group_chat,
)


# ── harness ──────────────────────────────────────────────────────────────────

class FakeClock:
    """The one time seam. ``sleep`` advances the clock and yields once."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


@pytest.fixture
def run_async():
    """Override the shared ``run_async`` for this file, to CLOSE the loops.

    The shared fixture abandons each event loop it creates. Tests here
    leave pending FIFO waiter futures and spawned acquire tasks behind,
    and an abandoned loop keeps them (and any thread it started) alive
    until the cyclic GC happens to collect it. The suite runs under a
    hard 1 GiB ``RLIMIT_AS`` — the first test file to run calls
    ``notify_hook.main()``, which clamps it on the pytest process itself
    and can never raise it back — so a leak here would not fail THIS
    file, it would fail an unrelated LATER test with "can't start new
    thread". That is roadmap 8.19 and is not ours to fix.

    So: cancel pending tasks, shut the default executor down and close
    each loop when the test ends.
        Closed EAGERLY, as each coroutine finishes, rather than collected and
    closed at teardown: the suite runs with VmSize within a few tens of
    kilobytes of its ``RLIMIT_AS`` on this machine already, so holding
    even two loops at once is worth avoiding.
    """
    def _run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True),
                    )
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    return _run


def _limiter(clock: FakeClock, **kw) -> BudgetRateLimiter:
    return BudgetRateLimiter(clock=clock, sleep=clock.sleep, **kw)


def _recorder(clock: FakeClock):
    """A callback that records ``(endpoint, chat_id, now)`` and returns ok."""
    stamps: list[tuple[str, object, float]] = []

    async def _call(endpoint="?", chat_id=None):
        stamps.append((endpoint, chat_id, clock.now))
        return {"ok": True}

    _call.stamps = stamps  # type: ignore[attr-defined]
    return _call


async def _acquire(limiter, callback, *, endpoint="sendMessage", chat_id=1,
                   kind=None):
    data = {} if chat_id is None else {"chat_id": chat_id}
    return await limiter.process_request(
        callback=callback, args=(), kwargs={"endpoint": endpoint,
                                            "chat_id": chat_id},
        endpoint=endpoint, data=data,
        rate_limit_args=({"kind": kind} if kind else None),
    )


def _chat(snapshot: dict, chat_id) -> dict:
    for entry in snapshot["chats"]:
        if entry["chat_id"] == chat_id:
            return entry
    raise AssertionError(f"chat {chat_id} not in {snapshot}")


def _ns(**kw) -> argparse.Namespace:
    """The argparse shape ``cmd_status`` reads, as ``tests/test_flood_mute.py``
    builds it."""
    return argparse.Namespace(**{"as_json": False, **kw})


def _max_in_window(stamps: list[float], window: float) -> int:
    """The most stamps found in any *window*-long span."""
    return max(
        (sum(1 for t in stamps if start <= t < start + window) for start in stamps),
        default=0,
    )


# ── TokenBucket ──────────────────────────────────────────────────────────────

def test_a_bucket_starts_full_and_refills_at_its_rate():
    """Mutation: start the bucket empty, or drop the refill in ``_refill``."""
    clock = FakeClock()
    bucket = TokenBucket(1.0, 3.0, clock=clock)
    assert bucket.tokens() == 3.0
    assert bucket.take(3.0) is True
    assert bucket.tokens() == 0.0
    clock.now += 2.0
    assert bucket.tokens() == 2.0


def test_a_bucket_never_refills_past_its_burst():
    """Mutation: drop the ``min(self.capacity, ...)`` cap — an idle chat
    would bank an unbounded burst and put it all into one second."""
    clock = FakeClock()
    bucket = TokenBucket(1.0, 3.0, clock=clock)
    bucket.take(3.0)
    clock.now += 3600.0
    assert bucket.tokens() == 3.0


def test_time_until_is_zero_when_the_tokens_are_already_there():
    """Mutation: always return a positive wait — every caller would sleep
    a tick even on an idle chat."""
    clock = FakeClock()
    bucket = TokenBucket(1.0, 3.0, clock=clock)
    assert bucket.time_until(1.0) == 0.0
    bucket.take(3.0)
    assert bucket.time_until(1.0) == pytest.approx(1.0)
    assert bucket.time_until(2.0) == pytest.approx(2.0)


def test_take_refuses_and_consumes_nothing_when_the_bucket_is_short():
    """Mutation: let ``take`` go negative — the burst cap stops meaning
    anything and a chat can overdraw its next second."""
    clock = FakeClock()
    bucket = TokenBucket(1.0, 3.0, clock=clock)
    assert bucket.take(5.0) is False
    assert bucket.tokens() == 3.0


# ── the chat bucket applies to private chats too (the 8.21 bug) ──────────────

def test_a_private_chat_is_paced_like_a_group(run_async):
    """A POSITIVE (private) chat id gets a real per-chat bucket.

    Mutation: make ``_budget_for`` return ``None`` for positive ids —
    exactly what python-telegram-bot's AIORateLimiter does — and the
    stamps collapse to one instant, which is the 2026-09-11 incident.

    ``start_rate`` is pinned to the ceiling because this row is about the
    BUCKET existing at all, not about the 8.27 earned rate: left to the
    default 0.5 it would measure the starting allowance instead, and the
    "collapse to one instant" mutation would still be caught, just at
    twice the spacing. The earned rate has rows of its own in
    ``tests/integration/outbound-gate-and-earned-rate/test_earned_rate.py``.
    """
    clock = FakeClock()
    limiter = _limiter(clock, start_rate=config.TELEGRAM_PRIVATE_MAX_RATE)
    call = _recorder(clock)

    async def _drive():
        for _ in range(6):
            await _acquire(limiter, call, chat_id=256113222)

    run_async(_drive())
    stamps = [t for _e, _c, t in call.stamps]
    assert _max_in_window(stamps, 1.0) <= 3, stamps
    assert stamps[-1] - stamps[0] == pytest.approx(3.0)


def test_a_burst_is_capped_at_three_calls(run_async):
    """Mutation: raise ``TELEGRAM_CHAT_BURST`` to 30 and six calls all land
    in the same instant."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(6):
            await _acquire(limiter, call, chat_id=42)

    run_async(_drive())
    stamps = [t for _e, _c, t in call.stamps]
    assert stamps[:3] == [stamps[0]] * 3
    assert stamps[3] > stamps[0]


def test_two_chats_do_not_share_a_per_chat_bucket(run_async):
    """Mutation: key every budget on a constant — one busy chat would
    throttle every other chat the daemon talks to."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for chat in (111, 222):
            for _ in range(3):
                await _acquire(limiter, call, chat_id=chat)

    run_async(_drive())
    assert [t for _e, _c, t in call.stamps] == [clock.now] * 6


# ── C1: the skip reserve ─────────────────────────────────────────────────────

def test_a_skip_leaves_the_last_token_for_a_blocking_caller(run_async):
    """A skip acquire with one token left is refused and the token stays.

    Mutation: change the reserve from ``< 2.0`` to ``< 1.0`` and the card
    spends the token an answer was waiting for — the blocking acquire
    below then has to wait a second.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(2):  # 3 -> 1 token left
            await _acquire(limiter, call, chat_id=7)
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, chat_id=7, endpoint="editMessageText",
                           kind="skip")
        assert _chat(limiter.snapshot(), 7)["tokens"] == pytest.approx(1.0)
        await _acquire(limiter, call, chat_id=7)  # the answer: runs NOW

    run_async(_drive())
    assert [t for _e, _c, t in call.stamps] == [clock.now] * 3
    snap = _chat(limiter.snapshot(), 7)
    assert snap["skipped"] == 1
    assert snap["calls"] == 3


def test_a_refused_skip_returns_without_sleeping(run_async):
    """Mutation: make the skip path ``await`` anything — a refused card
    edit would cost the animation loop a real wait every tick."""
    clock = FakeClock()
    slept: list[float] = []

    async def _sleep(seconds):
        slept.append(seconds)
        await clock.sleep(seconds)

    limiter = BudgetRateLimiter(clock=clock, sleep=_sleep)
    call = _recorder(clock)

    async def _drive():
        for _ in range(3):
            await _acquire(limiter, call, chat_id=7)
        started = clock.now
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, chat_id=7, kind="skip")
        assert clock.now == started

    run_async(_drive())
    assert slept == []


def test_a_group_card_yields_the_last_calls_of_the_minute_to_real_content(
    run_async,
):
    """Row C1 in a GROUP: the skip reserve is counted in SLOTS of the
    rolling window as well as in tokens, so the last two calls of the
    minute belong to an answer or a reply — never to a card refresh.

    Mutation: check only the chat bucket in ``_acquire_skip`` (drop the
    ``budget.group.free() < _SKIP_RESERVE`` clause) and a card spends the
    group's last call of the minute, leaving a real reply to wait up to a
    minute behind it.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(19):                 # 19 of the group's 20 spent
            await _acquire(limiter, call, chat_id=-1005)
        clock.now += 10.0                   # the chat bucket is full again
        with pytest.raises(FloodSkipped):   # ... but the minute is not
            await _acquire(limiter, call, chat_id=-1005, kind="skip")
        started = clock.now
        await _acquire(limiter, call, chat_id=-1005)   # blocking still gets it
        assert clock.now == started

    run_async(_drive())
    assert len(call.stamps) == 20
    assert _chat(limiter.snapshot(), -1005)["skipped"] == 1


def test_a_skip_is_refused_while_the_chat_is_deferred_by_a_429(run_async):
    """Mutation: drop the ``retry_until`` term from the skip refusal and a
    card keeps editing straight through Telegram's own window."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    limiter.note_retry_after(7, 5)

    async def _drive():
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, chat_id=7, kind="skip")

    run_async(_drive())
    assert call.stamps == []


def test_a_skip_with_no_resolvable_chat_is_never_refused(run_async):
    """R7 fail-open. Mutation: raise ``FloodSkipped`` when the chat can't
    be resolved and ``answerCallbackQuery`` starts vanishing."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(5):
            await _acquire(limiter, call, endpoint="answerCallbackQuery",
                           chat_id=None, kind="skip")

    run_async(_drive())
    assert len(call.stamps) == 5


# ── R2 / G: FIFO, never dropped ──────────────────────────────────────────────

def test_blocking_acquires_run_in_arrival_order(run_async):
    """Every blocking acquire runs, in the order it was requested.

    Mutation: ``waiters.pop()`` instead of ``waiters.popleft()`` — the
    queue is handed to the wrong ticket, and all but the first acquire
    are stranded (the ``order`` list stays short).
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    order: list[int] = []

    async def _drive():
        async def _one(i):
            async def _cb():
                order.append(i)
                return True
            await limiter.process_request(
                callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": 9}, rate_limit_args=None,
            )

        tasks = [asyncio.ensure_future(_one(i)) for i in range(8)]
        for _ in range(400):
            if all(t.done() for t in tasks):
                break
            await asyncio.sleep(0)
        for task in tasks:
            if not task.done():
                task.cancel()
        return [t.done() and t.exception() is None for t in tasks]

    done = run_async(_drive())
    assert all(done), done
    assert order == list(range(8))


def _sequential_stamps(run_async, chat_id, count):
    """``count`` blocking acquires into one chat; the clock stamp of each."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(count):
            await _acquire(limiter, call, chat_id=chat_id)

    run_async(_drive())
    return [t for _e, _c, t in call.stamps]


def test_a_group_is_additionally_capped_at_twenty_calls_in_any_minute(run_async):
    """Row G / R1's group clause, as a ROLLING WINDOW: at most 20 calls in
    ANY 60 s window, not 20 tokens that refill. Sixty back-to-back calls
    into a group, and no minute-long window anywhere in the result may
    hold more than twenty — while the same traffic into a private chat is
    held only by the 1/s bucket and runs three times faster.

    Mutation: model the group as ``TokenBucket(20/60, 20)`` again (its
    burst lets 25 calls into the first minute, which is exactly
    rev-iter1-002), or drop the group limit from ``ChatBudget``, and the
    window count goes over twenty.
    """
    group = _sequential_stamps(run_async, -1001, 60)
    private = _sequential_stamps(run_async, 256113222, 60)
    assert _max_in_window(group, 60.0) <= 20, group
    assert _max_in_window(private, 60.0) > 20
    # Still ordered, and still under the per-chat burst.
    assert group == sorted(group)
    assert _max_in_window(group, 1.0) <= 3, group


def test_twenty_five_group_calls_run_twenty_in_the_first_minute_then_five(
    run_async,
):
    """Case G's own arithmetic, literally: 25 queued blocking calls into a
    group leave 20 inside the first 60 s and the remaining 5 in the next
    window — none dropped, none reordered.

    Mutation: give the group a token bucket with a burst and all 25 land
    in the first 22 seconds.
    """
    stamps = _sequential_stamps(run_async, -1002, 25)
    start = stamps[0]
    assert len([t for t in stamps if t < start + 60.0]) == 20
    assert len([t for t in stamps if start + 60.0 <= t < start + 120.0]) == 5
    assert stamps == sorted(stamps)


def test_a_group_window_never_admits_a_twenty_first_call_early(run_async):
    """The window is a window, not a bucket: 20 calls, then a 21st that
    must wait for the OLDEST of them to age out — even though the window
    has been "refilling" for 59 seconds.

    Mutation: evict on ``now - period`` with the stamps in the wrong
    order, or refill slots continuously, and the 21st call goes early.

    ``start_rate`` pinned to the ceiling: this row is about the WINDOW,
    and the 8.27 earned rate would otherwise spread the first twenty
    calls over 38 s instead of 19 — changing the arithmetic without
    changing what is being tested.
    """
    clock = FakeClock()
    limiter = _limiter(clock, start_rate=config.TELEGRAM_PRIVATE_MAX_RATE)
    call = _recorder(clock)

    async def _drive():
        for _ in range(20):          # spread by the 1/s chat bucket
            await _acquire(limiter, call, chat_id=-1003)
        clock.now += 40.0            # 40 quiet seconds
        await _acquire(limiter, call, chat_id=-1003)

    run_async(_drive())
    stamps = [t for _e, _c, t in call.stamps]
    assert stamps[-1] - stamps[0] == pytest.approx(60.0)


def test_a_bucket_refilled_for_exactly_its_own_wait_can_spend_the_token():
    """The ``_TOKEN_EPS`` livelock guard, at the level where it bites.

    ``time_until`` promises "wait this long and you can take one". For a
    rate that is not a binary fraction that promise is false in IEEE754:
    ``(n - have) / rate * rate`` comes back a few ULPs SHORT, so the
    bucket refilled for exactly its own wait holds 0.9999999999999999
    tokens. The residual wait is then ~3e-16 s — which ``now + wait``
    cannot even represent at the millions of seconds a monotonic clock
    reports — so ``_acquire_blocking`` sleeps zero, wakes, recomputes the
    same wait and spins forever, wedging the event loop.

    Reproduced deterministically: 20/60 is not a binary fraction, 1e6 is
    where a monotonic clock lives, and half a second of partial refill is
    what any 429 deferral or overall-bucket wait leaves behind.

    Mutation: ``_TOKEN_EPS = 0.0``, or drop ``+ _TOKEN_EPS`` from
    ``TokenBucket.time_until`` or from ``TokenBucket.take`` — each one
    fails a line below. (This is what the iteration-1 test of this name
    did NOT do: it drove the loop from a clock value where the arithmetic
    happened to be exact, so it passed with the guard removed.)
    """
    clock = FakeClock()                              # 1e6
    bucket = TokenBucket(20 / 60, 20.0, clock=clock)  # not a binary fraction
    bucket.take(20.0)
    clock.now += 0.5                                 # a partial refill
    clock.now += bucket.time_until(1.0)              # sleep EXACTLY that long

    have = bucket._tokens + (clock.now - bucket._stamp) * bucket.rate
    assert have < 1.0, "the float shortfall this guard exists for is gone"
    assert clock.now + (1.0 - have) / bucket.rate == clock.now, \
        "the residual wait cannot advance the clock — that is the livelock"

    assert bucket.time_until(1.0) == 0.0
    assert bucket.take(1.0) is True


def test_an_acquire_on_a_fractional_rate_settles_instead_of_spinning(run_async):
    """The same guard through ``process_request``, bounded so a live
    wedge FAILS the run rather than hanging it: the acquire is pumped a
    fixed number of loop turns and then cancelled.

    Mutation: ``_TOKEN_EPS = 0.0`` and the 21st acquire never completes —
    the clock stops advancing while the loop keeps turning.
    """
    clock = FakeClock()
    limiter = _limiter(clock, chat_max_rate=20 / 60, chat_burst=20.0)
    call = _recorder(clock)

    async def _drive():
        for _ in range(20):                # drain the burst
            await _acquire(limiter, call, chat_id=44)
        clock.now += 0.5                   # ... leaving a fractional refill
        task = asyncio.ensure_future(_acquire(limiter, call, chat_id=44))
        for _ in range(5_000):
            if task.done():
                break
            await asyncio.sleep(0)
        if not task.done():
            task.cancel()
        return task.done() and task.exception() is None

    assert run_async(_drive()) is True, "the acquire loop never settled"
    assert len(call.stamps) == 21


def test_a_window_slot_freed_by_waiting_is_really_free():
    """The same livelock, one class over: the group's rolling window.

    At clock values where ``stamp + period`` ITSELF rounds down by an ULP
    — 1048525.6230842356 is one, and a monotonic clock reports exactly
    this kind of number — the naive wait lands where the oldest stamp is
    59.999999999883585 seconds old, so it has NOT left the window: the
    waiter asks for another 1.2e-10 s, cannot advance the clock by it and
    spins. ``time_until`` therefore rounds its wait up against the very
    expression ``_evict`` tests.

    Mutation: ``return wait`` straight out of ``SlidingWindow.time_until``
    without the rounding loop, and ``take`` silently records NOTHING (its
    return value is what the acquire path ignores) — so the window loses
    a stamp and the minute after admits more than its limit.
    """
    clock = FakeClock(1_048_525.6230842356)
    window = SlidingWindow(1, 60.0, clock=clock)
    assert window.take() is True
    stamp = clock.now
    naive = stamp + 60.0 - clock.now
    assert (clock.now + naive) - stamp < 60.0, \
        "the float shortfall this guard exists for is gone"

    clock.now += window.time_until(1.0)     # sleep exactly what it asked for
    assert window.free() == 1
    assert window.take() is True
    assert window.used() == 1


def test_a_group_still_admits_only_twenty_when_the_clocks_arithmetic_rounds(
    run_async,
):
    """The same rounding as the CONTRACT sees it: R1's twenty-a-minute has
    to hold at a clock value whose arithmetic rounds down, not only at the
    tidy 1e6 the rest of this file uses. Measured exactly the way a
    black-box test measures it — the largest number of stamps in any
    ``[t, t + 60)`` — which is what makes the one-ULP-early call visible.

    Mutation: ``return wait`` out of ``SlidingWindow.time_until`` without
    the rounding loop and the 21st call lands 1.2e-10 s inside the first
    minute, unrecorded, so the drift compounds into the next one.
    """
    clock = FakeClock(1_048_525.6230842356)
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(45):
            await _acquire(limiter, call, chat_id=-1004)

    run_async(_drive())
    stamps = [t for _e, _c, t in call.stamps]
    assert _max_in_window(stamps, 60.0) <= 20, stamps


def test_a_blocking_acquire_is_deferred_never_dropped(run_async):
    """Mutation: raise ``FloodSkipped`` for a blocking caller too and a
    real answer is silently dropped instead of arriving a second late."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(3):
            await _acquire(limiter, call, chat_id=5)
        started = clock.now
        await _acquire(limiter, call, chat_id=5)
        assert clock.now == pytest.approx(started + 1.0)

    run_async(_drive())
    assert len(call.stamps) == 4


# ── H: no chat id, no chat bucket ────────────────────────────────────────────

def test_answer_callback_query_is_never_delayed_by_a_chat(run_async):
    """Row H. Mutation: budget a call whose ``data`` has no ``chat_id``
    per chat — a toast would queue behind a jammed chat's cards."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    limiter.note_retry_after(3, 30)

    async def _drive():
        for _ in range(3):  # drain chat 3's bucket as well
            with pytest.raises(FloodSkipped):
                await _acquire(limiter, call, chat_id=3, kind="skip")
        started = clock.now
        for _ in range(5):
            await _acquire(limiter, call, endpoint="answerCallbackQuery",
                           chat_id=None)
        assert clock.now == started

    run_async(_drive())
    assert len(call.stamps) == 5
    assert _chat(limiter.snapshot(), 3)["calls"] == 0


def test_a_429_with_no_resolvable_chat_waits_before_its_one_retry(run_async):
    """Row H's other half (review rev-iter1-007). A 429 on a call with no
    chat id — ``answerCallbackQuery``, ``getMe`` — has no chat budget to
    defer, so without this the retry meets only the 30/s overall bucket
    and goes straight back into the window Telegram has just closed. That
    is the retry storm 8.21 exists to remove, in miniature.

    Mutation: retry immediately when ``budget is None`` (drop the
    ``self._sleep(seconds)``) and the second attempt lands in the same
    instant as the first.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    stamps: list[float] = []

    async def _call(**kw):
        stamps.append(clock.now)
        if len(stamps) == 1:
            raise RetryAfter(2)
        return {"ok": True}

    async def _drive():
        return await _acquire(limiter, _call, endpoint="answerCallbackQuery",
                              chat_id=None)

    assert run_async(_drive()) == {"ok": True}
    assert [round(t - stamps[0], 6) for t in stamps] == [0.0, 2.0]


def test_a_429_with_no_resolvable_chat_is_retried_exactly_once(run_async):
    """The bound on that retry: one, then the error surfaces. Mutation:
    recurse with ``allow_retry=True`` and a chat-less endpoint retries for
    as long as Telegram keeps refusing.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    stamps: list[float] = []

    async def _call(**kw):
        stamps.append(clock.now)
        raise RetryAfter(2)

    async def _drive():
        with pytest.raises(RetryAfter):
            await _acquire(limiter, _call, endpoint="answerCallbackQuery",
                           chat_id=None)

    run_async(_drive())
    assert len(stamps) == 2


# ── E1/E3/E4: the backoff ────────────────────────────────────────────────────

def test_a_small_429_stops_every_call_to_that_chat_until_it_elapses(run_async):
    """Row E1. Mutation: drop the ``retry_until`` term from the blocking
    wait and the next send goes straight back into Telegram's window."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    started = clock.now
    limiter.note_retry_after(11, 5)

    async def _drive():
        await _acquire(limiter, call, chat_id=11)

    run_async(_drive())
    assert call.stamps[0][2] == pytest.approx(started + 5.0)


def test_a_small_429_from_the_callback_is_deferred_and_retried_once(run_async):
    """A ``RetryAfter`` the callback raises is absorbed here: the request
    runs exactly once more, after the wait Telegram asked for.

    Mutation: re-raise instead of retrying (python-telegram-bot's
    ``max_retries=0`` behaviour) and the answer is lost.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    attempts: list[float] = []

    async def _cb():
        attempts.append(clock.now)
        if len(attempts) == 1:
            raise RetryAfter(5)
        return {"ok": True}

    async def _drive():
        return await limiter.process_request(
            callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": 12}, rate_limit_args=None,
        )

    assert run_async(_drive()) == {"ok": True}
    assert len(attempts) == 2
    assert attempts[1] - attempts[0] == pytest.approx(5.0)
    assert limiter.cadence_multiplier(12) == 2.0


def test_a_small_429_logs_one_warning_and_no_traceback(run_async, caplog):
    """R5. Mutation: add ``exc_info=True``, or log once per attempt, and
    2,021 429s become 2,021 stack traces again."""
    clock = FakeClock()
    limiter = _limiter(clock)
    caplog.set_level("DEBUG", logger="aipager.bot.flood_budget")

    async def _cb():
        raise RetryAfter(5)

    async def _drive():
        with pytest.raises(RetryAfter):
            await limiter.process_request(
                callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": 13}, rate_limit_args=None,
            )

    run_async(_drive())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2  # one per 429 seen; the retry got one too
    assert all(r.exc_info is None for r in caplog.records)
    assert "429 retry_after=5.0s" in warnings[0].message


def test_note_retry_after_logs_exactly_one_warning(caplog):
    """Mutation: log per attempt rather than per event."""
    clock = FakeClock()
    limiter = _limiter(clock)
    caplog.set_level("DEBUG", logger="aipager.bot.flood_budget")
    limiter.note_retry_after(14, 5)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    assert "cadence ×2" in warnings[0].message


def test_backoff_caps_at_eight():
    """Row E4. Mutation: remove ``min(..., FLOOD_BACKOFF_MAX)`` and four
    429s in a row put a card on a 20-second interval."""
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(4):
        limiter.note_retry_after(15, 3)
    assert limiter.cadence_multiplier(15) == 8.0
    limiter.note_retry_after(15, 3)
    assert limiter.cadence_multiplier(15) == 8.0
    assert card_interval(base=1.2, busy_sessions=1, is_group=False,
                         backoff=limiter.cadence_multiplier(15)) == pytest.approx(
        card_interval(base=1.2, busy_sessions=1, is_group=False) * 8)


def test_a_quiet_minute_halves_the_backoff(tmp_path):
    """Row E3. Mutation: delete ``_decay`` and a single 429 slows that
    chat's card for the life of the daemon."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    limiter.note_retry_after(16, 5)
    limiter.note_retry_after(16, 5)
    assert limiter.cadence_multiplier(16) == 4.0
    clock.now += 60.0
    assert limiter.cadence_multiplier(16) == 2.0
    clock.now += 60.0
    assert limiter.cadence_multiplier(16) == 1.0
    assert not path.exists()


def test_decay_never_loses_partial_progress_toward_the_next_halving():
    """Mutation: stamp ``last_429_at = now`` on decay instead of advancing
    it by whole windows — reading the multiplier would then restart the
    quiet window and it could never reach 1.0 under a chatty animator."""
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.note_retry_after(17, 5)
    limiter.note_retry_after(17, 5)
    for _ in range(12):  # 12 reads spread across one window
        clock.now += 5.0
        limiter.cadence_multiplier(17)
    assert limiter.cadence_multiplier(17) == 2.0


def test_a_ban_is_re_raised_untouched_and_never_backs_off(run_async, caplog):
    """Row F. Mutation: let ``note_retry_after`` accept a value over the
    cap and the limiter would start competing with the 8.17 mute for the
    one case the mute exists to own.

    The BACKOFF MULTIPLIER and the deferral are still untouched by a ban —
    that is what this row pins, and it is unchanged. What 8.27 added is
    that the ban drops the chat's EARNED RATE to the floor and stamps its
    history, with one WARNING; before it, a ban taught the limiter
    literally nothing. So the assertion is now "no backoff, no deferral,
    and exactly one rate line", not "no log at all".
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    caplog.set_level("DEBUG", logger="aipager.bot.flood_budget")

    async def _cb():
        raise RetryAfter(19289)

    async def _drive():
        with pytest.raises(RetryAfter):
            await limiter.process_request(
                callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": 18}, rate_limit_args=None,
            )

    run_async(_drive())
    assert limiter.cadence_multiplier(18) == 1.0
    assert _chat(limiter.snapshot(), 18)["retry_until_in"] == 0.0
    # 8.27: the rate remembers the ban even though the cadence does not.
    assert limiter.earned_rate(18) == pytest.approx(config.FLOOD_MIN_RATE)
    assert _chat(limiter.snapshot(), 18)["bans_today"] == 1
    rate_lines = [r for r in caplog.records if "banned for" in r.getMessage()]
    assert len(rate_lines) == 1, [r.getMessage() for r in caplog.records]
    assert not rate_lines[0].exc_info, "a ban is expected, not an error"
    assert [r for r in caplog.records if r not in rate_lines] == []


def test_note_retry_after_ignores_a_value_past_the_cap():
    """Mutation: drop the cap check in ``note_retry_after``."""
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.note_retry_after(19, config.TELEGRAM_MAX_RETRY_AFTER + 1)
    assert limiter.cadence_multiplier(19) == 1.0
    limiter.note_retry_after(19, config.TELEGRAM_MAX_RETRY_AFTER)
    assert limiter.cadence_multiplier(19) == 2.0


# ── N: reactions are exempt ──────────────────────────────────────────────────

def test_a_reaction_goes_out_while_the_chat_budget_is_empty(run_async):
    """Row N (§11 U4). Mutation: remove ``setMessageReaction`` from the
    exempt endpoint and the 👀 that tells the user their message was seen
    is itself refused — at exactly the moment the chat is jammed. (This
    row named the 🚨 give-up reaction until 8.26 D-1 deleted it: it fired
    into a chat that had just been banned.)"""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    limiter.note_retry_after(20, 30)

    async def _drive():
        for _ in range(3):
            with pytest.raises(FloodSkipped):
                await _acquire(limiter, call, chat_id=20, kind="skip")
        started = clock.now
        await _acquire(limiter, call, endpoint="setMessageReaction", chat_id=20)
        await _acquire(limiter, call, endpoint="setMessageReaction", chat_id=20,
                       kind="skip")
        assert clock.now == started

    run_async(_drive())
    assert len(call.stamps) == 2
    assert _chat(limiter.snapshot(), 20)["reactions"] == 2


def test_a_429_on_a_reaction_still_defers_and_backs_off_the_chat(run_async):
    """Row N's other half, and the guard roadmap 8.24 put underneath it: a
    reaction is exempt from the chat BUDGET, not from what a 429 MEANS. It
    is a message-shaped call that Telegram answered by saying "too many
    into this chat", so the chat is deferred and its card cadence doubles
    exactly as for any send — which is what 0.7.11 shipped and what
    ``note_429`` must not quietly change.

    The chat action is the ONLY exempt endpoint that skips
    ``note_retry_after`` (its 429 says nothing about the message bucket it
    is not in). Nothing asserted the other side of that ``!=`` until this
    row: review iteration 2 replaced
    ``note_429=endpoint != CHAT_ACTION_ENDPOINT`` with ``note_429=False``
    and the whole 6,815-test suite still passed.

    Mutation: widen ``note_429=False`` to every exempt endpoint and this is
    the only row in the suite that notices.
    """
    clock = FakeClock()
    limiter = _limiter(clock)

    async def _boom(**kw):
        raise RetryAfter(5)

    async def _drive():
        with pytest.raises(RetryAfter):
            await _acquire(limiter, _boom, endpoint="setMessageReaction",
                           chat_id=21)

    run_async(_drive())
    assert limiter.cadence_multiplier(21) == 2.0
    assert _chat(limiter.snapshot(), 21)["retry_until_in"] == pytest.approx(5.0)


# ── P / J: the signal file ───────────────────────────────────────────────────

def test_the_backoff_file_is_not_the_mute_file():
    """Row P. Mutation: point the backoff writer at ``FLOOD_MUTE_FILE``.
    ``FloodMute._write_signal`` unlinks that path whenever no mute is
    active, so the backoff section would vanish the first time a mute
    lapsed — and two writers replacing one path race."""
    assert config.FLOOD_BACKOFF_FILE != config.FLOOD_MUTE_FILE
    assert Path(config.FLOOD_BACKOFF_FILE).name == "aipager-flood-backoff.json"


def test_a_backoff_never_writes_to_the_mute_file(tmp_path):
    """Row P / G25. The two signal files have separate owners and separate
    lifecycles: ``FloodMute._write_signal`` UNLINKS the mute file whenever
    no mute is active, so a backoff section living there would vanish the
    first time a mute lapsed — and two writers doing write-then-replace on
    one path race, with the loser's half silently lost.

    Mutation: point ``_maybe_write_signal`` / ``clear_backoff_signal`` at
    ``config.FLOOD_MUTE_FILE`` and the mute's file gains a foreign
    document while the backoff's own file is never written.
    """
    clock = FakeClock()
    limiter = _limiter(clock)          # signal_path=None: reads config late
    limiter.note_retry_after(26, 5)
    assert Path(config.FLOOD_BACKOFF_FILE).exists()
    assert not Path(config.FLOOD_MUTE_FILE).exists()
    assert json.loads(Path(config.FLOOD_BACKOFF_FILE).read_text())["backoff"]


def test_flood_py_is_not_a_writer_of_the_backoff_file():
    """Row P, the other half: ``bot/flood.py`` owns the 8.17 mute and its
    own signal file, and NOT the backoff file — its two unlink tests are
    what a shared file would break.

    Mutation: teach ``flood.py`` about the backoff and the mute's own
    signal-file tests start failing for reasons unrelated to their names.

    The "no mention of ``flood_budget`` at all" form of this assertion was
    retired in 8.29 T1: arming a mute now tells the live limiter, so the
    ban drops the earned rate at the instant it is armed rather than
    whenever a call next happens to reach the limiter (which, while a mute
    holds, is never). That is ONE late import inside ONE function, and
    what this row pins now is that it stayed that way — nothing about the
    BACKOFF, and no module-level dependency that could invert the import
    order (``flood_budget`` imports ``flood`` at module level).
    """
    from aipager.bot import flood

    source = Path(flood.__file__).read_text(encoding="utf-8")
    assert "FLOOD_BACKOFF" not in source
    assert "backoff" not in source.lower()
    code = [line for line in source.splitlines()
            if "flood_budget" in line and not line.lstrip().startswith(("#", "*"))
            and ":class:" not in line and "``" not in line]
    assert code == ["        from aipager.bot.flood_budget import BudgetRateLimiter"]


def test_the_backoff_file_never_points_at_the_real_runtime_dir(tmp_path):
    """A guard on the guard (G29). Mutation: remove the new
    ``monkeypatch.setattr`` from ``conftest._isolate_flood_mute`` and
    every test that arms a backoff writes beside the LIVE daemon's
    socket."""
    assert Path(config.FLOOD_BACKOFF_FILE).parent == tmp_path
    assert Path(config.FLOOD_MUTE_FILE).parent == tmp_path


def test_a_probe_that_is_not_the_daemon_never_writes_beside_the_socket(
        tmp_path, monkeypatch, run_async):
    """Process safety (tester-iter1-008), found the hard way during QA: a
    throwaway ``python -c`` that calls ``note_retry_after`` used to write
    ``aipager-flood-backoff.json`` into the LIVE daemon's runtime
    directory, and `aipager status` would then report a backoff no daemon
    has. The conftest fixture cannot help anything running outside pytest.

    Both paths are rehearsed on a tmp directory shaped like the real one
    (signal file beside the socket), so the mutation below cannot write
    into ``$XDG_RUNTIME_DIR`` even while it is failing.

    Mutation: drop the ``_signal_armed`` check from
    ``_maybe_write_signal`` and the unarmed limiter writes the file.
    """
    monkeypatch.setattr("aipager.config.SOCKET_PATH",
                        str(tmp_path / "aipager.sock"))
    monkeypatch.setattr("aipager.config.FLOOD_BACKOFF_FILE",
                        str(tmp_path / "aipager-flood-backoff.json"))
    signal = Path(config.FLOOD_BACKOFF_FILE)

    probe = _limiter(FakeClock())          # nothing ever initialize()d it
    probe.note_retry_after(-100, 5)
    assert not signal.exists()

    daemon = _limiter(FakeClock())
    run_async(daemon.initialize())         # what ExtBot.initialize awaits
    daemon.note_retry_after(-100, 5)
    assert signal.exists()


def test_an_explicit_signal_path_is_always_the_callers_to_write(
        tmp_path, monkeypatch):
    """The other half of the arming rule: a caller that NAMES the path
    owns it, daemon or not — even when it names the daemon's own
    directory, which is how a future tool ("show me the live backoff")
    would drive this writer deliberately.

    Mutation: ``self._signal_armed = False`` in ``__init__`` (arm only on
    ``initialize``) and every ``signal_path=`` caller pointed at the
    runtime directory silently stops writing.
    """
    monkeypatch.setattr("aipager.config.SOCKET_PATH",
                        str(tmp_path / "aipager.sock"))
    signal = tmp_path / "aipager-flood-backoff.json"   # beside that socket
    limiter = _limiter(FakeClock(), signal_path=str(signal))
    limiter.note_retry_after(-100, 5)
    assert json.loads(signal.read_text())["backoff"][0]["multiplier"] == 2.0


def test_a_backing_off_chat_is_published_and_cleared_again(tmp_path):
    """Mutation: never unlink when no chat is backing off and `aipager
    status` reports a multiplier that decayed away minutes ago."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    limiter.note_retry_after(21, 5)
    doc = json.loads(path.read_text())
    assert doc["backoff"] == [
        {"chat_id": 21, "multiplier": 2.0, "last_429_at": doc["backoff"][0]["last_429_at"]},
    ]
    assert doc["backoff"][0]["last_429_at"] > 1_600_000_000  # a WALL epoch
    clock.now += 60.0
    limiter.cadence_multiplier(21)
    assert not path.exists()


def test_the_signal_file_is_rewritten_at_most_once_every_five_seconds(tmp_path):
    """Mutation: drop the throttle and a chatty chat rewrites a
    diagnostic file on every acquire."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    limiter.note_retry_after(22, 5)
    first = json.loads(path.read_text())["backoff"][0]["multiplier"]
    clock.now += 1.0
    limiter.note_retry_after(22, 5)  # ×4 now, but inside the 5 s window
    assert json.loads(path.read_text())["backoff"][0]["multiplier"] == first
    clock.now += 10.0
    limiter.note_retry_after(22, 5)
    assert json.loads(path.read_text())["backoff"][0]["multiplier"] == 8.0


def test_the_signal_file_counts_exempt_reactions_per_chat(tmp_path, run_async):
    """§11 U4's evidence clause. Mutation: stop counting reactions and the
    next incident cannot show whether Telegram meters them."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    call = _recorder(clock)

    async def _drive():
        for _ in range(3):
            await _acquire(limiter, call, endpoint="setMessageReaction",
                           chat_id=23)

    run_async(_drive())
    limiter.note_retry_after(23, 5)
    assert json.loads(path.read_text())["reactions"] == [
        {"chat_id": 23, "count": 3},
    ]


def test_backoff_signal_write_failure_never_reaches_the_send_path(tmp_path,
                                                                  run_async):
    """Mirrors ``test_flood_mute.py``'s signal-file guard. Mutation: let
    the ``OSError`` escape ``_maybe_write_signal`` and a full disk takes
    the send path down with it."""
    clock = FakeClock()
    unwritable = tmp_path / "nope" / "backoff.json"  # parent does not exist
    limiter = _limiter(clock, signal_path=str(unwritable))
    limiter.note_retry_after(24, 5)          # must not raise
    assert limiter.cadence_multiplier(24) == 2.0
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, chat_id=24)

    run_async(_drive())
    assert len(call.stamps) == 1


def test_a_sweep_takes_the_signal_down_once_the_backoff_has_decayed(tmp_path):
    """Design §5: the file is unlinked as soon as no chat is backing off.
    ``cadence_multiplier`` only runs while a busy card is TICKING, so a
    chat that 429s and then goes quiet would report "×2" to `aipager
    status` for as long as nobody started a turn.

    Mutation: drop the ``_decay`` loop from ``sweep`` (or make it a no-op)
    and the file survives its own backoff.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.note_retry_after(-100, 5)
    assert Path(config.FLOOD_BACKOFF_FILE).exists()

    clock.now += config.FLOOD_BACKOFF_DECAY_SECONDS   # one quiet window: ×2 → ×1
    limiter.sweep()

    assert not Path(config.FLOOD_BACKOFF_FILE).exists()
    assert status.read_flood_backoffs() == []


def test_a_sweep_keeps_publishing_a_chat_that_is_still_backing_off(tmp_path):
    """The other half: a sweep must not unlink a LIVE backoff. Mutation:
    unlink unconditionally in ``sweep`` and the operator loses the one
    line that explains why the cards are slow.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.note_retry_after(-100, 5)
    clock.now += 1.0
    limiter.sweep()
    assert [b["multiplier"] for b in status.read_flood_backoffs()] == [2.0]


def test_the_session_monitor_tick_sweeps_the_backoff(monkeypatch, run_async):
    """The wiring itself: the 2 s scan is what drives the decay when no
    card is ticking. Driven through ``_scan`` — the daemon's own periodic
    work — rather than by calling the helper, so the MUTATION that matters
    is covered.

    Mutation: delete the ``_sweep_flood_backoff()`` call from
    ``SessionMonitor._scan`` and a stale multiplier lives for ever.
    """
    from unittest.mock import AsyncMock

    from aipager.bot import rich_message as rm
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry

    clock = FakeClock()
    limiter = _limiter(clock)
    rm.set_rate_limiter(limiter)
    limiter.note_retry_after(-100, 5)
    assert Path(config.FLOOD_BACKOFF_FILE).exists()

    async def _noop(*a, **kw):
        return None

    monitor = SessionMonitor(SessionRegistry(), _noop)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))
    clock.now += config.FLOOD_BACKOFF_DECAY_SECONDS
    run_async(monitor._scan())

    assert not Path(config.FLOOD_BACKOFF_FILE).exists()


def test_a_monitor_scan_survives_a_daemon_with_no_budget_limiter(monkeypatch,
                                                                 run_async):
    """A daemon running PTB's own limiter (or none at all — every test
    that does not install one) must still scan. Mutation: call
    ``limiter.sweep()`` without the isinstance check and every such scan
    raises ``AttributeError`` into the monitor's error log.
    """
    from unittest.mock import AsyncMock

    from aipager.bot import rich_message as rm
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry

    rm.set_rate_limiter(object())

    async def _noop(*a, **kw):
        return None

    monitor = SessionMonitor(SessionRegistry(), _noop)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))
    run_async(monitor._scan())   # MUST NOT raise


def test_clear_backoff_signal_is_idempotent_and_never_raises(tmp_path):
    """Mutation: drop the ``missing_ok``/``OSError`` guard and daemon
    start-up crashes when there is nothing to clean up."""
    path = tmp_path / "gone.json"
    clear_backoff_signal(str(path))
    path.write_text("{}")
    clear_backoff_signal(str(path))
    assert not path.exists()
    clear_backoff_signal(str(tmp_path))  # a directory: OSError, swallowed


def test_reset_forgets_every_chat_and_unlinks_the_file(tmp_path):
    """Row J. Mutation: keep the budgets in ``reset()`` and a restart
    would inherit the previous run's deferral."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    limiter.note_retry_after(25, 5)
    assert path.exists()
    limiter.reset()
    assert limiter.cadence_multiplier(25) == 1.0
    assert limiter.snapshot()["chats"] == []
    assert not path.exists()


def test_shutdown_takes_the_signal_file_down_with_the_daemon(tmp_path, run_async):
    """Mutation: make ``shutdown`` a no-op (what ``AIORateLimiter`` does)
    and a dead daemon's backoff outlives it in `aipager status`."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    path.write_text('{"backoff": []}')
    run_async(limiter.shutdown())
    assert not path.exists()


def test_initialize_refreshes_the_signal_to_match_the_restored_state(
    tmp_path, run_async,
):
    """8.28 R5 REVERSES R8 here, and this row is the split of the old
    "initialize clears the file too".

    ``initialize()`` used to unlink the backoff file because "a restart
    starts every chat at ×1" — nothing was restored, so any file could
    only be a previous daemon's. Since 8.28 ``flood_state.load()`` runs
    BEFORE it and the restored state is real, so unlinking would publish
    "no backoff" for a chat that genuinely has one. It now REFRESHES the
    signal instead.

    Mutation: unlink here again and a restored ×4 chat is invisible to
    `aipager status` until its next 429.
    """
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    # A chat that the restore has just put back at ×2.
    limiter.note_retry_after(-1002, 5)
    path.unlink(missing_ok=True)

    run_async(limiter.initialize())

    assert path.exists(), "the restored backoff was not published"
    written = json.loads(path.read_text())["backoff"]
    assert [e["chat_id"] for e in written] == [-1002]
    assert written[0]["multiplier"] == 2.0


def test_initialize_still_publishes_nothing_when_no_chat_is_backing_off(
    tmp_path, run_async,
):
    """The other half: a clean restart must not leave a stale file behind
    either. With nothing restored there is nothing to publish, and
    `_maybe_write_signal` unlinks rather than writing an empty list."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    path.write_text('{"backoff": [{"chat_id": -1, "multiplier": 8.0}]}')

    run_async(limiter.initialize())

    assert not path.exists(), "a previous daemon's backoff survived a restart"


# ── misc surface ─────────────────────────────────────────────────────────────

def test_is_group_chat_reads_negative_ids_and_at_names_as_groups():
    """Mutation: treat a string id as private and a channel gets the 1/s
    private floor instead of its 20/min budget."""
    assert is_group_chat(-1001234567890) is True
    assert is_group_chat("@somechannel") is True
    assert is_group_chat(256113222) is False
    assert is_group_chat(None) is False


def test_a_string_chat_id_lands_on_the_same_budget_as_its_int(run_async):
    """Mirrors ``flood._key``. Mutation: key on the raw value and
    ``"123"`` and ``123`` become two chats with two budgets."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, chat_id=123)
        await _acquire(limiter, call, chat_id="123")

    run_async(_drive())
    assert len(limiter.snapshot()["chats"]) == 1
    assert _chat(limiter.snapshot(), 123)["calls"] == 2


def test_card_interval_is_the_documented_rule():
    """R4/R9. Mutation: drop the ``max(base, ...)`` and a sub-floor
    ``STREAM_EDIT_INTERVAL`` would set the cadence instead of the floor."""
    assert card_interval(base=1.2, busy_sessions=1, is_group=False) == pytest.approx(1.32)
    assert card_interval(base=1.2, busy_sessions=2, is_group=False) == pytest.approx(2.2)
    assert card_interval(base=1.2, busy_sessions=3, is_group=False) == pytest.approx(3.3)
    assert card_interval(base=1.2, busy_sessions=1, is_group=True) == pytest.approx(3.3)
    assert card_interval(base=3.0, busy_sessions=1, is_group=False) == pytest.approx(3.3)
    # R9: a value below the floor changes nothing observable.
    assert card_interval(base=0.1, busy_sessions=1, is_group=False) == pytest.approx(1.1)
    # N <= 0 is clamped to one: a card ticking for an uncounted session
    # must still be paced.
    assert card_interval(base=0.1, busy_sessions=0, is_group=False) == pytest.approx(1.1)
    assert card_interval(base=1.2, busy_sessions=1, is_group=False,
                         backoff=4.0) == pytest.approx(5.28)


def test_snapshot_reports_the_whole_budget(run_async):
    """Mutation: report the raw token count without refilling and every
    reader sees a chat that drained an hour ago as still empty."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, chat_id=-1002)
        await _acquire(limiter, call, chat_id=31)

    run_async(_drive())
    snap = limiter.snapshot()
    assert snap["overall_tokens"] == pytest.approx(28.0)
    group = _chat(snap, -1002)
    assert group["kind"] == "group"
    # 8.27: `group_window_free` is gone. The rolling window exists for
    # EVERY chat now — a private chat used to have none at all, so its
    # 1/s bucket permitted 60 calls a minute indefinitely, which is what
    # two BUSY sessions did for ~45 minutes before the 9.5-hour ban.
    # A group keeps the stricter of the two ceilings.
    assert group["sustained_limit"] == int(config.TELEGRAM_GROUP_MAX_CALLS)
    assert (group["sustained_used"], group["sustained_free"]) == (1, 19)
    private = _chat(snap, 31)
    assert private["kind"] == "private"
    assert private["sustained_limit"] == int(config.FLOOD_SUSTAINED_MAX)
    assert (private["sustained_used"], private["sustained_free"]) == (1, 29)
    assert private["waiters"] == 0
    # The 8.26/8.27 columns a reader (`aipager status`) depends on.
    assert private["rate"] == pytest.approx(config.FLOOD_START_RATE)
    assert private["minimal"] is False
    assert (private["muted_refusals"], private["ornaments_suspended"],
            private["bans_today"]) == (0, 0, 0)
    assert "group_window_free" not in private


def test_an_unrecognised_rate_limit_args_shape_is_blocking(run_async):
    """Fail-open. Mutation: treat anything truthy as a skip and PTB's own
    integer ``rate_limit_args`` convention would silently drop sends."""
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        for _ in range(3):
            await _acquire(limiter, call, chat_id=32)
        started = clock.now
        await limiter.process_request(
            callback=call, args=(), kwargs={"endpoint": "x", "chat_id": 32},
            endpoint="sendMessage", data={"chat_id": 32}, rate_limit_args=5,
        )
        assert clock.now == pytest.approx(started + 1.0)

    run_async(_drive())
    assert len(call.stamps) == 4


def test_flood_budget_reads_time_only_through_its_injected_clock():
    """A fake-clock suite can hide a real ``time.monotonic()`` read the
    injection missed — every row here would still pass while the daemon
    paced itself off the wall. Only the DEFAULT argument may name it.

    Mutation: call ``time.monotonic()`` anywhere in the module and this
    names the line.
    """
    import tokenize

    from aipager.bot import flood_budget

    # Tokenised, not parsed, and streamed through a four-token window
    # rather than collected into a list: comments and docstrings come back
    # as COMMENT and STRING tokens, so prose mentioning ``time.monotonic()``
    # cannot trip this, and neither a module AST (~1.7 MB) nor a full token
    # list is held — the suite runs within ~1 MB of its RLIMIT_AS and every
    # megabyte of peak raises the high-water mark for good (roadmap 8.19).
    offenders = []
    window: list[tuple[int, str]] = []
    with open(flood_budget.__file__, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type not in (tokenize.NAME, tokenize.OP):
                continue
            window.append((tok.start[0], tok.string))
            if len(window) > 4:
                window.pop(0)
            if [t for _, t in window] == ["time", ".", "monotonic", "("]:
                offenders.append(window[0][0])
    assert offenders == [], offenders


# ── P/J, the reading half: `aipager status` and `aipager doctor` ─────────────

def _write_backoff_signal(multiplier=4.0, chat_id=-100, ago=12.0) -> float:
    """Drop the signal file exactly as ``_maybe_write_signal`` writes it."""
    last = time.time() - ago
    Path(config.FLOOD_BACKOFF_FILE).write_text(json.dumps(
        {"backoff": [{"chat_id": chat_id, "multiplier": multiplier,
                      "last_429_at": last}], "reactions": []}))
    return last


def test_read_flood_backoffs_returns_backing_off_chats_only():
    """The reader mirrors ``read_flood_mutes``: never infer a backoff, and
    drop anything that does not carry a multiplier over 1.

    Mutation: return the raw ``data["backoff"]`` list and a malformed
    entry, a ×1 entry or a half-written file becomes a phantom backoff in
    the operator's status output.
    """
    assert status.read_flood_backoffs() == []            # no file
    Path(config.FLOOD_BACKOFF_FILE).write_text("{not json")
    assert status.read_flood_backoffs() == []            # garbage
    Path(config.FLOOD_BACKOFF_FILE).write_text(json.dumps({"backoff": "no"}))
    assert status.read_flood_backoffs() == []            # wrong shape
    Path(config.FLOOD_BACKOFF_FILE).write_text(
        json.dumps({"backoff": [{"chat_id": 7}]}))
    assert status.read_flood_backoffs() == []            # no multiplier
    _write_backoff_signal(multiplier=1.0)
    assert status.read_flood_backoffs() == []            # not backing off
    _write_backoff_signal(multiplier=4.0)
    entry = status.read_flood_backoffs()[0]
    assert (entry["chat_id"], entry["multiplier"]) == (-100, 4.0)


def test_the_age_of_a_backoff_is_computed_when_it_is_read():
    """The file is rewritten at most once every 5 s, so a stored age would
    be stale before anyone read it. Two reads of ONE file must therefore
    disagree by the time between them.

    Mutation: store ``last_429_ago`` at write time and read it back, and a
    chat that 429'd an hour ago still reports "12 s ago".
    """
    _write_backoff_signal(ago=12.0)
    assert int(status.read_flood_backoffs()[0]["last_429_ago"]) == 12
    _write_backoff_signal(ago=3600.0)
    assert int(status.read_flood_backoffs()[0]["last_429_ago"]) == 3600


def test_a_backoff_line_reads_like_the_troubleshooting_doc_says(tmp_path):
    """``docs/troubleshooting.md`` quotes this line verbatim; so does
    ``design.md`` §9. Mutation: render the multiplier with ``%f`` and the
    operator reads "×4.000000", or drop the ``:g`` and a ×2 backoff prints
    as "×2.0".
    """
    _write_backoff_signal(multiplier=4.0, chat_id=-100, ago=12.0)
    backoffs = status.read_flood_backoffs()
    assert status.flood_backoff_lines(backoffs) == [
        "Telegram flood backoff ×4 (chat -100), last 429 12 s ago"]
    assert status.flood_backoff_lines([]) == []


def test_the_daemon_writes_the_backoff_line_the_cli_reads_back(tmp_path):
    """Writer and reader agree on the file, end to end: arm a 429 the way
    the daemon does, read it the way `aipager status` does.

    Mutation: change either half's key names and this is the test that
    notices — the two live in different modules and different processes.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.note_retry_after(-100, 5)
    line = status.flood_backoff_lines(status.read_flood_backoffs())[0]
    assert line.startswith("Telegram flood backoff ×2 (chat -100), last 429 ")


def test_status_json_and_text_both_carry_the_backoff(monkeypatch, capsys):
    """Case J through `aipager status` itself, both renderings.

    Mutation: drop ``flood_backoff`` from the JSON payload or the lines
    from ``_render_plain`` and the operator has no way to see why the
    cards slowed down.
    """
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    _write_backoff_signal(multiplier=4.0, chat_id=-100)

    assert status.cmd_status(_ns(as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [b["chat_id"] for b in payload["flood_backoff"]] == [-100]

    status.cmd_status(_ns(as_json=False))
    assert "Telegram flood backoff ×4 (chat -100)" in capsys.readouterr().out

    Path(config.FLOOD_BACKOFF_FILE).unlink()
    status.cmd_status(_ns(as_json=False))
    assert "flood backoff" not in capsys.readouterr().out


def test_doctors_daemon_row_reports_a_backoff_without_changing_severity(
        tmp_path, monkeypatch):
    """`design.md` §10, and the promise `docs/troubleshooting.md` already
    makes to the user. A backoff is NORMAL operation — the chat is being
    paced, nothing is muted, no message is lost — so it rides along in the
    row's ``detail`` and the row stays green.

    Mutation: delete the ``read_flood_backoffs`` wiring from
    ``check_daemon`` (the state this shipped in at iteration 1) and the
    line never appears; make it a WARN and an operator starts restarting a
    healthy daemon into a chat that is merely being paced.
    """
    from aipager import doctor

    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
        row = doctor.check_daemon()
        assert row.status == doctor.OK
        assert not [d for d in row.detail if "backoff" in d]

        _write_backoff_signal(multiplier=4.0, chat_id=-100, ago=12.0)
        row = doctor.check_daemon()
        assert row.status == doctor.OK, "a backoff is not a warning"
        assert "Telegram flood backoff ×4 (chat -100), last 429 12 s ago" \
            in row.detail
    finally:
        server.close()


def test_doctors_muted_row_keeps_its_warning_and_gains_the_backoff_line(
        tmp_path, monkeypatch):
    """The other branch of §10: a mute still WARNs, still tells the
    operator not to restart into it, and the backoff line is appended
    after the mute's own lines rather than replacing them.

    Mutation: return early on the mute and the backoff disappears exactly
    when the chat is in the most trouble.
    """
    from aipager import doctor

    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
        Path(config.FLOOD_MUTE_FILE).write_text(json.dumps(
            {"muted": [{"chat_id": -100, "until": time.time() + 3600,
                        "retry_after": 28911}]}))
        _write_backoff_signal(multiplier=8.0, chat_id=-100, ago=1.0)

        row = doctor.check_daemon()
        assert row.status == doctor.WARN
        assert [d for d in row.detail if "flood-muted until" in d]
        assert [d for d in row.detail if "self-clears" in d]
        assert [d for d in row.detail if "flood backoff ×8" in d]
    finally:
        server.close()
