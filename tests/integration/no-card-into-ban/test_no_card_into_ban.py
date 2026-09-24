"""Roadmap 8.17c: while a chat is flood-muted nothing aipager sends on the
user's behalf reaches Telegram, and nothing the mute suppresses is lost.

0.7.13's gate (``BudgetRateLimiter.process_request``) already refuses every
call to a muted chat, so the WIRE half of 8.17c was closed before this
ship. What was still open is what each caller does with the refusal:

* a prompt's busy card was refused and then simply never existed — the
  turn ran to the end with no card, even when the ban lifted mid-turn
  (the 8.26 rows said "the next tick creates the card normally"; no tick
  ever did);
* a self-woken turn's deferred card (8.32) was refused the same way and
  its mark cleared, so it was lost too;
* Retry injected the prompt into Claude and left the error card, Retry
  button and all, behind a refused delete — half applied, and a second
  tap after the lift injected it again;
* a main keyboard refused by the mute was forgotten: the startup release
  cleared the hold BEFORE the send, so the send's "restore the hold on a
  mute" restored ``False``.

Every "no call" claim runs on :class:`GatedBot` (the real limiter); every
row names the mutation that fails it.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.bot.rich_message import RichMessageFloodBanned
from aipager.session_monitor import SessionMonitor
from aipager.state import Status

CHAT = 256113222          # the chat `_pin_single_chat_config` pins
BAN = 600.0


# ── helpers ──────────────────────────────────────────────────────────────────

def _bot(mk_bot, gated_bot):
    bot = mk_bot()
    bot._app.bot = gated_bot
    # The pinned status bar (8.31) re-renders from state on transitions and
    # is re-sent by its own tick after a ban; it is not one of 8.17c's
    # debts and would only interleave with the order these rows assert.
    bot.refresh_pinned = AsyncMock()
    bot.pinned_tick = AsyncMock()
    return bot


def _sess(bot, name="dev", trigger=11):
    sess = bot.registry.get_or_create(f"claude-{name}")
    sess.label = name
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    sess.trigger_msg_id = trigger
    return sess


async def _prompt(bot, sess, *, lazy=False):
    """What every Telegram prompt path does once the injection succeeded:
    ``transition(BUSY)`` then ``_send_busy_and_animate``."""
    bot.registry.transition(sess.name, Status.BUSY)
    await bot._send_busy_and_animate(sess, lazy=lazy)


def _lift(flood_clock):
    flood_clock.advance(BAN + 1)
    assert not MUTE.is_muted(CHAT)


def _affordable(limiter, flood_clock):
    """Take the chat out of minimal mode. A ban drops the earned rate to
    the floor, so right after a lift the chat CANNOT afford a card; a row
    about "the card once the chat can afford one" says so explicitly."""
    budget = limiter._budget_for(limiter._key(CHAT))
    budget.set_rate(1.0, flood_clock())
    budget.hourly_minimal = False
    assert not limiter.minimal_mode(CHAT)


async def _tick(bot, monkeypatch):
    """One session-monitor tick, exactly as the daemon's loop runs it."""
    async def _alive():
        return list(bot.registry._sessions)

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monitor = SessionMonitor(bot.registry, bot.notify)
    monitor.on_mute_catchup = getattr(bot, "flush_owed_keyboards", None)
    tick = getattr(monitor, "tick", None)
    if tick is not None:
        await tick()
    else:                                   # 0.7.14: there is only _scan
        await monitor._scan()


def _rich_recorder(monkeypatch, gated_bot):
    """``send_rich_message`` as the rich path behaves: refused with
    ``RichMessageFloodBanned`` while muted, else recorded on the SAME
    timeline as the gated bot's calls so order can be asserted."""
    async def _send(chat_id, markdown, **kw):
        if MUTE.is_muted(chat_id):
            raise RichMessageFloodBanned(MUTE.remaining(chat_id), chat_id)
        gated_bot.calls.append(("sendRichMessage", chat_id, {"text": markdown}))
        return {"message_id": 9000 + len(gated_bot.calls)}

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)


