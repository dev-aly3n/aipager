"""R7 — silence while suppressed: row M, criterion 21 (and criterion 8's
'exactly one static card line').

The busy-card watchdog logged 348 restarts in 54 minutes on 2026-09-15 —
verified NOT to be traffic (348 restarts against 6 HTTP refusals all day),
so it is log spam that buries the real lines. It is silenced while the
chat is muted or in minimal mode, and the animate task is kept ALIVE
(``False``, never ``None``) so there is nothing to restart.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager import config
from aipager.bot import animation
from aipager.session_monitor import (
    CARD_STALE_SECONDS,
    busy_card_watchdog_action,
    card_suppression_transition,
    cards_suppressed,
)
from aipager.bot.flood import MUTE
from aipager.state import Status, TrackedSession

CHAT = 123456
BAN = 34212.0


def _live_task():
    class _T:
        def done(self):
            return False
    return _T()


def _session(now: float, *, task=None, edited_ago: float = 1.0):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.busy_started_at = now - 120
    sess.last_hook_at = now - 1
    sess.last_tool_edit_at = now - edited_ago
    sess.animate_task = task
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


# ===== row M — the watchdog is silent while suppressed ==================

def test_the_watchdog_wants_a_restart_when_nothing_is_suppressed(
):
    """The control: a BUSY session with a dead animate task IS a restart
    the watchdog would order. Without this the next row proves nothing."""
    now = 1000.0
    assert busy_card_watchdog_action(_session(now), now) == ("restart", 0.0)


def test_a_suppressed_chat_gets_no_watchdog_action_at_all(
):
    """Row M / criterion 21: the same session, suppressed, orders
    nothing."""
    now = 1000.0
    assert busy_card_watchdog_action(
        _session(now), now, suppressed=True) is None


def test_a_suppressed_refresh_is_also_silenced():
    """The other action the watchdog can order — a stale card refresh —
    must be silenced too, or the noise simply changes shape."""
    now = 1000.0
    sess = _session(now, task=_live_task(), edited_ago=CARD_STALE_SECONDS + 5)
    assert busy_card_watchdog_action(sess, now, suppressed=True) is None


def test_the_watchdog_action_stays_pure():
    """``suppressed`` is passed IN precisely so the function never looks a
    mute up itself: a muted chat with ``suppressed=False`` must still
    produce its action. Purity is what keeps every existing row green."""
    now = 1000.0
    MUTE.mute(CHAT, BAN)
    assert busy_card_watchdog_action(_session(now), now) == ("restart", 0.0)


# ===== the transition, as a pure function ===============================

def test_the_first_suppressed_tick_is_a_start():
    assert card_suppression_transition(set(), CHAT, True) == "start"


def test_a_second_suppressed_tick_says_nothing():
    """One INFO per transition, never one per tick — the 348-restart
    lesson applied to the new lines themselves."""
    seen: set = set()
    card_suppression_transition(seen, CHAT, True)
    assert card_suppression_transition(seen, CHAT, True) is None


def test_the_tick_after_the_mute_lifts_is_a_lift():
    seen: set = set()
    card_suppression_transition(seen, CHAT, True)
    assert card_suppression_transition(seen, CHAT, False) == "lift"


def test_a_healthy_chat_never_transitions():
    assert card_suppression_transition(set(), CHAT, False) is None


def test_a_second_healthy_tick_still_says_nothing():
    seen: set = set()
    card_suppression_transition(seen, CHAT, False)
    assert card_suppression_transition(seen, CHAT, False) is None


def test_two_chats_transition_independently():
    """Error guessing: a shared ``seen`` set keyed wrongly would report
    one chat's mute as another's."""
    seen: set = set()
    card_suppression_transition(seen, CHAT, True)
    assert card_suppression_transition(seen, -100999, True) == "start"


def test_the_cycle_can_repeat():
    """A chat that is banned twice in a day must announce both."""
    seen: set = set()
    card_suppression_transition(seen, CHAT, True)
    card_suppression_transition(seen, CHAT, False)
    assert card_suppression_transition(seen, CHAT, True) == "start"


# ===== cards_suppressed — the one impure helper =========================

def test_a_muted_chat_is_suppressed():
    MUTE.mute(CHAT, BAN)
    assert cards_suppressed(CHAT) is True


def test_a_healthy_chat_is_not_suppressed():
    assert cards_suppressed(CHAT) is False


def test_a_chat_in_minimal_mode_is_suppressed(limiter, qa_clock):
    """The second half of R7's condition: minimal mode suspends ornaments,
    and the watchdog exists only to restart an ornament."""
    limiter.restore([{
        "chat_id": CHAT,
        "rate": config.FLOOD_MINIMAL_MODE_RATE_FLOOR - 0.001,
        "rate_earned_at": qa_clock.wall, "backoff": 1.0,
        "last_429_at": None, "ban_stamps": [],
    }])
    assert cards_suppressed(CHAT) is True


def test_cards_suppressed_never_raises_on_a_missing_chat():
    """It runs inside the 2 s scan: an exception here would take the whole
    monitor tick down."""
    assert cards_suppressed(None) in (True, False)


# ===== the animate task stays alive =====================================

def _busy_sess():
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 10
    # On the ANIMATOR's clock, which `anim_clock` rebinds: stamped from
    # the real `time.monotonic()` instead, a host up for more than the
    # fake clock's 500,000 s puts the start in the card's future, the
    # counter reads "0s" for ever, every re-render dedupes, and the
    # "still animates" control sees one edit.
    sess.busy_started_at = animation.time.monotonic()
    sess.stream_last_rendered = ""
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def test_a_tick_into_a_muted_chat_returns_false_not_none(
    mk_bot, run_async, monkeypatch,
):
    """Criterion 21. ``None`` ends ``_animate_busy``'s loop and the
    watchdog then restarts it every 20 s for the length of the ban —
    that IS the 348-restart storm. ``False`` keeps the task alive."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(return_value={"message_id": 10}))
    bot._app.bot.send_chat_action = AsyncMock()
    MUTE.mute(CHAT, BAN)
    assert run_async(bot._animate_tick(_busy_sess(), "Working", False)) is False


