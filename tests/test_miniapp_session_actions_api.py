"""Mini App session menu actions — perms/clearqueue/compact/restart/rename
on ``/api/sessions/{label}/...`` (design.md: "Mini App session menu
actions"), the five NEW routes beside the four Stop/Kill/Resume/Delete
routes already covered by ``test_miniapp_session_controls_api.py``.

Mirrors that file's exact fixture pattern (``_Policy``/``_Role``
stand-ins implementing both ``bypass_safety`` and ``can_prompt``,
``_init_data``/``_hdr`` HMAC signing, ``ADMIN_ID``/``DEVELOPER_ID``/
``READONLY_ID``/``OUTSIDER_ID``/``FOREIGN_MEMBER_ID``, aiohttp
``TestClient``/``TestServer``).

HARD RULE (this feature's own top risk, design.md/entrypoints.md): every
test that reaches ``inject.kill_session``, ``inject.launch_session``,
``inject.send_keys``, ``inject.send_text_and_enter`` or
``inject.is_alive`` monkeypatches it — no test here may kill a real
dtach session, launch a real claude process, send real keys, or touch a
real dtach socket. Every test whose route reaches the kill/poll/relaunch
core (perms, restart) also neuters ``aipager.bot.session_ops.asyncio.
sleep`` and controls ``aipager.bot.session_ops.Path`` so it never
actually waits out the 0.5s Ctrl-C pause or the up-to-3s socket poll.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.sessions import (
    NO_TRANSCRIPT_REASON,
    PERMS_ADMIN_REQUIRED_REASON,
    QUEUE_EMPTY_REASON,
    QUEUE_FULL_REASON,
)
from aipager.scope import Member, Scope
from aipager.state import QUEUE_CAP, SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555         # bypass_safety AND can_prompt
DEVELOPER_ID = 777     # can_prompt, NOT bypass_safety
READONLY_ID = 888      # neither bypass_safety NOR can_prompt
OUTSIDER_ID = 999      # member of no scope at all
FOREIGN_MEMBER_ID = 321  # a real member, but of the OTHER scope


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN, auth_date=None):
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, *, bypass_safety=False, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    """Minimal stand-in for the real policy, implementing BOTH
    `bypass_safety` and `can_prompt` — `_can_prompt_user` reads
    `Role.can_prompt` via `_role_can_prompt`, so a stand-in with only
    `bypass_safety` would AttributeError, not silently pass for the
    wrong reason.

    - "admin"      -> bypass_safety=True,  can_prompt=True
    - "developer"  -> bypass_safety=False, can_prompt=True
    - "read_only"  -> bypass_safety=False, can_prompt=False
    """

    _ROLES = {
        "admin": _Role(bypass_safety=True, can_prompt=True),
        "developer": _Role(bypass_safety=False, can_prompt=True),
        "read_only": _Role(bypass_safety=False, can_prompt=False),
    }

    def get_role(self, name):
        return self._ROLES.get(name)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
    )
    foreign_scope = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign_scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8768)


def _mk_session(
    server, label, *, scope_chat_id=SCOPE_CHAT_ID, status=Status.IDLE,
    claude_session_id="", skip_perms=False, cwd="/tmp/proj",
):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.claude_session_id = claude_session_id
    sess.skip_perms = skip_perms
    sess.cwd = cwd
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _neuter_kill_relaunch(monkeypatch, *, socket_gone=True):
    """Standard setup for a route that reaches
    `_kill_and_relaunch_core` (perms, restart): neuter the poll's sleep,
    control whether the socket is reported gone, and mock every dtach
    call the core can reach.
    """
    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=not socket_gone)),
    ))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", AsyncMock(return_value=True))


# ===== Perms =================================================================

def test_perms_admin_switches_ask_to_auto_and_get_reflects_it(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "switched", "label": "dev", "skip_perms": True,
            }
            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["skip_perms"] is True

            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
            assert "Auto" in send.await_args.kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


def test_perms_non_admin_switching_back_auto_to_ask_succeeds(
    server, run_async, monkeypatch,
):
    """Switching TO Ask never needs admin — a developer (can_prompt,
    not admin) can switch an Auto session back to Ask."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=True)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "switched", "label": "dev", "skip_perms": False,
            }
        finally:
            await client.close()
    run_async(_run())


