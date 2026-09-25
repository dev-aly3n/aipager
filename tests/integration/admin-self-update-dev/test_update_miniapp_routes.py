"""GET /api/update and POST /api/update/{action}: auth, 4xx, and wiring.

Roadmap 8.43 (operator decision 2026-09-25): GET carries the job only (no
lookup), POST /api/update/check looks versions up, and POST
/api/update/start {"kind": ...} is the one start route; the per-product
POST /api/update/{claude,aipager,both} are retired. Tests that drove the
retired routes now check, then start."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP = -100
ADMIN, MEMBER, STRANGER = 555, 777, 999


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, admin):
        self.bypass_safety = admin
        self.can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role(name == "admin")


def _scoped(env):
    env.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="developer"),
    ))]
    env.bot.policy = _Policy()
    env.chat_id = GROUP


def _call(env, run, fn):
    srv = MiniAppServer(env.bot, env.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client, srv)
        finally:
            await client.close()
    return run(scenario)


# ----- auth ----------------------------------------------------------------------

def test_update_api_rejects_missing_initdata(env, run):
    async def fn(c, srv):
        a = await c.get("/api/update")
        b = await c.post("/api/update/start")
        return a.status, b.status, await b.json()
    assert _call(env, run, fn) == (401, 401, {"error": "unauthorized"})
    assert env.calls == [] and env.fetches == []


def test_update_api_forbids_non_admin_member(env, run):
    _scoped(env)

    async def fn(c, srv):
        r = await c.post("/api/update/start", headers=_hdr(MEMBER), json={"kind": "aipager"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (403, {"error": "forbidden"})
    assert env.manager.snapshot() is None


def test_update_status_api_forbids_non_admin(env, run):
    _scoped(env)

    async def fn(c, srv):
        return (await c.get("/api/update", headers=_hdr(MEMBER))).status
    assert _call(env, run, fn) == 403
    assert env.fetches == []


def test_update_api_forbids_non_operator_personal_mode(env, run):
    async def fn(c, srv):
        a = await c.get("/api/update", headers=_hdr(STRANGER))
        b = await c.post("/api/update/start", headers=_hdr(STRANGER), json={"kind": "claude"})
        return a.status, b.status
    assert _call(env, run, fn) == (403, 403)
    assert env.manager.snapshot() is None


def test_update_api_rate_limits_writes(env, run, monkeypatch):
    monkeypatch.setattr(MiniAppServer, "_allow_write", lambda self, uid: False)

    async def fn(c, srv):
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "claude"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (429, {"error": "too_many_requests"})
    assert env.manager.snapshot() is None


def test_update_api_rejects_unknown_action(env, run):
    async def fn(c, srv):
        a = await c.post("/api/update/reboot-the-box", headers=_hdr(env.chat_id))
        b = await c.post("/api/update/cancel", headers=_hdr(env.chat_id),
                         data="not json")
        c2 = await c.post("/api/update/cancel", headers=_hdr(env.chat_id),
                          json={"job_id": "7"})
        d = await c.post("/api/update/cancel", headers=_hdr(env.chat_id),
                         json={"job_id": True})
        return a.status, b.status, c2.status, d.status
    assert _call(env, run, fn) == (400, 400, 400, 400)


def test_update_api_conflict_while_job_running(env, run):
    env.add_session(status=Status.BUSY)   # the aipager job waits on the gate
    env.claude_latest = env.claude_version  # only aipager is newer

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        first = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                             json={"kind": "aipager"})
        body = await first.json()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        second = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                              json={"kind": "aipager"})
        stale = await c.post("/api/update/restart-now", headers=_hdr(env.chat_id),
                             json={"job_id": body["job"]["id"] + 1})
        cancel = await c.post("/api/update/cancel", headers=_hdr(env.chat_id),
                              json={"job_id": body["job"]["id"]})
        await env.finish()
        return (first.status, second.status, await second.json(), stale.status,
                await stale.json(), cancel.status)
    got = _call(env, run, fn)
    assert got == (202, 409, {"error": "update_in_progress"}, 409,
                   {"error": "no_matching_job"}, 200)


def test_update_api_not_upgradable(env, run):
    """8.43: a check never offers an install that can't be upgraded here,
    and start_offer still refuses one that stopped being upgradable after
    the check (the not_upgradable guard in start())."""
    from aipager.install_source import InstallSource
    env.claude_latest = env.claude_version  # only aipager is newer

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        env.source = InstallSource(kind="editable", prefix="/s", python="/s/p",
                                   reason="editable")
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "aipager"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (409, {"error": "not_upgradable"})


# ----- the happy path ----------------------------------------------------------------

def test_update_api_status_shape(env, run):
    """8.43: the version payload moved from GET to POST /api/update/check."""
    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return r.status, await r.json()
    status, body = _call(env, run, fn)
    assert status == 200
    assert body["aipager"]["running"] == "0.7.13"
    assert body["aipager"]["latest"] == "0.7.14"
    assert body["aipager"]["update_available"] is True
    assert body["aipager"]["source"]["kind"] == "pipx"
    assert body["claude"]["current"] == "2.1.281"
    assert body["claude"]["latest"] == "2.1.290"
    assert body["restart"] == {"mode": "systemd", "automatic": True, "reason": None,
                               "manual_command": "systemctl --user restart aipager.service"}
    assert body["job"] is None


def test_mini_app_drives_the_same_job_the_chat_shows(env, run):
    env.pypi_latest = env.running           # only Claude Code is newer

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "claude"})
        body = await r.json()
        await env.finish()
        s = await c.get("/api/update", headers=_hdr(env.chat_id))
        return r.status, body, await s.json()
    status, started, after = _call(env, run, fn)
    assert status == 202 and started["job"]["kind"] == "claude"
    assert after["job"]["id"] == started["job"]["id"]
    assert after["job"]["phase"] == "done"
    # One status message into the caller's chat.
    assert [m["chat_id"] for m in env.sent] == [env.chat_id]


def test_preferences_carry_can_update(env, run):
    _scoped(env)

    async def fn(c, srv):
        a = await (await c.get("/api/preferences", headers=_hdr(ADMIN))).json()
        m = await (await c.get("/api/preferences", headers=_hdr(MEMBER))).json()
        return a["can_update"], m["can_update"]
    assert _call(env, run, fn) == (True, False)


def test_update_api_hides_install_paths_for_a_group_scope(env, run):
    from aipager.install_source import InstallSource

    _scoped(env)
    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(ADMIN))
        return r.status, await r.text()
    status, raw = _call(env, run, fn)
    assert status == 200
    assert "/home/op/aipager" not in raw
    assert "pipx, from a local path" in raw


def test_update_api_keeps_install_paths_in_a_private_scope(env, run):
    from aipager.install_source import InstallSource

    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return await r.json()
    body = _call(env, run, fn)
    assert "/home/op/aipager" in body["aipager"]["source"]["describe"]


def test_update_api_refuses_a_start_while_a_restart_is_pending(env, run):
    async def fn(c, srv):
        await env.manager.check()
        await env.start("aipager")
        await env.finish()
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "claude"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (409, {"error": "update_in_progress"})


def test_update_api_start_during_shutdown_is_503_shutting_down(env, run):
    """Review rev-iter2-002: once the daemon began to stop, a start is a 503
    ``shutting_down`` (try again once it is back), and nothing runs."""
    async def fn(c, srv):
        await env.manager.shutdown()
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "claude"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (503, {"error": "shutting_down"})
    assert env.manager.snapshot() is None
    assert env.calls == []
