"""The earned rate: AIMD, two recovery regimes, the sustained cap, minimal
mode (rows E, F, G, K; rule R4; 8.27).

What this replaces. Until 0.7.12 every chat was paced at a CONSTANT
``TELEGRAM_PRIVATE_MAX_RATE`` (1 call/s) with no volume ceiling for a
private chat at all, and a 429 changed only the busy card's cadence
multiplier — which decayed back in 60 seconds. So the daemon's memory of
a violation was one minute while Telegram's penalty regime lasts hours,
and its idea of a safe rate was a published number rather than anything
Telegram had ever told it. On 2026-09-15 that combination produced three
bans in one day, escalating ``retry_after`` 1283 -> 312 -> 34212.

The rate is now LEARNED per chat: additive increase per quiet window,
multiplicative decrease on a 429, straight to the floor on a ban — the
same shape TCP uses on a link whose capacity it also cannot query.

Every number here is asserted against its NAMED CONSTANT, never a
literal: a test that hard-codes 0.5 passes just as happily after someone
retunes ``FLOOD_START_RATE`` to something wrong.
"""

from __future__ import annotations

import time

import pytest

from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)

CHAT = 256113222
GROUP = -1001234567890
BAN = 34212.0


def _call(limiter, run_async, chat=CHAT, endpoint="sendMessage", **kw):
    async def _cb():
        return "sent"

    return run_async(limiter.process_request(
        callback=_cb, args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": chat}, rate_limit_args=kw.get("rate_limit_args"),
    ))


# ── a chat starts where it is told to start ──────────────────────────────────

def test_an_unseen_chat_starts_at_the_named_start_rate(limiter, flood_clock):
    """Half the ceiling, not the ceiling. ``TELEGRAM_PRIVATE_MAX_RATE`` is
    Telegram's published BURST allowance; 8.21 read it as the sustained
    one and opened every chat at it. A chat we know nothing about should
    not be opened at full rate.

    Mutation: seed `_budget_for` from `_chat_max_rate` again and this
    reports the ceiling.
    """
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)
    assert config.FLOOD_START_RATE < config.TELEGRAM_PRIVATE_MAX_RATE


def test_asking_about_a_chat_does_not_allocate_a_budget_for_it(limiter):
    """`earned_rate` is read by `aipager status` and by the card cadence
    for chats that may never send. Mutation: create the budget on read and
    an idle daemon accumulates one per chat anybody asked about."""
    limiter.earned_rate(-999)
    limiter.minimal_mode(-999)
    assert limiter.snapshot()["chats"] == []


# ── row F: one 429 halves the rate ───────────────────────────────────────────

def test_one_429_halves_the_earned_rate_with_exactly_one_warning(
    limiter, flood_clock, caplog,
):
    """Row F. MULTIPLICATIVE DECREASE: a 429 is the only direct evidence
    about this chat's real limit we ever get, so it must move the RATE and
    not merely the card's cadence.

    Exactly one WARNING and no traceback: Telegram escalates on the COUNT
    of violations, and 2,021 stack traces told the operator nothing 2,021
    times. The ×2 cadence multiplier is unchanged — the two live side by
    side because they have different consumers and different time scales.

    Mutation: drop the `set_rate` call from `note_retry_after` and the
    rate stays where it was while only the card slows down, which is
    exactly the 0.7.12 behaviour that earned three bans.
    """
    caplog.set_level("WARNING", logger="aipager.bot.flood_budget")
    limiter.note_retry_after(CHAT, 5)

    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.FLOOD_START_RATE / 2)
    assert limiter.cadence_multiplier(CHAT) == 2.0
    assert len(caplog.records) == 1, [r.getMessage() for r in caplog.records]
    assert caplog.records[0].exc_info is None, "a 429 is expected, not an error"


