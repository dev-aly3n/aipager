"""The per-chat budget itself (roadmap 8.21): buckets, FIFO, skip, backoff.

Rows C1, E3, E4, G, H, J, N and P of the design's case table. Everything
here drives ``BudgetRateLimiter`` directly, on an injected clock whose
``sleep`` advances that clock and yields once — no test in this file
sleeps for real, and none patches ``asyncio.sleep`` through a module path
(``aipager.bot.notify.asyncio`` IS the global module; CLAUDE.md).

Every test's docstring names the mutation that makes it fail.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot.flood_budget import (
    BudgetRateLimiter,
    FloodSkipped,
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
    """
    clock = FakeClock()
    limiter = _limiter(clock)
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


def test_a_group_is_additionally_capped_at_twenty_calls_a_minute(run_async):
    """Row G. A group gets the 1/s chat bucket AND a 20-per-60 s bucket,
    so once its burst is spent it settles at one call every 3 s — where
    the same traffic into a private chat settles at one per second.

    Mutation: drop the group bucket from ``ChatBudget`` and the group's
    tail paces exactly like the private one.
    """
    group = _sequential_stamps(run_async, -1001, 60)
    private = _sequential_stamps(run_async, 256113222, 60)
    # Steady state, the sustained rule: 20 calls span a full minute.
    assert group[-1] - group[-20] == pytest.approx(57.0)
    assert private[-1] - private[-20] == pytest.approx(19.0)
    # Still ordered, and still under the per-chat burst.
    assert group == sorted(group)
    assert _max_in_window(group, 1.0) <= 3, group


def test_a_group_acquire_never_spins_on_a_fractional_token(run_async):
    """Regression, found by this file: the group rate (20/60) is not a
    binary fraction, so refilling for exactly ``time_until`` lands a few
    ULPs SHORT of a whole token. The residual wait is then ~1e-15 s —
    which ``now + wait`` cannot represent once the clock reads the
    millions of seconds a real monotonic clock reports — so the acquire
    loop spins forever WITHOUT advancing and wedges the event loop.

    Mutation: drop ``_TOKEN_EPS`` from ``TokenBucket.time_until`` and the
    30th acquire never completes (the task is still pending when the
    pump gives up, so this fails rather than hanging).
    """
    clock = FakeClock()  # starts at 1e6, where the ULP gap really bites
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        async def _all():
            for _ in range(40):
                await _acquire(limiter, call, chat_id=-1001)

        task = asyncio.ensure_future(_all())
        for _ in range(20_000):
            if task.done():
                break
            await asyncio.sleep(0)
        if not task.done():
            task.cancel()
        return task.done() and task.exception() is None

    assert run_async(_drive()) is True, "the group acquire loop never settled"
    assert len(call.stamps) == 40


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
    one case the mute exists to own."""
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
    assert caplog.records == []


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
    exempt endpoint and the 🚨 that tells the user a message was dropped
    is itself dropped — at exactly the moment the chat is jammed."""
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
    """Row P, the other half: ``bot/flood.py`` is untouched by 8.21 and
    must stay that way — it is the 8.17 mute's sole owner, and its two
    unlink tests are what a shared file would break.

    Mutation: teach ``flood.py`` about the backoff and the mute's own
    signal-file tests start failing for reasons unrelated to their names.
    """
    from aipager.bot import flood

    source = Path(flood.__file__).read_text(encoding="utf-8")
    assert "FLOOD_BACKOFF" not in source
    assert "flood_budget" not in source
    assert "backoff" not in source.lower()


def test_the_backoff_file_never_points_at_the_real_runtime_dir(tmp_path):
    """A guard on the guard (G29). Mutation: remove the new
    ``monkeypatch.setattr`` from ``conftest._isolate_flood_mute`` and
    every test that arms a backoff writes beside the LIVE daemon's
    socket."""
    assert Path(config.FLOOD_BACKOFF_FILE).parent == tmp_path
    assert Path(config.FLOOD_MUTE_FILE).parent == tmp_path


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


def test_initialize_and_shutdown_both_clear_the_signal_file(tmp_path, run_async):
    """Mutation: make ``initialize``/``shutdown`` no-ops (what
    ``AIORateLimiter`` does) and a dead daemon's backoff outlives it."""
    clock = FakeClock()
    path = tmp_path / "backoff.json"
    limiter = _limiter(clock, signal_path=str(path))
    path.write_text('{"backoff": []}')
    run_async(limiter.initialize())
    assert not path.exists()
    path.write_text('{"backoff": []}')
    run_async(limiter.shutdown())
    assert not path.exists()


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
    assert group["group_tokens"] == pytest.approx(19.0)
    private = _chat(snap, 31)
    assert private["kind"] == "private"
    assert private["group_tokens"] is None
    assert private["waiters"] == 0


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