async def _finish(bot, sess, answer):
    """The turn ends: Stop -> IDLE -> the answer (held while muted)."""
    bot.registry.transition(sess.name, Status.IDLE)
    await bot.notify(sess, "idle_prompt", {"summary": answer})


def _owed(sess) -> bool:
    return bool(getattr(sess, "busy_card_owed", False))


# ── R1 + R2: a prompt during a mute ─────────────────────────────────────────

def test_a_prompt_during_a_mute_makes_no_call_and_its_card_goes_out_after_the_lift(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The headline row. During the mute: zero calls, no traceback, and the
    session remembers it owes a card. After the lift, with the turn still
    running: exactly one ``sendMessage`` — the card — replying to the
    prompt, tracked for reply routing and animated.

    Fails on 0.7.14: the card is refused and forgotten, and no tick ever
    sends it (``calls`` stays empty after the lift).

    Mutation: drop the owed-card dispatch from ``SessionMonitor._scan``
    (or the ``busy_card_owed = True`` in ``_open_busy_card``) and the
    card never appears.
    """
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        assert gated_bot.calls == [], "a busy card went into an active ban"
        assert sess.busy_msg_id is None
        assert _owed(sess), "the refused card was forgotten, not owed"

        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"], gated_bot.calls
    _endpoint, chat, kwargs = gated_bot.calls[0]
    assert chat == CHAT
    assert kwargs["reply_to_message_id"] == 11
    assert sess.busy_msg_id == 5000
    assert sess.busy_card_trigger == 11
    assert bot.registry.get_session_by_msg(5000, CHAT) is sess
    assert not _owed(sess)


def test_a_refused_card_logs_no_traceback(
    mk_bot, gated_bot, limiter, flood_clock, run_async, caplog,
):
    """R1: "no traceback spam". A prompt during a ban is the ordinary case,
    not an error.

    Mutation: remove ``except FloodMuted`` from ``send_busy`` and the
    generic arm logs ``Failed to send busy message`` with a stack."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)
    with caplog.at_level(logging.DEBUG):
        run_async(_prompt(bot, sess))
    assert not [r for r in caplog.records
                if r.exc_info and r.levelno >= logging.WARNING]


def test_right_after_the_lift_the_owed_card_waits_for_the_budget(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch, caplog,
):
    """R5: catch-up respects the flood budget. A ban drops the chat's
    earned rate to the floor, which is minimal mode — ornaments suspended.
    The owed card is an ornament: it stays owed and makes NO call (no
    burst into a fresh 429), and no traceback, until the chat can afford
    it. Then it goes, once.

    Mutation: drop the ``_card_refused_now`` check from
    ``_send_owed_card`` — the send is then refused by the limiter each
    tick (still no wire call, the gate holds) and this row catches the
    refusals being counted as ``ornaments_suspended``.
    """
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        _lift(flood_clock)
        assert limiter.minimal_mode(CHAT), "a ban no longer forces minimal mode?"
        budget = limiter._budget_for(limiter._key(CHAT))
        before = budget.ornaments_suspended
        for _ in range(3):
            await _tick(bot, monkeypatch)
        assert gated_bot.endpoints() == []
        assert budget.ornaments_suspended == before, "catch-up hammered the budget"
        assert _owed(sess)

        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)
        await _tick(bot, monkeypatch)

    with caplog.at_level(logging.DEBUG):
        run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]
    assert not [r for r in caplog.records
                if r.exc_info and r.levelno >= logging.WARNING]