def test_the_rate_never_falls_below_the_named_floor(limiter, flood_clock):
    """A rate of 0 is a deadlock, not a back-off: an answer must still
    eventually go out. Mutation: drop the `max(..., _min_rate)` clamp and
    enough 429s take the chat to zero, where `time_until` is +inf and the
    blocking acquire never returns."""
    for _ in range(20):
        limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_ban_drops_the_rate_straight_to_the_floor(limiter, flood_clock):
    """A ban is not a small 429 halved a few times — it is categorical.
    Mutation: route a ban through `note_retry_after`'s halving and a
    9.5-hour ban leaves the chat at half speed instead of at the floor.
    """
    limiter.note_ban(CHAT, BAN)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)
    chat = limiter.snapshot()["chats"][0]
    assert chat["bans_today"] == 1


def test_the_same_ban_is_never_counted_twice(limiter, flood_clock):
    """`_run`'s ban branch and `_sync_mute` both call `note_ban`, because
    a PTB ban and a rich-path ban arrive by different routes. Mutation:
    drop `ban_seen_until` and every request during one ban adds a stamp,
    so `bans_today` reports the request count.
    """
    limiter.note_ban(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    limiter.note_ban(CHAT, BAN - 100)
    assert limiter.snapshot()["chats"][0]["bans_today"] == 1


def test_a_genuinely_new_ban_is_counted(limiter, flood_clock):
    """The other half: idempotence must not swallow a SECOND ban. The
    2026-09-15 timeline was three in one day."""
    limiter.note_ban(CHAT, 1283)
    flood_clock.advance(2000)
    limiter.note_ban(CHAT, 312)
    assert limiter.snapshot()["chats"][0]["bans_today"] == 2


# ── row G: quiet minutes earn the rate back ──────────────────────────────────

def test_quiet_minutes_after_a_429_earn_the_rate_back_in_named_steps(
    limiter, flood_clock,
):
    """Row G. ADDITIVE INCREASE: +`FLOOD_RATE_INCREASE` per quiet
    `FLOOD_SUCCESS_WINDOW_SECONDS`, never past the ceiling.

    Mutation: make the anchor `now` instead of advancing it by whole
    windows and partial progress is lost on every read, so a chat that is
    asked about often never climbs at all.
    """
    limiter.note_retry_after(CHAT, 5)
    start = limiter.earned_rate(CHAT)
    assert start == pytest.approx(config.FLOOD_START_RATE / 2)

    for step in range(1, 5):
        flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS)
        assert limiter.earned_rate(CHAT) == pytest.approx(
            start + step * config.FLOOD_RATE_INCREASE), step


def test_the_climb_stops_at_the_named_ceiling(limiter, flood_clock):
    """Mutation: drop the `min(..., _chat_max_rate)` and a long-quiet chat
    climbs past Telegram's published burst allowance for ever."""
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 500)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_a_partial_window_earns_nothing_yet(limiter, flood_clock):
    """Whole steps only, like `_decay`. Mutation: make it proportional and
    the rate creeps up between two calls a second apart."""
    limiter.note_retry_after(CHAT, 5)
    before = limiter.earned_rate(CHAT)
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 0.9)
    assert limiter.earned_rate(CHAT) == pytest.approx(before)


# ── the second regime: recovery from a BAN takes hours ───────────────────────

def test_after_a_ban_the_climb_is_stretched_over_the_named_hours(
    limiter, flood_clock,
):
    """The two regimes, and why there are two (P-2).

    +0.1 per 60 s would take a banned chat from MIN back to the ceiling in
    9.5 MINUTES. That is not a memory of a 9.5-HOUR ban at all — it is
    defect D3 ("our memory is 60 s, Telegram's is hours") restated one
    layer up. So within `FLOOD_RATE_RECOVERY_HOURS` of a ban the same ten
    steps are stretched across those hours.

    Mutation: use `_success_window` unconditionally and a chat banned for
    9.5 hours is back at full speed before the ban has even lifted.

    The last assertion changed in iteration 2 (ruling 5): the whole
    recovery period now buys the whole climb UP TO THE REDUCED CEILING,
    because a chat with a ban in the last 24 h may not reach the full one.
    `test_a_ban_today_halves_the_ceiling_until_tomorrow` owns that rule;
    this row owns the SHAPE of the climb, so it asserts the climb is
    complete (at its ceiling) rather than naming a number twice.
    """
    limiter.note_ban(CHAT, 60.0)
    flood_clock.advance(61.0)          # let the mute lapse; the ban stands
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)

    # One 60 s window buys nothing now.
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)

    # The whole recovery period buys the whole climb.
    flood_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600.0)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE * 0.5)


