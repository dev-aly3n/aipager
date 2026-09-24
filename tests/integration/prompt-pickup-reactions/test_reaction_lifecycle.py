"""The reaction on the user's message follows its lifecycle:
👀 handed to the session → 👍 Claude took it, or 🤷 it will never be taken.

Scenarios drive the real handlers and ``notify()``; the only observable is
what reached ``set_message_reaction``. See ``aipager/bot/reactions.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager import preferences as prefs
from aipager.state import Status

CHAT_ID = -3003
EYES, THUMBS, SHRUG = "👀", "👍", "🤷"


def _busy_with_m1(bot, sess, run_async, send_text, pickup):
    """M1 starts a turn from idle and Claude takes it."""
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    run_async(send_text(bot, "first", 1))
    run_async(pickup(bot, sess, 1))
    assert sess.status == Status.BUSY
    # The MessageDisplay hook is live: the transcript scan that detects
    # absorptions runs, so Claude's queue as aipager sees it is trusted.
    sess.stream_hook_live = True


def _queue_m2(bot, sess, run_async, send_text, pickup):
    """M2 is sent while the turn runs; Claude Code queues it and fires
    the submit-time pick-up for it."""
    run_async(send_text(bot, "second", 2))
    run_async(pickup(bot, sess, 2))


def _hold_m3(bot, sess, run_async, send_text):
    """M3 arrives while a permission dialog is open: held, not injected."""
    status = sess.status
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    run_async(send_text(bot, "held", 3))
    assert len(sess.pending_queue) == 1, "M3 must be held"
    sess.pending_permission = None
    sess.status = status


def _finish_turn(bot, sess, run_async):
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "done", "raw_md": "done",
    }))


# ── R1 + R2: idle inject ────────────────────────────────────────────────────

def test_idle_inject_gets_eyes_then_thumbs_at_its_own_pickup(
        wired, run_async, send_text, pickup, reactions):
    """The exact signal for an idle inject is the hook's pick-up of its
    note (``UserPromptSubmit`` → ``queue_pickup{consumed}``): 👀 at the
    send, 👍 at the pick-up and not before."""
    bot, sess, _inj, _keys = wired
    run_async(send_text(bot, "hello", 1))
    assert reactions(bot) == {1: [EYES]}, "👀 on hand-off, nothing else yet"
    run_async(pickup(bot, sess, 1))
    assert reactions(bot) == {1: [EYES, THUMBS]}


# ── R2: busy-queued ─────────────────────────────────────────────────────────

def test_busy_queued_message_keeps_eyes_until_it_starts_the_next_turn(
        wired, run_async, send_text, pickup, reactions, rich_calls):
    """Claude Code fires the pick-up for a mid-turn message at SUBMIT time,
    when it only QUEUED it. 👍 must wait for the message to be taken: here,
    popped as the next turn when this one ends."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    assert reactions(bot).get(2) == [EYES], (
        "a queued message is not taken yet — no 👍 at the submit-time pick-up")

    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(2) == [EYES, THUMBS], (
        "popped as the next turn — 👍 now")


def test_message_absorbed_mid_turn_gets_thumbs_at_the_absorption(
        wired, run_async, send_text, pickup, reactions, append_queue_op):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    assert reactions(bot).get(2) == [EYES]

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram]\nsecond")
    run_async(bot.notify(sess, "assistant_text", {
        "delta": "folding that in", "message_id": "m-1",
    }))
    assert reactions(bot).get(2) == [EYES, THUMBS]


def test_message_delivered_to_a_background_agent_gets_thumbs(
        wired, run_async, send_text, pickup, reactions, append_queue_op):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    append_queue_op(sess, "remove", "delivered_to_agent", "second")
    run_async(bot.notify(sess, "assistant_text", {
        "delta": "passing it on", "message_id": "m-1",
    }))
    assert reactions(bot).get(2) == [EYES, THUMBS]


# ── R3: never taken ─────────────────────────────────────────────────────────

