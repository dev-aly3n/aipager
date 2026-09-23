"""Rows A, B, G (typing half), L (typing half), U, Y, Z (roadmap 8.30
R1/R2, rulings Q1/Q5): the "typing…" bubble is back inside the budget, as
the chat's LOWEST ornament, sent by ONE loop per chat.

vm3, 2026-09-23: one typing loop per busy SESSION, exempt from every
budget, sent the bubble twice every 4.5 s into one DM for 3 h 23 min —
~5,400 calls nobody counted — until a 429 on the bubble itself, and a
straight 7-hour ban 3 h 23 min after that.

The loop rows run the REAL animator's typing loop on a virtual event loop,
with every Bot API call funnelled through the real limiter (``vbot``);
the limiter rows drive ``process_request`` directly on :class:`FloodClock`.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)
from aipager.state import Status

CHAT = 256113222
INTERVAL = config.TYPING_INDICATOR_INTERVAL
MIN = 60.0
SKIP_CARD = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)
TYPING = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)


# ── helpers ──────────────────────────────────────────────────────────────────

def _session(bot, vloop, label: str, *, age: float = 5 * MIN, msg_id: int = 70):
    """A BUSY session with a live card whose turn is *age* seconds old —
    by default five minutes in, a 10 s-tier card."""
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = vloop.time()
    return sess


def _typing_bot(mk_bot, vbot):
    bot = mk_bot()
    bot._app.bot = vbot
    return bot


async def _ok():
    return "sent"


def _req(limiter, *, endpoint="sendMessage", args=None, callback=_ok):
    return limiter.process_request(
        callback=callback, args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": CHAT}, rate_limit_args=args)


def _typing_429(seconds: float = 5.0):
    async def _boom():
        raise RetryAfter(int(seconds))
    return _boom


# ── A: one loop per chat ─────────────────────────────────────────────────────

def test_a_two_busy_sessions_share_one_typing_loop(mk_bot, vbot, vloop):
    """Row A (R2). Two BUSY sessions in one DM, cards quiet, for 60 s: the
    chat's typing calls are ≥ ``TYPING_INDICATOR_INTERVAL`` apart and there
    are 13 or 14 of them — one bubble's worth. On 0.7.13 each session ran
    its own loop: two calls every 4.5 s, 28 a minute, for one bubble.

    The loop is started once per session, exactly as ``_start_animation``
    used to start it; the second start must find the chat's loop running.

    Mutation: drop the per-chat singleton (the "already running" return in
    ``_animate_typing``) and two loops run for the chat. (The count alone
    would not show it: the chat's shared last-sent stamp — the restart
    guard, row Y — would space two loops' calls out anyway, so the row
    counts the LOOPS as well.)
    """
    bot = _typing_bot(mk_bot, vbot)
    s1 = _session(bot, vloop, "one", msg_id=71)
    s2 = _session(bot, vloop, "two", msg_id=72)

    async def _drive():
        loops = [asyncio.ensure_future(bot._animate_typing(s)) for s in (s1, s2)]
        await asyncio.sleep(1.0)
        assert sum(not t.done() for t in loops) == 1, "two typing loops for one chat"
        await asyncio.sleep(59.0)
        for task in loops:
            task.cancel()
        await asyncio.gather(*loops, return_exceptions=True)

    vloop.run_until_complete(_drive())
    stamps = vbot.stamps("sendChatAction")
    assert len(stamps) in (13, 14), stamps
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= INTERVAL - 1e-6, gaps


# ── B: the lowest ornament ───────────────────────────────────────────────────

def test_b_typing_is_refused_below_its_reserve_when_a_card_is_not(
    limiter, gated_bot, run_async,
):
    """Row B (R1/R2). The chat's bucket drained below the typing reserve:
    ``sendChatAction`` raises ``FloodSkipped`` and nothing more reaches
    the wire — on 0.7.13 it went out, exempt. With the bucket EXACTLY at
    the card's reserve, a card edit is admitted while the bubble REFRESH
    is still refused: a lit bubble needs one token more than a card.

    AMENDED by 8.30's operator ruling: the bubble that LIGHTS a dark chat
    goes at a card's reserve (see the next row), so the chat is lit first
    here — that bubble takes the bucket from 3 to 2, the card's reserve.

    Mutation: give the refresh the card's ``_SKIP_RESERVE`` and it is
    admitted at the card's threshold.
    """
    run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                         rate_limit_args=TYPING))  # 3 -> 2
    with pytest.raises(FloodSkipped):
        run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                             rate_limit_args=TYPING))
    assert len(gated_bot.stamps("sendChatAction")) == 1
    run_async(gated_bot.edit_message_text("card", chat_id=CHAT, message_id=5,
                                          rate_limit_args=SKIP_CARD))
    assert gated_bot.stamps("editMessageText"), "the card was refused too"


def test_the_bubble_that_lights_a_dark_chat_goes_at_a_cards_reserve(
    limiter, gated_bot, flood_clock, run_async,
):
    """Operator ruling 2026-09-23: the bubble shows from the first second
    of every turn. At a turn's start the card message takes a token (3 →
    2), so at the refresh reserve the first bubble would wait for it to
    refill — 4 s into every turn on the vm3 replay. A bubble into a DARK
    chat (none admitted in Telegram's 5 s) goes at a card's reserve and
    leaves the card its own; the refresh 4.5 s later needs the extra
    token again, and once the chat has been dark for 5 s the next bubble
    lights it at the card's reserve again.

    Mutation: drop the dark-chat case and the first bubble is refused.
    """
    run_async(_req(limiter))                        # the card message: 3 -> 2
    run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                         rate_limit_args=TYPING))  # lit: 2 -> 1
    flood_clock.advance(2.0)                        # 1 -> 2 at 0.5/s
    run_async(gated_bot.edit_message_text("card", chat_id=CHAT, message_id=5,
                                          rate_limit_args=SKIP_CARD))  # 2 -> 1
    flood_clock.advance(2.5)                        # 4.5 s on: 1 -> 2.25
    with pytest.raises(FloodSkipped):               # a refresh needs 3
        run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                             rate_limit_args=TYPING))
    flood_clock.advance(0.5)                        # 5 s dark: 2.5
    run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                         rate_limit_args=TYPING))
    assert len(gated_bot.stamps("sendChatAction")) == 2


# ── G (typing half) and U: a typing 429 is a warning, not a card penalty ─────

def _at_rate(limiter, flood_clock, rate: float) -> None:
    limiter.restore([{"chat_id": CHAT, "rate": rate,
                      "rate_earned_at": flood_clock.wall}])
    assert limiter.earned_rate(CHAT) == pytest.approx(rate)


def _climb(limiter, flood_clock, run_async, minutes: int) -> float:
    for _ in range(minutes):
        flood_clock.advance(MIN)
        run_async(_req(limiter))
    return limiter.earned_rate(CHAT)


def test_g_one_429_on_typing_starts_a_six_hour_warning(
    limiter, flood_clock, run_async,
):
    """Row G (R5/Q5), the case the contract names. Rate 1.0; ONE 429 on
    ``sendChatAction`` (retry_after 5) → rate ≤ 0.5 five minutes later and
    three hours later, ``warning_remaining`` ≈ 6 h at arming. The CARDS are
    not deferred: ``retry_until`` untouched, cadence ×1. The bubble itself
    is refused for 5 s, then allowed. At 6 h 1 min the ceiling is 1.0
    again. On 0.7.13 a typing 429 was recorded against nothing: rate 1.0
    at +5 min.

    Mutation: drop ``note_typing_429``'s arming and the rate is 1.0 at
    +5 min. (``typing_blocked_until`` has its own row below: at +1 s here
    the bubble's token reserve would refuse it anyway.)
    """
    _at_rate(limiter, flood_clock, 1.0)
    with pytest.raises(RetryAfter):
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING,
                       callback=_typing_429(5)))
    snap = limiter.snapshot()["chats"][0]
    assert snap["retry_until_in"] == 0.0, "a typing 429 deferred the cards"
    assert limiter.cadence_multiplier(CHAT) == 1.0

    flood_clock.advance(1.0)
    with pytest.raises(FloodSkipped):                # inside its retry_after
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING))
    flood_clock.advance(5.0)
    assert run_async(_req(limiter, endpoint="sendChatAction", args=TYPING)) == "sent"
    assert limiter.warning_remaining(CHAT) == pytest.approx(
        config.FLOOD_WARNING_HOURS * 3600.0 - 6.0)

    assert _climb(limiter, flood_clock, run_async, 5) <= 0.5
    assert _climb(limiter, flood_clock, run_async, 180) <= 0.5
    flood_clock.advance(6 * 3600.0 - 185 * MIN + MIN)          # to +6 h 1 min
    assert limiter.warning_remaining(CHAT) == 0.0
    assert limiter.ceiling_for(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)


def test_g_the_bubble_is_refused_for_exactly_its_retry_after(
    limiter, flood_clock, run_async,
):
    """Row G's typing clause, with a retry_after long enough that the
    chat's token bucket is FULL while it runs — so it is the typing 429's
    own block, not the bubble's token reserve, that refuses it at +10 s.
    Allowed again once it has run out.

    Mutation: drop ``typing_blocked_until`` and the bubble goes out at
    +10 s.
    """
    run_async(_req(limiter))
    flood_clock.advance(10.0)
    with pytest.raises(RetryAfter):
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING,
                       callback=_typing_429(20)))
    flood_clock.advance(10.0)                       # bucket full, block not over
    assert limiter.snapshot()["chats"][0]["tokens"] == pytest.approx(
        config.TELEGRAM_CHAT_BURST)
    with pytest.raises(FloodSkipped):
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING))
    flood_clock.advance(11.0)
    assert run_async(_req(limiter, endpoint="sendChatAction", args=TYPING)) == "sent"


def test_u_a_typing_429_does_not_penalise_the_cards(
    limiter, flood_clock, run_async,
):
    """Row U (Q5). At the start rate (already under the warned ceiling), a
    typing 429 does NOT halve the rate, pause the cards, double the card
    backoff or put the chat in minimal mode — a card must not freeze over
    an ornament's refusal. The very next card edit is admitted.

    Mutation: route the typing 429 through ``note_retry_after`` and the
    rate halves, the backoff doubles and the card edit is deferred.
    """
    run_async(_req(limiter))
    flood_clock.advance(10.0)
    rate = limiter.earned_rate(CHAT)
    with pytest.raises(RetryAfter):
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING,
                       callback=_typing_429(5)))
    assert limiter.earned_rate(CHAT) == pytest.approx(rate)
    assert limiter.cadence_multiplier(CHAT) == 1.0
    assert limiter.minimal_mode(CHAT) is False
    assert run_async(_req(limiter, endpoint="editMessageText",
                          args=SKIP_CARD)) == "sent"


# ── L (typing half): the hour sheds the bubble first ─────────────────────────

def test_l_typing_is_refused_at_75_percent_of_the_hour_and_cards_are_not(
    limiter, flood_clock, run_async,
):
    """Row L, typing half (Q1). At 810 calls in the hour (75 % of the 1080
    ornament share) the bubble is refused — counted in
    ``typing_shed_refusals`` — while card edits are still admitted.

    Mutation: drop the ``typing_shed`` refusal and the bubble goes out.
    """
    async def _fill():
        for _ in range(810):
            await _req(limiter)
    run_async(_fill())
    flood_clock.advance(10.0)                       # a full token bucket
    with pytest.raises(FloodSkipped):
        run_async(_req(limiter, endpoint="sendChatAction", args=TYPING))
    assert limiter.snapshot()["chats"][0]["typing_shed_refusals"] == 1
    assert run_async(_req(limiter, endpoint="editMessageText",
                          args=SKIP_CARD)) == "sent"


# ── Y: the chat's loop outlives any one session, and restarts politely ───────

def test_y_one_loop_outlives_a_session_and_ends_with_the_last(
    mk_bot, vbot, vloop, rich_http,
):
    """Row Y (R2). Two BUSY sessions share a chat. Session one goes IDLE —
    its card is taken down — while session two is still BUSY: the chat's
    ONE loop carries on. When session two's card goes too, the loop ends.
    And a card RESTART never sends the bubble sooner than 4.5 s after the
    last one: the stamp is the chat's, not the loop's.

    Mutation: cancel the chat loop in ``_stop_typing`` whatever the other
    sessions are doing and the bubble dies with session one; key the
    last-sent stamp per loop and the restart sends at once.
    """
    rich_http.clock = vloop.time
    bot = _typing_bot(mk_bot, vbot)
    s1 = _session(bot, vloop, "one", msg_id=71)
    s2 = _session(bot, vloop, "two", msg_id=72)
    marks: dict[str, float] = {}

    async def _drive():
        bot._start_animation(s1)
        bot._start_animation(s2)
        assert s1.typing_task is s2.typing_task, "two loops for one chat"
        await asyncio.sleep(20.0)
        s1.status = Status.IDLE
        bot._stop_animation(s1)
        marks["one_stopped"] = vloop.time()
        await asyncio.sleep(20.0)
        # A card restart 2.5 s after a bubble — late enough that the
        # chat's bucket is full again, so only the chat's own last-sent
        # stamp stands between the new loop and an early bubble.
        seen = len(vbot.stamps("sendChatAction"))
        while len(vbot.stamps("sendChatAction")) == seen:
            await asyncio.sleep(0.1)
        last = vbot.stamps("sendChatAction")[-1]
        await asyncio.sleep(last + 2.5 - vloop.time())
        marks["restart"] = vloop.time()
        bot._start_animation(s2)
        await asyncio.sleep(12.0)
        s2.status = Status.IDLE
        bot._stop_animation(s2)
        marks["two_stopped"] = vloop.time()
        await asyncio.sleep(20.0)

    vloop.run_until_complete(_drive())
    stamps = vbot.stamps("sendChatAction")
    after_one = [t for t in stamps if t > marks["one_stopped"]]
    assert after_one, "the bubble died with the first session"
    before_restart = [t for t in stamps if t < marks["restart"]]
    after_restart = [t for t in stamps if t >= marks["restart"]]
    assert after_restart and after_restart[0] - before_restart[-1] >= INTERVAL - 1e-6
    assert [t for t in stamps if t > marks["two_stopped"]] == []
    assert getattr(bot, "_typing_tasks", {}) == {}


# ── Z: the mute ──────────────────────────────────────────────────────────────

def test_z_a_muted_chat_gets_no_bubble_from_its_loop(mk_bot, vbot, vloop):
    """Row Z (R7). A flood-muted chat: zero ``sendChatAction`` on the wire
    from the per-chat loop, however long it runs — and not even an attempt
    at the gate.

    What this row pins is the END-TO-END fact: ``_typing_chat`` and
    ``_send_typing`` each check the mute, so the loop never asks, and the
    limiter's gate would refuse it if it did. Removing the two loop-side
    checks together fails this row (the gate counts a ``muted_refusal``);
    each alone is pinned by
    ``tests/test_typing_indicator.py::test_no_bubble_into_a_flood_muted_chat``
    and ``::test_a_mute_armed_after_the_gate_still_stops_the_send``, and
    the gate by row K. (Iteration 1's fit check used to withhold the
    bubble first and hid the double removal; the operator ruling removed
    the fit check.)
    """
    bot = _typing_bot(mk_bot, vbot)
    s1 = _session(bot, vloop, "one", msg_id=71)
    MUTE.mute(CHAT, 400.0)

    async def _drive():
        task = asyncio.ensure_future(bot._animate_typing(s1))
        await asyncio.sleep(30.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    vloop.run_until_complete(_drive())
    assert vbot.stamps("sendChatAction") == []
    chats = vbot._limiter.snapshot()["chats"]
    assert all(c["muted_refusals"] == 0 for c in chats), chats


# ── typing always shows; the young card yields (operator ruling) ────────────

def _card_and_bubble(mk_bot, vbot, vloop, rich_http, *, age: float,
                     seconds: float = 60.0):
    rich_http.clock = vloop.time
    bot = _typing_bot(mk_bot, vbot)
    sess = _session(bot, vloop, "dev", age=age, msg_id=77)
    sess.last_tool_edit_at = 0.0
    marks: dict = {}

    async def _drive():
        marks["start"] = vloop.time()
        bot._start_animation(sess)
        await asyncio.sleep(seconds / 2)
        marks["planned"] = bot._card_interval(sess, streaming=True)
        await asyncio.sleep(seconds / 2)
        bot._stop_animation(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(_drive())
    return (marks["start"], vbot.stamps("sendChatAction"),
            [t for t, _md in rich_http.edits()], vbot._limiter,
            marks["planned"])


#: The longest a bubble may be late: one ``TYPING_RETRY_WAKE`` step past
#: the time a card edit's token takes to refill at the chat's START rate
#: (every fresh chat's, and the warned ceiling). The bubble needs a full
#: bucket (``_TYPING_RESERVE``) so that it never takes a card's token; a
#: card edit a moment before it costs it at most that refill.
LATE = 1.0 / config.FLOOD_START_RATE


def _most_in(stamps, window: float) -> int:
    return max((sum(1 for t in stamps if s <= t < s + window) for s in stamps),
               default=0)


def test_the_bubble_shows_from_the_first_second_and_the_young_card_yields(
    mk_bot, vbot, vloop, rich_http,
):
    """Operator ruling 2026-09-23, which REVERSES iteration 1's fit check
    (a bubble withheld beside a card in its first two minutes). A DM card
    30 s into its turn — tier 0 for the whole minute measured:

    * the bubble goes out in the turn's first second, at least 12 times a
      minute, and is never later than one token's refill (``LATE``) — the
      price of it never taking a card's token;
    * the card yields: it edits on the cadence planned with the bubble's
      share reserved — max(1.2, 60/27 → 1/(0.45 − 1/4.5)) × 1.1 = 4.83 s,
      12–13 edits a minute instead of 28 — and on that cadence exactly, so
      it never lost an edit to the bubble racing it;
    * together they stay inside the chat's 60 s window (30), and inside
      it LESS the bubble's reserve (27), which is what keeps the bubble
      admitted.

    Mutation: plan the card without the reservation (``typing_interval``
    0 in ``_card_pacing``) and the card edits at 2.2 s, the window fills,
    and the bubble lapses for seconds at a time.
    """
    start, bubbles, edits, limiter, planned = _card_and_bubble(
        mk_bot, vbot, vloop, rich_http, age=30.0)
    assert bubbles and bubbles[0] - start < 1.0, "no bubble in the first second"
    gaps = [b - a for a, b in zip(bubbles, bubbles[1:])]
    assert max(gaps) <= INTERVAL + LATE + 1e-3, gaps
    assert len(bubbles) >= 12, len(bubbles)
    card_gaps = [b - a for a, b in zip(edits, edits[1:])]
    assert 11 <= len(edits) <= 14, len(edits)
    assert planned == pytest.approx(4.83, abs=0.01)
    assert card_gaps == [pytest.approx(planned, abs=1e-3)] * len(card_gaps)
    everything = sorted(bubbles + edits)
    assert _most_in(everything, 60.0) <= 27
    assert limiter.snapshot()["chats"][0]["muted_refusals"] == 0


def test_the_card_plans_the_bubble_only_while_the_bubble_is_live(
    mk_bot, vbot, vloop,
):
    """The reservation follows the bubble, not the card: a card with no
    typing loop, a chat whose bubble the hour has shed (850 of 1,080
    ornaments spent, past the 75 % latch), and a chat whose bubble a 429
    blocks all keep the whole chat — 2.2 s, today's tier-0 cadence — and
    the reservation comes back when the block ends. Mutation: reserve
    whenever a card animates and each of the three reads 4.83 s.
    """
    bot = _typing_bot(mk_bot, vbot)
    sess = _session(bot, vloop, "dev", age=30.0, msg_id=77)
    limiter = vbot._limiter
    out: dict = {}

    async def _drive():
        out["no loop"] = bot._card_interval(sess, streaming=True)
        bot._start_typing(sess)
        await asyncio.sleep(0)
        out["live"] = bot._card_interval(sess, streaming=True)
        limiter.restore([{"chat_id": CHAT,
                          "hourly": [[vloop.wall() - 60.0, 850, 0]]}])
        assert limiter.hourly_usage(CHAT)["typing_shed"] is True
        out["shed"] = bot._card_interval(sess, streaming=True)
        limiter.reset()
        limiter.note_typing_429(CHAT, 5)
        out["blocked"] = bot._card_interval(sess, streaming=True)
        await asyncio.sleep(6.0)
        out["unblocked"] = bot._card_interval(sess, streaming=True)
        bot._stop_typing(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(_drive())
    young = pytest.approx(2.2)
    assert out["no loop"] == young
    assert out["live"] == pytest.approx(4.83, abs=0.01)
    assert out["shed"] == young
    assert out["blocked"] == young
    assert out["unblocked"] == pytest.approx(4.83, abs=0.01)


def test_the_bubble_flows_beside_a_long_running_card(
    mk_bot, vbot, vloop, rich_http,
):
    """Tiers 1–3 are unchanged: a card ten minutes in edits every 30 s,
    which is slower than anything the reservation asks, so its cadence is
    the tier's and the bubble refreshes on its own clock beside it —
    every 15 s at this age since operator ruling #2 — nothing refused.
    Mutation: withhold the bubble whenever any card animates and this
    counts none."""
    _start, bubbles, edits, limiter, _planned = _card_and_bubble(
        mk_bot, vbot, vloop, rich_http, age=10 * MIN, seconds=91.0)
    assert len(bubbles) >= 6, bubbles
    gaps = [b - a for a, b in zip(bubbles, bubbles[1:])]
    assert max(gaps) <= config.TYPING_AGE_TIER2_INTERVAL + LATE + 1e-3, gaps
    card_gaps = [b - a for a, b in zip(edits, edits[1:])]
    assert card_gaps, edits
    # Every card gap is the tier's 30 s (plus the loop's 10 ms wake
    # slack): no card edit was refused, and none waited for a wake grid —
    # either would show as a longer gap.
    assert all(30.0 <= g <= 30.05 for g in card_gaps), card_gaps
    assert limiter.snapshot()["chats"][0]["muted_refusals"] == 0


def test_an_unclassified_bubble_is_still_suspended_in_minimal_mode(
    limiter, flood_clock, run_async,
):
    """The bubble is an ORNAMENT whatever its caller declared: in minimal
    mode entered through the RATE (two 429s, 0.125/s — no hourly latch
    involved) a ``sendChatAction`` with no ``rate_limit_args`` at all is
    suspended like any ornament, and counted as one.

    Mutation: take the declared class for the chat action (unclassified =
    ESSENTIAL) and it goes out in minimal mode.
    """
    run_async(_req(limiter))
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    flood_clock.advance(30.0)                       # a full bucket, rate still low
    assert limiter.minimal_mode(CHAT) is True
    assert limiter.hourly_usage(CHAT)["typing_shed"] is False
    with pytest.raises(FloodSkipped):
        run_async(_req(limiter, endpoint="sendChatAction", args=None))
    assert limiter.snapshot()["chats"][0]["ornaments_suspended"] == 1


# ── a BAN answered to the bubble is a ban on the chat ────────────────────────

def test_g_a_ban_on_the_bubble_mutes_the_chat_and_holds_the_answer(
    mk_bot, vbot, vloop, volume_telegram,
):
    """Review rev-iter1-001. Since 8.30 typing is metered WITH the chat's
    messages, so a ban Telegram answers to ``sendChatAction`` is a ban on
    the chat — and nothing may go into it. Through the REAL typing send
    path (``_send_typing`` → the gated bot → the limiter), a
    ``retry_after`` of 25,429 s (vm3's) must arm the mute exactly as
    ``transport._send_with_retry`` and ``rich_message._ban_if_excessive``
    do: the very next ``sendMessage`` is refused at the gate with zero
    requests, the turn's answer — on the real ``notify`` path, over the
    real ``rich_message._post`` — is HELD with zero requests, and it is
    delivered once the ban lifts.

    Before the fix, ``note_ban`` dropped the rate to the floor (so the
    BUBBLE was suspended as an ornament — which is why the QA row
    ``test_a_ban_answered_to_the_bubble_gets_zero_further_requests`` saw
    nothing more on the wire: it counted only bubbles) but the mute was
    never armed, so the ESSENTIAL answer went straight into the ban.

    Mutation: drop the ``MUTE.mute(...)`` from ``_send_typing``'s
    ``RetryAfter`` arm and the ``sendMessage`` reaches the wire.
    """
    from aipager.bot.flood import FloodMuted
    from aipager.bot.held import HELD

    bot = _typing_bot(mk_bot, vbot)
    sess = _session(bot, vloop, "vm3")
    vbot.fail["sendChatAction"] = lambda: RetryAfter(25429)
    out: dict = {}

    async def main():
        chat = bot._typing_chat(sess)
        assert chat is not None
        out["sent"] = await bot._send_typing(sess, chat)
        out["muted"] = MUTE.is_muted(CHAT)
        try:
            await vbot.send_message(chat_id=CHAT, text="the next answer")
        except FloodMuted:
            out["refused"] = True
        sess.status = Status.IDLE
        await bot.notify(sess, "idle_prompt",
                         {"summary": "the answer the ban must not eat"})
        out["held"] = HELD.count(CHAT)
        out["wire_in_ban"] = list(volume_telegram.calls)
        await asyncio.sleep(25430.0)               # the ban lifts
        out["delivered"] = await bot.flush_held_answers(sess)

    vloop.run_until_complete(main())
    assert out["sent"] is True
    assert out["muted"] is True, "a ban on the bubble did not mute the chat"
    assert out.get("refused") is True
    assert [c[0] for c in vbot.calls] == ["sendChatAction"], vbot.calls
    assert out["wire_in_ban"] == [], "a request went into the ban"
    assert out["held"] == 1, "the answer was not held"
    assert out["delivered"] == 1
    assert [c[0] for c in volume_telegram.calls] == ["sendRichMessage"]
    assert HELD.count(CHAT) == 0


# ── the pacing the reservation reads ─────────────────────────────────────────

def test_pacing_reports_whether_the_bubble_is_open_and_its_window(
    limiter, flood_clock, run_async,
):
    """``pacing_for``'s ``typing`` is True for a healthy chat and False
    while the hour has shed the bubble, a 429 blocks it, or the chat is in
    minimal mode — the three refusals that last. ``typing_min_gap`` is the
    window less the bubble's reserve: 60/27 in a DM. Mutation: report
    ``typing`` True whatever the chat's state and a blocked bubble's
    share is still reserved, slowing the card for nothing."""
    assert limiter.pacing_for(CHAT)["typing"] is True           # no budget yet
    run_async(_req(limiter))
    pacing = limiter.pacing_for(CHAT)
    assert pacing["typing"] is True
    assert pacing["typing_min_gap"] == pytest.approx(60.0 / 27)
    limiter.note_typing_429(CHAT, 5)
    assert limiter.pacing_for(CHAT)["typing"] is False           # blocked
    flood_clock.advance(6.0)
    assert limiter.pacing_for(CHAT)["typing"] is True
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)                            # 0.125/s
    assert limiter.pacing_for(CHAT)["minimal"] is True
    assert limiter.pacing_for(CHAT)["typing"] is False           # minimal


def test_a_hook_edit_in_a_young_turn_waits_for_the_yielded_cadence(
    mk_bot, vbot, vloop,
):
    """The hook paths' own 1.2 s debounce is faster than the card's
    cadence. While the bubble's share is reserved, a hook-driven edit
    waits for the yielded interval too — else a busy hook stream spends
    the very slots reserved for the bubble. Without a bubble the 1.2 s
    debounce is today's. Mutation: drop the raise in ``_card_edit_due``
    and a hook edit 2 s after the last goes out beside a live bubble."""
    from aipager.config import STREAM_EDIT_INTERVAL

    bot = _typing_bot(mk_bot, vbot)
    sess = _session(bot, vloop, "dev", age=30.0)
    out: dict = {}

    async def _drive():
        now = vloop.time()
        sess.last_tool_edit_at = now - 2.0
        out["alone"] = bot._card_edit_due(sess, now, base_gap=STREAM_EDIT_INTERVAL)
        bot._start_typing(sess)
        await asyncio.sleep(0)
        now = vloop.time()
        sess.last_tool_edit_at = now - 2.0
        out["beside"] = bot._card_edit_due(sess, now, base_gap=STREAM_EDIT_INTERVAL)
        sess.last_tool_edit_at = now - 4.9
        out["later"] = bot._card_edit_due(sess, now, base_gap=STREAM_EDIT_INTERVAL)
        # A state-change path (base 0) is not held to the cadence.
        sess.last_tool_edit_at = now - 2.0
        out["state"] = bot._card_edit_due(sess, now, base_gap=0.0)
        bot._stop_typing(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(_drive())
    assert out == {"alone": True, "beside": False, "later": True, "state": True}


# ── operator ruling #2: the bubble decays with the OLDEST turn's age ────────

def _bubble_gaps(mk_bot, vbot, vloop, sessions_ages, *, seconds=60.0,
                 decay_off=()):
    """Run the chat's REAL typing loop beside sessions whose turns are the
    given ages (no card traffic, so the budget never refuses a bubble) and
    return the measured gaps between bubbles."""
    bot = _typing_bot(mk_bot, vbot)
    sessions = []
    for i, age in enumerate(sessions_ages):
        sess = _session(bot, vloop, f"s{i}", age=age, msg_id=70 + i)
        if i in decay_off:
            sess.override_card_age_decay = False
        sessions.append(sess)

    async def _drive():
        tasks = [asyncio.ensure_future(bot._animate_typing(s)) for s in sessions]
        await asyncio.sleep(seconds)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    vloop.run_until_complete(_drive())
    stamps = vbot.stamps("sendChatAction")
    return [round(b - a, 3) for a, b in zip(stamps, stamps[1:])]


@pytest.mark.parametrize("age,expected", [
    (9 * MIN, 4.5),             # stays under 10 min for the whole run
    (10 * MIN, 15.0),           # exactly 10:00
    (58 * MIN, 15.0),           # stays under an hour
    (60 * MIN, 15.0),           # exactly 60:00
    (4 * 60 * MIN, 15.0),
])
def test_the_bubble_decays_with_the_turns_age(mk_bot, vbot, vloop, age,
                                              expected):
    """Operator ruling #2 (final): 4.5 s for ten minutes, 15 s after,
    measured on the real loop. Mutation: keep the loop on
    ``TYPING_INDICATOR_INTERVAL`` and every row reads 4.5."""
    gaps = _bubble_gaps(mk_bot, vbot, vloop, [age])
    assert len(gaps) >= 3, gaps
    assert gaps == [pytest.approx(expected, abs=1e-3)] * len(gaps)


# The 60:00 boundary is no longer a row: tiers 2 and 3 are both 15 s, so a
# run across it measures 15 → 15 and cannot tell them apart — it would
# pass whatever the tier-3 boundary did (see fixes-2.md, the survivor).
@pytest.mark.parametrize("start,before,after", [
    (9 * MIN + 50.0, 4.5, 15.0),    # crosses 10:00 at 10 s into the run
])
def test_the_bubble_changes_pace_at_the_boundary(mk_bot, vbot, vloop, start,
                                                 before, after):
    """The 10:00 boundary, crossed on the loop: from 9:50 the bubbles run
    at 4.5 s while the turn is at most 9:59, and at 15 s from the first
    refresh due after 10:00 (the period is read when the next bubble is
    due). Mutation: shift the boundary and the switch lands on the wrong
    bubble."""
    gaps = _bubble_gaps(mk_bot, vbot, vloop, [start], seconds=70.0)
    first_after = next(i for i, g in enumerate(gaps)
                       if g == pytest.approx(after, abs=1e-3))
    assert all(g == pytest.approx(before, abs=1e-3) for g in gaps[:first_after])
    assert all(g == pytest.approx(after, abs=1e-3) for g in gaps[first_after:])
    # The bubble that opens the first slower gap went out before the
    # boundary, and the one after it is past it.
    boundary = 10 * MIN
    last_fast = start + sum(gaps[:first_after])
    assert last_fast <= boundary + 1e-3
    assert last_fast + before > boundary


def test_the_oldest_turn_sets_the_chats_pace(mk_bot, vbot, vloop):
    """The bubble is the CHAT's: a one-minute turn beside a 70-minute one
    gets the 70-minute turn's 15 s, not its own 4.5 s. Mutation: take the
    youngest (or the loop's own session's) age and this reads 4.5."""
    gaps = _bubble_gaps(mk_bot, vbot, vloop, [1 * MIN, 70 * MIN])
    assert gaps and gaps == [pytest.approx(15.0, abs=1e-3)] * len(gaps)


def test_the_preference_off_keeps_the_bubble_at_its_base_pace(
    mk_bot, vbot, vloop,
):
    """``card_age_decay`` off for the session: its 70-minute turn keeps a
    4.5 s bubble, as its card keeps today's cadence. Mutation: ignore the
    preference and this reads 15."""
    alone = _bubble_gaps(mk_bot, vbot, vloop, [70 * MIN], decay_off=(0,))
    assert alone and alone == [pytest.approx(INTERVAL, abs=1e-3)] * len(alone)


def test_one_session_with_the_preference_off_keeps_the_chats_bubble_fast(
    mk_bot, vbot, vloop,
):
    """Off for ANY busy session in the chat is off for the chat's one
    bubble: a 70-minute turn with decay on beside a 30-minute one with it
    off is 4.5 s. Mutation: read only the oldest session's preference and
    this reads 15."""
    gaps = _bubble_gaps(mk_bot, vbot, vloop, [70 * MIN, 30 * MIN],
                        decay_off=(1,))
    assert gaps and gaps == [pytest.approx(INTERVAL, abs=1e-3)] * len(gaps)


def test_the_young_card_reserves_the_bubbles_pace_in_force(mk_bot, vbot, vloop):
    """"The tier-0 card reservation uses the typing rate actually in
    force": a young card beside a 70-minute turn reserves one 15 s bubble,
    not one 4.5 s bubble, so it edits faster than it would beside a young
    turn. Mutation: reserve ``TYPING_INDICATOR_INTERVAL`` always and it
    reads the 4.5 s figure."""
    from aipager.bot.flood_budget import card_interval
    from aipager.config import STREAM_EDIT_INTERVAL

    bot = _typing_bot(mk_bot, vbot)
    young = _session(bot, vloop, "young", age=30.0, msg_id=71)
    _session(bot, vloop, "old", age=70 * MIN, msg_id=72)
    out: dict = {}

    async def _drive():
        bot._start_typing(young)
        await asyncio.sleep(0)
        out["interval"] = bot._card_interval(young, streaming=True)
        out["pacing"] = vbot._limiter.pacing_for(CHAT)
        bot._stop_typing(young)
        await asyncio.sleep(0)

    vloop.run_until_complete(_drive())
    pacing = out["pacing"]

    def planned(every):
        return card_interval(
            base=STREAM_EDIT_INTERVAL, busy_sessions=2, is_group=False,
            chat_rate=pacing["rate"],
            sustained_min_gap=max(pacing["sustained_min_gap"],
                                  pacing["typing_min_gap"]),
            typing_interval=every)

    assert out["interval"] == pytest.approx(planned(15.0))
    assert out["interval"] < planned(INTERVAL) - 1.0
