"""SC-2 (Mini App half) and SC-11: both /api/update routes refuse
non-admins and personal-mode non-operators (401/403) and run nothing;
POST adds 429/400/409; the admin's actions drive the same job the chat
shows. (design.md Success criteria #2, #11; entrypoints.md "HTTP routes")."""

from __future__ import annotations

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
GROUP = -100
ADMIN = 555
MEMBER = 777
OUTSIDER = 999


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id, *, bot_token=BOT_TOKEN):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, **kw):
    return {"X-Telegram-Init-Data": _init_data(user_id, **kw)}


class _Role:
    def __init__(self, admin):
        self.bypass_safety = admin
        self.can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role(name == "admin")


def _bot_common(bot, h):
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", GROUP), 803))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()


@pytest.fixture
def team_server(mk_bot, h, world):
    reg = SessionRegistry()
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="developer"),
    ))
    bot = mk_bot(reg, scopes=[scope])
    bot.policy = _Policy()
    _bot_common(bot, h)
    return MiniAppServer(bot, reg, port=8767)


@pytest.fixture
def personal_server(mk_bot, h, world):
    reg = SessionRegistry()
    bot = mk_bot(reg)
    _bot_common(bot, h)
    return MiniAppServer(bot, reg, port=8768)


def _call(srv, run_async, method, path, headers=None, body=None, *, before=None, after=None):
    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            if before:
                await before()
            kw = {"headers": headers or {}}
            if body is not None:
                kw["data" if isinstance(body, (bytes, str)) else "json"] = body
            resp = await getattr(client, method)(path, **kw)
            try:
                payload = await resp.json()
            except Exception:
                payload = None
            if after:
                await after()
            return resp.status, payload
        finally:
            await client.close()
    return run_async(go())


# ---- auth: GET -----------------------------------------------------------

def test_get_without_initdata_is_401(team_server, run_async):
    status, _ = _call(team_server, run_async, "get", "/api/update")
    assert status == 401


def test_get_with_bad_signature_is_401(team_server, run_async):
    status, _ = _call(team_server, run_async, "get", "/api/update",
                      _hdr(ADMIN, bot_token="999:wrong-token-wrong-token"))
    assert status == 401


def test_get_by_non_admin_member_is_403(team_server, run_async):
    status, body = _call(team_server, run_async, "get", "/api/update", _hdr(MEMBER))
    assert (status, body) == (403, {"error": "forbidden"})


def test_get_by_outsider_is_refused(team_server, run_async):
    status, _ = _call(team_server, run_async, "get", "/api/update", _hdr(OUTSIDER))
    assert status in (401, 403)


def test_get_by_non_operator_in_personal_mode_is_refused(personal_server, run_async):
    status, _ = _call(personal_server, run_async, "get", "/api/update", _hdr(OUTSIDER))
    assert status in (401, 403)


def test_non_admin_get_makes_no_outbound_calls(team_server, run_async, world):
    _call(team_server, run_async, "get", "/api/update", _hdr(MEMBER))
    assert world.urls == [] and world.calls == []


def test_get_by_admin_is_200(team_server, run_async):
    status, _ = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert status == 200


def test_get_by_operator_in_personal_mode_is_200(personal_server, run_async, h):
    status, _ = _call(personal_server, run_async, "get", "/api/update", _hdr(h.OPERATOR))
    assert status == 200