def test_escape_in_the_terminal_drops_the_target_but_sets_no_reaction(
        wired, run_async, send_text, pickup, reactions, append_queue_op,
        rich_calls):
    """Escape pulls Claude Code's queue back into the input box
    (``popAll``). The message is no longer queued, so the turn's end must
    not start a turn for it and 👍 it; but it may be edited and resubmitted
    from the terminal, so its fate is unknown and it keeps 👀."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    append_queue_op(sess, "popAll", None, "second")
    run_async(bot.notify(sess, "assistant_text", {
        "delta": "interrupted", "message_id": "m-1",
    }))
    assert sess.queued_targets == []
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(2) == [EYES], "no phantom next turn, no 🤷"


def test_popall_seen_only_at_the_stop_still_drops_the_target(
        wired, run_async, send_text, pickup, reactions, append_queue_op,
        rich_calls):
    """The popAll line can first become readable at the turn's Stop (no
    tick in between): the finish path reads it before starting the next
    queued turn."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    append_queue_op(sess, "popAll", None, "second")
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(2) == [EYES]
    assert sess.queued_targets == []


def test_clearqueue_marks_queued_and_held_messages_not_delivered(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    # A held one too (a dialog opened after M2 was queued).
    _hold_m3(bot, sess, run_async, send_text)
    assert reactions(bot).get(3) == [EYES]

    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok and outcome.dropped == 2
    assert "KillLine" in keys, "Claude's own queue is wiped from the pty"
    got = reactions(bot)
    assert got.get(2) == [EYES, SHRUG]
    assert got.get(3) == [EYES, SHRUG]
    assert got.get(1) == [EYES, THUMBS], "the running turn's own is untouched"


def test_stop_marks_queued_and_held_messages_not_delivered(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _hold_m3(bot, sess, run_async, send_text)

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok and outcome.dropped == 2
    assert "KillLine" in keys, "Claude's own queue is wiped from the pty"
    got = reactions(bot)
    assert got.get(2) == [EYES, SHRUG]
    assert got.get(3) == [EYES, SHRUG]
    assert got.get(1) == [EYES, THUMBS]


def test_safety_halt_drops_held_messages_but_leaves_claudes_queue_open(
        wired, run_async, send_text, pickup, reactions):
    """The halt's Escapes pull Claude's queue into the input box without
    wiping it, so a queued message's fate is unknown: it keeps 👀. A held
    message is simply dropped: 🤷."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _hold_m3(bot, sess, run_async, send_text)
    run_async(bot._halt_for_safety(sess, "blocked"))
    got = reactions(bot)
    assert got.get(3) == [EYES, SHRUG]
    assert got.get(2) == [EYES]


def test_killed_session_marks_its_queued_message_not_delivered(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    outcome = run_async(bot._kill_session_core(sess.name, sess.label))
    assert outcome.result == "killed"
    assert reactions(bot).get(2) == [EYES, SHRUG]


def test_session_that_dies_marks_its_queued_message_not_delivered(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    bot.registry.transition(sess.name, Status.GONE)
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    assert reactions(bot).get(2) == [EYES, SHRUG]
    assert sess.queued_targets == []


def test_prompt_claude_code_refused_is_marked_not_delivered(
        wired, run_async, send_text, reactions):
    bot, sess, _inj, _keys = wired
    run_async(send_text(bot, "hello", 1))
    run_async(bot.notify(sess, "prompt_not_taken", {
        "grace": 8.0, "msg": (1, CHAT_ID),
    }))
    assert reactions(bot) == {1: [EYES, SHRUG]}


def test_held_message_whose_release_fails_is_marked_not_delivered(
        wired, run_async, send_text, reactions, monkeypatch):
    bot, sess, _inj, _keys = wired
    _hold_m3(bot, sess, run_async, send_text)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    run_async(bot._drain_next_queued(sess))
    assert reactions(bot) == {3: [EYES, SHRUG]}


# ── R4: commands, voice, /stop ─────────────────────────────────────────────

def test_a_command_queued_behind_a_turn_follows_the_queue_lifecycle(
        wired, run_async, mk_update, send_text, pickup, reactions,
        rich_calls):
    """A prompt-type command sent while a turn runs is queued by Claude
    Code (its pick-up names it): 👀, then 👍 when it starts the next turn."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    update = mk_update("Init", message_id=7, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/init"))
    assert reactions(bot).get(7) == [EYES]
    run_async(pickup(bot, sess, 7))
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(7) == [EYES, THUMBS]


