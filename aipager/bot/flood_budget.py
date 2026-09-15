"""The one outbound gate: mute, priority, earned rate (8.21, 8.26-8.29).

Every outbound call this daemon makes — PTB's and the rich-message
module's raw httpx POSTs alike — acquires here first, so this module is
the single authority on whether a call leaves the process. Since 8.26
that includes whether the chat is BANNED: `process_request` checks
`flood.MUTE` before anything else and refuses with `FloodMuted`.

Why the gate moved here. Enforcement used to be 22 per-site checks across
five files, and `grep -c MUTE` on this file was 0 — the chokepoint every
call already passed through did not know bans existed. That approach
failed three times. On 2026-09-15 one forgotten site
(`animation.send_busy`) put a busy card into an active ban; the
escalation that followed ran retry_after 1283 -> 312 -> 34212 (9.5 h),
with 3, then 5, then 9 requests fired INTO each active ban and five
answers silently dropped. A new call site now inherits the gate instead
of having to remember it, and `tests/sweep_rules.py` fails the build for
one that does not.

One chat gets one budget: a token bucket whose rate is LEARNED (8.27) —
starting at `FLOOD_START_RATE`, climbing per quiet window, halving on a
429, dropping to `FLOOD_MIN_RATE` on a ban — plus a rolling sustained
window that now exists for EVERY chat kind, under the 30/s overall
bucket. `TELEGRAM_PRIVATE_MAX_RATE` is the ceiling that climb may not
pass, not the allowance it used to be: Telegram's real limit is a
function of the account's recent history, and the only way to know it is
to be told.

Callers declare a PRIORITY CLASS through `rate_limit_args` (8.26 R3), so
a chat under pressure sheds pixels rather than answers. An unclassified
call is ESSENTIAL: a forgotten classification must degrade to "never
dropped".

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
* **A skippable class of caller.** A busy-card edit is worth making only
  if the chat can afford it now; it raises :class:`FloodSkipped` instead
  of queueing, and always leaves a token in reserve for an answer or a
  reply, which never skip. (The "typing…" indicator used to be in this
  class. Since 8.24 it is not budgeted at all — see
  :data:`CHAT_ACTION_ENDPOINT`.)
* **A bounded deferral instead of a retry storm.** A small 429
  (``retry_after <= TELEGRAM_MAX_RETRY_AFTER``) bars the chat for exactly
  the time Telegram asked, retries a blocking caller once, and doubles
  that chat's card interval; one quiet minute halves it back. One
  WARNING, no traceback, nothing muted.

A ``retry_after`` PAST the cap is a ban, not a rate limit: it is
re-raised untouched so the 8.17 flood mute (``bot/flood.py``,
``rich_message._ban_if_excessive``) owns it exactly as it did in 0.7.10 —
but since 8.27 it also drops this chat's earned rate to the floor and
leaves a wall-clock stamp that survives a restart.

Reactions and the "typing…" chat action are exempt from the per-chat
BUDGET (design §11 U4, §12/8.24): Telegram meters them in a separate
bucket — live probes on 2026-09-12 caught chat actions answering 200 all
the way through a `retry_after` window that refused every edit into the
same chat — so charging them to the chat's budget only starved the cards.
THEY ARE NOT EXEMPT FROM THE MUTE (8.26 D-1). That distinction is the
whole of `_CHAT_BUDGET_EXEMPT`: exempt from PACING, never from BANS.

Public API
----------
CHAT_ACTION_ENDPOINT         -- the chat-action method, budget-exempt
FloodSkipped                 -- raised instead of making a skip-kind call
PRIORITY_ESSENTIAL/_ORNAMENT/_SIGNAL, rate_limit_args(...)
TokenBucket                  -- continuous refill, injectable clock
SlidingWindow                -- N calls in any W seconds (every chat now)
ChatBudget                   -- one chat's buckets, rate, bans, counters
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
import math
import os
import time
from pathlib import Path

from telegram.error import RetryAfter
from telegram.ext import BaseRateLimiter

from aipager.bot.flood import MUTE, FloodMuted
from aipager.config import (
    CARD_CADENCE_FLOOR_GROUP,
    CARD_CADENCE_FLOOR_PRIVATE,
    CARD_CADENCE_MARGIN,
    FLOOD_BACKOFF_DECAY_SECONDS,
    FLOOD_BACKOFF_MAX,
    FLOOD_MIN_RATE,
    FLOOD_MINIMAL_MODE_RATE_FLOOR,
    FLOOD_MUTE_MAX_SECONDS,
    FLOOD_RATE_INCREASE,
    FLOOD_RATE_RECOVERY_HOURS,
    FLOOD_START_RATE,
    FLOOD_SUCCESS_WINDOW_SECONDS,
    FLOOD_SUSTAINED_MAX,
    FLOOD_SUSTAINED_WINDOW,
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

# The second exempt method (§12, roadmap 8.24): the "typing…" chat
# action, and this one is exempt on MEASURED evidence rather than on
# inference. Bounded probes against the real API on 2026-09-12 drove one
# chat into a genuine `retry_after=10`; during that window every
# `editMessageText` was refused and all ELEVEN `sendChatAction typing`
# calls returned 200, with the bubble visible on the phone throughout.
# Chat actions are not in the per-chat message/edit bucket, so charging
# them to it — which 8.21 did, and then dropped the indicator to pay for
# the cards — takes a token from a card edit for a call Telegram never
# counted. Exempt like a reaction: the 30/s overall bucket only, never a
# chat token, never a wait on one. A 429 on it is NOT recorded against
# the chat either (see `_run`'s `note_429`).
CHAT_ACTION_ENDPOINT: str = "sendChatAction"

# Every method that skips the per-chat budget. Membership is checked
# BEFORE `_kind_of`, so a caller that also passes ``kind="skip"`` for one
# of these gets the exemption rather than the skip class — the reserve
# exists to protect real content from BUDGETED callers, and these do not
# spend the budget at all.
#
# EXEMPT MEANS EXEMPT FROM THE *BUDGET*, NEVER FROM THE *MUTE* (8.26 D-1).
# The mute gate in `process_request` sits ABOVE this branch on purpose: a
# request into an active ban is what escalated 1283 → 312 → 34212 on
# 2026-09-15, and the endpoint it targets is irrelevant to that
# escalation. Move the gate below this branch and a muted chat starts
# taking reactions and chat actions again — that is the mutation.
_CHAT_BUDGET_EXEMPT: frozenset = frozenset({REACTION_ENDPOINT,
                                           CHAT_ACTION_ENDPOINT})

# How long one ban stays in a chat's memory (8.29 R5 / rev-iter1-005).
# 24 hours, matching `config.FLOOD_MUTE_MAX_SECONDS`: within this window a
# chat's rate CEILING is halved, so "a ban today means a reduced start
# tomorrow" is a rule rather than a hope. Telegram's own memory is
# measured in hours; ours used to be measured in process lifetimes.
_BAN_MEMORY_SECONDS: float = 86400.0

# The fraction of the normal ceiling a chat with a ban in the last 24 h
# may climb to. Half: enough to keep one session's card animating, not
# enough to repeat yesterday's volume. Recovering FULLY six hours after a
# 9.5-hour ban is how the 2026-09-15 ladder was earned three times in one
# day.
_BANNED_CEILING_FRACTION: float = 0.5

# Tokens a chat must have before a SKIP-kind caller may spend one. The
# extra token is the reserve: an answer, a reply or any other blocking
# caller always finds something left, however many cards are ticking.
# Drop this to 1.0 and cards starve real content — that is the guard.
_SKIP_RESERVE: float = 2.0

# Floor on how often the backoff signal file is rewritten. It is a
# diagnostic for `aipager status` in ANOTHER process, not daemon state.
_SIGNAL_MIN_INTERVAL: float = 5.0

# Sentinel `_last_signal_key` value meaning "whatever is on disk is not
# ours". Never equal to a real key, which `_maybe_write_signal` builds
# with `json.dumps` of a list and so always starts with "[".
_SIGNAL_FORCE: str = "?force"

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

# ── priority classes (8.26 R3) ───────────────────────────────────────────
#
# Declared through the `rate_limit_args` dict every caller already has:
# `{"kind": "skip", "class": "ornament"}`. Build it with
# `rate_limit_args()`, never by hand.
#
# ESSENTIAL — an answer, a reply, a permission prompt. Never dropped,
#   never suspended, and it never waits behind an ORNAMENT: an ornament
#   acquires against `_SKIP_RESERVE` on the BLOCKING path too, where
#   before 8.26 only skip-kind callers respected it.
# ORNAMENT  — the busy card, the typing bubble, the pinned dashboard.
#   Refused with `FloodSkipped` in minimal mode; every caller already
#   treats that as "nothing sent, chat healthy, stamps untouched".
# SIGNAL    — reactions and the consumed-👍. Exempt from the per-chat
#   BUDGET (Telegram meters them elsewhere), never from the MUTE, and
#   never suspended by minimal mode.
#
# The card is ~95 % of outbound volume and the answer ~5 %, so under
# pressure this is the difference between losing pixels and losing work.
PRIORITY_ESSENTIAL: str = "essential"
PRIORITY_ORNAMENT: str = "ornament"
PRIORITY_SIGNAL: str = "signal"



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
        """Drop every stamp that has aged out of the window.

        Written as an AGE (``now - stamp``) rather than against a cutoff
        (``now - period``) so that it is the SAME expression
        :meth:`time_until` rounds its wait up against: the two then agree
        about whether a slot is free by construction, rather than by two
        roundings happening to land the same way. (No clock value has been
        found where the cutoff form actually differs — the difference of
        two nearby floats is exact — so this is a choice of formulation,
        not a guard with a mutation of its own.)
        """
        while self._stamps and (now - self._stamps[0]) >= self.period:
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

        Rounded UP until sleeping it really does age that stamp out.
        ``stamp + period`` is not exact at the magnitudes a monotonic
        clock reports: it can round DOWN by an ULP (~1.2e-10 s at 1e6 s),
        and a waiter that wakes there finds the stamp 59.999999999883585
        seconds old, asks for another 1.2e-10 s, and cannot advance the
        clock by it — the acquire loop spins without advancing and wedges
        the event loop. Rounding the WAIT up, rather than loosening the
        eviction, is what keeps "no more than ``limit`` in any ``period``"
        literally true: the twenty-first call is made a fraction of a
        nanosecond LATE, never early.
        """
        count = max(int(n), 1)
        need = count - self.free()
        if need <= 0:
            return 0.0
        if need > len(self._stamps):
            return float("inf")
        oldest = self._stamps[need - 1]
        now = self._clock()
        wait = oldest + self.period - now
        # Converges in one step (the deficit is at most an ULP of `now`);
        # bounded anyway, because a loop inside a rate limiter that cannot
        # prove its own termination is how a daemon wedges.
        for _ in range(4):
            short = self.period - ((now + wait) - oldest)
            if short <= 0:
                break
            wait += max(short, math.ulp(now + wait))
        return wait