def test_the_slow_regime_expires_and_minutes_count_again(limiter, flood_clock):
    """A ban is evidence about the next few hours, not for ever. Past
    `FLOOD_RATE_RECOVERY_HOURS` the chat is an ordinary chat again."""
    limiter.note_ban(CHAT, 1.0)
    flood_clock.advance(config.FLOOD_RATE_RECOVERY_HOURS * 3600.0 + 10.0)
    limiter.note_retry_after(CHAT, 5)
    before = limiter.earned_rate(CHAT)
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS)
    assert limiter.earned_rate(CHAT) == pytest.approx(
        before + config.FLOOD_RATE_INCREASE)


def test_the_climb_is_frozen_while_the_chat_is_muted(limiter, flood_clock):
    """Climbing through a ban is precisely learning nothing — the defect
    this whole feature replaces. And the frozen time must not be BANKED:
    a chat must not emerge from a 9.5-hour ban with 570 windows' worth of
    credit to cash in one read.

    Mutation: drop the `MUTE.is_muted` check from `_earn` and a chat
    climbs back to full rate DURING its ban, then sends at full rate the
    instant it lifts — straight into the next escalation.
    """
    limiter.note_ban(CHAT, BAN)
    MUTE.mute(CHAT, BAN)
    flood_clock.advance(BAN - 10.0)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)

    flood_clock.advance(20.0)          # the mute lapses
    assert not MUTE.is_muted(CHAT)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE), \
        "the mute banked windows to cash on release"


# ── row K: minimal mode ──────────────────────────────────────────────────────

def test_two_429s_from_the_start_rate_enter_minimal_mode(limiter, flood_clock):
    """Row K, and the ladder `FLOOD_MINIMAL_MODE_RATE_FLOOR` was sited to
    make legible: START 0.5 -> one 429 -> 0.25 (above the floor; the card
    keeps animating, slower) -> a second 429 -> 0.125 (below it).

    Asserted against the NAMED CONSTANT, never a literal.
    """
    limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(CHAT) > config.FLOOD_MINIMAL_MODE_RATE_FLOOR
    assert limiter.minimal_mode(CHAT) is False

    limiter.note_retry_after(CHAT, 5)
    assert limiter.earned_rate(CHAT) < config.FLOOD_MINIMAL_MODE_RATE_FLOOR
    assert limiter.minimal_mode(CHAT) is True


def test_minimal_mode_refuses_an_ornament_and_passes_an_essential(
    limiter, flood_clock,
):
    """Row K's core: pressure sheds PIXELS, not ANSWERS. The card is ~95 %
    of a chat's outbound volume and the answer ~5 %, so suspending the
    former is what buys the latter room.

    `FloodSkipped`, not `FloodMuted`: nothing is banned and the chat is
    healthy. Every ornament caller already reads `FloodSkipped` as
    "nothing sent, stamps untouched, try again" — `FloodMuted` would make
    the animator stop the card for good.

    Mutation: drop the minimal-mode branch from `process_request` and the
    ornament goes through, competing with the answer for a rate of 0.125
    calls/s.
    """
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.minimal_mode(CHAT) is True

    ran: list[str] = []

    async def _ornament():
        ran.append("ornament")

    async def _essential():
        ran.append("essential")

    with pytest.raises(FloodSkipped):
        run = limiter.process_request(
            callback=_ornament, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": CHAT},
            rate_limit_args=rate_limit_args(priority=PRIORITY_ORNAMENT))
        import asyncio
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run)
    assert ran == []


