"""Tests for /new name-conflict UX — Resume/Replace/Cancel inline buttons."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from aipager import telegram_bot as tb
from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mk_bot(registry):
    bot = tb.TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    return bot


def _mk_update(text):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 999
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat = MagicMock()
    update.effective_chat.id = -1001
    return update


# ---- /new collision detection -------------------------------------------

def test_new_fresh_name_takes_happy_path(monkeypatch):
    """No conflict → existing /new flow runs (no inline keyboard)."""
    registry = SessionRegistry()
    bot = _mk_bot(registry)

    async def _ok_launch(*a, **kw):
        return True, ""

    monkeypatch.setattr(tb.inject, "launch_session", _ok_launch)
    update = _mk_update("/new dev")
    _run(bot._handle_new_cmd(update, MagicMock()))

    # Old flow does TWO reply_text-equivalent calls (status_msg.edit_text
    # is on the status message, not the original). At minimum: no conflict
    # prompt with inline keyboard was sent.
    assert "claude-dev" not in bot._new_conflict_pending


def test_new_alive_collision_prompts_buttons():
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)
    update = _mk_update("/new jim do something")
    _run(bot._handle_new_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    text = call.args[0]
    kb = call.kwargs.get("reply_markup")
    assert "already running" in text
    assert kb is not None

    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "claude-jim:new_resume" in cb_data
    assert "claude-jim:new_replace" in cb_data
    assert "claude-jim:new_cancel" in cb_data

    # Prompt + skip_perms stashed for the callback to consume
    assert bot._new_conflict_pending["claude-jim"]["prompt"] == "do something"
    assert bot._new_conflict_pending["claude-jim"]["skip_perms"] is False


def test_new_gone_with_resumable_id_prompts_buttons():
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-OLD"
    sess.gone_at = time.time() - 60
    sess.last_assistant_preview = "I refactored auth."
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)
    update = _mk_update("/new jim")
    _run(bot._handle_new_cmd(update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "previously used" in text
    assert "I refactored auth." in text


def test_new_gone_without_resumable_id_falls_through(monkeypatch):
    """A GONE entry with no claude_session_id is not resumable — fall through
    to a fresh launch without showing the conflict prompt."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = time.time() - 60
    # claude_session_id deliberately empty
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)

    async def _ok_launch(*a, **kw):
        return True, ""

    monkeypatch.setattr(tb.inject, "launch_session", _ok_launch)
    update = _mk_update("/new jim")
    _run(bot._handle_new_cmd(update, MagicMock()))

    assert "claude-jim" not in bot._new_conflict_pending


def test_new_skip_perms_flag_carried_through():
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)
    update = _mk_update("/new !jim")
    _run(bot._handle_new_cmd(update, MagicMock()))

    assert bot._new_conflict_pending["claude-jim"]["skip_perms"] is True
