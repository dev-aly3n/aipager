"""A ban must never be credited as quiet success (8.29 T1, ruling 5).

The defect these rows exist for, in one sentence: the AIMD controller
learns only from calls that reach ``process_request``, and while a mute
holds NO CALL REACHES IT — the gate is above everything, and the callers'
own kept pre-checks turn most of them back before that. So the controller
saw an unbroken quiet window across the whole ban and rewarded it with an
additive increase. Measured on the shipped iteration-1 code: a chat
entered a 1283 s ban at ``FLOOD_START_RATE`` (0.5) and LEFT it at 1.0 —
the ceiling — with ``bans_today`` still 0. Four bans left it pinned at the
ceiling. That is the 1283 -> 312 -> 34212 escalation ladder reproduced
inside the fix meant to end it.

Silence during a ban is the absence of evidence, not evidence of good
behaviour, and the ban itself is the strongest possible evidence of the
opposite. Three mechanisms answer it, and there is a row for each:

* **(a)** the rate drops at the instant the ban is ARMED
  (``flood.MUTE.mute`` tells the live limiter), not when a call next
  happens to fail;
* **(b)** the muted interval is EXCLUDED from the success window — the
  clock restarts at the lift instant;
* **(c)** the first window after a lift is a FULL one, so the rate cannot
  climb back seconds after a ban.

Plus ruling 5's durable half: while a chat has a ban in the last 24 hours
its CEILING is halved, so recovering fully six hours after a 9.5-hour ban
is no longer possible.

Every clock here is the shared ``flood_clock`` (monotonic for the budget,
wall for the mute), and every number is read from ``aipager.config``.
"""

from __future__ import annotations

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE

CHAT = 256113222

#: The real ladder from the 2026-09-15 journal, in order. The third and
#: fourth are 9.5 hours each.
LADDER = (1283.0, 312.0, 34212.0, 34212.0)


@pytest.fixture
def live_limiter(limiter):
    """The ``limiter`` fixture PUBLISHED, exactly as ``lifecycle._make_builder``
    publishes the daemon's own.

    ``flood.MUTE.mute`` finds the live limiter through
    ``rich_message.get_rate_limiter()`` — the same registry the rich path
    and the session monitor read — so a row about arming has to publish
    it. conftest's ``_isolate_flood_mute`` sets it back to ``None`` after
    every test.
    """
    rm.set_rate_limiter(limiter)
    return limiter


async def _ok():
    return "sent"


def _send(limiter, chat=CHAT):
    return limiter.process_request(
        callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": chat}, rate_limit_args=None,
    )


def _seed(limiter, run_async, chat=CHAT):
    """Make the chat exist in the limiter (budgets are created lazily)."""
    run_async(_send(limiter, chat))


def _rate_row(limiter, chat=CHAT):
    return next(c for c in limiter.snapshot()["chats"] if c["chat_id"] == chat)


# ── (a) the ban is recorded at ARM time ─────────────────────────────────────

def test_arming_a_ban_drops_the_rate_with_no_call_involved(
    live_limiter, run_async, flood_clock,
):
    """T1(a). The mute is armed and NOTHING else happens — no send, no
    429, no tick. The rate must already be on the floor and the ban must
    already be counted.

    Mutation: delete `_tell_limiter_about_the_ban`'s call in `MUTE.mute`
    and the limiter learns nothing until some call reaches it, which
    during a ban is never.
    """
    _seed(live_limiter, run_async)
    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.FLOOD_START_RATE)

    MUTE.mute(CHAT, LADDER[0])

    assert live_limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)
    assert _rate_row(live_limiter)["bans_today"] == 1


def test_a_ban_is_counted_even_for_a_chat_the_limiter_had_never_seen(
    live_limiter,
):
    """The budget is created lazily, so the first thing that ever happens
    to a chat can be its ban — the restart-into-a-ban case. It must be
    remembered anyway, not dropped for want of a budget."""
    MUTE.mute(CHAT, LADDER[2])

    assert live_limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)
    assert _rate_row(live_limiter)["bans_today"] == 1


def test_one_ban_is_counted_once_however_many_paths_see_it(
    live_limiter, run_async, flood_clock,
):
    """Idempotence per ban. Arming tells the limiter; `_sync_mute` sees
    the same mute on the next call; `_run`'s own branch would too. One
    ban, one stamp — otherwise the halved ceiling would be charged three
    times for one event."""
    _seed(live_limiter, run_async)
    MUTE.mute(CHAT, LADDER[0])
    live_limiter.note_ban(CHAT, LADDER[0])       # `_run`'s ban branch
    live_limiter.earned_rate(CHAT)               # a read that runs `_sync_mute`

    assert _rate_row(live_limiter)["bans_today"] == 1


# ── (b) a muted interval is not quiet time ──────────────────────────────────

def test_a_chat_leaves_a_ban_at_the_floor_never_above_what_it_entered_with(
    live_limiter, run_async, flood_clock,
):
    """T1(b), the headline. The chat is at the FULL ceiling when the ban
    lands — the worst case, because that is the most credit a forgotten
    ban could hand back.

    Mutation: drop `_earn_floor` and this chat leaves a 1283 s ban at
    1.0 calls/s, measured.
    """
    _seed(live_limiter, run_async)
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 12)
    entered_with = live_limiter.earned_rate(CHAT)
    assert entered_with == pytest.approx(config.TELEGRAM_PRIVATE_MAX_RATE)

    MUTE.mute(CHAT, LADDER[0])
    flood_clock.advance(LADDER[0] + 1.0)         # the whole ban, then the lift
    assert not MUTE.is_muted(CHAT)

    left_with = live_limiter.earned_rate(CHAT)
    assert left_with == pytest.approx(config.FLOOD_MIN_RATE)
    assert left_with <= entered_with


