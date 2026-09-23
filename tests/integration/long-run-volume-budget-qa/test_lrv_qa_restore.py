"""Row J and X at their edges: ``serialise`` → ``restore`` round-trips
more than one chat, and ``restore`` swallows hostile state — never raises,
never yields "unlimited", never puts a healthy chat in minimal mode.
"""

from __future__ import annotations

import json
import math

import pytest

from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
OTHER = 111222333
HOUR = 3600.0
DAY = 86400.0


async def _ok():
    return "sent"


def fill(limiter, run_async, n, chat=CHAT):
    async def _go():
        for _ in range(n):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": chat}, rate_limit_args=None)
    run_async(_go())


def fresh(clock):
    return BudgetRateLimiter(clock=clock, sleep=clock.sleep,
                             wall_clock=clock.wall_clock,
                             signal_path=config.FLOOD_BACKOFF_FILE)


# ── round trip ───────────────────────────────────────────────────────────────

def _two_chats(limiter, clock, run_async):
    fill(limiter, run_async, 40, CHAT)
    fill(limiter, run_async, 7, OTHER)
    limiter.note_retry_after(OTHER, 5)


def test_round_trip_keeps_each_chats_hour(limiter, flood_clock, run_async):
    _two_chats(limiter, flood_clock, run_async)
    rows = limiter.serialise()
    second = fresh(flood_clock)
    second.restore(rows)
    assert (second.hourly_usage(CHAT)["used"],
            second.hourly_usage(OTHER)["used"]) == (40, 7)


def test_round_trip_keeps_the_warning_on_its_own_chat(limiter, flood_clock,
                                                      run_async):
    _two_chats(limiter, flood_clock, run_async)
    second = fresh(flood_clock)
    second.restore(limiter.serialise())
    assert (second.warning_remaining(CHAT) == 0.0
            and second.warning_remaining(OTHER) > 0.0)


def test_serialise_is_strict_json(limiter, flood_clock, run_async):
    _two_chats(limiter, flood_clock, run_async)
    text = json.dumps(limiter.serialise(), allow_nan=False)
    assert json.loads(text)


def test_a_restart_after_the_regime_expired_is_not_warned(
    limiter, flood_clock, run_async,
):
    fill(limiter, run_async, 1)
    limiter.note_retry_after(CHAT, 5)
    rows = limiter.serialise()
    flood_clock.advance(config.FLOOD_WARNING_HOURS * HOUR + 1.0)
    second = fresh(flood_clock)
    second.restore(rows)
    assert second.warning_remaining(CHAT) == 0.0


def test_a_restart_two_hours_later_forgets_the_hour(limiter, flood_clock,
                                                    run_async):
    fill(limiter, run_async, 300)
    rows = limiter.serialise()
    flood_clock.advance(2 * HOUR)
    second = fresh(flood_clock)
    second.restore(rows)
    assert second.hourly_usage(CHAT)["used"] == 0


def test_a_restart_keeps_minimal_mode_when_the_share_is_spent(
    limiter, flood_clock, run_async,
):
    fill(limiter, run_async, 1080)
    rows = limiter.serialise()
    second = fresh(flood_clock)
    second.restore(rows)
    assert second.minimal_mode(CHAT) is True


def test_the_state_file_round_trip_keeps_the_hour(flood_clock, run_async):
    """Through the durable file (the isolated path), as the daemon does."""
    import aipager.bot.rich_message as rm

    first = fresh(flood_clock)
    rm.set_rate_limiter(first)
    fill(first, run_async, 25)
    assert flood_state.save_if_dirty(force=True)
    second = fresh(flood_clock)
    rm.set_rate_limiter(second)
    assert flood_state.load() is True
    assert second.hourly_usage(CHAT)["used"] == 25


# ── hostile input: restore never raises ──────────────────────────────────────

@pytest.mark.parametrize("chats", [
    None, "garbage", 42, {"chat_id": CHAT}, [None], [42], ["x"],
    [{"chat_id": "abc"}], [{"chat_id": None}], [{}],
    [{"chat_id": float("nan")}],
])
def test_restore_never_raises_on_hostile_rows(flood_clock, chats):
    fresh(flood_clock).restore(chats)


def test_restore_counts_only_the_good_rows(flood_clock):
    rows = [None, {"chat_id": CHAT, "hourly": [[flood_clock.wall - 5, 0, 3]]},
            "junk", {"chat_id": "abc"}]
    lim = fresh(flood_clock)
    lim.restore(rows)
    assert lim.hourly_usage(CHAT)["used"] == 3


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), -1.0, "many", None, [1, 2], {"a": 1},
])
def test_hostile_ban_stamps_never_raise(flood_clock, value):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "ban_stamps": value}])
    assert lim.bans_remembered(CHAT) == 0


