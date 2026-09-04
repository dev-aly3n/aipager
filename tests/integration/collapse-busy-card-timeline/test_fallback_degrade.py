"""design.md's defensive `RichMessageFallbackRequired` arm in
`AnimationMixin._edit_busy_rich`: a rich-send failure that raises
`RichMessageFallbackRequired` on the busy-card edit path degrades to a
plain-text edit containing the `<details>` block's summary line but no
raw `<details>`/`<summary>` tags — currently unreachable in production
(`edit_message_text_rich` structurally cannot raise it today, per
research.md gotcha ~53/54) but pinned defensively with a monkeypatch
raise, per design.md's own framing.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from aipager.bot.rich_message import RichMessageFallbackRequired
from aipager.state import Status, TrackedSession


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    return sess


def test_fallback_required_edit_carries_summary_but_no_details_markup(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _sess()
    # Force a <details> block to exist in the rendered markdown.
    sess.tool_history = [(f"Bash: t-{i} " + "x" * 200, True) for i in range(60)]

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("transport changed")),
    )
    bot._app.bot.edit_message_text = AsyncMock()

    result = run_async(bot._edit_busy_rich(sess, "Working"))

    assert result is True
    bot._app.bot.edit_message_text.assert_awaited_once()
    call = bot._app.bot.edit_message_text.await_args
    text = call.args[0]
    assert "<details" not in text
    assert "<summary" not in text
    assert "</details>" not in text
    assert "earlier step" in text  # the summary line survives, plain text
    assert call.kwargs.get("parse_mode") is None


def test_fallback_required_preserves_the_stop_keyboard_reply_markup(
    mk_bot, run_async, monkeypatch,
):
    """The busy card's Stop button must ride along on the degraded
    plain-text edit exactly as it does on the rich one."""
    bot = mk_bot()
    sess = _sess()
    sess.tool_history = [("Bash: ls", True)]

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("transport changed")),
    )
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._edit_busy_rich(sess, "Working"))

    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs.get("reply_markup") is not None
