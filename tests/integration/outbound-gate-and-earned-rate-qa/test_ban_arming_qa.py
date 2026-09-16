"""T1 — a ban is evidence of bad behaviour, never credited as quiet time.

The iteration-1 defect this directory reported (tester-iter1-001): the
AIMD controller learned only from calls that reached ``process_request``,
but a mute means NO call reaches it, so the muted hours looked like an
unbroken quiet window and the chat left a 9.5-hour ban at the CEILING with
``bans_today == 0``. That is the 2026-09-15 escalation ladder
(1283 → 312 → 34212) reproduced inside the fix meant to end it.

The coordinator's fix has three parts and each is probed separately here,
so a row that passes names WHICH mechanism holds:

* **(a)** the rate drops when the ban is ARMED (``MUTE.mute``), with no
  call involved at all;
* **(b)** the muted interval is EXCLUDED from the success window;
* **(c)** the first success window after a lift is at least a full one.

Everything goes through the documented surface — ``flood.MUTE``,
``BudgetRateLimiter.earned_rate/snapshot/serialise`` and ``flood_state`` —
and every assertion is against a NAMED constant.
"""

from __future__ import annotations

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
UNSEEN = -1009988776
WINDOW = config.FLOOD_SUCCESS_WINDOW_SECONDS
CEILING = config.TELEGRAM_PRIVATE_MAX_RATE
FLOOR = config.FLOOD_MIN_RATE

#: The real ladder of 2026-09-15 plus the fourth ban the coordinator added.
LADDER = (1283.0, 312.0, 34212.0, 34212.0)


async def _ok():
    return "sent"


