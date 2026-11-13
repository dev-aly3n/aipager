"""The Mini App's ``POST /api/sessions/{label}/clear`` and the detail
payload's ``clear`` action: design.md success criteria 11 and 12.

The fixture is tests/test_miniapp_session_actions_api.py's ``server``
(copied, as entrypoints.md suggests): a real ``MiniAppServer`` over aiohttp's
TestClient, signed init data, the real built-in roles. The PTY is the
``inject.is_alive`` / ``inject.send_text_and_enter`` seam; the chat line is
``bot._app.bot.send_message``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.sessions import session_actions
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200
ADMIN_ID = 555
DEVELOPER_ID = 777
READONLY_ID = 888
FOREIGN_MEMBER_ID = 321

CHAT_LINE = "🧹 /clear sent to [dev] from the Mini App"
NO_PERMISSION = "You don't have permission to control this session."
CANONICAL = ["bringback", "stop", "clearqueue", "compact", "resume", "rename",
             "clear", "kill", "perms", "restart", "delete"]


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="user"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
        root="/home/alice/proj",
    )
    foreign = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign])
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    bot._app.bot.username = "aipager_test_bot"
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._react = AsyncMock()
    bot._card_for_injected = AsyncMock()
    return MiniAppServer(bot, registry, port=8768)


@pytest.fixture
def pty(monkeypatch):
    """The session's socket and what reaches Claude, never real."""
    state = type("Pty", (), {})()
    state.alive = AsyncMock(return_value=True)
    state.typed = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", state.alive)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", state.typed)
    monkeypatch.setattr("aipager.bot.session_ops._spawn", lambda coro: coro.close())
    return state


def _mk_session(server, label, *, scope_chat_id=SCOPE_CHAT_ID, status=Status.IDLE):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.perm_mode = "ask"
    sess.cwd = "/home/alice/proj"
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


def _post(server, run_async, label="dev", user=ADMIN_ID):
    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post(f"/api/sessions/{label}/clear", headers=_hdr(user))
            try:
                body = await resp.json()
            except Exception:
                body = None
            return resp.status, body
        finally:
            await client.close()
    return run_async(_run())


def _detail(server, run_async, label="dev", user=ADMIN_ID):
    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.get(f"/api/sessions/{label}", headers=_hdr(user))
            return await resp.json()
        finally:
            await client.close()
    return run_async(_run())


def _chat_lines(server):
    out = []
    for c in server.bot._app.bot.send_message.await_args_list:
        chat = c.kwargs.get("chat_id", c.args[0] if c.args else None)
        text = c.kwargs.get("text", c.args[1] if len(c.args) > 1 else None)
        out.append((chat, text))
    return out


# ---- criterion 11: success -----------------------------------------------------

def test_clear_on_an_idle_session_returns_200_sent(server, run_async, pty):
    _mk_session(server, "dev")
    assert _post(server, run_async) == (200, {"status": "sent", "label": "dev"})


def test_clear_on_an_idle_session_types_clear_once(server, run_async, pty):
    _mk_session(server, "dev")
    _post(server, run_async)
    assert [c.args[:2] for c in pty.typed.await_args_list] == [("claude-dev", "/clear")]


def test_clear_on_an_idle_session_posts_one_chat_line(server, run_async, pty):
    _mk_session(server, "dev")
    _post(server, run_async)
    assert _chat_lines(server) == [(SCOPE_CHAT_ID, CHAT_LINE)]


def test_a_user_role_may_clear_while_clear_is_on_the_keyboard(server, run_async, pty):
    _mk_session(server, "dev")
    status, _ = _post(server, run_async, user=DEVELOPER_ID)
    assert status == 200


# ---- criterion 11: 404 ---------------------------------------------------------

def test_clear_on_an_unknown_label_returns_404(server, run_async, pty):
    _mk_session(server, "dev")
    status, _ = _post(server, run_async, label="nosuch")
    assert status == 404