def test_a_local_command_queued_behind_a_turn_is_done_when_the_turn_ends(
        wired, run_async, mk_update, send_text, pickup, reactions,
        rich_calls):
    """``/model`` sent while a turn runs: Claude Code runs it when the
    turn ends and fires no hook for it. 👀, then 👌 at the turn's end."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    update = mk_update("Sonnet", message_id=7, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/model sonnet"))
    assert reactions(bot).get(7) == [EYES]
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(7) == [EYES, "👌"]


def test_voice_transcript_gets_the_same_lifecycle(
        wired, run_async, mk_update, pickup, reactions, monkeypatch):
    bot, sess, _inj, _keys = wired
    from aipager.bot import new_flow, session_parity
    monkeypatch.setattr(new_flow, "maybe_handle_text",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(session_parity, "maybe_handle_text",
                        AsyncMock(return_value=False))
    update = mk_update("", message_id=9, chat_id=CHAT_ID)
    run_async(bot._dispatch_voice_transcript(update, "spoken words",
                                             MagicMock()))
    assert reactions(bot) == {9: [EYES]}
    run_async(pickup(bot, sess, 9))
    assert reactions(bot) == {9: [EYES, THUMBS]}


def test_stop_command_is_acknowledged_with_an_allowed_emoji(
        wired, run_async, mk_update, reactions):
    bot, sess, _inj, _keys = wired
    sess.status = Status.BUSY
    update = mk_update("/stop", message_id=11, chat_id=CHAT_ID)
    run_async(bot._stop_session(sess, update=update))
    assert reactions(bot) == {11: ["👌"]}


# ── R5: bounded, never repeated ─────────────────────────────────────────────

def test_a_taken_message_is_never_reacted_to_again(
        wired, run_async, send_text, pickup, reactions):
    """Every later edge a taken message can see: a repeated pick-up, the
    queue cleared, the session killed. One 👀, one 👍, nothing more."""
    bot, sess, _inj, _keys = wired
    run_async(send_text(bot, "hello", 1))
    run_async(pickup(bot, sess, 1))
    wire = [{"msg_id": 1, "chat_id": CHAT_ID, "raw_text": "hello"}]
    run_async(bot.notify(sess, "queue_pickup", {"consumed": wire,
                                                "expired": []}))
    sess.queued_targets.append(dict(wire[0]))
    sess.stream_hook_live = True
    sess.stream_transcript_path = sess.transcript_path
    run_async(bot._clear_queue_core(sess))
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert reactions(bot) == {1: [EYES, THUMBS]}


def test_the_ledger_allows_at_most_three_calls_per_message(wired, run_async):
    """👀 → 🤷 → 👍 is the longest run; every repeat or step back is
    dropped before it reaches Telegram."""
    from aipager.bot.reactions import set_reaction
    bot, _sess, _inj, _keys = wired

    async def _all():
        for emoji in (EYES, EYES, SHRUG, EYES, SHRUG, THUMBS, SHRUG, EYES,
                      THUMBS):
            await set_reaction(bot, CHAT_ID, 1, emoji)
    run_async(_all())
    got = [c.args[2] for c in
           bot._app.bot.set_message_reaction.await_args_list]
    assert got == [EYES, SHRUG, THUMBS]


def test_thumbs_decided_while_eyes_is_in_flight_lands_after_it(
        wired, run_async):
    """The 👍 for an idle inject can be decided while the 👀 call is still
    on the wire; Telegram must see them in that order or the message ends
    on 👀."""
    from aipager.bot.reactions import set_reaction
    bot, _sess, _inj, _keys = wired
    order: list[str] = []
    gate = asyncio.Event()

    async def _slow(chat_id, msg_id, emoji, **_kw):
        if emoji == EYES:
            await gate.wait()
        order.append(emoji)

    bot._app.bot.set_message_reaction = _slow

    async def _scenario():
        eyes = asyncio.create_task(set_reaction(bot, CHAT_ID, 1, EYES))
        await asyncio.sleep(0)
        thumbs = asyncio.create_task(set_reaction(bot, CHAT_ID, 1, THUMBS))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(eyes, thumbs)
        # A 👀 decided after the 👍 never goes out.
        await set_reaction(bot, CHAT_ID, 1, EYES)

    run_async(_scenario())
    assert order == [EYES, THUMBS]


def test_a_disallowed_emoji_is_never_sent(wired, run_async):
    from aipager.bot.reactions import set_reaction
    bot, _sess, _inj, _keys = wired
    assert run_async(set_reaction(bot, CHAT_ID, 1, "✅")) is False
    assert bot._app.bot.set_message_reaction.await_count == 0


def test_no_reaction_reaches_a_muted_chat_and_none_is_replayed(
        wired, run_async, send_text, pickup):
    """Reactions are SIGNAL: the outbound gate refuses them into a flood
    mute like anything else. The skipped edge is not replayed after the
    lift — a later edge for the same state is deduplicated, and only the
    next NEW state goes out."""
    from aipager.bot.flood import MUTE
    from aipager.bot.flood_budget import BudgetRateLimiter

    bot, sess, _inj, _keys = wired
    limiter = BudgetRateLimiter(signal_path=None)
    wire_calls: list[str] = []

    async def _gated(chat_id, msg_id, emoji, rate_limit_args=None):
        async def _call():
            wire_calls.append(emoji)
        return await limiter.process_request(
            callback=_call, args=(), kwargs={},
            endpoint="setMessageReaction", data={"chat_id": chat_id},
            rate_limit_args=rate_limit_args,
        )

    bot._app.bot.set_message_reaction = _gated
    MUTE.mute(CHAT_ID, 60)
    run_async(send_text(bot, "hello", 1))
    assert wire_calls == [], "👀 went into a muted chat"
    MUTE.clear()
    run_async(pickup(bot, sess, 1))
    assert wire_calls == [THUMBS]
    wire = [{"msg_id": 1, "chat_id": CHAT_ID, "raw_text": "hello"}]
    run_async(bot.notify(sess, "queue_pickup", {"consumed": wire,
                                                "expired": []}))
    assert wire_calls == [THUMBS], "no replay, no repeat"
    limiter.reset()


# ── rev-iter1-001: an untrusted queue is left alone ─────────────────────────

def test_clearqueue_without_the_live_scan_sends_no_keys_and_no_shrug(
        wired, run_async, send_text, pickup, reactions):
    """Without the MessageDisplay hook the transcript scan never runs, so a
    queued target may have been absorbed long ago. An Escape into Claude
    Code's then-empty queue would interrupt the running turn."""
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    sess.stream_hook_live = False
    outcome = run_async(bot._clear_queue_core(sess))
    assert not outcome.ok and outcome.dropped == 0
    assert keys == []
    assert reactions(bot).get(2) == [EYES]


