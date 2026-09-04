"""design.md success criteria: `sess.last_card_truncated` is True only
when content was genuinely, irrecoverably dropped from the card (the
char-budget backstop tripped) — and the existing `.txt`-attachment
trigger (`notify.py:1637`, UNCHANGED code) fires exactly on that
narrower condition, carrying the complete, uncollapsed play-by-play
regardless of what the card itself had to hide or collapse.

Exercised end-to-end through `bot.notify(sess, "idle_prompt", ...)`
(layout="card"), mirroring `tests/integration/stream_busy_message/
test_layout_modes.py`'s own wiring pattern.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager import preferences as prefs
from aipager.state import Status, TrackedSession


def _sess(label="jim", *, scope_chat_id=555):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.busy_started_at = time.monotonic() - 30
    sess.scope_chat_id = scope_chat_id
    return sess


def _wire_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def test_extremely_long_turn_fires_the_txt_attachment_with_complete_content(
    mk_bot, run_async, monkeypatch,
):
    bot = _wire_bot(mk_bot)
    captured = {}

    async def _capture_document(_chat_id, *, document, filename, reply_to_message_id=None):
        captured["bytes"] = document.read()
        captured["filename"] = filename
    bot._app.bot.send_document = AsyncMock(side_effect=_capture_document)

    monkeypatch.setattr("aipager.bot.rich_message._post", AsyncMock(
        return_value={"ok": True, "result": {"message_id": 999}},
    ))

    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    sess.tool_history = [(f"Bash: step-{i} " + "z" * 300, True) for i in range(500)]
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.last_card_truncated is True
    bot._app.bot.send_document.assert_awaited_once()
    log_text = captured["bytes"].decode("utf-8")
    # The oldest row is certainly gone from the CARD (500 fat rows blow
    # the char budget many times over) but must survive intact in the
    # complete play-by-play attachment.
    assert "step-0 " in log_text
    assert "step-499 " in log_text
    assert "complete play-by-play" in log_text


def test_short_turn_does_not_fire_the_txt_attachment(mk_bot, run_async, monkeypatch):
    bot = _wire_bot(mk_bot)
    monkeypatch.setattr("aipager.bot.rich_message._post", AsyncMock(
        return_value={"ok": True, "result": {"message_id": 999}},
    ))

    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    sess.tool_history = [("Bash: ls", True)]
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.last_card_truncated is False
    bot._app.bot.send_document.assert_not_awaited()
