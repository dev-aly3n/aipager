"""T3 / rev-iter1-001 — the paused card line is RENDERED, and the card
comes back when minimal mode lifts.

The iteration-1 defect this directory reported (tester-iter1-002): ten
``_animate_tick`` calls at ``FLOOD_MIN_RATE`` put **zero** requests on the
wire with BOTH surfaces metered. The one message that explains why the
card froze was the one message minimal mode swallowed, so the user saw a
dead card and no reason — "indistinguishable from a hung bot, which is
the state the operator paged about".

The coordinator's ruling has two halves and both are metered here the same
way: the real rich ``_post`` behind an ``httpx.MockTransport`` AND a
limiter-routed PTB double, so "on the wire" means on the wire.

* the paused line is ESSENTIAL — it lands *while ornaments are being
  refused*, once per entry, however many ticks run;
* when minimal mode lifts, one non-debounced ESSENTIAL edit brings the
  card back, so the user stops reading "updates paused" in a chat that is
  no longer paused.
"""

from __future__ import annotations

import time

from aipager import config
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)
from aipager.state import Status, TrackedSession

CHAT = 123456
FLOOR = config.FLOOD_MIN_RATE


def _busy_sess():
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic()
    sess.stream_last_rendered = ""
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def _card_bot(mk_bot, gated_bot):
    """Every card surface real and metered — nothing patched over
    ``edit_message_text_rich``, because a patched coroutine records the
    edits the animator ATTEMPTED rather than the ones that reached
    Telegram, and in minimal mode those are exactly the numbers that
    differ."""
    bot = mk_bot()
    bot._app.bot = gated_bot
    return bot


def _set_rate(limiter, clock, rate):
    limiter.restore([{
        "chat_id": CHAT, "rate": rate, "rate_earned_at": clock.wall,
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }])


def _wire(rich_http, gated_bot) -> list[str]:
    """Every request that actually left, from both surfaces."""
    return rich_http.endpoints() + gated_bot.endpoints()


def _bodies_of(requests) -> list[str]:
    out = []
    for _endpoint, _chat, payload in requests:
        rich = payload.get("rich_message") or {}
        out.append(rich.get("markdown") or payload.get("text", ""))
    return out


def _bodies(rich_http, gated_bot) -> list[str]:
    """What the user would actually READ, from whichever surface carried
    it. The card can leave by either path (the rich ``_post`` or PTB's
    ``edit_message_text``) and looking at only one of them returns ``[]``
    for the other — a vacuous pass for every row about copy."""
    return _bodies_of(rich_http.requests) + gated_bot.texts()


def _mark(rich_http, gated_bot) -> tuple[int, int]:
    """How much each surface had said so far.

    The two recorders cannot be concatenated and read chronologically —
    the rich list has no stamps — so a row that wants "what was said
    NEXT" marks both surfaces and diffs them. Reading ``[-1]`` off the
    concatenation instead silently returns the older surface's last line
    (found exactly that way: the resume edit goes out on the rich path
    while the paused line went out on PTB's).
    """
    return len(rich_http.requests), len(gated_bot.texts())


def _bodies_since(rich_http, gated_bot, mark) -> list[str]:
    rich, gated = mark
    out = _bodies_of(rich_http.requests[rich:])
    return out + gated_bot.texts()[gated:]


def _run_ticks(bot, sess, clock, run_async, n=8, step=10.0):
    """*n* ticks *step* seconds apart — short enough that the chat cannot
    climb out of minimal mode half way through (measured: 10 ticks 30 s
    apart recovered by tick 3 and the row became a cadence test)."""
    async def _ticks():
        for _ in range(n):
            await bot._animate_tick(sess, "Working", False)
            clock.advance(step)

    run_async(_ticks())


def _ornament_is_refused(limiter, run_async) -> bool:
    """Is the gate refusing ornaments for this chat *right now*?"""
    async def _call():
        return "sent"

    try:
        run_async(limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": CHAT},
            rate_limit_args=rate_limit_args(
                kind="skip", priority=PRIORITY_ORNAMENT)))
    except FloodSkipped:
        return True
    return False


# ===== the line is BUILT and DELIVERED ==================================

