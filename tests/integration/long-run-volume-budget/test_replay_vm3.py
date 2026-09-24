"""The vm3 timeline, replayed (rows C and K; the 8.30 acceptance).

What happened, 2026-09-23, owner DM on vm3, aipager 0.7.13: two sessions
streaming into one chat for 3 h 23 min — bigdog ONE continuous turn the
whole time, catfish ten turns, the last of them long — under every
per-second and per-minute window the daemon had; a 429 on the typing
bubble, forgiven in five minutes; then, with no further warning, a
straight 7-hour ban. Modelled, not measured (edits were not logged): about
11,500 calls into the chat in that window, ~57 a minute.

The replay runs the REAL daemon code — the card animator, notify's hook
dispatcher, the per-chat typing loop and the limiter — for 12,240 virtual
seconds on a virtual event loop, against :class:`_VolumeTelegram`, a fake
that meters every endpoint (typing included) and bans on long-run volume.
The stimulus is the realistic one the design asks for (§8 Harness): a tool
call every 2–8 s, prose every 20–60 s, a phantom SubagentStop about every
36 s per session — not 0.7.13's tool row every 0.1 s, which would keep
every card permanently "busy".
"""

from __future__ import annotations

import asyncio
import logging
import random

import pytest

from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    rate_limit_args,
)
from aipager.state import Status

CHAT = 256113222
T = 12_240.0                       # 3 h 24 min of virtual time
HOUR = 3600.0
BIGDOG_MSG = 1000
PINNED = 9999
ANSWER = "the answer"
#: Minutes of the replay in which BOTH sessions send a hook. The replay
#: has 171; two hours leaves room for a reseeded stimulus.
BOTH_STREAMING_MINUTES = 120


def _most_in_window(stamps: list[float], window: float = HOUR) -> int:
    """The largest number of stamps in any half-open ``[t, t + window)``."""
    stamps = sorted(stamps)
    best, lo = 0, 0
    for hi, t in enumerate(stamps):
        while t - stamps[lo] >= window:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def _session(bot, label: str):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.IDLE
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.trigger_msg_id = 3
    return sess


async def _begin(bot, sess, msg_id: int, now: float) -> None:
    """A turn starts: the card is sent (an ornament, like ``send_busy``),
    the per-turn card state is reset the way ``_send_busy_and_animate``
    resets it, and the animation — card and typing — starts."""
    sess.status = Status.BUSY
    await bot.send_busy(sess)
    sess.busy_msg_id = msg_id
    sess.busy_started_at = now
    sess.last_tool_edit_at = 0.0
    sess.stream_last_rendered = ""
    sess.tool_history = []
    sess.stream_commentary = []
    sess.active_subagents = {}
    sess.card_frame_state = None
    sess.card_bypass_at = 0.0
    sess.card_elapsed_unit = "s"
    bot._start_animation(sess)


async def _end(bot, sess) -> None:
    """A turn ends: the card settles and the answer goes out (ESSENTIAL)."""
    sess.status = Status.IDLE
    bot._stop_animation(sess)
    await bot._edit_busy_rich(sess, "Done", final=True)
    await bot._app.bot.send_message(CHAT, ANSWER)
    sess.busy_msg_id = None


async def _work(bot, sess, rng: random.Random, until: float, loop,
                hooks: list | None = None) -> None:
    """A session actually working, until *until*: a tool call every 2–8 s
    (PreToolUse, then PostToolUse a moment later), prose every 20–60 s,
    a phantom SubagentStop (empty type, unknown id, 0.0 s) about every
    36 s. Each tool call is recorded in *hooks* as ``(label, time)``."""
    n = 0
    next_prose = loop.time() + rng.uniform(20, 60)
    next_phantom = loop.time() + rng.expovariate(1 / 36)
    while loop.time() < until:
        await asyncio.sleep(rng.uniform(2, 8))
        n += 1
        summary = f"Bash: step {n}"
        if hooks is not None:
            hooks.append((sess.label, loop.time()))
        await bot.notify(sess, "tool_use", {"tool_summary": summary,
                                            "tool_name": "Bash"})
        await asyncio.sleep(rng.uniform(0.3, 1.5))
        await bot.notify(sess, "tool_done", {"tool_summary": summary})
        if loop.time() >= next_prose:
            await bot.notify(sess, "assistant_text", {
                "delta": f"Moving on to part {n}.", "message_id": f"m{n}"})
            next_prose = loop.time() + rng.uniform(20, 60)
        while loop.time() >= next_phantom:
            await bot.notify(sess, "subagent_stop", {
                "agent_type": "", "elapsed": 0.0, "history_idx": None,
                "tool_count": 0})
            next_phantom += rng.expovariate(1 / 36)


