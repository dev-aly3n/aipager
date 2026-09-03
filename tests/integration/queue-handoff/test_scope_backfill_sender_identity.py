"""intent.md ("Stop stale queue-handoff notes from silently holding
every later message from the same human") requirement 1: sender
identity must be stable across a multi-scope backfill.

Observed live: ``state.py``'s multi-scope backfill stamps a session's
``scope_chat_id`` from 0 (not yet stamped) to a real chat id sometime
after some notes were already written with scope 0. Because
``transport._sender_key`` embeds the scope at write time, and the old
``mixed_sender_note_outstanding`` compared sender keys with plain
``!=``, EVERY later message from the very same human then looked like
"a different sender" — held forever (bounded only by the 24h note
TTL). ``transport._same_sender`` fixes this by treating the human's
identity as the ``driver_user_id`` alone whenever either side's scope
component is 0 (unstamped), while still requiring an exact
``driver_user_id`` match and still treating an unknown user (0/None) as
never matching anything.

Same black-box setup convention as ``test_hold_conditions.py``: a note
is written for every message on send (injected or held), so the
precondition is built purely by sending real messages through
``_handle_message`` — never by importing ``policy_snapshot.write_note``
directly. The one exception is the TTL test, which must backdate a
note's ``queued_at`` to simulate the passage of time; it does so by
editing the note file ``_handle_message`` itself already wrote, the
same pattern ``tests/test_policy_snapshot.py``'s TTL tests use.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from aipager import policy_snapshot as ps
from aipager.state import MIXED_SENDER_HOLD_WINDOW_SECONDS, Status

CHAT_ID = -1001
NAME = "claude-x"
SCOPE_CHAT = 256113222  # a stamped scope id, distinct from CHAT_ID

USER_A = 12345
USER_B = 999999


def _in_state(bot, sess, status):
    bot.registry.transition(NAME, status)


def _update(mk_update, text, message_id, user_id, chat_id=CHAT_ID):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=chat_id)


async def _send_text(bot, update):
    await bot._handle_message(update, MagicMock())


def _only_note_path():
    entries = [p for p in ps.notes_dir(NAME).iterdir() if p.suffix == ".json"]
    assert len(entries) == 1, f"expected exactly one note, found {entries}"
    return entries[0]


# ---- requirement 1: note scope 0, live session now stamped ---------------

def test_note_written_unstamped_then_stamped_session_same_user_not_held(
    wired, mk_update, run_async,
):
    """The exact live defect: a note written while scope_chat_id was 0
    must not conflict with the SAME human once the session gets
    stamped with a real scope afterward."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    assert sess.scope_chat_id == 0, "setup assumption: session starts unstamped"

    run_async(_send_text(bot, _update(mk_update, "from A first", 1, USER_A)))
    assert injected == ["from A first"]

    # Simulate the multi-scope backfill (state.py) stamping this session
    # sometime between the two messages.
    sess.scope_chat_id = SCOPE_CHAT

    run_async(_send_text(bot, _update(mk_update, "from A second", 2, USER_A)))

    assert injected == ["from A first", "from A second"], (
        "the same human's second message was held after the scope got "
        f"stamped: injected={injected}")
    assert sess.pending_queue == []


def test_note_written_stamped_then_unstamped_session_same_user_not_held(
    wired, mk_update, run_async,
):
    """The reverse direction: a note written while scope_chat_id was
    already a real value must not conflict with a live session that
    (for whatever reason) now reads as unstamped."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    sess.scope_chat_id = SCOPE_CHAT

    run_async(_send_text(bot, _update(mk_update, "from A first", 1, USER_A)))
    assert injected == ["from A first"]

    sess.scope_chat_id = 0  # now reads as unstamped

    run_async(_send_text(bot, _update(mk_update, "from A second", 2, USER_A)))

    assert injected == ["from A first", "from A second"], (
        f"the same human's second message was held: injected={injected}")
    assert sess.pending_queue == []


def test_two_different_users_still_held_across_a_scope_change(
    wired, mk_update, run_async,
):
    """Safety property preserved: the scope-blind identity fix must
    never let two DIFFERENT humans' messages merge, backfill or not."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)
    assert sess.scope_chat_id == 0

    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    sess.scope_chat_id = SCOPE_CHAT  # backfill happens in between

    run_async(_send_text(bot, _update(mk_update, "from B", 2, USER_B)))

    assert injected == ["from A"], (
        f"a different human's message was injected alongside an "
        f"outstanding note: {injected}")
    assert len(sess.pending_queue) == 1


def test_unknown_sender_note_still_holds_a_known_users_message(
    wired, mk_update, run_async,
):
    """A note whose driver_user_id could not be determined (0/None) must
    be treated conservatively — it never counts as "the same human" as
    anyone, including a real, later, identified sender."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    # effective_user.id=None -> driver_id_from_update returns None ->
    # sender_key folds it to 0 ("unknown human").
    run_async(_send_text(bot, _update(mk_update, "from unknown", 1, None)))
    assert injected == ["from unknown"], "setup assumption broke"

    run_async(_send_text(bot, _update(mk_update, "from A", 2, USER_A)))

    assert injected == ["from unknown"], (
        f"a known sender's message was injected despite an outstanding "
        f"unknown-sender note: {injected}")
    assert len(sess.pending_queue) == 1


def test_two_unknown_senders_are_not_treated_as_the_same_human(
    wired, mk_update, run_async,
):
    """The narrower case the previous test can't reach: an outstanding
    note from an unidentified sender (user id 0/None) must not match
    ANOTHER unidentified sender either — "unknown" never equals
    "unknown", only ever "different"."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "first unknown", 1, None)))
    assert injected == ["first unknown"], "setup assumption broke"

    run_async(_send_text(bot, _update(mk_update, "second unknown", 2, None)))

    assert injected == ["first unknown"], (
        f"a second unidentified sender was injected alongside an "
        f"outstanding unknown-sender note: {injected}")
    assert len(sess.pending_queue) == 1


# ---- requirement 3: the hold only consults recent notes -------------------

def test_a_note_older_than_the_hold_window_no_longer_holds(
    wired, mk_update, run_async,
):
    """MIXED_SENDER_HOLD_WINDOW_SECONDS bounds the hold independently of
    the note's own (much longer) TTL: once a note is old enough, it
    stops blocking a different-looking sender's message, even though
    it may still be "outstanding" for merge/depth purposes."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "from A", 1, USER_A)))
    assert injected == ["from A"], "setup assumption broke"

    note_path = _only_note_path()
    data = json.loads(note_path.read_text())
    data["queued_at"] = (
        __import__("time").time() - MIXED_SENDER_HOLD_WINDOW_SECONDS - 1
    )
    note_path.write_text(json.dumps(data))

    run_async(_send_text(bot, _update(mk_update, "from B", 2, USER_B)))

    assert injected == ["from A", "from B"], (
        f"a stale note past the hold window still blocked a different "
        f"sender: injected={injected}")
    assert sess.pending_queue == []
