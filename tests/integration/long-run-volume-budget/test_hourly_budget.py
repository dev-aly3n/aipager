"""Rows F, L (limiter half), M, N and the hourly half of H/I (roadmap 8.30
R1/R3/R6, rulings Q1/Q4/Q7): a rolling HOUR per chat.

Before 8.30 the longest window any chat had was 60 s. vm3's DM ran at
~57 calls a minute for 3 h 23 min — under every short window — and was
banned outright. Now every chat-scoped call except a reaction counts in a
rolling hour of ``FLOOD_HOURLY_MAX`` (1200) calls; ornaments stop at
1200 − ``FLOOD_HOURLY_ESSENTIAL_RESERVE`` (1080), which puts the chat in
minimal mode, and answers are never refused.

Every row drives the REAL ``process_request`` on :class:`FloodClock`. A
chat fills at its real pace (the sustained window allows 30 calls a
minute), so "the hour" in these rows is a real hour of virtual time.
"""

from __future__ import annotations

import logging

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)
from aipager.state import Status

CHAT = 256113222
TOTAL = config.FLOOD_HOURLY_MAX                                     # 1200
SHARE = config.FLOOD_HOURLY_MAX - config.FLOOD_HOURLY_ESSENTIAL_RESERVE  # 1080
ORNAMENT = rate_limit_args(priority=PRIORITY_ORNAMENT)
SKIP_CARD = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)


class _Wire:
    """Every call whose callback actually ran, by endpoint."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def cb(self, endpoint: str):
        async def _call():
            self.calls.append(endpoint)
            return "sent"
        return _call


def _req(limiter, wire, *, endpoint="sendMessage", args=None, chat=CHAT):
    return limiter.process_request(
        callback=wire.cb(endpoint), args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": chat}, rate_limit_args=args,
    )


def _fill(limiter, run_async, wire, n: int, *, args=None) -> None:
    """*n* admitted calls, as fast as the chat's own budget allows."""
    async def _go():
        for _ in range(n):
            await _req(limiter, wire, args=args)
    run_async(_go())


def _usage(limiter) -> dict:
    return limiter.hourly_usage(CHAT)


# ── the window itself ────────────────────────────────────────────────────────

def test_every_counted_call_is_in_the_hour_by_class(limiter, run_async):
    """R1: an answer and a card edit both count, in their own class; a
    reaction does not. On 0.7.13 there is no hour to read at all."""
    wire = _Wire()
    _fill(limiter, run_async, wire, 3)
    _fill(limiter, run_async, wire, 2, args=ORNAMENT)
    run_async(_req(limiter, wire, endpoint="setMessageReaction",
                   args=rate_limit_args(priority="signal")))
    usage = _usage(limiter)
    assert (usage["used"], usage["essential_used"], usage["ornament_used"]) == (5, 3, 2)
    assert (usage["budget"], usage["ornament_budget"]) == (TOTAL, SHARE)


def test_an_unseen_chat_reports_the_full_budget_without_allocating(limiter):
    usage = limiter.hourly_usage(-42)
    assert usage["used"] == 0 and usage["budget"] == TOTAL
    assert limiter.snapshot()["chats"] == []


def test_the_sixty_first_bucket_keeps_a_call_that_is_still_in_the_hour(
    limiter, flood_clock, run_async,
):
    """The window is 61 one-minute buckets, not 60. A call made at the END
    of a minute is 3540.2 s old sixty buckets later — still inside the
    hour — and must still be counted, or the strict invariant ("≤ cap in
    every true rolling hour") fails by up to a minute of calls.

    Mutation: size the window at 60 buckets and this call is dropped
    59.8 s early.
    """
    wire = _Wire()
    # Park the clock 0.1 s before a minute boundary, then call.
    flood_clock.advance(60.0 - (flood_clock.now % 60.0) - 0.1)
    run_async(_req(limiter, wire))
    flood_clock.advance(3540.2)                 # 3540.2 s later: inside the hour
    assert _usage(limiter)["used"] == 1
    flood_clock.advance(60.0)                   # 3600.2 s: out
    assert _usage(limiter)["used"] == 0


# ── F: the ornament share spent ──────────────────────────────────────────────

def test_f_a_spent_ornament_share_suspends_cards_and_delivers_answers(
    limiter, run_async,
):
    """Row F (R3). At ``used == 1080`` the chat is in minimal mode: a skip
    card edit is refused with ``FloodSkipped``, and an ESSENTIAL answer is
    delivered all the same.

    Mutation: drop the minimal latch (or ``hourly_minimal`` from
    ``_minimal``) and the card edit is admitted.
    """
    wire = _Wire()
    _fill(limiter, run_async, wire, SHARE)
    assert _usage(limiter)["used"] == SHARE
    assert limiter.minimal_mode(CHAT) is True
    assert _usage(limiter)["minimal"] is True

    with pytest.raises(FloodSkipped):
        run_async(_req(limiter, wire, endpoint="editMessageText", args=SKIP_CARD))
    before = len(wire.calls)
    run_async(_req(limiter, wire))                       # the answer
    assert len(wire.calls) == before + 1
    assert wire.calls[-1] == "sendMessage"


