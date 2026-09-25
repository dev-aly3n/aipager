"""``POST /api/sessions/{label}/answer`` (roadmap 8.44, 8.31 parity).

"Answer in chat" re-sends a waiting session's pending prompt, with its
answer keyboard, into the caller's chat through the pinned bar's own path
(``DashboardMixin._resend_pending_prompt``). Nothing is typed into the
PTY; the answer itself still arrives through the chat's buttons.

Fixture pattern mirrors ``test_miniapp_session_controls_api.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
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

PROMPT = {"text": "🔐 alpha wants to run Bash", "keyboard": None,
          "summary": "Bash: ls"}


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
    # A real-looking sent message, so the resend can record its id.
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    return MiniAppServer(bot, registry, port=8767)


def _mk_session(server, label="alpha", *, scope_chat_id=SCOPE_CHAT_ID,
                status=Status.INTERACTIVE, prompt=True):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    if prompt:
        sess.pending_prompt_msg = dict(PROMPT)
    return sess


def _post(server, run_async, label="alpha", headers=None):
    """POST the answer route once; returns (status, body, send mock)."""
    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post(
                f"/api/sessions/{label}/answer",
                headers=_hdr(ADMIN_ID) if headers is None else headers,
            )
            return resp.status, await resp.json()
        finally:
            await client.close()
    status, body = run_async(_run())
    return status, body, server.bot._app.bot.send_message


# ===== auth and scope =======================================================

def test_missing_init_data_is_401(server, run_async):
    status, body, send = _post(server, run_async, headers={})
    assert status == 401
    assert body == {"error": "unauthorized"}
    assert send.await_count == 0


def test_invalid_init_data_is_401(server, run_async):
    status, body, _send = _post(
        server, run_async, headers={"X-Telegram-Init-Data": "hash=nope"})
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_non_member_is_403(server, run_async):
    _mk_session(server)
    status, body, send = _post(server, run_async, headers=_hdr(OUTSIDER_ID))
    assert status == 403
    assert body == {"error": "forbidden"}
    assert send.await_count == 0


def test_label_from_another_scope_404s_like_an_unknown_one(server, run_async):
    _mk_session(server, "alpha", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
    status, body, send = _post(server, run_async, "alpha")
    unknown_status, unknown_body, _ = _post(server, run_async, "nobody")
    assert (status, body) == (404, {"error": "not_found"})
    assert (unknown_status, unknown_body) == (status, body)
    assert send.await_count == 0


def test_member_who_cannot_prompt_is_403_and_nothing_is_sent(server, run_async):
    _mk_session(server)
    status, body, send = _post(server, run_async, headers=_hdr(READONLY_ID))
    assert status == 403
    assert body == {"error": "forbidden"}
    assert send.await_count == 0


def test_developer_who_can_prompt_may_answer(server, run_async):
    _mk_session(server)
    status, body, send = _post(server, run_async, headers=_hdr(DEVELOPER_ID))
    assert status == 200
    assert send.await_count == 1


def test_rate_limited_caller_gets_429_and_nothing_is_sent(server, run_async, monkeypatch):
    _mk_session(server)
    monkeypatch.setattr(server, "_allow_write", lambda _uid: False)
    status, body, send = _post(server, run_async)
    assert status == 429
    assert body == {"error": "too_many_requests"}
    assert send.await_count == 0


# ===== state guards =========================================================

def test_idle_session_is_409_not_waiting_and_nothing_is_sent(server, run_async):
    _mk_session(server, status=Status.IDLE)
    status, body, send = _post(server, run_async)
    assert status == 409
    assert body["error"] == "not_waiting"
    assert "—" not in body["detail"]
    assert send.await_count == 0


def test_the_route_guard_refuses_before_the_core_is_reached(server, run_async):
    """The route's own status guard answers 409, without calling the
    shared resend core at all (the core has its own check, but the route
    must not depend on it)."""
    _mk_session(server, status=Status.BUSY)
    core = AsyncMock(return_value="sent")
    server.bot._resend_pending_prompt = core
    status, body, _send = _post(server, run_async)
    assert status == 409
    assert body["error"] == "not_waiting"
    assert core.await_count == 0


def test_waiting_with_no_stored_prompt_is_409_not_resendable(server, run_async):
    _mk_session(server, prompt=False)
    status, body, send = _post(server, run_async)
    assert status == 409
    assert body["error"] == "not_resendable"
    assert body["detail"]
    assert send.await_count == 0


def test_outbound_gate_skip_is_503_chat_busy(server, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    _mk_session(server)
    server.bot._app.bot.send_message = AsyncMock(
        side_effect=FloodSkipped(SCOPE_CHAT_ID, "sendMessage"))
    status, body, _send = _post(server, run_async)
    assert status == 503
    assert body["error"] == "chat_busy"


def test_a_send_with_no_message_back_is_502_send_failed(server, run_async):
    _mk_session(server)
    server.bot._app.bot.send_message = AsyncMock(return_value=None)
    status, body, _send = _post(server, run_async)
    assert status == 502
    assert body["error"] == "send_failed"


def test_telegram_refusing_the_send_is_502_send_failed_not_a_bare_500(
        server, run_async):
    """A Telegram error out of the send (network, timeout, bad request)
    answers a JSON 502 the page can show, not aiohttp's HTML 500."""
    from telegram.error import NetworkError
    _mk_session(server)
    server.bot._app.bot.send_message = AsyncMock(
        side_effect=NetworkError("connection reset"))
    status, body, _send = _post(server, run_async)
    assert status == 502
    assert body == {"error": "send_failed",
                    "detail": "Couldn't post the prompt to the chat."}


