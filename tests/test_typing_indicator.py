"""The "typing…" indicator, back and off-budget (roadmap 8.24).

0.7.11 (roadmap 8.21) removed ``sendChatAction`` from the busy-card loop
entirely — at card creation and on every tick, in every chat kind —
because the design had CHOSEN to count it against the per-chat send
budget, and at one call per tick per session that was half of the chat's
budget and the last thing standing between two cards in one DM and the
promised 2.2 s refresh.

Bounded probes against the real API on 2026-09-12 (design §12) showed the
premise was wrong in mechanism, not just in degree: driven into a genuine
``retry_after=10``, the chat refused every ``editMessageText`` for ten
seconds while ALL ELEVEN ``sendChatAction typing`` calls made inside that
window returned 200 — and the operator's phone showed "typing…" in the
chat header throughout. Telegram does not meter chat actions with
messages. So the indicator comes back:

* R1 — exempt from the per-chat budget in ``BudgetRateLimiter``, exactly
  like a reaction: the 30/s overall bucket only, never a chat token,
  never a group slot, never a wait on either, and a 429 on it recorded
  against nothing.
* R2 — refreshed once per ``TYPING_INDICATOR_INTERVAL`` per SESSION by a
  task of its own (``_animate_typing``), so no card path ever sends,
  awaits or paces it. Lit for a fresh card, because that task starts with
  the card's animation.
* R3 — no cadence state is touched: not ``last_tool_edit_at``, not
  ``card_skipped_since``, not the backoff, not the group window.
* R4 — a 429 on the action is skipped and logged once per chat per hour
  at INFO; nothing muted, nothing backed off, nothing raised.
* R5 — nothing while the session is IDLE/INTERACTIVE/GONE, nothing while
  the chat is flood-muted — re-checked at SEND time, not only at gate
  time.

The indicator's own task is what review iteration 1 asked for
(rev-iter1-001/003). The first version offered it from inside
``_animate_tick``, which meant the send could only land on the animator's
wake grid — realizing a 4.5 s interval as 5.28–6.6 s, past the 5 s expiry
Telegram gives a typing status — and meant ``send_busy`` awaited a chat
action, which is a card path: it is also ``_reanchor_busy_card``'s send,
so a round trip landed in front of the re-anchor's own edit while
``animate_lock`` was held.

Nothing here sleeps for longer than a few milliseconds and nothing patches
``asyncio.sleep`` or ``create_task`` through a module path
(``aipager.bot.animation.asyncio`` IS the global module; CLAUDE.md). The
limiter rows drive it on an injected clock; the task's exact period is
asserted on the virtual loop in
``tests/integration/per-chat-flood-budget/test_card_cadence_rule.py``.

Every test's docstring names the mutation that makes it fail.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot import animation
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import (
    BudgetRateLimiter,
    CHAT_ACTION_ENDPOINT,
    FloodSkipped,
)
from aipager.state import Status, TrackedSession


# The 8.21 rows below are about the BUDGET MECHANICS — the token bucket,
# the rolling window, the reserve, the deferral — at a known, fixed chat
# rate. 8.27 made that rate LEARNED, starting at `FLOOD_START_RATE` (half
# the ceiling), so leaving it to the default would silently double every
# expected interval here and turn these into rows about the starting
# allowance instead. Pinned to the ceiling so each row keeps measuring
# what it was written to measure; the earned rate has its own rows in
# `tests/integration/outbound-gate-and-earned-rate/test_earned_rate.py`.
_FIXED_RATE = config.TELEGRAM_PRIVATE_MAX_RATE

PRIVATE = 256113222   # what conftest's _pin_single_chat_config pins CHAT_ID to
GROUP = -1001234567890
BAN = 400.0           # past TELEGRAM_MAX_RETRY_AFTER: a real mute
TYPING = {"action": "typing"}


# ── harness ──────────────────────────────────────────────────────────────────

class FakeClock:
    """The one time seam for the limiter rows. ``sleep`` advances the
    clock and yields once."""

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

    The shared fixture abandons every loop it creates. Rows here leave
    limiter waiters and long-lived typing tasks behind, and the suite runs
    under a hard 1 GiB ``RLIMIT_AS`` — a leak here would fail an unrelated
    LATER test with "can't start new thread", not this file (roadmap 8.19,
    worked around the same way in ``tests/test_card_cadence.py``).
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
                        asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    return _run


@pytest.fixture(autouse=True)
def _forget_typing_429_log():
    """The once-an-hour log stamp is module-level (log-only) state, so a
    row that logs must not silence the next row's assertion."""
    animation._TYPING_429_LOGGED.clear()
    yield
    animation._TYPING_429_LOGGED.clear()


