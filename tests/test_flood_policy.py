"""The pure long-run flood policy (roadmap 8.30, ``aipager.flood_policy``).

Every rule here is shared by the daemon's limiter, the busy-card animator
and ``aipager status`` (another process), so each is pinned once, as
arithmetic, before any of those readers are. Every docstring names the
mutation that makes its row fail.
"""

from __future__ import annotations

import math

import pytest

from aipager import config, flood_policy
from aipager.flood_policy import (
    bans_within,
    card_age_floor,
    card_floor_beside_typing,
    clamp_warned_until,
    effective_ceiling,
    elapsed_unit,
    format_elapsed,
    hourly_limits,
    typing_interval,
)

WEEK = config.FLOOD_BAN_MEMORY_DAYS * 86400.0
NOW = 1_800_000_000.0


# ── the constants the contract fixes ─────────────────────────────────────────

def test_the_contract_numbers_are_the_configured_ones():
    """§8 Q1 / R5 / R6 / R4. A change to any of these is a change to the
    contract, so it has to be deliberate: this row names the one that
    moved."""
    assert config.FLOOD_HOURLY_MAX == 1200
    assert config.FLOOD_HOURLY_WINDOW == 3600.0
    assert config.FLOOD_HOURLY_ESSENTIAL_RESERVE == 120
    assert config.FLOOD_HOURLY_TYPING_SHED_AT == 0.75
    assert config.FLOOD_HOURLY_TYPING_RESUME_BELOW == 0.60
    assert config.FLOOD_HOURLY_MINIMAL_EXIT_AT == 0.80
    assert config.FLOOD_WARNING_HOURS == 6.0
    assert config.FLOOD_WARNED_CEILING == 0.5
    assert config.FLOOD_BAN_MEMORY_DAYS == 7.0
    assert (config.CARD_AGE_TIER1_AT, config.CARD_AGE_TIER1_INTERVAL) == (120.0, 10.0)
    assert (config.CARD_AGE_TIER2_AT, config.CARD_AGE_TIER2_INTERVAL) == (600.0, 30.0)
    assert (config.CARD_AGE_TIER3_AT, config.CARD_AGE_TIER3_INTERVAL) == (3600.0, 60.0)
    assert config.CARD_STATE_BYPASS_MIN_GAP == 10.0


def test_the_module_has_no_bot_dependency():
    """``aipager status`` imports this in a process with no daemon; it
    must stay free of ``aipager.bot`` (and so of telegram/httpx).

    Mutation: import anything from ``aipager.bot`` here and this names it.
    """
    import ast

    tree = ast.parse(open(flood_policy.__file__, encoding="utf-8").read())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert modules <= {"__future__", "math", "aipager.config"}, modules


# ── bans_within ──────────────────────────────────────────────────────────────

def test_a_ban_four_days_old_is_remembered_for_a_week():
    """R6: the memory is days, not hours. Mutation: pass 86400 as the
    window and the four-day-old stamp is forgotten."""
    stamps = [NOW - 4 * 86400.0]
    assert bans_within(stamps, NOW, WEEK) == 1
    assert bans_within(stamps, NOW, 86400.0) == 0


def test_a_ban_ages_out_after_the_window():
    """The memory decays by itself. Mutation: drop the upper bound and a
    ban from last month still divides the ceiling."""
    assert bans_within([NOW - WEEK - 1.0], NOW, WEEK) == 0
    assert bans_within([NOW - WEEK], NOW, WEEK) == 1


def test_a_future_stamp_is_not_a_ban():
    """A clock that moved is not a ban that happened. Mutation: drop the
    ``0 <=`` bound and a skewed file counts a ban nobody took."""
    assert bans_within([NOW + 60.0], NOW, WEEK) == 0


@pytest.mark.parametrize("junk", [
    "1800000000", None, True, float("nan"), float("inf"), [NOW], {"t": NOW},
])
def test_junk_stamps_are_skipped_not_raised_on(junk):
    """The status command feeds this straight out of a file. Mutation:
    drop the type check and a string stamp raises TypeError out of
    ``aipager status``."""
    assert bans_within([junk, NOW - 10.0], NOW, WEEK) == 1


def test_a_non_list_is_zero_bans():
    assert bans_within(None, NOW, WEEK) == 0
    assert bans_within("x", NOW, WEEK) == 0


# ── effective_ceiling ────────────────────────────────────────────────────────

def _ceiling(bans=0, warned=False):
    return effective_ceiling(
        max_rate=config.TELEGRAM_PRIVATE_MAX_RATE, min_rate=config.FLOOD_MIN_RATE,
        bans=bans, warned=warned, warned_ceiling=config.FLOOD_WARNED_CEILING)


def test_the_ceiling_is_divided_by_one_plus_bans():
    """R6. Mutation: a fixed half (0.7.13's ``_BANNED_CEILING_FRACTION``)
    and two bans leave the ceiling at 0.5, not a third."""
    assert _ceiling(0) == 1.0
    assert _ceiling(1) == 0.5
    assert _ceiling(2) == pytest.approx(1.0 / 3.0)


