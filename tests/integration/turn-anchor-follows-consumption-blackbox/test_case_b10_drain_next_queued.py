"""Contract case table row B10: "mixed-sender hold (team mode) releases
M2 at an idle moment" — "assert trigger_msg_id becomes M2
unconditionally (R1's stated exception) — this path must NOT go through
_adopt_trigger."

Setup mirrors ``tests/integration/queue-handoff``'s own mixed-sender
hold tests: a second sender's message while BUSY is held (queued, not
injected) rather than noted-and-injected; ``_drain_next_queued`` (fired
as part of the turn-end/Stop handling) releases it into a fresh prompt,
starting turn 2.
"""

from __future__ import annotations

import json

from aipager.state import Status

USER_A = 12345
USER_B = 999999
M1 = 1501
M2 = 1502


def test_drained_held_message_becomes_the_target_unconditionally(
    wire_transport, rich_calls, install_session, send_update, send_text,
    hook_receiver, run_async, tmp_path,
):
    bot, injected = wire_transport
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)
    recv = hook_receiver(bot)

    run_async(send_text(bot, send_update("from A", M1, user_id=USER_A)))
    assert sess.status == Status.BUSY
    assert sess.trigger_msg_id == M1

    # A different sender's message while BUSY: held (mixed-sender hold),
    # never injected — R1 leaves the target alone for this too.
    run_async(send_text(bot, send_update("from B", M2, user_id=USER_B)))
    assert sess.pending_queue, "setup assumption broke: message wasn't held"
    assert sess.trigger_msg_id == M1

    tp = tmp_path / "turn1.jsonl"
    tp.write_bytes(b"")
    bot._app.bot.send_message.reset_mock()

    # Turn 1 ends: Stop drains the held message into a fresh prompt,
    # starting turn 2 — unconditionally moving the target to M2.
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "Stop", "session": "claude-x",
        "last_assistant_message": "done", "transcript_path": str(tp),
    }).encode()))

    assert sess.trigger_msg_id == M2, (
        "R1's stated exception: _drain_next_queued must set the target "
        "unconditionally, since releasing a held message starts a turn")


def test_drained_message_no_longer_sits_in_the_pending_queue(
    wire_transport, rich_calls, install_session, send_update, send_text,
    hook_receiver, run_async, tmp_path,
):
    bot, injected = wire_transport
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)
    recv = hook_receiver(bot)

    run_async(send_text(bot, send_update("from A", M1, user_id=USER_A)))
    run_async(send_text(bot, send_update("from B", M2, user_id=USER_B)))
    assert sess.pending_queue

    tp = tmp_path / "turn1.jsonl"
    tp.write_bytes(b"")
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "Stop", "session": "claude-x",
        "last_assistant_message": "done", "transcript_path": str(tp),
    }).encode()))

    assert sess.pending_queue == [], (
        "the drained message must have left the hold queue once released")