def _sess(label="jim", *, status=Status.BUSY, chat=PRIVATE, streaming=False,
          msg_id=10) -> TrackedSession:
    sess = TrackedSession(name=f"claude-{label}", label=label, status=status)
    sess.busy_msg_id = msg_id      # a @property: pushes a kind="busy" entry
    sess.busy_started_at = time.monotonic() - 60
    sess.scope_kind = "dm"
    sess.scope_chat_id = chat
    sess.stream_dirty = streaming
    return sess


def _bot(mk_bot, *sessions):
    bot = mk_bot()
    for sess in sessions:
        bot.registry._sessions[sess.name] = sess
    bot._app.bot.send_chat_action = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return bot


def _actions(bot) -> list[dict]:
    return [call.kwargs for call in bot._app.bot.send_chat_action.await_args_list]


def _typing_call(chat_id=None) -> dict:
    """The exact kwargs one indicator refresh carries.

    ``rate_limit_args`` joined the shape in 8.26: the bubble is declared
    ORNAMENT (R3) so minimal mode can suspend it — and, since 8.30, SKIP
    kind, because it is budgeted again as the chat's lowest ornament and
    must never wait. Kept in ONE helper so a change to the declaration is a
    one-line edit here rather than a hunt through a dozen literal dicts —
    and so the rows below keep asserting the WHOLE call rather than
    quietly loosening to a subset. (AMENDED by 8.30: ``"kind": "skip"``.)
    """
    return {"chat_id": PRIVATE if chat_id is None else chat_id, **TYPING,
            "rate_limit_args": {"kind": "skip", "class": "ornament"}}


def _stops_after(sess, sends: list, n: int):
    """A ``send_chat_action`` double that ends the typing loop after *n*
    refreshes, by taking the card away — the same liveness signal the real
    turn ending gives it. Keeps every loop row bounded and deterministic
    without patching ``asyncio.sleep``."""
    async def _record(**kwargs):
        sends.append(kwargs)
        if len(sends) >= n:
            sess.busy_msg_id = 0      # no live card -> the loop exits
    return AsyncMock(side_effect=_record)


# ── R1: the limiter exemption ────────────────────────────────────────────────

def _limiter(clock: FakeClock, **kw) -> BudgetRateLimiter:
    return BudgetRateLimiter(start_rate=_FIXED_RATE, clock=clock, sleep=clock.sleep, **kw)


def _recorder(clock: FakeClock):
    stamps: list[tuple[str, object, float]] = []

    async def _call(endpoint="?", chat_id=None):
        stamps.append((endpoint, chat_id, clock.now))
        return {"ok": True}

    _call.stamps = stamps  # type: ignore[attr-defined]
    return _call


async def _acquire(limiter, callback, *, endpoint="sendMessage", chat_id=PRIVATE,
                   kind=None):
    return await limiter.process_request(
        callback=callback, args=(),
        kwargs={"endpoint": endpoint, "chat_id": chat_id},
        endpoint=endpoint, data={"chat_id": chat_id},
        rate_limit_args=({"kind": kind} if kind else None),
    )


def _chat(snapshot: dict, chat_id) -> dict:
    for entry in snapshot["chats"]:
        if entry["chat_id"] == chat_id:
            return entry
    raise AssertionError(f"chat {chat_id} not in {snapshot}")