def test_minimal_mode_counts_what_it_suspended(limiter, flood_clock, run_async):
    """Suspended ornaments are counted, not merely dropped: `aipager
    status` reports them, so an operator asking "why is my card frozen"
    has an answer other than "it is broken"."""
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)

    async def _ornament():
        raise AssertionError("the ornament ran in minimal mode")

    for _ in range(3):
        with pytest.raises(FloodSkipped):
            run_async(limiter.process_request(
                callback=_ornament, args=(), kwargs={},
                endpoint="editMessageText", data={"chat_id": CHAT},
                rate_limit_args=rate_limit_args(priority=PRIORITY_ORNAMENT)))
    chat = limiter.snapshot()["chats"][0]
    assert chat["ornaments_suspended"] == 3
    assert chat["minimal"] is True


def test_an_essential_still_goes_through_in_minimal_mode(
    limiter, flood_clock, run_async,
):
    """The half that matters most: the ANSWER survives."""
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    assert _call(limiter, run_async) == "sent"


def test_a_signal_is_never_suspended_by_minimal_mode(
    limiter, flood_clock, run_async,
):
    """A chat under pressure must still acknowledge that it heard you.
    Mutation: suspend SIGNAL too and a user in a penalised chat gets no
    👀 on the message they just sent, with no other feedback either."""
    from aipager.bot.flood_budget import PRIORITY_SIGNAL

    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    ran: list[str] = []

    async def _signal():
        ran.append("reacted")

    run_async(limiter.process_request(
        callback=_signal, args=(), kwargs={}, endpoint="setMessageReaction",
        data={"chat_id": CHAT},
        rate_limit_args=rate_limit_args(priority=PRIORITY_SIGNAL)))
    assert ran == ["reacted"]


def test_minimal_mode_lifts_when_the_rate_recovers(limiter, flood_clock):
    """It is a state, not a latch. Mutation: set a sticky flag instead of
    comparing the live rate and a chat stays in minimal mode for ever."""
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.minimal_mode(CHAT) is True
    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 2)
    assert limiter.minimal_mode(CHAT) is False


def test_an_unresolvable_chat_is_never_minimal(limiter):
    """There is no earned rate to be under a floor, and refusing these
    would mute the daemon rather than pace a chat."""
    assert limiter.minimal_mode(None) is False


# ── row E: the sustained cap, for a PRIVATE chat ─────────────────────────────

def test_a_private_chat_has_a_rolling_volume_ceiling(limiter, flood_clock):
    """Row E's precondition, and the gap that let the incident run.

    A private chat used to have NO rolling window: `SlidingWindow` was
    built only `if is_group`. Its 1/s bucket therefore permitted 60 calls
    a minute for ever, which is what two BUSY sessions did for ~45 minutes
    before the 9.5-hour ban.

    Mutation: rebuild the window `if is_group` again and this is `0`.
    """
    limiter.earned_rate(CHAT)
    budget = limiter._budget_for(CHAT)
    assert budget.window.limit == int(config.FLOOD_SUSTAINED_MAX)
    assert budget.window.period == pytest.approx(config.FLOOD_SUSTAINED_WINDOW)


def test_a_group_keeps_the_stricter_of_the_two_ceilings(limiter, flood_clock):
    """20/60 s is stricter than 30/60 s, and a group's limit is Telegram's
    own published one. Mutation: use `FLOOD_SUSTAINED_MAX` everywhere and
    a group is allowed 50 % more than Telegram permits."""
    limiter.earned_rate(GROUP)
    budget = limiter._budget_for(GROUP)
    assert budget.window.limit == int(config.TELEGRAM_GROUP_MAX_CALLS)


