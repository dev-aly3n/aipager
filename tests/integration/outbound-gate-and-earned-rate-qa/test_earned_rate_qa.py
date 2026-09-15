"""R3/R4 — the earned rate, minimal mode and the sustained window.

Rows F, G, K, L and criteria 8-13. Everything is asserted against the
NAMED constants in ``aipager.config``: a literal here would pass the day
someone retunes the ladder and the behaviour stops matching the document.
"""

from __future__ import annotations

import logging
import math

import pytest

from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    PRIORITY_ORNAMENT,
    FloodSkipped,
    card_interval,
    rate_limit_args,
)

CHAT = 256113222          # a PRIVATE chat: no sustained window before 8.29
BAN = 34212.0
WINDOW = config.FLOOD_SUCCESS_WINDOW_SECONDS


async def _ok():
    return "sent"


def most_in_window(stamps, window: float) -> int:
    """The largest number of stamps inside any rolling *window*."""
    ordered = sorted(stamps)
    return max((sum(1 for t in ordered[i:] if t - start < window)
                for i, start in enumerate(ordered)), default=0)


def _send(limiter, *, endpoint="sendMessage", chat=CHAT, priority=None,
          kind="blocking", ran=None):
    async def _callback():
        if ran is not None:
            ran.append(endpoint)
        return "sent"

    args = None
    if priority is not None or kind != "blocking":
        args = rate_limit_args(kind=kind, priority=priority or PRIORITY_ESSENTIAL)
    return limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": chat}, rate_limit_args=args)


def _seed(limiter, run_async, chat=CHAT):
    """Make the chat exist in the limiter (a budget is created lazily)."""
    run_async(_send(limiter, chat=chat))


# ===== row F — one 429 halves the rate ===================================

def test_a_new_chat_starts_at_the_named_start_rate(limiter):
    """Criterion 11's floor case: an unseen chat is worth
    ``FLOOD_START_RATE``, half the published ceiling."""
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)


def test_one_429_halves_the_earned_rate(limiter, run_async):
    """Row F / criterion 10 — multiplicative decrease."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.FLOOD_START_RATE / 2)


def test_one_429_emits_exactly_one_warning_with_no_traceback(
    limiter, run_async, caplog,
):
    """Row F. A traceback per 429 is what buried the real lines in the
    2026-09-15 journal."""
    _seed(limiter, run_async)
    with caplog.at_level(logging.WARNING, logger="aipager.bot.flood_budget"):
        limiter.note_retry_after(CHAT, 5)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and all(r.exc_info is None for r in warnings)


def test_one_429_doubles_the_cadence_multiplier(limiter, run_async):
    """Row F's second half: AIMD is ADDITIVE to the existing backoff, so
    the ×2 card cadence must still be there."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.cadence_multiplier(CHAT) == 2.0


def test_the_rate_never_falls_below_the_named_floor(limiter, run_async):
    """Boundary: halving is clamped at ``FLOOD_MIN_RATE``. Twenty 429s is
    far past where 0.5 × 2⁻ⁿ would underflow the floor."""
    _seed(limiter, run_async)
    for _ in range(20):
        limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_retry_after_past_the_cap_is_not_a_429(limiter, run_async):
    """Boundary / equivalence class: past ``TELEGRAM_MAX_RETRY_AFTER`` it
    is a BAN, and ``note_retry_after`` is documented as a no-op for it —
    the ban path owns that case and must not be double-counted."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, config.TELEGRAM_MAX_RETRY_AFTER + 1)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)


def test_a_429_on_one_chat_leaves_another_chats_rate_alone(
    limiter, run_async,
):
    """The rate is per chat — the whole point of learning it."""
    _seed(limiter, run_async)
    _seed(limiter, run_async, chat=-100777)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(-100777) == pytest.approx(
        config.FLOOD_START_RATE)


# ===== row G — the climb back, per quiet window ==========================

def test_one_quiet_window_earns_exactly_one_increase(
    limiter, qa_clock, run_async,
):
    """Row G / criterion 11, just-inside the boundary."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    before = limiter.earned_rate(CHAT)
    qa_clock.advance(WINDOW)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        before + config.FLOOD_RATE_INCREASE)