def test_clear_on_a_foreign_label_returns_404(server, run_async, pty):
    _mk_session(server, "dev", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
    status, _ = _post(server, run_async)
    assert status == 404


def test_clear_on_a_foreign_label_types_nothing(server, run_async, pty):
    _mk_session(server, "dev", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
    _post(server, run_async)
    pty.typed.assert_not_awaited()


def test_member_of_another_chat_gets_404(server, run_async, pty):
    _mk_session(server, "dev")
    status, _ = _post(server, run_async, user=FOREIGN_MEMBER_ID)
    assert status == 404


# ---- criterion 11: 403 / 429 ----------------------------------------------------

def test_read_only_member_gets_403_forbidden(server, run_async, pty):
    _mk_session(server, "dev")
    status, body = _post(server, run_async, user=READONLY_ID)
    assert (status, body.get("error")) == (403, "forbidden")


def test_role_that_may_not_send_clear_gets_403_with_needs_admin(server, run_async, pty):
    _mk_session(server, "dev")
    server.bot._command_map = {}
    assert _post(server, run_async, user=DEVELOPER_ID) == (
        403, {"error": "forbidden", "detail": "That command needs an admin."})


def test_admin_may_clear_without_clear_on_the_keyboard(server, run_async, pty):
    _mk_session(server, "dev")
    server.bot._command_map = {}
    status, _ = _post(server, run_async, user=ADMIN_ID)
    assert status == 200


def test_clear_is_rate_limited(server, run_async, pty):
    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            for i in range(40):
                _mk_session(server, f"dev{i}")
                resp = await client.post(f"/api/sessions/dev{i}/clear",
                                         headers=_hdr(ADMIN_ID))
                if resp.status == 429:
                    return (await resp.json()).get("error")
            return None
        finally:
            await client.close()
    assert run_async(_run()) == "too_many_requests"


# ---- criterion 11: 409 / 400 ----------------------------------------------------

def _gone(server, sess, pty):
    sess.status = Status.GONE
    sess.gone_at = time.monotonic()


def _dead_socket(server, sess, pty):
    pty.alive.return_value = False


def _busy(server, sess, pty):
    sess.status = Status.BUSY


def _dialog(server, sess, pty):
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": "Bash: ls"}


def _agents(server, sess, pty):
    sess.add_subagent("a1", {"type": "explore", "started_at": time.time()})


def _parked(server, sess, pty):
    sess.parked_job = "x"


def _model_hold(server, sess, pty):
    """A message held behind a typed /model still waits to go out
    (state knob: ``model_hold`` with a non-empty ``pending_queue``)."""
    sess.model_hold = True
    sess.pending_queue = [("hello", 7)]


def _model_check(server, sess, pty):
    """A typed /model may still be checking its model: both windows open,
    no statusline report of another model yet."""
    sess.model_check_until = time.monotonic() + 60
    sess.model_switch_pending_until = time.monotonic() + 60


def _typing_fails(server, sess, pty):
    pty.typed.return_value = False


WAITING_BODY = {"error": "waiting",
                "detail": "Claude is still finishing its work - clear the conversation "
                          "when it's done."}

REFUSALS = [
    (_gone, 409, {"error": "not_live", "detail": "Session isn't running."}),
    (_dead_socket, 409, {"error": "not_live", "detail": "Session isn't running."}),
    (_busy, 409, {"error": "busy",
                  "detail": "Claude is working - clear the conversation when this turn ends."}),
    (_dialog, 409, {"error": "prompt_open",
                    "detail": "Answer the open prompt first, then clear the conversation."}),
    (_agents, 409, {"error": "waiting",
                    "detail": "Claude is still finishing its work - clear the conversation "
                              "when it's done."}),
    (_parked, 409, {"error": "parked",
                    "detail": "Bring dev back from Claude Code's background first."}),
    (_model_hold, 409, WAITING_BODY),
    (_model_check, 409, WAITING_BODY),
    (_typing_fails, 400, {"error": "send_failed", "detail": "Couldn't send /clear."}),
]
REFUSAL_IDS = ["gone", "dead-socket", "busy", "prompt-open", "background-agents",
               "parked", "model-hold", "model-check", "send-failed"]


@pytest.mark.parametrize("arrange, status, body", REFUSALS, ids=REFUSAL_IDS)
def test_refused_clear_returns_status_and_body(server, run_async, pty, arrange, status,
                                               body):
    sess = _mk_session(server, "dev")
    arrange(server, sess, pty)
    assert _post(server, run_async) == (status, body)


@pytest.mark.parametrize("arrange", [r[0] for r in REFUSALS[:-1]], ids=REFUSAL_IDS[:-1])
def test_refused_clear_types_nothing(server, run_async, pty, arrange):
    sess = _mk_session(server, "dev")
    arrange(server, sess, pty)
    _post(server, run_async)
    pty.typed.assert_not_awaited()


ALL_REFUSALS = REFUSALS + [
    (lambda server, sess, pty: setattr(server.bot, "_command_map", {}), 403, None)]


@pytest.mark.parametrize("arrange", [r[0] for r in ALL_REFUSALS],
                         ids=REFUSAL_IDS + ["needs-admin"])
def test_refused_clear_posts_no_chat_line(server, run_async, pty, arrange):
    sess = _mk_session(server, "dev")
    arrange(server, sess, pty)
    _post(server, run_async, user=DEVELOPER_ID)
    assert _chat_lines(server) == []


def test_read_only_refusal_types_nothing(server, run_async, pty):
    _mk_session(server, "dev")
    _post(server, run_async, user=READONLY_ID)
    pty.typed.assert_not_awaited()


def test_read_only_refusal_posts_no_chat_line(server, run_async, pty):
    _mk_session(server, "dev")
    _post(server, run_async, user=READONLY_ID)
    assert _chat_lines(server) == []


def test_needs_admin_refusal_types_nothing(server, run_async, pty):
    _mk_session(server, "dev")
    server.bot._command_map = {}
    _post(server, run_async, user=DEVELOPER_ID)
    pty.typed.assert_not_awaited()


def test_busy_clear_is_not_queued_for_later(server, run_async, pty):
    """Error guessing: unlike Compact, a refused clear must not run later."""
    sess = _mk_session(server, "dev", status=Status.BUSY)
    _post(server, run_async)
    assert [item[0] for item in sess.pending_queue] == []


def test_get_only_route_is_not_a_clear(server, run_async, pty):
    """Error guessing: clearing is a POST; a GET never types /clear."""
    _mk_session(server, "dev")

    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            await client.get("/api/sessions/dev/clear", headers=_hdr(ADMIN_ID))
        finally:
            await client.close()
    run_async(_run())
    pty.typed.assert_not_awaited()


def test_unsigned_clear_types_nothing(server, run_async, pty):
    _mk_session(server, "dev")

    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post("/api/sessions/dev/clear")
            return resp.status
        finally:
            await client.close()
    run_async(_run())
    pty.typed.assert_not_awaited()


# ---- criterion 12: the detail payload's clear action ---------------------------

def test_idle_detail_offers_clear(server, run_async, pty):
    _mk_session(server, "dev")
    assert _detail(server, run_async)["actions"]["clear"] == {"available": True,
                                                               "reason": None}


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE, Status.GONE],
                         ids=["busy", "interactive", "gone"])
