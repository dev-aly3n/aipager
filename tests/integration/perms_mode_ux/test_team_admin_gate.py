"""Integration tests: SC14, SC15 — team-mode admin gating.

SC14: Non-admin /perms to Auto → "requires admin role", no mode change.
SC15: Non-admin /new !ben → "requires admin role", no session launched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(text, *, user_id=12345, chat_id=-1001):
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


def _make_bot_non_admin(*, registry=None):
    """Build a bot where _is_admin always returns False."""
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    # Patch the admin rule to return False: `_is_admin(update)` delegates
    # to `_is_admin_user(user_id, chat_id)`, which surfaces without an
    # Update (the /new Name card and Ready card) call directly.
    bot._is_admin = MagicMock(return_value=False)
    bot._is_admin_user = MagicMock(return_value=False)
    return bot


# --------------------------------------------------------------------------- #
# SC14 — Non-admin /perms → Auto: denied with admin-role message              #
# --------------------------------------------------------------------------- #

def test_sc14_non_admin_perms_to_auto_denied():
    """SC14: Non-admin user sending /perms to switch to Auto must receive a
    reply containing 'requires admin role'."""
    bot = _make_bot_non_admin()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False  # currently Ask → trying to switch to Auto
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "requires admin role" in msg, (
        f"Non-admin must get 'requires admin role'; got: {msg}"
    )


def test_sc14_non_admin_perms_to_auto_no_mode_change():
    """SC14: After non-admin /perms denial, skip_perms must NOT be changed."""
    bot = _make_bot_non_admin()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    assert sess.skip_perms is False, (
        "Non-admin denial must leave skip_perms unchanged"
    )


def test_sc14_non_admin_perms_to_auto_no_keyboard():
    """SC14: Non-admin /perms denial must not send a confirmation keyboard."""
    bot = _make_bot_non_admin()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    call_kwargs = update.message.reply_text.await_args[1] or {}
    kb = call_kwargs.get("reply_markup")
    # Either no keyboard, or if there is one, no perms_confirm in it
    if kb is not None:
        cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert not any("perms_confirm" in cb for cb in cbs), (
            "Non-admin denial must not show a confirm keyboard"
        )


def test_sc14_admin_perms_to_auto_not_denied():
    """SC14 negative: Admin user switching to Auto must NOT get the denial."""
    from aipager.bot import TelegramBot
    bot = TelegramBot(SessionRegistry())
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    bot._is_admin = MagicMock(return_value=True)

    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    if update.message.reply_text.await_args is not None:
        msg = update.message.reply_text.await_args[0][0]
        assert "requires admin role" not in msg, (
            "Admin must not get denial message"
        )


# --------------------------------------------------------------------------- #
# SC15 — Non-admin /new !ben: denied, no session launched                     #
# --------------------------------------------------------------------------- #

def test_sc15_non_admin_new_auto_gets_ask_and_says_why():
    """SC15 (2026-09-30 redesign): a non-admin's /new !ben never gets
    Auto. The session starts in Ask and the Ready card says why, instead
    of refusing someone who may create Ask sessions."""
    bot = _make_bot_non_admin()
    bot._app.bot.edit_message_text = AsyncMock()
    update = _make_update("/new !ben")

    async def _launch_ok(*a, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "Auto mode needs an admin" in text, text
    assert "💬 Ask" in text, text


def test_sc15_non_admin_new_auto_creates_no_auto_session():
    """SC15: whatever a non-admin types, no session with skip_perms=True."""
    bot = _make_bot_non_admin()
    bot._app.bot.edit_message_text = AsyncMock()
    update = _make_update("/new !ben")

    async def _launch_ok(*a, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))

    assert not [s for s in bot.registry.all_sessions().values() if s.skip_perms]
def test_sc15_admin_new_auto_not_denied():
    """SC15 negative: Admin /new !ben must not get the denial message."""
    from aipager.bot import TelegramBot
    bot = TelegramBot(SessionRegistry())
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/new !ben")

    from unittest.mock import patch

    async def mock_launch(*a, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        _run(bot._handle_new_cmd(update, MagicMock()))

    # Any reply should NOT contain "requires admin role"
    if update.message.reply_text.await_args is not None:
        msg = update.message.reply_text.await_args[0][0]
        assert "requires admin role" not in msg, (
            "Admin must not get denial for /new !ben"
        )


# --------------------------------------------------------------------------- #
# SC14/SC15 edge: auto→ask does NOT require admin                             #
# --------------------------------------------------------------------------- #

def test_non_admin_perms_auto_to_ask_allowed():
    """Switching from Auto to Ask (safer direction) must not require admin.
    spec says only /perms → Auto and /new ! require admin."""
    bot = _make_bot_non_admin()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = True  # currently Auto → switching to Ask
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._do_perms_switch_via_fn = AsyncMock()

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    # Must not have been denied
    if update.message.reply_text.await_args is not None:
        msg = update.message.reply_text.await_args[0][0]
        assert "requires admin role" not in msg, (
            "Auto→Ask switch must not require admin"
        )