def test_the_typing_action_is_refused_while_the_chat_budget_is_empty(run_async):
    """R1, AMENDED by 8.30 R1/R2 (was
    ``test_the_typing_action_goes_out_while_the_chat_budget_is_empty``).
    The chat has no token left: the action is REFUSED, at once, and never
    reaches Telegram. 8.24 sent it anyway, off-budget; on 2026-09-23 the
    uncounted bubble earned the vm3 DM a 429 of its own before a straight
    7-hour ban.

    Mutation: put ``sendChatAction`` back in ``_CHAT_BUDGET_EXEMPT`` and it
    goes out here.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        # Drain the burst, then confirm the chat really is empty.
        for _ in range(3):
            await _acquire(limiter, call)
        assert _chat(limiter.snapshot(), PRIVATE)["tokens"] < 1.0
        started = clock.now
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT)
        assert clock.now == started, "the typing action WAITED on a chat token"

    run_async(_drive())
    assert [s[0] for s in call.stamps] == ["sendMessage"] * 3
    assert _chat(limiter.snapshot(), PRIVATE)["chat_actions"] == 0


def test_the_typing_action_spends_a_chat_token_and_needs_a_spare_one(run_async):
    """R1's other half, AMENDED by 8.30 R1 (was
    ``test_the_typing_action_never_consumes_a_chat_token``). The action
    SPENDS a token like any call, and it is admitted only with a full
    bucket — one token MORE than a card edit needs — so the second of two
    back-to-back actions is refused rather than taking what a card is about
    to spend.

    Mutation: acquire it with the card's ``_SKIP_RESERVE`` and the second
    action goes out; skip the take and the token count does not drop.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT)
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT)

    run_async(_drive())
    snap = _chat(limiter.snapshot(), PRIVATE)
    assert snap["tokens"] == pytest.approx(config.TELEGRAM_CHAT_BURST - 1)
    assert snap["chat_actions"] == 1
    assert snap["skipped"] == 1


def test_the_typing_action_spends_a_group_window_slot(run_async):
    """R3's group clause, AMENDED by 8.30 R1 (was
    ``test_the_typing_action_never_spends_a_group_window_slot``). Every
    chat-scoped call but a reaction counts in the rolling window now, the
    bubble included: a group's 20-per-60 s window loses one slot per
    admitted action. (The card is protected by the bubble's higher
    reserve and by the typing loop's fit check, not by the bubble being
    free.)

    Mutation: skip the window take on the typing path and
    ``sustained_free`` does not move.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, endpoint="sendMessage", chat_id=GROUP)
        free = _chat(limiter.snapshot(), GROUP)["sustained_free"]
        clock.now += 10.0                      # a full token bucket again
        await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT,
                       chat_id=GROUP)
        return free

    free_before = run_async(_drive())
    snap = _chat(limiter.snapshot(), GROUP)
    assert snap["kind"] == "group", "this row needs a group budget"
    assert snap["sustained_free"] == free_before - 1
    assert snap["chat_actions"] == 1


def test_the_typing_action_is_refused_while_the_chat_is_deferred_by_a_429(
        run_async):
    """R1 as the live probe ran it, AMENDED by 8.30 (was
    ``test_the_typing_action_goes_out_while_the_chat_is_deferred_by_a_429``).
    The chat is inside a ``retry_after`` window and every budgeted call is
    barred — the bubble now included, refused at once and never waiting.
    The probe proved typing is not blocked BY an edit 429; it did not
    prove typing is free, and on vm3 it was not.

    Mutation: exempt the action from ``retry_until`` again and it goes out
    here.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    limiter.note_retry_after(PRIVATE, 10.0)

    async def _drive():
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, kind="skip")
        started = clock.now
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT)
        assert clock.now == started

    run_async(_drive())
    assert call.stamps == []


def test_the_typing_action_still_meets_the_overall_bucket(run_async):
    """R1 stops at the CHAT budget; the 30/s overall bucket is Telegram's
    global limit and applies to everything. AMENDED by 8.30 R2: the action
    is skip-kind now, so an empty overall bucket REFUSES it rather than
    making it wait — "never blocks" — where 0.7.13 waited.

    Mutation: skip the overall check on the typing path and the action
    goes out while the daemon is over its global rate.
    """
    clock = FakeClock()
    limiter = _limiter(clock, overall_max_rate=1.0, overall_time_period=1.0)
    call = _recorder(clock)

    async def _drive():
        await _acquire(limiter, call, endpoint="sendMessage", chat_id=GROUP)
        started = clock.now
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT)
        assert clock.now == started, "the action WAITED on the 30/s bucket"

    run_async(_drive())
    assert [s[0] for s in call.stamps] == ["sendMessage"]


def test_a_typing_action_is_skip_kind_whatever_its_caller_declared(run_async):
    """AMENDED by 8.30 R2 (was
    ``test_a_typing_action_marked_skip_is_exempt_rather_than_skipped``):
    the action is the chat's lowest ornament and never worth a wait, so it
    takes the skip path even when its caller forgot to say so — a deferred
    chat refuses it at once, blocking-declared or not, and it never
    sleeps out the deferral.

    Mutation: honour a blocking ``kind`` for the action and this row waits
    ten seconds and then sends.
    """
    clock = FakeClock()
    limiter = _limiter(clock)
    call = _recorder(clock)
    limiter.note_retry_after(PRIVATE, 10.0)

    async def _drive():
        for kind in ("skip", None):
            with pytest.raises(FloodSkipped):
                await _acquire(limiter, call, endpoint=CHAT_ACTION_ENDPOINT,
                               kind=kind)

    run_async(_drive())
    assert call.stamps == []
    assert clock.now == 1_000_000.0, "the action waited"


