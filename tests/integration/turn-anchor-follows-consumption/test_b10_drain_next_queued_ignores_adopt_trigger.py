"""Case table row B10 (design.md), revised by
"anchor-on-transcript-consumption": a held message released via
``_drain_next_queued`` STARTS a turn when the session is idle — it owns
the reply target and gets a card. When a turn is already running (the
finish path just started one for a message Claude had queued, R8), the
drained message is a mid-turn send instead: injected, but the running
turn's target stays and no second card is sent — the hook's submit-time
pick-up then records it as a queued target like any other.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status


def _wire(mk_bot):
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-x")
    sess.label = "x"
    sess.scope_chat_id = -2002
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._send_busy_and_animate = AsyncMock()
    return bot, sess


def test_b10_drain_at_an_idle_moment_starts_the_turn(mk_bot, run_async):
    bot, sess = _wire(mk_bot)
    sess.status = Status.IDLE
    sess.trigger_msg_id = None
    sess.queue_prompt("no it was a test", 2)

    run_async(bot._drain_next_queued(sess))

    assert sess.trigger_msg_id == 2
    assert sess.last_prompt == "no it was a test"
    assert sess.pending_queue == []
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


def test_b10_drain_while_a_turn_runs_is_a_mid_turn_send(mk_bot, run_async):
    """R8 just started the next turn for a message Claude had queued;
    the drained message must not steal that turn's target or card."""
    bot, sess = _wire(mk_bot)
    sess.status = Status.BUSY
    sess.trigger_msg_id = 1
    sess.last_prompt = "first"
    sess.queue_prompt("no it was a test", 2)

    run_async(bot._drain_next_queued(sess))

    bot._inject_prompt.assert_awaited_once()
    assert sess.trigger_msg_id == 1, "a running turn keeps its own target"
    assert sess.last_prompt == "first"
    assert sess.pending_queue == []
    bot._send_busy_and_animate.assert_not_awaited()