def test_the_warning_regime_caps_the_ceiling():
    """R5. Mutation: ignore ``warned`` and a chat that just took a 429
    may climb straight back to 1.0."""
    assert _ceiling(0, warned=True) == 0.5
    # The stricter of the two wins.
    assert _ceiling(2, warned=True) == pytest.approx(1.0 / 3.0)


def test_the_ceiling_never_drops_under_the_floor():
    """Five bans a week is 1/6 — under nothing, still above MIN. Twenty
    would be under it. Mutation: drop the ``max(min_rate, …)`` and the
    ceiling inverts under the floor."""
    assert _ceiling(100) == config.FLOOD_MIN_RATE


def test_a_negative_ban_count_is_none():
    assert _ceiling(-3) == 1.0


# ── hourly_limits ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bans,expected", [
    (0, (1200, 1080)), (1, (600, 540)), (2, (400, 360)), (3, (300, 270)),
])
def test_the_hourly_budget_is_divided_by_one_plus_bans(bans, expected):
    """R6 and row H/I, as arithmetic. Mutation: divide only the total and
    a banned chat's ornaments keep 1080 of its 600."""
    assert hourly_limits(bans) == expected


def test_the_hourly_limits_follow_the_arguments():
    """A limiter built with other limits is answered with ITS limits."""
    assert hourly_limits(0, hourly_max=100, reserve=10) == (100, 90)
    assert hourly_limits(1, hourly_max=101, reserve=10) == (50, 45)


# ── card_age_floor / elapsed_unit ────────────────────────────────────────────

@pytest.mark.parametrize("age,floor,unit", [
    (0.0, 0.0, "s"), (30.0, 0.0, "s"), (119.9, 0.0, "s"),
    (120.0, 10.0, "s"), (300.0, 10.0, "s"), (599.9, 10.0, "s"),
    (600.0, 30.0, "m"), (1800.0, 30.0, "m"), (3599.9, 30.0, "m"),
    (3600.0, 60.0, "h"), (7200.0, 60.0, "h"), (86400.0, 60.0, "h"),
])
def test_the_age_tiers(age, floor, unit):
    """R4's table, at and around each breakpoint. Mutation: move any
    breakpoint or interval and the row names the age."""
    assert card_age_floor(age, True) == floor
    assert elapsed_unit(age, True) == unit


@pytest.mark.parametrize("age", [30.0, 300.0, 1800.0, 7200.0])
def test_decay_off_is_today_at_every_age(age):
    """The ``card_age_decay`` preference, off: no floor, seconds for
    ever. Mutation: ignore ``enabled`` and the opt-out does nothing."""
    assert card_age_floor(age, False) == 0.0
    assert elapsed_unit(age, False) == "s"


@pytest.mark.parametrize("age", [-5.0, float("nan"), float("inf") * -1, None, "x"])
def test_no_age_is_not_an_old_age(age):
    assert card_age_floor(age, True) == 0.0
    assert elapsed_unit(age, True) == "s"


# ── format_elapsed ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,unit,text", [
    (0, "s", "0s"), (45, "s", "45s"), (250, "s", "4m 10s"), (7200, "s", "120m 0s"),
    (30, "m", "<1m"), (59.9, "m", "<1m"), (60, "m", "1m"), (1380, "m", "23m"),
    (30, "h", "<1m"), (1380, "h", "23m"), (3600, "h", "1h 0m"),
    (4980, "h", "1h 23m"), (7200, "h", "2h 0m"), (3780, "h", "1h 3m"),
])
def test_the_formats(seconds, unit, text):
    """Seconds is today's rule unchanged; minutes and hours drop the
    seconds the card is no longer refreshed often enough to show."""
    assert format_elapsed(seconds, unit) == text


def test_format_is_total_on_bad_input():
    assert format_elapsed(-4, "s") == "0s"
    assert format_elapsed(math.nan, "m") == "<1m"
    assert format_elapsed(90, "?") == "1m 30s"


# ── the warning regime read back from a file ────────────────────────────────

REGIME = config.FLOOD_WARNING_HOURS * 3600.0


def test_a_regime_inside_one_regime_is_kept_as_written():
    """A ``warned_until`` an hour ahead is an hour ahead."""
    assert clamp_warned_until(NOW + 3600.0, NOW) == NOW + 3600.0


def test_a_regime_years_ahead_is_clamped_to_one_regime():
    """Both readers of the file (the daemon's ``restore`` and ``aipager
    status``) share this clamp. Mutation: drop the ``min`` and a file
    written before a clock jump reads as a regime a year long."""
    assert clamp_warned_until(NOW + 3e7, NOW) == NOW + REGIME


def test_the_clamp_follows_its_argument():
    assert clamp_warned_until(NOW + 3e7, NOW, warning_seconds=60.0) == NOW + 60.0