def test_clearqueue_in_a_background_job_window_sends_no_keys_and_no_shrug(
        wired, run_async, send_text, pickup, reactions, monkeypatch):
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    monkeypatch.setattr(type(sess), "job_background_open", lambda self: True)
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.dropped == 0
    assert keys == []
    assert reactions(bot).get(2) == [EYES]


def test_clearqueue_reads_an_absorption_no_tick_has_seen_yet(
        wired, run_async, send_text, pickup, reactions, append_queue_op):
    """The absorption line landed after the last tick: /clearqueue scans
    first, so the message is 👍 (taken) and nothing is left to discard."""
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.dropped == 0
    assert keys == []
    assert reactions(bot).get(2) == [EYES, THUMBS]


def test_session_end_without_the_live_scan_leaves_queued_messages_alone(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    sess.stream_hook_live = False
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    assert reactions(bot).get(2) == [EYES]


def test_clear_is_not_the_end_of_claudes_queue(
        wired, run_async, send_text, pickup, reactions):
    """``/clear`` fires SessionEnd too, but the process lives on."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    run_async(bot.notify(sess, "session_end", {"source": "clear"}))
    assert reactions(bot).get(2) == [EYES]
    assert [t["msg_id"] for t in sess.queued_targets] == [2]


# ── rev-iter1-004/005/006 ───────────────────────────────────────────────────

def _busy_command(bot, sess, run_async, mk_update, send_text, pickup,
                  command="/model sonnet", msg_id=7):
    """A command button tapped while a turn runs: Claude Code queues it."""
    from aipager.bot.reactions import ledger_of
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    update = mk_update("Cmd", message_id=msg_id, chat_id=CHAT_ID)
    run_async(bot._send_command(update, command))
    assert ledger_of(bot).current(CHAT_ID, msg_id) == EYES


def test_a_queued_command_torn_down_by_kill_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    """Still queued when the session is killed: it never ran — 🤷."""
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert reactions(bot).get(7) == [EYES, SHRUG]


def test_a_queued_command_torn_down_by_stop_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    run_async(bot._stop_session_core(sess))
    assert reactions(bot).get(7) == [EYES, SHRUG]


def test_a_queued_command_torn_down_by_clearqueue_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    run_async(bot._clear_queue_core(sess))
    assert reactions(bot).get(7) == [EYES, SHRUG]


def test_a_queued_command_left_when_the_session_exits_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    _feed(bot, run_async, _session_end_payload(sess, "prompt_input_exit"))
    assert reactions(bot).get(7) == [EYES, SHRUG]


def test_a_queued_prompt_command_torn_down_by_stop_or_kill_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    """A prompt-type command Claude Code queued (its pick-up named it) is a
    queued target: /stop drops it, and so would /kill."""
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup,
                  command="/init", msg_id=8)
    run_async(pickup(bot, sess, 8))
    run_async(bot._stop_session_core(sess))
    assert reactions(bot).get(8) == [EYES, SHRUG]


def test_a_queued_prompt_command_torn_down_by_kill_did_not_run(
        wired, run_async, mk_update, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup,
                  command="/init", msg_id=8)
    run_async(pickup(bot, sess, 8))
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert reactions(bot).get(8) == [EYES, SHRUG]


def test_a_queued_command_runs_at_a_background_job_interim_stop(
        wired, run_async, mk_update, send_text, pickup, reactions,
        rich_calls, monkeypatch):
    """The turn launched a background agent: its Stop is an interim one,
    but Claude Code runs its queue all the same — 👌."""
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    monkeypatch.setattr(type(sess), "job_background_open", lambda self: True)
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(7) == [EYES, "👌"]


def test_a_queued_command_runs_after_an_api_error_stop(
        wired, run_async, mk_update, send_text, pickup, reactions,
        rich_calls):
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 402 payment required",
        "raw_md": "API Error: 402 payment required",
    }))
    assert reactions(bot).get(7) == [EYES, "👌"]


def test_a_teardown_marks_at_most_the_newest_ten(wired, run_async, reactions):
    from aipager.bot.reactions import BULK_CAP
    bot, sess, _inj, _keys = wired
    sess.status = Status.BUSY
    for i in range(1, 31):
        assert sess.queue_prompt(f"held {i}", i)
    run_async(bot._stop_session_core(sess))
    got = reactions(bot)
    assert sorted(got) == list(range(31 - BULK_CAP, 31))
    assert all(v == [SHRUG] for v in got.values())


def test_message_id_zero_is_never_reacted_to(
        wired, run_async, reactions, monkeypatch):
    """``/new <prompt>`` and the picker queue their prompt as message 0."""
    bot, sess, _inj, _keys = wired
    assert sess.queue_prompt("from /new", 0)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    run_async(bot._drain_next_queued(sess))
    wire = [{"msg_id": 0, "chat_id": CHAT_ID, "raw_text": "from /new"}]
    run_async(bot.notify(sess, "queue_pickup", {"consumed": wire,
                                                "expired": []}))
    assert reactions(bot) == {}


# ── review-2 ────────────────────────────────────────────────────────────────

def test_stop_wipes_a_queue_its_own_escapes_just_pulled_into_the_input_box(
        wired, run_async, send_text, pickup, reactions, append_queue_op,
        monkeypatch):
    """/stop's first Escape makes Claude Code pull its queue back into the
    input box and write ``popAll``. The queue must be read BEFORE that, or
    the message is forgotten, the input box is never wiped, and its text
    rides along with the next prompt."""
    bot, sess, _inj, keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)

    async def _send_keys(_name, key):
        if key == "Escape" and "Escape" not in keys:
            append_queue_op(sess, "popAll", None, "second")
        keys.append(key)
        return True

    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.dropped == 1
    assert "KillLine" in keys
    assert reactions(bot).get(2) == [EYES, SHRUG]


def test_thumbs_up_is_never_capped(wired, run_async, reactions):
    """Claude can take a dozen queued messages in one go; every one of
    them is taken. The teardown cap is for 🤷 only."""
    bot, sess, _inj, _keys = wired
    consumed = [{"msg_id": i, "chat_id": CHAT_ID, "raw_text": f"m{i}"}
                for i in range(1, 13)]
    run_async(bot._apply_consumption(sess, consumed))
    assert sorted(reactions(bot)) == list(range(1, 13))


SID = "5f1c0b6e-0000-4000-8000-000000000000"
SID2 = "9a2d4c1e-0000-4000-8000-000000000000"


def _session_end_payload(sess, reason, session_id=SID):
    """What Claude Code 2.1.x sends for SessionEnd (its hook schema:
    ``{hook_event_name: "SessionEnd", reason: clear|resume|logout|
    prompt_input_exit|other}`` plus the base fields — ``session_id`` and a
    transcript named by it), with the ``session`` key aipager-hook adds."""
    return {
        "session_id": session_id,
        "transcript_path": str(__import__("pathlib").Path(
            sess.transcript_path or "/nonexistent/t.jsonl",
        ).with_name(f"{session_id}.jsonl")),
        "cwd": "/home/user/project",
        "hook_event_name": "SessionEnd",
        "reason": reason,
        "session": sess.name,
    }


def _session_start_payload(sess, source, session_id=SID):
    payload = _session_end_payload(sess, "", session_id)
    del payload["reason"]
    payload["hook_event_name"] = "SessionStart"
    payload["source"] = source
    return payload


def _feed(bot, run_async, payload):
    import json

    from aipager.dtach.hook_receiver import HookReceiver
    recv = HookReceiver(bot.registry, bot.notify)
    run_async(recv._on_datagram(json.dumps(payload).encode()))


def test_clear_leaves_claudes_queue_alone(
        wired, run_async, send_text, pickup, reactions):
    """``/clear`` ends the conversation, not the process."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _feed(bot, run_async, _session_end_payload(sess, "clear"))
    assert reactions(bot).get(2) == [EYES]
    assert [t["msg_id"] for t in sess.queued_targets] == [2]


def test_resume_leaves_claudes_queue_alone(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _feed(bot, run_async, _session_end_payload(sess, "resume"))
    assert reactions(bot).get(2) == [EYES]


def test_a_real_exit_marks_claudes_queue_not_delivered(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _feed(bot, run_async, _session_end_payload(sess, "prompt_input_exit"))
    assert reactions(bot).get(2) == [EYES, SHRUG]


def test_a_dropped_queued_command_is_marked_not_delivered(
        wired, run_async, mk_update, send_text, pickup, reactions):
    """A command Claude Code really queued (its pick-up named it) and
    /clearqueue then dropped was never run: 🤷, like any other message."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    update = mk_update("Init", message_id=8, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/init"))
    run_async(pickup(bot, sess, 8))
    run_async(bot._clear_queue_core(sess))
    assert reactions(bot).get(8)[-1] == SHRUG


def test_a_keyboard_command_is_done_at_injection(
        wired, run_async, mk_update, reactions):
    """``/model`` and friends run without any prompt hook: nothing would
    ever move them off 👀. Enter on Claude Code's prompt runs the command,
    so the injection is where it is done: 👌, one call."""
    bot, _sess, _inj, _keys = wired
    update = mk_update("Sonnet", message_id=7, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/model sonnet"))
    assert reactions(bot) == {7: ["👌"]}


def test_a_slash_command_turn_with_no_hook_ends_on_ok_not_shrug(
        wired, run_async, mk_update, reactions):
    """A slash command sent as a prompt from idle is judged by the 8 s
    watchdog like any turn. Silence from Claude Code is not a refusal for a
    command — a built-in may have opened a dialog or run locally — so it
    ends on 👌, never 🤷."""
    bot, sess, _inj, _keys = wired
    update = mk_update("/x /context", message_id=5, chat_id=CHAT_ID)
    run_async(bot._direct_send(update, "x", "/context"))
    assert reactions(bot) == {5: [EYES]}
    run_async(bot.notify(sess, "prompt_not_taken", {
        "grace": 8.0, "msg": (5, CHAT_ID),
    }))
    assert reactions(bot) == {5: [EYES, "👌"]}


def test_a_refused_slash_command_with_its_note_gone_still_ends_on_ok(
        wired, run_async, mk_update, reactions):
    """The watchdog reads the command from the message's note, and from
    the session's last prompt when the note is already gone."""
    from aipager.policy_snapshot import clear_notes_dir
    bot, sess, _inj, _keys = wired
    update = mk_update("/x /context", message_id=5, chat_id=CHAT_ID)
    run_async(bot._direct_send(update, "x", "/context"))
    clear_notes_dir(sess.name)
    run_async(bot.notify(sess, "prompt_not_taken", {
        "grace": 8.0, "msg": (5, CHAT_ID),
    }))
    assert reactions(bot) == {5: [EYES, "👌"]}



def test_only_commands_are_marked_done_when_a_turn_ends(
        wired, run_async, send_text, pickup, reactions, rich_calls):
    """A plain message whose pick-up has not come yet when the turn ends is
    not a command that ran: it keeps 👀."""
    bot, sess, _inj, _keys = wired
    _busy_with_m1(bot, sess, run_async, send_text, pickup)
    run_async(send_text(bot, "second", 2))
    _finish_turn(bot, sess, run_async)
    assert reactions(bot).get(2) == [EYES]


# ── round 4: the "Session ended" notice ─────────────────────────────────────

def _notices(bot):
    return [c.args[1] if len(c.args) > 1 else c.kwargs.get("text", "")
            for c in bot._app.bot.send_message.await_args_list
            if "Session" in str(c.args[1:2] or c.kwargs.get("text", ""))]


def test_no_exit_notice_right_after_aipagers_own_kill(
        wired, run_async, reactions):
    """/kill ends the dtach host, the pty closes and Claude Code fires
    SessionEnd with its default reason ``other``. The operator was just
    told "💀 Killed"; a second notice is noise."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert _notices(bot) == []


def test_an_ordinary_exit_is_not_called_unexpected(wired, run_async):
    """``other`` is Claude Code's default reason (a signal, among others),
    not evidence of a crash."""
    bot, sess, _inj, _keys = wired
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    notices = _notices(bot)
    assert len(notices) == 1 and "Session exited" in notices[0]
    assert "unexpectedly" not in notices[0]


def test_a_datagram_carrying_source_instead_of_reason_still_names_it(
        wired, run_async):
    """The receiver falls back to ``source`` when ``reason`` is absent."""
    bot, sess, _inj, _keys = wired
    payload = _session_end_payload(sess, "x")
    del payload["reason"]
    payload["source"] = "logout"
    _feed(bot, run_async, payload)
    notices = _notices(bot)
    assert len(notices) == 1 and "logged out" in notices[0]


def test_a_session_that_vanishes_marks_its_outstanding_notes_not_delivered(
        wired, run_async, mk_update, send_text, pickup, reactions,
        monkeypatch):
    """The monitor's "socket disappeared" path: the GONE transition deletes
    the notes dir, so the notes are read just before it."""
    from aipager.session_monitor import SessionMonitor
    bot, sess, _inj, _keys = wired
    _busy_command(bot, sess, run_async, mk_update, send_text, pickup)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))
    monitor = SessionMonitor(bot.registry, bot.notify)
    run_async(monitor._scan())
    assert reactions(bot).get(7) == [EYES, SHRUG]


# ── rounds 5-6: the /kill notice suppression is keyed by instance ─────────

def test_the_kill_suppression_expires_after_a_minute(wired, run_async):
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    bot.registry.killed_sessions[SID] -= 61
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert len(_notices(bot)) == 1


def test_a_new_instance_under_the_killed_name_gets_its_notice(
        wired, run_async):
    """Whatever launched it — /new, Replace (fresh), ``aipager resume``, a
    terminal — a new Claude Code process has a new session id, and its
    exit is news even within a minute of /kill of the old one."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    _feed(bot, run_async, _session_end_payload(sess, "prompt_input_exit",
                                               session_id=SID2))
    assert len(_notices(bot)) == 1


def test_a_same_name_session_launched_after_a_kill_gets_its_notice(
        wired, run_async, monkeypatch):
    """``/kill x`` then ``/new x``: the new session's own exit is news."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    name, err = run_async(bot.create_session("x", scope_chat_id=None))
    assert name == sess.name and not err
    _feed(bot, run_async, _session_end_payload(
        bot.registry.get(name), "prompt_input_exit", session_id=SID2))
    assert len(_notices(bot)) == 1


def test_the_killed_conversation_resumed_under_its_own_id_gets_its_notice(
        wired, run_async):
    """``claude --resume <id>`` can keep the killed conversation's id. Its
    SessionStart ends the suppression for that id, so its own later exit
    is announced."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert _notices(bot) == [], "the killed process's own SessionEnd"
    _feed(bot, run_async, _session_start_payload(sess, "resume"))
    _feed(bot, run_async, _session_end_payload(sess, "prompt_input_exit"))
    assert len(_notices(bot)) == 1


def test_a_compaction_does_not_end_the_suppression(wired, run_async):
    """SessionStart(compact) is the same process carrying on, not a new one."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    _feed(bot, run_async, _session_start_payload(sess, "compact"))
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert _notices(bot) == []


def test_a_kill_of_a_session_with_no_known_id_suppresses_nothing(
        wired, run_async):
    """Nothing to match a SessionEnd against: fail open to the notice."""
    bot, sess, _inj, _keys = wired
    sess.claude_session_id = ""
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert bot.registry.killed_sessions == {}
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert len(_notices(bot)) == 1


def test_a_session_end_during_the_kill_itself_is_not_announced(
        wired, run_async, monkeypatch):
    """Claude Code's SessionEnd can be handled while ``kill_session`` is
    still waiting for the dtach host to exit."""
    bot, sess, _inj, _keys = wired

    async def _kill(name):
        _feed_now = __import__("json").dumps(
            _session_end_payload(sess, "other")).encode()
        from aipager.dtach.hook_receiver import HookReceiver
        await HookReceiver(bot.registry, bot.notify)._on_datagram(_feed_now)
        return True

    monkeypatch.setattr("aipager.dtach.inject.kill_session", _kill)
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert _notices(bot) == []


def test_a_failed_kill_suppresses_nothing(wired, run_async, monkeypatch):
    bot, sess, _inj, _keys = wired
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    run_async(bot._kill_session_core(sess.name, sess.label))
    _feed(bot, run_async, _session_end_payload(sess, "other"))
    assert len(_notices(bot)) == 1


def test_old_kill_records_are_pruned(wired, run_async):
    import time
    bot, sess, _inj, _keys = wired
    bot.registry.killed_sessions["old-id"] = time.monotonic() - 120
    bot.registry.killed_sessions["recent-id"] = time.monotonic() - 5
    run_async(bot._kill_session_core(sess.name, sess.label))
    assert "old-id" not in bot.registry.killed_sessions
    assert "recent-id" in bot.registry.killed_sessions


def test_the_ended_process_is_known_from_its_transcript_name_too(
        wired, run_async):
    """A SessionEnd without ``session_id`` still names its process: Claude
    Code names the transcript by it."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    payload = _session_end_payload(sess, "other")
    del payload["session_id"]
    _feed(bot, run_async, payload)
    assert _notices(bot) == []


def test_a_session_end_naming_no_process_is_announced(wired, run_async):
    """No ``session_id`` and no transcript: it cannot be matched to the
    killed process, so the notice goes out (fail open)."""
    bot, sess, _inj, _keys = wired
    run_async(bot._kill_session_core(sess.name, sess.label))
    payload = _session_end_payload(sess, "other")
    del payload["session_id"]
    del payload["transcript_path"]
    _feed(bot, run_async, payload)
    assert len(_notices(bot)) == 1


def test_a_kill_that_raises_suppresses_nothing(wired, run_async, monkeypatch):
    """If killing the host fails with an error, the process may still be
    running: its later exit must be announced."""
    import pytest
    bot, sess, _inj, _keys = wired
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(side_effect=OSError("boom")))
    with pytest.raises(OSError):
        run_async(bot._kill_session_core(sess.name, sess.label))
    assert bot.registry.killed_sessions == {}