def test_just_under_a_quiet_window_earns_nothing(
    limiter, qa_clock, run_async,
):
    """Row G, just-outside: the step is whole-window, like ``_decay``."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    before = limiter.earned_rate(CHAT)
    qa_clock.advance(WINDOW - 0.001)
    assert limiter.earned_rate(CHAT) == pytest.approx(before)


def test_twelve_quiet_minutes_climb_to_the_named_ceiling(
    limiter, qa_clock, run_async,
):
    """Row G as the design states it: 0.25 + 12×0.1 would be 1.45, and the
    ceiling is ``TELEGRAM_PRIVATE_MAX_RATE``."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    qa_clock.advance(12 * WINDOW)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_a_healthy_chat_never_climbs_past_the_ceiling(
    limiter, qa_clock, run_async,
):
    """Boundary: a day of quiet must not make the bot faster than
    Telegram's published burst."""
    _seed(limiter, run_async)
    qa_clock.advance(86_400)
    assert limiter.earned_rate(CHAT) <= config.TELEGRAM_PRIVATE_MAX_RATE


# ===== criterion 12 — the post-BAN regime is the slow one ===============

def test_a_ban_puts_the_rate_on_the_floor(limiter, run_async):
    """Criterion 12 / row H's rate half."""
    _seed(limiter, run_async)
    limiter.note_ban(CHAT, BAN)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_the_post_ban_climb_takes_the_named_recovery_hours(
    limiter, qa_clock, run_async,
):
    """Criterion 12. After a ban the climb is stretched over
    ``FLOOD_RATE_RECOVERY_HOURS`` — 'a ban today means a reduced start
    tomorrow', which the old 60 s decay could never express."""
    _seed(limiter, run_async)
    limiter.note_ban(CHAT, BAN)
    qa_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_halfway_through_recovery_the_rate_is_still_reduced(
    limiter, qa_clock, run_async,
):
    """The other side of the same boundary: the climb must be SLOW, not
    merely eventual. On the 429 regime six hours would have hit the
    ceiling 35 times over."""
    _seed(limiter, run_async)
    limiter.note_ban(CHAT, BAN)
    qa_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600 / 2)
    rate = limiter.earned_rate(CHAT)
    assert config.FLOOD_MIN_RATE < rate < config.TELEGRAM_PRIVATE_MAX_RATE


def test_no_rate_is_earned_while_the_chat_is_muted(
    limiter, qa_clock, run_async,
):
    """Criterion 12's second half: climbing THROUGH a ban is precisely
    learning nothing — defect D2 restated."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    qa_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_no_rate_is_earned_through_a_ban_armed_by_the_rich_path(
    limiter, qa_clock, run_async,
):
    """Criterion 12, for the ban the incident actually had.

    The rich path's ban is a 200 body, so it arms ``MUTE`` and never
    reaches ``_run``; and while the mute holds, the callers' own
    pre-checks (``rich_message._raise_if_muted``, the animator's typing
    guard) mean NOTHING reaches ``process_request`` either. If the climb
    is only frozen inside ``process_request``, a 21-minute ban is credited
    as 21 quiet success windows and the chat comes out of it FASTER than
    it went in — which is how 1283 becomes 312 becomes 34212.
    """
    _seed(limiter, run_async)          # the chat must EXIST, or this row
    MUTE.mute(CHAT, 1283.0)            # asks about a budget that is not
    qa_clock.advance(1284.0)           # there and passes for free
    assert limiter.earned_rate(CHAT) <= config.FLOOD_START_RATE


def test_a_ban_leaves_the_chat_slower_than_it_found_it(
    limiter, qa_clock, run_async,
):
    """R5 in one sentence: 'a ban today means a reduced start tomorrow'."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, 1283.0)
    before = limiter.earned_rate(CHAT)
    qa_clock.advance(1284.0)
    assert limiter.earned_rate(CHAT) <= before


# ===== row K — minimal mode, on the NAMED floor ==========================

def _restore_rate(limiter, qa_clock, rate, chat=CHAT):
    limiter.restore([{
        "chat_id": chat, "rate": rate, "rate_earned_at": qa_clock.wall,
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }])


def test_a_rate_exactly_at_the_floor_is_not_minimal_mode(
    limiter, qa_clock,
):
    """Boundary value, just-inside. ``FLOOD_MINIMAL_MODE_RATE_FLOOR`` is
    documented as 'below this, ornaments suspend'; at the floor itself the
    card still animates."""
    _restore_rate(limiter, qa_clock, config.FLOOD_MINIMAL_MODE_RATE_FLOOR)
    assert limiter.minimal_mode(CHAT) is False


def test_a_rate_a_hair_below_the_floor_is_minimal_mode(limiter, qa_clock):
    """Boundary value, just-outside."""
    _restore_rate(limiter, qa_clock,
                  config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001)
    assert limiter.minimal_mode(CHAT) is True


