"""Hardening of the Mini App's public surface.

Three guards, each closing something a caller who merely reaches the port
could do. The server binds loopback but is designed to sit behind a
tunnel, so "reaches the port" means "reaches the internet".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp import server as srv_mod
from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status


BOT_TOKEN = "123456:test-bot-token"


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


@pytest.fixture
def hardening_server(mk_bot):
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
    sess.cwd = "/home/dev/myproject"
    return MiniAppServer(bot, registry, port=8765)


async def _client_for(server: MiniAppServer):
    client = TestClient(TestServer(server._build_app()))
    await client.start_server()
    return client


# ===== 1. the auth gate must fail CLOSED on anything unforeseen ============

def test_non_ascii_hash_is_a_401_not_a_500(hardening_server, run_async):
    """hmac.compare_digest raises TypeError for a non-ASCII `hash`, which
    the caller fully controls. Before the fail-closed except, that escaped
    to aiohttp as a 500 with a traceback — an unauthenticated, unbounded
    error/log amplifier. Reproduced against the real code before fixing.
    """
    async def _run():
        client = await _client_for(hardening_server)
        try:
            resp = await client.get(
                "/api/status",
                headers={"X-Telegram-Init-Data": "auth_date=1&user=%7B%7D&hash=é"},
            )
            assert resp.status == 401, f"expected 401, got {resp.status}"
            assert await resp.json() == {"error": "unauthorized"}
        finally:
            await client.close()
    run_async(_run())


def test_unforeseen_verify_error_still_refuses(hardening_server, run_async, monkeypatch):
    """Any future exception class out of verify_init_data must refuse, not
    leak a 500. Pinned with an injected error so the guard cannot rot back
    to catching only the three known ones."""
    def _boom(*_a, **_k):
        raise RuntimeError("something nobody predicted")
    monkeypatch.setattr("aipager.miniapp.auth.verify_init_data", _boom)

    async def _run():
        client = await _client_for(hardening_server)
        try:
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": "x=1"},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


# ===== 2. rejection logging is budgeted, rejections are not ===============

def test_auth_failure_logging_is_capped_and_reports_suppression(
    hardening_server, run_async, caplog, monkeypatch,
):
    """The flood amplifier is the logging, not the crypto: one log.info per
    rejection plus an access-log line, both synchronous writes from inside
    the single event loop shared with Telegram polling. Every request is
    still rejected; only the log writes are capped.

    The cap is monkeypatched to a small number and the request count is a
    FIXED literal. An earlier version derived the loop count from the
    constant itself, so raising the constant to "disable the guard" simply
    made the test attempt a million requests and hang — a test that scales
    with the value it is meant to pin cannot detect that value changing.
    """
    monkeypatch.setattr(srv_mod, "_AUTH_LOG_MAX_PER_WINDOW", 3)
    REQUESTS = 15

    async def _run():
        client = await _client_for(hardening_server)
        try:
            return [(await client.get("/api/status")).status
                    for _ in range(REQUESTS)]
        finally:
            await client.close()

    with caplog.at_level(logging.INFO, logger="aipager.miniapp.server"):
        statuses = run_async(_run())

    # Every single one is still refused — throughput is not traded away.
    assert statuses == [401] * REQUESTS
    rejected = [r for r in caplog.records if "rejected (401)" in r.getMessage()]
    assert len(rejected) <= 3, f"{len(rejected)} log lines for {REQUESTS} rejections"
    assert hardening_server._auth_log_suppressed == REQUESTS - len(rejected)

    # The suppressed count surfaces once the window has room again.
    hardening_server._auth_log_hits = []
    with caplog.at_level(logging.WARNING, logger="aipager.miniapp.server"):
        hardening_server._log_auth_failure("/api/status", "bad signature")
    assert any("suppressed" in r.getMessage() for r in caplog.records)
    assert hardening_server._auth_log_suppressed == 0


# ===== 3. the 103-subprocess route is serialised ==========================

def test_diff_refuses_a_concurrent_second_caller(hardening_server, run_async, monkeypatch):
    """/diff shells out to up to 103 git subprocesses per call. A caller
    that cannot get a slot within the bounded wait is refused (429), so the
    route cannot be looped into a fork bomb against the event loop that also
    runs Telegram polling. A merely-concurrent second viewer queues and
    succeeds — that is a different test, in the diff-viewer integration
    suite.

    Drives the real HTTP route with real (test-signed) initData and a
    collect_diff that blocks until released, so the second request genuinely
    overlaps the first rather than merely finding a hand-set semaphore.
    """
    import asyncio

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    # Short wait so the refusal path is reached promptly. Production waits
    # 10s, because a legitimate second viewer should queue, not fail.
    monkeypatch.setattr(srv_mod, "_DIFF_QUEUE_WAIT_SECONDS", 0.2)
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def _blocking_collect_diff(cwd):
        calls.append(cwd)
        started.set()
        await gate.wait()          # hold the slot open
        return {"files": [], "truncated": False}

    monkeypatch.setattr("aipager.miniapp.diff.collect_diff", _blocking_collect_diff)

    async def _run():
        client = await _client_for(hardening_server)
        try:
            hdrs = {"X-Telegram-Init-Data": _init_data(555)}
            first = asyncio.create_task(
                client.get("/api/sessions/dev/diff", headers=hdrs))
            await asyncio.wait_for(started.wait(), timeout=5)

            # Timeout, not a bare await: if the refusal is removed this
            # request blocks on the semaphore while the first holds it, and
            # the test would hang instead of failing. Fail fast and loudly.
            second = await asyncio.wait_for(
                client.get("/api/sessions/dev/diff", headers=hdrs), timeout=5)
            assert second.status == 429, f"expected 429, got {second.status}"
            assert await second.json() == {"error": "too_many_requests"}

            gate.set()
            first_resp = await asyncio.wait_for(first, timeout=5)
            assert first_resp.status == 200

            # The refused caller never reached git — that is the whole point.
            assert len(calls) == 1, f"collect_diff ran {len(calls)} times"

            # And the slot is reusable afterwards, not leaked.
            third = await client.get("/api/sessions/dev/diff", headers=hdrs)
            assert third.status == 200
        finally:
            await client.close()

    run_async(_run())


# ===== 4. never advertise an unverified URL ===============================

def test_probe_reports_dead_url_as_unreachable(run_async, monkeypatch):
    """A hostname that does not resolve — the dead-ephemeral-tunnel case
    that started all of this — must come back False, not raise."""
    from aipager.miniapp import tunnel as _tun
    from aipager.miniapp.tunnel import probe_public_url

    # Zero the retry delay: probe_public_url now retries across a window
    # that covers a new tunnel's edge propagation, and this test would
    # otherwise sit through the whole real one.
    monkeypatch.setattr(_tun, "PROBE_RETRY_DELAY_SECONDS", 0)

    async def _run():
        # .invalid is reserved by RFC 2606 and can never resolve, so this
        # needs no network and cannot accidentally hit a real host.
        assert await probe_public_url(
            "https://aipager-probe-target.invalid/") is False
        assert await probe_public_url("") is False

    run_async(_run())


def test_unreachable_url_publishes_no_button(mk_bot, run_async, monkeypatch):
    """The whole point: an unverified URL must clear the button rather than
    advertise a link that spins forever on the phone."""
    from unittest.mock import AsyncMock

    bot = mk_bot(SessionRegistry())
    bot._app.bot.set_chat_menu_button = AsyncMock()
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(type(bot), "_miniapp_button_chats",
                        lambda self: [256113222], raising=False)

    run_async(bot.publish_miniapp_button("https://dead-tunnel.example/"))

    assert bot._miniapp_url == ""          # nothing advertised anywhere
    for call in bot._app.bot.set_chat_menu_button.await_args_list:
        button = call.kwargs["menu_button"]
        assert type(button).__name__ == "MenuButtonCommands", (
            f"published {type(button).__name__} for an unreachable URL")


def test_reachable_url_still_publishes_the_button(mk_bot, run_async, monkeypatch):
    """The guard must not break the working path — pinned so a future
    tightening of the probe cannot silently disable the Mini App."""
    from unittest.mock import AsyncMock

    bot = mk_bot(SessionRegistry())
    bot._app.bot.set_chat_menu_button = AsyncMock()
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(type(bot), "_miniapp_button_chats",
                        lambda self: [256113222], raising=False)

    run_async(bot.publish_miniapp_button("https://live-tunnel.example/"))

    assert bot._miniapp_url == "https://live-tunnel.example/"
    published = [c.kwargs["menu_button"]
                 for c in bot._app.bot.set_chat_menu_button.await_args_list]
    assert published, "no button published for a reachable URL"
    assert type(published[0]).__name__ == "MenuButtonWebApp"


# ===== a fresh tunnel answers 530 before it answers 200 ===================

def test_probe_retries_until_a_new_tunnel_becomes_reachable(run_async, monkeypatch):
    """Observed live: cloudflared reported its URL, the probe fired
    immediately, Cloudflare's edge returned 530 ("tunnel not ready"), the
    button was cleared — and the identical URL served 200 a minute later.
    Nothing republished, because the URL never changed, so the tunnel
    worked perfectly and the button never appeared at all.

    A single probe therefore asks the wrong question of a brand-new tunnel.
    """
    from aipager.miniapp import tunnel as tun

    monkeypatch.setattr(tun, "PROBE_RETRY_DELAY_SECONDS", 0)
    calls = []

    async def _flaky_once(url):
        calls.append(url)
        return len(calls) >= 3          # 530, 530, then up

    monkeypatch.setattr(tun, "_probe_once", _flaky_once)

    async def _run():
        assert await tun.probe_public_url("https://x.trycloudflare.com/") is True

    run_async(_run())
    assert len(calls) == 3, f"gave up after {len(calls)} attempts"


def test_probe_still_gives_up_on_a_genuinely_dead_url(run_async, monkeypatch):
    """Retrying must not turn 'dead' into 'wait forever' — a dead URL still
    has to clear the button, which is the guard's whole purpose."""
    from aipager.miniapp import tunnel as tun

    monkeypatch.setattr(tun, "PROBE_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(tun, "PROBE_ATTEMPTS", 4)
    calls = []

    async def _always_dead(url):
        calls.append(url)
        if len(calls) > 20:             # tripwire: bounded, never a spin
            raise AssertionError("probe retried without bound")
        return False

    monkeypatch.setattr(tun, "_probe_once", _always_dead)

    async def _run():
        assert await tun.probe_public_url("https://dead.example/") is False

    run_async(_run())
    assert len(calls) == 4, f"expected exactly 4 attempts, got {len(calls)}"
