"""Row D / O / R / S at the tier BREAKPOINTS (120 / 600 / 3600 s of turn
age) rather than in the middle of a tier: the card's edit gaps and its
elapsed unit switch exactly when the turn crosses a breakpoint; hook-driven
edits obey the tier they are in (Q3) — including tier 0, which stays as
today; and ``card_age_decay`` off gives 0.7.13's cadence at every tier.

The REAL animator and the REAL notify dispatcher on the harness's virtual
loop; card edits are the rich ``editMessageText`` POSTs recorded below the
limiter (``rich_http``).
"""

from __future__ import annotations

import asyncio
import re

from aipager import preferences as prefs
from aipager.state import Status

CHAT = 256113222
MIN = 60.0
HOUR = 3600.0
EPS = 1e-3
QUIET_TODAY = 4.4        # 0.7.13: a quiet card alone in a DM, every 4.4 s


def card(mk_bot, vbot, vloop, rich_http, *, age, override=None):
    rich_http.clock = vloop.time
    bot = mk_bot()
    bot._app.bot = vbot
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = 77
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = 0.0
    if override is not None:
        sess.override_card_age_decay = override
    return bot, sess


def animate(vloop, bot, sess, seconds):
    async def _go():
        task = asyncio.ensure_future(bot._animate_busy(sess))
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    vloop.run_until_complete(_go())


def status_line(markdown):
    return markdown.rsplit("\n", 1)[-1]


def gaps_after_age(edits, started_at, age_at):
    """Gaps ending at an edit made when the turn was at least *age_at*
    old."""
    out = []
    for (a, _), (b, _) in zip(edits, edits[1:]):
        if b - started_at >= age_at:
            out.append(b - a)
    return out


def gaps_before_age(edits, started_at, age_at):
    out = []
    for (a, _), (b, _) in zip(edits, edits[1:]):
        if b - started_at < age_at:
            out.append(b - a)
    return out


# ── the 120 s breakpoint ─────────────────────────────────────────────────────

def test_tier_zero_is_todays_cadence_just_before_two_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=80.0)
    animate(vloop, bot, sess, 60.0)
    early = gaps_before_age(rich_http.edits(), sess.busy_started_at, 120.0)
    assert early and max(early) < 10.0 - EPS, early


def test_no_gap_under_ten_seconds_once_past_two_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=110.0)
    animate(vloop, bot, sess, 90.0)
    late = gaps_after_age(rich_http.edits(), sess.busy_started_at, 120.0 + EPS)
    assert late and min(late) >= 10.0 - EPS, late


def test_exactly_two_minutes_is_the_ten_second_tier(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=120.0)
    animate(vloop, bot, sess, 60.0)
    edits = rich_http.edits()
    gaps = [b - a for (a, _), (b, _) in zip(edits, edits[1:])]
    assert gaps and min(gaps) >= 10.0 - EPS, gaps


def test_the_ten_second_tier_still_counts_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=125.0)
    animate(vloop, bot, sess, 15.0)
    assert re.search(r"· 2m \d+s$", status_line(rich_http.edits()[0][1]))


# ── the 600 s breakpoint ─────────────────────────────────────────────────────

def test_just_under_ten_minutes_the_counter_reads_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=585.0)
    animate(vloop, bot, sess, 8.0)
    assert re.search(r"· 9m \d+s$", status_line(rich_http.edits()[0][1]))


def test_past_ten_minutes_the_counter_reads_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=585.0)
    animate(vloop, bot, sess, 80.0)
    late = [m for t, m in rich_http.edits()
            if t - sess.busy_started_at >= 600.0 + EPS]
    assert late and all(re.search(r"· 1\dm$", status_line(m)) for m in late), \
        [status_line(m) for m in late]


def test_no_gap_under_thirty_seconds_once_past_ten_minutes(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=590.0)
    animate(vloop, bot, sess, 150.0)
    late = gaps_after_age(rich_http.edits(), sess.busy_started_at, 600.0 + EPS)
    assert late and min(late) >= 30.0 - EPS, late


# ── the 3600 s breakpoint ────────────────────────────────────────────────────

def test_just_under_an_hour_the_counter_reads_59m(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=3570.0)
    animate(vloop, bot, sess, 8.0)
    assert re.search(r"· 59m$", status_line(rich_http.edits()[0][1]))


def test_past_an_hour_the_counter_reads_hours(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=3570.0)
    animate(vloop, bot, sess, 150.0)
    late = [m for t, m in rich_http.edits()
            if t - sess.busy_started_at >= 3600.0 + EPS]
    assert late and all(re.search(r"· 1h \d+m$", status_line(m)) for m in late), \
        [status_line(m) for m in late]