def test_a_tick_into_a_muted_chat_sends_no_typing_bubble(
    mk_bot, run_async, monkeypatch,
):
    """The typing indicator is a Bot API call like any other — R1 has no
    exceptions, and this one fires every 4.5 s per session."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(return_value={"message_id": 10}))
    bot._app.bot.send_chat_action = AsyncMock()
    MUTE.mute(CHAT, BAN)
    run_async(bot._animate_tick(_busy_sess(), "Working", False))
    bot._app.bot.send_chat_action.assert_not_awaited()


def test_a_tick_into_a_muted_chat_makes_no_card_edit(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    edit = AsyncMock(return_value={"message_id": 10})
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich", edit)
    bot._app.bot.send_chat_action = AsyncMock()
    MUTE.mute(CHAT, BAN)
    run_async(bot._animate_tick(_busy_sess(), "Working", False))
    edit.assert_not_awaited()


def _set_rate(limiter, clock, rate):
    limiter.restore([{
        "chat_id": CHAT, "rate": rate, "rate_earned_at": clock.wall,
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }])


def _card_bot(mk_bot, gated_bot):
    """A bot whose EVERY card surface is real and metered.

    Nothing is patched over ``edit_message_text_rich`` on purpose: a
    patched coroutine bypasses the limiter, so it would record the edits
    the animator ATTEMPTED rather than the ones that reached Telegram —
    and in minimal mode those are exactly the numbers that differ. The
    rich side runs the real ``_post`` onto an ``httpx.MockTransport``,
    the PTB side the real ``process_request``.
    """
    bot = mk_bot()
    bot._app.bot = gated_bot
    return bot


def _run_ticks(bot, sess, clock, run_async, n=10, step=10.0):
    """*n* animation ticks *step* seconds apart.

    ``step`` is kept short enough that fewer than
    ``FLOOD_SUCCESS_WINDOW_SECONDS`` × 2 elapses: the earned rate CLIMBS
    during quiet time, so a long loop leaves minimal mode half way
    through and the row silently becomes a cadence test. (Measured
    exactly that way: 10 ticks 30 s apart produced 8 ordinary card edits
    because the chat had recovered by tick 3.)
    """
    async def _ticks():
        for _ in range(n):
            await bot._animate_tick(sess, "Working", False)
            clock.advance(step)

    run_async(_ticks())


def test_minimal_mode_puts_at_most_one_card_edit_on_the_wire(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """Criterion 8's last clause: 'exactly ONE static "updates paused"
    card edit however many ticks run'. Pixels are what pressure sheds."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, config.FLOOD_MIN_RATE)
    sess = _busy_sess()
    _run_ticks(bot, sess, anim_clock, run_async)
    assert limiter.minimal_mode(CHAT), "the row left minimal mode half way"
    assert len(rich_http.requests) + len(gated_bot.calls) <= 1


def test_minimal_mode_still_renders_the_promise_once(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The other half of criterion 8: 'ornaments suspended, answers still
    sent, ONE static card line'. A card that simply freezes with no
    explanation reads as a hung bot — the line IS the promise that work
    is still happening."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, config.FLOOD_MIN_RATE)
    sess = _busy_sess()
    _run_ticks(bot, sess, anim_clock, run_async)
    assert len(rich_http.requests) + len(gated_bot.calls) == 1


def test_a_minimal_mode_tick_keeps_the_task_alive(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """Same reason as the mute: a dead task is a restart storm, and
    minimal mode can last hours."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, config.FLOOD_MIN_RATE)
    sess = _busy_sess()

    async def _ticks():
        first = await bot._animate_tick(sess, "Working", False)
        anim_clock.advance(10)
        second = await bot._animate_tick(sess, "Working", False)
        return first, second

    assert run_async(_ticks())[1] is not None


def test_a_chat_above_the_floor_still_animates(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    """The control: at a rate just ABOVE
    ``FLOOD_MINIMAL_MODE_RATE_FLOOR`` the very same loop edits the card
    repeatedly, so "at most one" above is a statement about suppression
    and not about a frozen clock or a dead animator."""
    bot = _card_bot(mk_bot, gated_bot)
    _set_rate(limiter, anim_clock, config.FLOOD_MINIMAL_MODE_RATE_FLOOR + 0.05)
    sess = _busy_sess()
    _run_ticks(bot, sess, anim_clock, run_async)
    assert len(rich_http.requests) + len(gated_bot.calls) > 1
