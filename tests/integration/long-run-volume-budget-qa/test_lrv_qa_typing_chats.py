"""R2 across CHATS: one typing loop per chat — not one per session, and
not one for the whole daemon. Two chats get a bubble each; a chat's
latch, mute, 429 or ban is that chat's alone; a session that moves chats
takes its bubble with it; and a ban answered to the bubble itself
(vm3's first 429 was on typing) puts nothing more on the wire.

Runs the REAL typing loop on the harness's virtual event loop, every call
through the real limiter (``vbot``).
"""

from __future__ import annotations

import asyncio

from telegram.error import RetryAfter

from aipager import config
from aipager.bot.flood import MUTE
from aipager.state import Status

CHAT = 256113222
OTHER = 111222333
INTERVAL = config.TYPING_INDICATOR_INTERVAL
EPS = 1e-6


def session(bot, vloop, label, chat, *, msg_id=70, status=Status.BUSY):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = chat
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    # AMENDED by the developer for operator ruling #2 (8.30): was 10 min,
    # which is now the bubble's 15 s tier; these rows count a 4.5 s bubble,
    # so the turn is 5 min old (still a 10 s-tier card, still under 10 min
    # for the whole of the longest 60 s run).
    sess.busy_started_at = vloop.time() - 5 * 60.0
    sess.last_tool_edit_at = vloop.time()
    return sess


def typing_bot(mk_bot, vbot):
    bot = mk_bot()
    bot._app.bot = vbot
    return bot


def run_loops(vloop, bot, sessions, seconds, *, midway=None, live=None):
    async def _drive():
        tasks = [asyncio.ensure_future(bot._animate_typing(s)) for s in sessions]
        await asyncio.sleep(1.0)
        if live is not None:
            live.append(sum(not t.done() for t in tasks))
        if midway is not None:
            await midway(tasks)
        await asyncio.sleep(max(seconds - 1.0, 0.0))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    vloop.run_until_complete(_drive())


def gaps(stamps):
    return [b - a for a, b in zip(stamps, stamps[1:])]


# ── two chats, two loops ─────────────────────────────────────────────────────

def test_two_chats_run_two_loops(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    live: list[int] = []
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 10.0, live=live)
    assert live == [2]


def test_each_of_two_chats_gets_its_own_bubble(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 60.0)
    assert (len(vbot.stamps("sendChatAction", CHAT)) in (13, 14)
            and len(vbot.stamps("sendChatAction", OTHER)) in (13, 14))


def test_the_second_chats_bubbles_are_spaced_by_the_interval(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 60.0)
    assert min(gaps(vbot.stamps("sendChatAction", OTHER))) >= INTERVAL - EPS


def test_three_sessions_in_two_chats_run_two_loops(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    live: list[int] = []
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT, msg_id=71),
                           session(bot, vloop, "b", CHAT, msg_id=72),
                           session(bot, vloop, "c", OTHER, msg_id=73)],
              10.0, live=live)
    assert live == [2]


def test_three_sessions_in_one_chat_send_one_bubble_per_interval(
    mk_bot, vbot, vloop,
):
    bot = typing_bot(mk_bot, vbot)
    run_loops(vloop, bot, [session(bot, vloop, f"s{i}", CHAT, msg_id=70 + i)
                           for i in range(3)], 60.0)
    assert min(gaps(vbot.stamps("sendChatAction", CHAT))) >= INTERVAL - EPS


def test_no_bubble_when_no_session_in_the_chat_is_busy(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT,
                                   status=Status.IDLE)], 30.0)
    assert vbot.stamps("sendChatAction", CHAT) == []


# ── a chat's own state stays its own ─────────────────────────────────────────

def test_a_muted_chat_does_not_silence_another(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    MUTE.mute(CHAT, 3600.0)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 30.0)
    assert len(vbot.stamps("sendChatAction", OTHER)) >= 6


def test_a_muted_chat_gets_no_bubble_beside_a_live_one(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    MUTE.mute(CHAT, 3600.0)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 30.0)
    assert vbot.stamps("sendChatAction", CHAT) == []


def _shed(vlimiter, vloop, chat=CHAT, used=900):
    vlimiter.restore([{"chat_id": chat,
                       "hourly": [[vloop.wall() - 30.0, 0, used]]}])
    assert vlimiter.hourly_usage(chat)["typing_shed"] is True


def test_a_shed_chat_sends_no_bubble_from_its_loop(mk_bot, vbot, vloop,
                                                    vlimiter):
    """Row L end to end: 900 calls this hour (≥ 75 % of 1080) — the loop
    puts no bubble on the wire."""
    bot = typing_bot(mk_bot, vbot)
    _shed(vlimiter, vloop)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT)], 60.0)
    assert vbot.stamps("sendChatAction", CHAT) == []


def test_a_shed_chat_does_not_shed_another(mk_bot, vbot, vloop, vlimiter):
    bot = typing_bot(mk_bot, vbot)
    _shed(vlimiter, vloop)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT),
                           session(bot, vloop, "b", OTHER)], 60.0)
    assert len(vbot.stamps("sendChatAction", OTHER)) >= 12


def test_a_typing_429_in_one_chat_leaves_the_other_chats_cadence(
    mk_bot, vbot, vloop,
):
    bot = typing_bot(mk_bot, vbot)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(30)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT)], 1.0)
    first_chat = vbot.calls[0][1]
    other = OTHER if first_chat == CHAT else CHAT
    run_loops(vloop, bot, [session(bot, vloop, "b", other)], 30.0)
    assert len(vbot.stamps("sendChatAction", other)) >= 6


# ── a typing 429 and a typing ban through the loop ───────────────────────────

