"""Case table row B10 (design.md): a mixed-sender hold releases a
queued message via ``_drain_next_queued`` at an idle moment. R1's own
stated exception — this path starts a turn by definition and must keep
setting the target UNCONDITIONALLY, never through ``_adopt_trigger``
(which would refuse while BUSY)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status


def test_b10_drain_next_queued_sets_target_even_while_busy(mk_bot, run_async):
    """R1's exception, proven by contradiction: `_adopt_trigger` would
    refuse to move the target while BUSY. `_drain_next_queued` must move
    it anyway — if it were ever routed through `_adopt_trigger`, this
    test would catch the regression immediately (trigger stays at the
    pre-drain value instead of becoming M2)."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-x")
    sess.label = "x"
    sess.scope_chat_id = -2002
    # Deliberately BUSY at drain time — a real mixed-sender hold drains
    # once the session goes idle, but the CONTRACT under test is that
    # `_drain_next_queued` never consults status via `_adopt_trigger` at
    # all, so proving it moves the target even while (artificially) BUSY
    # is the sharpest test of "this path does NOT go through
    # _adopt_trigger" — a status-gated implementation would fail this.
    sess.status = Status.BUSY
    sess.trigger_msg_id = 1  # some earlier target
    sess.queue_prompt("no it was a test", 2)

    bot._inject_prompt = AsyncMock(return_value=True)
    bot._send_busy_and_animate = AsyncMock()

    run_async(bot._drain_next_queued(sess))

    assert sess.trigger_msg_id == 2, (
        "R1's exception: _drain_next_queued must set the target "
        "unconditionally, never through _adopt_trigger"
    )
    assert sess.last_prompt == "no it was a test"
    assert sess.pending_queue == []
