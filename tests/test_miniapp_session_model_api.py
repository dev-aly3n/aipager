"""``POST /api/sessions/{label}/model`` — switch a RUNNING session's model
from the Mini App (roadmap 8.35).

The route types ``/model <name>`` into a live Claude Code PTY, so every
test that reaches ``inject.is_alive`` or ``inject.send_text_and_enter``
monkeypatches it: no test here touches a real dtach socket.

Fixture pattern copied from ``test_miniapp_session_actions_api.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.sessions import (
    MODEL_SWITCH_BUSY_REASON,
    MODEL_SWITCH_PROMPT_OPEN_REASON,
    NO_PERMISSION_REASON,
    session_detail,
)
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555
DEVELOPER_ID = 777
READONLY_ID = 888
OUTSIDER_ID = 999
FOREIGN_MEMBER_ID = 321


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN):
    fields = {
        "auth_date": str(int(time.time())),
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
    srv = MiniAppServer(bot, registry, port=8769)
    # Every test that does not care about the confirmation wait keeps it
    # short; the two that do set their own.
    srv.model_confirm_seconds = 0.05
    return srv


def _mk_session(server, label, *, scope_chat_id=SCOPE_CHAT_ID,
                status=Status.IDLE, model_name="Sonnet 5"):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.model_name = model_name
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _pty(monkeypatch, *, alive=True, sent=True):
    """Stub both PTY seams; return the send mock."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=alive))
    send = AsyncMock(return_value=sent)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    return send


async def _post(client, label, body, user_id=ADMIN_ID):
    return await client.post(
        f"/api/sessions/{label}/model", headers=_hdr(user_id), json=body,
    )


# ===== injection ============================================================

def test_injects_exactly_slash_model_into_the_named_session(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "other")
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert resp.status == 200, await resp.text()
            send.assert_awaited_once()
            assert send.await_args.args == ("claude-dev", "/model claude-opus-5-5")
        finally:
            await client.close()
    run_async(_run())


def test_a_listed_label_is_injected_as_its_model_id(server, run_async, monkeypatch):
    """The picker sends the row's label; the PTY must get the id the
    shared list maps it to — including the 1M-context variant."""
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            from aipager.config import MODEL_CHOICES
            label = next(lbl for lbl, cmd in MODEL_CHOICES
                         if cmd == "/model claude-opus-5-5[1m]")
            resp = await _post(client, "dev", {"model": label})
            assert resp.status == 200, await resp.text()
            assert send.await_args.args[1] == "/model claude-opus-5-5[1m]"
        finally:
            await client.close()
    run_async(_run())