SEND_FAILED_BODY = {"error": "send_failed",
                    "detail": "Couldn't post the prompt to the chat."}


@pytest.mark.parametrize("exc", [
    OSError(104, "Connection reset by peer: secret-internal-path"),
    RuntimeError("gate invariant broken: secret-internal-state"),
    asyncio.TimeoutError(),
], ids=["os_error", "runtime_error", "asyncio_timeout"])
def test_any_other_send_error_is_502_send_failed_with_a_fixed_sentence(
        server, run_async, exc, caplog):
    """A non-Telegram exception out of the send (a transport OSError, a
    bug, an asyncio timeout) still answers the JSON 502, not aiohttp's
    plain-text 500 or empty 504. The detail is a fixed sentence: the
    exception text never reaches the page, but it is logged in full."""
    _mk_session(server)
    server.bot._app.bot.send_message = AsyncMock(side_effect=exc)
    with caplog.at_level("ERROR", logger="aipager.miniapp.server"):
        status, body, _send = _post(server, run_async)
    assert status == 502
    assert body == SEND_FAILED_BODY
    assert "secret" not in json.dumps(body)
    logged = [r for r in caplog.records if r.exc_info
              and r.exc_info[1] is exc]
    assert logged, "the unexpected send error must be logged with its traceback"


def test_telegram_retry_after_is_503_chat_busy(server, run_async):
    """Telegram's own flood wait (a RetryAfter that escaped the outbound
    gate) is the same "throttled" outcome as the gate's skip: 503
    chat_busy, not 502 send_failed."""
    from telegram.error import RetryAfter
    _mk_session(server)
    server.bot._app.bot.send_message = AsyncMock(side_effect=RetryAfter(7))
    status, body, _send = _post(server, run_async)
    assert status == 503
    assert body == {"error": "chat_busy",
                    "detail": "Telegram is busy. Try again in a moment."}


def test_cancelling_the_send_still_propagates(server, run_async, monkeypatch):
    """The catch-all is ``Exception``: a cancelled request (client gone,
    daemon shutting down) must cancel the handler, not answer a 502."""
    sess = _mk_session(server)

    async def _resolved(_request, _what):
        return sess, SCOPE_CHAT_ID, ADMIN_ID
    monkeypatch.setattr(server, "_resolve_own_scope_session", _resolved)
    server.bot._app.bot.send_message = AsyncMock(
        side_effect=asyncio.CancelledError())

    async def _run():
        with pytest.raises(asyncio.CancelledError):
            await server._handle_session_answer(MagicMock())
    run_async(_run())


