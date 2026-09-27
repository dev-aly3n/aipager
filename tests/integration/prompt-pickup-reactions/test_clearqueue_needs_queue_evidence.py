"""Roadmap 8.37: ``/clearqueue`` and ``/stop`` type Escape+KillLine into
Claude only when the transcript shows Claude holding a queued message.

An Escape into Claude Code's empty queue interrupts the running turn. The
old trigger was any outstanding note, and a slash command tapped while the
session was idle (``/model``) fires no hook, so its note was never
consumed: a later ``/clearqueue`` read it as "queued" and interrupted a
turn that had nothing queued at all.

The evidence is Claude Code's own ``queue-operation`` lines: an
``enqueue`` for the message with no later ``dequeue``/``remove``/``popAll``.
Scenarios drive the real handlers; the observables are the keys typed into
the pty and the reactions set.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager import policy_snapshot as ps
from aipager.state import Status

CHAT_ID = -3003
EYES, THUMBS, SHRUG, OK = "👀", "👍", "🤷", "👌"


def _op(sess, operation, content=None, reason=None):
    """Append one ``queue-operation`` line the way Claude Code writes it
    (``dequeue`` and a bare ``remove`` carry no content)."""
    line = {"type": "queue-operation", "operation": operation,
            "timestamp": "2026-09-27T10:00:00.000Z", "sessionId": "s"}
    if content is not None:
        line["content"] = content
    if reason is not None:
        line["reason"] = reason
    with open(sess.transcript_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _body_of(sess, msg_id):
    """The exact text aipager typed for *msg_id* (the note's ``body``,
    identity marker included) - what Claude Code's ``content`` carries."""
    for note in ps.list_outstanding_notes(sess.name):
        if note.get("msg_id") == msg_id:
            return note["body"]
    raise AssertionError(f"no note for {msg_id}")


def _idle_model(bot, sess, run_async, mk_update, msg_id=7):
    """``/model sonnet`` tapped while the session is idle: Claude Code runs
    it on the spot, writes no queue line and fires no prompt hook."""
    sess.status = Status.IDLE
    bot._confirm_model_feedback = AsyncMock()   # 8.35's statusline wait
    update = mk_update("Sonnet", message_id=msg_id, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/model sonnet"))


def _turn_from_idle(bot, sess, run_async, send_text, pickup, *, live):
    """M1 starts a turn from idle: Claude Code enqueues and dequeues it in
    the same instant, then fires UserPromptSubmit."""
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    run_async(send_text(bot, "first", 1))
    body = _body_of(sess, 1)
    _op(sess, "enqueue", body)
    _op(sess, "dequeue")
    run_async(pickup(bot, sess, 1))
    assert sess.status == Status.BUSY
    sess.stream_hook_live = live


def _queue_m2(bot, sess, run_async, send_text, pickup, *, enqueue=True):
    """M2 is sent while the turn runs; Claude Code queues it (``enqueue``)
    and fires the submit-time pick-up for it."""
    run_async(send_text(bot, "second", 2))
    if enqueue:
        _op(sess, "enqueue", _body_of(sess, 2))
    run_async(pickup(bot, sess, 2))


HOOK = pytest.mark.parametrize("live", [True, False],
                               ids=["hook-live", "hook-not-live"])


# ── the 8.37 bug ────────────────────────────────────────────────────────────

@HOOK
def test_idle_model_then_a_turn_then_clearqueue_types_nothing(
        wired, run_async, mk_update, send_text, pickup, reactions, live):
    bot, sess, _inj, keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    card = sess.busy_msg_id

    outcome = run_async(bot._clear_queue_core(sess))

    assert keys == [], "an Escape here would interrupt the running turn"
    assert not outcome.ok and outcome.dropped == 0
    assert sess.status == Status.BUSY and sess.busy_msg_id == card
    got = reactions(bot)
    assert got.get(7) == [OK], "the command ran: never 🤷"
    assert got.get(1) == [EYES, THUMBS]


@HOOK
def test_idle_model_then_a_turn_then_clearqueue_command_says_nothing_to_clear(
        wired, run_async, mk_update, send_text, pickup, live):
    bot, sess, _inj, keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    update = mk_update("/clearqueue", message_id=40, chat_id=CHAT_ID)
    run_async(bot._handle_clearqueue_cmd(update, None))
    assert keys == []
    assert "Nothing to clear" in update.message.reply_text.await_args.args[0]


@HOOK
def test_stop_after_an_idle_model_sends_only_its_interrupt(
        wired, run_async, mk_update, send_text, pickup, reactions, live):
    """/stop interrupts (Escape twice) whatever it finds; the queue wipe
    after it is for a message Claude really holds, not a stale note."""
    bot, sess, _inj, keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok and outcome.dropped == 0
    assert keys == ["Escape", "Escape"]
    assert reactions(bot).get(7) == [OK]


# ── a message Claude really holds is still cleared ──────────────────────────

@HOOK
def test_a_genuinely_queued_message_is_cleared_and_shrugged(
        wired, run_async, send_text, pickup, reactions, live):
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    card = sess.busy_msg_id

    outcome = run_async(bot._clear_queue_core(sess))

    assert outcome.ok and outcome.dropped == 1
    assert keys == ["Escape", "KillLine"], "never the interrupt pair"
    assert reactions(bot).get(2) == [EYES, SHRUG]
    assert reactions(bot).get(1) == [EYES, THUMBS]
    assert sess.status == Status.BUSY and sess.busy_msg_id == card
    assert sess.queued_targets == []


@HOOK
def test_a_genuinely_queued_message_beside_a_stale_note_counts_once(
        wired, run_async, mk_update, send_text, pickup, reactions, live):
    bot, sess, _inj, keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.dropped == 1
    assert keys == ["Escape", "KillLine"]
    assert reactions(bot).get(7) == [OK]


@HOOK
def test_stop_still_wipes_a_message_claude_holds(
        wired, run_async, send_text, pickup, reactions, live):
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.dropped == 1
    assert keys == ["Escape", "Escape", "Escape", "KillLine"]
    assert reactions(bot).get(2) == [EYES, SHRUG]


@HOOK
def test_a_queued_message_with_no_enqueue_line_is_not_evidence(
        wired, run_async, send_text, pickup, reactions, live):
    """aipager's own record of a queued target is not proof on its own:
    without Claude Code's ``enqueue`` line, nothing is typed."""
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup, enqueue=False)
    outcome = run_async(bot._clear_queue_core(sess))
    assert keys == []
    assert outcome.dropped == 0
    assert reactions(bot).get(2) == [EYES]