def test_no_gap_under_sixty_seconds_once_past_an_hour(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=3590.0)
    animate(vloop, bot, sess, 300.0)
    late = gaps_after_age(rich_http.edits(), sess.busy_started_at, 3600.0 + EPS)
    assert late and min(late) >= 60.0 - EPS, late


def test_a_slow_card_still_moves_at_least_once_a_tier(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """"Never looks frozen": at the 30 s tier a quiet card is still edited
    well inside a minute."""
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=20 * MIN)
    animate(vloop, bot, sess, 5 * MIN)
    edits = rich_http.edits()
    gaps = [b - a for (a, _), (b, _) in zip(edits, edits[1:])]
    assert gaps and max(gaps) <= 30.0 + 2.2 + EPS, gaps


# ── preference off: 0.7.13 exactly, at every tier ────────────────────────────

def test_off_at_thirty_minutes_the_cadence_is_todays(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    prefs.set_preference(CHAT, "card_age_decay", False)
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=30 * MIN)
    animate(vloop, bot, sess, 60.0)
    edits = rich_http.edits()
    gaps = [b - a for (a, _), (b, _) in zip(edits, edits[1:])]
    assert gaps and all(abs(g - QUIET_TODAY) < EPS for g in gaps), gaps


def test_off_at_thirty_minutes_the_counter_is_todays(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    prefs.set_preference(CHAT, "card_age_decay", False)
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=30 * MIN)
    animate(vloop, bot, sess, 10.0)
    assert re.search(r"· 30m \d+s$", status_line(rich_http.edits()[0][1]))


def test_off_at_five_hours_the_counter_is_todays(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=5 * HOUR,
                     override=False)
    animate(vloop, bot, sess, 10.0)
    assert re.search(r"· 300m \d+s$", status_line(rich_http.edits()[0][1]))


def test_a_session_override_off_at_five_minutes_is_todays_cadence(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=5 * MIN,
                     override=False)
    animate(vloop, bot, sess, 60.0)
    edits = rich_http.edits()
    gaps = [b - a for (a, _), (b, _) in zip(edits, edits[1:])]
    assert gaps and all(abs(g - QUIET_TODAY) < EPS for g in gaps), gaps


def test_preferences_default_to_on():
    assert prefs.get_preferences(CHAT).card_age_decay is True


def test_set_preference_rejects_a_non_bool():
    import pytest
    with pytest.raises(ValueError):
        prefs.set_preference(CHAT, "card_age_decay", "off")


def test_resolve_preferences_session_override_wins():
    prefs.set_preference(CHAT, "card_age_decay", True)
    resolved = prefs.resolve_preferences(CHAT, {"card_age_decay": False})
    assert resolved.card_age_decay is False


def test_resolve_preferences_null_override_falls_back_to_scope():
    prefs.set_preference(CHAT, "card_age_decay", False)
    resolved = prefs.resolve_preferences(CHAT, {"card_age_decay": None})
    assert resolved.card_age_decay is False


# ── hook-driven edits obey the tier they are in (Q3) ─────────────────────────

def hook_after_edit(mk_bot, vbot, vloop, rich_http, *, age, wait,
                    override=None):
    """Land one card edit (the animator's first), stop the animator, wait
    *wait* seconds, send ONE ``tool_use`` (which adds a row, so a gated
    edit would really POST), and return the number of card POSTs in the
    following second."""
    bot, sess = card(mk_bot, vbot, vloop, rich_http, age=age,
                     override=override)

    async def _go():
        task = asyncio.ensure_future(bot._animate_busy(sess))
        for _ in range(200):
            if rich_http.edits():
                break
            await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        sess.animate_task = vloop.create_future()     # "running": no resume
        await asyncio.sleep(wait)
        before = len(rich_http.edits())
        await bot.notify(sess, "tool_use", {"tool_summary": "Bash: make test",
                                            "tool_name": "Bash"})
        await asyncio.sleep(1.0)
        return len(rich_http.edits()) - before

    return vloop.run_until_complete(_go())


def test_tier_zero_hook_edit_is_immediate_as_today(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=30.0, wait=3.0) == 1


def test_ten_second_tier_hook_edit_waits_inside_the_tier(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=5 * MIN, wait=5.0) == 0


def test_ten_second_tier_hook_edit_goes_once_the_tier_has_passed(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=5 * MIN, wait=11.0) == 1


def test_thirty_second_tier_hook_edit_waits_at_twelve_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=30 * MIN, wait=12.0) == 0


def test_thirty_second_tier_hook_edit_goes_at_thirty_one_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=30 * MIN, wait=31.0) == 1


def test_sixty_second_tier_hook_edit_waits_at_forty_seconds(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=2 * HOUR, wait=40.0) == 0


def test_decay_off_hook_edit_at_two_hours_is_immediate_as_today(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    assert hook_after_edit(mk_bot, vbot, vloop, rich_http,
                           age=2 * HOUR, wait=3.0, override=False) == 1
