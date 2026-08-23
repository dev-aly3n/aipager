"""design.md's stated risk: "a note that is never matched... does
anything leak or accumulate?" and the design's own claim: "Both
[Stop and /clearqueue] delete the session's notes directory, so
nothing lingers to restrict an unrelated future turn for the rest of
its TTL."

entrypoints.md sanctions exactly one observable here: "``/tmp/claude-
notes-<session>/``... Existence and emptiness are legitimate
observables via a monkeypatched base path... File naming and internal
schema are NOT part of this contract." ``tests/conftest.py``'s autouse
``_isolate_notes_dir`` fixture already redirects
``aipager.policy_snapshot.notes_dir`` to ``tmp_path`` for every test, so
these assertions never touch a real ``/tmp``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import policy_snapshot as ps
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


def _notes_dir_has_entries():
    d = ps.notes_dir(NAME)
    return d.exists() and any(d.iterdir())


# ---- positive control: sending genuinely populates the notes dir --------

def test_sending_a_message_populates_the_notes_dir(wired, mk_update, run_async):
    """Without this, an "empty after Stop" assertion elsewhere in this
    file would be vacuous — prove the directory is non-empty first."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "hello", 1)))

    assert _notes_dir_has_entries(), (
        "the notes directory has no entries after a send — either "
        "notes_dir() is monkeypatched incorrectly for this test, or no "
        "note was ever written")


# ---- Stop clears the notes directory -------------------------------------

def test_stop_empties_the_notes_dir_when_notes_were_outstanding(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(_send_text(bot, _update(mk_update, "a", 1)))
    run_async(_send_text(bot, _update(mk_update, "b", 2)))
    assert _notes_dir_has_entries(), "setup assumption broke: no notes written"

    run_async(bot._stop_session(sess))

    assert not _notes_dir_has_entries(), (
        "notes lingered in the directory after Stop — a future turn "
        "would be restricted by a message that already got its "
        "acknowledged discard")


def test_stop_with_nothing_outstanding_leaves_the_notes_dir_empty(
        wired, run_async):
    """Boundary n=0: Stop must not somehow conjure entries out of thin air."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    run_async(bot._stop_session(sess))

    assert not _notes_dir_has_entries()


# ---- /clearqueue clears the notes directory -------------------------------

def test_clearqueue_empties_the_notes_dir_when_notes_were_outstanding(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    run_async(_send_text(bot, _update(mk_update, "a", 1)))
    assert _notes_dir_has_entries(), "setup assumption broke: no notes written"

    update = mk_update("/clearqueue", message_id=500, chat_id=CHAT_ID)
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))

    assert not _notes_dir_has_entries(), (
        "notes lingered in the directory after /clearqueue")


def test_a_never_matched_note_does_not_leak_past_the_next_clearqueue(
        wired, mk_update, run_async):
    """The design's own risk framing, phrased directly: a note that is
    never matched (no queue_pickup ever names it) must still be
    reachable by the cleanup paths, not stuck forever."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    # Sent, never confirmed picked up by anything.
    run_async(_send_text(bot, _update(mk_update, "orphan", 1)))
    assert _notes_dir_has_entries()

    update = mk_update("/clearqueue", message_id=500, chat_id=CHAT_ID)
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))

    assert not _notes_dir_has_entries(), (
        "an unmatched note was not reachable by /clearqueue's cleanup")