def test_a_card_refused_by_minimal_mode_is_owed_and_logs_no_traceback(
    mk_bot, gated_bot, limiter, flood_clock, run_async, caplog,
):
    """The new failure mode found re-verifying claim 1: right after a lift
    the chat is in minimal mode, and a NEW prompt's card is refused with
    ``FloodSkipped``. 0.7.14 caught that in ``send_busy``'s generic arm —
    a WARNING with a full stack per prompt — and lost the card.

    Mutation: remove ``except FloodSkipped`` from ``send_busy`` and the
    traceback is back."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)
    _lift(flood_clock)
    assert limiter.minimal_mode(CHAT)
    with caplog.at_level(logging.DEBUG):
        run_async(_prompt(bot, sess))
    assert gated_bot.endpoints() == []
    assert _owed(sess)
    assert not [r for r in caplog.records
                if r.exc_info and r.levelno >= logging.WARNING]


def test_a_turn_that_finishes_during_the_mute_delivers_only_its_held_answer(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """R2's other half: the card is owed only while the turn runs. A turn
    that ended inside the ban delivers its HELD answer after the lift and
    nothing else — no stray card for a turn that is over.

    Mutation: drop ``sess.busy_card_owed = False`` from
    ``_cancel_lazy_card`` AND the status check in ``_send_owed_card`` and
    a card appears after the answer."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    _rich_recorder(monkeypatch, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        await _finish(bot, sess, "the answer")
        assert gated_bot.calls == []
        assert HELD.count(CHAT) == 1

        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendRichMessage"], gated_bot.calls
    assert "the answer" in gated_bot.calls[0][2]["text"]
    assert not _owed(sess)