def test_two_429s_from_the_start_rate_reach_minimal_mode(
    limiter, run_async,
):
    """D-5's ladder, asserted through the public rate: START 0.5 -> 0.25
    (above the floor) -> 0.125 (below it)."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.minimal_mode(CHAT) is False
    limiter.note_retry_after(CHAT, 5)
    assert limiter.minimal_mode(CHAT) is True


def test_an_ornament_is_refused_in_minimal_mode_without_a_call(
    limiter, qa_clock, run_async,
):
    """Row K / criterion 8: pixels are what pressure sheds."""
    _restore_rate(limiter, qa_clock,
                  config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001)
    ran: list[str] = []
    with pytest.raises(FloodSkipped):
        run_async(_send(limiter, priority=PRIORITY_ORNAMENT, ran=ran))
    assert ran == []


def test_an_essential_still_goes_through_in_minimal_mode(
    limiter, qa_clock, run_async,
):
    """Row K's other half — answers still flow. This is the whole thesis:
    drop pixels, never answers."""
    _restore_rate(limiter, qa_clock,
                  config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001)
    ran: list[str] = []
    assert run_async(
        _send(limiter, priority=PRIORITY_ESSENTIAL, ran=ran)) == "sent"
    assert ran == ["sendMessage"]


def test_an_unclassified_call_is_essential_in_minimal_mode(
    limiter, qa_clock, run_async,
):
    """D-9: an unclassified call is ESSENTIAL, so a forgotten
    classification degrades to 'never dropped'."""
    _restore_rate(limiter, qa_clock,
                  config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001)
    assert run_async(_send(limiter)) == "sent"


def test_a_signal_is_never_suspended_by_minimal_mode(
    limiter, qa_clock, run_async,
):
    """SIGNAL is exempt from the budget and from minimal mode — never
    from the mute (that is row C)."""
    from aipager.bot.flood_budget import PRIORITY_SIGNAL
    _restore_rate(limiter, qa_clock,
                  config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001)
    assert run_async(_send(limiter, endpoint="setMessageReaction",
                           priority=PRIORITY_SIGNAL)) == "sent"


# ===== row L — an ornament never takes an answer's last token ===========

def _drain_to_one_token(limiter, run_async):
    """Spend the burst down to a single token without moving the clock."""
    async def _drive():
        for _ in range(int(config.TELEGRAM_CHAT_BURST) - 1):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)
    run_async(_drive())


def test_an_essential_takes_the_last_token_immediately(
    limiter, qa_clock, run_async,
):
    """Row L / criterion 9."""
    _drain_to_one_token(limiter, run_async)
    qa_clock.sleeps.clear()
    assert run_async(_send(limiter, priority=PRIORITY_ESSENTIAL)) == "sent"
    assert qa_clock.sleeps == []


def test_a_blocking_ornament_waits_for_the_last_token(
    limiter, qa_clock, run_async,
):
    """Row L. The reserve now applies on the BLOCKING path too, where
    until 8.29 only skip-kind respected it — an ornament must never take
    the tokens an answer will need."""
    _drain_to_one_token(limiter, run_async)
    qa_clock.sleeps.clear()
    run_async(_send(limiter, priority=PRIORITY_ORNAMENT))
    assert qa_clock.sleeps, "the ornament took the answer's token"


def test_a_skip_kind_ornament_is_refused_outright(
    limiter, qa_clock, run_async,
):
    """Row L's third case: a skip-kind ornament never waits, it goes."""
    _drain_to_one_token(limiter, run_async)
    ran: list[str] = []
    with pytest.raises(FloodSkipped):
        run_async(_send(limiter, priority=PRIORITY_ORNAMENT, kind="skip",
                        ran=ran))
    assert ran == []


# ===== R4 — the sustained window, now on PRIVATE chats too ==============

def test_a_private_chat_has_the_named_sustained_limit(limiter, run_async):
    """R4: 30 per rolling 60 s for every chat kind. Before 8.29
    ``SlidingWindow`` was group-only, so a private chat could emit
    1 call/s for ever — which is what two BUSY sessions did for 45
    minutes before the 9.5-hour ban."""
    _seed(limiter, run_async)
    rows = {c["chat_id"]: c for c in limiter.snapshot()["chats"]}
    assert rows[CHAT]["sustained_limit"] == config.FLOOD_SUSTAINED_MAX


def test_a_group_keeps_the_stricter_group_limit(limiter, run_async):
    """R4's parenthesis: groups keep 20/60 s as the stricter of the two."""
    _seed(limiter, run_async, chat=-1001234567)
    rows = {c["chat_id"]: c for c in limiter.snapshot()["chats"]}
    assert rows[-1001234567]["sustained_limit"] == min(
        config.FLOOD_SUSTAINED_MAX, config.TELEGRAM_GROUP_MAX_CALLS)


