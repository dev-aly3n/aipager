"""Pure policy helpers (entrypoints.md ``aipager.flood_policy`` and
``flood_budget.card_interval``): boundary values of every breakpoint the
design names — tier ages 120 / 600 / 3600 s, the 7-day ban memory, the
``1 + bans`` divisor and the warned ceiling. Rows D, H, I, R, S at their
edges, which the scenario rows only sample in the middle of each tier.
"""

from __future__ import annotations

import math

import pytest

from aipager import config
from aipager.bot.flood_budget import card_interval
from aipager.flood_policy import (
    bans_within,
    card_age_floor,
    effective_ceiling,
    elapsed_unit,
    format_elapsed,
    hourly_limits,
)

DAY = 86400.0
WEEK = 7 * DAY
NOW = 1_800_000_000.0


# ── card_age_floor: the refresh tiers (row D) ────────────────────────────────

@pytest.mark.parametrize("age,floor", [
    (0.0, 0.0),
    (119.9, 0.0),          # just inside tier 0
    (120.0, 10.0),         # the breakpoint itself is tier 1
    (120.1, 10.0),
    (599.9, 10.0),
    (600.0, 30.0),
    (3599.9, 30.0),
    (3600.0, 60.0),
    (86400.0 * 3, 60.0),   # no tier beyond 60 s
])
def test_card_age_floor_breakpoints(age, floor):
    assert card_age_floor(age, True) == floor


@pytest.mark.parametrize("age", [0.0, 119.9, 120.0, 600.0, 3600.0, 1e6])
def test_card_age_floor_is_zero_when_disabled(age):
    """Preference off: 0.7.13 cadence at every age (row S)."""
    assert card_age_floor(age, False) == 0.0


def test_card_age_floor_negative_age_is_tier_zero():
    """A clock step backwards (busy_started_at in the future) must not
    invent a slow tier — error guessing."""
    assert card_age_floor(-5.0, True) == 0.0


# ── elapsed_unit: the display unit follows the refresh (rows D, R) ───────────

@pytest.mark.parametrize("age,unit", [
    (0.0, "s"),
    (119.9, "s"),
    (120.0, "s"),          # the 10 s tier still shows seconds ("4m 10s")
    (599.9, "s"),
    (600.0, "m"),
    (3599.9, "m"),
    (3600.0, "h"),
    (1e6, "h"),
])
def test_elapsed_unit_breakpoints(age, unit):
    assert elapsed_unit(age, True) == unit


@pytest.mark.parametrize("age", [0.0, 600.0, 3600.0, 1e6])
def test_elapsed_unit_is_seconds_when_disabled(age):
    assert elapsed_unit(age, False) == "s"


# ── format_elapsed ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,unit,text", [
    (45.0, "s", "45s"),
    (250.0, "s", "4m 10s"),
    (0.0, "m", "<1m"),
    (59.9, "m", "<1m"),
    (60.0, "m", "1m"),
    (1380.0, "m", "23m"),
    (1380.0, "h", "23m"),          # under an hour, the h unit reads minutes
    (3600.0, "h", "1h 0m"),
    (4980.0, "h", "1h 23m"),
    (7259.0, "h", "2h 0m"),        # truncates, never rounds up
])
def test_format_elapsed(seconds, unit, text):
    assert format_elapsed(seconds, unit) == text


# ── bans_within: the 7-day memory (rows H, I) ────────────────────────────────

def test_bans_within_counts_a_stamp_exactly_seven_days_old():
    """``0 <= now - stamp <= seconds``: the boundary is inclusive."""
    assert bans_within([NOW - WEEK], NOW, WEEK) == 1


def test_bans_within_forgets_a_stamp_just_over_seven_days_old():
    assert bans_within([NOW - WEEK - 0.001], NOW, WEEK) == 0


def test_bans_within_counts_a_stamp_from_now():
    assert bans_within([NOW], NOW, WEEK) == 1


def test_bans_within_ignores_a_future_stamp():
    assert bans_within([NOW + 1.0], NOW, WEEK) == 0


def test_bans_within_skips_non_numeric_entries():
    stamps = [NOW - DAY, "yesterday", None, [NOW], {"t": NOW}, NOW - 2 * DAY]
    assert bans_within(stamps, NOW, WEEK) == 2


def test_bans_within_skips_non_finite_entries():
    stamps = [float("nan"), float("inf"), float("-inf"), NOW - DAY]
    assert bans_within(stamps, NOW, WEEK) == 1


def test_bans_within_empty_is_zero():
    assert bans_within([], NOW, WEEK) == 0


# ── effective_ceiling (rows G, H, I) ─────────────────────────────────────────

def _ceiling(bans, warned, *, max_rate=1.0, min_rate=0.1):
    return effective_ceiling(max_rate=max_rate, min_rate=min_rate, bans=bans,
                             warned=warned, warned_ceiling=0.5)


def test_ceiling_clean_chat_is_max_rate():
    assert _ceiling(0, False) == 1.0


def test_ceiling_warned_chat_is_capped():
    assert _ceiling(0, True) == 0.5


def test_ceiling_one_ban_halves():
    assert _ceiling(1, False) == pytest.approx(0.5)


def test_ceiling_two_bans_thirds():
    assert _ceiling(2, False) == pytest.approx(1 / 3)


def test_ceiling_warned_and_two_bans_takes_the_lower():
    assert _ceiling(2, True) == pytest.approx(1 / 3)


def test_ceiling_never_below_min_rate():
    assert _ceiling(9, True, min_rate=0.2) == pytest.approx(0.2)


def test_ceiling_warned_never_raises_a_low_max_rate():
    """A group's lower max rate stays lower than the warned ceiling."""
    assert _ceiling(0, True, max_rate=0.3) == pytest.approx(0.3)


# ── hourly_limits: budget / ornament share by bans (rows H, I, Q7) ──────────

@pytest.mark.parametrize("bans,total,share", [
    (0, 1200, 1080),
    (1, 600, 540),
    (2, 400, 360),
    (3, 300, 270),
    (4, 240, 216),
    (6, 171, 154),      # floor(1200/7), floor(1080/7): floors, never rounds up
])
def test_hourly_limits(bans, total, share):
    assert hourly_limits(bans) == (total, share)


def test_hourly_limits_match_the_config_constants():
    assert hourly_limits(0) == (
        config.FLOOD_HOURLY_MAX,
        config.FLOOD_HOURLY_MAX - config.FLOOD_HOURLY_ESSENTIAL_RESERVE)


def test_hourly_limits_are_ints():
    total, share = hourly_limits(6)
    assert isinstance(total, int) and isinstance(share, int)


# ── card_interval(age_floor=) ────────────────────────────────────────────────

def _today(**kw):
    return card_interval(base=config.BUSY_EDIT_INTERVAL, busy_sessions=1,
                         is_group=False, **kw)


def test_card_interval_default_floor_is_todays_value():
    assert _today() == _today(age_floor=0.0)


def test_card_interval_takes_the_age_floor_when_larger():
    assert _today(age_floor=60.0) == 60.0


def test_card_interval_keeps_todays_value_when_floor_is_smaller():
    base = _today()
    assert _today(age_floor=base / 2) == base


def test_card_interval_is_finite():
    assert math.isfinite(_today(age_floor=10.0))
