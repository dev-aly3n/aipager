"""design.md success criterion: "/clearqueue never sends a second,
interrupting Escape."

entrypoints.md: "bot._handle_clearqueue_cmd(update, ctx) — also
discards what Claude is holding, best-effort, without interrupting the
running turn. Combined count. 'Nothing to clear' sends no keys to the
pty." And under Keystrokes: "/clearqueue sends ["Escape", <kill-line>]
only when there is something to discard, and never a second Escape."
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"


def _in_state(bot, sess, status, *, permission=None):
    bot.registry.transition(NAME, status)
    sess.pending_permission = permission


def _update(mk_update, text, message_id, user_id=12345, chat_id=CHAT_ID):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=chat_id)


async def _send_text(bot, update):
    await bot._handle_message(update, MagicMock())


def _clearqueue_update(mk_update, message_id=500):
    return mk_update("/clearqueue", message_id=message_id, chat_id=CHAT_ID)


# ---- true no-op when there is nothing to clear ---------------------------

def test_clearqueue_sends_no_keys_at_all_when_nothing_to_clear(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(bot._handle_clearqueue_cmd(_clearqueue_update(mk_update), MagicMock()))

    assert keys == [], (
        f"/clearqueue wrote to the pty with nothing to clear: {keys}")


def test_clearqueue_reply_text_says_nothing_to_clear(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    update = _clearqueue_update(mk_update)

    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "Nothing to clear" in text


# ---- never a second, interrupting Escape ---------------------------------

def test_clearqueue_sends_exactly_one_escape_when_something_outstanding(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "a", 1)))

    run_async(bot._handle_clearqueue_cmd(_clearqueue_update(mk_update), MagicMock()))

    assert keys.count("Escape") == 1, (
        f"/clearqueue sent more than one Escape (would interrupt the "
        f"running turn): {keys}")
    assert keys[0] == "Escape"
    assert len(keys) == 2, f"expected exactly [Escape, kill-line], got {keys}"
    assert keys[1] != "Escape"


def test_clearqueue_never_sends_two_escapes_even_with_queue_and_notes(
        wired, mk_update, run_async):
    """Combined scenario: pending_queue AND an outstanding note both
    present — still exactly one Escape."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, user_id=111)))
    run_async(_send_text(bot, _update(mk_update, "from B", 2, user_id=222)))
    assert len(sess.pending_queue) == 1

    run_async(bot._handle_clearqueue_cmd(_clearqueue_update(mk_update), MagicMock()))

    assert keys.count("Escape") == 1, f"more than one Escape sent: {keys}"


# ---- combined count and does-not-interrupt-the-turn ----------------------

def test_clearqueue_combined_count_includes_notes(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "a", 1)))
    run_async(_send_text(bot, _update(mk_update, "b", 2)))
    update = _clearqueue_update(mk_update)

    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "2" in text, f"combined count not reflected in reply: {text!r}"


def test_clearqueue_does_not_change_session_status(wired, mk_update, run_async):
    """Contrast with Stop, which transitions the session to IDLE:
    /clearqueue must not touch the running turn at all."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "a", 1)))

    run_async(bot._handle_clearqueue_cmd(_clearqueue_update(mk_update), MagicMock()))

    assert sess.status == Status.BUSY, (
        f"/clearqueue changed session status to {sess.status}; it must "
        "never interrupt the running turn")


def test_clearqueue_clears_pending_queue(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, user_id=111)))
    run_async(_send_text(bot, _update(mk_update, "from B", 2, user_id=222)))
    assert len(sess.pending_queue) == 1

    run_async(bot._handle_clearqueue_cmd(_clearqueue_update(mk_update), MagicMock()))

    assert sess.pending_queue == []


# ---- racing a pick-up: count reflects state AT CALL TIME -----------------

def test_clearqueue_count_is_computed_fresh_not_stale_across_calls(
        wired, mk_update, run_async):
    """Error guessing (racing a pick-up, approximated black-box):
    after one /clearqueue call empties everything, a single freshly
    sent message must count as exactly 1 on the NEXT call — not
    accumulate stale state from before, and not silently stay at the
    previous total.

    Note: a true "hook races /clearqueue mid-consumption" scenario
    cannot be constructed at this black-box layer — note *deletion*
    happens inside notify_hook.py's ``_match_and_promote``, which runs
    in the hook's own process before the daemon ever sees a datagram
    (entrypoints.md: internal, exercised only through effects; the
    note directory's file-naming/schema, needed to delete one note by
    hand, is explicitly not part of the contract). See the tester's
    missing-coverage note for this gap."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "first", 1)))
    run_async(_send_text(bot, _update(mk_update, "second", 2)))

    first_update = _clearqueue_update(mk_update, message_id=500)
    run_async(bot._handle_clearqueue_cmd(first_update, MagicMock()))
    first_text = first_update.message.reply_text.await_args.args[0]
    assert "Cleared 2" in first_text

    run_async(_send_text(bot, _update(mk_update, "third", 3)))
    second_update = _clearqueue_update(mk_update, message_id=501)
    run_async(bot._handle_clearqueue_cmd(second_update, MagicMock()))

    second_text = second_update.message.reply_text.await_args.args[0]
    assert "Cleared 1" in second_text, (
        f"clearqueue's count was stale on the second call: {second_text!r}")