def test_a_private_chat_cannot_exceed_the_sustained_cap_in_any_window(
    limiter, flood_clock, run_async,
):
    """Row E, measured: drive far more calls than the cap allows and the
    window holds. The blocking acquire WAITS them out — nothing is
    dropped, which is the difference between a cap and a refusal.
    """
    limiter._budget_for(CHAT).set_rate(
        config.TELEGRAM_PRIVATE_MAX_RATE, flood_clock.now)
    stamps: list[float] = []

    async def _cb():
        stamps.append(flood_clock.now)

    async def _drive():
        for _ in range(int(config.FLOOD_SUSTAINED_MAX) + 10):
            await limiter.process_request(
                callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)

    run_async(_drive())
    assert len(stamps) == int(config.FLOOD_SUSTAINED_MAX) + 10, "a call was lost"
    worst = max(sum(1 for t in stamps if s <= t < s + config.FLOOD_SUSTAINED_WINDOW)
                for s in stamps)
    assert worst <= config.FLOOD_SUSTAINED_MAX, worst


# ── the cadence is TOLD about the new pacing ─────────────────────────────────

def test_the_card_cadence_reads_the_live_earned_rate(limiter, flood_clock):
    """Without this the cards would simply be refused: a chat at 0.1
    calls/s asked for an edit every 1.3 s would be turned down twelve
    times out of thirteen. `pacing_for` is one read, deliberately — the
    rate one tick and the backoff the next is how two budgets drift apart.

    Mutation: return a constant from `pacing_for` and a penalised chat
    fights its own limiter instead of slowing down.
    """
    before = limiter.pacing_for(CHAT)
    assert before["rate"] == pytest.approx(config.FLOOD_START_RATE)
    assert before["sustained_min_gap"] == pytest.approx(
        config.FLOOD_SUSTAINED_WINDOW / config.FLOOD_SUSTAINED_MAX)
    assert before["minimal"] is False

    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    after = limiter.pacing_for(CHAT)
    assert after["rate"] == pytest.approx(config.FLOOD_START_RATE / 4)
    assert after["backoff"] == 4.0
    assert after["minimal"] is True


def test_card_interval_is_byte_identical_without_the_new_keywords():
    """The optional-kwarg design, pinned. Every pure cadence row in the
    8.21 suites passes neither keyword and must keep its exact expected
    number; only the rows driving the real animator change.

    Mutation: give either keyword a non-None default and dozens of
    unrelated rows move at once.
    """
    from aipager.bot.flood_budget import card_interval

    for base, n, group in ((1.2, 1, False), (3.0, 2, True), (0.1, 0, False)):
        assert card_interval(base=base, busy_sessions=n, is_group=group) == \
            card_interval(base=base, busy_sessions=n, is_group=group,
                          chat_rate=None, sustained_min_gap=None)


# ── row K's card side: one static line, however many ticks run ───────────────

def _minimal_bot(mk_bot, limiter, gated_bot=None, chat=CHAT):
    """A BUSY session with a live card, in a chat already in minimal mode.

    When *gated_bot* is given, EVERY card surface is real and metered:
    the PTB side funnels through `limiter.process_request` with the
    endpoint name PTB would use, and the rich side runs the real `_post`
    onto an `httpx.MockTransport`. That is the difference between proving
    a dedupe and proving a DELIVERY — iteration 1's version of the row
    below monkeypatched `_edit_busy_raw` with a stub returning True, so it
    passed while the line it was about never reached Telegram at all.
    """
    import aipager.bot.rich_message as rm
    from aipager.state import Status

    rm.set_rate_limiter(limiter)
    limiter.note_retry_after(chat, 5)
    limiter.note_retry_after(chat, 5)
    assert limiter.minimal_mode(chat) is True

    bot = mk_bot()
    if gated_bot is not None:
        bot._app.bot = gated_bot
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.status = Status.BUSY
    sess.scope_chat_id = chat
    sess.busy_msg_id = 4242
    sess.stream_last_rendered = ""
    return bot, sess


def _ornament_is_refused(limiter, run_async, chat=CHAT) -> bool:
    """Is the chat refusing ORNAMENTS right now? Asked through the gate
    itself rather than by reading `minimal_mode`."""
    async def _never():                              # pragma: no cover
        raise AssertionError("an ornament was admitted")

    try:
        run_async(limiter.process_request(
            callback=_never, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": chat},
            rate_limit_args=rate_limit_args(priority=PRIORITY_ORNAMENT),
        ))
    except FloodSkipped:
        return True
    return False