class ChatBudget:
    """Everything the limiter knows about one chat.

    ``chat`` is the token bucket, whose ``rate`` is the chat's EARNED
    rate (8.27) rather than a constant: it starts at
    ``FLOOD_START_RATE``, climbs by ``FLOOD_RATE_INCREASE`` per quiet
    window, halves on a 429 and drops to ``FLOOD_MIN_RATE`` on a ban.
    ``rate_earned_at`` anchors that climb on the limiter's clock.

    ``window`` is the additional ROLLING WINDOW — not a second bucket,
    because a bucket's burst would let 25 calls into the first minute
    (see :class:`SlidingWindow`). It used to be called ``group`` and to
    exist only for groups and channels; since 8.27 EVERY chat has one.
    A private chat had no volume ceiling at all, so the 1/s bucket
    permitted 60 calls a minute indefinitely — which two BUSY sessions
    did for ~45 minutes before the 9.5-hour ban on 2026-09-15. Groups
    keep the stricter limit of the two.

    ``retry_until`` bars the chat after a small 429; ``backoff`` is the
    multiplier the busy-card CADENCE reads, doubling per 429 and halving
    per quiet ``FLOOD_BACKOFF_DECAY_SECONDS``. It and the earned rate are
    both kept on purpose: they have different consumers (the card's
    interval vs the chat's bucket) and different time scales (a minute vs
    hours).

    ``ban_stamps`` is the WALL-clock history of bans, capped at 20,
    newest kept — the hours-scale memory that survives a restart.
    ``ban_seen_until`` is how ``_sync_mute`` and ``_run`` avoid
    double-counting one ban from two entry points.

    ``waiters`` is an explicit FIFO of blocking tickets rather than an
    ``asyncio.Lock``: CPython's Lock happens to wake waiters in arrival
    order, but that is an implementation detail, not a contract, and
    "serve in arrival order" has to be a line of our own code for a
    mutation to be able to break it. Since 8.26 the order is FIFO within
    a priority class and priority across them.

    Counters: ``calls`` counts every callback actually run for this chat,
    INCLUDING the budget-exempt reactions and chat actions; ``reactions``
    and ``chat_actions`` count those exempt subsets on their own;
    ``skipped`` counts refused skip acquires; ``muted_refusals`` counts
    calls the mute gate refused; ``ornaments_suspended`` counts ornaments
    minimal mode refused.
    """

    #: Most bans remembered per chat. Bounded so a pathological history
    #: cannot grow the durable state file without limit; newest kept,
    #: because "how many bans TODAY" is the only question asked of it.
    MAX_BAN_STAMPS: int = 20

    def __init__(
        self, chat_id, *, clock, chat_rate: float, chat_burst: float,
        group_max_calls: float, group_window: float, is_group: bool,
        sustained_max: float = 30.0, sustained_window: float = 60.0,
    ) -> None:
        self.chat_id = chat_id
        self.is_group: bool = is_group
        self.chat: TokenBucket = TokenBucket(chat_rate, chat_burst, clock=clock)
        # The stricter of the two ceilings for a group; the plain
        # sustained cap for everything else.
        limit = (min(sustained_max, group_max_calls) if is_group
                 else sustained_max)
        period = group_window if is_group else sustained_window
        self.window: SlidingWindow = SlidingWindow(limit, period, clock=clock)
        self.retry_until: float = 0.0
        self.backoff: float = 1.0
        self.last_429_at: float = 0.0
        self.rate: float = float(chat_rate)
        self.rate_earned_at: float = clock()
        self.ban_stamps: list[float] = []
        self.ban_seen_until: float = 0.0
        # WALL-clock instant this chat's mute lifts (or lifted). The one
        # thing `_earn` needs and cannot recover later: while a mute holds
        # NOTHING reaches the limiter, so the first call after the lift
        # would otherwise measure its success window from before the ban
        # and credit every muted minute as quiet time (8.29 T1(b)/(c)).
        # Recorded whenever the limiter observes a live mute, and at the
        # instant a ban is armed; persisted, because a restart inside a
        # ban must not forget where the ban ends.
        self.muted_until: float = 0.0
        self.waiters: collections.deque = collections.deque()
        self.calls: int = 0
        self.skipped: int = 0
        self.reactions: int = 0
        self.chat_actions: int = 0
        self.muted_refusals: int = 0
        self.ornaments_suspended: int = 0

    def set_rate(self, new_rate: float, now: float) -> None:
        """Move the chat to *new_rate*, crediting the elapsed span first.

        The refill MUST happen before the rate changes. ``TokenBucket``
        accrues lazily as ``tokens + (now - stamp) * rate``, so writing
        the new rate first would re-price every second since the last
        touch at the new rate — retroactively granting tokens a chat
        never earned when the rate goes up, and confiscating tokens it
        did earn when it goes down. On a ban (1.0 -> 0.05) that
        difference is the whole penalty.

        A non-finite *new_rate* is REFUSED, not clamped (8.29 T2). NaN
        compares false against every bound, so an unchecked one survives
        the range check in :meth:`BudgetRateLimiter.restore`, and
        ``min(capacity, tokens + elapsed * NaN)`` then leaves the bucket
        permanently full — the chat's pacing silently disabled, which is
        precisely the failure this ship exists to end. The start rate is
        the conservative answer: untrusted input degrades to the default,
        never to "unlimited".
        """
        try:
            rate = float(new_rate)
        except (TypeError, ValueError):
            return
        if not math.isfinite(rate):
            log.warning(
                "flood: refusing a non-finite rate (%r) for chat %s — "
                "keeping %g calls/s", new_rate, self.chat_id, self.rate,
            )
            return
        self.chat._refill(now)
        self.rate = rate
        self.chat.rate = rate

    def note_ban_stamp(self, wall_now: float) -> None:
        """Record one ban, newest-kept and bounded."""
        self.ban_stamps.append(float(wall_now))
        if len(self.ban_stamps) > self.MAX_BAN_STAMPS:
            del self.ban_stamps[:-self.MAX_BAN_STAMPS]

    def bans_in_last_24h(self, wall_now: float) -> int:
        """How many bans this chat has taken in the last 24 hours.

        The durable memory R5 asks for, read two ways: ``status`` displays
        it (as ``bans_today``), and the limiter HALVES this chat's rate
        ceiling while it is non-zero, so "a ban today means a reduced
        start tomorrow" is literally true rather than an aspiration. It
        decays by itself — a stamp simply ages out of the window — which
        is why there is no counter to keep in step with the list.
        """
        return sum(1 for stamp in self.ban_stamps
                   if 0.0 <= wall_now - stamp <= _BAN_MEMORY_SECONDS)

    def bans_today(self, wall_now: float) -> int:
        """The display name for :meth:`bans_in_last_24h`; what
        ``snapshot()`` and ``aipager status`` report."""
        return self.bans_in_last_24h(wall_now)


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
    chat_rate: float | None = None, sustained_min_gap: float | None = None,
) -> float:
    """Seconds between busy-card edits for one session (design §4.3).

    ``max(base, N * floor, 1/chat_rate, sustained_min_gap) * MARGIN *
    backoff``, where N is the number of BUSY sessions sharing the chat:
    the cards of a chat share the chat's budget rather than each assuming
    it owns one. Pure — no registry, no clock, no I/O — so both the
    animation loop's sleep and the tick's debounce can read it and cannot
    drift apart.

    ``chat_rate`` and ``sustained_min_gap`` are the chat's EARNED pacing
    (8.27), read live from the limiter. Both default to ``None``, which
    reproduces the pre-8.27 output EXACTLY — that is deliberate, so every
    pure-function cadence row keeps its expected numbers and only the
    rows that drive the real animator change. Without them a chat whose
    earned rate has fallen would simply have every card edit refused;
    telling the cadence about the new pacing is what makes a slow chat
    animate slowly instead of failing loudly.

    ``busy_sessions <= 0`` is clamped to 1: a card ticking for a session
    the N predicate does not count (``_animate_busy`` keeps ticking
    through an open background job, which is wider than ``Status.BUSY``)
    must still be paced.
    """
    floor = CARD_CADENCE_FLOOR_GROUP if is_group else CARD_CADENCE_FLOOR_PRIVATE
    if chat_rate is not None and chat_rate > 0:
        floor = max(floor, 1.0 / chat_rate)
    if sustained_min_gap is not None:
        floor = max(floor, sustained_min_gap)
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


