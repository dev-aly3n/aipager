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
    by default five minutes in, a 10 s-tier card that leaves the chat room
    for the bubble (a card in its first two minutes does not)."""
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
    ``sendChatAction`` raises ``FloodSkipped`` and nothing reaches the
    wire — on 0.7.13 it went out, exempt. With the bucket EXACTLY at the
    card's reserve, a card edit is admitted while the bubble is still
    refused: the bubble needs one token more than a card.

    Mutation: give typing the card's ``_SKIP_RESERVE`` and it is admitted
    at the card's threshold.
    """
    run_async(_req(limiter))                        # 3 tokens -> 2 (card reserve)
    with pytest.raises(FloodSkipped):
        run_async(gated_bot.send_chat_action(chat_id=CHAT, action="typing",
                                             rate_limit_args=TYPING))
    assert gated_bot.stamps("sendChatAction") == []
    run_async(gated_bot.edit_message_text("card", chat_id=CHAT, message_id=5,
                                          rate_limit_args=SKIP_CARD))
    assert gated_bot.stamps("editMessageText"), "the card was refused too"


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
    at the gate: the loop's own checks keep it off the limiter entirely.
    Mutation: drop the mute checks in ``_typing_chat`` and ``_send_typing``
    and the gate still refuses every call (nothing reaches the wire), but
    the refusals are counted, which this row forbids; drop the gate too
    and the bubble goes into the ban."""
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


# ── the fit check: the bubble never costs a card an edit ─────────────────────

def _card_and_bubble(mk_bot, vbot, vloop, rich_http, *, age: float):
    rich_http.clock = vloop.time
    bot = _typing_bot(mk_bot, vbot)
    sess = _session(bot, vloop, "dev", age=age, msg_id=77)
    sess.last_tool_edit_at = 0.0

    async def _drive():
        bot._start_animation(sess)
        await asyncio.sleep(60.0)
        bot._stop_animation(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(_drive())
    return vbot.stamps("sendChatAction"), rich_http.edits(), vbot._limiter


def test_the_bubble_waits_beside_a_card_in_its_first_two_minutes(
    mk_bot, vbot, vloop, rich_http,
):
    """Q1's "lowest ornament", made exact. A card 30 s into its turn plans
    the chat's pacing to the margin, so the bubble does not go out and the
    card loses NOTHING to it: not one refused edit. (Admitted on a spare
    token alone, the bubble cost one DM card 5 of its 28 edits a minute.)

    Mutation: drop ``_typing_fits_cards`` from the loop and the bubble
    goes out and the card starts losing edits to it.
    """
    bubbles, edits, limiter = _card_and_bubble(mk_bot, vbot, vloop, rich_http,
                                               age=30.0)
    assert bubbles == []
    assert limiter.snapshot()["chats"][0]["skipped"] == 0
    assert len(edits) >= 12


def test_the_bubble_flows_beside_a_long_running_card(
    mk_bot, vbot, vloop, rich_http,
):
    """The other side: a card ten minutes in edits every 30 s, which leaves
    the chat room, so the bubble refreshes on its own 4.5 s clock — where
    it matters most, with the card slow. Mutation: withhold the bubble
    whenever any card animates and this counts none."""
    bubbles, _edits, limiter = _card_and_bubble(mk_bot, vbot, vloop, rich_http,
                                                age=10 * MIN)
    assert len(bubbles) >= 12, bubbles
    assert limiter.snapshot()["chats"][0]["skipped"] == 0
