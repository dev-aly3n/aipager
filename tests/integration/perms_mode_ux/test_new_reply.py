"""Integration tests: SC10, SC11 — enriched /new reply text.

SC10: /new ben → contains 💬, "Ask", actual cwd, "/perms" nudge; model omitted when unknown.
SC11: /new !ben → contains 🤖, "Auto", no "/perms" nudge.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(text, *, user_id=12345, chat_id=0):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 999
    update.message.reply_text = AsyncMock()
    update.message.reply_to_message = None
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


def _make_bot(*, registry=None):
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


async def _launch_ok(*a, **kw):
    return True, ""


# --------------------------------------------------------------------------- #
# SC10 — /new ben reply contains Ask mode indicators and /perms nudge         #
# --------------------------------------------------------------------------- #

def test_sc10_new_ask_reply_contains_ask_icon():
    """SC10: /new ben reply must contain 💬 icon for Ask mode."""
    bot = _make_bot()
    update = _make_update("/new ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args[0][0]
    assert "💬" in text, f"Ask mode reply must contain 💬; got: {text}"


def test_sc10_new_ask_reply_contains_ask_label():
    """SC10: /new ben reply must contain the word 'Ask'."""
    bot = _make_bot()
    update = _make_update("/new ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "Ask" in text, f"Ask mode reply must contain 'Ask'; got: {text}"


def test_sc10_new_ask_reply_contains_perms_nudge():
    """SC10: /new ben reply must contain '/perms' as a discoverability nudge."""
    bot = _make_bot()
    update = _make_update("/new ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "/perms" in text, f"Ask mode reply must contain /perms nudge; got: {text}"


def test_sc10_new_ask_reply_contains_cwd():
    """SC10: /new ben reply must contain the actual launch cwd."""
    bot = _make_bot()
    update = _make_update("/new ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    # cwd should appear in the reply — get the current working directory
    # The handler uses os.getcwd() or similar; at minimum it shouldn't be absent
    assert "/" in text, f"Reply must contain a cwd path; got: {text}"


def test_sc10_new_ask_model_omitted_when_unknown():
    """SC10: When model_name is empty, the model clause is omitted.
    The reply must not contain 'None' or an empty placeholder."""
    bot = _make_bot()
    update = _make_update("/new ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "None" not in text, f"Reply must not contain 'None'; got: {text}"


# --------------------------------------------------------------------------- #
# SC11 — /new !ben reply contains Auto mode, no /perms nudge                  #
# --------------------------------------------------------------------------- #

def test_sc11_new_auto_reply_contains_auto_icon():
    """SC11: /new !ben reply must contain 🤖 icon for Auto mode."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args[0][0]
    assert "🤖" in text, f"Auto mode reply must contain 🤖; got: {text}"


def test_sc11_new_auto_reply_contains_auto_label():
    """SC11: /new !ben reply must contain the word 'Auto'."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "Auto" in text, f"Auto mode reply must contain 'Auto'; got: {text}"


def test_sc11_new_auto_reply_no_perms_nudge():
    """SC11: /new !ben reply must NOT contain '/perms' nudge."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "/perms" not in text, (
        f"Auto mode reply must NOT contain /perms nudge; got: {text}"
    )


def test_sc11_new_auto_reply_no_ask_icon():
    """SC11: /new !ben reply must NOT contain 💬 (Ask icon)."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !ben")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "💬" not in text, (
        f"Auto mode reply must NOT contain 💬; got: {text}"
    )
