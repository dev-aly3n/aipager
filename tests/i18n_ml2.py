"""Helpers for the black-box scenarios of multilingual phase 2
(``tests/integration/multilingual-phase-2``): a chat set to Persian or
Russian, driven through the real handlers (``_handle_message``,
``_handle_callback``, the command handlers), with every Telegram call a
recorded mock. A plain module, imported by the scenarios, so no conftest
can shadow it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

DM = 555                 # a private chat: its id is its person's id
GROUP = -1001
OWNER, BOB = 555, 2


def scopes():
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="alice DM",
              members=(Member(id=OWNER, label="alice", role="owner"),)),
    ]




def add_session(bot, *, chat=DM, label="x1", status=Status.IDLE):
    s = bot.registry.get_or_create(f"claude-{label}__{'d' if chat > 0 else 'g'}{abs(chat)}")
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, ("dm" if chat > 0 else "group")
    s.status = status
    return s


def message(text, *, chat=DM, user=OWNER, reply_to=None, language_code="en"):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.caption = None
    update.message.message_id = 900
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    update.message.reply_to_message = reply_to
    update.message.quote = None
    update.message.external_reply = None
    update.message.forward_origin = None
    update.message.via_bot = None
    update.message.media_group_id = None
    update.message.chat_id = chat
    update.message.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    update.effective_user = MagicMock(id=user, language_code=language_code, is_bot=False)
    update.effective_user.username = "alice" if user == OWNER else "bob"
    update.effective_chat = update.message.chat
    return update


def callback(data, *, chat=DM, user=OWNER, message_id=42, text=""):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock(message_id=message_id, text=text, chat_id=chat)
    query.message.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    query.from_user = MagicMock(id=user, language_code="en")
    query.from_user.username = "alice" if user == OWNER else "bob"
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query



def make_bot(mk_bot):
    bot = mk_bot(scopes=scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    tg = bot._app.bot
    tg.id = 999
    tg.username = "aipager_test_bot"
    tg.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    tg.edit_message_text = AsyncMock()
    tg.set_my_commands = AsyncMock()
    tg.set_chat_menu_button = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._miniapp_url = "https://example.invalid/app"
    return bot