def test_a_429_on_the_typing_action_never_backs_the_chat_off(run_async):
    """R4 in the limiter: a 429 on an off-budget call is no evidence about
    the chat's SEND budget, so it must not defer the chat or double its
    card cadence — that would slow every card for a call that never
    competed with them. It is re-raised for the animator to log and drop.

    Mutation: let the exempt path fall into ``note_retry_after`` and one
    refused ornament halves the card's refresh rate for a minute.
    """
    clock = FakeClock()
    limiter = _limiter(clock)

    async def _boom(**kw):
        raise RetryAfter(5)

    async def _drive():
        with pytest.raises(RetryAfter):
            await _acquire(limiter, _boom, endpoint=CHAT_ACTION_ENDPOINT)

    run_async(_drive())
    assert limiter.cadence_multiplier(PRIVATE) == 1.0
    snap = _chat(limiter.snapshot(), PRIVATE)
    assert snap["backoff"] == 1.0
    assert snap["retry_until_in"] == 0.0


def test_a_429_on_a_budgeted_call_still_backs_the_chat_off(run_async):
    """Anti-vacuity for the row above: ``note_429`` must be the only thing
    that changed. A 429 on an ordinary send still defers the chat and
    doubles its cadence, exactly as in 8.21.

    Mutation: default ``note_429`` to False and this is the row that
    notices.
    """
    clock = FakeClock()
    limiter = _limiter(clock)

    async def _boom(**kw):
        raise RetryAfter(5)

    async def _drive():
        with pytest.raises(FloodSkipped):
            await _acquire(limiter, _boom, kind="skip")

    run_async(_drive())
    assert limiter.cadence_multiplier(PRIVATE) == 2.0


# ── R2: the indicator's own task ─────────────────────────────────────────────

def test_the_typing_task_refreshes_until_the_card_is_gone(mk_bot, run_async,
                                                         monkeypatch):
    """R2: the bubble is a REFRESH, not a one-shot — Telegram clears a
    typing status after 5 s, so a turn that runs for minutes needs one
    call every interval for as long as it runs. The loop's exit is the
    card's own liveness rule, so a finished turn stops it.

    The elapsed time is asserted too, which is what pins the SLEEP rather
    than merely the loop: three refreshes are two intervals apart, so the
    run cannot be quicker than that. Without it, a loop that dropped its
    sleep would still satisfy every other assertion here — it would simply
    spin, and the only thing that would notice is the memory cap
    (a mutation run proved exactly that: SIGKILL, no test named).
    Deliberately a WALL-clock bound on a 20 ms interval, not a virtual
    one: the exact period is asserted on the virtual loop in
    ``tests/integration/per-chat-flood-budget/`` and this row only has to
    prove the loop waits at all.

    Mutation: ``return`` after the first send (or ``if`` instead of
    ``while``) and the bubble shows for the first five seconds of every
    turn only; drop the liveness condition and it refreshes for ever after
    the card is gone; drop the sleep and this returns in microseconds.
    """
    interval = 0.02
    monkeypatch.setattr(animation, "TYPING_INDICATOR_INTERVAL", interval)
    sess = _sess()
    bot = _bot(mk_bot, sess)
    sends: list[dict] = []
    bot._app.bot.send_chat_action = _stops_after(sess, sends, 3)

    started = time.monotonic()
    run_async(asyncio.wait_for(bot._animate_typing(sess), timeout=3.0))
    elapsed = time.monotonic() - started

    assert len(sends) == 3
    assert all(s == _typing_call() for s in sends), sends
    assert elapsed >= 2 * interval, f"the loop did not wait between refreshes ({elapsed:.4f}s)"


