"""design.md / entrypoints.md: "/status (end of reply)" gets the App
button only in private chats ("Telegram rejects the ENTIRE keyboard"
carrying a web_app button in a group). ``test_app_button_discoverability.py``
proves this with an EMPTY session list; ``test_parity_integration.py``
proves the ⋮ row exists per session, but without asserting the group +
app-button interaction. Neither exercises the case that actually
matters in production: a GROUP chat, with a LIVE session (so /status
renders real ⋮ buttons), and a configured Mini App URL — the exact
combination where a misplaced ``web_app`` button would silently break
every other button in the same message, including the brand-new ⋮ row
this feature adds.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from aipager.state import Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _alive(name):
    return True


CHAT = -100


def _has_web_app_button(markup):
    if markup is None:
        return False
    return any(
        getattr(btn, "web_app", None) is not None
        for row in markup.inline_keyboard for btn in row
    )


def test_status_in_a_group_with_a_live_session_never_carries_a_web_app_button(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    sess = TrackedSession(name="claude-groupwiz__g100", label="groupwiz",
                           status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update("/status", chat_id=CHAT, chat_type="group")
    with patch("aipager.dtach.inject.is_alive", side_effect=_alive):
        _run(bot._handle_status(upd, MagicMock()))

    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert not _has_web_app_button(kb), (
        "a group /status with a live session must never carry a "
        "web_app button in the SAME keyboard as the ⋮ row"
    )


def test_status_in_a_group_with_a_live_session_still_has_a_working_menu_button(
        mk_bot, helpers):
    """The absence of the App button in a group must not come at the
    cost of the ⋮ row itself — the two features (discoverability gating,
    session-parity ⋮ row) must coexist correctly in the same reply."""
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    sess = TrackedSession(name="claude-groupwiz2__g100", label="groupwiz2",
                           status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update("/status", chat_id=CHAT, chat_type="group")
    with patch("aipager.dtach.inject.is_alive", side_effect=_alive):
        _run(bot._handle_status(upd, MagicMock()))

    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    sx_cbs = [c for c in cbs if c.startswith("_:sx:")]
    dests = [helpers.destinations(bot, CHAT, [c])[0] for c in sx_cbs]
    assert (sess.name, "menu") in dests, (
        f"expected a working ⋮ menu button for {sess.name}, got dests={dests}"
    )


def test_status_in_a_private_chat_with_a_live_session_has_both_menu_and_app_button(
        mk_bot, helpers):
    """Control case: in a PRIVATE chat, both features are allowed to
    coexist — proving the group-only restriction above is specific to
    chat type, not a blanket regression against the App button whenever
    sessions are present."""
    bot = helpers.make_personal_bot(mk_bot, miniapp_url="https://example.com/app")
    sess = TrackedSession(name="claude-privwiz__100", label="privwiz",
                           status=Status.IDLE, scope_chat_id=100)
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update("/status", chat_id=100, chat_type="private")
    with patch("aipager.dtach.inject.is_alive", side_effect=_alive):
        _run(bot._handle_status(upd, MagicMock()))

    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    assert _has_web_app_button(kb)
    cbs = helpers.callback_data_in(kb)
    dests = [helpers.destinations(bot, 100, [c])[0] for c in cbs
             if c.startswith("_:sx:")]
    assert (sess.name, "menu") in dests