def test_entering_minimal_mode_puts_exactly_one_edit_on_the_wire(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """T3's headline, both surfaces metered: not zero (a frozen card with
    no reason) and not one per tick (the spam this ship deletes)."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, FLOOR)
    _run_ticks(bot, _busy_sess(), anim_clock, run_async)
    assert len(_wire(rich_http, gated_bot)) == 1


def test_that_one_edit_is_an_edit_of_the_card(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """…and it is an ``editMessageText``, not some other call that
    happened to be counted."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, FLOOR)
    _run_ticks(bot, _busy_sess(), anim_clock, run_async)
    assert _wire(rich_http, gated_bot) == ["editMessageText"]


def test_the_paused_line_is_rendered_not_merely_reclassified(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The coordinator's exact note on T3: "it is **not merely
    suppressed, it is never even built. Make sure the fix RENDERS it**".
    The design's line is "⏳ working — updates paused"; what the user has
    to see is the word that explains the freeze."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, FLOOR)
    _run_ticks(bot, _busy_sess(), anim_clock, run_async)
    assert any("paused" in body for body in _bodies(rich_http, gated_bot))


def test_the_paused_line_lands_while_ornaments_are_being_refused(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The whole point of the ruling, in one row: at the very instant the
    line is delivered, an ORNAMENT through the SAME limiter and the SAME
    chat is refused. Without this clause "one edit" could be an ordinary
    animation frame that slipped through."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, FLOOR)
    _run_ticks(bot, _busy_sess(), anim_clock, run_async)
    assert _ornament_is_refused(limiter, run_async)


def test_the_line_still_lands_with_the_chat_budget_empty(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """ESSENTIAL means "never dropped": the promise has to survive an
    empty bucket, which is the state a throttled chat is actually in."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, FLOOR)

    async def _drain():
        for _ in range(int(config.TELEGRAM_CHAT_BURST)):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)

    run_async(_drain())
    before = len(_wire(rich_http, gated_bot))
    _run_ticks(bot, _busy_sess(), anim_clock, run_async, n=3)
    assert len(_wire(rich_http, gated_bot)) == before + 1


async def _ok():
    return "sent"


def test_a_healthy_chat_never_renders_the_paused_line(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The control: the line is a statement about minimal mode, not
    boilerplate every card carries."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, config.TELEGRAM_PRIVATE_MAX_RATE)
    _run_ticks(bot, _busy_sess(), anim_clock, run_async)
    assert not any("paused" in body for body in _bodies(rich_http, gated_bot))


# ===== the LIFT render ==================================================

def _enter_then_leave(bot, sess, limiter, clock, run_async, ticks=4):
    """Minimal mode, then recovery — the shape of a chat seconds after a
    ban lifts."""
    _set_rate(limiter, clock, FLOOR)
    _run_ticks(bot, sess, clock, run_async, n=ticks)
    _set_rate(limiter, clock, config.TELEGRAM_PRIVATE_MAX_RATE)


def test_leaving_minimal_mode_brings_the_card_back(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """rev-iter1-001's second half: "again once when minimal mode lifts if
    the card is still live". Otherwise the user keeps reading "updates
    paused" in a chat that recovered ten minutes ago."""
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _enter_then_leave(bot, sess, limiter, anim_clock, run_async)
    before = len(_wire(rich_http, gated_bot))
    run_async(bot._animate_tick(sess, "Working", False))
    assert len(_wire(rich_http, gated_bot)) == before + 1


def test_the_lift_edit_survives_the_cadence_debounce(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """It must be NON-debounced. The tick that notices the recovery
    arrives seconds after the paused edit, inside the card interval, so a
    debounced resume would be skipped and the stale line would stay up
    until the next tool event — which during a long tool call is minutes.
    """
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _set_rate(limiter, anim_clock, FLOOR)
    _run_ticks(bot, sess, anim_clock, run_async, n=2, step=1.0)
    _set_rate(limiter, anim_clock, config.TELEGRAM_PRIVATE_MAX_RATE)
    before = len(_wire(rich_http, gated_bot))
    run_async(bot._animate_tick(sess, "Working", False))
    assert len(_wire(rich_http, gated_bot)) == before + 1


def test_the_card_that_comes_back_no_longer_says_paused(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The resume edit has to REPLACE the promise, not repeat it."""
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _enter_then_leave(bot, sess, limiter, anim_clock, run_async)
    mark = _mark(rich_http, gated_bot)
    run_async(bot._animate_tick(sess, "Working", False))
    assert not any("paused" in body
                   for body in _bodies_since(rich_http, gated_bot, mark))


def test_the_lift_renders_once_and_not_on_every_tick(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """Once per transition. A resume that ignored the debounce for ever
    would be the 6-per-minute card spam with a new name."""
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _enter_then_leave(bot, sess, limiter, anim_clock, run_async)
    run_async(bot._animate_tick(sess, "Working", False))
    after_resume = len(_wire(rich_http, gated_bot))
    run_async(bot._animate_tick(sess, "Working", False))
    assert len(_wire(rich_http, gated_bot)) == after_resume


def test_a_chat_that_never_paused_gets_no_resume_edit(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The control for the lift: an ordinary debounced tick on a healthy
    chat still makes no edit, so the resume rows above are about the
    transition and not about the debounce being broken."""
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _set_rate(limiter, anim_clock, config.TELEGRAM_PRIVATE_MAX_RATE)
    run_async(bot._animate_tick(sess, "Working", False))
    before = len(_wire(rich_http, gated_bot))
    run_async(bot._animate_tick(sess, "Working", False))
    assert len(_wire(rich_http, gated_bot)) == before


def test_the_task_stays_alive_across_the_whole_transition(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """Criterion 21 across the transition: ``None`` ends the animation
    loop and the watchdog then restarts it every 20 s."""
    bot = _card_bot(mk_bot, gated_bot)
    sess = _busy_sess()
    _enter_then_leave(bot, sess, limiter, anim_clock, run_async)
    assert run_async(bot._animate_tick(sess, "Working", False)) is not None
