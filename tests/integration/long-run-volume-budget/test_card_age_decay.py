"""Rows D, E, O, P, Q, R, T (roadmap 8.30 R4, rulings Q2/Q3, "other
counters"): a busy card's cadence and elapsed unit decay with turn age.

vm3, 2026-09-23: bigdog's ONE turn lasted four hours, and its card was
edited on the same few-second cadence in hour three as in second three —
some three thousand edits of one message, plus every hook event (676
phantom SubagentStops among them) racing the animator for a 1.2 s debounce.

Now: from 2 / 10 / 60 min of turn age the card is edited no more often than
every 10 / 30 / 60 s, its counters switch to minutes and then hours so it
never looks frozen, and only a STATE change (busy -> waiting, a prompt) may
jump the queue — once, and at most once per 10 s.

Every row runs the REAL animator and the REAL notify dispatcher on a
virtual event loop, with the rich card path metered by the real limiter
over an ``httpx.MockTransport``. Nothing sleeps for real.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from aipager.session_monitor import (
    CARD_STALE_SECONDS,
    busy_card_watchdog_action,
    card_stale_after,
)
from aipager.state import Status, TrackedSession

CHAT = 256113222
MIN = 60.0
HOUR = 3600.0
EPS = 1e-3


def _card(mk_bot, vbot, vloop, rich_http, *, age: float, label: str = "dev",
          msg_id: int = 77):
    """A BUSY session with a live card whose turn is *age* seconds old."""
    rich_http.clock = vloop.time
    bot = mk_bot()
    bot._app.bot = vbot
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = 0.0
    return bot, sess


def _gaps(edits) -> list[float]:
    stamps = [t for t, _text in edits]
    return [b - a for a, b in zip(stamps, stamps[1:])]


def _status(markdown: str) -> str:
    return markdown.rsplit("\n", 1)[-1]


def _run_animator(vloop, bot, sess, seconds: float) -> None:
    async def _go():
        task = asyncio.ensure_future(bot._animate_busy(sess))
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    vloop.run_until_complete(_go())


# ── D ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("age,run,min_gap,max_gap,elapsed", [
    # Tier 0 is today exactly: a QUIET card (no new prose) is gated by
    # BUSY_EDIT_INTERVAL x margin (3.3 s) on the 2.2 s wake grid, so it
    # edits every 4.4 s — the same number 0.7.13 gives.
    (30.0, 60.0, 4.4, 4.4, r"(?:\d+m )?\d+s"),
    (5 * MIN, 2 * MIN, 10.0, 12.2, r"\d+m \d+s"),    # 10 s, seconds shown
    (30 * MIN, 3 * MIN, 30.0, 62.2, r"\d+m"),        # 30 s, minutes shown
    (2 * HOUR, 5 * MIN, 60.0, 62.2, r"\d+h \d+m"),   # 60 s, hours shown
])
def test_d_the_card_cadence_and_unit_follow_the_turns_age(
    mk_bot, vbot, vloop, vlimiter, rich_http, age, run, min_gap, max_gap,
    elapsed,
):
    """Row D (R4). One card alone in a DM, driven by the real animator: at
    30 s the gaps are today's (4.4 s for a quiet card); at 5 min ≥ 10 s;
    at 30 min ≥ 30 s; at 2 h ≥ 60 s. The status line's counter reads ``31s`` / ``5m 12s`` /
    ``31m`` / ``2h 1m`` — the unit matches the refresh, so a slow card
    still shows a counter that moves. On 0.7.13 every one of these cards
    edits every 4.4 s and counts seconds.

    Mutation: drop the age floor from ``_card_edit_due`` and every gap is
    4.4 s; pass ``unit`` nowhere and the 2 h card reads ``120m 13s``.
    """
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=age)
    _run_animator(vloop, bot, sess, run)
    edits = rich_http.edits()
    assert len(edits) >= 3, edits
    gaps = _gaps(edits)
    assert min(gaps) >= min_gap - EPS, gaps
    assert max(gaps) <= max_gap + EPS, gaps
    for _t, markdown in edits:
        status = _status(markdown)
        assert re.search(rf"· {elapsed}$", status), status


# ── E / Q: only a state change bypasses the tier ─────────────────────────────

async def _edit_now(bot, sess) -> None:
    """Land one ordinary card edit, the way a due tick does."""
    assert await bot._edit_busy_rich(sess, "Working") is True


def _to_waiting(sess) -> None:
    """The interim Stop of a job whose background agent is still running."""
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": 0.0}
    sess.status = Status.IDLE
    assert sess.job_background_open()


def test_e_a_tool_row_waits_for_the_tier_and_a_state_change_does_not(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row E, as amended by Q2. A card at 2 h:

    1. a new tool row causes NO immediate edit, and appears on the next
       edit, ≥ 60 s after the last one;
    2. BUSY -> waiting (a job interim) causes ONE immediate edit, and the
       card is back on its 60 s tier after it;
    3. a second state change inside 10 s does not edit early: it waits out
       the 10 s bypass gap, then shows once, and the tier resumes.

    Mutation: let a new tool row bypass (or drop the frame-state compare)
    and step 1 edits at once; drop the bypass and step 2 waits a minute;
    drop the 10 s gap and step 3 edits again.
    """
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=2 * HOUR)

    async def _drive():
        await _edit_now(bot, sess)
        t0 = vloop.time()
        await asyncio.sleep(5)
        await bot.notify(sess, "tool_use", {"tool_summary": "Bash: make",
                                            "tool_name": "Bash"})
        assert len(rich_http.edits()) == 1, "a tool row jumped the tier"
        await asyncio.sleep(70)            # the animator the hook resumed
        after_tool = rich_http.edits()[1:]
        assert after_tool, "the tool row never reached the card"
        assert after_tool[0][0] - t0 >= 60.0 - EPS
        assert "Bash: make" in after_tool[0][1]

        # 2. a state change: shown at once.
        before = len(rich_http.edits())
        _to_waiting(sess)
        t1 = vloop.time()
        await bot.notify(sess, "idle_prompt", {"raw_md": "", "summary": ""})
        edits = rich_http.edits()
        assert len(edits) == before + 1, "the state change waited for the tier"
        assert edits[-1][0] - t1 < 1.0
        assert "still working" in _status(edits[-1][1])

        # 3. a second state change 4 s later: no early edit — nothing
        # before the 10 s bypass gap has passed; then it takes the next
        # bypass, once, and the card is back on its 60 s tier.
        await asyncio.sleep(4)
        sess.status = Status.BUSY
        sess.job_continuation_active = True
        await bot.notify(sess, "job_continuation", {})
        await asyncio.sleep(5.9 - (vloop.time() - t1 - 4))
        assert len(rich_http.edits()) == before + 1, "a second bypass inside 10 s"
        await asyncio.sleep(3 * MIN)
        later = rich_http.edits()[before:]
        assert later[1][0] - t1 >= 10.0 - EPS
        assert "still working" not in _status(later[1][1])
        gaps = _gaps(later[1:])
        assert gaps and min(gaps) >= 60.0 - EPS, gaps

    vloop.run_until_complete(_drive())
    bot._stop_animation(sess)


