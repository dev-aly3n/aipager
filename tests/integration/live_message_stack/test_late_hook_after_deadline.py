"""Black-box test for design.md success criterion 18 -- the required
late-hook-ordering case (explicitly called out by the orchestrator as
likely-uncovered, added after the developer began):

A `compacting` entry's deadline fires FIRST (producing the honest
"didn't confirm completion" text). A REAL `SessionStart(source="compact")`
confirmation then arrives SECOND. The final text must still become
"Compacted: {before}% -> {after}%", neither call may raise, and the
second `pop_compacting()` (fired internally by the late `compact_done`)
must be a silent no-op on the already-popped stack.

Per design.md's Decision 3 ("An early timeout is self-correcting..."):
this specific scenario assumes the turn is still genuinely running
(`Status.BUSY`, a busy layer beneath the compaction) -- the case a
too-short deadline is explicitly designed to self-correct.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


def _sess(label="jim", *, busy_msg_id=42):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    return s


def _edit_text(call) -> str:
    if "text" in call.kwargs:
        return call.kwargs["text"]
    return call.args[0]


def test_late_compact_done_overwrites_timeout_text_with_success(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()

    # Deadline fires first.
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    # Real hook arrives second.
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))

    final_text = _edit_text(bot._app.bot.edit_message_text.await_args)
    assert "Compacted: 80% → 5%" in final_text


def test_late_compact_done_neither_call_raises(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))
    # Reaching this line without an exception IS the assertion.


def test_late_compact_done_edits_the_same_physical_message(mk_bot, run_async):
    """Never a second send -- both resolutions target the one message
    the user already sees, per the 'zero extra Telegram calls' design."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 200.0}))
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))

    for call in bot._app.bot.edit_message_text.await_args_list:
        assert call.kwargs.get("message_id") == 42
    bot._app.bot.send_message.assert_not_awaited()


def test_late_compact_done_pop_compacting_already_popped_is_a_silent_noop():
    """Directly exercises the state-level contract the ordering relies
    on: a second pop_compacting() call after the timeout already popped
    the entry finds nothing and returns None, never raising."""
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic() - 200, deadline_seconds=180.0)
    first = sess.pop_compacting()  # models the timeout handler's own pop
    assert first is not None
    second = sess.pop_compacting()  # models compact_done's own pop, arriving late
    assert second is None
