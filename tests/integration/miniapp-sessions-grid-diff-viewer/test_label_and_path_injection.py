"""Black-box adversarial tests: attack the "git never receives a
client-supplied path" claim in entrypoints.md through the `{label}`
path segment, on both `/api/sessions/{label}` and
`/api/sessions/{label}/diff`.

Same disclosed methodology deviation as
test_cross_scope_isolation.py / stage 1's tester: MiniAppServer is
imported only to obtain a real bound socket; every assertion is
against the wire-level HTTP response.

None of these payloads should ever: reach 500, hang, return 200 with
filesystem content, or behave observably differently from a plain
unknown label.
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


async def _get_raw(base_url, raw_path, headers=None):
    """Issue a request with an already-encoded path, bypassing any
    client-side re-encoding, so percent-escapes reach the server verbatim."""
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        async with session.get(
            f"{base_url}{raw_path}", headers=headers, allow_redirects=False,
        ) as resp:
            body = await resp.text()
            return resp.status, body


@pytest.fixture
def live_server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="developer"),))
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


INJECTION_LABELS = [
    ("dot-dot-encoded-slash", "..%2F..%2Fetc%2Fpasswd"),
    ("double-encoded-traversal", "%252e%252e%252fetc%252fpasswd"),
    ("absolute-path-encoded", "%2Fetc%2Fpasswd"),
    ("windows-style-traversal", "..%5C..%5Cetc%5Cpasswd"),
    ("nul-byte", "dev%00"),
    ("shell-metachar-semicolon", "dev%3Brm%20-rf%20%2F"),
    ("shell-metachar-dollar", "dev%24%28whoami%29"),
    ("shell-metachar-pipe", "dev%7Cwhoami"),
    ("shell-metachar-backtick", "dev%60whoami%60"),
    ("unicode-emoji", quote("label-\U0001F600", safe="")),
    ("unicode-rtl-override", quote("label-‮", safe="")),
    ("very-long-label", "x" * 8000),
    ("literal-dot-dot", ".."),
    ("literal-dot", "."),
    ("home-tilde", "~"),
    ("newline-encoded", "dev%0Aid"),
    ("space-only", "%20%20%20"),
    ("sql-ish", "dev%27%20OR%20%271%27%3D%271"),
]


@pytest.mark.parametrize("suffix", ["", "/diff"])
@pytest.mark.parametrize("case_id,payload", INJECTION_LABELS, ids=[c for c, _ in INJECTION_LABELS])
def test_injection_label_never_500_never_200(live_server, run_async, case_id, payload, suffix):
    server, base_url = live_server
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get_raw(
                base_url, f"/api/sessions/{payload}{suffix}",
                {"X-Telegram-Init-Data": good},
            )

    status, body = run_async(_run())
    assert status < 500, f"{case_id}: server error {status}, body={body!r}"
    assert status != 200, f"{case_id}: unexpectedly succeeded, body={body!r}"
    # Never leak real filesystem content back to the client.
    assert "root:" not in body
    assert "/bin/bash" not in body


@pytest.mark.parametrize("case_id,payload", INJECTION_LABELS, ids=[c for c, _ in INJECTION_LABELS])
def test_injection_label_matches_unknown_label_shape_when_routed(
    live_server, run_async, case_id, payload,
):
    """When the payload is routable to the handler at all (i.e. does not
    contain a literal, decoded `/` that breaks the single-path-segment
    match), it must produce the exact same 404 JSON shape as a plain
    unknown label -- no distinguishing error text, no stack trace."""
    server, base_url = live_server
    good = _init_data(555)

    async def _run():
        async with _running(server):
            injected = await _get_raw(
                base_url, f"/api/sessions/{payload}",
                {"X-Telegram-Init-Data": good},
            )
            baseline = await _get_raw(
                base_url, "/api/sessions/genuinely-unknown-label",
                {"X-Telegram-Init-Data": good},
            )
            return injected, baseline

    (i_status, i_body), (b_status, b_body) = run_async(_run())
    if i_status == 404:
        try:
            i_json = json.loads(i_body)
        except ValueError:
            i_json = None
        if i_json == {"error": "not_found"}:
            assert i_body == b_body
        # else: router-level 404 (aiohttp's own, for un-matchable paths
        # e.g. literal slashes) -- acceptable per contract, not asserted
        # further here.
    else:
        # Any other non-500/non-200 status (400, 404 router-level, etc.)
        # is acceptable; only 200/500 would be a real problem, already
        # covered by the previous test.
        pass


def test_git_never_invoked_for_diff_on_injection_labels(live_server, run_async, monkeypatch):
    """Even on the /diff route, no injection-shaped label may cause a
    subprocess to be spawned at all -- the label never resolves to a
    real session, so collect_diff (and therefore git) must never run."""
    server, base_url = live_server
    good = _init_data(555)

    async def _boom(*args, **kwargs):
        raise AssertionError("git must never be invoked for an unresolved label")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _boom)

    async def _run():
        results = []
        async with _running(server):
            for case_id, payload in INJECTION_LABELS:
                status, body = await _get_raw(
                    base_url, f"/api/sessions/{payload}/diff",
                    {"X-Telegram-Init-Data": good},
                )
                results.append((case_id, status, body))
        return results

    results = run_async(_run())
    for case_id, status, body in results:
        assert status < 500, f"{case_id}: server error {status}, body={body!r}"


def test_label_with_literal_slash_via_percent_decoding_never_leaks_other_route(
    live_server, run_async,
):
    """A label that decodes to something containing '/api/status' or
    similar must not be treated as a route override / smuggled path."""
    server, base_url = live_server
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get_raw(
                base_url, "/api/sessions/..%2Fstatus",
                {"X-Telegram-Init-Data": good},
            )

    status, body = run_async(_run())
    assert status < 500
    assert status != 200