def test_q_two_state_changes_four_seconds_apart_edit_once(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row Q (Q2). At 30 min (the 30 s tier): busy -> waiting, then back
    four seconds later — exactly ONE immediate edit; the second change
    rides the tier. Mutation: drop ``CARD_STATE_BYPASS_MIN_GAP`` and both
    edit."""
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=30 * MIN)

    async def _drive():
        await _edit_now(bot, sess)
        await asyncio.sleep(5)
        _to_waiting(sess)
        await bot.notify(sess, "idle_prompt", {"raw_md": "", "summary": ""})
        await asyncio.sleep(4)
        sess.status = Status.BUSY
        sess.job_continuation_active = True
        await bot.notify(sess, "job_continuation", {})
        await asyncio.sleep(0.5)

    vloop.run_until_complete(_drive())
    assert len(rich_http.edits()) == 2, rich_http.edits()


# ── O / P: hook-driven edits obey the tier ───────────────────────────────────

_EVENTS = (
    ("tool_use", {"tool_summary": "Read: /x", "tool_name": "Read"}),
    ("tool_done", {"tool_summary": "Read: /x"}),
    ("subagent_start", {"agent_type": "Explore", "agent_id": "a9"}),
    ("subagent_stop", {"agent_type": "Explore", "elapsed": 2.0,
                       "history_idx": None, "tool_count": 1}),
    ("assistant_text", {"delta": "Looking at the next file.",
                        "message_id": "m1"}),
)


def test_o_a_busy_hook_stream_at_two_hours_costs_one_edit_a_minute(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row O (Q3). A 2 h card, and every hook event kind — tool_use,
    tool_done, subagent_start, subagent_stop, assistant_text — one every
    3 s for 5 minutes, with the animator the first tool_use resumes: at
    most 6 edits (five on the 60 s tier plus at most one bypass). On
    0.7.13 each hook edits on a 1.2 s debounce: ~250.

    Mutation: put any one notify path back on a bare
    ``STREAM_EDIT_INTERVAL`` comparison and the count jumps.
    """
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=2 * HOUR)

    async def _drive():
        for i in range(100):                        # 100 x 3 s = 5 min
            event, ctx = _EVENTS[i % len(_EVENTS)]
            await bot.notify(sess, event, dict(ctx))
            await asyncio.sleep(3)

    vloop.run_until_complete(_drive())
    bot._stop_animation(sess)
    edits = rich_http.edits()
    assert 1 <= len(edits) <= 6, [t for t, _m in edits]