def test_the_loop_survives_a_small_typing_429(mk_bot, vbot, vloop):
    """Error guessing: the loop must not die on its own 429 — the bubble
    comes back once retry_after has run out."""
    bot = typing_bot(mk_bot, vbot)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(5)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT)], 30.0)
    assert len(vbot.stamps("sendChatAction", CHAT)) >= 3


def test_no_bubble_inside_a_small_typing_429s_retry_after(mk_bot, vbot, vloop):
    bot = typing_bot(mk_bot, vbot)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(12)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT)], 30.0)
    stamps = vbot.stamps("sendChatAction", CHAT)
    assert len(stamps) < 2 or stamps[1] - stamps[0] >= 12.0 - EPS


def test_a_ban_answered_to_the_bubble_gets_zero_further_requests(
    mk_bot, vbot, vloop,
):
    """vm3: Telegram's first answer was to the TYPING call. If that answer
    is a ban (retry_after 25429 s), the loop must put nothing more into the
    chat — no bubble every 4.5 s into a live ban (R7: zero requests into
    a ban, bubble included)."""
    bot = typing_bot(mk_bot, vbot)
    vbot.fail["sendChatAction"] = lambda: RetryAfter(25429)
    run_loops(vloop, bot, [session(bot, vloop, "a", CHAT)], 600.0)
    assert len(vbot.stamps("sendChatAction", CHAT)) == 1


# ── a session that moves between chats ──────────────────────────────────────

def test_a_session_that_moves_away_stops_the_old_chats_bubble(
    mk_bot, vbot, vloop,
):
    bot = typing_bot(mk_bot, vbot)
    sess = session(bot, vloop, "a", CHAT)
    marks = {}

    async def _move(_tasks):
        await asyncio.sleep(10.0)
        sess.scope_chat_id = OTHER
        marks["moved"] = vloop.time()

    run_loops(vloop, bot, [sess], 60.0, midway=_move)
    late = [t for t in vbot.stamps("sendChatAction", CHAT)
            if t > marks["moved"] + INTERVAL + EPS]
    assert late == []


def test_a_moved_session_restarted_gets_the_new_chats_bubble(
    mk_bot, vbot, vloop,
):
    bot = typing_bot(mk_bot, vbot)
    sess = session(bot, vloop, "a", CHAT)
    run_loops(vloop, bot, [sess], 10.0)
    sess.scope_chat_id = OTHER
    run_loops(vloop, bot, [sess], 30.0)
    assert len(vbot.stamps("sendChatAction", OTHER)) >= 6


def test_a_session_moving_into_a_busy_chat_does_not_double_its_bubble(
    mk_bot, vbot, vloop,
):
    """Two sessions, one per chat; the second moves into the first's chat
    and is restarted there. The chat still gets one bubble per interval."""
    bot = typing_bot(mk_bot, vbot)
    a = session(bot, vloop, "a", CHAT, msg_id=71)
    b = session(bot, vloop, "b", OTHER, msg_id=72)

    async def _move(tasks):
        await asyncio.sleep(10.0)
        b.scope_chat_id = CHAT
        tasks.append(asyncio.ensure_future(bot._animate_typing(b)))

    run_loops(vloop, bot, [a, b], 60.0, midway=_move)
    assert min(gaps(vbot.stamps("sendChatAction", CHAT))) >= INTERVAL - EPS


# ── a ban in one chat, with cards and bubbles live in two ────────────────────

def _two_chat_ban(mk_bot, vbot, vloop, rich_http):
    """Two long-running cards (typing loops included), one per chat. CHAT
    is banned for 30 min at +5 min. Returns ``(window, wire)`` where wire
    is every request on either transport as ``(chat, t)``."""
    rich_http.clock = vloop.time
    bot = typing_bot(mk_bot, vbot)
    a = session(bot, vloop, "a", CHAT, msg_id=71)
    b = session(bot, vloop, "b", OTHER, msg_id=72)
    for s in (a, b):
        s.busy_started_at = vloop.time() - 20 * 60.0
        s.last_tool_edit_at = 0.0
    window = {}

    async def _go():
        bot._start_animation(a)
        bot._start_animation(b)
        await asyncio.sleep(300.0)
        MUTE.mute(CHAT, 1800.0)
        window["from"] = vloop.time()
        await asyncio.sleep(1799.0)
        window["to"] = vloop.time()
        await asyncio.sleep(300.0)
        for s in (a, b):
            s.status = Status.IDLE
            bot._stop_animation(s)
        await asyncio.sleep(0)

    vloop.run_until_complete(_go())
    wire = [(c, t) for _e, c, t in vbot.calls]
    wire += [(c, t) for (_e, c, _p), t in zip(rich_http.requests,
                                              rich_http.stamps)]
    return window, wire


def test_zero_requests_into_a_banned_chat_with_bubbles_live(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    window, wire = _two_chat_ban(mk_bot, vbot, vloop, rich_http)
    into = [t for c, t in wire
            if c == CHAT and window["from"] <= t <= window["to"]]
    assert into == []


def test_the_other_chat_keeps_its_bubble_through_the_ban(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    window, _wire = _two_chat_ban(mk_bot, vbot, vloop, rich_http)
    during = [t for t in vbot.stamps("sendChatAction", OTHER)
              if window["from"] <= t <= window["to"]]
    assert len(during) >= 100


def test_the_banned_chat_had_traffic_before_the_ban(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Control for the zero-requests row: the chat was live until the ban,
    so "zero" is not an artefact of a chat that never spoke."""
    window, wire = _two_chat_ban(mk_bot, vbot, vloop, rich_http)
    assert any(c == CHAT and t < window["from"] for c, t in wire)


def test_the_banned_chat_speaks_again_after_the_ban(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    window, wire = _two_chat_ban(mk_bot, vbot, vloop, rich_http)
    assert any(c == CHAT and t > window["to"] for c, t in wire)
