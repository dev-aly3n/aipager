"""design.md success criteria: "The App button appears on /start,
/status, /settings, and after a one-shot /new — private chats only,
never alongside a keyboard already carrying it in the same turn, never
in a group."

Only /status and /settings and the one-shot /new success reply are
checked for an INLINE app row here: /start's own docstring
(`_app_button_row`, `handlers.py`) documents that it deliberately
relies on the persistent keyboard instead (sent at the end of
`_handle_start_cmd`) rather than a competing inline button in the same
turn — covered by the existing keyboard test suite, not duplicated
here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _has_web_app_button(markup):
    if markup is None:
        return False
    return any(
        getattr(btn, "web_app", None) is not None
        for row in markup.inline_keyboard for btn in row
    )


def test_status_private_chat_with_miniapp_url_shows_app_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update("/status", chat_id=555, chat_type="private")
    _run(bot._handle_status(upd, None))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert _has_web_app_button(kb)


def test_status_group_chat_never_shows_app_button(mk_bot, helpers):
    """Telegram rejects a whole keyboard carrying a web_app button in a
    group — must never be offered there, regardless of miniapp_url."""
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update("/status", chat_id=-100, chat_type="group")
    _run(bot._handle_status(upd, None))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert not _has_web_app_button(kb)


def test_status_private_chat_without_miniapp_url_shows_no_app_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="")
    upd = helpers.make_message_update("/status", chat_id=555, chat_type="private")
    _run(bot._handle_status(upd, None))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert not _has_web_app_button(kb)


def test_settings_private_chat_with_miniapp_url_shows_app_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update("/settings", chat_id=555, chat_type="private")
    _run(bot._handle_settings_cmd(upd, None))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert _has_web_app_button(kb)


def test_settings_group_chat_never_shows_app_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update("/settings", chat_id=-100, chat_type="group")
    _run(bot._handle_settings_cmd(upd, None))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert not _has_web_app_button(kb)


def test_oneshot_new_success_reply_private_chat_shows_app_button(mk_bot, helpers):
    from unittest.mock import MagicMock

    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update(
        "/new henlo", chat_id=555, chat_type="private")

    async def _launch_ok(*a, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(upd, MagicMock()))

    status_msg = upd.message.reply_text.return_value
    kb = status_msg.edit_text.await_args.kwargs.get("reply_markup")
    assert _has_web_app_button(kb)


def test_oneshot_new_success_reply_group_chat_never_shows_app_button(mk_bot, helpers):
    from unittest.mock import MagicMock

    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    upd = helpers.make_message_update(
        "/new henlo", chat_id=-100, chat_type="group")

    async def _launch_ok(*a, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(upd, MagicMock()))

    status_msg = upd.message.reply_text.return_value
    kb = status_msg.edit_text.await_args.kwargs.get("reply_markup")
    assert not _has_web_app_button(kb)