def test_f_the_card_shows_the_paused_line_exactly_once(
    limiter, gated_bot, rich_http, run_async, mk_bot,
):
    """Row F's card half: minimal mode entered through the HOUR shows the
    same single ESSENTIAL "updates paused" line minimal mode always has —
    once, however many ticks run, and nothing else on the wire.

    Mutation: leave ``hourly_minimal`` out of ``_minimal`` and every tick
    animates the card instead.
    """
    wire = _Wire()
    _fill(limiter, run_async, wire, SHARE)
    bot, sess = _card(mk_bot, limiter, gated_bot)

    async def _ticks():
        for _ in range(5):
            await bot._animate_tick(sess, "Working", False)
    run_async(_ticks())

    edits = [c for c in gated_bot.calls if c[0] == "editMessageText"]
    assert len(edits) == 1, gated_bot.calls
    assert rich_http.requests == []


def test_the_ornament_recheck_refuses_a_blocking_card_that_waited(
    limiter, flood_clock, run_async,
):
    """Q7, the concurrency half. A blocking ornament queued while the hour
    still had room, and an answer spent the last slot while it waited.
    When it reaches the take block it must be REFUSED, not sent: the
    latch was read before it queued.

    Mutation: drop the ornament re-check in ``_acquire_blocking`` and the
    card edit goes out as call 1081, with the share already spent.
    """
    import asyncio

    wire = _Wire()
    _fill(limiter, run_async, wire, SHARE - 1)
    assert limiter.minimal_mode(CHAT) is False
    limiter.note_retry_after(CHAT, 5)            # everything must wait 5 s

    async def _race():
        # Both scheduled before either runs: the card queues FIRST and
        # starts waiting out the deferral; the answer, arriving while it
        # waits, is served ahead of it (an essential never waits behind an
        # ornament) and takes the hour's last ornament-share slot.
        card = asyncio.ensure_future(
            _req(limiter, wire, endpoint="editMessageText", args=ORNAMENT))
        answer = asyncio.ensure_future(_req(limiter, wire))
        return await asyncio.gather(card, answer, return_exceptions=True)

    card, answer = run_async(_race())
    assert isinstance(card, FloodSkipped), card
    assert answer == "sent"
    assert wire.calls.count("editMessageText") == 0
    assert _usage(limiter)["ornament_used"] == 0


# ── L: the typing latch (limiter half) ───────────────────────────────────────

def test_l_the_typing_latch_sheds_at_75_and_resumes_under_60_percent(
    limiter, flood_clock, run_async,
):
    """Row L, limiter half (Q1). The typing bubble is the LOWEST ornament:
    the latch turns on at 75 % of the ornament share (810) while card edits
    are still admitted, stays on through 700, and turns off only under
    60 % (648). The refusal of ``sendChatAction`` itself is row L's other
    half, with the typing rows.

    The hour is filled at the chat's own pace (~30 a minute) and then left
    to age out a minute at a time, so ``used`` walks down through the
    hysteresis band rather than jumping over it.

    Mutation: drop the latch's ``elif``/``if`` hysteresis (a plain
    predicate at 75 %) and it clears at 700; shed at 60 % and it trips
    early.
    """
    wire = _Wire()
    _fill(limiter, run_async, wire, 809)
    assert _usage(limiter)["typing_shed"] is False
    _fill(limiter, run_async, wire, 1)
    assert _usage(limiter)["used"] == 810
    assert _usage(limiter)["typing_shed"] is True
    # Cards still flow at 810: the bubble goes first. (A moment for the
    # token bucket the fill just drained.)
    flood_clock.advance(10.0)
    run_async(_req(limiter, wire, endpoint="editMessageText", args=SKIP_CARD))
    assert wire.calls[-1] == "editMessageText"

    seen_band = False
    for _ in range(90):
        flood_clock.advance(60.0)
        usage = _usage(limiter)
        if usage["used"] >= 0.60 * SHARE:
            assert usage["typing_shed"] is True, usage
            seen_band = seen_band or usage["used"] <= 700
        else:
            assert usage["typing_shed"] is False, usage
            break
    else:
        pytest.fail("the hour never aged out")
    assert seen_band, "the walk never passed through the hysteresis band"


# ── M: minimal-mode hysteresis ───────────────────────────────────────────────

def test_m_minimal_mode_enters_at_the_share_and_leaves_at_80_percent(
    limiter, flood_clock, run_async,
):
    """Row M (Q4). Minimal entered at 1080 stays on through 865 and lifts
    only at ≤ 864 (80 % of the share): every entry and every exit costs an
    ESSENTIAL edit per card, so a latch that flapped on each freed minute
    would spend the essential reserve announcing itself.

    Mutation: leave minimal mode as soon as ``used < share`` and it lifts
    at the first minute that ages out.
    """
    wire = _Wire()
    _fill(limiter, run_async, wire, SHARE)
    assert limiter.minimal_mode(CHAT) is True
    for _ in range(90):
        flood_clock.advance(60.0)
        used = _usage(limiter)["used"]
        if used > 0.80 * SHARE:
            assert limiter.minimal_mode(CHAT) is True, used
        else:
            assert limiter.minimal_mode(CHAT) is False, used
            break
    else:
        pytest.fail("the hour never aged out")


