"""Row B / criterion 2 — the Mini App's 13 chat mirrors inherit the gate.

The incident's grep found 13 ``self.bot._app.bot.send_message`` calls in
``aipager/miniapp/server.py``, none of them behind a mute check. D-9 says
they need no per-site edit: the chokepoint covers them structurally. That
claim is only worth what a behavioural test says about it, so every one of
the thirteen is driven here on a limiter-routed double — twice: once
healthy (the control, which must actually send) and once muted (which must
send nothing and raise nothing).

The four that a per-coroutine harness finds awkward —
``_mirror_session_created``, ``_mirror_directory_created``,
``_mirror_preference_change`` and ``_mirror_session_preference_change`` —
are additionally driven END TO END through the Mini App's own HTTP routes,
which is how they actually fire in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot.flood import MUTE
from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

SCOPE_CHAT = -100
ADMIN_ID = 555
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
BAN = 34212.0


# ── the Mini App's auth plumbing, copied from tests/test_miniapp_*_api.py ──

@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _hdr(user_id=ADMIN_ID):
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, BOT_TOKEN)
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _Role:
    def __init__(self, bypass_safety=True, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    def get_role(self, name):
        return _Role()


@pytest.fixture
def server(mk_bot, gated_bot, tmp_path, monkeypatch):
    """A ``MiniAppServer`` whose bot is the LIMITER-ROUTED double.

    ``mk_bot`` binds ``bot._app.bot`` to a ``MagicMock``; leaving it there
    would make every assertion in this file pass for the wrong reason, so
    it is replaced before the server is built.
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(project))
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT, kind="group", label="team",
        members=(Member(id=ADMIN_ID, label="ada", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot = gated_bot
    gated_bot.username = "aipager_test_bot"

    seed = registry.get_or_create("claude-seed__g100")
    seed.label = "seed"
    seed.scope_chat_id = SCOPE_CHAT
    seed.status = Status.GONE
    seed.cwd = str(project)

    dev = registry.get_or_create("claude-dev__g100")
    dev.label = "dev"
    dev.scope_chat_id = SCOPE_CHAT
    dev.status = Status.IDLE
    dev.cwd = str(project)

    srv = MiniAppServer(bot, registry, port=8765)
    srv._project_dir = str(project)
    bot.create_session = AsyncMock(return_value=("claude-new__g100", ""))
    return srv


#: Every mirror named in entrypoints.md, with arguments that make it fire.
MIRRORS = [
    ("_mirror_session_created", (SCOPE_CHAT, "dev", False, "/tmp/x")),
    ("_mirror_directory_created", (SCOPE_CHAT, "/tmp/x/fresh")),
    ("_mirror_preference_change", (SCOPE_CHAT, ADMIN_ID, "layout", "merged")),
    ("_mirror_session_perms_switched", (SCOPE_CHAT, "dev", True)),
    ("_mirror_session_queue_cleared", (SCOPE_CHAT, "dev", 2)),
    ("_mirror_session_compacted", (SCOPE_CHAT, "dev")),
    ("_mirror_session_restarted", (SCOPE_CHAT, "dev")),
    ("_mirror_session_renamed", (SCOPE_CHAT, "dev", "dev2")),
    ("_mirror_session_stopped", (SCOPE_CHAT, "dev", 1)),
    ("_mirror_session_killed", (SCOPE_CHAT, "dev")),
    ("_mirror_session_resumed", (SCOPE_CHAT, "dev")),
    ("_mirror_session_deleted", (SCOPE_CHAT, "dev")),
]

_KWARGS = {"_mirror_session_preference_change": {"reset": False}}
MIRRORS.append(
    ("_mirror_session_preference_change",
     (SCOPE_CHAT, "dev", "answer_length", "short")))


@pytest.mark.parametrize("name,args", MIRRORS, ids=[m[0] for m in MIRRORS])
def test_every_mirror_actually_sends_when_the_chat_is_healthy(
    server, gated_bot, run_async, name, args,
):
    """The control for row B, one per coroutine.

    A mirror that silently sends nothing here (a renamed argument, an
    unmet precondition) would make its muted twin vacuous — which is
    exactly how a "covered" row ends up proving nothing.
    """
    run_async(getattr(server, name)(*args, **_KWARGS.get(name, {})))
    assert gated_bot.endpoints() == ["sendMessage"]
    assert gated_bot.calls[0][1] == SCOPE_CHAT


@pytest.mark.parametrize("name,args", MIRRORS, ids=[m[0] for m in MIRRORS])
def test_no_mirror_sends_anything_into_a_muted_chat(
    server, gated_bot, run_async, name, args,
):
    """Row B / criterion 2 — all thirteen, behaviourally."""
    MUTE.mute(SCOPE_CHAT, BAN)
    run_async(getattr(server, name)(*args, **_KWARGS.get(name, {})))
    assert gated_bot.calls == []


def test_the_thirteen_names_are_all_present(server):
    """If a mirror is renamed or a fourteenth appears, the row above stops
    covering the surface it claims to cover."""
    found = {n for n in dir(server) if n.startswith("_mirror_")}
    assert found == {name for name, _args in MIRRORS}


# ===== the same four, end to end through the Mini App's HTTP routes ======

async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _drive(run_async, srv, coro_factory):
    async def _run():
        client = await _client_for(srv)
        try:
            return await coro_factory(client)
        finally:
            await client.close()
    return run_async(_run())


def test_a_preference_write_over_http_mirrors_when_healthy(
    server, gated_bot, run_async,
):
    resp = _drive(run_async, server, lambda c: c.put(
        "/api/preferences/layout", headers=_hdr(), json={"value": "merged"}))
    assert resp.status == 200
    assert gated_bot.endpoints() == ["sendMessage"]


def test_a_preference_write_over_http_sends_nothing_while_muted(
    server, gated_bot, run_async,
):
    """``_mirror_preference_change`` as it actually fires: a PUT from the
    phone. The write must still succeed — the gate refuses the pixel, not
    the operation."""
    MUTE.mute(SCOPE_CHAT, BAN)
    resp = _drive(run_async, server, lambda c: c.put(
        "/api/preferences/layout", headers=_hdr(), json={"value": "merged"}))
    assert resp.status == 200
    assert gated_bot.calls == []


def test_a_session_preference_write_over_http_sends_nothing_while_muted(
    server, gated_bot, run_async,
):
    MUTE.mute(SCOPE_CHAT, BAN)
    resp = _drive(run_async, server, lambda c: c.put(
        "/api/sessions/dev/preferences/answer_length",
        headers=_hdr(), json={"value": "short"}))
    assert resp.status == 200
    assert gated_bot.calls == []


def test_creating_a_session_over_http_sends_nothing_while_muted(
    server, gated_bot, run_async,
):
    MUTE.mute(SCOPE_CHAT, BAN)
    resp = _drive(run_async, server, lambda c: c.post(
        "/api/sessions", headers=_hdr(), json={"name": "fresh"}))
    assert resp.status == 200
    assert gated_bot.calls == []


def test_creating_a_directory_over_http_sends_nothing_while_muted(
    server, gated_bot, run_async,
):
    MUTE.mute(SCOPE_CHAT, BAN)
    resp = _drive(run_async, server, lambda c: c.post(
        "/api/directories", headers=_hdr(),
        json={"parent": server._project_dir, "name": "announced"}))
    assert resp.status == 200
    assert gated_bot.calls == []


def test_creating_a_directory_over_http_mirrors_when_healthy(
    server, gated_bot, run_async,
):
    """The control for the four HTTP rows: the route really does mirror,
    so "no calls while muted" is about a path that runs."""
    resp = _drive(run_async, server, lambda c: c.post(
        "/api/directories", headers=_hdr(),
        json={"parent": server._project_dir, "name": "announced"}))
    assert resp.status == 200
    assert gated_bot.endpoints() == ["sendMessage"]