# ── a fate already written is not a queued message ──────────────────────────

FATES = pytest.mark.parametrize("fate", [
    ("remove", "second", "absorbed_mid_turn"),
    ("remove", "second", "delivered_to_agent"),
    ("remove", None, None),
    ("dequeue", None, None),
    ("popAll", "second", None),
], ids=["absorbed", "to-agent", "bare-remove", "dequeue", "popAll"])


@HOOK
@FATES
def test_a_message_whose_fate_is_already_written_gets_no_keys(
        wired, run_async, send_text, pickup, live, fate):
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    operation, content, reason = fate
    if content == "second":
        content = _enqueued_content(sess)
    _op(sess, operation, content, reason)
    run_async(bot._clear_queue_core(sess))
    assert keys == []


@HOOK
@FATES
def test_stop_after_the_fate_is_written_sends_only_its_interrupt(
        wired, run_async, send_text, pickup, live, fate):
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    operation, content, reason = fate
    if content == "second":
        content = _enqueued_content(sess)
    _op(sess, operation, content, reason)
    run_async(bot._stop_session_core(sess))
    assert keys == ["Escape", "Escape"]


def _enqueued_content(sess):
    """The ``content`` of the last ``enqueue`` line in the transcript."""
    last = None
    with open(sess.transcript_path, encoding="utf-8") as fh:
        for raw in fh:
            line = json.loads(raw)
            if line.get("operation") == "enqueue":
                last = line["content"]
    return last


@HOOK
def test_absorbing_another_message_leaves_this_one_queued(
        wired, run_async, send_text, pickup, reactions, live):
    """An absorption names its own message; a different one Claude still
    holds keeps its evidence."""
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _queue_m2(bot, sess, run_async, send_text, pickup)
    _op(sess, "enqueue", "typed in the terminal")
    _op(sess, "remove", "typed in the terminal", "absorbed_mid_turn")
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.dropped == 1
    assert keys == ["Escape", "KillLine"]


# ── aipager's own held messages ─────────────────────────────────────────────

def _hold_m3(bot, sess, run_async, send_text):
    status = sess.status
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    run_async(send_text(bot, "held", 3))
    assert len(sess.pending_queue) == 1, "M3 must be held"
    sess.pending_permission = None
    sess.status = status


@HOOK
def test_held_only_is_cleared_without_touching_claude(
        wired, run_async, mk_update, send_text, pickup, reactions, live):
    bot, sess, _inj, keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=live)
    _hold_m3(bot, sess, run_async, send_text)
    update = mk_update("/clearqueue", message_id=41, chat_id=CHAT_ID)
    run_async(bot._handle_clearqueue_cmd(update, None))
    assert keys == []
    assert sess.pending_queue == []
    assert "Cleared 1 queued message " in (
        update.message.reply_text.await_args.args[0])
    assert reactions(bot).get(3) == [EYES, SHRUG]
    assert reactions(bot).get(7) == [OK]


# ── the idle command's note does not linger ─────────────────────────────────

def _notes(sess, **kw):
    return [n.get("msg_id") for n in ps.list_outstanding_notes(sess.name, **kw)]