def test_m_entry_and_exit_each_cost_one_essential_edit(
    limiter, flood_clock, gated_bot, rich_http, run_async, mk_bot,
):
    """Row M's cost clause: across one whole entry-to-exit cycle a live
    card puts exactly TWO edits on the wire — the ESSENTIAL "updates
    paused" line on entry and the ESSENTIAL resume on exit — however many
    ticks run in between.

    Mutation: drop the paused line's dedupe (``_PAUSED_CARD_TEXT``) and
    the card pays an ESSENTIAL edit on every tick of the pause. (The exit
    hysteresis itself is pinned by the row above.)
    """
    wire = _Wire()
    _fill(limiter, run_async, wire, SHARE)
    bot, sess = _card(mk_bot, limiter, gated_bot)

    async def _tick():
        await bot._animate_tick(sess, "Working", False)

    run_async(_tick())                                   # entry: paused line
    for _ in range(90):
        flood_clock.advance(60.0)
        # Keep the hour busy with answers — the realistic case, and the
        # one that would make a hysteresis-free latch flap.
        _fill(limiter, run_async, wire, 1)
        run_async(_tick())
        if not limiter.minimal_mode(CHAT):
            break
    else:
        pytest.fail("minimal mode never lifted")
    paused = [c for c in gated_bot.calls if c[0] == "editMessageText"]
    resumed = [r for r in rich_http.requests if r[0] == "editMessageText"]
    assert len(paused) == 1, paused
    assert len(resumed) == 1, resumed


# ── N: essentials are never refused ──────────────────────────────────────────

def test_n_essentials_over_the_budget_are_delivered_and_logged_once(
    limiter, run_async, caplog,
):
    """Row N (Q7). ESSENTIAL calls past 1200 in an hour are ALL delivered;
    ``essential_overflow`` counts them and the chat gets ONE WARNING for
    the hour, not one per call.

    Mutation: refuse an essential over the total and the answers stop;
    drop the once-an-hour guard and the log gets fifty lines.
    """
    wire = _Wire()
    caplog.set_level(logging.WARNING, logger="aipager.bot.flood_budget")
    _fill(limiter, run_async, wire, TOTAL + 50)
    assert len(wire.calls) == TOTAL + 50
    assert _usage(limiter)["essential_overflow"] == 50
    lines = [r for r in caplog.records
             if "essential calls over the hourly budget" in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in lines]


def test_n_the_overflow_warning_comes_back_the_next_hour(
    limiter, flood_clock, run_async, caplog,
):
    """One WARNING per chat per ROLLING HOUR, not per process: an hour
    later a chat still over budget earns another. Mutation: a boolean
    'already warned' flag and the second hour is silent."""
    wire = _Wire()
    caplog.set_level(logging.WARNING, logger="aipager.bot.flood_budget")
    _fill(limiter, run_async, wire, TOTAL + 1)
    flood_clock.advance(config.FLOOD_HOURLY_WINDOW)
    _fill(limiter, run_async, wire, TOTAL + 1)
    lines = [r for r in caplog.records
             if "essential calls over the hourly budget" in r.getMessage()]
    assert len(lines) == 2


# ── H / I: the hourly budget remembers bans too ──────────────────────────────

@pytest.mark.parametrize("bans,total,share", [(1, 600, 540), (2, 400, 360)])
def test_h_i_bans_this_week_divide_the_hourly_budget(
    limiter, flood_clock, run_async, bans, total, share,
):
    """Rows H and I, hourly half (R6): one ban in the week halves the hour,
    two third it — 600/540, then 400/360 — and a banned chat's cards stop
    at the SMALLER share. Mutation: divide only the ceiling, not the hour,
    and the banned chat keeps 1200/1080."""
    wire = _Wire()
    run_async(_req(limiter, wire))
    for day in range(bans):
        limiter.note_ban(CHAT, 1283.0)
        flood_clock.advance(86400.0)
    flood_clock.advance(2 * 86400.0)         # the oldest is days old
    usage = _usage(limiter)
    assert (usage["budget"], usage["ornament_budget"]) == (total, share)
    assert limiter.bans_remembered(CHAT) == bans


# ── helpers ──────────────────────────────────────────────────────────────────

def _card(mk_bot, limiter, gated_bot):
    """A BUSY session with a live card, every card surface metered: the
    PTB side through ``gated_bot``, the rich side through ``rich_http``."""
    rm.set_rate_limiter(limiter)
    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = 77
    sess.busy_started_at = 0.0
    return bot, sess
