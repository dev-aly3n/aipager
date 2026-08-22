"""design.md success criterion: "Three messages sent in quick succession
while BUSY all reach the pty immediately; none sit in pending_queue."

Black-box: drive ``bot._handle_message`` (and, for equivalence, the
already-immediate ``_direct_send``/``_send_command`` paths) with
fabricated Updates, and assert on what reached the mocked
``inject.send_text_and_enter`` pty boundary and on
``TrackedSession.pending_queue``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"


def _in_state(bot, sess, status):
    bot.registry.transition(NAME, status)
    sess.pending_permission = None


def _update(mk_update, text, message_id=9, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=CHAT_ID)


async def _send_text(bot, update):
    await bot._handle_message(update, MagicMock())


# ---- boundary-value analysis over the count of quick-succession sends ----

def test_a_single_message_while_busy_injects_immediately(wired, mk_update, run_async):
    """n=1: the floor of the "quick succession" boundary."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)

    run_async(_send_text(bot, _update(mk_update, "only one")))

    assert injected == ["only one"]
    assert sess.pending_queue == []


def test_three_messages_from_the_same_sender_while_busy_all_reach_the_pty(
        wired, mk_update, run_async):
    """The literal success criterion: three in quick succession, none
    held."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)

    for i, text in enumerate(["first", "second", "third"]):
        run_async(_send_text(bot, _update(mk_update, text, message_id=10 + i)))

    assert injected == ["first", "second", "third"], (
        f"not every quick-succession message reached the pty: {injected}")
    assert sess.pending_queue == [], (
        "a BUSY-status message was queued instead of injected: "
        f"{sess.pending_queue}")


def test_a_run_of_five_messages_while_busy_all_inject(wired, mk_update, run_async):
    """Beyond the literal "three" example: a longer run must behave the
    same way — this is not a special-cased count of exactly three."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)

    texts = [f"msg-{i}" for i in range(5)]
    for i, text in enumerate(texts):
        run_async(_send_text(bot, _update(mk_update, text, message_id=20 + i)))

    assert injected == texts
    assert sess.pending_queue == []


def test_busy_no_longer_a_hold_condition_for_a_fresh_session(
        wired, mk_update, run_async):
    """Equivalence check against the OLD behaviour: BUSY alone (no
    dialog open, no mixed sender) must never hold a message under the
    new contract, for a session with no history at all."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)

    run_async(_send_text(bot, _update(mk_update, "hello")))

    assert len(injected) == 1
    assert sess.pending_queue == []


@pytest.mark.parametrize("status", [Status.BUSY, Status.IDLE])
def test_same_sender_run_injects_regardless_of_busy_or_idle(
        wired, mk_update, run_async, status):
    """Equivalence partitioning over Status: the injecting behaviour for
    a same-sender run must not depend on which non-blocking status the
    session is in."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, status)

    for i, text in enumerate(["a", "b"]):
        run_async(_send_text(bot, _update(mk_update, text, message_id=30 + i)))

    assert injected == ["a", "b"]
    assert sess.pending_queue == []


# ---- equivalence across the other inbound paths design.md names ---------

def test_direct_send_injects_while_busy(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    update = _update(mk_update, "direct text")

    run_async(bot._direct_send(update, "x", "direct text"))

    assert injected == ["direct text"]
    assert sess.pending_queue == []


def test_send_command_injects_while_busy(wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    update = _update(mk_update, "/model sonnet")

    run_async(bot._send_command(update, "/model sonnet"))

    assert injected == ["/model sonnet"]
    assert sess.pending_queue == []