# ===== success ==============================================================

def test_success_sends_the_prompt_to_the_scope_chat_and_registers_it(
        server, run_async):
    from aipager.bot.dashboard import current_prompt_token
    sess = _mk_session(server)
    status, body, send = _post(server, run_async)
    assert status == 200
    assert body == {"status": "sent", "label": "alpha"}
    assert send.await_count == 1
    args, kwargs = send.await_args.args, send.await_args.kwargs
    assert args[:2] == (SCOPE_CHAT_ID, PROMPT["text"])
    assert kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in kwargs
    # One message only: no "answered from the Mini App" mirror line.
    token = current_prompt_token(sess)
    assert token is not None
    surfaces = {k: v for k, v in server.bot._resent_prompts.items()
                if k[0] == SCOPE_CHAT_ID}
    assert list(surfaces.values()) == [(sess.name, token)]
    (chat, msg_id), = surfaces
    assert server.registry.get_session_by_msg(msg_id, chat) is sess


def test_the_route_never_types_into_the_pty(server, run_async, monkeypatch):
    keys = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", keys)
    _mk_session(server)
    status, _body, _send = _post(server, run_async)
    assert status == 200
    assert keys.await_count == 0


def _tap_copy(bot, msg_id):
    """A tap on the copy's Allow button, in the scope chat, by the admin."""
    shown = []

    async def _answer(text=None, **_kw):
        shown.append(text)
        return True

    query = MagicMock()
    query.data = "claude-alpha:allow"
    query.answer = _answer
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = msg_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = SCOPE_CHAT_ID
    query.from_user = MagicMock()
    query.from_user.id = ADMIN_ID
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = SCOPE_CHAT_ID
    update.effective_chat.type = "supergroup"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(bot._handle_callback(update, MagicMock()))
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.close()
    return shown


def test_a_tap_on_the_copy_after_the_prompt_changed_is_refused(
        server, run_async, monkeypatch):
    sess = _mk_session(server)
    status, _body, _send = _post(server, run_async)
    assert status == 200
    (chat, msg_id), = [k for k in server.bot._resent_prompts
                       if k[0] == SCOPE_CHAT_ID]
    # The prompt was answered and the session now waits on a NEW one.
    sess.pending_prompt_msg = {"text": "🔐 alpha wants to run Edit",
                               "keyboard": None, "summary": "Edit: a.py"}
    keys = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", keys)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    shown = _tap_copy(server.bot, msg_id)
    assert shown[:1] == ["already answered"], shown
    assert keys.await_count == 0


# ===== the grid's can_act ===================================================

def _grid(server, run_async, user_id):
    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/api/sessions", headers=_hdr(user_id))
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


def test_grid_can_act_is_true_for_a_member_who_can_prompt(server, run_async):
    sess = _mk_session(server)
    sess.pending_permission = {"tool_summary": "Bash: ls", "tool_info": None}
    status, body = _grid(server, run_async, DEVELOPER_ID)
    assert status == 200
    assert body["can_act"] is True
    row = body["sessions"][0]
    assert row["waiting_summary"] == "Bash: ls"
    assert "cwd" not in row


def test_grid_can_act_is_false_for_a_viewer(server, run_async):
    _mk_session(server)
    status, body = _grid(server, run_async, READONLY_ID)
    assert status == 200
    assert body["can_act"] is False


def test_detail_answer_state_follows_the_caller(server, run_async):
    _mk_session(server)

    async def _run(user_id):
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/api/sessions/alpha", headers=_hdr(user_id))
            return await resp.json()
        finally:
            await client.close()

    assert run_async(_run(ADMIN_ID))["answer"] == {"available": True, "reason": None}
    viewer = run_async(_run(READONLY_ID))["answer"]
    assert viewer["available"] is False
    assert viewer["reason"]
