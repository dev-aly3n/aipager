"""Rows G, H, I (roadmap 8.30 R5/R6): a 429 is a warning worth hours, and
a ban is remembered for a week.

vm3, 2026-09-23. The chat's one warning — a 429 at 01:13 — was forgiven
in about five minutes: the 60 s success window climbed the rate from 0.5
back to the 1.0 ceiling. Its previous ban, 2026-09-19, was four days old
and outside 0.7.13's 24-hour memory, so it limited nothing. 3 h 23 min
later Telegram answered with a straight 7-hour ban.

Every row runs on :class:`FloodClock`, which moves the monotonic budget
clock and the wall clock (ban stamps, the warning regime) together.
H and I must fail on 0.7.13; G does too (its rate is 1.0 at +5 min there).
"""

from __future__ import annotations

import pytest

from aipager import config

CHAT = 256113222
DAY = 86400.0
MINUTE = 60.0


async def _ok():
    return "sent"


def send(limiter, *, chat=CHAT, endpoint="sendMessage", rate_limit_args=None,
         callback=_ok):
    """One call through the real gate. The coroutine, for ``run_async``."""
    return limiter.process_request(
        callback=callback, args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": chat}, rate_limit_args=rate_limit_args,
    )


def _climb(limiter, flood_clock, run_async, hours: float) -> float:
    """Let *hours* of quiet pass with one call a minute, the way a live
    chat's earned rate is actually read, and return the rate at the end."""
    steps = int(hours * 60)
    for _ in range(steps):
        flood_clock.advance(MINUTE)
        run_async(send(limiter))
    return limiter.earned_rate(CHAT)


def _ban(limiter, flood_clock, run_async, *, days_ago: float) -> None:
    """One ban, *days_ago* days before 'now'. Recorded through the public
    ``note_ban`` exactly as ``_run`` records one, then the clocks moved."""
    run_async(send(limiter))
    limiter.note_ban(CHAT, 1283.0)
    flood_clock.advance(days_ago * DAY)


# ── H ───────────────────────────────────────────────────────────────────────

def test_h_a_ban_four_days_ago_halves_the_ceiling(limiter, flood_clock,
                                                  run_async):
    """Row H (R6). One ban stamp 4 days old, warning regime long expired →
    the chat may climb to 0.5/s and no higher. On 0.7.13 the stamp is
    outside the 24 h memory and the chat climbs to the full 1.0 — the
    shape of vm3 on 2026-09-23.

    Mutation: count bans over 24 h (or drop the divisor) and the climb
    reaches 1.0.
    """
    _ban(limiter, flood_clock, run_async, days_ago=4)
    rate = _climb(limiter, flood_clock, run_async, hours=2)
    assert rate == pytest.approx(0.5)
    assert limiter.ceiling_for(CHAT) == pytest.approx(0.5)
    assert limiter.bans_remembered(CHAT) == 1


def test_h_the_ban_is_forgotten_after_a_week(limiter, flood_clock, run_async):
    """The memory decays: eight days on, the chat climbs to the full
    ceiling again. Mutation: an unbounded memory pins it at half."""
    _ban(limiter, flood_clock, run_async, days_ago=8)
    assert _climb(limiter, flood_clock, run_async, hours=1) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)
    assert limiter.bans_remembered(CHAT) == 0


# ── I ───────────────────────────────────────────────────────────────────────

def test_i_two_bans_this_week_third_the_ceiling(limiter, flood_clock,
                                                run_async):
    """Row I (R6). Two ban stamps inside the week → ceiling 1/3 calls/s.
    On 0.7.13 both are outside the day (or, inside it, the ceiling is a
    fixed half), so the chat climbs past 1/3.

    Mutation: a fixed half instead of ``/(1 + bans)`` and the chat reaches
    0.5.
    """
    _ban(limiter, flood_clock, run_async, days_ago=3)
    _ban(limiter, flood_clock, run_async, days_ago=2)
    rate = _climb(limiter, flood_clock, run_async, hours=2)
    assert rate == pytest.approx(1.0 / 3.0)
    assert limiter.ceiling_for(CHAT) == pytest.approx(1.0 / 3.0)
    assert limiter.bans_remembered(CHAT) == 2


def test_a_new_ban_clamps_a_rate_already_above_the_new_ceiling(
    limiter, flood_clock, run_async,
):
    """The ``_earn`` clamp. A restored ban history lowers the ceiling under
    a rate the chat already has; the rate must come DOWN to it, not merely
    stop climbing. Driven through ``restore`` (the file is how a ban
    stamp arrives without a fresh ban dropping the rate to the floor).

    Mutation: keep 0.7.13's ``if rate >= ceiling: return`` and the chat
    keeps spending at 1.0 under a 1/3 ceiling.
    """
    limiter.restore([{"chat_id": CHAT, "rate": 1.0,
                      "rate_earned_at": flood_clock.wall}])
    assert limiter.earned_rate(CHAT) == pytest.approx(1.0)
    # Two bans arrive in the history (another process's write, a restart).
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [flood_clock.wall - 2 * DAY,
                                     flood_clock.wall - DAY]}])
    flood_clock.advance(1.0)
    assert limiter.earned_rate(CHAT) == pytest.approx(1.0 / 3.0)