def test_non_idle_detail_does_not_offer_clear(server, run_async, pty, status):
    _mk_session(server, "dev", status=status)
    assert "clear" not in _detail(server, run_async)["actions"]


def test_idle_detail_with_background_agents_does_not_offer_clear(server, run_async, pty):
    sess = _mk_session(server, "dev")
    sess.add_subagent("a1", {"type": "explore", "started_at": time.time()})
    assert "clear" not in _detail(server, run_async)["actions"]


def test_read_only_viewer_sees_clear_unavailable_with_reason(server, run_async, pty):
    _mk_session(server, "dev")
    assert _detail(server, run_async, user=READONLY_ID)["actions"]["clear"] == {
        "available": False, "reason": NO_PERMISSION}


def test_idle_detail_actions_follow_the_canonical_order(server, run_async, pty):
    _mk_session(server, "dev")
    keys = list(_detail(server, run_async)["actions"])
    assert keys == [k for k in CANONICAL if k in keys]


@pytest.mark.parametrize("status", ["idle"])
def test_session_actions_offers_clear_for_idle(status):
    assert session_actions(status, resumable=False, can_act=True)["clear"] == {
        "available": True, "reason": None}


@pytest.mark.parametrize("status", ["busy", "waiting", "tasks", "background", "gone"])
def test_session_actions_offers_no_clear_outside_idle(status):
    assert "clear" not in session_actions(status, resumable=True, can_act=True)


def test_session_actions_clear_unavailable_without_permission():
    assert session_actions("idle", resumable=False, can_act=False)["clear"] == {
        "available": False, "reason": NO_PERMISSION}


def test_session_actions_places_clear_after_rename_and_before_kill():
    keys = list(session_actions("idle", resumable=True, can_act=True, is_admin=True,
                                queue_depth=2, context_pct=50))
    assert keys == [k for k in CANONICAL if k in keys] and "clear" in keys


# ---- a confirmed clear goes in once per turn ---------------------------------

def test_a_second_clear_before_the_turn_moves_on_types_nothing(server, run_async, pty):
    """Two Mini App clears (or one here and a chat confirm) before Claude
    Code's SessionEnd for the first: /clear is typed once, and the second
    is told the session changed."""
    _mk_session(server, "dev")
    assert _post(server, run_async)[0] == 200
    status, body = _post(server, run_async)
    assert [c.args[:2] for c in pty.typed.await_args_list] == [("claude-dev", "/clear")]
    assert (status, body["error"]) == (409, "already_cleared")
    assert "dev" in body["detail"]
