"""design.md success criterion: "Stop's acknowledgement reports a count
including outstanding notes; /clearqueue never sends a second,
interrupting Escape."

entrypoints.md: ``bot._stop_session(sess, update=None, query=None) ->
StopOutcome``. ``StopOutcome.dropped`` now counts messages discarded
from Claude's own not-yet-picked-up queue IN ADDITION TO
``pending_queue``. Keystrokes: Stop still sends exactly
``["Escape", "Escape"]`` for the interrupt, now followed by one more
``"Escape"`` and the kill-line key when notes were outstanding.

The combined-count precondition is built purely through the exported
surface: sending messages via ``_handle_message`` writes an outstanding
note as a side effect (per entrypoints.md, every send — injected or
held — writes one), so no internal ``policy_snapshot`` import is
needed to set up "Claude is holding N messages".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


def _stub_animation(bot):
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()


# ---- StopOutcome.dropped: the combined count -----------------------------

def test_dropped_counts_outstanding_notes_not_just_pending_queue(
        wired, mk_update, run_async):
    """The direct regression this feature calls out: "today it counts
    only pending_queue, which under this change is usually empty, so
    Stop would otherwise silently under-report almost every discard."
    Three same-sender sends while BUSY all inject (per
    test_immediate_injection.py) and leave pending_queue EMPTY — so a
    naive len(pending_queue) count would report 0 here."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)

    for i, text in enumerate(["a", "b", "c"]):
        run_async(_send_text(bot, _update(mk_update, text, message_id=1 + i)))
    assert sess.pending_queue == [], "setup assumption broke: queue not empty"

    outcome = run_async(bot._stop_session(sess))

    assert outcome.dropped == 3, (
        f"dropped={outcome.dropped}, expected 3 outstanding notes even "
        "though pending_queue was empty")


def test_dropped_is_zero_when_nothing_outstanding(wired, run_async):
    """Boundary n=0: no notes, no queue."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)

    outcome = run_async(bot._stop_session(sess))

    assert outcome.dropped == 0


def test_dropped_sums_pending_queue_and_outstanding_notes(
        wired, mk_update, run_async):
    """A combined scenario built entirely through the exported surface:
    one outstanding note (User A's send) plus one held message in
    pending_queue (User B's mixed-sender hold)."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)

    run_async(_send_text(bot, _update(mk_update, "from A", 1, user_id=111)))
    run_async(_send_text(bot, _update(mk_update, "from B", 2, user_id=222)))
    assert len(sess.pending_queue) == 1, "setup assumption broke: no hold"

    outcome = run_async(bot._stop_session(sess))

    assert outcome.dropped == 2, (
        f"dropped={outcome.dropped}, expected 1 note + 1 queued = 2")


# ---- keystrokes: unchanged interrupt pair, new discard tail --------------

def test_stop_still_sends_exactly_two_escapes_with_nothing_outstanding(
        wired, run_async):
    """Must-not-regress: with nothing to discard, the interrupt sequence
    is unchanged from before this feature."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)

    run_async(bot._stop_session(sess))

    assert keys == ["Escape", "Escape"], (
        f"the plain interrupt sequence changed: {keys}")


def test_stop_appends_discard_tail_when_notes_are_outstanding(
        wired, mk_update, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)
    run_async(_send_text(bot, _update(mk_update, "a", 1)))

    run_async(bot._stop_session(sess))

    assert keys[:2] == ["Escape", "Escape"], (
        "the interrupt pair must still come first: "
        f"{keys}")
    assert len(keys) == 4, (
        f"expected interrupt pair + one Escape + kill-line, got {keys}")
    assert keys[2] == "Escape"
    assert keys[3] != "Escape", (
        f"the 4th key must be the kill-line, not another Escape: {keys}")


# ---- chat acknowledgement states the combined count ----------------------

def test_stop_acknowledgement_via_query_states_the_combined_count(
        wired, mk_update, run_async):
    """Driven via ``query=`` — entrypoints.md: "The chat acknowledgement
    states the combined count." """
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)
    for i, text in enumerate(["a", "b"]):
        run_async(_send_text(bot, _update(mk_update, text, message_id=1 + i)))

    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))
    query = MagicMock()
    query.message = MagicMock(message_id=700)
    query.edit_message_text = AsyncMock()

    outcome = run_async(bot._stop_session(sess, query=query))

    assert outcome.dropped == 2
    assert answers, "no acknowledgement text was produced"
    assert "2" in answers[-1], (
        f"acknowledgement does not mention the combined count: {answers}")


def test_stop_acknowledgement_omits_count_wording_when_nothing_dropped(
        wired, run_async):
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)

    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))
    query = MagicMock()
    query.message = MagicMock(message_id=700)
    query.edit_message_text = AsyncMock()

    outcome = run_async(bot._stop_session(sess, query=query))

    assert outcome.dropped == 0
    assert answers
    assert "discarded" not in answers[-1].lower(), (
        f"a zero-discard stop still claimed a discard: {answers}")


def test_the_stop_command_itself_states_the_combined_count_in_chat(
        wired, mk_update, run_async):
    """entrypoints.md, under bot._stop_session: "The chat acknowledgement
    states the combined count." The /stop command (``_handle_stop_cmd``,
    the ordinary way an operator invokes Stop in chat — not a button
    tap) drives ``_stop_session`` via ``update=``, not ``query=``. That
    text-visible acknowledgement must state the combined count too, not
    only the button-tap ack this file's ``..._via_query_...`` test
    covers."""
    bot, sess, injected, keys = wired
    _in_state(bot, sess, Status.BUSY)
    _stub_animation(bot)
    for i, text in enumerate(["a", "b"]):
        run_async(_send_text(bot, _update(mk_update, text, message_id=1 + i)))

    stop_update = mk_update("/stop", message_id=999, chat_id=CHAT_ID)
    run_async(bot._handle_stop_cmd(stop_update, MagicMock()))

    reply_texts = [c.args[0] for c in
                    stop_update.message.reply_text.await_args_list if c.args]
    assert any("2" in t for t in reply_texts), (
        "the /stop command produced no chat-visible acknowledgement "
        f"stating the combined discard count; reply_text calls={reply_texts}")
