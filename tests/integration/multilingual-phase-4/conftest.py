"""Fixtures for the black-box tests of multilingual phase 4 (the Mini App in
Persian and Russian).

Everything here drives the documented surfaces of entrypoints.md: the HTTP
routes through ``MiniAppServer._build_app`` with an aiohttp TestClient, the
exported functions of ``aipager.miniapp.ui_text`` / ``phrase`` /
``chat_lines``, and the served page in node. Every process seam
(``aipager.dtach.inject``, ``bot.create_session``, ``session_ops._sleep``) is
a mock: nothing here launches claude or dtach, touches a socket, or reaches
Telegram.

The viewer's language is the ``ui_language`` stored for the viewer's own id
(``preferences.set_ui_language``); a chat's language is the one stored for
that chat id.
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

GROUP = -1001          # the group every session lives in
ADA = 555              # owner of the group (an admin)
BOB = 2                # admin of the group
CY = 3                 # read-only member
DEV = 7                # ordinary member: can prompt, not an admin
OUTSIDER = 999999      # member of no scope

# Characters a test asserts on, written as escapes (no raw bidi in source).
RLM = "\u200f"
LRM = "\u200e"
FSI = "\u2068"
PDI = "\u2069"
BIDI = {"\u200e", "\u200f", "\u061c", "\u202a", "\u202b", "\u202c", "\u202d",
        "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}
FA_DIGITS = "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
AR_DIGITS = "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"


def has_arabic_script(s):
    return any("\u0600" <= c <= "\u06ff" or "\ufb50" <= c <= "\ufdff" for c in str(s))


def has_cyrillic(s):
    return any("\u0400" <= c <= "\u04ff" for c in str(s))


def has_bidi(s):
    return any(c in BIDI for c in str(s))


def has_native_digit(s):
    return any(c in FA_DIGITS or c in AR_DIGITS for c in str(s))


def scopes(group_label="team"):
    return [
        Scope(chat_id=GROUP, kind="group", label=group_label, members=(
            Member(id=ADA, label="ada", role="owner"),
            Member(id=BOB, label="bob", role="admin"),
            Member(id=CY, label="cy", role="read_only"),
            Member(id=DEV, label="dev", role="user"),
        )),
    ]


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


def set_lang(chat_or_user_id, code):
    from aipager import preferences
    preferences.set_ui_language(chat_or_user_id, code)


class Resp:
    def __init__(self, status, headers, raw, body):
        self.status = status
        self.headers = headers
        self.raw = raw
        self.body = body


class Env:
    """One Mini App server over a bot whose Telegram calls are mocks."""

    def __init__(self, bot, server, run_async, project):
        self.bot = bot
        self.server = server
        self.registry = server.registry
        self.run_async = run_async
        self.project = project

    # ---- HTTP -------------------------------------------------------------
    def call(self, method, path, *, user=BOB, headers=None, scope=None,
             auto_decompress=True, **kw):
        from aiohttp.test_utils import TestClient, TestServer
        if headers is None:
            headers = hdr(user)
        headers = dict(headers)
        if scope is not None:
            headers["X-Aipager-Scope"] = str(scope)

        async def _go():
            client = TestClient(TestServer(self.server._build_app()),
                                auto_decompress=auto_decompress)
            await client.start_server()
            try:
                resp = await client.request(method, path, headers=headers, **kw)
                raw = await resp.read()
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = None
                return Resp(resp.status, dict(resp.headers), raw, body)
            finally:
                await client.close()
        return self.run_async(_go())

    # ---- sessions ---------------------------------------------------------
    def session(self, label, *, status="idle", skip_perms=False, model="Sonnet 5",
                cwd=None, claude_session_id="", queued=0):
        from aipager.state import Status
        sess = self.registry.get_or_create(f"claude-{label}")
        sess.label = label
        sess.scope_chat_id = GROUP
        sess.status = getattr(Status, status.upper())
        sess.skip_perms = skip_perms
        sess.model_name = model
        sess.cwd = cwd if cwd is not None else str(self.project)
        sess.claude_session_id = claude_session_id
        if sess.status == Status.GONE:
            sess.gone_at = time.monotonic()
        for i in range(queued):
            sess.queue_prompt(f"queued {i}", i + 1)
        return sess

    # ---- the chat ---------------------------------------------------------
    def posted(self):
        """``(chat_id, text)`` of every message sent to Telegram."""
        out = []
        for c in self.bot._app.bot.send_message.await_args_list:
            text = c.kwargs.get("text")
            if text is None and len(c.args) > 1:
                text = c.args[1]
            out.append((c.kwargs.get("chat_id", c.args[0] if c.args else None), text))
        return out

    def last_line(self):
        posted = self.posted()
        assert posted, "nothing was posted into a chat"
        return posted[-1][1]


@pytest.fixture
def env(mk_bot, run_async, monkeypatch, tmp_path):
    from aipager.miniapp.server import MiniAppServer
    from aipager.state import SessionRegistry, Status

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(project))

    # Every PTY / process seam is a recorded mock.
    async def _no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=False))))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))

    registry = SessionRegistry()
    bot = mk_bot(registry, scopes=scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    tg = bot._app.bot
    tg.id = 999
    tg.username = "aipager_test_bot"
    tg.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot.create_session = AsyncMock(return_value=("claude-made", ""))

    # A gone session in the project folder makes it an allowed root.
    seed = registry.get_or_create("claude-seed")
    seed.label = "seed"
    seed.scope_chat_id = GROUP
    seed.status = Status.GONE
    seed.gone_at = time.monotonic()
    seed.cwd = str(project)

    server = MiniAppServer(bot, registry, port=8765)
    server.model_confirm_seconds = 0.05
    return Env(bot, server, run_async, project)


@pytest.fixture
def p4():
    """This module's helpers (the directory name has hyphens, so test modules
    cannot import it; bound at import time because every hyphenated conftest
    is loaded under the bare name ``conftest``)."""
    return _THIS