def test_an_idle_command_note_expires_once_it_has_run(
        wired, run_async, mk_update):
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    later = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    expired: list = []
    assert _notes(sess, now=later, expired_out=expired) == []
    assert expired == [], "a command that ran is not an overdue message"
    assert _notes(sess) == [], "and it is gone from disk"


def test_an_idle_command_note_survives_its_grace_for_a_prompt_hook(
        wired, run_async, mk_update):
    """A prompt-type command fires UserPromptSubmit a moment after the
    Enter: its note must still be there for that pick-up to match."""
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    soon = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS - 2
    assert _notes(sess, now=soon) == [7]


def test_an_expired_command_note_no_longer_blocks_the_next_pickup(
        wired, run_async, mk_update, send_text):
    """The pick-up matches a prefix run of the oldest notes: a lingering
    command note at the head stopped every later message matching."""
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    run_async(send_text(bot, "first", 1))
    body = _body_of(sess, 1)
    later = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    outstanding = ps.list_outstanding_notes(sess.name, now=later)
    matched = ps.match_notes_prefix_run(outstanding, body)
    assert [n.get("msg_id") for n in matched] == [1]


def test_a_busy_command_note_is_left_for_its_turn_end(
        wired, run_async, mk_update, send_text, pickup):
    """A command tapped while a turn runs is queued in Claude, not run:
    its note stays outstanding past the grace."""
    bot, sess, _inj, _keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=True)
    update = mk_update("Cmd", message_id=9, chat_id=CHAT_ID)
    run_async(bot._send_command(update, "/plan"))
    later = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    assert _notes(sess, now=later) == [9]


def test_a_turn_end_expires_the_commands_it_ran_and_nothing_else(
        wired, run_async, mk_update, send_text, pickup, reactions):
    bot, sess, _inj, _keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=True)
    run_async(bot._send_command(
        mk_update("Cmd", message_id=9, chat_id=CHAT_ID), "/plan"))
    run_async(send_text(bot, "plain text", 2))   # no pick-up yet
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "done", "raw_md": "done",
    }))
    assert reactions(bot).get(9) == [EYES, OK]
    later = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    assert _notes(sess, now=later) == [2], "the plain message is untouched"


def test_marking_a_note_ran_touches_only_the_named_message(
        wired, run_async, send_text):
    bot, sess, _inj, _keys = wired
    run_async(send_text(bot, "plain", 1))
    ps.write_note(sess.name, None, None, None, msg_id=1, chat_id=999,
                  sender_key=(1, 1), body="/model x", raw_text="/model x")
    ps.mark_command_notes_ran(sess.name, msg_id=1, chat_id=CHAT_ID)
    later = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    left = ps.list_outstanding_notes(sess.name, now=later)
    assert sorted((n["msg_id"], n["chat_id"]) for n in left) == sorted([
        (1, 999), (1, CHAT_ID)]), "not a command, or another chat's: kept"


def _dir_entries(sess):
    d = ps.notes_dir(sess.name)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_a_consumed_note_takes_its_ran_mark_with_it(
        wired, run_async, mk_update):
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    assert any(n.endswith(".ran") for n in _dir_entries(sess))
    ps.delete_notes(sess.name, ps.list_outstanding_notes(sess.name))
    assert _dir_entries(sess) == []


def test_a_ran_mark_whose_note_is_gone_is_swept(wired, run_async, mk_update):
    """A pick-up that deleted the note file on its own leaves the mark
    behind; the next listing removes it."""
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    for name in _dir_entries(sess):
        if name.endswith(".json"):
            (ps.notes_dir(sess.name) / name).unlink()
    assert ps.list_outstanding_notes(sess.name) == []
    assert _dir_entries(sess) == []


def test_marking_again_keeps_the_first_mark(wired, run_async, mk_update):
    bot, sess, _inj, _keys = wired
    _idle_model(bot, sess, run_async, mk_update)
    later = time.time() + 1000
    assert ps.mark_command_notes_ran(
        sess.name, msg_id=7, chat_id=CHAT_ID, now=later) == 0
    gone_at = time.time() + ps.RAN_COMMAND_NOTE_GRACE_SECONDS + 1
    assert _notes(sess, now=gone_at) == []


def test_stop_never_shrugs_a_message_claude_already_absorbed(
        wired, run_async, send_text, pickup, reactions):
    """Its note is still outstanding (no pick-up named it, and without the
    MessageDisplay hook no scan has read the absorption), but the
    transcript says Claude took it: /stop neither wipes nor 🤷s it."""
    bot, sess, _inj, keys = wired
    _turn_from_idle(bot, sess, run_async, send_text, pickup, live=False)
    run_async(send_text(bot, "second", 2))
    body = _body_of(sess, 2)
    _op(sess, "enqueue", body)
    _op(sess, "remove", body, "absorbed_mid_turn")
    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.dropped == 0
    assert keys == ["Escape", "Escape"]
    assert reactions(bot).get(2) == [EYES]
