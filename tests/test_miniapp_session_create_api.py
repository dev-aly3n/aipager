"""POST /api/sessions — the only route that can spawn a process.

Every test here mocks `bot.create_session`; nothing in this file may
launch a real Claude process or touch a real dtach socket.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

ADMIN_ID = 555      # bypass_safety + can_prompt
MEMBER_ID = 777     # can_prompt, NOT admin
READONLY_ID = 888   # neither
OUTSIDER_ID = 999


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
        "user": json.dumps({"id": user_id, "first_name": "T"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, name):
        self.bypass_safety = name == "admin"
        self.can_prompt = name in ("admin", "developer")


class _Policy:
    def get_role(self, name):
        return _Role(name)


@pytest.fixture
def server(mk_bot, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=MEMBER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cy", role="read_only"),
        ),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    # An existing session establishes the allowed root — the picker is
    # seeded from directories this scope already works in.
    seed = registry.get_or_create("claude-seed__g100")
    seed.label = "seed"
    seed.scope_chat_id = -100
    seed.status = Status.GONE
    seed.cwd = str(project)

    srv = MiniAppServer(bot, registry, port=8765)
    # THE mock that keeps every test in this file from spawning anything.
    bot.create_session = AsyncMock(return_value=("claude-dev__g100", ""))
    srv._project_dir = str(project)
    return srv


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _post(client, body, user_id=ADMIN_ID):
    return client.post("/api/sessions", headers=_hdr(user_id), json=body)


# ===== happy path =========================================================

def test_create_launches_and_returns_the_label(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev"})
            assert resp.status == 200
            assert (await resp.json())["label"] == "dev"
            server.bot.create_session.assert_awaited_once()
            kwargs = server.bot.create_session.await_args.kwargs
            assert kwargs["scope_chat_id"] == -100
            assert kwargs["skip_perms"] is False
        finally:
            await client.close()
    run_async(_run())


def test_create_announces_the_session_in_chat(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            await _post(client, {"name": "dev"})
            assert server.bot._app.bot.send_message.await_count == 1
            assert server.bot._app.bot.send_message.await_args.kwargs["chat_id"] == -100
        finally:
            await client.close()
    run_async(_run())


def test_a_failing_chat_mirror_does_not_fail_a_launched_session(server, run_async):
    """The process is already running by the time we announce it — a
    failed notification must not report failure the operator might retry."""
    async def _run():
        client = await _client_for(server)
        server.bot._app.bot.send_message.side_effect = RuntimeError("telegram down")
        try:
            assert (await _post(client, {"name": "dev"})).status == 200
        finally:
            await client.close()
    run_async(_run())


# ===== authorization ======================================================

def test_unauthenticated_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.post("/api/sessions", json={"name": "dev"})
            assert resp.status == 401
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_outsider_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, OUTSIDER_ID)).status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_cannot_create(server, run_async):
    """A READ_ONLY member cannot prompt a session, so must not be able to
    spawn one."""
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, READONLY_ID)).status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_ordinary_member_can_create_without_admin(server, run_async):
    """Creating is the same capability as driving — deliberately NOT
    gated on bypass_safety."""
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, MEMBER_ID)).status == 200
        finally:
            await client.close()
    run_async(_run())


def test_auto_mode_requires_admin(server, run_async):
    """`/new !name` is admin-gated in chat. Without the same gate here the
    Mini App would hand a non-admin --dangerously-skip-permissions."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": True}, MEMBER_ID)
            assert resp.status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_admin_may_use_auto_mode(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": True}, ADMIN_ID)
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["skip_perms"] is True
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("truthy", ["true", 1, "yes", ["x"]])
def test_only_a_real_bool_enables_auto_mode(server, run_async, truthy):
    """A truthy-but-not-True value must not smuggle Auto mode past the
    admin gate for a non-admin."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": truthy}, MEMBER_ID)
            assert resp.status == 200, "non-bool truthy should be ignored, not gated"
            assert server.bot.create_session.await_args.kwargs["skip_perms"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== validation is server-side ==========================================

@pytest.mark.parametrize("name", [
    "", "   ", "has space", "../../etc/passwd", "dev;rm -rf /",
    "dev\x00evil", "status", "a" * 200, None, 123,
])
def test_bad_names_rejected_without_launching(server, run_async, name):
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": name})).status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_directory_outside_the_allowed_roots_is_rejected(server, run_async, tmp_path):
    async def _run():
        client = await _client_for(server)
        outside = tmp_path / "not-a-project"
        outside.mkdir()
        try:
            resp = await _post(client, {"name": "dev", "cwd": str(outside)})
            assert resp.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_traversal_in_cwd_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "cwd": "/etc/../etc"})
            assert resp.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_allowed_directory_is_passed_through_resolved(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "cwd": server._project_dir})
            assert resp.status == 200
            import os
            assert server.bot.create_session.await_args.kwargs["cwd"] == \
                os.path.realpath(server._project_dir)
        finally:
            await client.close()
    run_async(_run())


def test_malformed_body_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            r1 = await client.post("/api/sessions", headers=_hdr(ADMIN_ID), data="nope")
            assert r1.status == 400
            r2 = await client.post("/api/sessions", headers=_hdr(ADMIN_ID), json=[1, 2])
            assert r2.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


# ===== collisions and failures ============================================

def test_name_collision_with_a_live_session_is_a_409_not_a_launch(server, run_async):
    async def _run():
        live = server.registry.get_or_create("claude-taken__g100")
        live.label = "taken"
        live.scope_chat_id = -100
        live.status = Status.BUSY
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "taken"})
            assert resp.status == 409
            assert "already exists" in (await resp.json())["detail"]
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_refused_launch_surfaces_as_an_error_not_a_success(server, run_async):
    async def _run():
        server.bot.create_session = AsyncMock(return_value=("", "dtach not installed"))
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev"})
            assert resp.status == 400
            assert "dtach" in (await resp.json())["detail"]
        finally:
            await client.close()
    run_async(_run())


def test_create_is_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            saw_429 = False
            for i in range(45):
                resp = await _post(client, {"name": f"dev{i}"})
                if resp.status == 429:
                    saw_429 = True
                    break
            assert saw_429, "create route accepted unbounded launches"
        finally:
            await client.close()
    run_async(_run())


# ===== the options route ==================================================

def test_options_lists_only_directories_this_scope_uses(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            import os
            assert body["directories"] == [os.path.realpath(server._project_dir)]
            assert body["can_create"] is True
            assert body["can_use_auto"] is True
        finally:
            await client.close()
    run_async(_run())


def test_options_tells_a_readonly_member_they_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(READONLY_ID))).json()
            assert body["can_create"] is False
            assert body["can_use_auto"] is False
        finally:
            await client.close()
    run_async(_run())


def test_options_tells_a_member_they_cannot_use_auto(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(MEMBER_ID))).json()
            assert body["can_create"] is True
            assert body["can_use_auto"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== model selection ====================================================

def test_options_serves_the_canonical_model_list(server, run_async):
    """The client must not keep its own model list — one source of truth,
    the same one the chat keyboard offers."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            from aipager.config import MODEL_CHOICES
            assert [m["label"] for m in body["models"]] == \
                [label for label, _ in MODEL_CHOICES]
            # Each alias carries a "what is this for" hint instead of a
            # version number: an alias always resolves to the latest of its
            # family, so a baked-in version would be wrong on the next
            # release. No entry may look like one.
            import re
            for m in body["models"]:
                assert "hint" in m
                assert not re.search(r"\d+(\.\d+)?$", m["hint"] or "x"), (
                    f"model hint looks like a pinned version: {m!r}"
                )
        finally:
            await client.close()
    run_async(_run())


def test_chosen_model_is_queued_as_a_model_command(server, run_async):
    """`launch_session` has no model flag; selection rides Claude Code's
    own /model slash command, queued to drain on first IDLE."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": "Opus"})
            assert resp.status == 200
            sess = server.registry.get_or_create("claude-dev__g100")
            queued = [str(item) for item in sess.pending_queue]
            assert any("/model opus" in q for q in queued), queued
        finally:
            await client.close()
    run_async(_run())


def test_no_model_choice_queues_nothing(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            await _post(client, {"name": "dev", "model": ""})
            sess = server.registry.get_or_create("claude-dev__g100")
            assert not sess.pending_queue
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("model", ["gpt-4", "../../etc/passwd", "opus; rm -rf /", 123, None])
def test_unknown_model_is_ignored_not_injected(server, run_async, model):
    """An unrecognised model must never reach the session as text — it is
    dropped, and the session still launches."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": model})
            assert resp.status == 200
            sess = server.registry.get_or_create("claude-dev__g100")
            assert not sess.pending_queue, sess.pending_queue
        finally:
            await client.close()
    run_async(_run())


