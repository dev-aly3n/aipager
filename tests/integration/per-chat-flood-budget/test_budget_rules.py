"""Black-box tests for the per-chat flood budget (roadmap 8.21, rows A,
B3, C1, D, E3, E4, G, H, J, K, N, O, P; rules R1, R2, R3, R4, R7, R8).

Written against ``entrypoints.md`` only: the limiter is driven through
``BudgetRateLimiter.process_request`` with a recording callback, exactly as
PTB's ExtBot and ``rich_message._post`` drive it. Nothing here imports a
private name, and every stamp comes from the injected clock — no test in
this file sleeps for real, and ``asyncio.sleep`` is never patched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
from pathlib import Path

import pytest

from aipager import config, status
from aipager.bot.flood_budget import (
    BudgetRateLimiter,
    FloodSkipped,
    TokenBucket,
    card_interval,
    is_group_chat,
)

PRIVATE = 256113222
GROUP = -1001234567890


@pytest.fixture
def run_async():
    """Override the shared fixture so every loop is CLOSED (see
    ``tests/test_miniapp_webapp_sdk.py``): these tests leave FIFO waiters
    pending, and an abandoned loop costs address space the suite does not
    have under its 1 GiB ``RLIMIT_AS``."""
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run

    for loop in loops:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class FakeClock:
    """The only injection seam entrypoints.md offers: a monotonic callable
    plus an async sleep that advances it and yields once."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    """The limiter under test, with its signal file passed EXPLICITLY.

    ``signal_path=None`` means "publish only when the daemon's control
    socket is there", which makes a file assertion depend on whether the
    machine running the suite happens to have a daemon up: the two file
    rows below passed on the developer's box and failed with no socket
    present (see the report's CI-parity finding). The explicit path is the
    seam entrypoints.md documents, and it is what makes these rows
    deterministic anywhere. ``config.FLOOD_BACKOFF_FILE`` is already
    inside ``tmp_path`` by the autouse ``_isolate_flood_mute`` fixture."""
    lim = BudgetRateLimiter(clock=clock, sleep=clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    yield lim
    lim.reset()


def _call(limiter, clock, log, *, chat_id=PRIVATE, endpoint="sendMessage",
          kind=None, tag=None, raises=None):
    """One outbound call through the limiter, recording ``(tag, chat, t)``."""
    async def cb():
        log.append((tag if tag is not None else endpoint, chat_id, clock.now))
        if raises is not None:
            raise raises
        return {"ok": True}

    data = {} if chat_id is None else {"chat_id": chat_id}
    return limiter.process_request(
        callback=cb, args=(), kwargs={}, endpoint=endpoint, data=data,
        rate_limit_args=({"kind": "skip"} if kind == "skip" else None),
    )


def _most_in_window(stamps: list[float], window: float) -> int:
    """The largest number of stamps in any half-open window of that width."""
    return max((sum(1 for t in stamps if s <= t < s + window) for s in stamps),
               default=0)


# ── R1: the per-chat rate ────────────────────────────────────────────────────

def test_a_token_bucket_starts_full_and_refuses_one_token_past_its_capacity(clock):
    """Boundary values around the documented burst. Mutation: start the
    bucket empty, or let ``take`` succeed on a hair more than it holds."""
    bucket = TokenBucket(1.0, 3.0, clock=clock)
    assert bucket.tokens() == 3.0
    assert bucket.take(3.0) is True
    assert bucket.take(0.001) is False


def test_a_drained_bucket_reports_exactly_how_long_one_token_takes(clock):
    """``time_until`` is the pacing promise in scalar form. Mutation:
    return 0.0 while empty and every caller stops waiting."""
    bucket = TokenBucket(2.0, 3.0, clock=clock)
    bucket.take(3.0)
    assert bucket.time_until(1.0) == pytest.approx(0.5)


def test_twenty_back_to_back_sends_into_one_private_chat_stay_under_one_per_second(
    limiter, clock, run_async,
):
    """Row K / R1 with the cadence rule out of the picture: twenty sends
    submitted in one instant, the limiter alone holding the line.
    Mutation: drop the per-chat bucket and all twenty land at once."""
    log: list[tuple] = []

    async def burst():
        for _ in range(20):
            await _call(limiter, clock, log)

    run_async(asyncio.wait_for(burst(), timeout=10))
    stamps = [t for _, _, t in log]
    assert _most_in_window(stamps, 1.0) <= 3
    assert stamps[-1] - stamps[0] >= 16.0


def test_every_chat_scoped_endpoint_is_metered_by_the_same_chat_bucket(
    limiter, clock, run_async,
):
    """R1 names the message-shaped endpoints; one bucket must cover them
    all. Mutation: meter only ``sendMessage`` and the card's edits go
    unpaced.

    ``sendChatAction`` is deliberately NOT in this list since roadmap 8.24
    — Telegram does not meter chat actions with messages (measured
    2026-09-12, §12), so the daemon does not either, and the row below
    asserts that directly. It used to be listed here and the row still
    passed, because the assertion is an upper bound that an instant exempt
    call cannot break: a guard naming an endpoint it no longer covers
    (review rev-iter1-002).

    Asserted as an EXACT sequence, not just a ceiling, so the row cannot
    pass while one of the endpoints it names is unmetered: six calls paced
    1/s with a burst of 3 land at 0, 0, 0, 1, 2, 3.
    """
    endpoints = ["sendMessage", "editMessageText", "editMessageReplyMarkup",
                 "deleteMessage", "sendDocument", "sendPhoto"]
    log: list[tuple] = []

    async def burst():
        for endpoint in endpoints:
            await _call(limiter, clock, log, endpoint=endpoint)

    run_async(asyncio.wait_for(burst(), timeout=10))
    start = log[0][2]
    assert [round(t - start, 6) for _, _, t in log] == [0, 0, 0, 1, 2, 3]
    assert _most_in_window([t for _, _, t in log], 1.0) <= 3


def test_the_chat_action_endpoint_is_not_metered_by_the_chat_bucket(
    limiter, clock, run_async,
):
    """The sibling of the row above, and the reason ``sendChatAction`` left
    its list (roadmap 8.24 / review rev-iter1-002). The endpoint stays
    NAMED in this suite — it is simply named as the one exception.

    Telegram was measured answering it 200 on all eleven calls made inside
    a real ``retry_after=10`` window that refused every ``editMessageText``
    into the same chat (§12), so the daemon must not charge it to that
    chat's budget: an ornament that takes a token is an ornament that
    costs a card edit.

    Mutation: drop ``sendChatAction`` from ``_CHAT_BUDGET_EXEMPT`` (i.e.
    meter it like a message again) and the fourth call here waits a second
    instead of going out at once — and the exempt counter stays 0.
    """
    log: list[tuple] = []

    async def burst():
        for _ in range(3):                      # drain the chat's burst
            await _call(limiter, clock, log, endpoint="sendMessage")
        for _ in range(4):                      # …then four chat actions
            await _call(limiter, clock, log, endpoint="sendChatAction")

    run_async(asyncio.wait_for(burst(), timeout=10))
    start = log[0][2]
    assert [round(t - start, 6) for _, _, t in log] == [0, 0, 0, 0, 0, 0, 0]
    chat = next(c for c in limiter.snapshot()["chats"]
                if c["chat_id"] == PRIVATE)
    assert chat["chat_actions"] == 4
    assert chat["tokens"] < 1.0, "the actions spent the chat's tokens"


def test_sixty_chats_sending_at_once_are_paced_by_the_overall_bucket(
    clock, run_async,
):
    """R1's overall 30/s, proven with sixty chats that each have a full
    private budget of their own, so only the overall bucket can hold them.
    Asserted as the SUSTAINED rate (the burst of 30 is the bucket's
    capacity): the 60th call cannot land before the 31st-to-60th have been
    refilled at 30/s. Mutation: drop the overall bucket and all sixty land
    in the same instant."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def burst():
        for i in range(60):
            await _call(limiter, clock, log, chat_id=1000 + i)

    try:
        run_async(asyncio.wait_for(burst(), timeout=10))
        stamps = [t for _, _, t in log]
        assert stamps[-1] - stamps[0] > 0.99
    finally:
        limiter.reset()


def test_a_group_chat_admits_no_more_than_twenty_calls_in_any_sixty_seconds(
    clock, run_async,
):
    """Row B3/G, the group's second bucket. The fixture pins a POSITIVE
    CHAT_ID, so the group id is stamped explicitly. Mutation: apply only
    the 1/s private bucket and 25 calls land inside one minute."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def burst():
        for i in range(25):
            await _call(limiter, clock, log, chat_id=GROUP, tag=i)

    try:
        run_async(asyncio.wait_for(burst(), timeout=20))
        assert _most_in_window([t for _, _, t in log], 60.0) <= 20
    finally:
        limiter.reset()


def test_exactly_twenty_group_calls_land_in_the_first_minute_and_the_rest_after(
    clock, run_async,
):
    """Row G's exact schedule, now that the group limit is a ROLLING 60 s
    window rather than a token bucket: 25 queued calls split 20 / 5 at the
    minute boundary. Mutation: model the window as a bucket with capacity
    20 again and all 25 land inside the first 22 s (iteration 1's red
    row)."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def burst():
        for i in range(25):
            await _call(limiter, clock, log, chat_id=GROUP, tag=i)

    try:
        run_async(asyncio.wait_for(burst(), timeout=20))
        stamps = [t for _, _, t in log]
        first_minute = [t for t in stamps if t < stamps[0] + 60.0]
        assert (len(first_minute), len(stamps)) == (20, 25)
    finally:
        limiter.reset()


def test_the_twenty_first_group_call_waits_for_the_window_to_roll(
    clock, run_async,
):
    """The boundary itself: the 21st call cannot go out until the oldest
    of the twenty is a full minute old. Mutation: drop the oldest-stamp
    wait and it leaves early — which is exactly how a group earns its
    429."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def burst():
        for i in range(21):
            await _call(limiter, clock, log, chat_id=GROUP, tag=i)

    try:
        run_async(asyncio.wait_for(burst(), timeout=20))
        stamps = [t for _, _, t in log]
        assert stamps[20] - stamps[0] == pytest.approx(60.0, abs=1e-6)
    finally:
        limiter.reset()


def test_a_group_window_that_has_rolled_admits_a_fresh_twenty(
    clock, run_async,
):
    """"Rolling" means the budget comes back on its own: after a quiet
    minute the group may take another twenty at the ordinary 1/s pace,
    with no call ever making 21 in any window. Mutation: never expire the
    stamps and the group is throttled for ever after its first busy
    minute."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def burst(n):
        for i in range(n):
            await _call(limiter, clock, log, chat_id=GROUP, tag=i)

    try:
        run_async(asyncio.wait_for(burst(20), timeout=20))
        clock.now += 61.0
        started = clock.now
        run_async(asyncio.wait_for(burst(20), timeout=20))
        stamps = [t for _, _, t in log]
        assert stamps[-1] - started < 20.0, "the rolled window did not come back"
        assert _most_in_window(stamps, 60.0) <= 20
    finally:
        limiter.reset()


def test_a_skip_acquire_into_a_full_group_window_is_refused(clock, run_async):
    """The skip half of the rolling window: a card must yield to the
    window even when the 1/s bucket is full of tokens (it is, five seconds
    after the twentieth call). Mutation: check only the token bucket for
    skip callers and the card spends the group's minute."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def fill():
        for i in range(20):
            await _call(limiter, clock, log, chat_id=GROUP, tag=i)

    try:
        run_async(asyncio.wait_for(fill(), timeout=20))
        clock.now += 5.0  # the per-chat bucket is back to its full burst
        assert limiter.snapshot()["chats"][0]["tokens"] == pytest.approx(3.0)

        async def card():
            await _call(limiter, clock, log, chat_id=GROUP,
                        endpoint="editMessageText", kind="skip", tag="card")

        with pytest.raises(FloodSkipped):
            run_async(asyncio.wait_for(card(), timeout=10))
        assert [tag for tag, _, _ in log] == list(range(20))
    finally:
        limiter.reset()


# ── R2: blocking acquires ────────────────────────────────────────────────────

def test_twenty_five_queued_group_calls_all_run_in_submission_order(
    clock, run_async,
):
    """Row G / R2: none dropped, none reordered. Mutation: serve waiters
    from a set (or LIFO) and the recorded tags come back shuffled."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def submit_all():
        tasks = []
        for i in range(25):
            tasks.append(asyncio.ensure_future(
                _call(limiter, clock, log, chat_id=GROUP, tag=i)))
            await asyncio.sleep(0)  # each acquire arrives before the next
        await asyncio.gather(*tasks)

    try:
        run_async(asyncio.wait_for(submit_all(), timeout=20))
        assert [tag for tag, _, _ in log] == list(range(25))
    finally:
        limiter.reset()


def test_a_queued_blocking_acquire_is_visible_as_a_waiter_and_then_runs(
    limiter, clock, run_async,
):
    """R2's "never dropped", from the snapshot's side. Mutation: drop the
    waiter instead of queueing it and ``waiters`` never rises above 0."""
    log: list[tuple] = []
    seen: list[int] = []

    async def scenario():
        for _ in range(3):  # drain the burst
            await _call(limiter, clock, log)
        task = asyncio.ensure_future(_call(limiter, clock, log, tag="late"))
        await asyncio.sleep(0)
        seen.append(limiter.snapshot()["chats"][0]["waiters"])
        await task

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert seen == [1] and log[-1][0] == "late"


# ── R3: skip acquires ────────────────────────────────────────────────────────

def test_a_skip_acquire_with_one_token_left_is_refused_without_calling_telegram(
    limiter, clock, run_async,
):
    """Row C1: fewer than two tokens means the card yields. Mutation:
    let skip callers spend the last token and real content queues behind
    the spinner."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(2):  # 3 - 2 = 1 token left
            await _call(limiter, clock, log)
        with pytest.raises(FloodSkipped):
            await _call(limiter, clock, log, endpoint="editMessageText",
                        kind="skip", tag="card")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert [tag for tag, _, _ in log] == ["sendMessage", "sendMessage"]


def test_a_refusal_names_the_chat_and_the_endpoint_it_refused(
    limiter, clock, run_async,
):
    """``FloodSkipped.chat_id`` / ``.endpoint`` are contract: the card
    loop, the dashboard and the rich path all catch one exception class
    and have to be able to say WHAT was dropped, in a log line an operator
    reads at 3 a.m. Mutation: raise a bare ``FloodSkipped()`` and both
    attributes come back empty."""
    log: list[tuple] = []
    caught: list[FloodSkipped] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        try:
            await _call(limiter, clock, log, endpoint="editMessageText",
                        kind="skip", tag="card")
        except FloodSkipped as exc:
            caught.append(exc)

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert [(e.chat_id, e.endpoint) for e in caught] == \
        [(PRIVATE, "editMessageText")]


def test_the_token_a_skip_acquire_was_refused_is_still_there_for_an_answer(
    limiter, clock, run_async,
):
    """Row C1's second half: the refusal costs the chat nothing, and the
    blocking caller behind it runs in the same instant. Mutation: consume
    the token before refusing and the answer waits a second."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(2):
            await _call(limiter, clock, log)
        with pytest.raises(FloodSkipped):
            await _call(limiter, clock, log, kind="skip", tag="card")
        await _call(limiter, clock, log, tag="answer")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert log[-1] == ("answer", PRIVATE, clock.now)


def test_a_refused_skip_acquire_never_awaits_the_clock(limiter, clock, run_async):
    """R3's "<1 ms": a skip acquire must not wait at all. Mutation: fall
    back to the blocking path when short and the injected sleep records
    the wait."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        clock.sleeps.clear()
        with pytest.raises(FloodSkipped):
            await _call(limiter, clock, log, kind="skip")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert clock.sleeps == []


def test_a_refused_card_is_counted_as_skipped_and_not_as_a_call(
    limiter, clock, run_async,
):
    """The snapshot is the incident's post-mortem surface. Mutation: count
    a refusal as a call and the counters stop telling them apart."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        with pytest.raises(FloodSkipped):
            await _call(limiter, clock, log, kind="skip")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    chat = limiter.snapshot()["chats"][0]
    assert (chat["calls"], chat["skipped"]) == (3, 1)


def test_an_answer_lands_within_one_second_while_two_cards_are_refused(
    limiter, clock, run_async,
):
    """Row D: two streaming cards must never delay real content by more
    than one token's wait. Mutation: make card edits blocking and the
    answer queues behind them."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):  # the chat is at zero tokens
            await _call(limiter, clock, log)
        started = clock.now
        for tag in ("card-a", "card-b"):
            with pytest.raises(FloodSkipped):
                await _call(limiter, clock, log, kind="skip", tag=tag)
        await _call(limiter, clock, log, tag="answer")
        return clock.now - started

    waited = run_async(asyncio.wait_for(scenario(), timeout=10))
    assert waited <= 1.0 and log[-1][0] == "answer"


# ── R7 / U4: what is not metered by a chat bucket ────────────────────────────

def test_a_callback_answer_runs_instantly_while_the_chat_is_empty_and_deferred(
    limiter, clock, run_async,
):
    """Row H / R7: ``answerCallbackQuery`` carries no chat_id, so a jammed
    or deferred chat must not touch it. Mutation: resolve a chat for it
    anyway and every button tap waits out the flood."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        limiter.note_retry_after(PRIVATE, 5)
        started = clock.now
        await _call(limiter, clock, log, chat_id=None,
                    endpoint="answerCallbackQuery", tag="tap")
        return clock.now - started

    waited = run_async(asyncio.wait_for(scenario(), timeout=10))
    assert waited == 0.0 and log[-1][0] == "tap"


def test_a_chatless_call_is_never_skipped_even_when_it_asks_to_be(
    limiter, clock, run_async,
):
    """R7's second half: "nothing is ever skipped" without a chat.
    Mutation: honour ``kind=skip`` for an unresolvable chat and
    ``getMe``/``setMyCommands`` start vanishing under load."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        await _call(limiter, clock, log, chat_id=None, endpoint="getMe",
                    kind="skip", tag="getMe")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert log[-1][0] == "getMe"


def test_a_reaction_goes_out_while_the_chat_is_empty_and_deferred(
    limiter, clock, run_async,
):
    """Row N / §11 U4: the 🚨 give-up reaction is the one signal that must
    still reach the user when the chat is jammed. Mutation: budget
    reactions again and the reaction waits behind the flood."""
    log: list[tuple] = []

    async def scenario():
        for _ in range(3):
            await _call(limiter, clock, log)
        limiter.note_retry_after(PRIVATE, 5)
        started = clock.now
        await _call(limiter, clock, log, endpoint="setMessageReaction",
                    tag="reaction")
        return clock.now - started

    waited = run_async(asyncio.wait_for(scenario(), timeout=10))
    assert waited == 0.0 and log[-1][0] == "reaction"


def test_a_reaction_is_counted_per_chat_so_the_exemption_stays_observable(
    limiter, clock, run_async,
):
    """§11 U4 accepts a risk on the condition that it is measurable.
    Mutation: stop counting reactions and the next incident cannot show
    whether Telegram meters them."""
    log: list[tuple] = []
    run_async(asyncio.wait_for(
        _call(limiter, clock, log, endpoint="setMessageReaction"), timeout=10))
    assert limiter.snapshot()["chats"][0]["reactions"] == 1


def test_a_reaction_is_still_held_by_the_overall_bucket(clock, run_async):
    """§11 U4 exempts ``setMessageReaction`` from the CHAT budget only —
    R1 keeps it inside the 30/s overall one, because Telegram meters the
    bot as a whole whatever it thinks of reactions. Mutation: return early
    for reactions before the overall acquire and a burst of 🚨 can
    out-send the daemon's own global limit."""
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    log: list[tuple] = []

    async def scenario():
        for i in range(30):  # the overall bucket's entire burst, 30 chats
            await _call(limiter, clock, log, chat_id=2000 + i)
        started = clock.now
        await _call(limiter, clock, log, chat_id=PRIVATE, tag="reaction",
                    endpoint="setMessageReaction")
        return clock.now - started

    try:
        waited = run_async(asyncio.wait_for(scenario(), timeout=10))
        assert waited > 0.0 and log[-1][0] == "reaction"
    finally:
        limiter.reset()


def test_a_card_is_refused_instantly_while_its_chat_is_deferred(
    limiter, clock, run_async,
):
    """Error guessing: the whole point of a deferral is that NOTHING goes
    to that chat for ``retry_after`` seconds, and a card must learn that
    without waiting the deferral out inside the acquire (its loop would
    freeze for five seconds a tick). Mutation: treat a skip acquire like a
    blocking one once ``retry_until`` is armed and the injected sleep
    records the whole deferral."""
    log: list[tuple] = []
    limiter.note_retry_after(PRIVATE, 5)
    clock.sleeps.clear()

    async def scenario():
        with pytest.raises(FloodSkipped):
            await _call(limiter, clock, log, endpoint="editMessageText",
                        kind="skip", tag="card")

    run_async(asyncio.wait_for(scenario(), timeout=10))
    assert (log, clock.sleeps) == ([], [])


# ── R5: the backoff, as accounting ───────────────────────────────────────────

def test_four_small_429s_double_the_cadence_multiplier_and_cap_it_at_eight(
    limiter,
):
    """Row E4 / R5, on the exponent's boundary. Mutation: drop the cap and
    a fifth 429 would slow the card to a crawl."""
    seen = []
    for _ in range(4):
        limiter.note_retry_after(PRIVATE, 5)
        seen.append(limiter.cadence_multiplier(PRIVATE))
    assert seen == [2.0, 4.0, 8.0, 8.0]


def test_the_multiplier_halves_after_a_full_quiet_minute_and_not_a_moment_before(
    limiter, clock,
):
    """Row E3 with the boundary tested from both sides. Mutation: decay on
    every read and one 429 stops slowing anything down."""
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 59.9
    before = limiter.cadence_multiplier(PRIVATE)
    clock.now += 0.2
    assert (before, limiter.cadence_multiplier(PRIVATE)) == (2.0, 1.0)


def test_two_quiet_minutes_bring_a_quadrupled_chat_all_the_way_back_to_one(
    limiter, clock,
):
    """R5's "halves per quiet 60 s", twice, with the floor at 1.0.
    Mutation: decay past 1.0 and the cadence would speed up past its own
    rule."""
    limiter.note_retry_after(PRIVATE, 5)
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 180.0
    assert limiter.cadence_multiplier(PRIVATE) == 1.0


def test_an_unknown_chat_reports_no_backoff_at_all(limiter):
    """R5/R8's default. Mutation: return 0.0 and every interval collapses."""
    assert limiter.cadence_multiplier(-999999) == 1.0


def test_a_ban_sized_retry_after_is_not_accounted_as_a_backoff(limiter):
    """R6's boundary from the budget's side: above
    ``TELEGRAM_MAX_RETRY_AFTER`` the mute owns the chat and the backoff
    must not double as well. Mutation: drop the guard and a ban both mutes
    and slows the chat for an hour after it lifts."""
    limiter.note_retry_after(PRIVATE, config.TELEGRAM_MAX_RETRY_AFTER + 1)
    assert limiter.cadence_multiplier(PRIVATE) == 1.0


def test_a_retry_after_exactly_at_the_cap_is_still_a_rate_limit(limiter):
    """The other side of the same boundary (90 s is a rate limit, 91 is a
    ban). Mutation: use ``>=`` and the boundary silently becomes a ban."""
    limiter.note_retry_after(PRIVATE, config.TELEGRAM_MAX_RETRY_AFTER)
    assert limiter.cadence_multiplier(PRIVATE) == 2.0


def test_a_429_reported_for_an_unresolvable_chat_is_a_no_op(limiter):
    """entrypoints.md: ``note_retry_after`` no-ops without a chat.
    Mutation: create a phantom chat entry keyed by None."""
    limiter.note_retry_after(None, 5)
    assert limiter.snapshot()["chats"] == []


def test_one_warning_and_no_traceback_is_logged_per_small_429(limiter, caplog):
    """R5's log contract — the 0.7.10 incident's log was a traceback per
    tick. Mutation: log with ``exception()`` and this fails on
    ``exc_info``."""
    with caplog.at_level("WARNING", logger="aipager.bot.flood_budget"):
        limiter.note_retry_after(PRIVATE, 5)
    records = [r for r in caplog.records
               if r.name == "aipager.bot.flood_budget"]
    assert len(records) == 1 and records[0].exc_info is None


def test_no_call_reaches_a_deferred_chat_until_its_retry_after_elapses(
    limiter, clock, run_async,
):
    """R5's "zero calls for retry_after seconds", straight from the
    limiter. Mutation: ignore ``retry_until`` for blocking callers and the
    deferral becomes a retry storm."""
    log: list[tuple] = []
    limiter.note_retry_after(PRIVATE, 5)
    started = clock.now
    run_async(asyncio.wait_for(_call(limiter, clock, log), timeout=10))
    assert log[0][2] - started >= 5.0


def test_a_deferred_chat_does_not_defer_its_neighbour(limiter, clock, run_async):
    """R5 is per chat. Mutation: arm the deferral globally and one busy
    chat stops the whole daemon."""
    log: list[tuple] = []
    limiter.note_retry_after(PRIVATE, 5)
    started = clock.now
    run_async(asyncio.wait_for(
        _call(limiter, clock, log, chat_id=777777), timeout=10))
    assert log[0][2] == started


# ── R8 / rows J and P: the signal file ───────────────────────────────────────

def _backoff_file() -> Path:
    return Path(config.FLOOD_BACKOFF_FILE)


def test_a_backing_off_chat_publishes_its_multiplier_to_the_signal_file(limiter):
    """Row P / the status surface. Mutation: never write the file and
    ``aipager status`` cannot show a backoff at all."""
    limiter.note_retry_after(PRIVATE, 5)
    entry = json.loads(_backoff_file().read_text())["backoff"][0]
    assert (entry["chat_id"], entry["multiplier"]) == (PRIVATE, 2.0)


def test_the_signal_file_never_lands_on_the_mute_file(limiter):
    """Row P / §11 U2: two writers on one file was the rejected design;
    ``flood.py`` unlinks the mute file whenever no mute is active.
    Mutation: point the backoff writer at ``FLOOD_MUTE_FILE``."""
    limiter.note_retry_after(PRIVATE, 5)
    assert not Path(config.FLOOD_MUTE_FILE).exists()


def test_the_daemon_start_hook_clears_a_stale_backoff_file(limiter, run_async):
    """Rows J/P + R8: ``initialize()`` is what ``ExtBot.initialize`` calls
    at startup. Mutation: leave the file behind and a restart inherits a
    ×8 cadence nobody can clear."""
    limiter.note_retry_after(PRIVATE, 5)
    run_async(limiter.initialize())
    assert not _backoff_file().exists()


def test_the_daemon_stop_hook_clears_the_backoff_file(limiter, run_async):
    """Row P's other half. Mutation: skip the unlink on shutdown and
    ``aipager status`` reports a backoff for a daemon that is not
    running."""
    limiter.note_retry_after(PRIVATE, 5)
    run_async(limiter.shutdown())
    assert not _backoff_file().exists()


def test_the_signal_file_goes_away_once_the_chat_has_decayed_back_to_one(
    limiter, clock,
):
    """Row E3's second half / R8: a chat that has served its quiet minute
    is no longer backing off, so ``aipager status`` must stop reporting
    one. Mutation: leave the file behind and the CLI reports a backoff
    that no longer exists."""
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 120.0
    assert limiter.cadence_multiplier(PRIVATE) == 1.0
    assert not _backoff_file().exists()


def _published(path: Path) -> list:
    return json.loads(path.read_text())["backoff"]


def test_the_signal_file_is_not_rewritten_more_than_once_every_five_seconds(
    limiter, clock,
):
    """The write throttle, per the coordinator's ruling on
    tester-iter1-007: on change AND never more than once per 5 s. A chat
    that 429s twice inside the window costs ONE write, so a flood cannot
    turn into a write storm on the runtime directory. Mutation: drop the
    throttle and the file already reads x4 here."""
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 1.0
    limiter.note_retry_after(PRIVATE, 5)
    assert limiter.cadence_multiplier(PRIVATE) == 4.0
    assert _published(_backoff_file())[0]["multiplier"] == 2.0


def test_a_change_held_back_by_the_throttle_is_published_at_the_end_of_it(
    limiter, clock,
):
    """The other half of the same ruling: throttled is deferred, never
    dropped — the next event past the window publishes the state the file
    missed. Mutation: skip the pending write and `aipager status`
    permanently under-reports a chat that escalated inside one window."""
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 1.0
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 5.0
    limiter.sweep()
    assert _published(_backoff_file())[0]["multiplier"] == 4.0


def test_the_next_429_past_the_window_publishes_what_the_throttle_held(
    limiter, clock,
):
    """The same ruling through the path an operator actually hits: the
    file catches up on ORDINARY traffic, not only on the monitor's sweep.
    A throttle that needed the sweep to exist would under-report every
    chat between two ticks. Mutation: clear the pending state instead of
    holding it and the file stops at ×2 for ever."""
    limiter.note_retry_after(PRIVATE, 5)   # ×2 — written at once
    clock.now += 1.0
    limiter.note_retry_after(PRIVATE, 5)   # ×4 — inside the 5 s window
    clock.now += 5.0
    limiter.note_retry_after(PRIVATE, 5)   # ×8 — the window has rolled
    assert _published(_backoff_file())[0]["multiplier"] == 8.0


def test_a_sweep_republishes_a_decaying_chat_and_then_takes_the_file_down(
    limiter, clock,
):
    """``sweep()`` is what the session monitor ticks: with no traffic at
    all, the decay is invisible until something reads it, so the CLI would
    keep showing a stale multiplier for a quiet chat. Mutation: make the
    sweep a no-op and the signal file outlives the backoff it describes."""
    limiter.note_retry_after(PRIVATE, 5)
    limiter.note_retry_after(PRIVATE, 5)
    clock.now += 65.0
    limiter.sweep()
    published = _published(_backoff_file())[0]["multiplier"]
    clock.now += 120.0
    limiter.sweep()
    assert (published, _backoff_file().exists()) == (2.0, False)


def test_no_backoff_signal_is_written_by_a_process_that_is_not_the_daemon(
    clock, monkeypatch, tmp_path,
):
    """The signal file is the DAEMON's, and `aipager status`, the hook
    binary and any stray probe share its default path — on a box where the
    daemon is running they sit right next to its socket. A limiter that
    resolves its path late therefore publishes only while that socket is
    there. Mutation: drop the check and any process that reports a 429
    invents a backoff for the running daemon.

    Deterministic on any machine BECAUSE it pins the socket path itself:
    with no socket, nothing is written even though the 429 is accounted."""
    monkeypatch.setattr("aipager.config.SOCKET_PATH",
                        str(tmp_path / "no-daemon-here.sock"))
    lim = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    try:
        lim.note_retry_after(PRIVATE, 5)
        assert lim.cadence_multiplier(PRIVATE) == 2.0
        assert not _backoff_file().exists()
    finally:
        lim.reset()


def test_a_fresh_limiter_starts_every_chat_at_one_after_a_restart(
    limiter, clock, run_async,
):
    """Row J / R8: nothing survives the restart. Mutation: read the file
    back at construction and the backoff becomes persistent state."""
    limiter.note_retry_after(PRIVATE, 5)
    fresh = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    try:
        run_async(fresh.initialize())
        assert fresh.cadence_multiplier(PRIVATE) == 1.0
    finally:
        fresh.reset()


def test_no_session_field_carries_skip_or_budget_state_across_a_save():
    """R8's second half: ``card_skipped_since`` is deliberately absent from
    the persisted fields. Mutation: add it to ``_PERSIST_FIELDS`` and a
    restart starts the card mid-starvation."""
    from aipager import state
    from aipager.state import SessionRegistry, Status, TrackedSession

    assert TrackedSession(name="claude-x", label="x").card_skipped_since == 0.0
    reg = SessionRegistry()
    sess = reg.get_or_create("claude-jim")
    sess.label, sess.status = "jim", Status.BUSY
    sess.card_skipped_since = 1_000_000.0
    reg.save()
    assert "card_skipped_since" not in Path(state.SESSION_STATE_FILE).read_text()


def test_status_reads_a_backing_off_chat_and_renders_one_line_for_it(limiter):
    """The CLI half of row P, read from another process. Mutation: write a
    schema ``read_flood_backoffs`` cannot parse and status goes silent."""
    limiter.note_retry_after(PRIVATE, 5)
    backoffs = status.read_flood_backoffs()
    assert len(backoffs) == 1
    assert "×2" in status.flood_backoff_lines(backoffs)[0]


def test_status_never_infers_a_backoff_from_a_missing_or_broken_file(tmp_path):
    """Error guessing: a truncated write, or no daemon at all, must read
    as "no backoff", never as a crash. Mutation: let the parser raise and
    ``aipager status`` dies on a half-written file."""
    broken = tmp_path / "half-written.json"
    broken.write_text('{"backoff": [{"chat_id": 1, "mult')
    assert status.read_flood_backoffs(str(broken)) == []
    assert status.read_flood_backoffs(str(tmp_path / "absent.json")) == []


# ── R4: the cadence rule, as a pure function ─────────────────────────────────

@pytest.mark.parametrize("busy,is_group,expected", [
    (1, False, 1.32),    # row A: max(1.2, 1×1.0) × 1.1
    (2, False, 2.2),     # row B1
    (3, False, 3.3),     # row B2, before the session goes idle
    (0, False, 1.32),    # degenerate N, clamped to one session
    (1, True, 3.3),      # row B3: the group floor wins
    (2, True, 6.6),
])
def test_the_card_interval_follows_the_cadence_rule(busy, is_group, expected):
    """R4's formula at every boundary that matters. Mutation: drop the
    ``× N`` term and two sessions share one second again (the incident)."""
    assert card_interval(base=1.2, busy_sessions=busy,
                         is_group=is_group) == pytest.approx(expected)


def test_a_sub_floor_base_interval_changes_nothing_observable():
    """R9: an operator who sets ``STREAM_EDIT_INTERVAL=0.1`` still cannot
    outrun the chat's floor. Mutation: use ``base`` directly and the env
    var becomes a foot-gun."""
    assert card_interval(base=0.1, busy_sessions=1, is_group=False) == \
        pytest.approx(1.1)


def test_the_backoff_multiplies_the_card_interval(limiter):
    """Row E4's cadence half: a chat that 429'd slows its card by the same
    factor. Mutation: ignore ``backoff`` and the card keeps hammering a
    chat Telegram just refused."""
    assert card_interval(base=1.2, busy_sessions=1, is_group=False,
                         backoff=8.0) == pytest.approx(10.56)


@pytest.mark.parametrize("chat_id,expected", [
    (-1001234567890, True), (-100, True), (256113222, False),
    ("@channelname", True), (None, False),
])
def test_group_chats_are_recognised_by_id_shape(chat_id, expected):
    """Equivalence partitions of the chat-id domain. Mutation: treat a
    ``str`` id as private and a channel gets the 1/s private budget."""
    assert is_group_chat(chat_id) is expected


# ── row O: the Mini App sends through the same ExtBot ────────────────────────

def test_no_mini_app_module_builds_a_telegram_bot_of_its_own():
    """Row O / §11 D12: the Mini App's sends are budgeted for free only
    because they go through the daemon's own ExtBot. Mutation: construct a
    ``telegram.Bot`` in a miniapp module and its sends bypass the limiter.

    Streamed line by line: the suite runs under a 1 GiB address-space cap
    and must not hold whole files or ASTs."""
    from aipager import miniapp

    offenders = []
    for path in sorted(Path(miniapp.__file__).parent.rglob("*.py")):
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if "Bot(" in line and "telegram" in line.replace("Bot(", ""):
                    offenders.append(f"{path.name}:{lineno}")
                elif "telegram.Bot(" in line or "= Bot(" in line:
                    offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], offenders


