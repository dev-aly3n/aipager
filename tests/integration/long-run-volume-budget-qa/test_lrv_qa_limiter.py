"""The limiter's public surface at its edges (entrypoints.md
``BudgetRateLimiter``): the hourly latches exactly at 810 / 648 / 1080 /
864, the rolling hour at its ends, per-chat isolation of every new state,
the warning regime's expiry, and the ban memory at exactly seven days.

Everything runs on the harness's :class:`FloodClock` (monotonic and wall
moved together). Latch edges are reached two ways: by live traffic, and by
``restore`` of an exact hour — ``restore`` is public, and it is the only
way to stand a chat at, say, exactly 648 calls with the typing latch on.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)

CHAT = 256113222
OTHER = 111222333
SHARE = config.FLOOD_HOURLY_MAX - config.FLOOD_HOURLY_ESSENTIAL_RESERVE  # 1080
DAY = 86400.0
HOUR = 3600.0
SKIP_CARD = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)
BLOCKING_ORNAMENT = rate_limit_args(kind="blocking", priority=PRIORITY_ORNAMENT)
TYPING = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)


class Wire:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def cb(self, endpoint, chat):
        async def _call():
            self.calls.append((endpoint, chat))
            return "sent"
        return _call


def req(limiter, wire, *, endpoint="sendMessage", args=None, chat=CHAT,
        callback=None):
    return limiter.process_request(
        callback=callback or wire.cb(endpoint, chat), args=(), kwargs={},
        endpoint=endpoint, data={"chat_id": chat}, rate_limit_args=args)


def fill(limiter, run_async, wire, n, *, chat=CHAT, args=None):
    async def _go():
        for _ in range(n):
            await req(limiter, wire, chat=chat, args=args)
    run_async(_go())


def stand_at(limiter, clock, used, **latches):
    """Put CHAT at exactly *used* counted calls, all in one bucket 30 s
    old, with the given latches as stored in the file."""
    row = {"chat_id": CHAT, "hourly": [[clock.wall - 30.0, 0, used]]}
    row.update(latches)
    assert limiter.restore([row]) == 1


def raises429(seconds):
    async def _boom():
        raise RetryAfter(int(seconds))
    return _boom


# ── the typing latch: on at >= 810, off below 648 (row L) ────────────────────

def test_typing_latch_off_at_809_live(limiter, run_async):
    fill(limiter, run_async, Wire(), 809)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is False


def test_typing_latch_on_at_810_live(limiter, run_async):
    fill(limiter, run_async, Wire(), 810)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is True


def test_typing_latch_on_at_810_restored_without_a_latch(limiter, flood_clock):
    stand_at(limiter, flood_clock, 810)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is True


def test_typing_latch_stays_on_at_exactly_648(limiter, flood_clock):
    """``resumes at used < 648``: 648 is still inside the band."""
    stand_at(limiter, flood_clock, 648, typing_shed=True)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is True


def test_typing_latch_lifts_at_647(limiter, flood_clock):
    stand_at(limiter, flood_clock, 647, typing_shed=True)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is False


def test_typing_latch_off_in_the_band_stays_off(limiter, flood_clock):
    """Hysteresis runs both ways: at 700 with the latch off, nothing trips
    it (it enters only at 810)."""
    stand_at(limiter, flood_clock, 700, typing_shed=False)
    assert limiter.hourly_usage(CHAT)["typing_shed"] is False


def test_typing_is_refused_in_the_band_while_latched(
    limiter, flood_clock, run_async,
):
    stand_at(limiter, flood_clock, 700, typing_shed=True)
    flood_clock.advance(10.0)
    with pytest.raises(FloodSkipped):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING))


def test_typing_is_sent_in_the_band_when_not_latched(
    limiter, flood_clock, run_async,
):
    stand_at(limiter, flood_clock, 700, typing_shed=False)
    flood_clock.advance(10.0)
    assert run_async(req(limiter, Wire(), endpoint="sendChatAction",
                         args=TYPING)) == "sent"


def test_a_card_is_admitted_while_typing_is_shed(limiter, flood_clock, run_async):
    stand_at(limiter, flood_clock, 1000)
    flood_clock.advance(10.0)
    assert run_async(req(limiter, Wire(), endpoint="editMessageText",
                         args=SKIP_CARD)) == "sent"


# ── the minimal latch: on at >= 1080, off at <= 864 (rows F, M) ─────────────

def test_minimal_off_at_1079_live(limiter, run_async):
    fill(limiter, run_async, Wire(), SHARE - 1)
    assert limiter.minimal_mode(CHAT) is False


def test_minimal_on_at_1080_live(limiter, run_async):
    fill(limiter, run_async, Wire(), SHARE)
    assert limiter.minimal_mode(CHAT) is True


def test_minimal_stays_on_at_865(limiter, flood_clock):
    stand_at(limiter, flood_clock, 865, hourly_minimal=True)
    assert limiter.minimal_mode(CHAT) is True


def test_minimal_lifts_at_exactly_864(limiter, flood_clock):
    """``leave only when <= 80 %``: 864 is exactly 80 % of 1080."""
    stand_at(limiter, flood_clock, 864, hourly_minimal=True)
    assert limiter.minimal_mode(CHAT) is False


def test_minimal_not_entered_in_the_band(limiter, flood_clock):
    stand_at(limiter, flood_clock, 1000, hourly_minimal=False)
    assert limiter.minimal_mode(CHAT) is False


def test_minimal_entered_from_restore_at_1080_without_a_latch(
    limiter, flood_clock,
):
    stand_at(limiter, flood_clock, SHARE, hourly_minimal=False)
    assert limiter.minimal_mode(CHAT) is True


def test_hourly_usage_minimal_mirrors_minimal_mode(limiter, flood_clock):
    stand_at(limiter, flood_clock, 900, hourly_minimal=True)
    assert limiter.hourly_usage(CHAT)["minimal"] is True


# ── the ornament share is strict (Q7) ────────────────────────────────────────

def test_the_1080th_call_may_be_an_ornament(limiter, flood_clock, run_async):
    """At 1079 used the share is not spent: one skip card goes out."""
    wire = Wire()
    fill(limiter, run_async, wire, SHARE - 1)
    flood_clock.advance(10.0)
    assert run_async(req(limiter, wire, endpoint="editMessageText",
                         args=SKIP_CARD)) == "sent"


def test_the_1081st_call_may_not_be_an_ornament(limiter, flood_clock, run_async):
    wire = Wire()
    fill(limiter, run_async, wire, SHARE - 1)
    flood_clock.advance(10.0)
    run_async(req(limiter, wire, endpoint="editMessageText", args=SKIP_CARD))
    flood_clock.advance(10.0)
    with pytest.raises(FloodSkipped):
        run_async(req(limiter, wire, endpoint="editMessageText", args=SKIP_CARD))


def test_a_blocking_ornament_with_the_share_spent_is_refused_not_parked(
    limiter, flood_clock, run_async,
):
    """Error guessing: a BLOCKING ornament must not wait an hour for the
    window to free — it raises ``FloodSkipped`` promptly."""
    fill(limiter, run_async, Wire(), SHARE)
    t0 = flood_clock.now

    async def _go():
        return await asyncio.wait_for(
            req(limiter, Wire(), endpoint="editMessageText",
                args=BLOCKING_ORNAMENT), timeout=5.0)

    with pytest.raises(FloodSkipped):
        run_async(_go())
    assert flood_clock.now - t0 < 60.0


def test_an_answer_is_delivered_with_the_share_spent(limiter, flood_clock,
                                                     run_async):
    wire = Wire()
    fill(limiter, run_async, wire, SHARE)
    flood_clock.advance(10.0)
    assert run_async(req(limiter, wire)) == "sent"


def test_ornament_used_never_exceeds_the_share_under_mixed_traffic(
    limiter, flood_clock, run_async,
):
    """Every attempt is an ornament until refused: the hour's ornament
    count stops at the share exactly (refusals are not counted)."""
    wire = Wire()

    async def _go():
        for _ in range(SHARE + 200):
            try:
                await req(limiter, wire, endpoint="editMessageText",
                          args=BLOCKING_ORNAMENT)
            except FloodSkipped:
                pass
    run_async(_go())
    assert limiter.hourly_usage(CHAT)["ornament_used"] <= SHARE


# ── the rolling hour at its ends ─────────────────────────────────────────────

def test_a_call_is_still_counted_just_before_an_hour(limiter, flood_clock,
                                                     run_async):
    """Never forgotten EARLY: made at the start of a minute, still counted
    3599 s later."""
    flood_clock.advance(60.0 - (flood_clock.now % 60.0) + 0.001)
    run_async(req(limiter, Wire()))
    flood_clock.advance(3599.0)
    assert limiter.hourly_usage(CHAT)["used"] == 1


def test_a_call_is_gone_after_an_hour_and_a_minute(limiter, flood_clock,
                                                   run_async):
    """Forgotten at the latest one bucket (60 s) past the hour."""
    run_async(req(limiter, Wire()))
    flood_clock.advance(3660.1)
    assert limiter.hourly_usage(CHAT)["used"] == 0


def test_a_long_quiet_gap_empties_the_hour(limiter, flood_clock, run_async):
    fill(limiter, run_async, Wire(), 50)
    flood_clock.advance(5 * DAY)
    assert limiter.hourly_usage(CHAT)["used"] == 0


def test_the_hour_frees_the_minimal_latch_after_it_rolls(limiter, flood_clock,
                                                         run_async):
    fill(limiter, run_async, Wire(), SHARE)
    flood_clock.advance(2 * HOUR)
    assert limiter.minimal_mode(CHAT) is False


# ── per chat, never global ───────────────────────────────────────────────────

def test_one_chats_hour_does_not_count_against_another(limiter, run_async):
    fill(limiter, run_async, Wire(), 100)
    assert limiter.hourly_usage(OTHER)["used"] == 0


def test_one_chat_in_hourly_minimal_leaves_another_free(limiter, run_async):
    fill(limiter, run_async, Wire(), SHARE)
    assert limiter.minimal_mode(OTHER) is False


def test_a_card_in_another_chat_is_admitted_when_one_chat_is_spent(
    limiter, flood_clock, run_async,
):
    fill(limiter, run_async, Wire(), SHARE)
    assert run_async(req(limiter, Wire(), chat=OTHER, endpoint="editMessageText",
                         args=SKIP_CARD)) == "sent"


def test_a_429_in_one_chat_does_not_warn_another(limiter, run_async):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    assert limiter.warning_remaining(OTHER) == 0.0


def test_a_ban_in_one_chat_does_not_lower_anothers_ceiling(limiter, run_async):
    run_async(req(limiter, Wire()))
    limiter.note_ban(CHAT, 1283.0)
    assert limiter.ceiling_for(OTHER) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_a_typing_429_in_one_chat_does_not_block_anothers_bubble(
    limiter, flood_clock, run_async,
):
    wire = Wire()
    with pytest.raises(RetryAfter):
        run_async(req(limiter, wire, endpoint="sendChatAction", args=TYPING,
                      callback=raises429(20)))
    flood_clock.advance(1.0)
    assert run_async(req(limiter, wire, chat=OTHER, endpoint="sendChatAction",
                         args=TYPING)) == "sent"


# ── the warning regime (rows G, J) ───────────────────────────────────────────

def test_warning_remaining_is_six_hours_at_arming(limiter, run_async):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    assert limiter.warning_remaining(CHAT) == pytest.approx(
        config.FLOOD_WARNING_HOURS * HOUR)


def test_the_ceiling_is_warned_one_minute_before_expiry(limiter, flood_clock,
                                                         run_async):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(config.FLOOD_WARNING_HOURS * HOUR - 60.0)
    assert limiter.ceiling_for(CHAT) == pytest.approx(config.FLOOD_WARNED_CEILING)


def test_the_ceiling_is_restored_one_second_after_expiry(limiter, flood_clock,
                                                         run_async):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(config.FLOOD_WARNING_HOURS * HOUR + 1.0)
    assert limiter.ceiling_for(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_warning_remaining_is_zero_after_expiry_not_negative(
    limiter, flood_clock, run_async,
):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(config.FLOOD_WARNING_HOURS * HOUR + 1.0)
    assert limiter.warning_remaining(CHAT) == 0.0


def test_a_later_429_extends_the_regime_from_its_own_time(
    limiter, flood_clock, run_async,
):
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(2 * HOUR)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.warning_remaining(CHAT) == pytest.approx(
        config.FLOOD_WARNING_HOURS * HOUR)


def test_the_regime_runs_on_the_wall_clock_not_the_monotonic_one(
    limiter, flood_clock, run_async,
):
    """Error guessing: a suspended laptop — wall time jumps 7 h, the
    monotonic clock does not. The regime (wall-clock state) is over."""
    run_async(req(limiter, Wire()))
    limiter.note_retry_after(CHAT, 5)
    flood_clock.wall += 7 * HOUR
    assert limiter.warning_remaining(CHAT) == 0.0


def test_a_typing_429_clamps_a_rate_above_the_warned_ceiling(
    limiter, flood_clock, run_async,
):
    limiter.restore([{"chat_id": CHAT, "rate": 1.0,
                      "rate_earned_at": flood_clock.wall}])
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(5)))
    assert limiter.earned_rate(CHAT) <= config.FLOOD_WARNED_CEILING + 1e-9


def test_a_typing_429_does_not_halve_a_rate_already_under_the_ceiling(
    limiter, flood_clock, run_async,
):
    """Q5: the typing 429 arms the regime (a ceiling CLAMP) — it is not
    the AIMD halving the budgeted 429 does."""
    limiter.restore([{"chat_id": CHAT, "rate": 0.4,
                      "rate_earned_at": flood_clock.wall}])
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(5)))
    assert limiter.earned_rate(CHAT) == pytest.approx(0.4)


def test_a_typing_429_arms_the_regime(limiter, run_async):
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(5)))
    assert limiter.warning_remaining(CHAT) == pytest.approx(
        config.FLOOD_WARNING_HOURS * HOUR)


def test_a_typing_429_does_not_arm_minimal_mode(limiter, run_async):
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(5)))
    assert limiter.minimal_mode(CHAT) is False


def test_the_bubble_is_allowed_just_after_its_retry_after(
    limiter, flood_clock, run_async,
):
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(20)))
    flood_clock.advance(20.5)
    assert run_async(req(limiter, Wire(), endpoint="sendChatAction",
                         args=TYPING)) == "sent"


def test_the_bubble_is_refused_just_before_its_retry_after(
    limiter, flood_clock, run_async,
):
    with pytest.raises(RetryAfter):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING,
                      callback=raises429(20)))
    flood_clock.advance(19.5)
    with pytest.raises(FloodSkipped):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING))


# ── the ban memory at its edge (rows H, I) ───────────────────────────────────

def test_a_ban_just_under_seven_days_old_still_halves_the_ceiling(
    limiter, flood_clock,
):
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [flood_clock.wall - 7 * DAY + 60.0]}])
    assert limiter.ceiling_for(CHAT) == pytest.approx(0.5)


def test_a_ban_just_over_seven_days_old_is_forgotten(limiter, flood_clock):
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [flood_clock.wall - 7 * DAY - 60.0]}])
    assert limiter.ceiling_for(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_a_ban_ages_out_of_the_hourly_budget_too(limiter, flood_clock):
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [flood_clock.wall - 7 * DAY - 60.0]}])
    assert limiter.hourly_usage(CHAT)["budget"] == config.FLOOD_HOURLY_MAX


def test_bans_remembered_counts_the_week(limiter, flood_clock):
    w = flood_clock.wall
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [w - 8 * DAY, w - 6 * DAY, w - DAY]}])
    assert limiter.bans_remembered(CHAT) == 2


def test_a_ban_aging_out_live_restores_the_budget(limiter, flood_clock,
                                                  run_async):
    run_async(req(limiter, Wire()))
    limiter.note_ban(CHAT, 1283.0)
    flood_clock.advance(7 * DAY + 60.0)
    assert limiter.hourly_usage(CHAT)["budget"] == config.FLOOD_HOURLY_MAX


def test_three_bans_quarter_the_budget(limiter, flood_clock):
    w = flood_clock.wall
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [w - 3 * DAY, w - 2 * DAY, w - DAY]}])
    usage = limiter.hourly_usage(CHAT)
    assert (usage["budget"], usage["ornament_budget"]) == (300, 270)


def test_a_banned_chats_cards_stop_at_its_smaller_share(
    limiter, flood_clock, run_async,
):
    """One ban this week: minimal at 540, not 1080."""
    limiter.restore([{"chat_id": CHAT, "ban_stamps": [flood_clock.wall - DAY]}])
    fill(limiter, run_async, Wire(), 540)
    assert limiter.minimal_mode(CHAT) is True


def test_the_ceiling_never_drops_below_the_minimal_floor_math(limiter,
                                                              flood_clock):
    """Five bans in a week: 1/6 per design's Risks — positive and finite,
    never zero (a zero ceiling would stall answers forever)."""
    w = flood_clock.wall
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [w - k * DAY for k in range(1, 6)]}])
    assert limiter.ceiling_for(CHAT) > 0.0


# ── unseen chats ─────────────────────────────────────────────────────────────

def test_an_unseen_chat_has_no_warning(limiter):
    assert limiter.warning_remaining(-777) == 0.0


def test_an_unseen_chat_has_no_bans(limiter):
    assert limiter.bans_remembered(-777) == 0


def test_an_unseen_dm_has_the_full_ceiling(limiter):
    assert limiter.ceiling_for(777) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


# ── the gate still refuses the bubble into a ban (row K / R7) ────────────────

def test_the_bubble_is_refused_into_a_muted_chat(limiter, run_async):
    """Entrypoints: every endpoint is gated by the mute, ``sendChatAction``
    included — refused as a ban (``FloodMuted``), not skipped."""
    MUTE.mute(CHAT, 25429.0)
    with pytest.raises(FloodMuted):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING))


def test_no_bubble_callback_runs_into_a_muted_chat(limiter, run_async):
    wire = Wire()
    MUTE.mute(CHAT, 25429.0)
    try:
        run_async(req(limiter, wire, endpoint="sendChatAction", args=TYPING))
    except (FloodMuted, FloodSkipped):
        pass
    assert wire.calls == []


def test_the_bubble_is_refused_into_a_mute_even_with_the_hour_empty(
    limiter, flood_clock, run_async,
):
    """The mute gate comes before any budget: a full bucket and an empty
    hour do not let the bubble through."""
    MUTE.mute(CHAT, 25429.0)
    flood_clock.advance(30.0)
    with pytest.raises(FloodMuted):
        run_async(req(limiter, Wire(), endpoint="sendChatAction", args=TYPING))
