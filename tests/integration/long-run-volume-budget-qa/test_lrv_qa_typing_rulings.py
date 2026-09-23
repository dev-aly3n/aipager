"""The three operator rulings of 2026-09-23 (contract §8, after v1.1),
black-box:

(a) the typing bubble shows from the first second of every turn, and a
    young (tier-0) card YIELDS: typing's share is reserved out of the
    chat's 60 s window, so a young DM card edits about every 4.8 s beside
    the bubble — the chat stays inside 30 calls a minute, the bubble is
    not dropped, and the card is not starved;
(b) the bubble's interval decays with the age of the chat's OLDEST busy
    turn — ``TYPING_INDICATOR_INTERVAL`` (4.5 s) under 10 min, 15 s after
    (``TYPING_AGE_TIER2_INTERVAL`` / ``TYPING_AGE_TIER3_INTERVAL``) — and
    ``card_age_decay`` off keeps it at 4.5 s always; a long turn's bubble
    flickers but never goes dark;
(c) a ban-sized 429 answered to ``sendChatAction`` arms the chat's mute:
    zero requests go into the ban, and the turn's answer is held, then
    delivered once when the ban lifts.

The pure rows import only ``aipager.flood_policy.typing_interval`` (an
entry point). The loop rows run the REAL card and typing loops on the
harness's virtual event loop; every Bot API call goes through the real
limiter (``vbot``) and every rich card/answer POST is recorded below the
limiter (``rich_http``), so "the wire" is both transports together.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager import preferences as prefs
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.flood_policy import typing_interval
from aipager.state import Status

CHAT = 256113222
MIN = 60.0
HOUR = 3600.0
EPS = 1e-3
BASE = config.TYPING_INDICATOR_INTERVAL               # 4.5
SLOW = config.TYPING_AGE_TIER2_INTERVAL               # 15
#: How late a bubble may be beyond its interval: one wake of the loop plus
#: one token refill at the limiter's starting rate.
LATE = 1.0 / config.FLOOD_START_RATE
BAN = 25429


# ── (b) the pure rule ────────────────────────────────────────────────────────

def test_the_tier_constants_are_the_rulings():
    assert (BASE, SLOW, config.TYPING_AGE_TIER3_INTERVAL) == (4.5, 15.0, 15.0)


@pytest.mark.parametrize("age", [0.0, 1.0, 119.9, 120.0, 599.0, 599.9])
def test_typing_interval_is_the_base_under_ten_minutes(age):
    assert typing_interval(age, True) == pytest.approx(BASE)


@pytest.mark.parametrize("age", [600.0, 601.0, 3599.9])
def test_typing_interval_is_fifteen_seconds_from_ten_minutes(age):
    assert typing_interval(age, True) == pytest.approx(SLOW)


@pytest.mark.parametrize("age", [3600.0, 3601.0, 4 * HOUR, 7 * 24 * HOUR])
def test_typing_interval_is_fifteen_seconds_past_an_hour(age):
    assert typing_interval(age, True) == pytest.approx(
        config.TYPING_AGE_TIER3_INTERVAL)


@pytest.mark.parametrize("age", [0.0, 599.9, 600.0, 3600.0, 12 * HOUR])
def test_typing_interval_is_the_base_always_when_disabled(age):
    assert typing_interval(age, False) == pytest.approx(BASE)


def test_typing_interval_negative_age_is_the_base():
    """Error guessing: a clock step backwards must not make a turn "old"."""
    assert typing_interval(-30.0, True) == pytest.approx(BASE)


def test_typing_interval_young_turn_uses_the_base_it_is_given():
    assert typing_interval(30.0, True, base=3.0) == pytest.approx(3.0)


def test_typing_interval_disabled_uses_the_base_it_is_given():
    assert typing_interval(2 * HOUR, False, base=3.0) == pytest.approx(3.0)


# ── helpers for the loop rows ────────────────────────────────────────────────

def _bot(mk_bot, vbot, rich_http, vloop):
    rich_http.clock = vloop.time
    bot = mk_bot()
    bot._app.bot = vbot
    # A DM that already has its pinned dashboard, as every live one does:
    # on a fresh bot ``notify`` creates it, and the gated double's answer
    # makes it do so repeatedly — ornament traffic no real chat has.
    bot.registry.pinned_msg_id = 9999
    return bot


def _session(bot, vloop, label, *, age, msg_id=77, chat=CHAT,
             status=Status.BUSY, override=None):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = chat
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = 0.0
    if override is not None:
        sess.override_card_age_decay = override
    return sess


def _bubbles(vbot, chat=CHAT):
    return vbot.stamps("sendChatAction", chat)


def _edits(rich_http, msg_id=None):
    return [t for t, _m in rich_http.edits(msg_id)]


def _wire(vbot, rich_http, chat=CHAT):
    """Every request that reached either transport for *chat*, sorted."""
    out = [t for _e, c, t in vbot.calls if c == chat]
    out += [t for (_e, c, _p), t in zip(rich_http.requests, rich_http.stamps)
            if c == chat]
    return sorted(out)


def _most_in(stamps, window=60.0):
    return max((sum(1 for t in stamps if s <= t < s + window) for s in stamps),
               default=0)


def _gaps(stamps):
    return [b - a for a, b in zip(stamps, stamps[1:])]


def _run_cards(vloop, bot, sessions, seconds, *, hook_every=None):
    """Start each session's card (which brings the chat's ONE typing loop)
    at the current instant, optionally feed ``tool_use`` hooks every
    *hook_every* seconds to each — a number, or a ``(lo, hi)`` range drawn
    from a seeded RNG per session — then stop them. Returns the start."""
    start = vloop.time()

    async def _hooks(sess):
        n = 0
        rng = random.Random(f"{sess.label}-20260923")
        while True:
            if isinstance(hook_every, tuple):
                await asyncio.sleep(rng.uniform(*hook_every))
            else:
                await asyncio.sleep(hook_every)
            n += 1
            await bot.notify(sess, "tool_use", {
                "tool_summary": f"Bash: step {n}", "tool_name": "Bash"})

    async def _go():
        for s in sessions:
            bot._start_animation(s)
        feeders = ([asyncio.ensure_future(_hooks(s)) for s in sessions]
                   if hook_every else [])
        await asyncio.sleep(seconds)
        for f in feeders:
            f.cancel()
        await asyncio.gather(*feeders, return_exceptions=True)
        for s in sessions:
            s.status = Status.IDLE
            bot._stop_animation(s)
        await asyncio.sleep(0)

    vloop.run_until_complete(_go())
    return start


def _run_typing(vloop, bot, sessions, seconds, *, midway=None):
    """Run only the typing loop(s) — one ``_animate_typing`` per session,
    which must collapse to one loop per chat."""
    async def _go():
        tasks = [asyncio.ensure_future(bot._animate_typing(s))
                 for s in sessions]
        if midway is not None:
            await midway()
        await asyncio.sleep(seconds)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    vloop.run_until_complete(_go())


# ── (a) the bubble from the first second; the young card yields ─────────────

def test_a_the_first_bubble_goes_out_in_the_turns_first_second(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    start = _run_cards(vloop, bot, [sess], 10.0)
    bubbles = _bubbles(vbot)
    assert bubbles and bubbles[0] - start < 1.0, bubbles[:3]


def test_a_the_first_bubble_is_not_behind_the_first_card_edit_by_a_second(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The card must not take the bubble's first slot: whichever goes
    first, the bubble is out within a second of the card's first edit."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 10.0)
    assert _bubbles(vbot)[0] - _edits(rich_http)[0] < 1.0


def test_a_a_young_card_and_its_bubble_stay_inside_thirty_a_minute(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0)
    assert _most_in(_wire(vbot, rich_http)) <= config.FLOOD_SUSTAINED_MAX


def test_a_a_streaming_young_card_and_its_bubble_stay_inside_thirty_a_minute(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Error guessing: a hook every 0.5 s pushes the card to edit as fast
    as any path allows — the chat still stays inside the 60 s window."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0, hook_every=0.5)
    assert _most_in(_wire(vbot, rich_http)) <= config.FLOOD_SUSTAINED_MAX


def test_a_the_bubble_is_not_dropped_beside_a_young_card(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0)
    assert max(_gaps(_bubbles(vbot))) <= BASE + LATE + EPS


def test_a_the_bubble_is_not_dropped_beside_a_streaming_young_card(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0, hook_every=0.5)
    assert max(_gaps(_bubbles(vbot))) <= BASE + LATE + EPS


def test_a_the_young_card_is_never_starved_by_the_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """"About every 4.8 s": no gap between the young card's edits is
    longer than 6 s."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0)
    assert max(_gaps(_edits(rich_http))) <= 6.0