def test_perms_non_admin_targeting_auto_gets_403_with_detail(server, run_async):
    """The route's 403 detail (entrypoints.md: "Auto mode requires
    admin.") is deliberately SHORTER than session_actions()'s own
    PERMS_ADMIN_REQUIRED_REASON ("Switching to Auto mode requires
    admin.") — two independently-worded strings by design, not a typo
    to reconcile."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 403
            assert await resp.json() == {
                "error": "forbidden", "detail": "Auto mode requires admin.",
            }
        finally:
            await client.close()
    run_async(_run())


def test_perms_busy_session_sends_ctrl_c_not_kill(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, skip_perms=False)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
        finally:
            await client.close()
        from aipager.dtach import inject
        inject.send_keys.assert_awaited_once_with("claude-dev", "C-c")
        inject.kill_session.assert_not_awaited()
    run_async(_run())


def test_perms_not_live_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


def test_perms_already_restarting_returns_409(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        sess.restarting_until = time.monotonic() + 10.0
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "already_restarting"
        finally:
            await client.close()
    run_async(_run())


def test_perms_still_stopping_returns_409_and_leaves_mode_unchanged(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        _neuter_kill_relaunch(monkeypatch, socket_gone=False)

        async def _boom(*a, **k):
            raise AssertionError("launch_session must not run after still_stopping")
        monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "still_stopping"
            assert server.registry.get("claude-dev").skip_perms is False
        finally:
            await client.close()
    run_async(_run())


def test_perms_launch_failed_returns_400(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(False, "dtach broken")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            assert await resp.json() == {
                "error": "launch_failed", "detail": "dtach broken",
            }
        finally:
            await client.close()
    run_async(_run())


def test_perms_readonly_member_gets_403_and_nothing_changes(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
            assert sess.skip_perms is False
            server.bot._app.bot.send_message.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_perms_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(server, f"dev{i}", status=Status.IDLE, skip_perms=True)
                resp = await client.post(
                    f"/api/sessions/dev{i}/perms", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "perms route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Clear queue ===========================================================

def test_clearqueue_busy_with_pending_returns_dropped_count_and_mirrors(
    server, run_async,
):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        sess.queue_prompt("a", 1)
        sess.queue_prompt("b", 2)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "cleared", "label": "dev", "dropped": 2,
            }
            assert sess.pending_queue == []
            # The current turn itself keeps running.
            assert sess.status == Status.BUSY

            assert send.await_count == 1
            assert "2" in send.await_args.kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_empty_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert await resp.json() == {
                "error": "queue_empty", "detail": QUEUE_EMPTY_REASON,
            }
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_readonly_member_gets_403(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        sess.queue_prompt("a", 1)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
            assert len(sess.pending_queue) == 1, "a refused clear still cleared it"
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                sess = _mk_session(server, f"dev{i}", status=Status.BUSY)
                sess.queue_prompt("a", 1)
                resp = await client.post(
                    f"/api/sessions/dev{i}/clearqueue", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "clearqueue route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Compact ================================================================

def test_compact_busy_returns_queued_and_increments_queue_depth(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "queued", "label": "dev"}
            assert sess.pending_queue[-1][0] == "/compact"

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["queue_depth"] == 1

            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_compact_idle_returns_sent_and_calls_inject(server, run_async, monkeypatch):
    """Exercises inject.send_text_and_enter — monkeypatched, never real."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        send_text = AsyncMock(return_value=True)
        monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send_text)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "sent", "label": "dev"}
            send_text.assert_awaited_once()
            assert send_text.await_args.args[0] == "claude-dev"
            assert send_text.await_args.args[1] == "/compact"
        finally:
            await client.close()
    run_async(_run())


def test_compact_queue_full_returns_409_and_leaves_queue_depth_unchanged(
    server, run_async,
):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        for i in range(QUEUE_CAP):
            sess.queue_prompt(f"msg{i}", i)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert await resp.json() == {
                "error": "queue_full", "detail": QUEUE_FULL_REASON,
            }

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["queue_depth"] == QUEUE_CAP
        finally:
            await client.close()
    run_async(_run())


def test_compact_not_live_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