@pytest.mark.parametrize("event,ctx", _EVENTS, ids=[e for e, _c in _EVENTS])
def test_o_each_hook_path_waits_for_the_tier(
    mk_bot, vbot, vloop, vlimiter, rich_http, event, ctx,
):
    """Row O, one path at a time, so a mutation names the path: a 2 h card
    edited 10 s ago, then one hook event — the gate lets NO edit through.
    On 0.7.13 each of these attempts one at once (10 s clears the 1.2 s
    debounce).

    Counted as edit ATTEMPTS through the real ``_edit_busy_rich``, not as
    POSTs: a hook-live sentence stays off the card until a tool row
    follows it, so an ``assistant_text`` edit would dedupe to no POST and
    the row would pass whether or not the gate held. The animator is not
    running (a tool_use would resume it — another path's business).
    """
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=2 * HOUR)
    sess.animate_task = asyncio.Future(loop=vloop)   # "running": no resume
    sess.record_tool("Read: /x", False)              # for tool_done to settle
    real = bot._edit_busy_rich
    attempts: list[str] = []

    async def _counting(*args, **kwargs):
        attempts.append("edit")
        return await real(*args, **kwargs)

    async def _drive():
        await _edit_now(bot, sess)
        bot._edit_busy_rich = _counting
        await asyncio.sleep(10)
        await bot.notify(sess, event, dict(ctx))

    vloop.run_until_complete(_drive())
    assert attempts == [], f"{event} edited inside the 60 s tier"
    assert len(rich_http.edits()) == 1, rich_http.edits()


