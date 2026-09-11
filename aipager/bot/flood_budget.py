"""Per-chat Telegram send budget, card cadence and 429 backoff (roadmap 8.21).

Every outbound call this daemon makes — PTB's and the rich-message
module's raw httpx POSTs alike — acquires here first. One chat gets one
budget: 1 call/s sustained with a burst of 3, plus no more than 20 in
any rolling 60 s when the chat is a group or channel, under the existing
30/s overall bucket.

Why it exists: python-telegram-bot's ``AIORateLimiter`` keys its per-chat
bucket on a NEGATIVE chat id (``_aioratelimiter.py:263``), so a private
DM was paced by nothing but the 30/s overall bucket. Two sessions
streaming their busy cards into one DM at 0.9 s each put ~2.2 edits/s
into it; Telegram answered with 2,021 small 429s in 14 hours and then a
5.4-hour ban (Mohamad's install, 2026-09-11). Because the limiter was
installed with ``max_retries=0``, each 429 was re-raised with a
traceback before PTB's own halt could arm, and every caller hit the same
window again on its next tick.

The three answers, all here:

* **A real per-chat bucket**, private chats included.
* **A skippable class of caller.** A busy-card edit or a typing
  indicator is worth making only if the chat can afford it now; it
  raises :class:`FloodSkipped` instead of queueing, and always leaves a
  token in reserve for an answer or a reply, which never skip.
* **A bounded deferral instead of a retry storm.** A small 429
  (``retry_after <= TELEGRAM_MAX_RETRY_AFTER``) bars the chat for exactly
  the time Telegram asked, retries a blocking caller once, and doubles
  that chat's card interval; one quiet minute halves it back. One
  WARNING, no traceback, nothing muted.

A ``retry_after`` PAST the cap is a ban, not a rate limit: it is
re-raised untouched so the 8.17 flood mute (``bot/flood.py``,
``rich_message._ban_if_excessive``) owns it exactly as it did in 0.7.10.
Reactions are exempt from the chat budget entirely (design §11 U4): the
🚨 in ``transport._send_with_retry`` fires precisely when a chat's queue
is jammed, and it is the one signal that still reaches the user.

Public API
----------
FloodSkipped                 -- raised instead of making a skip-kind call
TokenBucket                  -- continuous refill, injectable clock
SlidingWindow                -- N calls in any W seconds (the group rule)
ChatBudget                   -- one chat's buckets, deferral and counters
BudgetRateLimiter            -- the daemon's telegram.ext.BaseRateLimiter
card_interval(...)           -- the pure busy-card cadence rule
is_group_chat(chat_id)       -- negative int or str id => group/channel
clear_backoff_signal(path)   -- unlink the status signal file

Time is read ONLY through the injected ``clock`` (and the wall clock, for
the cross-process signal file): the whole case table runs on a fake clock
with no real sleeps, which ``aiolimiter`` cannot do because its bucket
reads the event loop's own clock (``leakybucket.py:110``).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import time
from pathlib import Path

from telegram.error import RetryAfter
from telegram.ext import BaseRateLimiter

from aipager.config import (
    CARD_CADENCE_FLOOR_GROUP,
    CARD_CADENCE_FLOOR_PRIVATE,
    CARD_CADENCE_MARGIN,
    FLOOD_BACKOFF_DECAY_SECONDS,
    FLOOD_BACKOFF_MAX,
    TELEGRAM_CHAT_BURST,
    TELEGRAM_GROUP_MAX_CALLS,
    TELEGRAM_GROUP_WINDOW,
    TELEGRAM_MAX_RETRY_AFTER,
    TELEGRAM_OVERALL_MAX_RATE,
    TELEGRAM_OVERALL_TIME_PERIOD,
    TELEGRAM_PRIVATE_MAX_RATE,
)

log = logging.getLogger(__name__)

# The Bot API method that is exempt from the per-chat budget (§11 U4).
# It still meets the 30/s overall bucket and is counted per chat, so a
# future incident can settle with evidence whether Telegram meters
# reactions into the same window as sends.
REACTION_ENDPOINT: str = "setMessageReaction"

# Tokens a chat must have before a SKIP-kind caller may spend one. The
# extra token is the reserve: an answer, a reply or any other blocking
# caller always finds something left, however many cards are ticking.
# Drop this to 1.0 and cards starve real content — that is the guard.
_SKIP_RESERVE: float = 2.0

# Floor on how often the backoff signal file is rewritten. It is a
# diagnostic for `aipager status` in ANOTHER process, not daemon state.
_SIGNAL_MIN_INTERVAL: float = 5.0

# Tolerance on a token comparison, in tokens. Refilling is
# `tokens + (now - stamp) * rate`, and for a rate that is not a binary
# fraction (20/60, say) that lands a few ULPs SHORT of a whole token
# after sleeping exactly `time_until`. The residual wait is
# then ~1e-15 s, which `now + wait` cannot even represent once the clock
# is in the millions of seconds a monotonic clock reports — so the
# acquire loop spins forever without advancing, wedging the event loop.
# One microsecond of refill is far below anything a 1-token/s budget can
# notice and far above the float noise.
_TOKEN_EPS: float = 1e-6

# The same tolerance for :class:`SlidingWindow`, in SECONDS. A waiter
# sleeps exactly `time_until()` and then expects the oldest stamp to have
# left the window; `stamp + period - now` followed by `now + wait` is not
# exact in binary, so without this the stamp can come back a hair YOUNGER
# than the period, the next wait is ~1e-10 s, the clock cannot advance by
# it and the acquire loop spins. One microsecond of slack on a 60 s window
# is 1/60,000,000 of the budget.
_WINDOW_EPS: float = 1e-6


class FloodSkipped(Exception):
    """Raised instead of making a skip-kind call when the chat's budget is
    short. Never raised for a blocking caller.

    Carries the resolved ``chat_id`` (may be ``None``) and the Bot API
    ``endpoint`` so a caller that logs it can say which chat and which
    call went unmade. Callers treat it as transient: the busy card
    returns ``False`` (never ``None``, which every caller reads as
    "message gone"), the transport seam returns
    :data:`~aipager.bot.transport.SKIPPED`, and the next trigger simply
    tries again.
    """

    def __init__(self, chat_id=None, endpoint: str = "") -> None:
        super().__init__(f"skipped {endpoint or 'call'} to chat {chat_id}")
        self.chat_id = chat_id
        self.endpoint = endpoint


class TokenBucket:
    """A continuously-refilling token bucket with an injectable clock.

    ``rate`` tokens are added per second up to ``capacity`` (the burst),
    and the bucket starts full. ``clock`` is the ONE time seam: nothing
    here calls ``time.monotonic()`` directly, so the whole case table
    runs on a fake clock without a single real sleep.
    """

    __slots__ = ("rate", "capacity", "_clock", "_tokens", "_stamp")

    def __init__(self, rate: float, capacity: float, clock=time.monotonic) -> None:
        self.rate: float = float(rate)
        self.capacity: float = float(capacity)
        self._clock = clock
        self._tokens: float = float(capacity)
        self._stamp: float = clock()

    def _refill(self, now: float) -> None:
        if now > self._stamp:
            self._tokens = min(
                self.capacity, self._tokens + (now - self._stamp) * self.rate,
            )
        self._stamp = now

    def tokens(self) -> float:
        """Tokens available right now, refilled to the clock and capped."""
        self._refill(self._clock())
        return self._tokens

    def take(self, n: float = 1.0) -> bool:
        """Consume *n* tokens if they are there. Synchronous, never waits."""
        if self.tokens() + _TOKEN_EPS < n:
            return False
        self._tokens = max(self._tokens - n, 0.0)
        return True

    def time_until(self, n: float = 1.0) -> float:
        """Seconds until *n* tokens exist; ``0.0`` when they already do."""
        have = self.tokens()
        if have + _TOKEN_EPS >= n:
            return 0.0
        if self.rate <= 0:
            return float("inf")
        return (n - have) / self.rate


class SlidingWindow:
    """At most ``limit`` calls in ANY ``period``-long window.

    Telegram's group rule is a ROLLING WINDOW, not a bucket, and the two
    are not interchangeable: a 20-token bucket refilling at 20/60 s starts
    full, so 25 calls paced by the 1/s chat bucket all land inside the
    first 22 seconds and the bucket never binds — 25 in the first minute
    against a limit of 20 (review iteration 1, rev-iter1-002). Keeping the
    stamps themselves is what makes "no more than 20 per minute" literally
    true: the 21st call waits until the oldest of the 20 is ``period`` old.

    Half-open by construction: a stamp exactly ``period`` seconds old has
    left the window, so twenty calls at t and twenty at t+60 is legal and
    ``[t, t+60)`` still holds exactly twenty.

    ``clock`` is the same injected seam :class:`TokenBucket` uses — nothing
    here reads the wall or monotonic clock directly.
    """

    __slots__ = ("limit", "period", "_clock", "_stamps")

    def __init__(self, limit: float, period: float, clock=time.monotonic) -> None:
        self.limit: int = max(int(limit), 0)
        self.period: float = float(period)
        self._clock = clock
        self._stamps: collections.deque = collections.deque()

    def _evict(self, now: float) -> None:
        """Drop every stamp that has aged out of the window."""
        cutoff = now - self.period + _WINDOW_EPS
        while self._stamps and self._stamps[0] <= cutoff:
            self._stamps.popleft()

    def used(self) -> int:
        """Calls made inside the current window."""
        self._evict(self._clock())
        return len(self._stamps)

    def free(self) -> int:
        """Calls that may still be made inside the current window."""
        return max(self.limit - self.used(), 0)

    def take(self, n: float = 1.0) -> bool:
        """Record *n* calls if the window has room. Never waits."""
        count = max(int(n), 1)
        if self.free() < count:
            return False
        now = self._clock()
        for _ in range(count):
            self._stamps.append(now)
        return True

    def time_until(self, n: float = 1.0) -> float:
        """Seconds until *n* more calls fit; ``0.0`` when they already do.

        That is when the ``n``-th oldest stamp still inside the window
        leaves it — which is exactly the wait a blocking caller owes.
        """
        count = max(int(n), 1)
        need = count - self.free()
        if need <= 0:
            return 0.0
        if need > len(self._stamps):
            return float("inf")
        return self._stamps[need - 1] + self.period - self._clock()


class ChatBudget:
    """Everything the limiter knows about one chat.

    ``chat`` is the 1/s-with-a-burst bucket every chat gets. ``group`` is
    the additional 20-calls-per-60 s ROLLING WINDOW, present only for
    groups and channels — not a second bucket, because a bucket's burst
    would let 25 calls into the first minute (see
    :class:`SlidingWindow`). ``retry_until`` bars the chat after a small
    429; ``backoff``
    is the multiplier the busy-card cadence reads, doubling per 429 and
    halving per quiet ``FLOOD_BACKOFF_DECAY_SECONDS``.

    ``waiters`` is an explicit FIFO of blocking tickets rather than an
    ``asyncio.Lock``: CPython's Lock happens to wake waiters in arrival
    order, but that is an implementation detail, not a contract, and
    "serve in arrival order" has to be a line of our own code for a
    mutation to be able to break it.

    Counters: ``calls`` counts every callback actually run for this chat,
    INCLUDING the budget-exempt reactions; ``reactions`` counts that
    exempt subset on its own; ``skipped`` counts refused skip acquires.
    """

    def __init__(
        self, chat_id, *, clock, chat_rate: float, chat_burst: float,
        group_max_calls: float, group_window: float, is_group: bool,
    ) -> None:
        self.chat_id = chat_id
        self.is_group: bool = is_group
        self.chat: TokenBucket = TokenBucket(chat_rate, chat_burst, clock=clock)
        self.group: SlidingWindow | None = (
            SlidingWindow(group_max_calls, group_window, clock=clock)
            if is_group else None
        )
        self.retry_until: float = 0.0
        self.backoff: float = 1.0
        self.last_429_at: float = 0.0
        self.waiters: collections.deque = collections.deque()
        self.calls: int = 0
        self.skipped: int = 0
        self.reactions: int = 0


def is_group_chat(chat_id) -> bool:
    """True when *chat_id* addresses a group, supergroup or channel.

    A negative numeric id, or any string id — a Telegram ``@name`` only
    ever addresses a channel or supergroup, never a private chat. ``None``
    is False: an unresolvable id gets the private (stricter) treatment.
    """
    if isinstance(chat_id, str):
        return True
    if isinstance(chat_id, int) and not isinstance(chat_id, bool):
        return chat_id < 0
    return False


def card_interval(
    *, base: float, busy_sessions: int, is_group: bool, backoff: float = 1.0,
) -> float:
    """Seconds between busy-card edits for one session (design §4.3).

    ``max(base, N * floor) * MARGIN * backoff``, where N is the number of
    BUSY sessions sharing the chat: the cards of a chat share the chat's
    budget rather than each assuming it owns one. Pure — no registry, no
    clock, no I/O — so both the animation loop's sleep and the tick's
    debounce can read it and cannot drift apart.

    ``busy_sessions <= 0`` is clamped to 1: a card ticking for a session
    the N predicate does not count (``_animate_busy`` keeps ticking
    through an open background job, which is wider than ``Status.BUSY``)
    must still be paced.
    """
    floor = CARD_CADENCE_FLOOR_GROUP if is_group else CARD_CADENCE_FLOOR_PRIVATE
    return (
        max(base, max(busy_sessions, 1) * floor)
        * CARD_CADENCE_MARGIN
        * max(backoff, 1.0)
    )


def clear_backoff_signal(path: str | None = None) -> None:
    """Unlink the backoff signal file. Idempotent, and never raises.

    The path is read late from ``config`` so the daemon, the tests and
    the conftest isolation fixture all agree on where it is. A failure to
    clean up a diagnostic file must never reach a send path.
    """
    from aipager import config

    try:
        Path(path or config.FLOOD_BACKOFF_FILE).unlink(missing_ok=True)
    except OSError:
        log.debug("could not unlink the flood-backoff signal file", exc_info=True)


def _kind_of(rate_limit_args) -> str:
    """``"skip"`` only for the exact ``{"kind": "skip"}`` convention.

    Anything else — ``None``, a missing key, PTB's own integer retry
    count, a shape we do not recognise — is blocking. Fail-open: an
    unrecognised caller must never have its message silently dropped.
    """
    if isinstance(rate_limit_args, dict) and rate_limit_args.get("kind") == "skip":
        return "skip"
    return "blocking"


def _retry_after_seconds(exc: RetryAfter) -> float:
    """Seconds out of a ``telegram.error.RetryAfter``.

    Prefers the private ``_retry_after`` timedelta — what PTB itself
    reads (``_aioratelimiter.py:285``) and the only form that does not
    emit a deprecation warning — then the public attribute, coerced.
    Defaults to 1.0 rather than 0 so a malformed error still costs the
    chat a pause.
    """
    for raw in (getattr(exc, "_retry_after", None), getattr(exc, "retry_after", None)):
        if raw is None:
            continue
        total = getattr(raw, "total_seconds", None)
        if callable(total):
            with contextlib.suppress(TypeError, ValueError):
                return float(total())
        with contextlib.suppress(TypeError, ValueError):
            return float(raw)
    return 1.0


class BudgetRateLimiter(BaseRateLimiter):
    """The daemon's one rate limiter, on both HTTP paths (R10).

    ``lifecycle._make_builder`` builds exactly one, hands it to PTB's
    ``ApplicationBuilder`` and to ``rich_message.set_rate_limiter`` — so
    the raw rich POSTs and PTB's calls draw on the same buckets instead
    of two budgets that drift.

    Replaces ``AIORateLimiter`` outright: none of PTB's retry loop
    survives, so ``max_retries`` goes with it. A ``retry_after`` past
    ``TELEGRAM_MAX_RETRY_AFTER`` is re-raised untouched — that is a ban,
    and the 8.17 mute owns it.
    """

    def __init__(
        self,
        *,
        overall_max_rate: float = TELEGRAM_OVERALL_MAX_RATE,
        overall_time_period: float = TELEGRAM_OVERALL_TIME_PERIOD,
        chat_max_rate: float = TELEGRAM_PRIVATE_MAX_RATE,
        chat_burst: float = TELEGRAM_CHAT_BURST,
        group_max_calls: float = TELEGRAM_GROUP_MAX_CALLS,
        group_window: float = TELEGRAM_GROUP_WINDOW,
        backoff_max: float = FLOOD_BACKOFF_MAX,
        backoff_decay_seconds: float = FLOOD_BACKOFF_DECAY_SECONDS,
        clock=time.monotonic,
        sleep=None,
        signal_path: str | None = None,
    ) -> None:
        self._clock = clock
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._overall = TokenBucket(
            overall_max_rate / overall_time_period, overall_max_rate, clock=clock,
        )
        self._chat_max_rate = float(chat_max_rate)
        self._chat_burst = float(chat_burst)
        self._group_max_calls = float(group_max_calls)
        self._group_window = float(group_window)
        self._backoff_max = float(backoff_max)
        self._backoff_decay_seconds = float(backoff_decay_seconds)
        self._signal_path = signal_path
        self._budgets: dict = {}
        self._last_signal_key: str | None = None
        self._last_signal_at: float = 0.0

    # ── lifecycle (telegram.ext.BaseRateLimiter) ─────────────────────────

    async def initialize(self) -> None:
        """Drop a previous daemon's backoff file. Nothing is restored: a
        restart starts every chat at ×1 (R8)."""
        clear_backoff_signal(self._signal_path)

    async def shutdown(self) -> None:
        """Take the backoff signal down with the daemon, so `aipager
        status` never reports a dead process's backoff."""
        clear_backoff_signal(self._signal_path)

    # ── chat resolution ──────────────────────────────────────────────────

    @staticmethod
    def _key(chat_id):
        """The dict key for *chat_id*: an int when it parses, else the
        string (``@name``). Mirrors ``flood._key`` so ``"123"`` and
        ``123`` land on ONE budget, as they land on one mute."""
        if chat_id is None:
            return None
        with contextlib.suppress(ValueError, TypeError):
            return int(chat_id)
        if isinstance(chat_id, str) and chat_id:
            return chat_id
        return None

    def _budget_for(self, chat_id):
        """The :class:`ChatBudget` for *chat_id*, created on first use.

        ``None`` for an unresolvable chat — such a call meets the overall
        bucket and nothing else, and is never skipped (R7). EVERY
        resolvable chat gets one, positive ids included: that is the bug
        PTB's limiter had.
        """
        if chat_id is None:
            return None
        budget = self._budgets.get(chat_id)
        if budget is None:
            budget = ChatBudget(
                chat_id,
                clock=self._clock,
                chat_rate=self._chat_max_rate,
                chat_burst=self._chat_burst,
                group_max_calls=self._group_max_calls,
                group_window=self._group_window,
                is_group=is_group_chat(chat_id),
            )
            self._budgets[chat_id] = budget
        return budget

    # ── backoff ──────────────────────────────────────────────────────────

    def _decay(self, budget: ChatBudget, now: float) -> None:
        """One halving per full quiet window since the last 429.

        Lazy — run on every read and at the top of every acquire — so
        there is no timer to leak. ``last_429_at`` advances by whole
        windows only, so partial progress toward the next halving is
        never lost by reading the value.
        """
        if budget.backoff <= 1.0 or budget.last_429_at <= 0.0:
            return
        steps = int((now - budget.last_429_at) // self._backoff_decay_seconds)
        if steps > 0:
            budget.backoff = max(budget.backoff / (2.0 ** steps), 1.0)
            budget.last_429_at += steps * self._backoff_decay_seconds

    def note_retry_after(self, chat_id, seconds: float) -> None:
        """Record a 429 Telegram reported for *chat_id* (the rich path's
        entry point; the PTB path routes its ``RetryAfter`` here too).

        Bars the chat for exactly the seconds Telegram asked, doubles its
        card cadence up to the ceiling, and refreshes the signal file.
        Exactly ONE warning, with no traceback: Telegram escalates on the
        count of violations, and 2,021 stack traces told the operator
        nothing 2,021 times.

        No-op when the chat cannot be resolved, or when ``seconds``
        exceeds ``TELEGRAM_MAX_RETRY_AFTER`` — that is a ban and the
        8.17 mute owns it, including its own log line (R6).
        """
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if seconds > TELEGRAM_MAX_RETRY_AFTER:
            return
        budget = self._budget_for(self._key(chat_id))
        if budget is None:
            return
        now = self._clock()
        self._decay(budget, now)
        budget.retry_until = max(budget.retry_until, now + max(seconds, 0.0))
        budget.backoff = min(budget.backoff * 2.0, self._backoff_max)
        budget.last_429_at = now
        log.warning(
            "flood: chat %s 429 retry_after=%ss → cadence ×%g",
            budget.chat_id, seconds, budget.backoff,
        )
        self._maybe_write_signal()

    def cadence_multiplier(self, chat_id) -> float:
        """This chat's current card-interval multiplier, ``>= 1.0``.

        ``1.0`` for a chat that has never seen a 429. Decays on read, and
        refreshes the signal file — the animator reads this every tick,
        which is what takes the file back down once a chat goes quiet.
        """
        key = self._key(chat_id)
        budget = self._budgets.get(key) if key is not None else None
        if budget is None:
            return 1.0
        self._decay(budget, self._clock())
        self._maybe_write_signal()
        return max(budget.backoff, 1.0)

    # ── acquire ──────────────────────────────────────────────────────────

    async def _wait_for_overall(self) -> None:
        """Block until the 30/s overall bucket has a token, and take it."""
        while True:
            wait = self._overall.time_until(1.0)
            if wait <= 0:
                self._overall.take(1.0)
                return
            await self._sleep(wait)

    def _acquire_skip(self, budget: ChatBudget, endpoint: str) -> None:
        """Spend one token, or raise :class:`FloodSkipped`. Synchronous —
        a skip caller must never await, or the "cheap when refused"
        property that lets a card be skipped at all is gone.

        Refuses while the chat is deferred by a 429, and whenever taking
        a token would leave fewer than :data:`_SKIP_RESERVE` behind: the
        last token belongs to whatever blocking caller comes next. In a
        group the same reserve is counted in SLOTS of the rolling window —
        the last two calls of the minute belong to real content, not to a
        card refresh.
        """
        now = self._clock()
        if (now < budget.retry_until
                or budget.chat.tokens() < _SKIP_RESERVE
                or (budget.group is not None
                    and budget.group.free() < _SKIP_RESERVE)
                or self._overall.tokens() < 1.0):
            budget.skipped += 1
            raise FloodSkipped(budget.chat_id, endpoint)
        budget.chat.take(1.0)
        if budget.group is not None:
            budget.group.take(1.0)
        self._overall.take(1.0)

    async def _acquire_blocking(self, budget: ChatBudget | None) -> None:
        """Wait until every limit that applies has room, then spend it.

        Three limits, whichever is furthest out: the chat's token bucket,
        the group's rolling 60 s window and the 30/s overall bucket (plus
        any 429 deferral). Never drops a request and never reorders one:
        each caller appends
        a ticket and only the ticket at the head of the deque waits on
        the clock, handing the queue on in its ``finally``. The final
        availability check and the consumption happen in ONE synchronous
        block, with no await between them — otherwise two waiters woken
        by the same refill both see the token.
        """
        if budget is None:
            await self._wait_for_overall()
            return
        ticket: asyncio.Future = asyncio.get_running_loop().create_future()
        budget.waiters.append(ticket)
        try:
            if budget.waiters[0] is not ticket:
                await ticket
            while True:
                now = self._clock()
                self._decay(budget, now)
                wait = max(
                    budget.retry_until - now,
                    budget.chat.time_until(1.0),
                    (budget.group.time_until(1.0)
                     if budget.group is not None else 0.0),
                    self._overall.time_until(1.0),
                )
                if wait <= 0:
                    budget.chat.take(1.0)
                    if budget.group is not None:
                        budget.group.take(1.0)
                    self._overall.take(1.0)
                    return
                await self._sleep(wait)
        finally:
            if budget.waiters and budget.waiters[0] is ticket:
                budget.waiters.popleft()
            else:
                # Cancelled before it ever reached the head.
                with contextlib.suppress(ValueError):
                    budget.waiters.remove(ticket)
            if budget.waiters and not budget.waiters[0].done():
                budget.waiters[0].set_result(None)

    # ── the BaseRateLimiter entry point ──────────────────────────────────

    async def process_request(
        self, callback, args, kwargs, endpoint, data, rate_limit_args,
    ):
        """Pace one Bot API call and return whatever *callback* returns.

        Positional order and parameter names mirror
        ``BaseRateLimiter.process_request`` so both keyword callers work:
        PTB's ``ExtBot._do_post`` and ``rich_message._post``.

        Raises :class:`FloodSkipped` when a skip-kind caller finds the
        budget short — the callback is not called. A ``RetryAfter`` past
        ``TELEGRAM_MAX_RETRY_AFTER`` propagates untouched; a small one is
        recorded, waited out, and the blocking caller's request is made
        exactly once more.
        """
        chat_id = self._key(data.get("chat_id") if isinstance(data, dict) else None)
        budget = self._budget_for(chat_id)
        if budget is not None:
            self._decay(budget, self._clock())

        if endpoint == REACTION_ENDPOINT:
            # §11 U4: reactions are exempt from the chat budget. They are
            # a separate bucket on Telegram's side, and `transport.py`'s
            # 🚨 give-up reaction fires exactly when this chat's queue is
            # jammed — budgeting it would gag the one signal that still
            # reaches the user (see transport.py:456-458). Counted, so a
            # future incident can show whether that is still true.
            if budget is not None:
                budget.reactions += 1
            await self._wait_for_overall()
            return await self._run(
                budget, callback, args, kwargs, endpoint, chat_id,
                kind="blocking", allow_retry=False,
            )

        kind = _kind_of(rate_limit_args)
        if kind == "skip" and budget is not None:
            self._acquire_skip(budget, endpoint)
        else:
            # No resolvable chat (answerCallbackQuery, getMe, …) meets the
            # overall bucket and nothing else, and is never skipped (R7).
            await self._acquire_blocking(budget)
        return await self._run(
            budget, callback, args, kwargs, endpoint, chat_id, kind=kind,
        )

    async def _run(
        self, budget, callback, args, kwargs, endpoint, chat_id,
        *, kind: str = "blocking", allow_retry: bool = True,
    ):
        """Run the callback whose budget has already been paid.

        A 429 that comes back anyway is the limiter's to absorb: PTB's
        own loop is gone, so nothing else will. Any other exception
        propagates untouched — ``BaseRateLimiter``'s contract says this
        method "should not handle any other exception raised by
        ``callback``".
        """
        if budget is not None:
            budget.calls += 1
        try:
            return await callback(*args, **kwargs)
        except RetryAfter as exc:
            seconds = _retry_after_seconds(exc)
            if seconds > TELEGRAM_MAX_RETRY_AFTER:
                # A ban. `transport._send_with_retry`'s give-up branch and
                # `rich_message._ban_if_excessive` own it: mute, 🚨, stop.
                # No backoff bump, no deferral, no log line here (R6).
                raise
            self.note_retry_after(chat_id, seconds)
            if kind == "skip":
                raise FloodSkipped(chat_id, endpoint) from exc
            if not allow_retry:
                raise
            await self._acquire_blocking(budget)
            return await self._run(
                budget, callback, args, kwargs, endpoint, chat_id,
                kind=kind, allow_retry=False,
            )

    # ── observability ────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """The whole budget, decayed to now, for tests and the status file.

        ``calls`` counts every callback run for the chat, reactions
        included; ``reactions`` is that exempt subset on its own.
        ``group_window_free`` is how many of the group's 20 calls are
        still unspent in the rolling 60 s window (``None`` for a private
        chat, which has no such window).
        """
        now = self._clock()
        chats = []
        for budget in self._budgets.values():
            self._decay(budget, now)
            chats.append({
                "chat_id": budget.chat_id,
                "kind": "group" if budget.is_group else "private",
                "tokens": budget.chat.tokens(),
                "group_window_free": (
                    budget.group.free() if budget.group is not None else None
                ),
                "backoff": max(budget.backoff, 1.0),
                "retry_until_in": max(budget.retry_until - now, 0.0),
                "calls": budget.calls,
                "skipped": budget.skipped,
                "reactions": budget.reactions,
                "waiters": len(budget.waiters),
            })
        return {"overall_tokens": self._overall.tokens(), "chats": chats}

    def reset(self) -> None:
        """Forget every chat and take the signal file down with them."""
        self._budgets.clear()
        self._last_signal_key = None
        self._last_signal_at = 0.0
        clear_backoff_signal(self._signal_path)

    def _maybe_write_signal(self) -> None:
        """Publish the chats that are backing off, for `aipager status`.

        Rewritten on change and at most once per :data:`_SIGNAL_MIN_INTERVAL`,
        and UNLINKED as soon as no chat is backing off — a stale multiplier
        is worse than none. ``last_429_at`` goes out on the WALL clock
        because the reader is a different process, where a monotonic value
        means nothing.

        Every failure is swallowed at debug: this is a diagnostic, and a
        full disk must never reach a send path (mirrors
        ``flood.FloodMute._write_signal``).
        """
        now = self._clock()
        backoff: list[dict] = []
        reactions: list[dict] = []
        for budget in self._budgets.values():
            self._decay(budget, now)
            if budget.backoff > 1.0:
                backoff.append({
                    "chat_id": budget.chat_id,
                    "multiplier": float(budget.backoff),
                    "last_429_at": time.time() - max(now - budget.last_429_at, 0.0),
                })
            if budget.reactions:
                reactions.append(
                    {"chat_id": budget.chat_id, "count": budget.reactions},
                )
        if not backoff:
            if self._last_signal_key is not None:
                clear_backoff_signal(self._signal_path)
                self._last_signal_key = None
                self._last_signal_at = 0.0
            return
        # Compare on the stable part only: the wall stamp moves on every
        # call, so including it would make "on change" mean "always".
        key = json.dumps(
            [[str(e["chat_id"]), e["multiplier"]] for e in backoff]
            + [[str(r["chat_id"]), r["count"]] for r in reactions],
        )
        if key == self._last_signal_key:
            return
        if self._last_signal_at and (now - self._last_signal_at) < _SIGNAL_MIN_INTERVAL:
            return
        from aipager import config

        path = Path(self._signal_path or config.FLOOD_BACKOFF_FILE)
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps({"backoff": backoff, "reactions": reactions}),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError:
            log.debug("could not write the flood-backoff signal file", exc_info=True)
            return
        self._last_signal_key = key
        self._last_signal_at = now


__all__ = [
    "BudgetRateLimiter",
    "ChatBudget",
    "FloodSkipped",
    "REACTION_ENDPOINT",
    "SlidingWindow",
    "TokenBucket",
    "card_interval",
    "clear_backoff_signal",
    "is_group_chat",
]