def test_the_typing_task_goes_dark_while_a_background_job_waits(
        mk_bot, run_async, monkeypatch):
    """R5 + the continuation case, in one row. A session sitting on an open
    background job is NOT working — the card shows a waiting frame — so the
    bubble must go dark. But the task must not exit either: the same card
    and the same task carry on into the ``<task-notification>``
    continuation, and nothing would restart them (no new card is sent), so
    an exiting task means no bubble for the rest of the job.

    Mutation: gate the LOOP on ``status is BUSY`` instead of gating the
    SEND, and the bubble never comes back for a continuation; drop the
    BUSY gate from ``_typing_chat`` and a waiting card claims to be
    working.
    """
    monkeypatch.setattr(animation, "TYPING_INDICATOR_INTERVAL", 0.001)
    sess = _sess()
    sess.active_subagents["a1"] = {"type": "Explore",
                                   "started_at": time.monotonic()}
    sess.status = Status.IDLE          # interim Stop, job still open
    assert sess.job_background_open(), "fixture must model an open job"
    bot = _bot(mk_bot, sess)
    sends: list[dict] = []

    async def _record(**kwargs):
        sends.append(kwargs)
        sess.busy_msg_id = 0

    bot._app.bot.send_chat_action = AsyncMock(side_effect=_record)

    async def _drive():
        task = asyncio.ensure_future(bot._animate_typing(sess))
        for _ in range(20):                 # let it wake several times
            await asyncio.sleep(0.001)
        assert sends == [], "a waiting card lit the bubble"
        assert not task.done(), "the task gave up on the continuation"
        sess.status = Status.BUSY           # the continuation turn starts
        await asyncio.wait_for(task, timeout=3.0)

    run_async(_drive())
    assert sends == [_typing_call()]


def test_no_card_path_sends_or_awaits_a_chat_action(mk_bot, run_async):
    """rev-iter1-001, as a regression row: **no card path may touch the
    chat action** — not the tick in any branch, and not ``send_busy``,
    which is also ``_reanchor_busy_card``'s send and therefore runs inside
    a tick while ``sess.animate_lock`` is held. An awaited action there put
    a Telegram round trip in front of the re-anchor's own edit, the old
    card's delete and ``track_message``.

    Mutation: send (or await) the action from ``send_busy`` or from either
    branch of ``_animate_tick`` and this names the path.
    """
    sess = _sess(streaming=True)
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=True)

    async def _drive():
        sess.last_tool_edit_at = 0.0
        assert await bot._animate_tick(sess, "Working", False) is True   # edit
        sess.last_tool_edit_at = time.monotonic()
        assert await bot._animate_tick(sess, "Working", False) is False  # debounce
        sess.last_tool_edit_at = 0.0     # due again, this time while waiting
        assert await bot._animate_tick(sess, "Working", True) is True    # waiting
        assert await bot.send_busy(sess) == 42                           # creation
        await asyncio.sleep(0)   # nothing spawned may sneak a send in either

    run_async(_drive())
    bot._app.bot.send_chat_action.assert_not_awaited()


def test_a_fresh_card_starts_the_typing_task(mk_bot, run_async):
    """R2's card-creation clause, as it works now: ``_send_busy_and_animate``
    sends the card and then starts the animation, and the typing task comes
    with it — so a new card lights its bubble immediately, without any card
    path awaiting the call.

    Mutation: drop ``_start_typing`` from ``_start_animation`` and no card
    ever lights a bubble; start it inside ``send_busy`` instead and the
    fresh-card path sends twice (once from there, once when
    ``_start_animation`` replaces the task a moment later).
    """
    sess = _sess(msg_id=None, status=Status.BUSY)
    bot = _bot(mk_bot, sess)

    async def _drive():
        await bot._send_busy_and_animate(sess)
        assert sess.typing_task is not None and not sess.typing_task.done()
        await asyncio.sleep(0)          # let the task's first refresh run
        await asyncio.sleep(0)
        bot._stop_animation(sess)       # tidy up before the loop closes

    run_async(_drive())
    assert sess.busy_msg_id == 42
    assert _actions(bot) == [_typing_call()], _actions(bot)