# ── the operator surface: `aipager doctor` and `aipager status` ──────────────

def _bound_socket(monkeypatch, tmp_path):
    """A daemon that is reachable, so ``check_daemon`` gets past its own
    liveness probe and the only thing left to look at is the detail."""
    path = tmp_path / "aipager.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(path))
    return sock


def test_the_doctor_daemon_row_names_a_chat_that_is_backing_off(
    limiter, monkeypatch, tmp_path,
):
    """``docs/troubleshooting.md`` already tells operators that ``aipager
    doctor`` shows the backoff, so an unwired ``check_daemon`` makes the
    shipped docs promise behaviour that does not exist. Mutation: drop the
    ``read_flood_backoffs`` call from ``check_daemon`` and the row goes
    silent while the signal file says ×2."""
    from aipager import doctor

    sock = _bound_socket(monkeypatch, tmp_path)
    try:
        limiter.note_retry_after(PRIVATE, 5)
        row = doctor.check_daemon()
    finally:
        sock.close()
    assert any("×2" in line and str(PRIVATE) in line for line in row.detail), \
        row.detail


def test_a_backing_off_chat_never_turns_the_doctor_row_into_a_warning(
    limiter, monkeypatch, tmp_path,
):
    """The coordinator's ruling 2, second half: a backoff is normal
    operation, not a fault — only a MUTE warns. Mutation: raise the
    severity with the detail and every busy minute reads as a problem."""
    from aipager import doctor

    sock = _bound_socket(monkeypatch, tmp_path)
    try:
        limiter.note_retry_after(PRIVATE, 5)
        row = doctor.check_daemon()
    finally:
        sock.close()
    assert row.status == doctor.OK