def test_options_serves_the_schema_and_scope_defaults_for_the_form(server, run_async):
    """The Advanced section renders reply-style settings from these, so a
    missing key silently empties that part of the form."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            fields = {g["field"] for g in body["schema"]}
            assert fields == {
                "layout", "simple_formatting", "answer_length", "language_level",
            }
            assert set(body["scope_defaults"]) == fields
        finally:
            await client.close()
    run_async(_run())


def test_queued_command_comes_from_the_send_field_not_the_label(server, run_async, monkeypatch):
    """`keyboard.json` lets an operator map a label to an unrelated
    command. Rebuilding the command by lowercasing the label would type
    nonsense into their session while chat sent the right thing.

    The default list hides this — every shipped label lowercases into its
    own command — so this test uses a mapping where they differ, which is
    the only shape that discriminates.
    """
    monkeypatch.setattr(
        "aipager.config.MODEL_CHOICES",
        [("Claude 4.5 Opus", "/model claude-opus-4-5")],
    )

    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": "Claude 4.5 Opus"})
            assert resp.status == 200
            sess = server.registry.get_or_create("claude-dev__g100")
            queued = " ".join(str(item) for item in sess.pending_queue)
            assert "/model claude-opus-4-5" in queued, queued
            assert "claude 4.5 opus" not in queued.lower(), (
                "command was rebuilt from the label instead of using `send`"
            )
        finally:
            await client.close()
    run_async(_run())
