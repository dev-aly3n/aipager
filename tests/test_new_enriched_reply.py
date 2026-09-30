"""The /new reply is the Ready card: mode, model, folder, where the next
message goes, and a one-tap switch to the other mode (it replaced the
"/perms" nudge in the 2026-09-30 redesign). Auto is the default for an
admin; a non-admin gets Ask."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import preferences
from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_launch_ok():
    """Patch inject.launch_session to succeed."""
    async def _launch(*a, **kw):
        return True, ""
    return _launch


def _ready(bot, update, launch):
    bot._app.bot.edit_message_text = AsyncMock()
    with patch("aipager.dtach.inject.launch_session", side_effect=launch):
        import asyncio
        asyncio.new_event_loop().run_until_complete(
            bot._handle_new_cmd(update, MagicMock()))
    bot._app.bot.edit_message_text.assert_awaited_once()
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    buttons = [b.text for row in kw["reply_markup"].inline_keyboard for b in row]
    return kw["text"], buttons


def test_new_ask_mode_reply_offers_auto(mk_bot, mk_update, mk_launch_ok):
    """An Ask session (the chat's default set to Ask): 💬 Ask on the card,
    and a Switch to Auto button instead of a /perms nudge."""
    update = mk_update("/new dev")
    preferences.set_new_session_default(update.effective_chat.id, "mode", "ask")
    text, buttons = _ready(mk_bot(), update, mk_launch_ok)

    assert "💬 Ask" in text, text
    assert "🤖" not in text, text
    assert "/perms" not in text, text
    assert "🤖 Switch to Auto" in buttons, buttons


def test_new_auto_mode_reply_offers_ask(mk_bot, mk_update, mk_launch_ok):
    text, buttons = _ready(mk_bot(), mk_update("/new !dev"), mk_launch_ok)

    assert "🤖 Auto" in text, text
    assert "/perms" not in text, text
    assert "💬 Switch to Ask" in buttons, buttons


def test_new_reply_reads_default_model_when_unknown(mk_bot, mk_update, mk_launch_ok):
    text, _ = _ready(mk_bot(), mk_update("/new dev"), mk_launch_ok)

    assert "None" not in text, text
    assert "🧠 Default model" in text, text


def test_new_reply_includes_model_when_known(mk_bot, mk_update, mk_launch_ok):
    """A known model (normally from the first statusLine event) shows."""
    from aipager.scope import disambiguated_name

    bot = mk_bot()
    session_name = disambiguated_name("dev", 0, "dm")
    pre = TrackedSession(name=session_name, label="dev", status=Status.GONE)
    pre.model_name = "Sonnet 4.5"
    bot.registry._sessions[session_name] = pre

    text, _ = _ready(bot, mk_update("/new dev", chat_id=0), mk_launch_ok)
    assert "Sonnet 4.5" in text, text


def test_new_auto_needs_an_admin(mk_bot, mk_update, mk_launch_ok):
    """A non-admin's /new !dev gets Ask, and the card says why."""
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    bot._is_admin_user = MagicMock(return_value=False)
    text, _ = _ready(bot, mk_update("/new !dev"), mk_launch_ok)

    assert "Auto mode needs an admin" in text, text
    assert "💬 Ask" in text, text


def _find_dev_session(bot):
    """Find the 'dev'-labeled session regardless of internal name."""
    for sess in bot.registry.all_sessions().values():
        if sess.label == "dev":
            return sess
    return None


def test_new_is_auto_by_default_for_an_admin(mk_bot, mk_update, mk_launch_ok):
    bot = mk_bot()
    _ready(bot, mk_update("/new dev"), mk_launch_ok)

    sess = _find_dev_session(bot)
    assert sess is not None and sess.skip_perms is True


def test_new_honours_the_chats_ask_default(mk_bot, mk_update, mk_launch_ok):
    bot = mk_bot()
    update = mk_update("/new dev")
    preferences.set_new_session_default(update.effective_chat.id, "mode", "ask")
    _ready(bot, update, mk_launch_ok)

    sess = _find_dev_session(bot)
    assert sess is not None and sess.skip_perms is False


def test_new_auto_sets_skip_perms_true(mk_bot, mk_update, mk_launch_ok):
    bot = mk_bot()
    _ready(bot, mk_update("/new !dev"), mk_launch_ok)

    sess = _find_dev_session(bot)
    assert sess is not None and sess.skip_perms is True