def test_stopping_the_animation_stops_the_bubble(mk_bot, run_async):
    """Every path that takes a busy card down — the turn ending, /stop, a
    kill, a reclaim — goes through ``_stop_animation``, so that is where
    the bubble is taken down too. A session that is no longer working must
    not keep claiming it is.

    Mutation: drop ``_stop_typing`` from ``_stop_animation`` and the task
    survives the turn, refreshing "typing…" every 4.5 s until the card's
    liveness rule happens to notice.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)

    async def _drive():
        bot._start_animation(sess)
        task = sess.typing_task
        assert task is not None and not task.done()
        bot._stop_animation(sess)
        await asyncio.sleep(0)
        return task

    task = run_async(_drive())
    assert task.cancelled() or task.done()
    assert sess.typing_task is None


def test_a_restart_replaces_the_typing_task_rather_than_adding_one(mk_bot,
                                                                  run_async):
    """The watchdog and the resume path both call ``_start_animation`` on a
    card that may already be ticking. Two typing tasks for one session
    would double its refresh rate for ever — and the second would outlive
    every ``_stop_typing``, since only one task can be held in
    ``sess.typing_task``.

    ``_start_typing`` is driven DIRECTLY here as well as through
    ``_start_animation``. Through that path the replacement is also
    covered by ``_stop_animation``, so a row that only ever went through
    it would pass with ``_start_typing``'s own guard deleted — the guard
    would be untested code that merely looks like one (CLAUDE.md), and a
    mutation run proved exactly that.

    Mutation: drop the ``_stop_typing`` at the top of ``_start_typing`` and
    the direct round below leaks the first task.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)

    async def _drive():
        bot._start_typing(sess)                 # directly: no _stop_animation
        first = sess.typing_task
        bot._start_typing(sess)
        second = sess.typing_task
        await asyncio.sleep(0)
        assert first is not second
        assert first.cancelled() or first.done(), "a second task was added"

        bot._start_animation(sess)              # and through the real caller
        third = sess.typing_task
        bot._start_animation(sess)
        await asyncio.sleep(0)
        bot._stop_animation(sess)
        return second, third

    second, third = run_async(_drive())
    assert second.cancelled() or second.done()
    assert third.cancelled() or third.done()
    assert sess.typing_task is None


def test_a_zero_interval_starts_no_task_at_all(mk_bot, run_async, monkeypatch):
    """``TYPING_INDICATOR_INTERVAL=0`` is the off switch — for an operator
    who does not want the bubble, and the escape hatch if Telegram ever
    starts metering chat actions after all. Nothing is scheduled, so it
    cannot cost even a wake.

    Mutation: treat 0 as a period (``asyncio.sleep(0)`` in a ``while``)
    and the off switch becomes a hot loop sending as fast as Telegram
    allows.
    """
    monkeypatch.setattr(animation, "TYPING_INDICATOR_INTERVAL", 0.0)
    sess = _sess()
    bot = _bot(mk_bot, sess)

    async def _drive():
        bot._start_animation(sess)
        assert sess.typing_task is None
        await asyncio.sleep(0)
        bot._stop_animation(sess)

    run_async(_drive())
    bot._app.bot.send_chat_action.assert_not_awaited()
    assert bot._typing_chat(sess) is None


# ── R3: no cadence state, no skip class ──────────────────────────────────────

def test_the_gate_touches_no_state_at_all(mk_bot):
    """R3. The card's cadence is decided by ``last_tool_edit_at`` and
    ``card_skipped_since``; the indicator's gate is a pure question and
    must write nothing — a bubble that bumped ``last_tool_edit_at`` would
    debounce the very next card edit.

    Mutation: stamp anything in ``_typing_chat`` (the first draft of 8.24
    stamped a ``last_typing_at`` there) and this row sees the write.
    """
    sess = _sess(streaming=True)
    bot = _bot(mk_bot, sess)
    sess.last_tool_edit_at = 111.0
    sess.card_skipped_since = 222.0
    sess.stream_last_rendered = "frozen"
    before = dict(vars(sess))

    assert bot._typing_chat(sess) == PRIVATE
    assert bot._typing_chat(sess) == PRIVATE   # idempotent: still due

    assert vars(sess) == before
    assert (sess.last_tool_edit_at, sess.card_skipped_since,
            sess.stream_last_rendered) == (111.0, 222.0, "frozen")