def test_a_the_streaming_young_card_is_never_starved_by_the_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 115.0, hook_every=0.5)
    assert max(_gaps(_edits(rich_http))) <= 6.0


def test_a_the_young_card_yields_to_about_one_edit_in_five_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """It yields (fewer than today's ~27 a minute) but only by the
    bubble's share — 11 to 14 edits in a minute of tier 0."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    start = _run_cards(vloop, bot, [sess], 115.0)
    minute = [t for t in _edits(rich_http) if start + 30.0 <= t < start + 90.0]
    assert 11 <= len(minute) <= 14, len(minute)


def test_a_two_young_cards_and_one_bubble_stay_inside_thirty_a_minute(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    a = _session(bot, vloop, "a", age=0.0, msg_id=71)
    b = _session(bot, vloop, "b", age=0.0, msg_id=72)
    _run_cards(vloop, bot, [a, b], 115.0, hook_every=1.0)
    assert _most_in(_wire(vbot, rich_http)) <= config.FLOOD_SUSTAINED_MAX


def test_a_two_young_cards_still_get_one_bubble_per_interval(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    a = _session(bot, vloop, "a", age=0.0, msg_id=71)
    b = _session(bot, vloop, "b", age=0.0, msg_id=72)
    _run_cards(vloop, bot, [a, b], 115.0, hook_every=1.0)
    assert min(_gaps(_bubbles(vbot))) >= BASE - EPS


def test_a_two_quiet_young_cards_are_neither_starved(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Two cards share what the bubble leaves: each still moves at least
    every 12 s (twice the one-card bound)."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    a = _session(bot, vloop, "a", age=0.0, msg_id=71)
    b = _session(bot, vloop, "b", age=0.0, msg_id=72)
    _run_cards(vloop, bot, [a, b], 115.0)
    worst = max(max(_gaps(_edits(rich_http, 71))),
                max(_gaps(_edits(rich_http, 72))))
    assert worst <= 12.0, worst


def test_a_the_bubble_opens_a_second_turn_in_its_first_second(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """"Every turn": a turn that ends and a new one that starts 30 s
    later gets its bubble in its own first second, not the first turn's
    leftover cadence or nothing."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    _run_cards(vloop, bot, [sess], 20.0)

    async def _idle():
        await asyncio.sleep(30.0)
    vloop.run_until_complete(_idle())
    sess.status = Status.BUSY
    sess.busy_started_at = vloop.time()
    start = _run_cards(vloop, bot, [sess], 10.0)
    second = [t for t in _bubbles(vbot) if t >= start]
    assert second and second[0] - start < 1.0, second[:3]


