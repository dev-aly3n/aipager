"""Black-box fixtures for the Mini App redesign ("Lanterns", roadmap 8.44).

Everything here is built from the public surface listed in the pipeline's
entrypoints.md: the aiohttp app behind ``MiniAppServer._build_app()``
driven with HMAC-signed initData, the registry's public session fields,
and a recording stand-in for the Telegram Bot API object. No test in this
directory reaches the real Telegram API, a real dtach socket, a real
``claude``, systemd, or the operator's ``~/.claude`` / ``~/.config``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

# The directory name holds a hyphen, so it cannot be imported as a package;
# test modules reach these helpers through this alias instead.
sys.modules.setdefault("miniapp_redesign_bb", sys.modules[__name__])

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555          # admin: may prompt
DEVELOPER_ID = 777      # may prompt, not admin
READONLY_ID = 888       # member who may NOT prompt
OUTSIDER_ID = 999999    # member of no scope
FOREIGN_MEMBER_ID = 321  # member of the other scope only


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_data(user_id, *, bot_token=BOT_TOKEN, auth_date=None):
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def hdr(user_id, **kw):
    return {"X-Telegram-Init-Data": init_data(user_id, **kw)}


class _Role:
    def __init__(self, *, bypass_safety=False, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    _ROLES = {
        "admin": _Role(bypass_safety=True, can_prompt=True),
        "developer": _Role(bypass_safety=False, can_prompt=True),
        "read_only": _Role(bypass_safety=False, can_prompt=False),
    }

    def get_role(self, name):
        return self._ROLES.get(name)


class TelegramRecorder:
    """Stands in for ``telegram.Bot``: every ``send_message`` is recorded
    as ``(chat_id, text, reply_markup, kwargs)`` and answered with a fresh
    message id. ``raise_on_send`` (an exception instance or factory) makes
    the next sends fail the way Telegram or the outbound gate would."""

    def __init__(self):
        self.sends: list[tuple] = []
        self.ids: list[int] = []
        self.raise_on_send = None
        #: answer every send with None (a send the transport dropped)
        self.return_none = False
        self._next_id = 7000

    async def send_message(self, chat_id=None, text=None, *, reply_markup=None,
                           **kw):
        if self.raise_on_send is not None:
            exc = self.raise_on_send
            raise exc() if callable(exc) and not isinstance(exc, BaseException) else exc
        if self.return_none:
            return None
        self._next_id += 1
        self.sends.append((chat_id, text, reply_markup, kw))
        self.ids.append(self._next_id)
        return SimpleNamespace(message_id=self._next_id, chat_id=chat_id,
                               chat=SimpleNamespace(id=chat_id))


@pytest.fixture
def tg():
    return TelegramRecorder()


@pytest.fixture
def server(mk_bot, tg):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
    )
    foreign = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign])
    bot.policy = _Policy()
    fake = MagicMock()
    fake.username = "aipager_test_bot"
    fake.send_message = tg.send_message
    # anything else the bot might call (edits, pins, deletes) is inert
    fake.edit_message_text = AsyncMock()
    fake.edit_message_reply_markup = AsyncMock()
    fake.pin_chat_message = AsyncMock()
    fake.delete_message = AsyncMock()
    bot._app.bot = fake
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8779)


@pytest.fixture(autouse=True)
def _no_pty_keystrokes(monkeypatch):
    """Record (never perform) every keystroke / liveness / kill call, so a
    test can assert the answer route never types into the PTY."""
    keys: list = []

    async def _send_keys(*a, **k):
        keys.append((a, k))
        return True

    async def _alive(*_a, **_k):
        return True

    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _alive)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    return keys


def mk_session(server, label, *, scope_chat_id=SCOPE_CHAT_ID,
               status=Status.IDLE, cwd="/srv/secret/proj", name=None):
    sess = server.registry.get_or_create(name or f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.scope_kind = "group"
    sess.status = status
    sess.cwd = cwd
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


def put_on_permission(sess, summary="Bash: make deploy"):
    """A session waiting on a permission prompt, as the pinned bar's own
    tests record one (status INTERACTIVE + pending_permission)."""
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": summary, "tool_info": None,
                               "wait_started_at": 0.0}
    return sess


async def client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


# ── node page harness (bb_page.js) ──────────────────────────────────────────

HARNESS = __import__("pathlib").Path(__file__).parent / "bb_page.js"


@pytest.fixture(scope="module")
def node_bin():
    import shutil
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available; page behaviour tests skipped")
    return exe


@pytest.fixture(scope="module")
def served_page(tmp_path_factory):
    from aipager.miniapp.static import index_html
    path = tmp_path_factory.mktemp("bbpage") / "page.html"
    path.write_text(index_html(sdk_from_self=True), encoding="utf-8")
    return path


def drive(node_bin, page_path, scenario, fix_path=None):
    """Run one bb_page.js scenario; ``fix_path`` is an optional JSON file of
    API fixtures merged over the harness defaults."""
    import subprocess
    argv = [node_bin, str(HARNESS), str(page_path), scenario]
    if fix_path is not None:
        argv.append(str(fix_path))
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    raise AssertionError(
        f"scenario {scenario} produced no result (exit {proc.returncode})\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}")