def test_no_minute_of_a_nine_hour_ban_is_credited_as_a_success_window(
    live_limiter, run_async, flood_clock,
):
    """T1(b) at the scale of the real incident: 9.5 hours is 570 success
    windows at the 60 s regime, which would be 57 times the whole climb."""
    _seed(live_limiter, run_async)
    MUTE.mute(CHAT, LADDER[2])
    flood_clock.advance(LADDER[2] + 1.0)

    assert live_limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_the_climb_resumes_by_itself_once_the_ban_is_behind_the_chat(
    live_limiter, run_async, flood_clock,
):
    """The control that keeps the rows above from being satisfied by
    "the rate never moves again". A ban is a penalty, not a death
    sentence: a full post-ban window after the lift earns one step."""
    _seed(live_limiter, run_async)
    MUTE.mute(CHAT, LADDER[1])
    flood_clock.advance(LADDER[1] + 1.0)
    window = live_limiter._success_window_for(
        live_limiter._budget_for(CHAT), flood_clock.wall)

    flood_clock.advance(window + 1.0)

    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.FLOOD_MIN_RATE + config.FLOOD_RATE_INCREASE)


# ── (c) the first window after a lift is a full one ─────────────────────────

def test_the_first_success_window_after_a_lift_is_a_whole_one(
    live_limiter, run_async, flood_clock,
):
    """T1(c). The rate must not climb back seconds after a lift on credit
    banked BEFORE the ban — which is what an anchor left where the ban
    found it would do.

    Mutation: floor the anchor at the ban's START instead of its lift and
    a 9.5-hour ban pays out four whole climbs the instant it ends.
    """
    _seed(live_limiter, run_async)
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 0.9)  # banked
    MUTE.mute(CHAT, LADDER[0])
    flood_clock.advance(LADDER[0] + 1.0)
    budget = live_limiter._budget_for(CHAT)
    window = live_limiter._success_window_for(budget, flood_clock.wall)

    flood_clock.advance(window - 2.0)
    assert live_limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)

    flood_clock.advance(3.0)
    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.FLOOD_MIN_RATE + config.FLOOD_RATE_INCREASE)


# ── THE ACCEPTANCE ROW ──────────────────────────────────────────────────────

def test_the_four_ban_replay_ends_at_the_floor_not_at_the_ceiling(
    live_limiter, run_async, flood_clock,
):
    """**The acceptance criterion for iteration 2** (coordinator T1).

    The 2026-09-15 ladder, plus the fourth ban the replay measured:
    1283 -> 312 -> 34212 -> 34212. On the iteration-1 code this chat
    finished at 1.0 calls/s — the ceiling — having ENTERED the first ban
    at 0.5, because each ban was credited as quiet success. It must
    finish at or below `FLOOD_MIN_RATE`.

    Each ban is served in full and lifts by itself; between bans the chat
    gets a quiet minute, which is what the 60 s regime would reward and
    the post-ban regime will not.
    """
    _seed(live_limiter, run_async)
    trace = []
    for ban in LADDER:
        entered = live_limiter.earned_rate(CHAT)
        MUTE.mute(CHAT, ban)
        flood_clock.advance(ban + 1.0)
        left = live_limiter.earned_rate(CHAT)
        trace.append((ban, entered, left))
        assert left <= entered, f"a {ban:.0f}s ban made the chat FASTER: {trace}"
        assert left <= config.FLOOD_MIN_RATE, trace
        flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS)

    final = live_limiter.earned_rate(CHAT)
    assert final <= config.FLOOD_MIN_RATE, trace
    assert final < config.TELEGRAM_PRIVATE_MAX_RATE, trace
    assert _rate_row(live_limiter)["bans_today"] == len(LADDER)


# ── ruling 5: a ban today means a reduced start tomorrow ────────────────────

def test_a_ban_today_halves_the_ceiling_until_tomorrow(
    live_limiter, run_async, flood_clock,
):
    """Ruling 5. A chat with a ban in the last 24 h may climb to HALF the
    normal ceiling however long it waits; a day after the ban it is an
    ordinary chat again.

    Mutation: return `_chat_max_rate` unconditionally from
    `max_rate_for` and a 9.5-hour ban is fully forgotten six hours later,
    which is how three bans were earned in one day.
    """
    _seed(live_limiter, run_async)
    MUTE.mute(CHAT, 1.0)
    flood_clock.advance(2.0)                     # the mute lifts

    # A ban three hours ago, and then the whole recovery period.
    flood_clock.advance(3 * 3600.0)
    flood_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600.0)
    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE * 0.5)

    # A full day after the ban, the memory ages out and so does the cap.
    flood_clock.advance(86400.0)
    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)
    assert _rate_row(live_limiter)["bans_today"] == 0


def test_a_chat_with_no_ban_in_a_day_reaches_the_full_ceiling(
    live_limiter, run_async, flood_clock,
):
    """The control: the halved ceiling is a PENALTY, not the new normal."""
    _seed(live_limiter, run_async)
    flood_clock.advance(86400.0)

    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_a_restored_rate_is_clamped_to_the_reduced_ceiling(
    live_limiter, flood_clock,
):
    """The durable half of ruling 5: a state file that remembers a ban
    cannot also hand back the full rate. Order matters inside `restore` —
    the ban history is read before the rate for exactly this reason."""
    live_limiter.restore([{
        "chat_id": CHAT,
        "rate": config.TELEGRAM_PRIVATE_MAX_RATE,
        "ban_stamps": [flood_clock.wall - 3600.0],
    }])

    assert live_limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE * 0.5)
