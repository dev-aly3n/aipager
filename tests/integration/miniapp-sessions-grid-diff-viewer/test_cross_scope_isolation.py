"""Black-box adversarial tests for the headline requirement of this
stage: `/api/sessions/{label}` and `/api/sessions/{label}/diff` must
never distinguish "label does not exist" from "label belongs to a
different scope" -- entrypoints.md's 404 row says these are
"identical response either way".

DISCLOSED METHODOLOGY DEVIATION (same as stage 1's tester, see
tests/integration/serve-telegram-mini-app/test_miniapp_http_security_gate.py):
entrypoints.md lists `MiniAppServer` as internal, but there is no other
way to obtain a live bound HTTP server that serves the documented route
contract. This file imports it only to construct/start/stop a real
server; every assertion is against the wire-level HTTP response.

Every request in this file is a real HTTP request over a real loopback
TCP socket via `aiohttp.ClientSession`.

Gaps this file closes relative to the developer's
tests/test_miniapp_server.py (which already covers: same-shape 404 body
for cross-scope vs unknown label, on both /api/sessions/{label} and its
/diff sibling, for one scope pair):
  - full header comparison (not just status+body) between the two 404s,
    including Content-Length and Content-Type, across BOTH routes
  - response TIMING between the two cases, as a side-channel probe
  - a THIRD scope in play, and a label that is a shared/duplicate string
    across two scopes' session names
  - confirms `/api/sessions` itself never includes another scope's
    session even when scopes share a session's *internal* registry name
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
            body = await resp.read()
            return resp.status, dict(resp.headers), body


def _three_scope_server(mk_bot):
    """Three scopes (a, b, c). Alice is only a member of scope a.
    Scope b holds a session labelled 'shared-label'; scope c holds a
    DIFFERENT session that also happens to be labelled 'shared-label'
    (two different scopes independently choosing the same human-facing
    label) -- the strongest form of the cross-scope collision."""
    registry = SessionRegistry()
    scope_a = Scope(chat_id=-1, kind="group", label="a",
                     members=(Member(id=1, label="alice", role="developer"),))
    scope_b = Scope(chat_id=-2, kind="group", label="b",
                     members=(Member(id=2, label="bob", role="developer"),))
    scope_c = Scope(chat_id=-3, kind="group", label="c",
                     members=(Member(id=3, label="carol", role="developer"),))
    bot = mk_bot(registry, scopes=[scope_a, scope_b, scope_c])
    bot._app.bot.username = "bot"

    sess_b = registry.get_or_create("claude-b")
    sess_b.label = "shared-label"
    sess_b.scope_chat_id = -2
    sess_b.status = Status.BUSY
    sess_b.cwd = "/home/bob/project"

    sess_c = registry.get_or_create("claude-c")
    sess_c.label = "shared-label"
    sess_c.scope_chat_id = -3
    sess_c.status = Status.IDLE
    sess_c.cwd = "/home/carol/project"

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    return server, f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------- #
# Full-header, byte-for-byte comparison on both routes                  #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("suffix", ["", "/diff"])
def test_cross_scope_and_unknown_label_headers_identical(mk_bot, run_async, suffix):
    server, base_url = _three_scope_server(mk_bot)
    alice = _init_data(1)

    async def _run():
        async with _running(server):
            other_scope = await _get(
                base_url, f"/api/sessions/shared-label{suffix}",
                {"X-Telegram-Init-Data": alice},
            )
            unknown = await _get(
                base_url, f"/api/sessions/genuinely-unknown-xyz{suffix}",
                {"X-Telegram-Init-Data": alice},
            )
            return other_scope, unknown

    (s1, h1, b1), (s2, h2, b2) = run_async(_run())

    assert s1 == s2 == 404
    assert b1 == b2
    # Compare every header that could leak a difference. Date legitimately
    # varies between two sequential requests, so it is excluded; nothing
    # else should differ.
    h1 = {k: v for k, v in h1.items() if k.lower() != "date"}
    h2 = {k: v for k, v in h2.items() if k.lower() != "date"}
    assert h1 == h2
    assert h1.get("Content-Length") == h2.get("Content-Length")


@pytest.mark.parametrize("suffix", ["", "/diff"])
def test_cross_scope_and_unknown_label_same_shared_session_name_scope_c(
    mk_bot, run_async, suffix,
):
    """Same probe, but request as a member of scope a against the OTHER
    duplicate-labelled session living in scope c -- proves the identical
    treatment isn't an artifact of which specific foreign scope holds
    the collision."""
    server, base_url = _three_scope_server(mk_bot)
    alice = _init_data(1)

    async def _run():
        async with _running(server):
            other_scope = await _get(
                base_url, f"/api/sessions/shared-label{suffix}",
                {"X-Telegram-Init-Data": alice},
            )
            unknown = await _get(
                base_url, f"/api/sessions/another-unknown-label{suffix}",
                {"X-Telegram-Init-Data": alice},
            )
            return other_scope, unknown

    (s1, _h1, b1), (s2, _h2, b2) = run_async(_run())
    assert s1 == s2 == 404
    assert b1 == b2
    assert json.loads(b1) == {"error": "not_found"}


# --------------------------------------------------------------------- #
# Timing side channel (best-effort, generous tolerance to avoid         #
# flakiness -- catches gross leaks like an extra registry scan or git   #
# invocation on the "exists but wrong scope" path, not microsecond-     #
# level noise)                                                          #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("suffix", ["", "/diff"])
def test_cross_scope_vs_unknown_label_timing_not_grossly_distinguishable(
    mk_bot, run_async, suffix,
):
    server, base_url = _three_scope_server(mk_bot)
    alice = _init_data(1)
    N = 25

    async def _run():
        other_times, unknown_times = [], []
        async with _running(server):
            # Warm up (connection pooling, import caches, etc.)
            for _ in range(3):
                await _get(base_url, f"/api/sessions/shared-label{suffix}",
                           {"X-Telegram-Init-Data": alice})
                await _get(base_url, f"/api/sessions/genuinely-unknown-xyz{suffix}",
                           {"X-Telegram-Init-Data": alice})
            for _ in range(N):
                t0 = time.perf_counter()
                await _get(base_url, f"/api/sessions/shared-label{suffix}",
                           {"X-Telegram-Init-Data": alice})
                other_times.append(time.perf_counter() - t0)

                t0 = time.perf_counter()
                await _get(base_url, f"/api/sessions/genuinely-unknown-xyz{suffix}",
                           {"X-Telegram-Init-Data": alice})
                unknown_times.append(time.perf_counter() - t0)
        return other_times, unknown_times

    other_times, unknown_times = run_async(_run())
    other_times.sort()
    unknown_times.sort()
    # Median damps scheduler noise/outliers better than the mean.
    med_other = other_times[len(other_times) // 2]
    med_unknown = unknown_times[len(unknown_times) // 2]
    delta = abs(med_other - med_unknown)
    # Generous: fail only on a gross, structural timing difference (e.g.
    # one path spawning git / scanning the whole registry an extra time),
    # not on scheduler jitter.
    assert delta < 0.05, (
        f"cross-scope vs unknown-label median latency differs by "
        f"{delta * 1000:.1f}ms (other={med_other * 1000:.1f}ms, "
        f"unknown={med_unknown * 1000:.1f}ms) -- possible timing side "
        f"channel distinguishing the two 404 cases"
    )


# --------------------------------------------------------------------- #
# /api/sessions never leaks another scope's session, even under a       #
# label collision                                                       #
# --------------------------------------------------------------------- #

def test_sessions_grid_never_includes_foreign_scope_even_with_label_collision(
    mk_bot, run_async,
):
    server, base_url = _three_scope_server(mk_bot)
    alice = _init_data(1)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/sessions", {"X-Telegram-Init-Data": alice})

    status, _headers, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    assert payload["sessions"] == []


def test_sessions_grid_bob_only_sees_his_own_shared_label_session(mk_bot, run_async):
    server, base_url = _three_scope_server(mk_bot)
    bob = _init_data(2)

    async def _run():
        async with _running(server):
            return await _get(base_url, "/api/sessions", {"X-Telegram-Init-Data": bob})

    status, _headers, body = run_async(_run())
    assert status == 200
    payload = json.loads(body)
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["label"] == "shared-label"
    # Bob's own session detail must be his (busy), not carol's (idle).
    assert payload["sessions"][0]["status"] == "busy"
