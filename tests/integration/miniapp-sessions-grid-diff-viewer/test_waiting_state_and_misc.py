"""Black-box tests for:

  - the "waiting" state contract: a session in Status.INTERACTIVE must
    read "waiting" on both new routes regardless of pending_permission
    shape (including None -- the separate-message fallback, flagged in
    entrypoints.md's task brief as the case most likely to be wrong),
    while /api/status (stage 1, unchanged) still emits "interactive"
    verbatim.
  - timeline completeness on /api/sessions/{label}.
  - GET-only enforcement swept across all three new routes, asserting
    the mutating verb never returns 200 (not just "not 200/405-shaped"),
    as its own dedicated file/assertion independent of auth state.
  - GET / still serves HTML with no secret, unchanged by this stage.

Same disclosed methodology deviation as the other files in this
directory (MiniAppServer imported only to obtain a real bound socket;
every assertion is against the wire-level HTTP response).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import aiohttp
import pytest

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=5)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sign(fields: dict, bot_token: str) -> str:
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id: int, *, bot_token: str = BOT_TOKEN) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@asynccontextmanager
async def _running(server):
    await server.start()
    try:
        yield
    finally:
        await server.stop()


async def _get(base_url, path, headers=None):
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        async with session.get(f"{base_url}{path}", headers=headers) as resp:
            body = await resp.text()
            return resp.status, body


async def _request(base_url, method, path, headers=None):
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        async with session.request(method, f"{base_url}{path}", headers=headers) as resp:
            body = await resp.text()
            return resp.status, body


def _server_with_session(mk_bot, *, status, pending_permission):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = status
    sess.pending_permission = pending_permission
    sess.cwd = "/home/dev/myproject"
    port = _free_port()
    return MiniAppServer(bot, registry, port=port), f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------- #
# INTERACTIVE -> "waiting" on the new routes, across the three shapes   #
# of pending_permission documented/implied by entrypoints.md's          #
# waiting_kind column ("permission" | "question" | null)                #
# --------------------------------------------------------------------- #

INTERACTIVE_VARIANTS = [
    ("tool_permission", {"tool_summary": "Bash: rm -rf tmp/", "tool_info": {}}, "permission"),
    ("ask_question", {"ask_question": True, "question": "Which approach?"}, "question"),
    ("none_fallback", None, None),
]


@pytest.mark.parametrize(
    "variant_id,pending,expected_kind", INTERACTIVE_VARIANTS,
    ids=[v[0] for v in INTERACTIVE_VARIANTS],
)
def test_sessions_grid_shows_waiting_for_every_interactive_variant(
    mk_bot, run_async, variant_id, pending, expected_kind,
):
    server, base_url = _server_with_session(
        mk_bot, status=Status.INTERACTIVE, pending_permission=pending,
    )
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/sessions", {"X-Telegram-Init-Data": good})

    status, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    row = payload["sessions"][0]
    assert row["status"] == "waiting", (
        f"{variant_id}: INTERACTIVE with pending_permission={pending!r} "
        f"must read 'waiting', got {row['status']!r}"
    )
    assert row["status"] != "interactive"
    assert row["waiting_kind"] == expected_kind


@pytest.mark.parametrize(
    "variant_id,pending,expected_kind", INTERACTIVE_VARIANTS,
    ids=[v[0] for v in INTERACTIVE_VARIANTS],
)
def test_session_detail_shows_waiting_for_every_interactive_variant(
    mk_bot, run_async, variant_id, pending, expected_kind,
):
    server, base_url = _server_with_session(
        mk_bot, status=Status.INTERACTIVE, pending_permission=pending,
    )
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/sessions/dev", {"X-Telegram-Init-Data": good})

    status, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "waiting", (
        f"{variant_id}: /api/sessions/{{label}} must read 'waiting' for "
        f"INTERACTIVE with pending_permission={pending!r}, got "
        f"{payload['status']!r}"
    )
    assert payload["status"] != "interactive"
    assert payload["waiting_kind"] == expected_kind


def test_api_status_still_emits_interactive_verbatim_unchanged(mk_bot, run_async):
    """Stage 1's /api/status is explicitly unchanged per entrypoints.md
    -- it must still say "interactive", never "waiting"."""
    server, base_url = _server_with_session(
        mk_bot, status=Status.INTERACTIVE,
        pending_permission={"tool_summary": "Bash: rm -rf tmp/"},
    )
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": good})

    status, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    row = payload["sessions"][0]
    assert row["status"] == "interactive"
    assert row["status"] != "waiting"


# --------------------------------------------------------------------- #
# Timeline completeness                                                 #
# --------------------------------------------------------------------- #

def test_timeline_length_equals_tool_history_plus_commentary(mk_bot, run_async):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = Status.BUSY
    sess.cwd = "/home/dev/myproject"
    # Deliberately more rows than any chat-card truncation limit (a
    # chat card commonly caps visible tool rows) to prove the drill-down
    # timeline is NOT capped the way chat must be.
    sess.tool_history = [(f"tool {i}", True) for i in range(40)]
    sess.stream_commentary = [(i, f"commentary {i}") for i in range(0, 40, 2)]

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    base_url = f"http://127.0.0.1:{port}"
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/sessions/dev", {"X-Telegram-Init-Data": good})

    status, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    expected_len = len(sess.tool_history) + len(sess.stream_commentary)
    assert len(payload["timeline"]) == expected_len, (
        f"timeline has {len(payload['timeline'])} rows, expected "
        f"{expected_len} (= {len(sess.tool_history)} tool_history + "
        f"{len(sess.stream_commentary)} stream_commentary), nothing "
        f"should be capped"
    )


# --------------------------------------------------------------------- #
# GET-only enforcement (unauthenticated -- proves the verb is rejected  #
# at the route level, not merely masked by a 401)                       #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_new_routes_never_return_200_for_any_mutating_verb(mk_bot, run_async, path, method):
    server, base_url = _server_with_session(
        mk_bot, status=Status.IDLE, pending_permission=None,
    )

    async def _run():
        async with _running(server):
            return await _request(base_url, method, path)

    status, _body = run_async(_run())
    assert status != 200
    assert status in (404, 405)


def test_index_still_returns_html_no_secret_with_stage2_session_present(mk_bot, run_async):
    """GET / is unchanged by this stage -- re-verify with a stage-2-shaped
    (INTERACTIVE/waiting) session present, to catch any regression where
    the new session data leaked into the static shell."""
    server, base_url = _server_with_session(
        mk_bot, status=Status.INTERACTIVE,
        pending_permission={"tool_summary": "Bash: rm -rf tmp/ definitely-secret-marker"},
    )

    async def _run():
        async with _running(server):
            return await _get(base_url, "/")

    status, body = run_async(_run())
    assert status == 200
    assert "<html" in body.lower()
    assert BOT_TOKEN not in body
    assert "definitely-secret-marker" not in body
    assert "/home/dev/myproject" not in body
