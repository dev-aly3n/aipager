"""design.md success criteria:

- "A message sent while INTERACTIVE is still held, unchanged." — the
  landed safety fix from f632617 must survive this change (intent.md:
  "Sending immediately must NOT mean sending into an open dialog").
- "A message from a different Telegram user than the one already
  outstanding is held." — the new mixed-sender hold condition.

Black-box setup for "an outstanding note from a different sender":
per entrypoints.md, a note is written for EVERY message on send
(injected or held) — so the precondition is built purely by sending a
first message through the real ``_handle_message`` path (which writes
its own note as a side effect of injecting), never by importing
``policy_snapshot.write_note`` directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"

USER_A = 12345
USER_B = 999999
USER_C = 555555


def _in_state(bot, sess, status, *, permission=None):
    bot.registry.transition(NAME, status)
    sess.pending_permission = permission


def _update(mk_update, text, message_id, user_id, chat_id=CHAT_ID):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=chat_id)


async def _send_text(bot, update):
    await bot._handle_message(update, MagicMock())


# ---- INTERACTIVE: must-not-regress (landed in f632617) ------------------

def test_message_during_open_dialog_is_not_injected(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.INTERACTIVE,
              permission={"tool_summary": "Bash: rm -rf /important"})

    run_async(_send_text(bot, _update(mk_update, "please stop", 9, USER_A)))

    assert injected == [], "a message was typed into an open permission dialog"


def test_message_during_open_dialog_is_held_in_pending_queue(
        wired, mk_update, run_async):
    """Held, not dropped — the message must survive to be delivered
    later."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.INTERACTIVE,
              permission={"tool_summary": "Bash: rm -rf /important"})

    run_async(_send_text(bot, _update(mk_update, "please stop", 9, USER_A)))

    assert len(sess.pending_queue) == 1


def test_an_idle_session_with_no_dialog_still_injects(wired, mk_update, run_async):
    """Positive control for the INTERACTIVE tests above: prove the fixture
    actually injects in the ordinary case, so a "held" result above is
    not simply because nothing ever injects in this harness."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "please stop", 9, USER_A)))

    assert injected == ["please stop"]
    assert sess.pending_queue == []


# ---- mixed sender: new hold condition ------------------------------------

def test_a_different_sender_is_held_while_a_note_is_outstanding(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    # User A's message injects and leaves an outstanding note (nothing
    # has confirmed pick-up yet).
    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    assert injected == ["from A"]

    # User B's message must be held, not injected and not merged into A's.
    run_async(_send_text(bot, _update(mk_update, "from B", 2, USER_B)))

    assert injected == ["from A"], (
        "a different sender's message was injected alongside an "
        f"outstanding note: {injected}")
    assert len(sess.pending_queue) == 1, (
        "the mixed-sender message was dropped instead of held")


def test_the_held_mixed_sender_message_is_not_merged_into_the_pty_text(
        wired, mk_update, run_async):
    """Explicitly rules out silent merging: B's text must never appear
    concatenated onto A's injected body."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    run_async(_send_text(bot, _update(mk_update, "from B", 2, USER_B)))

    assert not any("from B" in body for body in injected), (
        f"B's text leaked into the pty stream: {injected}")


def test_a_second_message_from_the_same_sender_is_not_held(
        wired, mk_update, run_async):
    """Positive control / ordering check: the SAME sender's own
    outstanding note must not trip the mixed-sender hold — this is what
    lets a same-sender run (test_immediate_injection.py) work at all."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    run_async(_send_text(bot, _update(mk_update, "from A first", 1, USER_A)))
    run_async(_send_text(bot, _update(mk_update, "from A second", 2, USER_A)))

    assert injected == ["from A first", "from A second"]
    assert sess.pending_queue == []


def test_a_third_different_sender_is_also_held(wired, mk_update, run_async):
    """Equivalence partitioning over "different": not just a single
    alternate identity — ANY sender other than the outstanding one."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    run_async(_send_text(bot, _update(mk_update, "from C", 3, USER_C)))

    assert injected == ["from A"]
    assert len(sess.pending_queue) == 1


def test_zero_outstanding_notes_the_first_message_from_anyone_injects(
        wired, mk_update, run_async):
    """Boundary n=0: a fresh session with no outstanding notes at all
    must not hold the very first message, regardless of sender."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "first ever", 1, USER_B)))

    assert injected == ["first ever"]
    assert sess.pending_queue == []


def test_mixed_sender_hold_also_applies_while_busy(wired, mk_update, run_async):
    """The mixed-sender hold is independent of Status — BUSY no longer
    holds on its own, but a mixed sender must still hold even while
    BUSY."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    run_async(_send_text(bot, _update(mk_update, "from B", 2, USER_B)))

    assert injected == ["from A"]
    assert len(sess.pending_queue) == 1