def test_a_numeric_string_reads_as_its_number():
    """As ``flood_budget._finite`` reads every other field of the file."""
    assert clamp_warned_until(str(NOW + 60.0), NOW) == NOW + 60.0


@pytest.mark.parametrize("junk", [
    float("nan"), float("inf"), -5.0, 0.0, "soon", None, True, [1], {},
])
def test_an_unreadable_regime_is_no_regime(junk):
    assert clamp_warned_until(junk, NOW) == 0.0


# ── typing's share of a chat (operator ruling 2026-09-23) ──────────────────

DM_TYPING_GAP = 60.0 / 27        # the DM window less the bubble's 3 slots
GROUP_TYPING_GAP = 60.0 / 17     # the group's 20 less the same 3


def test_a_dm_reserves_the_bubble_before_the_cards_divide_it():
    """0.45 calls/s less one bubble per 4.5 s leaves 0.2278/s for the
    cards: a floor of 4.39 s. Mutation: do not subtract the bubble and
    this reads 2.22 s."""
    assert card_floor_beside_typing(DM_TYPING_GAP, 4.5) == pytest.approx(
        1.0 / (27 / 60 - 1 / 4.5))


def test_a_chat_too_slow_for_both_keeps_the_cards_at_one_edit_per_tier_one():
    """A group (0.283/s) or a chat penalised to 0.25/s cannot fit a 4.5 s
    bubble beside a useful card: the cards keep one edit per
    ``CARD_AGE_TIER1_INTERVAL`` together and the bubble takes the rest.
    Mutation: drop the floor and a group card edits once every 16 s — a
    penalised DM's once every 36 s."""
    tier1 = config.CARD_AGE_TIER1_INTERVAL
    assert card_floor_beside_typing(GROUP_TYPING_GAP, 4.5) == pytest.approx(tier1)
    assert card_floor_beside_typing(4.0, 4.5) == pytest.approx(tier1)


def test_a_chat_slower_than_the_floor_keeps_its_own_pace():
    """A chat at 0.05/s cannot give its cards 0.1/s: the floor is capped
    at the chat's own capacity, never faster than the chat."""
    assert card_floor_beside_typing(20.0, 4.5) == pytest.approx(20.0)


@pytest.mark.parametrize("floor,interval", [
    (2.0, 0.0), (2.0, -1.0), (0.0, 4.5), (float("nan"), 4.5),
    (2.0, float("inf")), ("x", 4.5),
])
def test_nothing_is_reserved_for_a_bubble_that_is_not_there(floor, interval):
    """The input floor comes back untouched (``is``: NaN is not ``==``
    itself)."""
    assert card_floor_beside_typing(floor, interval) is floor


def test_the_card_interval_carries_the_reservation():
    """``card_interval(typing_interval=)`` is the ONE place the animator
    reads it: 4.83 s for a DM card planned against the window less the
    bubble's reserve, and exactly today's 2.2 s without it."""
    from aipager.bot.flood_budget import card_interval

    kw = dict(base=1.2, busy_sessions=1, is_group=False, chat_rate=1.0)
    assert card_interval(sustained_min_gap=2.0, **kw) == pytest.approx(2.2)
    assert card_interval(sustained_min_gap=DM_TYPING_GAP, typing_interval=4.5,
                         **kw) == pytest.approx(4.83, abs=0.01)
    assert card_interval(sustained_min_gap=DM_TYPING_GAP, typing_interval=4.5,
                         **{**kw, "busy_sessions": 2}) == pytest.approx(
        9.66, abs=0.01)


# ── the bubble's own decay (operator ruling #2) ──────────────────────────────

@pytest.mark.parametrize("age,expected", [
    (0.0, 4.5), (599.9, 4.5), (600.0, 9.0), (3599.9, 9.0), (3600.0, 15.0),
    (4 * 3600.0, 15.0),
])
def test_the_typing_tiers(age, expected):
    """4.5 s to 10 min, 9 s to an hour, 15 s after — at the card's own
    tier-2/3 boundaries. Mutation: shift a boundary or a value."""
    assert config.TYPING_AGE_TIER2_INTERVAL == 9.0
    assert config.TYPING_AGE_TIER3_INTERVAL == 15.0
    assert typing_interval(age, True) == expected


@pytest.mark.parametrize("age", [0.0, 700.0, 7200.0])
def test_typing_decay_off_is_the_base_pace(age):
    assert typing_interval(age, False) == config.TYPING_INDICATOR_INTERVAL


def test_the_bubble_is_never_faster_than_its_base():
    """A base configured above a tier's value wins: decay only slows."""
    assert typing_interval(700.0, True, base=12.0) == 12.0


@pytest.mark.parametrize("base", [0.0, -1.0])
def test_a_disabled_bubble_stays_disabled(base):
    assert typing_interval(7200.0, True, base=base) == base


@pytest.mark.parametrize("age", [float("nan"), -5.0, "x", None])
def test_no_age_is_a_young_turn_for_the_bubble(age):
    assert typing_interval(age, True) == config.TYPING_INDICATOR_INTERVAL