def test_the_owed_card_is_forgotten_by_the_finish_path(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The status check alone is not enough: a turn that ended during the
    mute and a NEW turn that started after it are both BUSY. The finish
    path must clear the old turn's debt, or the next turn inherits it.

    Mutation: drop ``sess.busy_card_owed = False`` from
    ``_cancel_lazy_card``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    _rich_recorder(monkeypatch, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        assert _owed(sess)
        await _finish(bot, sess, "the answer")

    run_async(scenario())
    assert not _owed(sess)


def test_a_second_turn_start_for_the_same_turn_does_not_reset_it(
    mk_bot, gated_bot, limiter, flood_clock, run_async,
):
    """A Telegram prompt calls ``_send_busy_and_animate`` and its own
    ``UserPromptSubmit`` hook calls it again a moment later. With a live
    card the second call is the "already showing busy" no-op; with an
    OWED card there is nothing on the stack, and it used to fall into the
    full turn-start reset again — restarting the turn's clock and moving
    its transcript offset past what the turn had already written.

    Mutation: drop the ``busy_card_owed`` early return at the top of
    ``_send_busy_and_animate``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        started = sess.busy_started_at
        sess.tool_history.append({"name": "Bash"})
        bot.registry.transition(sess.name, Status.BUSY)    # BUSY -> BUSY no-op
        await bot._send_busy_and_animate(sess)
        assert sess.busy_started_at == started
        assert sess.tool_history, "the same turn was reset"

    run_async(scenario())
    assert gated_bot.calls == []


def test_a_genuinely_new_turn_after_an_owed_one_still_resets(
    mk_bot, gated_bot, limiter, flood_clock, run_async,
):
    """The control for the row above: the early return is for the SAME
    turn only (``job_reclaim_pending`` clear). A new turn — the flag set
    by ``transition`` — resets as always."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        sess.tool_history.append({"name": "Bash"})
        sess.job_reclaim_pending = True                     # a new turn
        await bot._send_busy_and_animate(sess)
        assert not sess.tool_history

    run_async(scenario())


# ── the paths that start a turn ─────────────────────────────────────────────

def test_a_quick_template_tap_during_a_mute(
    mk_bot, mk_update, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """A quick-template tap is a prompt like any other: zero calls, the
    card owed, and it goes after the lift.

    Mutation: as the headline row."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.IDLE
    bot.registry.last_active_session = sess.name
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    update = mk_update("🔍 Review", message_id=31, chat_id=CHAT)
    update.effective_chat.id = CHAT
    update.message.chat.id = CHAT
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot._send_template(update, "review this")
        assert gated_bot.calls == [], gated_bot.calls
        assert sess.status is Status.BUSY
        assert _owed(sess)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]
    assert gated_bot.calls[0][2]["reply_to_message_id"] == 31


def test_two_sessions_in_one_chat_held_answer_first_then_the_running_card(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """Two sessions share the muted DM. ``a``'s turn ends inside the ban
    (its answer is held); ``b``'s is still running at the lift. Catch-up
    is in order: the held answer, then ``b``'s card — and never a card
    for ``a``. Both sessions' cards made zero calls during the mute.

    Both go out on the FIRST tick after the lift: the held answers are
    flushed inside the per-session loop and the owed cards in a pass after
    it, so the card never has to wait a tick for an answer registered
    after it.

    Mutation: run ``_flush_owed_cards`` before the per-session loop in
    ``_scan`` and ``b``'s card finds ``a``'s answer still held — it is a
    tick late (and without the held gate it would overtake it)."""
    bot = _bot(mk_bot, gated_bot)
    b = _sess(bot, "b", trigger=21)          # first in scan order
    a = _sess(bot, "a", trigger=12)
    _rich_recorder(monkeypatch, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, a)
        await _prompt(bot, b)
        await _finish(bot, a, "a's answer")
        assert gated_bot.calls == []
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)           # ONE tick delivers both

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendRichMessage", "sendMessage"], gated_bot.calls
    assert "a's answer" in gated_bot.calls[0][2]["text"]
    assert gated_bot.calls[1][2]["reply_to_message_id"] == 21
    assert b.busy_msg_id == 5000
    assert not a.busy_msg_id


def test_an_owed_card_waits_while_an_answer_is_still_held(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """An answer whose delivery failed for a non-mute reason stays held
    for a retry on a later tick. An owed card must not overtake it.

    Mutation: drop ``_chat_holds_answers`` from ``_flush_owed_cards``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        HELD.hold(chat_id=CHAT, session="claude-gone", label="gone",
                  rich_text="x", plain_text="x")
        await _tick(bot, monkeypatch)
        assert gated_bot.endpoints() == []
        assert _owed(sess)
        HELD.clear()
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


def test_an_owed_card_for_a_turn_no_longer_running_is_dropped(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The status rule on its own, for a turn that ended without the
    finish path (a false-idle recovery, a GONE session): no card, and the
    debt is dropped rather than paid into the NEXT turn.

    Mutation: drop the status check from ``_send_owed_card``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        sess.status = Status.IDLE
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == []
    assert not _owed(sess)


def test_an_owed_card_is_not_sent_over_a_card_already_up(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """Something else put this turn's card up meanwhile (a compaction
    card, a claim in flight): that is the turn's card, and a second one
    would orphan it.

    Mutation: drop the ``if sess.busy_msg_id`` branch from
    ``_send_owed_card``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        sess.busy_msg_id = 888
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert "sendMessage" not in gated_bot.endpoints()
    assert sess.busy_msg_id == 888
    assert not _owed(sess)


def test_a_self_woken_turn_during_a_mute_owes_its_earned_card(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """8.32 during a mute. The deferral arms as usual (no card, no call);
    when it is EARNED — the delay ran out or a tool ran — the send is
    refused, and 0.7.14 cleared the mark and lost the card. Now it is
    owed and goes after the lift.

    Mutation: drop the owed flag in ``_open_busy_card`` (the helper
    ``_send_lazy_card`` sends through) and the card is lost."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess, lazy=True)
        assert sess.lazy_card_at and not _owed(sess)
        await bot._send_lazy_card(sess, reason="delay")
        assert gated_bot.calls == []
        assert not sess.lazy_card_at
        assert _owed(sess)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


def test_an_unearned_self_woken_card_is_not_owed(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The control: a deferral that was never earned stays a deferral —
    the lift sends nothing for it."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess, lazy=True)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == []
    assert not _owed(sess)


# ── R2's named rules: reclaim, anchor, re-anchor, typing ────────────────────

def test_reclaim_during_a_mute_leaves_the_old_card_and_owes_the_new_one(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """RECLAIM. A new prompt over a previous job's waiting card: the old
    card's settle edit is refused (``_edit_busy_rich`` returns False on a
    mute, never None) and it is forgotten locally as before; the new
    turn's card is owed, not sent, and after the lift it is a NEW message.

    Mutation: as the headline row."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.BUSY
    sess.busy_msg_id = 777
    sess.job_reclaim_pending = True
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot._send_busy_and_animate(sess)
        assert gated_bot.calls == []
        assert sess.busy_msg_id is None
        assert _owed(sess)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]
    assert sess.busy_msg_id == 5000


def test_anchor_the_late_card_replies_to_the_trigger_as_it_is_at_the_lift(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """ANCHOR / RE-ANCHOR. While the card is owed there is nothing to
    re-anchor: ``_reanchor_busy_card`` makes no call. Meanwhile the turn's
    anchor moves (a mid-turn message consumed). The late card is sent the
    way 8.32's late card is — replying to ``trigger_msg_id`` read AT SEND
    TIME — and records ``busy_card_trigger`` from that send, so there is
    no mismatch left for ``_consume_and_reanchor`` to fix.

    Mutation: pass the turn's original trigger to the owed send and the
    reply target is stale."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot, trigger=11)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        await bot._reanchor_busy_card(sess, 15, final=False)
        assert gated_bot.calls == []
        sess.trigger_msg_id = 15                 # the anchor moved
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.calls[0][2]["reply_to_message_id"] == 15
    assert sess.busy_card_trigger == 15


def test_typing_loop_starts_with_the_late_card_not_before(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """TYPING. An owed card does not start the chat's typing loop — the
    bubble is refused by the same mute and suspended by the same minimal
    mode, so a loop would only count refusals. The late card starts it
    through ``_start_animation``, like any card.

    Mutation: drop ``_start_animation`` from the owed send and the turn
    runs with a card that never animates and no bubble."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        assert not bot._typing_live(sess)
        assert not sess.animation_running()
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)
        assert sess.animation_running()
        assert bot._typing_live(sess)

    run_async(scenario())


def test_an_interactive_turn_keeps_its_card_owed(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """A turn waiting on a permission answer at the lift is still running:
    the debt is kept (the prompt, not a busy frame, is what belongs on
    screen), and paid once the turn is BUSY again."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        sess.status = Status.INTERACTIVE
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await bot._send_owed_card(sess, reason="mute lifted")
        assert gated_bot.endpoints() == []
        assert _owed(sess)
        sess.status = Status.BUSY
        await bot._send_owed_card(sess, reason="mute lifted")

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


# ── R3: Retry ───────────────────────────────────────────────────────────────

def _retry_query(message_id=42):
    query = MagicMock()
    query.data = "claude-dev:retry"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = "❌ failed"
    query.message.chat.id = CHAT
    query.from_user = MagicMock(id=12345)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    return update, query


def test_retry_during_a_mute_makes_zero_calls_and_does_not_half_apply(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """R3. 0.7.14: Retry injected the prompt into Claude, its delete of the
    error card was refused (so the Retry button stayed), and the card was
    refused and lost — so a second tap after the lift injected the prompt
    AGAIN. Now nothing happens at all: no injection, no transition, no
    Telegram call — not even the toast, which the 0.7.14 dispatcher
    withholds during a ban (8.26 D-1: ``answerCallbackQuery`` into a ban
    is a request into it). The tap can simply be repeated after the lift.

    Mutation: remove the mute check from the ``retry`` branch and the
    prompt is injected."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.IDLE
    sess.last_prompt = "the prompt"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    update, query = _retry_query()
    MUTE.mute(CHAT, BAN)

    run_async(bot._handle_callback(update, MagicMock()))

    bot._inject_prompt.assert_not_awaited()
    assert sess.status is Status.IDLE
    assert gated_bot.calls == []
    query.answer.assert_not_awaited()
    assert not _owed(sess)


def test_retry_during_a_mute_offers_the_clear_time_toast(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The toast Retry hands the dispatcher names the clear time. It is
    withheld while the ban runs (the row above), so this pins the text
    through ``_safe_answer`` itself.

    Mutation: return without answering and nothing is offered."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.IDLE
    sess.last_prompt = "the prompt"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    offered: list = []

    async def _answer(query, text=None, **kw):
        offered.append(text)

    bot._safe_answer = _answer
    update, query = _retry_query()
    MUTE.mute(CHAT, BAN)

    run_async(bot._handle_callback(update, MagicMock()))
    texts = [t for t in offered if t]
    assert len(texts) == 1
    assert texts[0].startswith("Telegram is rate-limiting this chat — try again after ")
    from aipager.bot.flood import clear_time
    assert texts[0].endswith(clear_time(flood_clock.wall + BAN))


def test_retry_after_the_lift_works_as_before(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The control: the refusal is the mute's, not Retry's."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.IDLE
    sess.last_prompt = "the prompt"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    update, query = _retry_query()
    MUTE.mute(CHAT, BAN)
    _lift(flood_clock)
    _affordable(limiter, flood_clock)

    run_async(bot._handle_callback(update, MagicMock()))
    bot._inject_prompt.assert_awaited_once()
    assert sess.status is Status.BUSY
    assert gated_bot.endpoints() == ["deleteMessage", "sendMessage"]


# ── R4 + R5: the keyboard ───────────────────────────────────────────────────

def test_a_restart_during_a_ban_keeps_the_keyboard_and_sends_it_after_the_lift(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch, caplog,
):
    """R4. A restart while the ban is still in force (the mute is restored
    from disk): the startup keyboard is held for the Mini App URL, then
    released into the mute. 0.7.14 cleared the hold BEFORE the send, so
    the send's "restore the hold on a mute" restored ``False`` and the
    keyboard was gone until some unrelated refresh.

    Mutations: drop the ``_keyboard_owed`` record in ``_send_keyboard``'s
    mute branch and nothing is sent after the lift; put back the
    pre-send clear in ``release_deferred_keyboard`` and the hold is gone
    during the ban; drop ``MUTE.is_muted`` from ``flush_owed_keyboards``
    and it tries (and logs) every tick of the ban."""
    bot = _bot(mk_bot, gated_bot)
    bot.defer_first_keyboard()
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot.release_deferred_keyboard()
        assert gated_bot.calls == []
        # R4: still deferred — cleared only by a send that went out.
        assert bot._keyboard_deferred is True
        with caplog.at_level(logging.INFO):
            await _tick(bot, monkeypatch)        # still muted: nothing
            await _tick(bot, monkeypatch)
        assert gated_bot.calls == []
        assert not [r for r in caplog.records if "keyboard" in r.getMessage()], \
            "the catch-up tried the keyboard every tick of the ban"
        _lift(flood_clock)
        await _tick(bot, monkeypatch)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert bot._keyboard_deferred is False
    assert gated_bot.endpoints() == ["sendMessage"]
    assert "reply_markup" in gated_bot.calls[0][2]


def test_a_commands_refresh_during_a_ban_owes_the_keyboard_too(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The same loss without a restart: a session appearing during the
    ban refreshes the commands and sends the main keyboard, which the mute
    refuses. It is owed and sent after the lift."""
    bot = _bot(mk_bot, gated_bot)
    bot._app.bot.set_my_commands = AsyncMock()
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot._update_bot_commands()
        assert gated_bot.calls == []
        _lift(flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


def test_a_url_hold_is_not_released_by_the_lift(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The control: the startup hold for the Mini App URL is a different
    debt. A tick with no mute in sight must not send it early — that is
    the 'first keyboard without its App button' bug the hold exists for."""
    bot = _bot(mk_bot, gated_bot)
    bot.defer_first_keyboard()
    run_async(_tick(bot, monkeypatch))
    assert gated_bot.calls == []
    assert bot._keyboard_deferred is True


def test_catch_up_order_held_answer_then_card_then_keyboard(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """R5's order across all three debts, on the first tick after the lift:
    a finished turn's held answer, then a running turn's card, then the
    keyboard.

    Mutation: call ``on_mute_catchup`` before ``_scan`` in
    ``SessionMonitor.tick`` and the keyboard, finding an answer still
    held, misses the tick."""
    bot = _bot(mk_bot, gated_bot)
    running = _sess(bot, "running", trigger=21)
    done = _sess(bot, "done", trigger=12)
    _rich_recorder(monkeypatch, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, running)
        await _prompt(bot, done)
        await _finish(bot, done, "done's answer")
        await bot.release_deferred_keyboard()
        await bot._send_keyboard(level="main")
        assert gated_bot.calls == []
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)           # ONE tick delivers all three

    run_async(scenario())
    got = gated_bot.endpoints()
    assert got == ["sendRichMessage", "sendMessage", "sendMessage"], gated_bot.calls
    assert gated_bot.calls[1][2]["reply_to_message_id"] == 21      # the card
    assert "reply_markup" in gated_bot.calls[2][2]                  # the keyboard
    assert gated_bot.calls[2][2].get("reply_to_message_id") is None


def test_the_keyboard_follows_an_owed_card_on_the_same_tick(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """No held answer at all: an owed card and an owed keyboard. The card
    is sent by the scan, the keyboard by the catch-up after it — card
    first, both on the first tick.

    Mutation: call ``on_mute_catchup`` before ``_scan`` in
    ``SessionMonitor.tick`` and the keyboard goes first."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot, trigger=21)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        await bot._send_keyboard(level="main")
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage", "sendMessage"]
    assert gated_bot.calls[0][2]["reply_to_message_id"] == 21
    assert "reply_to_message_id" not in gated_bot.calls[1][2]


def test_the_keyboard_waits_while_an_answer_is_still_held(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """An answer whose delivery fails for a non-mute reason stays held for
    a few ticks; the keyboard waits behind it rather than overtaking it.

    Mutation: drop the ``HELD.count`` check from ``flush_owed_keyboards``."""
    bot = _bot(mk_bot, gated_bot)
    MUTE.mute(CHAT, BAN)
    HELD.hold(chat_id=CHAT, session="claude-gone", label="gone",
              rich_text="x", plain_text="x")

    async def scenario():
        await bot._send_keyboard(level="main")
        _lift(flood_clock)
        await bot.flush_owed_keyboards()
        assert gated_bot.calls == []
        HELD.clear()
        await bot.flush_owed_keyboards()

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


@pytest.mark.parametrize("level", ["templates", "commands"])
def test_a_sub_keyboard_refused_by_the_mute_is_not_owed(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch, level,
):
    """Only the MAIN keyboard is a debt. A Templates/Commands keyboard is
    the reply to a tap made during the ban — a command reply, which the
    ban withholds (docs: "a command typed during a mute answers with
    nothing at all"); re-sending a sub-menu hours later would be noise."""
    bot = _bot(mk_bot, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot._send_keyboard(level=level)
        _lift(flood_clock)
        await _tick(bot, monkeypatch)

    run_async(scenario())
    assert gated_bot.calls == []


# ── review-1 follow-ups: the non-mute failure arms, and two edge rules ───────

def test_a_release_that_fails_for_another_reason_drops_the_url_hold(
    mk_bot, gated_bot, limiter, flood_clock, run_async,
):
    """``release_deferred_keyboard`` no longer clears the hold before the
    send (R4). A send that RAISES for a reason that is not the mute is an
    attempt, as it always was: the hold is dropped, or it would block
    every later commands refresh's keyboard for good.

    Mutation: drop ``self._keyboard_deferred = False`` from the release's
    ``except`` arm."""
    bot = _bot(mk_bot, gated_bot)
    bot.defer_first_keyboard()
    bot._send_keyboard = AsyncMock(side_effect=RuntimeError("boom"))
    run_async(bot.release_deferred_keyboard())
    assert bot._keyboard_deferred is False


def test_an_owed_keyboard_whose_send_raises_is_not_retried_every_tick(
    mk_bot, gated_bot, limiter, flood_clock, run_async, caplog,
):
    """A keyboard that keeps failing for a non-mute reason must not be
    retried — and logged at WARNING — every 2 s for ever.

    Mutation: drop the ``pop`` from ``flush_owed_keyboards``' ``except``
    arm."""
    bot = _bot(mk_bot, gated_bot)
    MUTE.mute(CHAT, BAN)
    run_async(bot._send_keyboard(level="main"))
    assert bot._keyboard_owed
    _lift(flood_clock)
    bot._send_keyboard = AsyncMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            run_async(bot.flush_owed_keyboards())
    assert bot._send_keyboard.await_count == 1
    assert not bot._keyboard_owed


def test_any_main_keyboard_that_goes_out_pays_the_debt(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """A main keyboard sent after the lift by some other path (a Back to
    main, /start) is the keyboard the mute owed: the catch-up must not
    send a second one.

    Mutation: drop the ``_keyboard_owed.pop`` in ``_send_keyboard``."""
    bot = _bot(mk_bot, gated_bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await bot._send_keyboard(level="main")
        _lift(flood_clock)
        await bot._send_keyboard(level="main")          # e.g. /start
        await bot.flush_owed_keyboards()

    run_async(scenario())
    assert gated_bot.endpoints() == ["sendMessage"]


def test_an_owed_card_is_not_paid_into_a_new_turn_that_has_not_started(
    mk_bot, gated_bot, limiter, flood_clock, run_async,
):
    """A tick can land between a new turn's ``transition(BUSY)`` and its
    own turn start. The debt is the old turn's; the new turn's reset
    decides about the card.

    Mutation: drop the ``job_reclaim_pending`` check from
    ``_send_owed_card``."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    async def scenario():
        await _prompt(bot, sess)
        _lift(flood_clock)
        _affordable(limiter, flood_clock)
        sess.job_reclaim_pending = True
        await bot._send_owed_card(sess, reason="mute lifted")

    run_async(scenario())
    assert gated_bot.endpoints() == []


def test_retry_is_refused_when_the_session_chat_is_muted_but_the_tap_chat_is_not(
    mk_bot, gated_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """Retry checks both chats: the tapped message's and the session's.
    A session whose chat is banned must not be re-prompted from anywhere.

    Mutation: drop ``resolve_chat_id(sess)`` from the retry check."""
    bot = _bot(mk_bot, gated_bot)
    sess = _sess(bot)
    sess.status = Status.IDLE
    sess.last_prompt = "the prompt"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    update, query = _retry_query()
    query.message.chat.id = -100555
    MUTE.mute(CHAT, BAN)
    run_async(bot._handle_callback(update, MagicMock()))
    bot._inject_prompt.assert_not_awaited()
    assert gated_bot.calls == []


def test_a_keyboard_paid_while_another_is_being_sent_is_not_sent_twice(
    mk_bot, gated_bot, limiter, flood_clock, run_async,
):
    """Multi-chat: while the catch-up awaits one chat's keyboard, a /start
    in another owed chat sends that chat's main keyboard and pays its
    debt. The catch-up iterates a snapshot, so it must re-check before
    sending the second.

    Mutation: drop the ``key not in self._keyboard_owed`` re-check from
    ``flush_owed_keyboards``."""
    bot = _bot(mk_bot, gated_bot)
    bot._keyboard_owed = {111: 111, 222: 222}
    sent: list = []

    async def _send(level=None, chat_id=None):
        sent.append(chat_id)
        bot._keyboard_owed.pop(chat_id, None)
        bot._keyboard_owed.pop(222, None)        # the concurrent /start

    bot._send_keyboard = _send
    run_async(bot.flush_owed_keyboards())
    assert sent == [111]


def test_the_held_gate_finds_answers_held_under_a_non_numeric_chat(mk_bot):
    """``_hold_answer`` files an answer under the session's numeric chat,
    else its raw one; the owed-card gate must look under the same key or
    a card could overtake an answer in such a chat.

    Mutation: drop the ``or resolve_chat_id(sess)`` fallback from
    ``session_monitor._held_chat_key``."""
    from aipager.session_monitor import _chat_holds_answers, _held_chat_key

    bot = mk_bot()
    sess = _sess(bot)
    sess.scope_chat_id = "@a_channel"
    key = _held_chat_key(sess)
    HELD.hold(chat_id=key, session=sess.name, label="dev",
              rich_text="x", plain_text="x")
    assert key == "@a_channel"
    assert _chat_holds_answers(_held_chat_key(sess))
