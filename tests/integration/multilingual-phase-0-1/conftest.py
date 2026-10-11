"""Shared fixtures for the black-box tests of multilingual phases 0 and 1.

Everything here drives the documented surfaces of entrypoints.md: the
Telegram handlers and callbacks through ``bot._handle_callback`` and the
command handlers (the harness the rest of the suite uses), and the Mini App
over HTTP through ``MiniAppServer._build_app`` (the only way the suite gets
a server; no attribute of it is asserted on).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest

from aipager.policy import load_policy
from aipager.scope import Member, Scope

_THIS = sys.modules[__name__]

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

DM_CHAT = 555            # a private chat: its id is the user's id
GROUP_CHAT = -1001
OWNER_ID = 555           # owner of the group and the DM
BOB_ID = 2               # an ordinary group member
RO_ID = 3                # a read-only group member
OUTSIDER_ID = 999999     # in no scope

DM_PRIVATE_TEXT = (
    "🌐 The language aipager uses for its own messages and buttons, here and "
    "in the Mini App. Your Claude sessions don't change: Claude answers in "
    "the language you write to it.")
GROUP_LIST_TEXT = (
    "🌐 The language aipager uses for this group's cards and buttons. "
    "Claude's sessions don't change: Claude answers in the language you "
    "write to it.")
CONFIRMATION = ("✅ aipager now speaks English here. Your sessions and "
                "Claude's answers are unchanged.")
AUTO_LABEL = "Automatic (your Telegram language)"
ADMIN_ALERT = "Only an admin can change settings in this group."
APP_HINT = "Only aipager's own text. Claude's answers don't change."


def scopes():
    return [
        Scope(chat_id=GROUP_CHAT, kind="group", label="team", members=(
            Member(id=OWNER_ID, label="aly", role="owner"),
            Member(id=BOB_ID, label="bob", role="user"),
            Member(id=RO_ID, label="ro", role="read_only"),
        )),
        Scope(chat_id=DM_CHAT, kind="dm", label="aly DM",
              members=(Member(id=OWNER_ID, label="aly", role="owner"),)),
    ]


@pytest.fixture
def tg_bot(mk_bot):
    """A scope-mode bot whose Telegram calls are all recorded mocks."""
    bot = mk_bot(scopes=scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    tg = bot._app.bot
    tg.id = 999
    tg.username = "aipager_test_bot"
    tg.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    tg.edit_message_text = AsyncMock()
    tg.set_my_commands = AsyncMock()
    tg.delete_my_commands = AsyncMock()
    tg.set_chat_menu_button = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def make_query(data, *, chat_id, user_id, message_id=42, text=""):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = text
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.chat_id = chat_id
    query.message.chat.type = "private" if chat_id > 0 else "supergroup"
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.from_user.language_code = "en"
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query


@pytest.fixture
def tap(run_async):
    """Tap a callback button; returns the query mock."""
    def _tap(bot, data, *, chat_id, user_id):
        update, query = make_query(data, chat_id=chat_id, user_id=user_id)
        run_async(bot._handle_callback(update, MagicMock()))
        return query
    return _tap


@pytest.fixture
def command(mk_update, run_async):
    """Run a command handler; returns ``(text, reply_markup)`` of its reply."""
    def _cmd(bot, handler_name, text, *, chat_id, user_id):
        update = mk_update(text, chat_id=chat_id, user_id=user_id)
        update.effective_chat.type = "private" if chat_id > 0 else "supergroup"
        update.effective_user.language_code = "en"
        run_async(getattr(bot, handler_name)(update, MagicMock()))
        call = update.message.reply_text.await_args
        assert call is not None, f"{handler_name} sent no reply"
        body = call.args[0] if call.args else call.kwargs.get("text")
        return body, call.kwargs.get("reply_markup")
    return _cmd


def edited(query):
    """Text and markup of the last edit_message_text on *query*."""
    call = query.edit_message_text.await_args
    assert call is not None, "the message was not edited"
    body = call.args[0] if call.args else call.kwargs.get("text")
    return body, call.kwargs.get("reply_markup")


def toasts(query):
    out = []
    for c in query.answer.await_args_list:
        text = c.args[0] if c.args else c.kwargs.get("text")
        if text:
            out.append((text, c.kwargs.get("show_alert")))
    return out


def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def prefs_bytes():
    """The redirected preferences.json's bytes, or None when absent."""
    from aipager import preferences
    path = Path(preferences._PREFERENCES_PATH)
    return path.read_bytes() if path.exists() else None


# ---- Mini App ------------------------------------------------------------------

@pytest.fixture
def mini_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_data(user_id, *, language_code=None, token=BOT_TOKEN):
    user = {"id": user_id, "first_name": "Test"}
    if language_code is not None:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    fields["hash"] = _sign(fields, token)
    return urlencode(fields)


def hdr(user_id, **kw):
    return {"X-Telegram-Init-Data": init_data(user_id, **kw)}


@pytest.fixture
def mini_server(tg_bot, mini_token):
    from aipager.miniapp.server import MiniAppServer
    return MiniAppServer(tg_bot, tg_bot.registry, port=8765)


async def client_for(server):
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(server._build_app()))
    await client.start_server()
    return client


@pytest.fixture
def ml():
    """This module's helpers and constants (the directory name has hyphens,
    so test modules cannot import it relatively). Bound at import time:
    pytest loads every hyphenated conftest as the bare name ``conftest``,
    so a lookup when the test runs finds whichever one was imported last."""
    return _THIS