def test_p_phantom_subagent_stops_never_edit_before_the_tier(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row P (Q2). A phantom SubagentStop — empty type, unknown id, 0.0 s —
    every second at 2 h: no edit before the 60 s tier. vm3 took 676 of
    them in three hours, each a candidate edit on a 1.2 s debounce.

    Mutation: treat a subagent event as a state change and the first
    phantom edits.
    """
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=2 * HOUR)
    phantom = {"agent_type": "", "elapsed": 0.0, "history_idx": None,
               "tool_count": 0}

    async def _drive():
        await _edit_now(bot, sess)
        for _ in range(58):
            await asyncio.sleep(1)
            await bot.notify(sess, "subagent_stop", dict(phantom))

    vloop.run_until_complete(_drive())
    assert len(rich_http.edits()) == 1


# ── R: every counter on the card uses the tier's unit ────────────────────────

def _one_render(mk_bot, vbot, vloop, rich_http, *, age, agents, waiting=False):
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=age)
    now = vloop.time()
    for n, agent_age in enumerate(agents):
        idx = sess.record_tool("\U0001f916 Explore", False)
        sess.active_subagents[f"a{n}"] = {
            "type": "Explore", "started_at": now - agent_age, "history_idx": idx}
    if waiting:
        sess.status = Status.IDLE
    vloop.run_until_complete(bot._edit_busy_rich(sess, "Working", waiting=waiting))
    return rich_http.edits()[-1][1]


def test_r_agent_rows_and_the_waiting_frame_count_in_minutes_when_slow(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row R ("other counters"). At the 60 s tier a live agent row counts
    ``<1m`` / ``12m`` / ``1h 3m``, and so does the waiting frame's line —
    a seconds counter there would look frozen for 59 seconds of every 60.

    Mutation: leave the agent row (or the waiting frame) on seconds.
    """
    card = _one_render(mk_bot, vbot, vloop, rich_http, age=2 * HOUR,
                       agents=[30.0, 12 * MIN, 63 * MIN])
    assert "Explore · starting · <1m" in card
    assert "Explore · starting · 12m" in card
    assert "Explore · starting · 1h 3m" in card
    assert not re.search(r"\d+m \d+s", card), card

    waiting = _one_render(mk_bot, vbot, vloop, rich_http, age=2 * HOUR,
                          agents=[12 * MIN], waiting=True)
    assert re.search(r"still working · 2h 0m$", _status(waiting)), waiting


def test_r_under_ten_minutes_the_counters_read_exactly_as_today(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Below the minutes tier nothing about the text changes: ``4m 10s``.
    Mutation: switch units at the 10 s tier and a 4-minute card loses its
    seconds."""
    card = _one_render(mk_bot, vbot, vloop, rich_http, age=250.0,
                       agents=[250.0])
    assert "Explore · starting · 4m 10s" in card
    assert re.search(r"· 4m 10s$", _status(card)), card


def test_r_the_final_card_keeps_todays_format(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """The settled card is not a live counter: its duration reads as it
    always has, even for a 2 h turn."""
    bot, sess = _card(mk_bot, vbot, vloop, rich_http, age=2 * HOUR)
    vloop.run_until_complete(bot._edit_busy_rich(sess, "Done", final=True))
    assert re.search(r"· 120m 0s$", _status(rich_http.edits()[-1][1]))


# ── T: the stale-card watchdog knows the tier ────────────────────────────────

def _watched(now: float, *, age: float, edited_ago: float) -> TrackedSession:
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.busy_started_at = now - age
    sess.last_tool_edit_at = now - edited_ago

    class _Live:
        def done(self):
            return False
    sess.animate_task = _Live()
    return sess


@pytest.mark.parametrize("age,edited_ago,expect", [
    (2 * HOUR, 40.0, None),            # 60 s tier: 40 s is on schedule
    (2 * HOUR, 125.0, "refresh"),      # ...125 s is not
    (30 * MIN, 45.0, None),            # 30 s tier: stale after 60 s
    (30 * MIN, 61.0, "refresh"),
    (60.0, 25.0, "refresh"),           # tier 0: the 20 s rule, unchanged
    (60.0, 15.0, None),
])
def test_t_the_watchdog_waits_for_twice_the_tier(age, edited_ago, expect):
    """Row T. A card on the 60 s tier last edited 40 s ago is on schedule —
    no forced refresh (0.7.13 forced one at 20 s: "forced stale-card
    refresh (63s since last edit)" on vm3). At 125 s it is stale. At tier
    0 the 20 s rule is unchanged.

    Mutation: pass ``CARD_STALE_SECONDS`` instead of ``stale_after`` and
    the 40 s row refreshes.
    """
    now = 5_000_000.0
    sess = _watched(now, age=age, edited_ago=edited_ago)
    action = busy_card_watchdog_action(
        sess, now, stale_after=card_stale_after(sess, now))
    assert (action[0] if action else None) == expect
    assert card_stale_after(_watched(now, age=60.0, edited_ago=0), now) == \
        CARD_STALE_SECONDS


def test_t_the_scan_passes_the_tier_to_the_watchdog(
    steady_clock, monkeypatch, run_async,
):
    """Row T, wiring half: the session monitor's own scan leaves a 60 s-tier
    card edited 40 s ago alone. Mutation: drop ``stale_after=`` from the
    scan's call and it forces a refresh."""
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    sess = _watched(steady_clock(), age=2 * HOUR, edited_ago=40.0)
    sess.last_hook_at = steady_clock() - 1
    registry._sessions["claude-jim"] = sess
    calls: list[str] = []

    async def _notify(s, event, ctx):
        calls.append(event)

    async def _sessions():
        return list(registry._sessions)

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _sessions)
    run_async(SessionMonitor(registry, _notify)._scan())
    assert "busy_card_watchdog" not in calls