def _replay(bot, vloop, *, seed: int = 20260923) -> dict:
    """Run the vm3 timeline and return the catfish schedule, the run's
    start and end, and every tool hook as ``(label, time)``."""
    rng = random.Random(seed)
    bigdog = _session(bot, "bigdog")
    catfish = _session(bot, "catfish")
    hooks: list[tuple[str, float]] = []
    schedule: dict = {"catfish": [], "hooks": hooks,
                      "start": vloop.time(), "end": vloop.time() + T}
    # The run's END on the loop's clock. `T` is a DURATION: the virtual
    # loop starts at 1,000,000, so passing `T` itself as `_work`'s
    # deadline (as this did until 8.30's iteration 3) ended bigdog's work
    # before its first hook, and catfish's last turn too. The replay then
    # never had two sessions streaming at once, only bigdog's animator.
    end = vloop.time() + T

    async def _bigdog():
        await _begin(bot, bigdog, BIGDOG_MSG, vloop.time())
        await _work(bot, bigdog, random.Random(seed + 1), end, vloop, hooks)

    async def _catfish():
        for turn in range(9):
            await asyncio.sleep(rng.uniform(120, 360))          # idle gap
            start = vloop.time()
            await _begin(bot, catfish, 2000 + turn, start)
            await _work(bot, catfish, rng, start + rng.uniform(180, 480), vloop,
                        hooks)
            await _end(bot, catfish)
            schedule["catfish"].append((start, vloop.time()))
        await asyncio.sleep(rng.uniform(120, 360))
        start = vloop.time()
        await _begin(bot, catfish, 2100, start)                # the long one
        schedule["catfish"].append((start, end))
        await _work(bot, catfish, rng, end, vloop, hooks)

    async def main():
        tasks = [asyncio.ensure_future(_bigdog()),
                 asyncio.ensure_future(_catfish())]
        await asyncio.sleep(T)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sess in (bigdog, catfish):
            bot._stop_animation(sess)
        await asyncio.sleep(0)

    vloop.run_until_complete(main())
    return schedule


# ── C ───────────────────────────────────────────────────────────────────────