def test_get_payload_has_documented_shape(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert {"aipager", "claude", "restart", "job"} <= set(body)


def test_get_payload_versions(team_server, run_async, h):
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert (body["aipager"]["running"], body["aipager"]["latest"],
            body["claude"]["current"], body["claude"]["latest"]) == \
        (h.RUNNING, h.LATEST, h.CLAUDE_OLD, h.CLAUDE_NEW)


def test_get_payload_update_available_flags(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert body["aipager"]["update_available"] is True and body["claude"]["update_available"] is True


def test_get_payload_source_fields(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert {"kind", "origin", "detail", "upgradable", "reason"} <= set(body["aipager"]["source"])


def test_get_payload_restart_mode(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert (body["restart"]["mode"], body["restart"]["automatic"]) == ("systemd", True)


def test_get_payload_network_down_is_null_not_error(team_server, run_async, world):
    world.latest_pypi = None
    world.claude_latest = None
    status, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert (status, body["aipager"]["latest"], body["claude"]["latest"]) == (200, None, None)


def test_get_payload_network_down_is_not_update_available(team_server, run_async, world):
    world.latest_pypi = None
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert body["aipager"]["update_available"] is False


def test_get_payload_prerelease_is_not_update_available(team_server, run_async, world):
    world.latest_pypi = "0.9.0rc1"
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert body["aipager"]["update_available"] is False


def test_get_payload_equal_version_is_not_update_available(team_server, run_async, world, h):
    world.latest_pypi = h.RUNNING
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert body["aipager"]["update_available"] is False


def test_get_payload_older_latest_is_not_update_available(team_server, run_async, world):
    world.latest_pypi = "0.7.12"
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN))
    assert body["aipager"]["update_available"] is False


# ---- auth: POST ----------------------------------------------------------

@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_post_without_initdata_is_401(team_server, run_async, action):
    status, _ = _call(team_server, run_async, "post", f"/api/update/{action}")
    assert status == 401


@pytest.mark.parametrize("action", ["claude", "aipager", "both", "restart-now",
                                    "wait-more", "cancel"])
def test_post_by_non_admin_member_is_403(team_server, run_async, action):
    status, _ = _call(team_server, run_async, "post", f"/api/update/{action}",
                      _hdr(MEMBER), {"job_id": 1})
    assert status == 403


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_post_by_non_admin_runs_nothing(team_server, run_async, world, action):
    _call(team_server, run_async, "post", f"/api/update/{action}", _hdr(MEMBER))
    assert world.calls == [] and team_server.bot.updates.snapshot() is None


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_post_by_personal_mode_stranger_runs_nothing(personal_server, run_async, world, action):
    status, _ = _call(personal_server, run_async, "post", f"/api/update/{action}",
                      _hdr(OUTSIDER))
    assert status in (401, 403) and world.calls == []


def test_post_unknown_action_is_400(team_server, run_async):
    status, body = _call(team_server, run_async, "post", "/api/update/rm-rf", _hdr(ADMIN))
    assert (status, body) == (400, {"error": "bad_request"})


def test_post_unknown_action_by_non_admin_is_403_not_400(team_server, run_async):
    """Gate order: auth before validation."""
    status, _ = _call(team_server, run_async, "post", "/api/update/rm-rf", _hdr(MEMBER))
    assert status == 403


@pytest.mark.parametrize("body", [None, {}, {"job_id": "12"}, {"job_id": None},
                                  "not json", [1, 2]])
def test_post_control_with_bad_body_is_400(team_server, run_async, body):
    status, _ = _call(team_server, run_async, "post", "/api/update/restart-now",
                      _hdr(ADMIN), body)
    assert status == 400


def test_post_control_without_job_is_409_no_matching_job(team_server, run_async):
    status, body = _call(team_server, run_async, "post", "/api/update/cancel",
                         _hdr(ADMIN), {"job_id": 123})
    assert (status, body) == (409, {"error": "no_matching_job"})


def test_post_aipager_on_refused_source_is_409_not_upgradable(team_server, run_async, world):
    world.origin = "editable"
    status, body = _call(team_server, run_async, "post", "/api/update/aipager", _hdr(ADMIN))
    assert (status, body) == (409, {"error": "not_upgradable"})


def test_post_claude_by_admin_is_202_with_job(team_server, run_async, h):
    async def after():
        await h.wait_phase(team_server.bot, h.TERMINAL)
    status, body = _call(team_server, run_async, "post", "/api/update/claude",
                         _hdr(ADMIN), after=after)
    assert status == 202 and body["job"]["kind"] == "claude"


def test_post_claude_by_admin_runs_the_same_claude_update(team_server, run_async, world, h):
    async def after():
        await h.wait_phase(team_server.bot, h.TERMINAL)
    _call(team_server, run_async, "post", "/api/update/claude", _hdr(ADMIN), after=after)
    assert [c["argv"] for c in world.claude_update_calls()] == [[world.claude_path, "update"]]


def test_miniapp_job_posts_status_message_to_scope_chat(team_server, run_async, h):
    async def after():
        await h.wait_phase(team_server.bot, h.TERMINAL)
    _call(team_server, run_async, "post", "/api/update/claude", _hdr(ADMIN), after=after)
    call = team_server.bot._app.bot.send_message.await_args
    assert call.kwargs.get("chat_id", call.args[0] if call.args else None) == GROUP


def test_post_while_job_running_is_409_update_in_progress(team_server, run_async, h):
    h.add_session(team_server.registry, "w", status=Status.BUSY, chat_id=GROUP)

    async def before():
        await team_server.bot.updates.start("aipager", chat_id=GROUP, user_id=ADMIN,
                                            origin="chat",
                                            status_message=h.status_message(GROUP))
        await h.wait_phase(team_server.bot, "waiting_for_idle")
    status, body = _call(team_server, run_async, "post", "/api/update/claude",
                         _hdr(ADMIN), before=before)
    assert (status, body) == (409, {"error": "update_in_progress"})


def test_get_shows_the_chat_started_job(team_server, run_async, h):
    """Same job in both surfaces."""
    h.add_session(team_server.registry, "w", status=Status.BUSY, chat_id=GROUP)

    async def before():
        await team_server.bot.updates.start("aipager", chat_id=GROUP, user_id=ADMIN,
                                            origin="chat",
                                            status_message=h.status_message(GROUP))
        await h.wait_phase(team_server.bot, "waiting_for_idle")
    _, body = _call(team_server, run_async, "get", "/api/update", _hdr(ADMIN), before=before)
    assert (body["job"]["kind"], body["job"]["phase"]) == ("aipager", "waiting_for_idle")


def test_post_restart_now_drives_the_chat_job(team_server, run_async, h, world):
    h.add_session(team_server.registry, "w", status=Status.BUSY, chat_id=GROUP)
    state = {}

    async def before():
        res = await team_server.bot.updates.start("aipager", chat_id=GROUP, user_id=ADMIN,
                                                  origin="chat",
                                                  status_message=h.status_message(GROUP))
        state["id"] = res.job["id"]
        await h.wait_phase(team_server.bot, "waiting_for_idle")

    async def after():
        await h.wait_phase(team_server.bot, h.TERMINAL)

    async def go():
        client = TestClient(TestServer(team_server._build_app()))
        await client.start_server()
        try:
            await before()
            resp = await client.post("/api/update/restart-now", headers=_hdr(ADMIN),
                                     json={"job_id": state["id"]})
            await after()
            return resp.status
        finally:
            await client.close()
    status = run_async(go())
    assert status == 200 and len(world.schedule_calls()) == 1


def test_post_control_with_stale_job_id_is_409(team_server, run_async, h):
    h.add_session(team_server.registry, "w", status=Status.BUSY, chat_id=GROUP)
    state = {}

    async def go():
        client = TestClient(TestServer(team_server._build_app()))
        await client.start_server()
        try:
            res = await team_server.bot.updates.start(
                "aipager", chat_id=GROUP, user_id=ADMIN, origin="chat",
                status_message=h.status_message(GROUP))
            state["id"] = res.job["id"]
            await h.wait_phase(team_server.bot, "waiting_for_idle")
            resp = await client.post("/api/update/cancel", headers=_hdr(ADMIN),
                                     json={"job_id": state["id"] + 1})
            return resp.status, await resp.json()
        finally:
            await client.close()
    assert run_async(go()) == (409, {"error": "no_matching_job"})


def test_post_writes_are_rate_limited(team_server, run_async):
    async def go():
        client = TestClient(TestServer(team_server._build_app()))
        await client.start_server()
        try:
            for _ in range(60):
                resp = await client.post("/api/update/cancel", headers=_hdr(ADMIN),
                                         json={"job_id": 1})
                if resp.status == 429:
                    return await resp.json()
            return None
        finally:
            await client.close()
    assert run_async(go()) == {"error": "too_many_requests"}


# ---- preferences hint ------------------------------------------------------

def test_preferences_can_update_true_for_admin(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/preferences", _hdr(ADMIN))
    assert body["can_update"] is True


def test_preferences_can_update_false_for_member(team_server, run_async):
    _, body = _call(team_server, run_async, "get", "/api/preferences", _hdr(MEMBER))
    assert body["can_update"] is False


def test_page_contains_updates_block(team_server, run_async):
    async def go():
        client = TestClient(TestServer(team_server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/")
            return await resp.text()
        finally:
            await client.close()
    assert 'id="updates-block"' in run_async(go())