def _seed(limiter, run_async, chat=CHAT):
    """Make the chat exist in the limiter (budgets are created lazily)."""
    run_async(limiter.process_request(
        callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": chat}, rate_limit_args=None))


def _serve(qa_clock, seconds: float, chat=CHAT) -> None:
    """Sit out a ban to its end and let the lift actually happen.

    ``MUTE.is_muted`` is a destructive read: the deadline lapsing is not
    enough, something has to LOOK before the entry is forgotten and the
    "lifted" INFO is logged. The daemon's monitor does that every 2 s.
    """
    qa_clock.advance(seconds + 1.0)
    assert MUTE.is_muted(chat) is False


def _bans_today(limiter, chat=CHAT) -> int:
    rows = {row["chat_id"]: row for row in limiter.snapshot()["chats"]}
    return rows[chat]["bans_today"]


# ===== (a) the drop happens at ARM time, with no call involved ==========

def test_a_healthy_chat_really_is_at_the_ceiling_before_the_ban(
    limiter, qa_clock, run_async,
):
    """The control every row below leans on. Without it "the ban put the
    chat on the floor" passes for a chat that was never above it."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    assert limiter.earned_rate(CHAT) == pytest.approx(CEILING)


def test_arming_a_ban_drops_the_rate_with_no_call_involved(
    limiter, qa_clock, run_async,
):
    """T1(a). ``MUTE.mute`` is the ONLY thing that happens here — no send,
    no 429, nothing that reaches ``process_request``. On iteration 1 the
    rate was untouched at this instant, which is how a ban could be
    forgotten entirely."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    MUTE.mute(CHAT, LADDER[0])
    assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


def test_arming_a_ban_records_it_for_the_operator_immediately(
    limiter, qa_clock, run_async,
):
    """The same instant, on the other observable: a ban nobody tried to
    send through still has to be COUNTED, or `aipager status` reports a
    clean day through a 9.5-hour ban."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[0])
    assert _bans_today(limiter) == 1


def test_a_ban_for_a_chat_the_limiter_has_never_seen_raises_nothing(
    limiter,
):
    """Error guessing: the first thing a fresh daemon may learn about a
    chat is that it is banned. Arming must not need a budget to exist."""
    MUTE.mute(UNSEEN, LADDER[0])
    assert limiter.earned_rate(UNSEEN) == pytest.approx(FLOOR)


def test_a_ban_for_an_unseen_chat_is_still_remembered_after_it_lifts(
    limiter, qa_clock,
):
    """…and the memory has to survive the lift, or the never-seen chat is
    exactly the one that resumes at full speed."""
    MUTE.mute(UNSEEN, LADDER[1])
    _serve(qa_clock, LADDER[1], chat=UNSEEN)
    assert limiter.earned_rate(UNSEEN) <= config.FLOOD_START_RATE


@pytest.mark.parametrize("entry_rate", [
    config.FLOOD_MIN_RATE,
    config.FLOOD_MINIMAL_MODE_RATE_FLOOR,
    config.FLOOD_START_RATE,
    config.TELEGRAM_PRIVATE_MAX_RATE,
])
def test_a_chat_never_leaves_a_ban_faster_than_it_entered_it(
    limiter, qa_clock, entry_rate,
):
    """T1's first named test, across the whole rate partition: "a chat
    that enters a ban at rate R leaves it at ``FLOOD_MIN_RATE``, never
    above R"."""
    limiter.restore([{
        "chat_id": CHAT, "rate": entry_rate, "rate_earned_at": qa_clock.wall,
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }])
    MUTE.mute(CHAT, LADDER[2])
    _serve(qa_clock, LADDER[2])
    assert limiter.earned_rate(CHAT) <= entry_rate


def test_a_chat_leaves_a_ban_on_the_floor_it_was_put_on(
    limiter, qa_clock, run_async,
):
    """The stronger half of the same sentence — not merely "no faster"
    but AT the floor, which is where a 9.5-hour ban has to leave it."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    MUTE.mute(CHAT, LADDER[2])
    _serve(qa_clock, LADDER[2])
    assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


# ===== (b) the muted interval is excluded from the success window =======

def test_not_one_minute_of_a_nine_hour_ban_is_credited_as_quiet_time(
    limiter, qa_clock, run_async,
):
    """T1(b). 34212 s is 570 success windows; at ``FLOOD_RATE_INCREASE``
    each that is 57 calls/s of credit for sitting inside a ban."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    for _ in range(10):
        qa_clock.advance(LADDER[2] / 10.0)
        assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


def test_the_climb_measured_before_the_ban_is_not_banked_through_it(
    limiter, qa_clock, run_async,
):
    """Error guessing on (b): credit half-earned BEFORE the ban must not
    be paid out after it. Fifty-nine quiet seconds, then the ban, then a
    single second on the far side would otherwise complete a window."""
    _seed(limiter, run_async)
    qa_clock.advance(WINDOW - 1.0)
    MUTE.mute(CHAT, LADDER[1])
    _serve(qa_clock, LADDER[1])
    assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


# ===== (c) the first window after the lift is a full one ================

def test_the_first_window_after_a_lift_is_at_least_a_full_one(
    limiter, qa_clock, run_async,
):
    """T1(c). The lift instant must restart the success clock: a chat
    that climbs a step one second after a 9.5-hour ban lifts has learned
    nothing from it."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    _serve(qa_clock, LADDER[2])
    qa_clock.advance(WINDOW - 2.0)
    assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


def test_the_climb_does_resume_once_the_chat_has_earned_it(
    limiter, qa_clock, run_async,
):
    """The control for (c): the freeze is a freeze, not a permanent
    floor. Without this row every assertion above passes for a daemon
    that simply never recovers."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[1])
    _serve(qa_clock, LADDER[1])
    qa_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600)
    assert limiter.earned_rate(CHAT) > FLOOR


# ===== error guessing: arming twice, re-arming, restarting mid-ban ======

def test_two_arms_of_one_ban_in_the_same_instant_count_one_ban(
    limiter, qa_clock, run_async,
):
    """``note_ban`` is documented "Idempotent per ban"
    (``entrypoints.md``), and one Telegram ban really does reach the
    daemon twice: the rich path arms on its 200 body and
    ``_send_with_retry`` arms on the ``RetryAfter``."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    MUTE.mute(CHAT, LADDER[2])
    assert _bans_today(limiter) == 1


def test_two_arms_that_compute_one_deadline_count_one_ban(
    limiter, qa_clock, run_async,
):
    """The realistic shape of the same thing: Telegram counts ``retry_after``
    down, so a second response five seconds later asks for five seconds
    less and both arms describe ONE deadline."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    qa_clock.advance(5.0)
    MUTE.mute(CHAT, LADDER[2] - 5.0)
    assert _bans_today(limiter) == 1


@pytest.mark.xfail(strict=True, reason=(
    "measured deviation, tester-iter2-001: idempotence is keyed on the "
    "resulting DEADLINE, not on the ban. Two arms of one ban that arrive "
    "at different instants carrying the SAME retry_after push the deadline "
    "out and are counted as two bans. Harmless in direction (it never "
    "under-counts, and ruling 5's cap only asks whether the count is "
    "non-zero) but `bans_today` over-reports to `aipager status`, and "
    "entrypoints.md promises 'Idempotent per ban'. Strict xfail so that "
    "fixing it turns this row red and the contract gets restated."))
def test_arming_the_same_ban_twice_seconds_apart_counts_one_ban(
    limiter, qa_clock, run_async,
):
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    qa_clock.advance(5.0)
    MUTE.mute(CHAT, LADDER[2])
    assert _bans_today(limiter) == 1


def test_a_ban_is_never_under_counted(limiter, qa_clock, run_async):
    """The direction that would actually hurt: ruling 5's half-ceiling cap
    reads this counter, so a ban rounded down to zero would hand the chat
    the full ceiling the day after a ban."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    MUTE.mute(CHAT, LADDER[2])
    assert _bans_today(limiter) >= 1


def test_a_ban_that_lifts_and_re_arms_counts_two_bans(
    limiter, qa_clock, run_async,
):
    """The other side of that boundary: two genuine bans an hour apart
    are two bans, not one — this is the counter ruling 5's half-ceiling
    cap reads."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[1])
    _serve(qa_clock, LADDER[1])
    qa_clock.advance(3600.0)
    MUTE.mute(CHAT, LADDER[1])
    assert _bans_today(limiter) == 2


def test_a_re_armed_ban_puts_the_rate_back_on_the_floor(
    limiter, qa_clock, run_async,
):
    """A chat that had climbed away from the floor between two bans must
    be put back on it by the second."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[1])
    _serve(qa_clock, LADDER[1])
    qa_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600)
    assert limiter.earned_rate(CHAT) > FLOOR      # it did climb back
    MUTE.mute(CHAT, LADDER[1])
    assert limiter.earned_rate(CHAT) == pytest.approx(FLOOR)


def _restart(qa_clock, monkeypatch, downtime: float = 0.0):
    """Everything a process restart forgets, forgotten; only the file
    crosses the gap."""
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty(force=True) is True
    MUTE.clear()
    qa_clock.advance(downtime)
    fresh = BudgetRateLimiter(clock=qa_clock, sleep=qa_clock.sleep,
                              signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(fresh)
    monkeypatch.setattr(rm, "_rate_limiter", fresh, raising=False)
    assert flood_state.load() is True
    return fresh


def test_a_restart_in_the_middle_of_a_ban_keeps_the_chat_on_the_floor(
    limiter, qa_clock, run_async, monkeypatch,
):
    """Error guessing, and the operator's actual reflex: restart the
    daemon when the bot goes quiet. On 0.7.12 that was the fastest way to
    put nine more requests into an active ban."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    MUTE.mute(CHAT, LADDER[2])
    fresh = _restart(qa_clock, monkeypatch, downtime=600.0)
    assert fresh.earned_rate(CHAT) == pytest.approx(FLOOR)


def test_a_restart_mid_ban_does_not_hand_back_the_muted_hours(
    limiter, qa_clock, run_async, monkeypatch,
):
    """The same restart, carried through to the lift: the process that
    comes back has no memory of the arm instant except the file, so this
    is where a forgotten ban would show up as a full-speed resume."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    MUTE.mute(CHAT, LADDER[2])
    fresh = _restart(qa_clock, monkeypatch, downtime=600.0)
    _serve(qa_clock, LADDER[2] - 600.0)
    assert fresh.earned_rate(CHAT) == pytest.approx(FLOOR)


def test_a_restart_mid_ban_still_remembers_that_there_was_a_ban(
    limiter, qa_clock, run_async, monkeypatch,
):
    """…and the count survives too, so ruling 5's half-ceiling cap is not
    reset by a restart."""
    _seed(limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    fresh = _restart(qa_clock, monkeypatch, downtime=600.0)
    assert _bans_today(fresh) >= 1


# ===== the acceptance row: the four-ban ladder ==========================

def _run_ladder(limiter, qa_clock, bans=LADDER, quiet: float = 60.0):
    """The 2026-09-15 ladder: each ban armed, served in full, a quiet
    minute between them — the shape an escalating penalty regime has."""
    for retry_after in bans:
        MUTE.mute(CHAT, retry_after)
        _serve(qa_clock, retry_after)
        qa_clock.advance(quiet)


def test_the_four_ban_ladder_ends_on_the_floor_not_at_the_ceiling(
    limiter, qa_clock, run_async,
):
    """**The acceptance criterion for this whole change.**

    1283 → 312 → 34212 → 34212. On iteration 1 this chat finished at 1.0
    calls/s — the ceiling, DOUBLE the rate it entered the first ban with —
    because every muted hour was credited as quiet success.
    """
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    _run_ladder(limiter, qa_clock)
    assert limiter.earned_rate(CHAT) <= FLOOR


def test_the_ladder_never_lets_the_rate_back_above_where_it_started(
    limiter, qa_clock, run_async,
):
    """The ladder sampled at every step rather than only at the end: a
    chat that spiked to the ceiling between two bans has already sent the
    burst that earns the next one."""
    _seed(limiter, run_async)
    qa_clock.advance(12 * WINDOW)
    entry = limiter.earned_rate(CHAT)
    seen = []
    for retry_after in LADDER:
        MUTE.mute(CHAT, retry_after)
        _serve(qa_clock, retry_after)
        qa_clock.advance(60.0)
        seen.append(limiter.earned_rate(CHAT))
    assert max(seen) <= entry


def test_the_ladder_remembers_every_one_of_the_four_bans(
    limiter, qa_clock, run_async,
):
    """``bans_today == 0`` through four bans was the iteration-1 symptom
    that made the forgotten ban visible."""
    _seed(limiter, run_async)
    _run_ladder(limiter, qa_clock)
    assert _bans_today(limiter) == len(LADDER)


def test_the_same_timeline_without_bans_does_reach_the_ceiling(
    limiter, qa_clock, run_async,
):
    """The control that turns the acceptance row into evidence: the SAME
    elapsed time with no ban in it climbs all the way up, so "ends on the
    floor" is a statement about the bans and not about a frozen clock."""
    _seed(limiter, run_async)
    for retry_after in LADDER:
        qa_clock.advance(retry_after + 61.0)
    assert limiter.earned_rate(CHAT) == pytest.approx(CEILING)