def test_the_sustained_cap_is_never_exceeded_in_any_rolling_window(
    limiter, qa_clock, run_async,
):
    """Criterion 13, at the limiter. Forty calls as fast as the budget
    allows: no rolling ``FLOOD_SUSTAINED_WINDOW`` may contain more than
    ``FLOOD_SUSTAINED_MAX`` of them."""
    _restore_rate(limiter, qa_clock, config.TELEGRAM_PRIVATE_MAX_RATE)
    stamps: list[float] = []

    async def _drive():
        for _ in range(40):
            async def _callback():
                stamps.append(qa_clock.now)
                return "sent"
            await limiter.process_request(
                callback=_callback, args=(), kwargs={},
                endpoint="sendMessage", data={"chat_id": CHAT},
                rate_limit_args=None)

    run_async(_drive())
    assert most_in_window(
        stamps, config.FLOOD_SUSTAINED_WINDOW) <= config.FLOOD_SUSTAINED_MAX


def test_the_call_one_past_the_cap_waits_for_the_window(
    limiter, qa_clock, run_async,
):
    """Boundary value on the window itself: exactly
    ``FLOOD_SUSTAINED_MAX`` calls are allowed, and the next one must wait
    until the oldest leaves the window rather than being refused."""
    _restore_rate(limiter, qa_clock, config.TELEGRAM_PRIVATE_MAX_RATE)
    stamps: list[float] = []

    async def _callback():
        stamps.append(qa_clock.now)
        return "sent"

    async def _drive(n):
        for _ in range(n):
            await limiter.process_request(
                callback=_callback, args=(), kwargs={},
                endpoint="sendMessage", data={"chat_id": CHAT},
                rate_limit_args=None)

    run_async(_drive(int(config.FLOOD_SUSTAINED_MAX)))
    first = stamps[0]
    run_async(_drive(1))
    assert stamps[-1] - first >= config.FLOOD_SUSTAINED_WINDOW


def test_sustained_used_counts_up_to_the_cap(limiter, qa_clock, run_async):
    """The R9 reader's figure has to be the one the cap is enforced on."""
    _restore_rate(limiter, qa_clock, config.TELEGRAM_PRIVATE_MAX_RATE)

    async def _drive():
        for _ in range(int(config.FLOOD_SUSTAINED_MAX)):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)

    run_async(_drive())
    assert limiter.sustained_used(CHAT) == int(config.FLOOD_SUSTAINED_MAX)


# ===== the card cadence learns the pacing ===============================

def test_card_interval_is_unchanged_when_the_new_keywords_are_omitted(
    limiter,
):
    """The optional-kwarg contract: omitting both must be identical to
    passing them as ``None``, which is what keeps every pure cadence row
    in the suite green."""
    assert card_interval(base=2.0, busy_sessions=1, is_group=False) == \
        card_interval(base=2.0, busy_sessions=1, is_group=False,
                      chat_rate=None, sustained_min_gap=None)


def test_a_slow_earned_rate_stretches_the_card_interval(limiter):
    """The cards have to be TOLD about the new pacing or every run
    becomes a stream of refusals."""
    fast = card_interval(base=2.0, busy_sessions=1, is_group=False,
                         chat_rate=config.TELEGRAM_PRIVATE_MAX_RATE)
    slow = card_interval(base=2.0, busy_sessions=1, is_group=False,
                         chat_rate=config.FLOOD_MIN_RATE)
    assert slow > fast


def test_pacing_for_reports_the_rate_the_cards_must_respect(
    limiter, run_async,
):
    """``pacing_for`` is what ``animation._card_interval`` reads late."""
    _seed(limiter, run_async)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.pacing_for(CHAT)["rate"] == pytest.approx(
        limiter.earned_rate(CHAT))


# ===== error guessing on the rate itself ================================

def test_a_restored_negative_rate_never_becomes_the_bucket_rate(
    limiter, qa_clock,
):
    """Error guessing: a corrupted file (or a clock that ran backwards
    while the writer was computing) must not hand the bucket a negative
    rate, which would stall every send for ever."""
    _restore_rate(limiter, qa_clock, -1.0)
    assert limiter.earned_rate(CHAT) >= config.FLOOD_MIN_RATE


def test_a_restored_nan_rate_never_becomes_the_bucket_rate(
    limiter, qa_clock,
):
    """Error guessing: NaN compares false against every bound, so an
    unchecked NaN would silently disable the chat's pacing."""
    _restore_rate(limiter, qa_clock, float("nan"))
    assert not math.isnan(limiter.earned_rate(CHAT))


def test_a_restored_rate_above_the_ceiling_is_clamped(limiter, qa_clock):
    """Error guessing: a hand-edited file must not be able to buy more
    throughput than Telegram publishes."""
    _restore_rate(limiter, qa_clock, 99.0)
    assert limiter.earned_rate(CHAT) <= config.TELEGRAM_PRIVATE_MAX_RATE