def test_c_the_vm3_timeline_stays_inside_the_long_run_budget(
    mk_bot, vloop, vlimiter, volume_telegram, volume_bot, caplog, monkeypatch,
):
    """Row C, the acceptance (Q1/Q7). Over the whole 3.4 h:

    * ornament calls ≤ 1080 in every rolling hour, and all calls ≤ 1200
      with zero essential overflow;
    * consecutive typing calls ≥ 4.5 s apart, the first in the run's first
      second, and — by operator ruling 2026-09-23, "typing always shows,
      the young card yields" — never late by more than one retry past a
      token's refill beyond the bubble's own period, which decays with
      the oldest turn's age (ruling #2: 4.5 s, then 15 s from 10 min);
    * "flicker, never dark": the rolling hour never SHEDS the bubble
      (75 % of the ornament share) — no gap of ten minutes or more. It was
      dark for 71 minutes before ruling #2, and for 22.6 with a 9 s tier;
    * zero 429s and zero bans from a Telegram that bans on volume;
    * bigdog's four-hour card: at most 60 edits in any hour after its
      first (it has no state change to bypass on);
    * total calls ≤ 4,080 — the modelled 0.7.13 figure is ~11,500;
    * with the pinned "needs you" bar LIVE (8.31, refreshed by the
      session monitor's 2 s tick exactly as in the daemon), both sessions
      streaming at once: zero dark minutes, the bubble never shed, and
      minimal mode never entered, sampled every minute;
    * the bar is edited no more often than what it shows changes (the
      state transitions, sampled on every tick) and never more than 120
      times in any rolling hour.

    History: 8.30's pinned dashboard was refreshed by ``notify`` on every
    hook, headed by the session that sent it. Before its debounce this
    replay made 2,203 dashboard edits, the bubble was dark for 164
    minutes, and minimal mode ran for 48. 8.31 replaced it with the bar,
    which no hook refreshes and which is live on every install.

    On 0.7.13 this is two typing loops, a card on a 2–4 s cadence for the
    whole run and every hook racing a 1.2 s debounce.
    """
    caplog.set_level(logging.WARNING, logger="aipager.bot.flood_budget")
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    bot = mk_bot()
    bot._app.bot = volume_bot
    bot.registry.pinned_msg_ids[CHAT] = PINNED
    samples: list[tuple[bool, bool]] = []
    #: What the bar would show, on every tick: its changes are the state
    #: transitions an edit may follow.
    renders: list[str] = []

    async def _sample():
        while True:
            await asyncio.sleep(60.0)
            hour = vlimiter.hourly_usage(CHAT)
            samples.append((vlimiter.minimal_mode(CHAT), hour["typing_shed"]))

    async def _tick():
        # The session monitor's scan: SessionMonitor.on_tick = pinned_tick.
        # The monitor starts with the daemon, long before a turn; here its
        # first scan lands just after the replay's first turn began.
        await asyncio.sleep(2.0)
        while True:
            text, keyboard = bot._render_pinned(CHAT)
            renders.append(text + repr(keyboard and keyboard.to_dict()))
            await bot.pinned_tick()
            await asyncio.sleep(2.0)

    sampler = vloop.create_task(_sample())
    ticker = vloop.create_task(_tick())
    run = _replay(bot, vloop)
    sampler.cancel()
    ticker.cancel()

    calls = volume_telegram.calls
    everything = [t for _e, c, _m, t, _x in calls if c == CHAT]
    answers = [t for e, c, _m, t, x in calls
               if c == CHAT and e == "sendMessage" and x == ANSWER]
    ornaments = [t for e, c, _m, t, x in calls
                 if c == CHAT and not (e == "sendMessage" and x == ANSWER)]
    typing = volume_telegram.stamps(endpoint="sendChatAction")
    bigdog = volume_telegram.stamps(endpoint="editMessageText",
                                    message_id=BIGDOG_MSG)
    # The dashboard edit goes through PTB with its message id positional,
    # so the fake records no id for it; every card edit carries one.
    dashboard = [t for e, c, m, t, _x in calls
                 if c == CHAT and e == "editMessageText" and m is None]
    start = everything[0]
    dark = [m for m in range(int(T // 60.0))
            if not any(start + 60.0 * m <= t < start + 60.0 * (m + 1)
                       for t in typing)]
    report = {"total": len(everything), "answers": len(answers),
              "typing": len(typing), "bigdog_edits": len(bigdog),
              "dashboard": len(dashboard),
              "max_hour": _most_in_window(everything),
              "max_hour_ornament": _most_in_window(ornaments),
              "dark_minutes": len(dark),
              "minimal_minutes": sum(m for m, _s in samples),
              "shed_minutes": sum(s for _m, s in samples)}

    assert volume_telegram.small_429s == [], report
    assert volume_telegram.bans == [], report
    assert report["max_hour_ornament"] <= 1080, report
    assert report["max_hour"] <= 1200, report
    assert vlimiter.hourly_usage(CHAT)["essential_overflow"] == 0
    assert not [r for r in caplog.records
                if "essential calls over the hourly budget" in r.getMessage()]
    gaps = [b - a for a, b in zip(typing, typing[1:])]
    assert typing and min(gaps) >= 4.5 - 1e-6, report
    assert typing[0] - everything[0] < 1.0, "no bubble in the first second"
    late = config.TYPING_AGE_TIER3_INTERVAL + 1.0 / config.FLOOD_START_RATE
    assert not [g for g in gaps if late + 1e-3 < g < 10 * 60.0], report
    late_gaps = [g for g in gaps if g > late + 1e-3]
    assert late_gaps == [], (late_gaps, report)
    after_first_hour = [t for t in bigdog if t >= bigdog[0] + HOUR]
    assert _most_in_window(after_first_hour) <= 60, report
    assert report["total"] <= 4080, report
    assert len(answers) == 9, report          # every short turn answered
    assert len(samples) >= int(T // 60.0) - 1, report
    assert report["dark_minutes"] == 0, report
    assert report["shed_minutes"] == 0, report
    assert report["minimal_minutes"] == 0, report
    transitions = 1 + sum(1 for a, b in zip(renders, renders[1:]) if a != b)
    report["transitions"] = transitions
    assert 0 < report["dashboard"] <= transitions, report
    assert _most_in_window(dashboard) <= 120, report
    # Both sessions really stream (rev-iter3-001). Until 8.30's iteration
    # 3 the replay passed `T` as `_work`'s absolute deadline on a loop
    # that starts at 1,000,000, so bigdog made no hook at all and catfish's
    # long turn none either: every row above passed on one session's
    # animator. Both must still be sending hooks in the run's last ten
    # minutes, and for at least two hours of minutes both must have sent.
    last = run["end"] - 600.0
    for label in ("bigdog", "catfish"):
        assert [t for lab, t in run["hooks"] if lab == label and t >= last], (
            f"{label} sent no hook in the run's last ten minutes")
    by_minute: dict[int, set[str]] = {}
    for lab, t in run["hooks"]:
        by_minute.setdefault(int((t - run["start"]) // 60.0), set()).add(lab)
    both = sum(1 for labs in by_minute.values() if len(labs) == 2)
    assert both >= BOTH_STREAMING_MINUTES, (both, report)


# ── K ───────────────────────────────────────────────────────────────────────

def test_k_a_ban_mid_replay_still_gets_zero_requests_bubble_included(
    mk_bot, vloop, vlimiter, volume_telegram, volume_bot,
):
    """Row K (R7), the 0.7.13 guarantee through the new traffic: a ban
    lands mid-run and NOTHING reaches Telegram until it lifts — not a card
    edit, not an answer, and not the typing bubble, which is budgeted now
    but still exempt from nothing that matters about a mute. The gate
    refuses the bubble with ``FloodMuted`` (a ban), never ``FloodSkipped``.

    Mutation: move the typing branch above the mute gate in
    ``process_request`` and the bubble goes into the ban.
    """
    bot = mk_bot()
    bot._app.bot = volume_bot
    bigdog = _session(bot, "bigdog")
    catfish = _session(bot, "catfish")
    window: dict = {}

    async def main():
        await _begin(bot, bigdog, BIGDOG_MSG, vloop.time() - 20 * 60.0)
        await _begin(bot, catfish, 2000, vloop.time() - 20 * 60.0)
        workers = [asyncio.ensure_future(_work(bot, s, random.Random(i), 1e12, vloop))
                   for i, s in enumerate((bigdog, catfish))]
        await asyncio.sleep(600.0)
        MUTE.mute(CHAT, 1800.0)                  # Telegram: banned for 30 min
        window["from"] = vloop.time()
        with pytest.raises(FloodMuted):
            await volume_bot.send_chat_action(
                chat_id=CHAT, action="typing",
                rate_limit_args=rate_limit_args(kind="skip",
                                                priority=PRIORITY_ORNAMENT))
        await asyncio.sleep(1799.0)
        window["to"] = vloop.time()
        await asyncio.sleep(600.0)
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for sess in (bigdog, catfish):
            bot._stop_animation(sess)

    vloop.run_until_complete(main())
    into_ban = [t for t in volume_telegram.stamps()
                if window["from"] <= t <= window["to"]]
    assert into_ban == []
    assert volume_telegram.stamps(endpoint="sendChatAction"), "no bubble at all"
    after = [t for t in volume_telegram.stamps() if t > window["to"]]
    assert after, "nothing came back after the ban lifted"