def _status_ns(**kw):
    return argparse.Namespace(**kw)


def _quiet_status(monkeypatch):
    """``cmd_status`` without a daemon to interrogate: the same stubs
    ``tests/test_flood_mute.py`` uses for the mute's own CLI rows."""
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))


def test_status_json_carries_the_backoff_beside_the_mute(
    limiter, monkeypatch, capsys,
):
    """The documented ``--json`` contract: a NEW ``flood_backoff`` key,
    with ``flood_muted`` unchanged next to it — the two are different
    conditions and a script must be able to tell them apart. Mutation:
    reuse the ``flood_muted`` key and a rate limit reads as a ban."""
    _quiet_status(monkeypatch)
    limiter.note_retry_after(PRIVATE, 5)
    assert status.cmd_status(_status_ns(as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["flood_muted"],
            [(e["chat_id"], e["multiplier"]) for e in payload["flood_backoff"]]) \
        == ([], [(PRIVATE, 2.0)])


def test_status_prints_one_backoff_line_in_its_plain_renderer(
    limiter, monkeypatch, capsys,
):
    """The same state through the human renderer, which is what an
    operator in the middle of an incident actually reads. Mutation: render
    it only in ``--json`` and the CLI stays silent about a chat that is
    deliberately slowing down."""
    _quiet_status(monkeypatch)
    limiter.note_retry_after(PRIVATE, 5)
    status.cmd_status(_status_ns(as_json=False))
    assert "flood backoff ×2" in capsys.readouterr().out


def test_the_mute_module_never_mentions_the_backoff_file(limiter):
    """Row P / §11 U2, as a property of the code rather than of a git
    diff: the two signal files have ONE owner each. ``flood.py`` unlinks
    the mute file whenever no mute is active, which is exactly why the
    backoff could not share it. Mutation: let ``flood.py`` write or unlink
    ``FLOOD_BACKOFF_FILE`` and the two owners race — the rejected design.

    Read line by line, never held whole (1 GiB address-space cap)."""
    from aipager.bot import flood as flood_module

    offenders = []
    with open(flood_module.__file__, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if "FLOOD_BACKOFF" in line or "flood-backoff" in line:
                offenders.append(f"{lineno}: {line.strip()}")
    assert offenders == [], offenders