def test_minimal_mode_edits_the_card_once_however_many_ticks_run(
    mk_bot, limiter, gated_bot, rich_http, flood_clock, run_async,
):
    """Row K's last clause, through the REAL limiter (ruling 1).

    Two claims, and the second is the one iteration 1 shipped broken:

    * exactly ONE `editMessageText` reaches Telegram however many ticks
      run — a chat at 0.125 calls/s cannot afford a repeated edit, and a
      line that says "updates paused" repeated every tick is not paused;
    * **it lands while ornaments are being refused.** The paused line is
      ESSENTIAL: sent as an ORNAMENT it is refused by the very suspension
      it exists to explain, leaving a card frozen mid-animation with no
      reason — which reads as a hung bot.

    Mutations, both covered: drop the `stream_last_rendered` dedupe and
    this makes one edit per tick; send the line as an ORNAMENT and it
    makes NONE.
    """
    bot, sess = _minimal_bot(mk_bot, limiter, gated_bot)

    async def _ticks():
        for _ in range(8):
            assert await bot._animate_tick(sess, "Working", False) is False

    run_async(_ticks())

    calls = gated_bot.sent + [(m, (), {}) for m, _c in rich_http.requests]
    assert [endpoint for endpoint, _a, _k in calls] == ["editMessageText"], calls
    assert "updates paused" in gated_bot.sent[0][1][0]
    assert _ornament_is_refused(limiter, run_async), (
        "the row proves nothing unless ornaments were being refused")


def test_the_animate_task_survives_minimal_mode(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """`_animate_tick` returns FALSE, never None, while minimal.

    `None` breaks `_animate_busy`'s loop and the task dies; the watchdog
    then restarts it every 20 s for as long as the condition lasts — 348
    restarts in 54 minutes, measured 2026-09-15, against six actual HTTP
    refusals all day. `False` means "no call made, no stamps touched": the
    loop stays alive and resumes by itself when the rate recovers.

    Mutation: return None here and the restart storm is back.
    """
    bot, sess = _minimal_bot(mk_bot, limiter)
    monkeypatch.setattr(bot, "_edit_busy_raw", _async_true)

    result = run_async(bot._animate_tick(sess, "Working", False))
    assert result is False, "a minimal-mode tick killed the animate task"


async def _async_true(*a, **kw):
    return True


def test_leaving_minimal_mode_resumes_the_animation(
    mk_bot, limiter, gated_bot, rich_http, flood_clock, run_async,
):
    """Ruling 1's second half: the paused line is sent once more when
    minimal mode LIFTS, if the card is still live.

    The card coming back to life IS that render, so the assertion is that
    one edit reaches Telegram on the first tick after the lift — and it
    has to hold in the state a chat is actually in seconds after its rate
    crosses back over the floor: DEBOUNCED (the last edit was moments
    ago) and with an empty bucket, where an ordinary skip-kind ornament
    tick would be refused and the user would be left reading "updates
    paused" in a chat that is no longer paused.

    Mutation: drop the `card_resume_due` branch from `_animate_tick` and
    this tick makes no call at all.
    """
    bot, sess = _minimal_bot(mk_bot, limiter, gated_bot)
    run_async(bot._animate_tick(sess, "Working", False))
    assert sess.stream_last_rendered != ""
    gated_bot.calls.clear()
    gated_bot.sent.clear()
    rich_http.requests.clear()

    flood_clock.advance(config.FLOOD_SUCCESS_WINDOW_SECONDS * 2)
    assert limiter.minimal_mode(CHAT) is False
    budget = limiter._budget_for(CHAT)
    budget.chat.take(budget.chat.tokens())     # an empty bucket
    sess.last_tool_edit_at = time.monotonic()  # and inside the debounce

    run_async(bot._animate_tick(sess, "Working", False))

    edits = gated_bot.endpoints() + [m for m, _c in rich_http.requests]
    assert edits == ["editMessageText"], (
        "the card did not resume when the rate recovered")
    assert sess.card_resume_due is False, "one resume edit per lift, not a loop"