def test_the_switch_is_mirrored_to_the_chat(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        _pty(monkeypatch)
        client = await _client_for(server)
        try:
            await _post(client, "dev", {"model": "opus"})
            send = server.bot._app.bot.send_message
            assert send.await_count == 1
            assert "dev" in send.await_args.kwargs["text"]
            assert "opus" in send.await_args.kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


# ===== authorisation ========================================================

def test_a_read_only_member_is_refused_and_nothing_is_typed(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"}, READONLY_ID)
            assert resp.status == 403
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_developer_may_switch(server, run_async, monkeypatch):
    """Same bar as the other session actions: can_prompt, not admin."""
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"}, DEVELOPER_ID)
            assert resp.status == 200
            send.assert_awaited_once()
        finally:
            await client.close()
    run_async(_run())


def test_no_init_data_is_401(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await client.post("/api/sessions/dev/model",
                                     json={"model": "opus"})
            assert resp.status == 401
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("user_id", [OUTSIDER_ID, FOREIGN_MEMBER_ID])
def test_a_caller_outside_the_scope_never_reaches_the_session(
    server, run_async, monkeypatch, user_id,
):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"}, user_id)
            assert resp.status in (403, 404)
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        _pty(monkeypatch)
        client = await _client_for(server)
        try:
            seen_429 = False
            for _ in range(35):
                resp = await _post(client, "dev", {"model": "opus"})
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "model route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== validation ===========================================================

@pytest.mark.parametrize("model", [
    "opus; rm -rf /",
    "opus$(whoami)",
    "opus\nsonnet",
    "opus sonnet",
    "-p",
    "--dangerously-skip-permissions",
    "opus[2m]",
    "[1m]",
    "a" * 65,
    "",
    None,
    123,
])
def test_an_invalid_model_is_refused_and_nothing_is_typed(
    server, run_async, monkeypatch, model,
):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": model})
            assert resp.status == 400, model
            assert (await resp.json())["error"] == "bad_request"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_non_object_body_is_400(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await client.post("/api/sessions/dev/model",
                                     headers=_hdr(ADMIN_ID), json=["opus"])
            assert resp.status == 400
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


# ===== session state ========================================================

def test_a_gone_session_is_refused(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_dead_socket_is_refused(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch, alive=False)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_busy_session_is_refused_with_the_shared_reason(
    server, run_async, monkeypatch,
):
    """R2. Claude Code runs /model mid-turn (it is an `immediate`
    command), so the switch is refused while a turn is running rather
    than typed into it."""
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert await resp.json() == {
                "error": "busy", "detail": MODEL_SWITCH_BUSY_REASON,
            }
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_an_open_prompt_is_refused(server, run_async, monkeypatch):
    """Keystrokes typed while a dialog is open are read as input to it."""
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert await resp.json() == {
                "error": "prompt_open", "detail": MODEL_SWITCH_PROMPT_OPEN_REASON,
            }
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_failed_send_is_400(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev")
        _pty(monkeypatch, sent=False)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 400
            assert (await resp.json())["error"] == "send_failed"
        finally:
            await client.close()
    run_async(_run())


# ===== "switching…" resolution ==============================================

def test_the_switch_is_confirmed_when_the_statusline_reports_a_new_model(
    server, run_async, monkeypatch,
):
    """`model_name` is the statusline's display name. The route answers
    "switched" with the NEW name only once it has actually changed."""
    async def _run():
        sess = _mk_session(server, "dev", model_name="Sonnet 5")
        send = _pty(monkeypatch)
        server.model_confirm_seconds = 5.0

        async def _statusline_later(*_a, **_k):
            asyncio.get_running_loop().call_later(
                0.1, setattr, sess, "model_name", "Opus 5.5")
            return True
        send.side_effect = _statusline_later
        client = await _client_for(server)
        try:
            started = time.monotonic()
            resp = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert resp.status == 200
            assert await resp.json() == {
                "status": "switched", "label": "dev",
                "model": "Opus 5.5", "previous_model": "Sonnet 5",
            }
            # Resolved on the change, not by running out the clock.
            assert time.monotonic() - started < 4.0
        finally:
            await client.close()
    run_async(_run())


def test_the_switch_is_unconfirmed_when_the_model_never_changes(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev", model_name="Sonnet 5")
        _pty(monkeypatch)
        server.model_confirm_seconds = 0.2
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "unconfirmed"
            assert body["model"] == "Sonnet 5"
            assert body["requested"] == "claude-opus-5-5"
            assert body["detail"].startswith("not confirmed — check the session")
        finally:
            await client.close()
    run_async(_run())


def test_await_model_change_resolves_and_times_out(run_async):
    from aipager.bot.session_ops import await_model_change
    from aipager.state import TrackedSession

    async def _run():
        sess = TrackedSession(name="claude-x", label="x", model_name="A")
        # times out: nothing changes
        assert await await_model_change(sess, "A", timeout=0.1, interval=0.01) is None
        # resolves: a later statusline tick changes it
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "B")
        assert await await_model_change(sess, "A", timeout=2.0, interval=0.01) == "B"
        # an empty reading is not a model
        sess.model_name = ""
        assert await await_model_change(sess, "B", timeout=0.1, interval=0.01) is None
    run_async(_run())


# ===== detail payload =======================================================

def _detail(status, *, can_act=True):
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-x", label="x", status=status,
                          model_name="Opus 5.5")
    return session_detail(sess, time.monotonic(), can_act=can_act)


def test_detail_offers_the_switch_only_when_idle():
    assert _detail(Status.IDLE)["model_switch"] == {"available": True, "reason": None}
    assert _detail(Status.BUSY)["model_switch"] == {
        "available": False, "reason": MODEL_SWITCH_BUSY_REASON}
    assert _detail(Status.INTERACTIVE)["model_switch"] == {
        "available": False, "reason": MODEL_SWITCH_PROMPT_OPEN_REASON}
    assert _detail(Status.GONE)["model_switch"] is None
    assert _detail(Status.IDLE, can_act=False)["model_switch"] == {
        "available": False, "reason": NO_PERMISSION_REASON}


# ===== review iteration 1: fail-closed edges ================================

def test_an_unknown_session_is_refused(server, run_async, monkeypatch):
    """UNKNOWN is the post-restart state before the monitor has looked:
    aipager does not know whether a turn is running, so it does not type."""
    from aipager.miniapp.sessions import MODEL_SWITCH_UNKNOWN_REASON

    async def _run():
        _mk_session(server, "dev", status=Status.UNKNOWN)
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert await resp.json() == {
                "error": "unknown", "detail": MODEL_SWITCH_UNKNOWN_REASON,
            }
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_no_baseline_model_is_never_reported_as_switched(
    server, run_async, monkeypatch,
):
    """A session that has not reported a model yet: its FIRST statusline
    reading may well be the old model, so it proves nothing."""
    async def _run():
        sess = _mk_session(server, "dev", model_name="")
        send = _pty(monkeypatch)
        server.model_confirm_seconds = 2.0

        async def _first_report(*_a, **_k):
            asyncio.get_running_loop().call_later(
                0.05, setattr, sess, "model_name", "Sonnet 5")
            return True
        send.side_effect = _first_report
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert resp.status == 200
            assert (await resp.json())["status"] == "unconfirmed"
        finally:
            await client.close()
    run_async(_run())


def test_a_second_switch_is_refused_while_the_first_is_unconfirmed(
    server, run_async, monkeypatch,
):
    """The first `/model` may be sitting behind Claude Code's invisible
    "Switch model?" dialog; a second one would be typed into it."""
    from aipager.miniapp.sessions import MODEL_SWITCH_PENDING_REASON

    async def _run():
        _mk_session(server, "dev")
        send = _pty(monkeypatch)
        client = await _client_for(server)
        try:
            first = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert (await first.json())["status"] == "unconfirmed"
            second = await _post(client, "dev", {"model": "opus"})
            assert second.status == 409
            assert await second.json() == {
                "error": "switch_pending", "detail": MODEL_SWITCH_PENDING_REASON,
            }
            assert send.await_count == 1
            detail = await (await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))).json()
            assert detail["model_switch"] == {
                "available": False, "reason": MODEL_SWITCH_PENDING_REASON}
        finally:
            await client.close()
    run_async(_run())


def test_a_confirmed_switch_frees_the_next_one(server, run_async, monkeypatch):
    async def _run():
        sess = _mk_session(server, "dev", model_name="Sonnet 5")
        send = _pty(monkeypatch)
        server.model_confirm_seconds = 5.0

        async def _statusline_later(*_a, **_k):
            asyncio.get_running_loop().call_later(
                0.05, setattr, sess, "model_name",
                "Opus 5.5" if sess.model_name == "Sonnet 5" else "Sonnet 5")
            return True
        send.side_effect = _statusline_later
        client = await _client_for(server)
        try:
            first = await _post(client, "dev", {"model": "claude-opus-5-5"})
            assert (await first.json())["status"] == "switched"
            second = await _post(client, "dev", {"model": "sonnet"})
            assert second.status == 200
            assert (await second.json())["status"] == "switched"
        finally:
            await client.close()
    run_async(_run())


def test_a_failed_send_leaves_nothing_pending(server, run_async, monkeypatch):
    async def _run():
        sess = _mk_session(server, "dev")
        _pty(monkeypatch, sent=False)
        client = await _client_for(server)
        try:
            await _post(client, "dev", {"model": "opus"})
            assert sess.model_switch_pending_until == 0.0
        finally:
            await client.close()
    run_async(_run())


def test_the_refusal_is_rechecked_after_the_liveness_probe(
    server, run_async, monkeypatch,
):
    """A turn can start while `is_alive` is awaited; nothing is typed then."""
    async def _run():
        sess = _mk_session(server, "dev")

        async def _alive_but_turn_started(_name):
            sess.status = Status.BUSY
            return True
        monkeypatch.setattr("aipager.dtach.inject.is_alive", _alive_but_turn_started)
        send = AsyncMock(return_value=True)
        monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
        client = await _client_for(server)
        try:
            resp = await _post(client, "dev", {"model": "opus"})
            assert resp.status == 409
            assert (await resp.json())["error"] == "busy"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())