def _mark_state_dirty() -> None:
    """Tell the durable store something material changed. Never raises.

    Late import, and swallowed: ``flood_state`` imports this module, and a
    failure to schedule a diagnostic write must never reach a send path.
    """
    try:
        from aipager.bot import flood_state

        flood_state.mark_dirty()
    except Exception:  # pragma: no cover - defensive
        log.debug("could not mark flood state dirty", exc_info=True)


def _finite(value) -> float | None:
    """*value* as a finite float, or ``None`` if it is not one.

    Everything read from ``FLOOD_STATE_FILE`` goes through here (8.29 T2).
    Persisted state is UNTRUSTED INPUT: the file can be truncated by a
    crash mid-write, hand-edited, or carried across a clock jump, and
    ``json.loads`` happily returns ``float('nan')`` for the bare literal
    ``NaN`` that ``json.dumps`` itself writes. NaN then compares FALSE
    against every bound, so it survives a range check, and
    ``min(capacity, tokens + elapsed * NaN)`` leaves the token bucket
    permanently full — pacing silently disabled for that chat, for ever.
    Negative and absurd values were already clamped; only the non-finite
    ones slipped through.

    ``None`` means "this field was not readable", and every caller
    defaults it rather than guessing.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _window_gap(limit: float, period: float) -> float:
    """The average gap a rolling ``limit``-per-``period`` window implies.

    What the card cadence uses as a floor, so a chat spends its whole
    allowance EVENLY rather than in a burst that hits the ceiling and then
    stalls for the rest of the window — the shape Telegram penalises.
    """
    return period / limit if limit > 0 else 0.0


def _kind_of(rate_limit_args) -> str:
    """``"skip"`` only for the exact ``{"kind": "skip"}`` convention.

    Anything else — ``None``, a missing key, PTB's own integer retry
    count, a shape we do not recognise — is blocking. Fail-open: an
    unrecognised caller must never have its message silently dropped.
    """
    if isinstance(rate_limit_args, dict) and rate_limit_args.get("kind") == "skip":
        return "skip"
    return "blocking"


def _class_of(rate_limit_args) -> str:
    """The priority class a caller declared, defaulting to ESSENTIAL.

    Fail-open in the direction that matters (8.26 D-9): an UNCLASSIFIED
    call is ESSENTIAL, so a forgotten classification degrades to "never
    dropped" rather than "silently dropped". That is the whole thesis —
    shed pixels, never answers — and it is what keeps this diff bounded:
    the real inventory is 66 outbound sites, 23 of them inside one
    1726-line method, and only the ~10 ornaments and 2 signals need an
    edit. Invert this default and every site not yet reviewed becomes
    droppable.
    """
    if isinstance(rate_limit_args, dict):
        declared = rate_limit_args.get("class")
        if declared in (PRIORITY_ORNAMENT, PRIORITY_SIGNAL):
            return declared
    return PRIORITY_ESSENTIAL


def rate_limit_args(
    *, kind: str = "blocking", priority: str = PRIORITY_ESSENTIAL,
) -> dict | None:
    """Build the wire dict a caller passes as ``rate_limit_args=``.

    Returns ``None`` — not ``{}`` — for a plain blocking ESSENTIAL call:
    python-telegram-bot drops a FALSY ``rate_limit_args`` before the
    limiter ever sees it (``_extbot.py:335``), so ``{}`` and ``None`` are
    indistinguishable downstream and only ``None`` says so honestly.

    One builder so no call site hand-rolls the dict and quietly misspells
    a key into "unclassified" — which, by :func:`_class_of`, would be a
    silent demotion to ESSENTIAL rather than a visible error.
    """
    args: dict = {}
    if kind == "skip":
        args["kind"] = "skip"
    if priority != PRIORITY_ESSENTIAL:
        args["class"] = priority
    return args or None


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
        start_rate: float = FLOOD_START_RATE,
        rate_increase: float = FLOOD_RATE_INCREASE,
        min_rate: float = FLOOD_MIN_RATE,
        success_window: float = FLOOD_SUCCESS_WINDOW_SECONDS,
        recovery_hours: float = FLOOD_RATE_RECOVERY_HOURS,
        sustained_max: float = FLOOD_SUSTAINED_MAX,
        sustained_window: float = FLOOD_SUSTAINED_WINDOW,
        minimal_floor: float = FLOOD_MINIMAL_MODE_RATE_FLOOR,
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
        # The earned rate (8.27). `_chat_max_rate` above is the CEILING
        # this climbs toward, not the allowance it used to be.
        self._start_rate = float(start_rate)
        self._rate_increase = float(rate_increase)
        self._min_rate = float(min_rate)
        self._success_window = float(success_window)
        self._recovery_hours = float(recovery_hours)
        self._sustained_max = float(sustained_max)
        self._sustained_window = float(sustained_window)
        self._minimal_floor = float(minimal_floor)
        self._signal_path = signal_path
        # Only the DAEMON may write beside the daemon's socket. An
        # explicit path means the caller owns it (tests, tools);
        # `initialize()` — which python-telegram-bot awaits from
        # `ExtBot.initialize`, i.e. only in a real daemon — arms the
        # default one. See `_maybe_write_signal`.
        self._signal_armed: bool = signal_path is not None
        self._budgets: dict = {}
        self._last_signal_key: str | None = None
        self._last_signal_at: float = 0.0

    # ── lifecycle (telegram.ext.BaseRateLimiter) ─────────────────────────

    async def initialize(self) -> None:
        """Arm the signal writer and publish the state just loaded.

        8.28 REVERSES WHAT THIS USED TO DO. It said: "Nothing is restored:
        a restart starts every chat at ×1 (R8)" — and it unlinked the
        backoff file to make that true. That was the defect: a daemon
        restarted during a penalty regime came back at full speed, knowing
        nothing, and on 2026-09-15 its own startup notice was the first
        request into a ban it had just forgotten. ``flood_state.load()``
        now runs before this, so the right behaviour is to REFRESH the
        signal to match the restored state rather than to clear it.

        Arming the signal writer: ``ExtBot.initialize`` awaits this, and
        nothing else does, so reaching it is what proves this limiter
        belongs to a running daemon rather than to a `python -c` probe
        pointed at the live runtime directory (`_maybe_write_signal`).
        """
        self._signal_armed = True
        # Force past `_maybe_write_signal`'s "on change" throttle. That
        # throttle compares against `_last_signal_key`, which describes
        # what THIS process last wrote — and this process has written
        # nothing, while the file on disk is a previous daemon's. A
        # sentinel that can never equal a real key (which is always a JSON
        # array) makes both branches fire: publish when there is something
        # to publish, unlink when there is not.
        self._last_signal_key = _SIGNAL_FORCE
        self._last_signal_at = 0.0
        self._maybe_write_signal()

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
                # A chat we have never seen starts at the EARNED start
                # rate, not at the ceiling (8.27). `_chat_max_rate` is
                # now the ceiling `_earn` climbs toward.
                chat_rate=self._start_rate,
                chat_burst=self._chat_burst,
                group_max_calls=self._group_max_calls,
                group_window=self._group_window,
                is_group=is_group_chat(chat_id),
                sustained_max=self._sustained_max,
                sustained_window=self._sustained_window,
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

    # ── the earned rate (8.27, AIMD) ─────────────────────────────────────

    def _success_window_for(self, budget: ChatBudget, wall_now: float) -> float:
        """How long one quiet window is for this chat, right now.

        TWO REGIMES, because a 429 and a ban are evidence about different
        time scales (P-2):

        * after a **429**: ``FLOOD_SUCCESS_WINDOW_SECONDS`` (60 s), so a
          chat that merely went too fast for a moment recovers in minutes;
        * within ``FLOOD_RATE_RECOVERY_HOURS`` of a **ban LIFTING**: the
          same ten steps stretched over those hours (2160 s per step),
          because a 9.5-hour ban is not information about the next minute.

        The arithmetic is what settles it: +0.1 per 60 s climbs MIN (0.05)
        to the ceiling (1.0) in 9.5 MINUTES. Using that after a ban would
        be a memory shorter than the incident it is supposed to remember —
        defect D3 restated.
        """
        if not budget.ban_stamps:
            return self._success_window
        # From the END of the penalty, not its start (8.29 T1(c)). A ban
        # is armed for hours: measured from the arming, the slow regime of
        # a 9.5-HOUR ban has entirely expired by the time that ban lifts,
        # so the chat leaves it on the 60 s regime and climbs a whole step
        # a minute later — the escalation this ship exists to end, one
        # layer down. `muted_until` is the lift instant; `ban_stamps` is
        # all there is for a ban recorded without a mute.
        recent = max([*budget.ban_stamps, budget.muted_until])
        if wall_now - recent > self._recovery_hours * 3600.0:
            return self._success_window
        span = max(self._chat_max_rate - self._min_rate, 0.0)
        steps = max(math.ceil(span / self._rate_increase), 1)
        return (self._recovery_hours * 3600.0) / steps

    def max_rate_for(self, budget: ChatBudget, wall_now: float) -> float:
        """The ceiling THIS chat may climb to, right now (R5).

        ``TELEGRAM_PRIVATE_MAX_RATE`` for a chat with a clean day, HALF of
        it while it has a ban in the last 24 hours (:data:`_BAN_MEMORY_SECONDS`),
        decaying out by itself as the stamp ages past the window.

        This is "a ban today means a reduced start tomorrow", made
        arithmetic. Without it the post-ban regime alone returns a chat to
        the full ceiling six hours after a 9.5-hour ban — a memory shorter
        than the incident it is supposed to remember, and the shape that
        earned three bans in one day on 2026-09-15.

        Never below ``FLOOD_MIN_RATE``: a ceiling under the floor would
        invert the two and pin a chat at a rate it can never leave.
        """
        if not budget.ban_stamps:
            return self._chat_max_rate
        if budget.bans_in_last_24h(wall_now) <= 0:
            return self._chat_max_rate
        return max(self._chat_max_rate * _BANNED_CEILING_FRACTION,
                   self._min_rate)

    def _earn_floor(self, budget: ChatBudget, now: float) -> float:
        """The earliest instant a success window may be measured from —
        the moment the chat's most recent ban LIFTED (8.29 T1(b)/(c)).

        A muted interval is not quiet time. It is the interval in which
        every send was refused, by us, because Telegram had banned the
        chat; crediting it as success is crediting the ban itself. And
        because nothing reaches ``process_request`` while a mute holds,
        the freeze in :meth:`_earn` alone never runs during the ban — the
        FIRST call after the lift is what evaluates the whole span, and it
        would see one unbroken quiet window per minute of the ban.
        Measured before this existed: a chat entered a 1283 s ban at 0.5
        calls/s and came out at 1.0, the ceiling.

        So the anchor is floored at the lift instant. Two consequences,
        both wanted:

        * **(b)** the muted interval is EXCLUDED from the success window
          rather than merely not counted twice; and
        * **(c)** the first window after a lift is a FULL one, measured
          from the lift, because whatever credit had accrued before the
          ban is discarded rather than banked.

        ``muted_until`` is a WALL-clock deadline (it is the mute's own),
        so it is rebased onto this limiter's clock with
        :meth:`mono_equiv`, which reports a deadline still in the FUTURE
        as "now" — exactly right: while the ban runs, nothing may be
        earned from any instant at all.

        It is the MUTED interval that is excluded, not merely a recorded
        ``retry_after``: a ban the daemon knows about but is not muted for
        is a rate event like any other, and freezing on it would stop a
        healthy chat's climb for hours over a ban that is not in force.
        """
        if budget.muted_until <= 0.0:
            return 0.0
        return self.mono_equiv(budget.muted_until)

    def _earn(self, budget: ChatBudget, now: float) -> None:
        """Credit whole quiet windows since the anchor. Lazy, like
        :meth:`_decay` — no timer to leak, and no partial progress lost by
        reading the value, because the anchor advances by WHOLE windows.

        Frozen while the chat is muted: climbing through a ban is
        precisely learning nothing, which is the defect this replaces.
        The anchor is still moved to ``now`` so the mute does not bank a
        pile of windows to be cashed the moment it lifts — and
        :meth:`_earn_floor` makes that true even for the muted minutes in
        which this method was never called at all, which is every one of
        them (8.29 T1).
        """
        if MUTE.is_muted(budget.chat_id):
            self._note_mute_deadline(budget)
            budget.rate_earned_at = now
            return
        # The ban is over, but the interval it covered is still not quiet
        # time: never measure a success window from before the lift.
        floor = self._earn_floor(budget, now)
        if floor > budget.rate_earned_at:
            budget.rate_earned_at = floor
        ceiling = self.max_rate_for(budget, time.time())
        if budget.rate >= ceiling:
            budget.rate_earned_at = now
            return
        window = self._success_window_for(budget, time.time())
        if window <= 0:
            return
        steps = int((now - budget.rate_earned_at) // window)
        if steps <= 0:
            return
        budget.set_rate(
            min(budget.rate + steps * self._rate_increase, ceiling),
            now,
        )
        budget.rate_earned_at += steps * window

    def _note_mute_deadline(self, budget: ChatBudget) -> None:
        """Remember when this chat's mute lifts, while it is still known.

        ``flood.MUTE.is_muted`` is a DESTRUCTIVE read — the first call
        after the deadline forgets the entry — so the lift instant is only
        knowable while the mute holds. Every read path that touches a
        budget runs this, and so does the arming point, which is what
        makes ``_earn_floor`` work for a ban that came and went with no
        call in between: the case that is not the exception but the rule,
        because the whole point of a mute is that nothing is sent.
        """
        remaining = MUTE.remaining(budget.chat_id)
        if remaining > 0.0:
            budget.muted_until = max(budget.muted_until,
                                     time.time() + remaining)

    def _sync_mute(self, budget: ChatBudget, now: float) -> None:
        """Notice a ban armed on the RICH path, where ``_run`` never sees it.

        A rich-path ban arrives as a 200 body handled by
        ``rich_message._handle_response``, which mutes the chat and raises
        — it never reaches ``_run``'s ``RetryAfter`` branch. Rather than
        an observer registry or a new callback, the limiter simply
        compares ``MUTE``'s wall deadline against what it has already
        recorded, on a path it runs anyway.

        ``note_ban`` sets ``ban_seen_until``, so the two entry points
        (here and ``_run``) cannot double-count one ban.
        """
        remaining = MUTE.remaining(budget.chat_id)
        if remaining <= 0.0:
            return
        deadline = time.time() + remaining
        if deadline <= budget.ban_seen_until + 1.0:
            return
        self.note_ban(budget.chat_id, remaining)

    def note_ban(self, chat_id, seconds: float) -> None:
        """Record a ban: rate to the floor, one stamp, state dirty.

        Idempotent per ban — ``ban_seen_until`` is what makes a second
        sighting of the SAME ban a no-op, so ``_run``'s branch and
        ``_sync_mute`` can both call it. Does NOT arm the mute:
        ``flood.MUTE.mute`` stays the one arming point (``transport.py``
        and ``rich_message.py``), so there is exactly one place to look
        when asking why a chat is muted.
        """
        value = _finite(seconds)
        if value is None:
            # A non-finite or unreadable duration is not a ban we can
            # reason about (8.29 T2): `now + inf` is a deadline nothing
            # ever passes, which would freeze this chat's climb for ever.
            return
        seconds = min(max(value, 0.0), FLOOD_MUTE_MAX_SECONDS)
        budget = self._budget_for(self._key(chat_id))
        if budget is None:
            return
        wall_now = time.time()
        deadline = wall_now + seconds
        # ABOVE the idempotence guard on purpose. `_run`'s ban branch
        # calls this BEFORE `transport._send_with_retry` arms the mute, so
        # on that path the only call that can see a live mute is the
        # second one — the one the guard turns back.
        self._note_mute_deadline(budget)
        if deadline <= budget.ban_seen_until + 1.0:
            return
        budget.ban_seen_until = deadline
        budget.note_ban_stamp(wall_now)
        now = self._clock()
        before = budget.rate
        budget.set_rate(self._min_rate, now)
        budget.rate_earned_at = now
        if before != budget.rate:
            log.warning(
                "flood: chat %s banned for %ds → earned rate %g → %g calls/s",
                budget.chat_id, int(max(seconds, 0.0)), before, budget.rate,
            )
        _mark_state_dirty()

    def _minimal(self, budget: ChatBudget | None) -> bool:
        """Is this chat in minimal mode — ornaments suspended, answers not?

        ``None`` (an unresolvable chat) is never minimal: there is no
        earned rate to be below a floor, and refusing such a call would
        mute the daemon rather than pace a chat.
        """
        if budget is None:
            return False
        return budget.rate < self._minimal_floor

    # ── the earned rate, as a public surface ─────────────────────────────

    def earned_rate(self, chat_id) -> float:
        """This chat's learned send rate in calls/s, earned up to now.

        ``FLOOD_START_RATE`` for a chat never seen — reported without
        creating a budget for it, so merely asking does not allocate.
        """
        key = self._key(chat_id)
        budget = self._budgets.get(key) if key is not None else None
        if budget is None:
            return self._start_rate
        now = self._clock()
        self._decay(budget, now)
        self._earn(budget, now)
        return budget.rate

    def minimal_mode(self, chat_id) -> bool:
        """True while this chat's earned rate is under the floor."""
        key = self._key(chat_id)
        budget = self._budgets.get(key) if key is not None else None
        if budget is None:
            return False
        self._earn(budget, self._clock())
        return self._minimal(budget)

    def sustained_used(self, chat_id) -> int:
        """Calls this chat has made inside the current rolling window."""
        key = self._key(chat_id)
        budget = self._budgets.get(key) if key is not None else None
        return budget.window.used() if budget is not None else 0

    def _sustained_gap(self, chat_id) -> float:
        """The sustained gap a budget for *chat_id* would have."""
        if is_group_chat(chat_id):
            return _window_gap(min(self._sustained_max, self._group_max_calls),
                               self._group_window)
        return _window_gap(self._sustained_max, self._sustained_window)

    def pacing_for(self, chat_id) -> dict:
        """Everything the busy-card cadence needs, in one read.

        One accessor rather than four, because the animator must read
        them LATE and together: caching any of them — or reading the rate
        one tick and the backoff the next — is how two budgets drift
        apart (the 8.17 failure, repeated).
        """
        key = self._key(chat_id)
        budget = self._budgets.get(key) if key is not None else None
        if budget is None:
            # A chat with no budget yet reports what one WOULD have, not
            # zeros. `_card_interval` asks before the first edit of every
            # turn, so a zero gap here would let a fresh chat's first card
            # tick at the old 1.32 s and only slow to 2.2 s once a budget
            # existed — a burst at exactly the moment a chat is least
            # known, which is the opposite of what the cap is for.
            return {"rate": self._start_rate,
                    "sustained_min_gap": self._sustained_gap(chat_id),
                    "backoff": 1.0, "minimal": False}
        now = self._clock()
        self._decay(budget, now)
        self._earn(budget, now)
        return {
            "rate": budget.rate,
            "sustained_min_gap": _window_gap(budget.window.limit,
                                             budget.window.period),
            "backoff": max(budget.backoff, 1.0),
            "minimal": self._minimal(budget),
        }

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
        value = _finite(seconds)
        if value is None:
            return
        seconds = value
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
        # MULTIPLICATIVE DECREASE (8.27). Telegram just said this chat is
        # going too fast, which is the only direct evidence about its real
        # limit we ever get — so halve the EARNED RATE, not merely the card
        # cadence. The two are kept separate on purpose: the multiplier
        # above paces the card and decays in a minute, this paces the chat
        # and recovers in minutes or hours depending on whether a ban is
        # in recent history.
        before = budget.rate
        budget.set_rate(max(budget.rate * 0.5, self._min_rate), now)
        budget.rate_earned_at = now
        # ONE warning, no traceback: Telegram escalates on the COUNT of
        # violations, and 2,021 stack traces told the operator nothing
        # 2,021 times. The rate goes in the same line rather than a second
        # one, so a 429 is still exactly one log record.
        log.warning(
            "flood: chat %s 429 retry_after=%ss → cadence ×%g, rate %g → %g/s",
            budget.chat_id, seconds, budget.backoff, before, budget.rate,
        )
        self._maybe_write_signal()
        _mark_state_dirty()

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
        last token belongs to whatever blocking caller comes next. The
        same reserve is counted in SLOTS of the rolling window — the last
        two calls of the window belong to real content, not to a card
        refresh. Since 8.27 every chat has that window, so this applies
        to a private DM too; it used to hold only for groups.
        """
        now = self._clock()
        if (now < budget.retry_until
                or budget.chat.tokens() < _SKIP_RESERVE
                or budget.window.free() < _SKIP_RESERVE
                or self._overall.tokens() < 1.0):
            budget.skipped += 1
            raise FloodSkipped(budget.chat_id, endpoint)
        budget.chat.take(1.0)
        budget.window.take(1.0)
        self._overall.take(1.0)

    async def _acquire_blocking(
        self, budget: ChatBudget | None, *, reserve: float = 0.0,
    ) -> None:
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

        ``reserve`` is how many tokens this caller must leave BEHIND
        (8.26 R3, row L). ``_SKIP_RESERVE`` for an ORNAMENT, ``0.0`` for
        everything else: a blocking card edit must not take the last
        token an answer is about to need. Before 8.26 only SKIP-kind
        callers respected the reserve, so a card that had been refused
        twice and escalated to blocking could take it — which is the
        priority inversion R3 exists to end. It is a WAIT, never a
        refusal: the ornament simply queues until the chat is comfortable.
        """
        if budget is None:
            await self._wait_for_overall()
            return
        ticket: asyncio.Future = asyncio.get_running_loop().create_future()
        # The reserve IS the ornament marker: it is the only class that
        # asks for one (see `process_request`).
        ticket._aipager_ornament = reserve > 0.0
        if reserve > 0.0:
            budget.waiters.append(ticket)
        else:
            # R3, the ordering half of "an ESSENTIAL never waits behind an
            # ORNAMENT". The reserve alone does not deliver that: this
            # deque is a strict FIFO, so an ornament that arrived first and
            # is waiting out `1 + _SKIP_RESERVE` tokens would HEAD-OF-LINE
            # BLOCK an answer that only needs one and could have it now —
            # the very inversion R3 exists to end, just moved from the
            # bucket into the queue.
            #
            # So a non-ornament is inserted ahead of every ORNAMENT ticket
            # and behind every non-ornament one. FIFO is preserved WITHIN a
            # class, which is what "serve in arrival order" was protecting;
            # across classes the order is now priority, deliberately.
            # An ornament is never dropped by this — only overtaken.
            index = len(budget.waiters)
            for position, waiting in enumerate(budget.waiters):
                if getattr(waiting, "_aipager_ornament", False):
                    index = position
                    break
            budget.waiters.insert(index, ticket)
        try:
            if budget.waiters[0] is not ticket:
                await ticket
            while True:
                now = self._clock()
                self._decay(budget, now)
                wait = max(
                    budget.retry_until - now,
                    budget.chat.time_until(1.0 + reserve),
                    budget.window.time_until(1.0),
                    self._overall.time_until(1.0),
                )
                if wait <= 0:
                    budget.chat.take(1.0)
                    budget.window.take(1.0)
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

        Raises :class:`~aipager.bot.flood.FloodMuted` when the resolved
        chat is flood-muted — THE GATE (8.26 R1). The callback is not
        called, nothing is retried and no fallback is offered: the next
        attempt into a ban is a fresh violation that extends it. This is
        the one place that covers every outbound path, because every Bot
        API call this daemon makes reaches here — PTB's ``ExtBot._do_post``
        and ``rich_message._post`` alike. Enforcement used to live in 22
        per-site checks across five files, and exactly one forgotten site
        (``animation.send_busy``) cost 9.5 hours on 2026-09-15.

        Raises :class:`FloodSkipped` when a skip-kind caller finds the
        budget short — the callback is not called. A ``RetryAfter`` past
        ``TELEGRAM_MAX_RETRY_AFTER`` propagates untouched; a small one is
        recorded, waited out, and the blocking caller's request is made
        exactly once more.
        """
        chat_id = self._key(data.get("chat_id") if isinstance(data, dict) else None)
        budget = self._budget_for(chat_id)
        if budget is not None:
            now = self._clock()
            # All three are lazy, synchronous and never raise: the backoff
            # multiplier decays, a ban armed on the rich path is noticed,
            # and quiet windows are credited to the earned rate. Ordered
            # so `_earn` sees the ban `_sync_mute` just recorded and
            # freezes instead of climbing through it.
            self._decay(budget, now)
            self._sync_mute(budget, now)
            self._earn(budget, now)

        # ── THE GATE (R1) ────────────────────────────────────────────────
        # Placed here, above the exempt branch, so a mute covers BOTH
        # branches with an id already normalised by `_key`. Reactions and
        # chat actions are exempt from the budget, never from the mute
        # (D-1); `sendChatAction` answering 200 through a small 429 window
        # says nothing about a BAN.
        #
        # `chat_id is None` means there is no chat to check — `getMe`,
        # `getFile`, `setMyCommands`, `answerCallbackQuery`. Those are NOT
        # gated: a mute on one chat must never mute the daemon itself.
        # It is also why `flood._key(None) == "None"` never matters here:
        # the short-circuit happens before any `flood` key is built.
        #
        # `MUTE.is_muted` is deliberately the DESTRUCTIVE read: the first
        # call after a deadline lapses is what logs the single "lifted"
        # INFO and rewrites the signal file.
        if chat_id is not None and MUTE.is_muted(chat_id):
            if budget is not None:
                budget.muted_refusals += 1
            raise FloodMuted(MUTE.remaining(chat_id), chat_id)

        # The priority class this caller declared. Read BEFORE the exempt
        # branch so a SIGNAL keeps its budget exemption whatever else
        # changes, and so the class is available to the acquire below.
        cls = _class_of(rate_limit_args)

        # ── MINIMAL MODE (R3) ────────────────────────────────────────────
        # Below `FLOOD_MINIMAL_MODE_RATE_FLOOR` a chat can no longer afford
        # decoration: ornaments are suspended so the rate it still has is
        # spent on answers. `FloodSkipped`, deliberately NOT `FloodMuted` —
        # nothing is banned and the chat is healthy, and every ornament
        # caller already treats `FloodSkipped` as "nothing sent, stamps
        # untouched, try again next tick". Raising `FloodMuted` here would
        # make the animator stop the card for good.
        #
        # SIGNAL is never suspended (the user must still see their message
        # acknowledged) and ESSENTIAL never is — that is the whole point.
        if cls == PRIORITY_ORNAMENT and self._minimal(budget):
            budget.ornaments_suspended += 1
            raise FloodSkipped(chat_id, endpoint)

        if endpoint in _CHAT_BUDGET_EXEMPT:
            # §11 U4: reactions are exempt from the chat budget. They are
            # a separate bucket on Telegram's side, and `transport.py`'s
            # 🚨 give-up reaction fires exactly when this chat's queue is
            # jammed — budgeting it would gag the one signal that still
            # reaches the user (see transport.py:456-458). Counted, so a
            # future incident can show whether that is still true.
            #
            # §12: the "typing…" chat action is exempt on the same terms,
            # and on live evidence — it answered 200 throughout a real
            # `retry_after=10` window that refused every edit into the
            # same chat (see CHAT_ACTION_ENDPOINT). Neither waits on a
            # chat token nor consumes one; both still meet the 30/s
            # overall bucket.
            if budget is not None:
                if endpoint == REACTION_ENDPOINT:
                    budget.reactions += 1
                else:
                    budget.chat_actions += 1
            await self._wait_for_overall()
            return await self._run(
                budget, callback, args, kwargs, endpoint, chat_id,
                kind="blocking", allow_retry=False,
                note_429=endpoint != CHAT_ACTION_ENDPOINT,
            )

        kind = _kind_of(rate_limit_args)
        if kind == "skip" and budget is not None:
            self._acquire_skip(budget, endpoint)
        else:
            # No resolvable chat (answerCallbackQuery, getMe, …) meets the
            # overall bucket and nothing else, and is never skipped (R7).
            #
            # An ORNAMENT leaves the reserve behind even here, on the
            # BLOCKING path (R3, row L): an answer must never queue behind
            # a card edit that took the last token.
            await self._acquire_blocking(
                budget,
                reserve=(_SKIP_RESERVE if cls == PRIORITY_ORNAMENT else 0.0),
            )
        return await self._run(
            budget, callback, args, kwargs, endpoint, chat_id, kind=kind,
        )

    async def _run(
        self, budget, callback, args, kwargs, endpoint, chat_id,
        *, kind: str = "blocking", allow_retry: bool = True,
        note_429: bool = True,
    ):
        """Run the callback whose budget has already been paid.

        A 429 that comes back anyway is the limiter's to absorb: PTB's
        own loop is gone, so nothing else will. Any other exception
        propagates untouched — ``BaseRateLimiter``'s contract says this
        method "should not handle any other exception raised by
        ``callback``".

        ``note_429=False`` re-raises a small 429 without recording it
        against the chat. Only the "typing…" chat action passes it
        (§12/R4): that call is not in the chat's message bucket, so a 429
        on it is no evidence about the bucket — deferring the chat and
        doubling its card cadence over one would slow every card for a
        call that never competed with them. The animator logs it once an
        hour and skips the indicator.
        """
        if budget is not None:
            budget.calls += 1
        try:
            return await callback(*args, **kwargs)
        except RetryAfter as exc:
            seconds = _retry_after_seconds(exc)
            if seconds > TELEGRAM_MAX_RETRY_AFTER:
                # A ban. `transport._send_with_retry`'s give-up branch and
                # `rich_message._ban_if_excessive` own the MUTE; the
                # re-raise below is unchanged and still theirs.
                #
                # What IS recorded here is the rate (8.27): a ban drops the
                # chat to `FLOOD_MIN_RATE` and leaves a wall-clock stamp
                # that outlives the process. Before 8.27 a ban taught the
                # limiter nothing at all — "restart starts every chat at
                # ×1" — so the daemon came back at full speed into an
                # account Telegram was still penalising. No backoff bump
                # and no deferral here, exactly as before (R6).
                self.note_ban(chat_id, seconds)
                raise
            if not note_429:
                # An exempt call's 429 (the typing action, §12/R4): the
                # chat's send budget is not implicated, so nothing is
                # deferred and no backoff is bumped. The caller drops the
                # call — it never reaches the mute or the card path.
                raise
            self.note_retry_after(chat_id, seconds)
            if kind == "skip":
                raise FloodSkipped(chat_id, endpoint) from exc
            if not allow_retry:
                raise
            if budget is None:
                # No resolvable chat (answerCallbackQuery, getMe, …), so
                # `note_retry_after` had nothing to defer and the retry
                # would meet only the 30/s overall bucket — i.e. go
                # straight back into the window Telegram has just closed,
                # which is the exact failure mode 8.21 exists to remove.
                # Wait out what it asked for, once. Bounded: anything past
                # TELEGRAM_MAX_RETRY_AFTER was re-raised above as a ban.
                await self._sleep(max(seconds, 0.0))
            await self._acquire_blocking(budget)
            return await self._run(
                budget, callback, args, kwargs, endpoint, chat_id,
                kind=kind, allow_retry=False,
            )

    # ── persistence (8.28) ───────────────────────────────────────────────

    def mono_equiv(self, wall_stamp: float) -> float:
        """A wall-clock stamp expressed on THIS limiter's clock.

        The durable file is written in wall time — the reader is another
        process and possibly another boot — while every budget stamp is on
        the injected (monotonic) clock. Restoring one into the other needs
        exactly this rebasing, and it is a named method so a test can
        assert on it rather than on an inline expression.

        A stamp in the FUTURE (a clock that went backwards between the
        write and the read) is treated as "now": never credit a chat with
        quiet time it has not had.
        """
        elapsed = max(0.0, time.time() - float(wall_stamp))
        return self._clock() - elapsed

    def serialise(self) -> list[dict]:
        """The durable per-chat part, in wall time.

        ``sustained_used`` / ``sustained_limit`` / ``minimal`` are for the
        `aipager status` reader and are deliberately IGNORED on load: they
        describe a rolling window that has long since rolled by the time
        anyone restarts.
        """
        now = self._clock()
        wall_now = time.time()
        chats = []
        for budget in self._budgets.values():
            self._decay(budget, now)
            self._earn(budget, now)
            chats.append({
                "chat_id": budget.chat_id,
                "rate": budget.rate,
                "rate_earned_at": wall_now - max(now - budget.rate_earned_at, 0.0),
                "backoff": max(budget.backoff, 1.0),
                "last_429_at": (
                    wall_now - max(now - budget.last_429_at, 0.0)
                    if budget.last_429_at > 0.0 else 0.0
                ),
                "ban_stamps": list(budget.ban_stamps),
                # Already WALL clock (it is the deadline the ban was armed
                # with), so it goes out as it is. Carried across the
                # restart so the same ban is not counted a second time by
                # `_sync_mute` when the new daemon's first call finds the
                # restored mute still running — and so `_earn_floor` still
                # knows when it lifts.
                "ban_seen_until": budget.ban_seen_until,
                # The lift instant `_earn_floor` measures from. A restart
                # INSIDE a ban must not come back able to credit the rest
                # of that ban as quiet time.
                "muted_until": budget.muted_until,
                "sustained_used": budget.window.used(),
                "sustained_limit": budget.window.limit,
                "minimal": self._minimal(budget),
            })
        return chats

    def restore(self, chats) -> int:
        """Reinstate per-chat state from a loaded document.

        Never raises, whatever the file holds — a corrupt state file must
        leave the daemon running (the ``SessionRegistry.load`` contract).
        Unknown keys are ignored and a malformed entry is skipped.

        ``backoff`` is restored as stored and then decays NATURALLY,
        because ``_decay`` reads the rebased ``last_429_at``: a daemon
        that was down for an hour finds the multiplier already back at
        ×1 without any special case here.
        """
        if not isinstance(chats, list):
            return 0
        restored = 0
        wall_now = time.time()
        for entry in chats:
            if not isinstance(entry, dict):
                continue
            key = self._key(entry.get("chat_id"))
            if key is None:
                continue
            try:
                budget = self._budget_for(key)
                if budget is None:
                    continue
                # BAN HISTORY FIRST: the ceiling the rate is clamped to
                # depends on it (`max_rate_for`), so restoring the rate
                # before the stamps would admit a rate this chat's own
                # history says it may not have.
                stamps = entry.get("ban_stamps")
                if isinstance(stamps, list):
                    # A stamp in the FUTURE is a clock that moved, not a
                    # ban that has not happened yet — clamped to now
                    # rather than dropped, because dropping it would
                    # forget a penalty, which is the unsafe direction.
                    budget.ban_stamps = [
                        min(stamp, wall_now) for stamp in (
                            _finite(x) for x in
                            stamps[-ChatBudget.MAX_BAN_STAMPS:]
                        )
                        if stamp is not None and stamp >= 0.0
                    ]
                for field, attr in (("ban_seen_until", "ban_seen_until"),
                                    ("muted_until", "muted_until")):
                    value = _finite(entry.get(field))
                    if value is not None and value > 0.0:
                        # Clamped like every other deadline (D-7): a file
                        # written before a clock jump must not freeze the
                        # climb for years.
                        setattr(budget, attr,
                                min(value, wall_now + FLOOD_MUTE_MAX_SECONDS))
                rate = _finite(entry.get("rate"))
                if rate is not None:
                    budget.set_rate(
                        min(max(rate, self._min_rate),
                            self.max_rate_for(budget, wall_now)),
                        self._clock(),
                    )
                earned_at = _finite(entry.get("rate_earned_at"))
                if earned_at:
                    budget.rate_earned_at = self.mono_equiv(earned_at)
                backoff = _finite(entry.get("backoff"))
                if backoff:
                    budget.backoff = min(max(backoff, 1.0), self._backoff_max)
                last_429 = _finite(entry.get("last_429_at"))
                if last_429:
                    budget.last_429_at = self.mono_equiv(last_429)
            except (TypeError, ValueError):
                log.debug("flood state: skipping malformed chat entry %r",
                          entry, exc_info=True)
                continue
            restored += 1
        return restored

    # ── observability ────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """The whole budget, decayed to now, for tests and the status file.

        ``calls`` counts every callback run for the chat, the exempt ones
        included; ``reactions`` and ``chat_actions`` are those exempt
        subsets on their own.
        ``group_window_free`` is GONE (8.27), replaced by
        ``sustained_used``/``sustained_free``/``sustained_limit``, which
        exist for every chat rather than only for groups — a private chat
        had no volume ceiling at all, which is what let two BUSY sessions
        run ~45 minutes into a 9.5-hour ban.

        ``rate`` is the chat's EARNED send rate and ``minimal`` says it
        has fallen under the floor. ``muted_refusals`` counts calls the
        mute gate refused and ``ornaments_suspended`` those minimal mode
        did — neither reached its callback, so neither is in ``calls``.
        """
        now = self._clock()
        wall_now = time.time()
        chats = []
        for budget in self._budgets.values():
            self._decay(budget, now)
            self._earn(budget, now)
            chats.append({
                "chat_id": budget.chat_id,
                "kind": "group" if budget.is_group else "private",
                "tokens": budget.chat.tokens(),
                "rate": budget.rate,
                "minimal": self._minimal(budget),
                "sustained_used": budget.window.used(),
                "sustained_free": budget.window.free(),
                "sustained_limit": budget.window.limit,
                "backoff": max(budget.backoff, 1.0),
                "retry_until_in": max(budget.retry_until - now, 0.0),
                "calls": budget.calls,
                "skipped": budget.skipped,
                "reactions": budget.reactions,
                "chat_actions": budget.chat_actions,
                "muted_refusals": budget.muted_refusals,
                "ornaments_suspended": budget.ornaments_suspended,
                "bans_today": budget.bans_today(wall_now),
                "waiters": len(budget.waiters),
            })
        return {"overall_tokens": self._overall.tokens(), "chats": chats}

    def sweep(self) -> None:
        """Decay every chat to now and refresh the status signal file.

        Called from the session monitor's 2 s tick, because the animator's
        :meth:`cadence_multiplier` — the other caller that takes the file
        back down — only runs while a busy card is TICKING. A chat that
        429s and then goes quiet (every session IDLE, no card) would leave
        `aipager status` and `aipager doctor` reporting "×4" for hours,
        where design §5 says the file is unlinked as soon as no chat is
        backing off. Driving it from work that already happens means no
        timer of our own to leak, and it is cheap: a handful of chats, and
        `_maybe_write_signal` returns before serialising anything whenever
        nothing is backing off.

        One call does it all: :meth:`_maybe_write_signal` decays every
        chat on its way through them, then rewrites — or unlinks — the
        file. (A separate decay loop here would be dead code, and dead
        code that looks like a guard is worse than none.)
        """
        self._maybe_write_signal()

    def reset(self) -> None:
        """Forget every chat and take the signal file down with them.

        Clearing the whole dict is what drops the earned rates, ban
        stamps and counters with it — a per-field reset here would be a
        second place to keep in step with `ChatBudget.__init__`, and the
        one that got forgotten.
        """
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
        if not self._signal_armed and path.parent == Path(config.SOCKET_PATH).parent:
            # Not the daemon's limiter (nothing ever awaited `initialize`,
            # and no explicit path was given), and the file it is about to
            # write sits in the LIVE daemon's runtime directory beside its
            # socket. Refuse: `aipager status` and `aipager doctor` would
            # report a phantom backoff for a daemon that has none, and the
            # daemon itself never reads the file back, so nothing would
            # correct it. This happened during 8.21's own QA, from a
            # throwaway `python -c` that called `note_retry_after`.
            #
            # Tests are unaffected: conftest's `_isolate_flood_mute` moves
            # FLOOD_BACKOFF_FILE into `tmp_path`, which is not the socket's
            # directory — that fixture stays the primary isolation, this is
            # the belt for everything that runs outside pytest.
            log.debug("flood: not writing %s — this process is not the daemon",
                      path)
            return
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
    "CHAT_ACTION_ENDPOINT",
    "ChatBudget",
    "FloodSkipped",
    "PRIORITY_ESSENTIAL",
    "PRIORITY_ORNAMENT",
    "PRIORITY_SIGNAL",
    "REACTION_ENDPOINT",
    "SlidingWindow",
    "TokenBucket",
    "card_interval",
    "clear_backoff_signal",
    "is_group_chat",
    "rate_limit_args",
]
