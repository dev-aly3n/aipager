"""Black-box tests for design.md success criterion 4: the already-working
`compact_done` path (normal `PostCompact`/`SessionStart(source="compact")`
confirmation with `before_pct > 0`) must be a pure regression check --
same "Compacted: {before}% -> {after}%" text, same busy-animation resume,
no behavior change from the stack refactor.

Also confirms the text is textually distinct from criterion 6's
deadline-timeout text (used by `test_compact_timeout_sweep.py` as the
other half of that comparison).
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


def test_compact_done_normal_path_produces_compacted_text(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    text = _edit_text(bot._app.bot.edit_message_text.await_args)
    assert "Compacted: 80% → 5%" in text


def test_compact_done_normal_path_edits_the_same_busy_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs.get("message_id") == 42
    bot._app.bot.send_message.assert_not_awaited()


def test_compact_done_normal_path_resumes_busy_animation(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    bot._start_animation.assert_called_once()


def test_compact_done_after_a_push_compacting_pops_back_to_busy(mk_bot, run_async):
    """The stack-native side: a compaction that was actually pushed via
    the compacting event must be popped, leaving a plain busy top."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(msg_id=42, now=time.monotonic(), deadline_seconds=180.0)
    assert sess.stack_top_kind() == "compacting"
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    assert sess.stack_top_kind() == "busy"
    assert sess.busy_msg_id == 42
