"""Fixtures for the "waiting for agents shows as working" scenario
(roadmap 8.121).

A real ``HookReceiver`` drives a real ``TelegramBot.notify`` (the
Telegram boundary mocked), and every surface is read the way its user
reads it: the pinned bar's render, ``GET /api/sessions`` through the Mini
App's own aiohttp app with signed initData, the CLI's rows from the state
file the registry writes, and the busy card's render. No test here
reaches Telegram, a dtach socket, a real ``claude`` or the real home.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.policy import load_policy
from aipager.scope import Member, Scope

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
CHAT = -100
ADMIN_ID = 555
SESSION = "frontend"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    # Byte-identical Stop payloads in a zero-time replay are not what this
    # scenario is about (the general hook dedup is pre-existing).
    monkeypatch.setattr(hr, "HOOK_DEDUP_WINDOW_SECONDS", -1.0)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))


def _sign(fields: dict) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_header(user_id: int = ADMIN_ID) -> dict:
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    fields["hash"] = _sign(fields)
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def init_header():
    """``init_header()``: signed initData headers for the chat's admin (a
    fixture: this directory's name cannot be imported from)."""
    return _init_header


@pytest.fixture
def bot(mk_bot):
    scope = Scope(chat_id=CHAT, kind="group", label="team",
                  members=(Member(id=ADMIN_ID, label="alice", role="admin"),),
                  root="/home/alice")
    b = mk_bot(scopes=[scope])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.username = "aipager_test_bot"
    b._app.bot.edit_message_text = AsyncMock()
    b._stop_animation = MagicMock()
    b._maybe_update_bot_name = AsyncMock()
    b._update_bot_commands = AsyncMock()

    async def _send_busy(sess):
        sess.busy_msg_id = 42
        sess.busy_started_at = time.monotonic()

    b._send_busy_and_animate = AsyncMock(side_effect=_send_busy)
    return b