def test_compact_send_failed_returns_400(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        monkeypatch.setattr(
            "aipager.dtach.inject.send_text_and_enter", AsyncMock(return_value=False),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "send_failed"
        finally:
            await client.close()
    run_async(_run())


def test_compact_readonly_member_gets_403(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
            assert sess.pending_queue == []
        finally:
            await client.close()
    run_async(_run())


def test_compact_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(server, f"dev{i}", status=Status.BUSY)
                resp = await client.post(
                    f"/api/sessions/dev{i}/compact", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "compact route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Restart ================================================================

def test_restart_busy_session_hard_kills_never_ctrl_c(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, skip_perms=True)
        _neuter_kill_relaunch(monkeypatch)
        launch = AsyncMock(return_value=(True, ""))
        monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "restarted", "label": "dev"}

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["status"] == "idle"

            assert send.await_count == 1
        finally:
            await client.close()
        from aipager.dtach import inject
        inject.kill_session.assert_awaited_once_with("claude-dev")
        inject.send_keys.assert_not_awaited()
        # Restart never changes the mode — same skip_perms it started with.
        assert launch.await_args.kwargs["skip_perms"] is True
    run_async(_run())


def test_restart_preserves_pending_queue(server, run_async, monkeypatch):
    """Unlike Stop/Clear-queue, Restart does not discard queued work."""
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        sess.queue_prompt("a", 1)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert len(sess.pending_queue) == 1
        finally:
            await client.close()
    run_async(_run())


def test_restart_not_live_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


def test_restart_already_restarting_returns_409(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        sess.restarting_until = time.monotonic() + 10.0
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "already_restarting"
        finally:
            await client.close()
    run_async(_run())


def test_restart_still_stopping_returns_409(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _neuter_kill_relaunch(monkeypatch, socket_gone=False)

        async def _boom(*a, **k):
            raise AssertionError("launch_session must not run after still_stopping")
        monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "still_stopping"
        finally:
            await client.close()
    run_async(_run())


def test_restart_launch_failed_returns_400(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(False, "dtach broken")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            assert await resp.json() == {
                "error": "launch_failed", "detail": "dtach broken",
            }
        finally:
            await client.close()
    run_async(_run())


def test_restart_readonly_member_gets_403(server, run_async, monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("inject.kill_session reached despite a 403")
    monkeypatch.setattr("aipager.dtach.inject.kill_session", _boom)

    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
            assert sess.status == Status.IDLE
        finally:
            await client.close()
    run_async(_run())


def test_restart_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _neuter_kill_relaunch(monkeypatch)
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(server, f"dev{i}", status=Status.IDLE)
                resp = await client.post(
                    f"/api/sessions/dev{i}/restart", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "restart route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Rename =================================================================

def test_rename_success_returns_200_and_get_follows(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "frontend"},
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "renamed", "label": "frontend",
                "previous_label": "dev", "changed": True,
            }

            new_get = await client.get(
                "/api/sessions/frontend", headers=_hdr(ADMIN_ID),
            )
            assert new_get.status == 200
            old_get = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert old_get.status == 404

            assert send.await_count == 1
            assert "dev" in send.await_args.kwargs["text"]
            assert "frontend" in send.await_args.kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


def test_rename_to_same_label_is_a_no_op(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "dev"},
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "renamed", "label": "dev",
                "previous_label": "dev", "changed": False,
            }
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_rename_invalid_name_returns_400_bad_request(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "bad name!"},
            )
            assert resp.status == 400
            body = await resp.json()
            assert body["error"] == "bad_request"
            assert "detail" in body

            unchanged = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert unchanged.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_rename_missing_label_field_returns_400(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID), json={},
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "bad_request"
        finally:
            await client.close()
    run_async(_run())


def test_rename_malformed_body_is_refused(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID), data="not json",
            )
            assert resp.status == 400
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID), json=["a", "b"],
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_rename_conflicts_with_a_live_session(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(server, "frontend", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "frontend"},
            )
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "conflict"
            assert "frontend" in body["detail"]
        finally:
            await client.close()
    run_async(_run())


def test_rename_conflicts_with_a_gone_but_resumable_session(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(
            server, "frontend", status=Status.GONE, claude_session_id="uuid-1",
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "frontend"},
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "conflict"
        finally:
            await client.close()
    run_async(_run())


def test_rename_succeeds_over_a_gone_and_unresumable_session(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(server, "frontend", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "frontend"},
            )
            assert resp.status == 200
            assert (await resp.json())["changed"] is True
        finally:
            await client.close()
    run_async(_run())


def test_rename_readonly_member_gets_403(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(READONLY_ID),
                json={"label": "frontend"},
            )
            assert resp.status == 403
            assert sess.label == "dev"
        finally:
            await client.close()
    run_async(_run())


def test_rename_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(server, f"dev{i}", status=Status.IDLE)
                resp = await client.post(
                    f"/api/sessions/dev{i}/rename", headers=_hdr(ADMIN_ID),
                    json={"label": f"dev{i}-new"},
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "rename route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== cross-scope isolation: byte-identical 404s across all five routes ===

@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
@pytest.mark.parametrize("path_suffix", [
    "/perms", "/clearqueue", "/compact", "/restart", "/rename",
])
def test_foreign_or_unknown_label_404s_identically_on_every_route(
    server, run_async, label, path_suffix,
):
    async def _run():
        _mk_session(server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await client.post(
                f"/api/sessions/{label}{path_suffix}", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path_suffix", [
    "/perms", "/clearqueue", "/compact", "/restart", "/rename",
])
def test_no_init_data_returns_401_on_every_route(server, run_async, path_suffix):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(f"/api/sessions/dev{path_suffix}")
            assert resp.status == 401
            assert await resp.json() == {"error": "unauthorized"}
        finally:
            await client.close()
    run_async(_run())


def test_404_matches_the_existing_detail_route_404_byte_for_byte(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            detail_resp = await client.get(
                "/api/sessions/ghost", headers=_hdr(ADMIN_ID),
            )
            perms_resp = await client.post(
                "/api/sessions/ghost/perms", headers=_hdr(ADMIN_ID),
            )
            assert detail_resp.status == perms_resp.status == 404
            assert await detail_resp.json() == await perms_resp.json()
        finally:
            await client.close()
    run_async(_run())


# ===== NO_TRANSCRIPT_REASON re-export sanity (imported above) ==============
#
# Not a route test — pins that the shared reason strings this file
# imports from aipager.miniapp.sessions stay importable, so a rename of
# one of those constants fails loudly here rather than only inside a
# 409 body assertion elsewhere in this file.

def test_shared_reason_constants_are_non_empty_strings():
    for value in (
        NO_TRANSCRIPT_REASON, PERMS_ADMIN_REQUIRED_REASON,
        QUEUE_EMPTY_REASON, QUEUE_FULL_REASON,
    ):
        assert isinstance(value, str) and value