def test_hostile_ban_stamps_mixed_with_real_ones_count_the_real(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "ban_stamps": [
        "x", None, float("nan"), flood_clock.wall - DAY]}])
    assert lim.bans_remembered(CHAT) == 1


def test_a_future_ban_stamp_does_not_make_the_ceiling_unlimited(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "ban_stamps": [flood_clock.wall + 1e9]}])
    assert lim.ceiling_for(CHAT) <= config.TELEGRAM_PRIVATE_MAX_RATE


@pytest.mark.parametrize("warned", [
    float("nan"), float("inf"), float("-inf"), -5.0, "soon", None, True,
])
def test_a_hostile_warned_until_leaves_a_finite_ceiling(flood_clock, warned):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "warned_until": warned}])
    assert math.isfinite(lim.ceiling_for(CHAT)) and lim.ceiling_for(CHAT) > 0


def test_a_warned_until_ten_minutes_ahead_is_kept(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "warned_until": flood_clock.wall + 600.0}])
    assert lim.warning_remaining(CHAT) == pytest.approx(600.0)


def test_a_warned_until_exactly_one_regime_ahead_is_kept(flood_clock):
    full = config.FLOOD_WARNING_HOURS * HOUR
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "warned_until": flood_clock.wall + full}])
    assert lim.warning_remaining(CHAT) == pytest.approx(full)


def test_a_warned_until_just_past_one_regime_is_clamped(flood_clock):
    full = config.FLOOD_WARNING_HOURS * HOUR
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT,
                  "warned_until": flood_clock.wall + full + 3600.0}])
    assert lim.warning_remaining(CHAT) == pytest.approx(full)


def test_a_warned_until_in_the_past_is_no_regime(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "warned_until": flood_clock.wall - 1.0}])
    assert lim.warning_remaining(CHAT) == 0.0


def test_a_restored_warning_clamps_a_restored_rate(flood_clock, run_async):
    """Restore order (design §6): warning before rate — a file with rate 1.0
    and a live regime must not climb above the warned ceiling."""
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "rate": 1.0,
                  "rate_earned_at": flood_clock.wall,
                  "warned_until": flood_clock.wall + 3600.0}])
    fill(lim, run_async, 1)
    assert lim.earned_rate(CHAT) <= config.FLOOD_WARNED_CEILING + 1e-9


def test_restored_bans_clamp_a_restored_rate(flood_clock, run_async):
    w = flood_clock.wall
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "rate": 1.0, "rate_earned_at": w,
                  "ban_stamps": [w - 2 * DAY, w - DAY]}])
    fill(lim, run_async, 1)
    assert lim.earned_rate(CHAT) <= 1 / 3 + 1e-9


def test_a_minimal_latch_restored_on_an_empty_hour_is_dropped(flood_clock):
    """A real True, but nothing in the hour: re-evaluated off."""
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly_minimal": True, "typing_shed": True}])
    assert lim.minimal_mode(CHAT) is False


def test_a_typing_latch_restored_on_an_empty_hour_is_dropped(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly_minimal": True, "typing_shed": True}])
    assert lim.hourly_usage(CHAT)["typing_shed"] is False


def test_many_absurd_buckets_are_bounded(flood_clock):
    """Sixty-one buckets each claiming 1e12 calls: clamped, finite, and
    the chat is (correctly) spent rather than overflowing to something
    that compares as "free"."""
    w = flood_clock.wall
    rows = [[w - 60.0 * k - 1.0, 10**12, 10**12] for k in range(61)]
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly": rows}])
    assert lim.minimal_mode(CHAT) is True


def test_duplicate_bucket_minutes_do_not_raise(flood_clock):
    w = flood_clock.wall
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly": [[w - 10, 1, 1], [w - 10, 2, 2]]}])
    assert lim.hourly_usage(CHAT)["used"] >= 0


def test_a_huge_hourly_list_does_not_raise(flood_clock):
    w = flood_clock.wall
    rows = [[w - (k % 3600), 1, 0] for k in range(20_000)]
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly": rows}])
    assert lim.hourly_usage(CHAT)["used"] >= 0


def test_restore_of_a_hostile_row_leaves_status_json_strict(flood_clock):
    lim = fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly": [[float("nan"), float("inf"), 1]],
                  "warned_until": float("inf"), "ban_stamps": [float("nan")]}])
    json.dumps(lim.snapshot(), allow_nan=False)