def test_the_indicator_passes_the_skip_kind_and_the_ornament_class(
        mk_bot, run_async):
    """Two things at once, and they are easy to confuse. AMENDED by 8.30
    R1/R2 (was
    ``test_the_indicator_passes_the_ornament_class_and_never_the_skip_kind``).

    ``kind`` says "may this be refused when the budget is momentarily
    short": the indicator carries ``{"kind": "skip"}`` again, because it
    is budgeted again and must never wait — 8.24's endpoint exemption,
    which made the kind moot, is gone.

    ``class`` says "how much is it worth": ORNAMENT, the most disposable
    thing this daemon sends, so minimal mode suspends it while answers
    keep flowing.

    Mutation: drop the skip kind, or the class, and this names the kwarg.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)

    run_async(bot._send_typing(sess, PRIVATE))

    kwargs = bot._app.bot.send_chat_action.await_args.kwargs
    assert kwargs == {"chat_id": PRIVATE, **TYPING,
                      "rate_limit_args": {"kind": "skip", "class": "ornament"}}


# ── R4: a 429 on the action itself ───────────────────────────────────────────

def test_a_429_on_the_indicator_is_skipped_and_logged_once_an_hour(
        mk_bot, run_async, caplog):
    """R4: one INFO line per chat per hour, the action dropped, nothing
    else disturbed — no mute, no backoff, no exception out of the task. At
    one refresh per 4.5 s an un-throttled log line would be its own flood.

    Mutation: drop the once-an-hour guard and the second refusal logs
    again; let the ``RetryAfter`` escape ``_send_typing`` and the task dies
    with "Task exception was never retrieved", taking the bubble with it
    for the rest of the turn.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    bot._app.bot.send_chat_action = AsyncMock(side_effect=RetryAfter(5))

    async def _drive():
        for _ in range(2):
            await bot._send_typing(sess, PRIVATE)

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(_drive())

    lines = [r for r in caplog.records if "typing indicator" in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    assert lines[0].levelno == logging.INFO
    assert "rate-limited" in lines[0].getMessage()
    assert not MUTE.is_muted(PRIVATE)


def test_a_ban_sized_refusal_of_the_indicator_reads_differently(
        mk_bot, run_async, caplog):
    """rev-iter1-005(b): a ``retry_after`` past ``TELEGRAM_MAX_RETRY_AFTER``
    is a ban, not a rate limit, and the limiter re-raises it untouched. The
    indicator still does not arm the mute from here (R4 — the mute belongs
    to the paths that carry real content), but ten minutes of refusal and
    five seconds of it must not read identically in the log.

    Mutation: log both the same and a chat-action-level ban is invisible.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    bot._app.bot.send_chat_action = AsyncMock(side_effect=RetryAfter(BAN))

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._send_typing(sess, PRIVATE))

    lines = [r for r in caplog.records if "typing indicator" in r.getMessage()]
    assert len(lines) == 1
    assert "BANNED" in lines[0].getMessage()
    assert not MUTE.is_muted(PRIVATE), "the indicator must never arm the mute"


def test_the_hourly_log_guard_lets_the_next_hour_through(mk_bot, run_async,
                                                         caplog):
    """The suppression is a window, not a mute: a chat still rate-limiting
    the indicator an hour later is worth one more line.

    Mutation: record the chat in a set instead of stamping it and the
    second hour is silent for ever.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    bot._app.bot.send_chat_action = AsyncMock(side_effect=RetryAfter(5))

    async def _drive():
        await bot._send_typing(sess, PRIVATE)
        for key in list(animation._TYPING_429_LOGGED):
            animation._TYPING_429_LOGGED[key] -= 3601.0
        await bot._send_typing(sess, PRIVATE)

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(_drive())

    assert len([r for r in caplog.records
                if "typing indicator" in r.getMessage()]) == 2


def test_one_chat_earns_one_line_however_its_id_is_spelled(mk_bot, run_async,
                                                           caplog):
    """``resolve_chat_id`` answers an ``int`` for a scoped session and the
    legacy ``str`` ``CHAT_ID`` for an unstamped one, so the same chat can
    reach here spelled both ways. ``flood._key`` is the existing
    normaliser and the mute already uses it.

    Mutation: key the log dict on the raw value and one chat earns two
    lines an hour.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    bot._app.bot.send_chat_action = AsyncMock(side_effect=RetryAfter(5))

    async def _drive():
        await bot._send_typing(sess, PRIVATE)
        await bot._send_typing(sess, str(PRIVATE))

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(_drive())

    assert len([r for r in caplog.records
                if "typing indicator" in r.getMessage()]) == 1


def test_any_other_failure_of_the_indicator_is_swallowed(mk_bot, run_async,
                                                         monkeypatch):
    """An ornament may not break anything. ``send_chat_action`` can fail for
    every reason a Telegram call can, and the task has to survive it — a
    task that dies on a transient network error takes the bubble away for
    the rest of the turn.

    Mutation: narrow the ``except`` to ``RetryAfter`` and the refresh loop
    dies on the first ``httpx`` hiccup.
    """
    monkeypatch.setattr(animation, "TYPING_INDICATOR_INTERVAL", 0.001)
    sess = _sess()
    bot = _bot(mk_bot, sess)
    calls: list[int] = []

    async def _boom(**kwargs):
        calls.append(1)
        if len(calls) >= 3:
            sess.busy_msg_id = 0
        raise RuntimeError("nope")

    bot._app.bot.send_chat_action = AsyncMock(side_effect=_boom)

    run_async(asyncio.wait_for(bot._animate_typing(sess), timeout=3.0))

    assert len(calls) == 3, "the loop died on the first failure"


# ── R5: never while idle, never into a mute ──────────────────────────────────

@pytest.mark.parametrize("status", [Status.IDLE, Status.INTERACTIVE,
                                    Status.GONE])
def test_no_bubble_when_the_session_is_not_busy(mk_bot, status):
    """R5: a "typing…" bubble means "Claude is working". A session waiting
    on a permission dialog, sitting on an open background job or gone is
    not, and the chat list must not claim otherwise.

    Mutation: drop the BUSY check in ``_typing_chat`` and every waiting
    card lights a phantom bubble.
    """
    sess = _sess(status=status)
    bot = _bot(mk_bot, sess)

    assert bot._typing_chat(sess) is None


def test_no_bubble_into_a_flood_muted_chat(mk_bot):
    """R5: the mute is a real ban (roadmap 8.17), and every call into one
    is a fresh violation that extends it. The indicator is the last thing
    worth spending a ban on.

    Mutation: drop the ``MUTE.is_muted`` check from ``_typing_chat`` and
    the daemon pokes a live ban every 4.5 s per session for as long as the
    turn runs.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    MUTE.mute(PRIVATE, BAN)

    assert bot._typing_chat(sess) is None


def test_a_mute_armed_after_the_gate_still_stops_the_send(mk_bot, run_async):
    """rev-iter1-005(a): the gate and the send are not the same instant —
    a concurrent answer or reply can arm the mute in between (that is
    exactly when a mute gets armed). Re-checked at send time, fail-closed.

    Mutation: check the mute only in ``_typing_chat`` and this row pokes a
    live ban.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    chat = bot._typing_chat(sess)
    assert chat == PRIVATE          # the gate said yes…
    MUTE.mute(PRIVATE, BAN)         # …and then the ban landed

    run_async(bot._send_typing(sess, chat))

    bot._app.bot.send_chat_action.assert_not_awaited()


def test_a_turn_that_ends_after_the_gate_still_stops_the_send(mk_bot,
                                                             run_async):
    """The same window, for the status: the turn can finish between the
    gate and the await, and a bubble that arrives after the answer is a
    session that looks busy when it is done.

    Mutation: check the status only in ``_typing_chat`` and a finished turn
    can still light the bubble.
    """
    sess = _sess()
    bot = _bot(mk_bot, sess)
    chat = bot._typing_chat(sess)
    sess.status = Status.IDLE

    run_async(bot._send_typing(sess, chat))

    bot._app.bot.send_chat_action.assert_not_awaited()


def test_no_bubble_without_a_resolvable_chat(mk_bot, monkeypatch):
    """An unstamped session on an unconfigured install resolves to the
    empty string, which PTB would send to the API as a chat id.

    Mutation: drop the falsy-chat check and the daemon posts
    ``sendChatAction`` with no chat id.
    """
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    sess = _sess(chat=0)
    bot = _bot(mk_bot, sess)

    assert bot._typing_chat(sess) is None


# ── the config surface ───────────────────────────────────────────────────────

def test_the_interval_is_read_from_the_environment_with_a_documented_default():
    """4.5 s, env-overridable. Telegram's contract is "the status is set
    for 5 seconds or less", so the refresh has to be under 5 s to keep the
    bubble lit at all — and asserting the SOURCE line, not the imported
    value, keeps this row honest for an operator who has the variable in
    their own ``config.env`` (the trap ``_pin_single_chat_config`` exists
    for, which kept the `test` workflow red for weeks).

    Mutation: raise the default past Telegram's 5 s expiry and the bubble
    blinks off between every refresh; drop the env read and an operator
    cannot turn it off.
    """
    src = Path(config.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("TYPING_INDICATOR_INTERVAL", "4.5")' in src
    assert config.TYPING_INDICATOR_INTERVAL < 5.0
    assert animation.TYPING_INDICATOR_INTERVAL == config.TYPING_INDICATOR_INTERVAL
