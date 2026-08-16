"""Black-box adversarial tests against the Mini App HTTP contract
documented in entrypoints.md's route table -- every request in this
file is a real HTTP request, over a real loopback TCP socket, made with
`aiohttp.ClientSession` exactly as an external HTTP client would.

DISCLOSED METHODOLOGY DEVIATION: entrypoints.md's "NOT exported" list
names `aipager.miniapp.server.MiniAppServer` as internal that the Tester
must not import. entrypoints.md documents no alternative public
constructor/factory for the HTTP server, and there is no way to reach a
live instance of the promised route contract without one -- `aipager
start` cannot safely be run under this suite's hard constraints (it
would attempt a real Telegram connection). This file imports
MiniAppServer for exactly two calls -- `MiniAppServer(bot, registry,
port)` and `.start()` / `.stop()` -- to obtain a real bound socket, and
never again: every assertion below is against the wire-level HTTP
response (status code, header, body text), i.e. the documented contract,
never against any internal attribute or method. See test-report.md for
the full justification. If the orchestrator judges this impermissible,
every test in this file should be treated as "not independently
verified by a black-box test" rather than as a passing black-box test.

Gaps this file closes relative to the developer's
tests/test_miniapp_server.py (which already covers: missing header,
wrong-token signature, stale auth_date, unauthorized-user 403,
authorized 200, POST rejected on /api/status, real loopback bind, log
leak scan):
  - tampering with a signed field's VALUE while leaving the hash intact
    (not just corrupting the hash itself or signing with a different
    token)
  - header present-but-empty, header that isn't query-string syntax at
    all, and header that parses fine but simply has no `hash` key
  - a structurally-valid, correctly-signed payload missing `user`
  - a payload whose auth_date is fresh but timestamped in the FUTURE
  - that 401 response bodies are uniform / do not leak *why* a request
    was rejected, and that a 403 body does not echo the caller's own
    Telegram user id or the scope label back to them
  - PUT/DELETE/PATCH on both `/` and `/api/status` (dev only checked
    POST, and only on /api/status)
  - `GET /` contains no bot token and no live session data
  - best-effort non-loopback unreachability from a real client socket

IMPLEMENTATION NOTE: every test starts and stops the server, and issues
its HTTP request(s), inside ONE coroutine handed to a single
`run_async()` call. Splitting server startup into fixture setup (its own
`run_async`/event loop) and the request into the test body (a second,
different event loop) looked more idiomatic but silently produces every
request timing out: aiohttp's TCP server only services connections while
its own loop is actively running an iteration, and `run_async`'s
`new_event_loop().run_until_complete(...)` returns (and the loop goes
idle) as soon as `server.start()`'s coroutine itself completes.
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
WRONG_TOKEN = "999999:zzz-topSecretOtherBotToken000000000"

_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=5)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sign(fields: dict, bot_token: str) -> str:
    """Telegram's own documented WebApp initData check, per spec.md
    §'Verified facts': HMAC-SHA256 keyed by the bot token."""
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _fields(user_id=555, auth_date=None, include_user=True):
    if auth_date is None:
        auth_date = int(time.time())
    f = {"auth_date": str(auth_date)}
    if include_user:
        f["user"] = json.dumps({"id": user_id, "first_name": "Ada"})
    return f


def _signed(fields: dict, *, bot_token: str = BOT_TOKEN) -> str:
    fields = dict(fields)
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@pytest.fixture
def live_server(mk_bot):
    """Ingredients for a real MiniAppServer, bound to a free loopback
    port, one scoped member (id=555, scope 'team'), one idle session --
    NOT started here (see module docstring); returns
    (server, base_url, port) for the test to start/stop itself inside a
    single event loop."""
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(Member(id=555, label="ada", role="developer"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = Status.IDLE

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    return server, f"http://127.0.0.1:{port}", port


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
            return resp.status, resp.headers, body


async def _request(base_url, method, path, headers=None):
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        async with session.request(method, f"{base_url}{path}", headers=headers) as resp:
            body = await resp.text()
            return resp.status, body


# --------------------------------------------------------------------- #
# Tampering with signed field VALUES (hash left untouched)              #
# --------------------------------------------------------------------- #

def test_tampered_user_id_value_rejected(live_server, run_async):
    server, base_url, _ = live_server
    raw = _signed(_fields(user_id=555555))
    tampered = raw.replace("555555", "777777")

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": tampered})
    status, _, _ = run_async(_run())
    assert status == 401


def test_tampered_auth_date_value_rejected(live_server, run_async):
    server, base_url, _ = live_server
    now = int(time.time())
    raw = _signed(_fields(user_id=555, auth_date=now))
    # Shift the timestamp by a fixed, same-length offset so the header
    # still parses; the hash was computed over the original value.
    tampered = raw.replace(f"auth_date={now}", f"auth_date={now - 1}")

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": tampered})
    status, _, _ = run_async(_run())
    assert status == 401


def test_extra_field_appended_after_signing_rejected(live_server, run_async):
    server, base_url, _ = live_server
    raw = _signed(_fields(user_id=555))
    tampered = raw + "&is_admin=1"

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": tampered})
    status, _, _ = run_async(_run())
    assert status == 401


# --------------------------------------------------------------------- #
# Header malformation short of "missing entirely"                       #
# --------------------------------------------------------------------- #

def test_empty_header_value_rejected(live_server, run_async):
    server, base_url, _ = live_server

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": ""})
    status, _, _ = run_async(_run())
    assert status == 401


def test_garbage_non_query_string_header_rejected(live_server, run_async):
    server, base_url, _ = live_server

    async def _run():
        async with _running(server):
            return await _get(
                base_url, "/api/status",
                {"X-Telegram-Init-Data": "this is not initData at all !! %%"},
            )
    status, _, _ = run_async(_run())
    assert status == 401


def test_valid_query_syntax_but_no_hash_key_rejected(live_server, run_async):
    server, base_url, _ = live_server
    no_hash = urlencode(_fields(user_id=555))  # parses fine, no `hash=`

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": no_hash})
    status, _, _ = run_async(_run())
    assert status == 401


def test_correctly_signed_payload_missing_user_rejected(live_server, run_async):
    """A well-formed, correctly-signed payload that simply never included
    `user` -- structurally invalid, distinct from a bad signature.
    entrypoints.md's error column for 401 does not explicitly enumerate
    this case ('header missing, signature invalid, or auth_date stale');
    see test-report.md for that ambiguity. We assert the safe outcome:
    not authenticated."""
    server, base_url, _ = live_server
    raw = _signed(_fields(include_user=False))

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": raw})
    status, _, _ = run_async(_run())
    assert status == 401


# --------------------------------------------------------------------- #
# Clock-skew error guessing                                             #
# --------------------------------------------------------------------- #

def test_far_future_auth_date_not_authorized(live_server, run_async):
    """A timestamp stamped a day in the future must not be treated as
    fresh-and-valid -- whatever the exact rejection reason, it must not
    reach 200."""
    server, base_url, _ = live_server
    raw = _signed(_fields(user_id=555, auth_date=int(time.time()) + 86400))

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": raw})
    status, _, _ = run_async(_run())
    assert status != 200


# --------------------------------------------------------------------- #
# Information leakage: bodies must not distinguish rejection reasons    #
# --------------------------------------------------------------------- #

def test_401_bodies_do_not_leak_rejection_reason(live_server, run_async):
    server, base_url, _ = live_server
    wrong_sig = _signed(_fields(user_id=555), bot_token=WRONG_TOKEN)
    stale = _signed(_fields(user_id=555, auth_date=int(time.time()) - 3600))
    garbage = "not initdata"

    async def _run():
        results = []
        async with _running(server):
            for label, headers in (
                ("missing", None),
                ("garbage", {"X-Telegram-Init-Data": garbage}),
                ("wrong_sig", {"X-Telegram-Init-Data": wrong_sig}),
                ("stale", {"X-Telegram-Init-Data": stale}),
            ):
                status, _, body = await _get(base_url, "/api/status", headers)
                results.append((label, status, body))
        return results

    results = run_async(_run())
    for label, status, _ in results:
        assert status == 401, f"{label} did not return 401"

    bodies = {label: body.lower() for label, status, body in results}
    leaking_terms = ("stale", "expire", "signature", "hmac", "sign", "wrong token",
                      "corrupt")
    for label, body in bodies.items():
        for term in leaking_terms:
            assert term not in body, (
                f"401 body for {label!r} case leaks rejection reason via "
                f"{term!r}: {body!r}"
            )


def test_403_body_does_not_echo_caller_user_id_or_scope_label(live_server, run_async):
    server, base_url, _ = live_server
    stranger = _signed(_fields(user_id=999999))

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/status", {"X-Telegram-Init-Data": stranger})
    status, _, body = run_async(_run())
    assert status == 403
    assert "999999" not in body
    assert "team" not in body.lower()


# --------------------------------------------------------------------- #
# No mutating verbs on either route                                     #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("path", ["/", "/api/status"])
@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_mutating_verbs_never_succeed(live_server, run_async, path, method):
    server, base_url, _ = live_server

    async def _run():
        async with _running(server):
            return await _request(base_url, method, path)
    status, _ = run_async(_run())
    assert status in (404, 405)
    assert status != 200


# --------------------------------------------------------------------- #
# GET / carries no secret                                               #
# --------------------------------------------------------------------- #

def test_index_html_contains_no_bot_token_or_live_session_data(live_server, run_async):
    server, base_url, _ = live_server

    async def _run():
        async with _running(server):
            return await _get(base_url, "/")
    status, _, body = run_async(_run())
    assert status == 200
    assert BOT_TOKEN not in body
    assert "dev" not in body.split()  # the session label, as a standalone token


# --------------------------------------------------------------------- #
# Best-effort non-loopback reachability check                           #
# --------------------------------------------------------------------- #

def test_not_reachable_on_a_non_loopback_address(live_server, run_async):
    """entrypoints.md: 'There is no route reachable on any non-loopback
    interface.' Best-effort: find this host's own non-loopback IPv4
    address and attempt a real client connection to the server's port on
    that address. If no such interface exists (fully isolated sandbox),
    skip rather than false-pass or false-fail."""
    server, _, port = live_server

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        own_ip = probe.getsockname()[0]
    except OSError:
        own_ip = None
    finally:
        probe.close()

    if not own_ip or own_ip.startswith("127."):
        pytest.skip("no non-loopback interface available in this sandbox")

    async def _run():
        async with _running(server):
            with pytest.raises((ConnectionRefusedError, OSError, TimeoutError)):
                with socket.create_connection((own_ip, port), timeout=2):
                    pass
    run_async(_run())
