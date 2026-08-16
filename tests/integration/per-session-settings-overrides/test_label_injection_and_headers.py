"""Black-box adversarial tests for the NEW session-preference routes'
label handling: `/api/sessions/{label}/preferences` and
`/api/sessions/{label}/preferences/{field}` (GET/PUT/DELETE).

entrypoints.md's 404 row promises: "404 label not found in caller's own
scope (identical body/status to a label that doesn't exist anywhere)" —
for ALL THREE new routes. It also promises the label-injection battery
that an earlier stage's tester already built for the sibling
`/api/sessions/{label}` and `/diff` routes
(tests/integration/miniapp-sessions-grid-diff-viewer/test_label_and_path_injection.py)
applies equally here, since the spec's Security requirements section
says "A session-scoped route additionally must resolve the label only
within the caller's own scope ... exactly as the existing session
routes do." The developer's own test_miniapp_session_preferences_api.py
covers cross-scope 404 with only two labels ("ghost-does-not-exist",
"other") and compares status+body only, never headers, and never runs
the wider injection payload set. This file closes both gaps for the
preferences routes specifically.

DISCLOSED METHODOLOGY DEVIATION (same as the earlier stage's tester):
entrypoints.md lists `MiniAppServer` as internal, but there is no other
way to obtain a live bound HTTP server that serves the documented route
contract. This file imports it only to construct/start/stop a real
server; every assertion is against the wire-level HTTP response.

Every request here is a real HTTP request over a real loopback TCP
socket via `aiohttp.ClientSession` (port 0 -> OS-assigned ephemeral
port, never a fixed or non-loopback bind).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

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


async def _req(base_url, method, raw_path, headers=None, json_body=None):
    """Issue a request with an already-encoded path, bypassing any
    client-side re-encoding, so percent-escapes reach the server
    verbatim -- same technique as the sibling-route injection tests."""
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        kwargs = {"allow_redirects": False}
        if json_body is not None:
            kwargs["json"] = json_body
        async with session.request(
            method, f"{base_url}{raw_path}", headers=headers, **kwargs,
        ) as resp:
            body = await resp.read()
            return resp.status, dict(resp.headers), body


@pytest.fixture
def live_server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="admin"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = Status.IDLE
    sess.cwd = "/home/dev/myproject"

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    return server, f"http://127.0.0.1:{port}"


def _three_scope_server(mk_bot):
    """Alice is a member of scope a only. Scope b holds a session
    labelled 'shared-label' -- the cross-scope collision case."""
    registry = SessionRegistry()
    scope_a = Scope(chat_id=-1, kind="group", label="a",
                     members=(Member(id=1, label="alice", role="admin"),))
    scope_b = Scope(chat_id=-2, kind="group", label="b",
                     members=(Member(id=2, label="bob", role="admin"),))
    bot = mk_bot(registry, scopes=[scope_a, scope_b])
    bot._app.bot.username = "bot"

    sess_b = registry.get_or_create("claude-b")
    sess_b.label = "shared-label"
    sess_b.scope_chat_id = -2
    sess_b.status = Status.IDLE
    sess_b.cwd = "/home/bob/project"

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    return server, f"http://127.0.0.1:{port}"


INJECTION_LABELS = [
    ("dot-dot-encoded-slash", "..%2F..%2Fetc%2Fpasswd"),
    ("double-encoded-traversal", "%252e%252e%252fetc%252fpasswd"),
    ("absolute-path-encoded", "%2Fetc%2Fpasswd"),
    ("nul-byte", "dev%00"),
    ("shell-metachar-semicolon", "dev%3Brm%20-rf%20%2F"),
    ("unicode-emoji", quote("label-\U0001F600", safe="")),
    ("very-long-label", "x" * 8000),
    ("home-tilde", "~"),
    # NOTE: a literal, unencoded ".." segment is deliberately excluded.
    # yarl (aiohttp's URL type) performs RFC 3986 dot-segment removal
    # client-side before the request is ever sent -- confirmed directly:
    # URL(".../api/sessions/../preferences/x") normalizes to
    # ".../api/preferences/x" purely as a client-side string operation,
    # the same normalization any HTTP client/browser would perform. That
    # reaches a real, distinctly-and-properly-gated sibling route
    # (`PUT /api/preferences/{field}`), not a bug in this route's label
    # handling -- so it would be a misleading "finding" here. The
    # percent-encoded traversal variants above (which stay opaque to
    # dot-segment normalization) are the real attack surface and are
    # covered.
    ("newline-encoded", "dev%0Aid"),
    ("sql-ish", "dev%27%20OR%20%271%27%3D%271"),
]


# --------------------------------------------------------------------- #
# Injection battery x GET/PUT/DELETE on the new preferences routes      #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("PUT", "/answer_length", {"value": "short"}),
    ("DELETE", "/answer_length", None),
])
@pytest.mark.parametrize(
    "case_id,payload", INJECTION_LABELS, ids=[c for c, _ in INJECTION_LABELS],
)
def test_injection_label_never_500_never_200_on_preferences_routes(
    live_server, run_async, case_id, payload, method, suffix, body,
):
    server, base_url = live_server
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _req(
                base_url, method, f"/api/sessions/{payload}/preferences{suffix}",
                {"X-Telegram-Init-Data": good}, json_body=body,
            )

    status, _headers, raw_body = run_async(_run())
    text = raw_body.decode(errors="replace")
    assert status < 500, f"{case_id} {method}: server error {status}, body={text!r}"
    assert status != 200, f"{case_id} {method}: unexpectedly succeeded, body={text!r}"
    assert "root:" not in text
    assert "Traceback" not in text


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("PUT", "/answer_length", {"value": "short"}),
    ("DELETE", "/answer_length", None),
])
@pytest.mark.parametrize(
    "case_id,payload", INJECTION_LABELS, ids=[c for c, _ in INJECTION_LABELS],
)
def test_injection_label_did_not_actually_write_anything(
    live_server, run_async, case_id, payload, method, suffix, body,
):
    """However the injection payload is routed (rejected, 404'd, or
    router-swallowed), it must never leave a mark on the ONE real
    session's preferences. Checked by reading that session's own
    preferences back afterward as the legitimate owner."""
    server, base_url = live_server
    good = _init_data(555)

    async def _run():
        async with _running(server):
            await _req(
                base_url, method, f"/api/sessions/{payload}/preferences{suffix}",
                {"X-Telegram-Init-Data": good}, json_body=body,
            )
            return await _req(
                base_url, "GET", "/api/sessions/dev/preferences",
                {"X-Telegram-Init-Data": good},
            )

    status, _headers, raw_body = run_async(_run())
    assert status == 200
    values = json.loads(raw_body)["values"]
    assert values["answer_length"]["overridden"] is False, (
        f"{case_id} {method}: injection payload against an unrelated/"
        f"unresolved label mutated the real session's preferences"
    )


# --------------------------------------------------------------------- #
# Full-header, byte-for-byte 404 comparison: cross-scope vs unknown     #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("PUT", "/answer_length", {"value": "short"}),
    ("DELETE", "/answer_length", None),
])
def test_cross_scope_and_unknown_label_headers_identical(
    mk_bot, run_async, method, suffix, body,
):
    server, base_url = _three_scope_server(mk_bot)
    alice = _init_data(1)

    async def _run():
        async with _running(server):
            other_scope = await _req(
                base_url, method, f"/api/sessions/shared-label/preferences{suffix}",
                {"X-Telegram-Init-Data": alice}, json_body=body,
            )
            unknown = await _req(
                base_url, method,
                f"/api/sessions/genuinely-unknown-xyz/preferences{suffix}",
                {"X-Telegram-Init-Data": alice}, json_body=body,
            )
            return other_scope, unknown

    (s1, h1, b1), (s2, h2, b2) = run_async(_run())

    assert s1 == s2 == 404
    assert b1 == b2
    assert json.loads(b1) == {"error": "not_found"}
    # Date legitimately varies between two sequential requests; nothing
    # else may differ -- a difference here (e.g. a distinct Content-Length
    # from a slightly different error message) would be an observable
    # side channel distinguishing "wrong scope" from "doesn't exist".
    h1 = {k: v for k, v in h1.items() if k.lower() != "date"}
    h2 = {k: v for k, v in h2.items() if k.lower() != "date"}
    assert h1 == h2
    assert h1.get("Content-Length") == h2.get("Content-Length")