# ── (b) the bubble decays with the OLDEST busy turn ─────────────────────────

def test_b_at_nine_minutes_the_bubble_is_every_four_and_a_half_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The minute that ends at 9:59."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=9 * MIN)
    _run_typing(vloop, bot, [sess], 59.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_at_nine_minutes_no_bubble_is_closer_than_the_base(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=9 * MIN)
    _run_typing(vloop, bot, [sess], 59.0)
    assert min(_gaps(_bubbles(vbot))) >= BASE - EPS


def test_b_from_ten_minutes_and_one_second_the_bubble_is_every_fifteen(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=10 * MIN + 1.0)
    _run_typing(vloop, bot, [sess], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and min(gaps) >= SLOW - EPS, gaps


def test_b_from_ten_minutes_and_one_second_the_bubble_still_flickers(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=10 * MIN + 1.0)
    _run_typing(vloop, bot, [sess], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= SLOW + LATE + EPS, gaps


def test_b_the_pace_changes_as_the_turn_crosses_ten_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """One loop running from 9:30 to 11:00: gaps ending before 10:00 are
    the base, gaps starting after 10:00 are 15 s."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=9 * MIN + 30.0)
    _run_typing(vloop, bot, [sess], 90.0)
    t0 = sess.busy_started_at
    b = _bubbles(vbot)
    before = [y - x for x, y in zip(b, b[1:]) if y - t0 < 600.0]
    after = [y - x for x, y in zip(b, b[1:]) if x - t0 >= 600.0]
    assert (before and all(g <= BASE + LATE + EPS for g in before)
            and after and all(g >= SLOW - EPS for g in after)), (before, after)


def test_b_the_oldest_busy_turn_sets_the_pace_old_first(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    old = _session(bot, vloop, "old", age=30 * MIN, msg_id=71)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    _run_typing(vloop, bot, [old, young], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and min(gaps) >= SLOW - EPS, gaps


def test_b_the_oldest_busy_turn_sets_the_pace_young_first(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The loop belongs to whichever session started it — here the young
    one — but the pace is still the OLDEST busy turn's."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    old = _session(bot, vloop, "old", age=30 * MIN, msg_id=71)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    _run_typing(vloop, bot, [young, old], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and min(gaps) >= SLOW - EPS, gaps


def test_b_an_old_turn_joining_a_young_chat_slows_its_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    old = _session(bot, vloop, "old", age=30 * MIN, msg_id=71,
                   status=Status.IDLE)
    mark = {}

    async def _join():
        await asyncio.sleep(20.0)
        old.status = Status.BUSY
        old.busy_started_at = vloop.time() - 30 * MIN
        mark["t"] = vloop.time()
        asyncio.ensure_future(bot._animate_typing(old))

    _run_typing(vloop, bot, [young], 60.0, midway=_join)
    b = [t for t in _bubbles(vbot) if t >= mark["t"] + SLOW + LATE]
    assert len(b) >= 2 and min(_gaps(b)) >= SLOW - EPS, b


def test_b_when_the_old_turn_ends_the_young_ones_pace_returns(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """"Oldest BUSY turn": an old session going idle leaves the chat's
    oldest busy turn young again, and the bubble is back to the base."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    old = _session(bot, vloop, "old", age=30 * MIN, msg_id=71)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    mark = {}

    async def _end_old():
        await asyncio.sleep(20.0)
        old.status = Status.IDLE
        mark["t"] = vloop.time()

    _run_typing(vloop, bot, [old, young], 80.0, midway=_end_old)
    b = [t for t in _bubbles(vbot) if t >= mark["t"] + SLOW + LATE]
    gaps = _gaps(b)
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_an_idle_old_session_does_not_slow_a_young_busy_one(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    _session(bot, vloop, "old", age=3 * HOUR, msg_id=71, status=Status.IDLE)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    _run_typing(vloop, bot, [young], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_an_old_turn_in_another_chat_does_not_slow_this_ones_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    other = _session(bot, vloop, "old", age=3 * HOUR, msg_id=71,
                     chat=111222333)
    young = _session(bot, vloop, "young", age=1 * MIN, msg_id=72)
    _run_typing(vloop, bot, [other, young], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_preference_off_keeps_an_old_turns_bubble_at_the_base(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    prefs.set_preference(CHAT, "card_age_decay", False)
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=2 * HOUR)
    _run_typing(vloop, bot, [sess], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_a_session_override_off_keeps_an_old_turns_bubble_at_the_base(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=2 * HOUR, override=False)
    _run_typing(vloop, bot, [sess], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and max(gaps) <= BASE + LATE + EPS, gaps


def test_b_preference_off_never_sends_faster_than_the_base(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    prefs.set_preference(CHAT, "card_age_decay", False)
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=2 * HOUR)
    _run_typing(vloop, bot, [sess], 60.0)
    assert min(_gaps(_bubbles(vbot))) >= BASE - EPS


def test_b_a_session_override_on_beats_a_scope_off(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    prefs.set_preference(CHAT, "card_age_decay", False)
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=2 * HOUR, override=True)
    _run_typing(vloop, bot, [sess], 60.0)
    gaps = _gaps(_bubbles(vbot))
    assert gaps and min(gaps) >= SLOW - EPS, gaps


# ── (b) long turns never go dark ─────────────────────────────────────────────

def _two_hour_run(mk_bot, vbot, vloop, rich_http, *, sessions=1,
                  hook_every=5.0, sample=None):
    """*sessions* sessions streaming into one DM for two hours from turn
    start — cards, a ``tool_use`` hook every *hook_every* seconds to each
    (a working turn's realistic spacing), and the chat's one bubble.
    Returns ``(start, end, bubbles)``; *sample*, if given, is called once
    a minute."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    ss = [_session(bot, vloop, f"s{i}", age=0.0, msg_id=71 + i)
          for i in range(sessions)]
    if sample is not None:
        async def _sampler():
            while True:
                await asyncio.sleep(60.0)
                sample()
        vloop.call_soon(lambda: asyncio.ensure_future(_sampler()))
    start = _run_cards(vloop, bot, ss, 2 * HOUR, hook_every=hook_every)
    return start, start + 2 * HOUR, _bubbles(vbot)


def _dark(start, end, bubbles):
    """The longest stretch without a bubble, the run's ends included."""
    return max(_gaps([start] + bubbles + [end]))


def test_b_one_streaming_two_hour_turn_never_goes_dark(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    start, end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http)
    assert _dark(start, end, bubbles) <= SLOW + 1.0 + EPS


def test_b_two_quiet_two_hour_turns_never_go_dark(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    start, end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http,
                                        sessions=2, hook_every=None)
    assert _dark(start, end, bubbles) <= SLOW + 1.0 + EPS


def test_b_two_streaming_two_hour_turns_never_go_dark(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """vm3's own shape — two sessions working in one DM, a tool call every
    2–8 s each (seeded) — for two hours. "Flicker, never dark"."""
    start, end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http,
                                        sessions=2, hook_every=(2.0, 8.0))
    assert _dark(start, end, bubbles) <= SLOW + 1.0 + EPS


def test_b_two_streaming_two_hour_turns_never_shed_the_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The decay exists so the hour never reaches the 75 % shed: sampled
    once a minute, the chat's bubble is never shed."""
    shed: list[bool] = []
    _two_hour_run(mk_bot, vbot, vloop, rich_http, sessions=2,
                  hook_every=(2.0, 8.0), sample=lambda: shed.append(
                      vlimiter.hourly_usage(CHAT)["typing_shed"]))
    assert len(shed) >= 100 and not any(shed), shed.count(True)


def test_b_a_two_hour_turns_bubble_decays_after_ten_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    start, _end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http)
    late = [t for t in bubbles if t >= start + 10 * MIN + SLOW]
    assert min(_gaps(late)) >= SLOW - EPS


def test_b_a_two_hour_turns_first_ten_minutes_are_the_base(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    start, _end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http)
    early = [t for t in bubbles if t < start + 10 * MIN]
    assert max(_gaps(early)) <= BASE + LATE + EPS


def test_b_a_two_hour_turn_keeps_the_bubble_inside_its_hourly_share(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The reason for the decay: in the second hour the bubble alone is
    about 240 calls, not 800."""
    start, _end, bubbles = _two_hour_run(mk_bot, vbot, vloop, rich_http)
    second_hour = [t for t in bubbles if start + HOUR <= t < start + 2 * HOUR]
    assert 200 <= len(second_hour) <= 245, len(second_hour)


# ── (c) a ban answered to the bubble mutes the chat ─────────────────────────

ANSWER = "the answer the ban must not eat"


def _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http, *, flush_twice=False):
    """A young turn with its card and bubble. Telegram answers the first
    ``sendChatAction`` with retry_after 25429. The turn ends 60 s later
    with its answer; the answer is re-offered (``held_answer_flush``)
    during the ban and again after it lifts. Returns a dict of what was
    observed."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "vm3", age=0.0)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(BAN)
    out: dict = {}

    async def _go():
        bot._start_animation(sess)
        await asyncio.sleep(60.0)
        out["muted"] = MUTE.is_muted(CHAT)
        sess.status = Status.IDLE
        bot._stop_animation(sess)
        await bot.notify(sess, "idle_prompt", {"summary": ANSWER})
        out["held"] = HELD.count(CHAT)
        await asyncio.sleep(600.0)
        await bot.notify(sess, "held_answer_flush", {})
        out["held_mid_ban"] = HELD.count(CHAT)
        out["banned_at"] = _bubbles(vbot)[0]
        await asyncio.sleep(BAN + 5.0 - (vloop.time() - out["banned_at"]))
        out["lift_seen_at"] = vloop.time()
        await bot.notify(sess, "held_answer_flush", {})
        if flush_twice:
            await bot.notify(sess, "held_answer_flush", {})
        await asyncio.sleep(1.0)
        out["held_after"] = HELD.count(CHAT)

    vloop.run_until_complete(_go())
    out["wire"] = _wire(vbot, rich_http)
    out["answers"] = [p for (e, c, p) in rich_http.requests
                      if c == CHAT and ANSWER in str(p)]
    out["answers"] += [c for c in vbot.calls
                       if c[0] == "sendMessage" and c[1] == CHAT]
    return out


def test_c_the_ban_is_answered_to_the_turns_first_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Setup check: the bubble really went out (so the ban was answered
    to it) at the turn's start."""
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert out["banned_at"] - out["wire"][0] < 1.0


def test_c_a_ban_on_the_bubble_mutes_the_chat(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert out["muted"] is True


def test_c_zero_requests_into_the_ban_after_the_bubble(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Cards, further bubbles, the answer, re-offers of the answer: none
    of it reaches either transport between the ban and its lift."""
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    into = [t for t in out["wire"]
            if out["banned_at"] < t < out["banned_at"] + BAN]
    assert into == [], len(into)


def test_c_the_answer_is_held_during_the_ban(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert out["held"] == 1


def test_c_a_re_offer_during_the_ban_keeps_the_answer_held(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert out["held_mid_ban"] == 1


def test_c_the_answer_is_delivered_once_after_the_lift(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert len(out["answers"]) == 1, out["answers"]


def test_c_the_answer_is_not_delivered_twice_by_a_second_flush(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http, flush_twice=True)
    assert len(out["answers"]) == 1, out["answers"]


def test_c_nothing_is_left_held_after_delivery(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    assert out["held_after"] == 0


def test_c_the_answer_goes_after_the_lift_not_before(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    out = _ban_on_the_bubble(mk_bot, vbot, vloop, rich_http)
    after = [t for t in out["wire"] if t >= out["banned_at"] + BAN]
    assert after and min(after) >= out["lift_seen_at"] - EPS


def _mute_after_bubble_429(mk_bot, vbot, vloop, rich_http, retry_after):
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(retry_after)
    seen = {}

    async def _go():
        bot._start_animation(sess)
        await asyncio.sleep(3.0)
        seen["muted"] = MUTE.is_muted(CHAT)
        sess.status = Status.IDLE
        bot._stop_animation(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(_go())
    return seen["muted"]


def test_c_a_small_429_on_the_bubble_does_not_mute_the_chat(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Boundary: the mute is for a BAN. A retry_after of 5 on the bubble
    is the warning regime (Q5), not a mute."""
    assert _mute_after_bubble_429(mk_bot, vbot, vloop, rich_http, 5) is False


def test_c_a_retry_after_at_the_ban_threshold_does_not_mute(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """``TELEGRAM_MAX_RETRY_AFTER`` itself is still a small 429 (a ban is
    strictly longer), on the bubble as on any call."""
    assert _mute_after_bubble_429(
        mk_bot, vbot, vloop, rich_http,
        int(config.TELEGRAM_MAX_RETRY_AFTER)) is False


def test_c_a_retry_after_just_past_the_ban_threshold_mutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert _mute_after_bubble_429(
        mk_bot, vbot, vloop, rich_http,
        int(config.TELEGRAM_MAX_RETRY_AFTER) + 1) is True


def test_c_a_small_429_on_the_bubble_does_not_pause_the_young_card(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Q5: a typing 429 blocks the BUBBLE for its retry_after, not the
    cards — the young card keeps editing inside a 20 s block."""
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    sess = _session(bot, vloop, "dev", age=0.0)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(20)
    _run_cards(vloop, bot, [sess], 30.0)
    t429 = _bubbles(vbot)[0]
    inside = [t for t in _edits(rich_http) if t429 < t < t429 + 20.0]
    assert len(inside) >= 3, inside


def test_c_a_ban_on_the_bubble_in_one_chat_leaves_another_chat_speaking(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    other = 111222333
    bot = _bot(mk_bot, vbot, rich_http, vloop)
    a = _session(bot, vloop, "a", age=0.0, msg_id=71)
    b = _session(bot, vloop, "b", age=0.0, msg_id=72, chat=other)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(BAN)
    _run_cards(vloop, bot, [a, b], 60.0)
    first = [c for e, c, _t in vbot.calls if e == "sendChatAction"][0]
    spared = other if first == CHAT else CHAT
    assert len(_bubbles(vbot, spared)) >= 10
