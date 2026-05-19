"""Tests for /new name-conflict UX — Resume/Replace/Cancel inline buttons."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from aipager import telegram_bot as tb
from aipager.state import SessionRegistry, Status, TrackedSession


# ---- /new collision detection -------------------------------------------

def test_new_fresh_name_takes_happy_path(monkeypatch, mk_bot, mk_update, run_async):
    """No conflict → existing /new flow runs (no inline keyboard)."""
    registry = SessionRegistry()
    bot = mk_bot(registry)

    async def _ok_launch(*a, **kw):
        return True, ""

    monkeypatch.setattr(tb.inject, "launch_session", _ok_launch)
    update = mk_update("/new dev")
    run_async(bot._handle_new_cmd(update, MagicMock()))

    # Old flow does TWO reply_text-equivalent calls (status_msg.edit_text
    # is on the status message, not the original). At minimum: no conflict
    # prompt with inline keyboard was sent.
    assert "claude-dev" not in bot._new_conflict_pending


def test_new_alive_collision_prompts_buttons(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/new jim do something")
    run_async(bot._handle_new_cmd(update, MagicMock()))

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


def test_new_gone_with_resumable_id_prompts_buttons(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-OLD"
    sess.gone_at = time.time() - 60
    sess.last_assistant_preview = "I refactored auth."
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/new jim")
    run_async(bot._handle_new_cmd(update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "previously used" in text
    assert "I refactored auth." in text


def test_new_gone_without_resumable_id_falls_through(monkeypatch, mk_bot, mk_update, run_async):
    """A GONE entry with no claude_session_id is not resumable — fall through
    to a fresh launch without showing the conflict prompt."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = time.time() - 60
    # claude_session_id deliberately empty
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    async def _ok_launch(*a, **kw):
        return True, ""

    monkeypatch.setattr(tb.inject, "launch_session", _ok_launch)
    update = mk_update("/new jim")
    run_async(bot._handle_new_cmd(update, MagicMock()))

    assert "claude-jim" not in bot._new_conflict_pending


def test_new_skip_perms_flag_carried_through(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/new !jim")
    run_async(bot._handle_new_cmd(update, MagicMock()))

    assert bot._new_conflict_pending["claude-jim"]["skip_perms"] is True
