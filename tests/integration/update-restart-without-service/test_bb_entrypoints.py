"""Black box: the entry points of Restart now (entrypoints.md): the
``_:up:rs`` callback (admin only, single flight, lock held, shutting
down) and the Mini App's ``POST /api/update/restart`` (401/403/429/202/
409/503) and ``GET /api/update``'s ``waiting``."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import self_update
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

from bb_helpers import BBEnv, owner_id, popups, run_scenario, tap


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)

BOT_TOKEN = "123456:BB-fake-token-for-initdata-only-000"
GROUP = -100424242
ADMIN, MEMBER, STRANGER = 555001, 555002, 555003
DENIED = "🚫 Only an admin can update aipager."


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _scoped(bb):
    bb.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="alice", role="admin"),
        Member(id=MEMBER, label="bob", role="user"),
    ))]
    bb.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                Path("/nonexistent/policy.d"))
    bb.chat_id = GROUP


def _ready(bb):
    bb.launch_as_daemon()
    bb.attach_stop()


# ----- Telegram: _:up:rs ----------------------------------------------------------------------

def _tap_rs(bb, user_id, chat_id, then=None):
    update, query = tap(bb, "_:up:rs", user_id, chat_id)

    async def body():
        await bb.bot._handle_callback(update, MagicMock())
        if then:
            return await then()
    return query, run_scenario(bb, body)


def test_rs_from_operator_starts_a_restart_job(bb):
    _ready(bb)
    _tap_rs(bb, owner_id(), owner_id(), then=bb.finish)
    assert (bb.manager.snapshot() or {}).get("kind") == "restart"


def test_rs_from_operator_requests_the_stop(bb):
    _ready(bb)
    _tap_rs(bb, owner_id(), owner_id(), then=bb.finish)
    assert len(bb.stops) == 1


def test_rs_from_group_admin_starts_a_job(bb):
    _scoped(bb)
    _ready(bb)
    _tap_rs(bb, ADMIN, GROUP, then=bb.finish)
    assert (bb.manager.snapshot() or {}).get("kind") == "restart"


def test_rs_from_member_is_denied(bb):
    _scoped(bb)
    _ready(bb)
    query, _ = _tap_rs(bb, MEMBER, GROUP)
    assert DENIED in popups(query)


def test_rs_from_member_starts_nothing(bb):
    _scoped(bb)
    _ready(bb)
    _tap_rs(bb, MEMBER, GROUP)
    assert (bb.manager.snapshot(), bb.restart_probe_calls(), bb.stops) == (None, [], [])


def test_rs_from_stranger_in_personal_mode_starts_nothing(bb):
    _ready(bb)
    _tap_rs(bb, STRANGER, STRANGER)
    assert bb.manager.snapshot() is None


def test_rs_two_quick_taps_make_one_job(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)
    u1, q1 = tap(bb, "_:up:rs", owner_id(), owner_id())
    u2, q2 = tap(bb, "_:up:rs", owner_id(), owner_id())
    seen = []

    async def body():
        await asyncio.gather(bb.bot._handle_callback(u1, MagicMock()),
                             bb.bot._handle_callback(u2, MagicMock()))
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        seen.append(bb.manager.snapshot()["id"])
        bb.manager.control("cancel", bb.manager.snapshot()["id"])
        await bb.finish()
    run_scenario(bb, body)
    # one job, one probe run before the gate
    assert len(bb.restart_probe_calls()) == 1


def test_rs_second_tap_while_waiting_gets_a_popup(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)
    u1, _q1 = tap(bb, "_:up:rs", owner_id(), owner_id())
    u2, q2 = tap(bb, "_:up:rs", owner_id(), owner_id())

    async def body():
        await bb.bot._handle_callback(u1, MagicMock())
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.bot._handle_callback(u2, MagicMock())
        bb.manager.control("cancel", bb.manager.snapshot()["id"])
        await bb.finish()
    run_scenario(bb, body)
    assert popups(q2) and q2.message.edit_text.await_count == 0


def test_rs_lock_held_elsewhere_refuses_with_popup(bb):
    _ready(bb)
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        query, _ = _tap_rs(bb, owner_id(), owner_id())
    finally:
        other.release()
    assert "An update is running elsewhere. Try again once it has finished." in popups(query)


def test_rs_lock_held_elsewhere_starts_nothing(bb):
    _ready(bb)
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        _tap_rs(bb, owner_id(), owner_id())
    finally:
        other.release()
    assert (bb.restart_probe_calls(), bb.stops) == ([], [])


def test_rs_while_shutting_down_starts_nothing(bb):
    _ready(bb)
    update, query = tap(bb, "_:up:rs", owner_id(), owner_id())

    async def body():
        await bb.manager.shutdown()
        await bb.bot._handle_callback(update, MagicMock())
    run_scenario(bb, body)
    assert (bb.manager.snapshot(), bb.stops) == (None, [])


def test_rs_while_shutting_down_answers_with_a_popup(bb):
    _ready(bb)
    update, query = tap(bb, "_:up:rs", owner_id(), owner_id())

    async def body():
        await bb.manager.shutdown()
        await bb.bot._handle_callback(update, MagicMock())
    run_scenario(bb, body)
    assert popups(query) and query.message.edit_text.await_count == 0


def test_rs_callback_data_fits_telegram_budget():
    assert len("_:up:rs".encode()) <= 64


# ----- Mini App ---------------------------------------------------------------------------------

def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


def _call(bb, fn):
    srv = MiniAppServer(bb.bot, bb.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return run_scenario(bb, scenario)


def test_api_restart_without_initdata_is_401(bb):
    _ready(bb)

    async def fn(c):
        return (await c.post("/api/update/restart")).status
    assert _call(bb, fn) == 401


def test_api_restart_with_forged_initdata_is_401(bb):
    _ready(bb)
    bad = _init_data(owner_id()).replace("hash=", "hash=00")

    async def fn(c):
        return (await c.post("/api/update/restart",
                             headers={"X-Telegram-Init-Data": bad})).status
    assert _call(bb, fn) == 401


def test_api_restart_401_starts_nothing(bb):
    _ready(bb)

    async def fn(c):
        await c.post("/api/update/restart")
    _call(bb, fn)
    assert bb.manager.snapshot() is None


def test_api_restart_member_is_403(bb):
    _scoped(bb)
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(MEMBER))
        return r.status
    assert _call(bb, fn) == 403


def test_api_restart_stranger_personal_mode_is_403(bb):
    _ready(bb)

    async def fn(c):
        return (await c.post("/api/update/restart", headers=_hdr(STRANGER))).status
    assert _call(bb, fn) == 403


def test_api_restart_403_starts_nothing(bb):
    _scoped(bb)
    _ready(bb)

    async def fn(c):
        await c.post("/api/update/restart", headers=_hdr(MEMBER))
    _call(bb, fn)
    assert (bb.manager.snapshot(), bb.restart_probe_calls()) == (None, [])


def test_api_restart_admin_is_202(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        body = await r.json()
        await bb.finish()
        return r.status, body
    status, body = _call(bb, fn)
    assert status == 202


def test_api_restart_202_job_kind_restart(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        body = await r.json()
        await bb.finish()
        return body
    assert _call(bb, fn)["job"]["kind"] == "restart"


def test_api_restart_body_is_optional_and_ignored(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()),
                         json={"kind": "claude", "anything": 1})
        await bb.finish()
        return r.status
    assert _call(bb, fn) == 202


def test_api_restart_202_job_has_documented_fields(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        body = await r.json()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", body["job"]["id"])
        await bb.finish()
        return body
    job = _call(bb, fn)["job"]
    assert {"id", "kind", "phase", "blockers", "summary", "started_at"} <= set(job)


def test_api_restart_second_while_waiting_is_409(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        first = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        jid = (await first.json())["job"]["id"]
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        second = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        out = (second.status, await second.json())
        bb.manager.control("cancel", jid)
        await bb.finish()
        return out
    assert _call(bb, fn) == (409, {"error": "update_in_progress"})


def test_api_restart_lock_held_elsewhere_is_409(bb):
    _ready(bb)
    other = self_update.UpdateLock()
    assert other.try_acquire()

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        return r.status, await r.json()
    try:
        got = _call(bb, fn)
    finally:
        other.release()
    assert got == (409, {"error": "update_in_progress"})


def test_api_restart_shutting_down_is_503(bb):
    _ready(bb)

    async def fn(c):
        await bb.manager.shutdown()
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        return r.status, await r.json()
    assert _call(bb, fn) == (503, {"error": "shutting_down"})


def test_api_restart_30_writes_are_allowed(bb):
    """Boundary: the 30th write in 60 s is still served (not 429)."""
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        statuses = []
        for _ in range(30):
            r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
            statuses.append(r.status)
        snap = bb.manager.snapshot()
        if snap:
            await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
            bb.manager.control("cancel", snap["id"])
        await bb.finish()
        return statuses
    assert 429 not in _call(bb, fn)


def test_api_restart_31st_write_is_429(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        for _ in range(30):
            await c.post("/api/update/restart", headers=_hdr(owner_id()))
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        out = (r.status, await r.json())
        snap = bb.manager.snapshot()
        if snap:
            await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
            bb.manager.control("cancel", snap["id"])
        await bb.finish()
        return out
    assert _call(bb, fn) == (429, {"error": "too_many_requests"})


def test_api_restart_rate_limit_is_checked_before_starting(bb, monkeypatch):
    _ready(bb)
    monkeypatch.setattr(MiniAppServer, "_allow_write", lambda self, uid: False)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        return r.status
    assert (_call(bb, fn), bb.manager.snapshot()) == (429, None)


def test_api_restart_member_gets_403_not_429(bb, monkeypatch):
    """Order: 403 comes before the rate limit."""
    _scoped(bb)
    _ready(bb)
    monkeypatch.setattr(MiniAppServer, "_allow_write", lambda self, uid: False)

    async def fn(c):
        return (await c.post("/api/update/restart", headers=_hdr(MEMBER))).status
    assert _call(bb, fn) == 403


def test_api_restart_now_control_applies_to_a_restart_job(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        jid = (await r.json())["job"]["id"]
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        rn = await c.post("/api/update/restart-now", headers=_hdr(owner_id()),
                          json={"job_id": jid})
        await bb.finish()
        return rn.status
    status = _call(bb, fn)
    assert (status, len(bb.stops)) == (200, 1)


def test_api_cancel_control_applies_to_a_restart_job(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(owner_id()))
        jid = (await r.json())["job"]["id"]
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        cn = await c.post("/api/update/cancel", headers=_hdr(owner_id()),
                          json={"job_id": jid})
        await bb.finish()
        return cn.status
    assert (_call(bb, fn), bb.manager.snapshot()["phase"], bb.stops) == (
        200, "cancelled", [])


# ----- GET /api/update: waiting ----------------------------------------------------------------

def test_get_update_waiting_null_before_any_check(bb):
    _ready(bb)

    async def fn(c):
        return await (await c.get("/api/update", headers=_hdr(owner_id()))).json()
    body = _call(bb, fn)
    assert "waiting" in body and body["waiting"] is None


def test_get_update_waiting_after_check(bb):
    _ready(bb)

    async def fn(c):
        await c.post("/api/update/check", headers=_hdr(owner_id()))
        return await (await c.get("/api/update", headers=_hdr(owner_id()))).json()
    assert _call(bb, fn)["waiting"] == {"version": "1.1.0", "running": "1.0.1",
                                        "source": "installed"}


def test_get_update_runs_no_probe(bb):
    _ready(bb)

    async def fn(c):
        await c.get("/api/update", headers=_hdr(owner_id()))
    _call(bb, fn)
    assert bb.calls == []


def test_get_update_waiting_from_hooks(bb):
    _ready(bb)

    async def fn(c):
        bb.manager.note_hook_version("9.0.0")
        await bb.drain()
        return await (await c.get("/api/update", headers=_hdr(owner_id()))).json()
    w = _call(bb, fn)["waiting"] or {}
    assert (w.get("version"), w.get("source")) == ("9.0.0", "hooks")


def test_post_check_payload_carries_restart_offer(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/check", headers=_hdr(owner_id()))
        return await r.json()
    body = _call(bb, fn)
    assert body.get("check", {}).get("restart_offer") is True


def test_post_check_payload_aipager_waiting(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/check", headers=_hdr(owner_id()))
        return await r.json()
    a = _call(bb, fn)["aipager"]
    assert (a.get("installed"), a.get("installed_ok"), a.get("waiting")) == (
        "1.1.0", True, True)


def test_post_check_payload_restart_in_place(bb):
    _ready(bb)

    async def fn(c):
        r = await c.post("/api/update/check", headers=_hdr(owner_id()))
        return await r.json()
    r = _call(bb, fn)["restart"]
    assert (r.get("in_place"), r.get("steps")) == (True, "terminal")


def test_get_update_finished_job_can_restart(bb):
    bb.restart_probe = ("1.0.1", True)
    bb.install_probe = ("1.1.0", True)
    _ready(bb)

    async def fn(c):
        await c.post("/api/update/check", headers=_hdr(owner_id()))
        await c.post("/api/update/start", headers=_hdr(owner_id()), json={"kind": "aipager"})
        await bb.finish()
        return await (await c.get("/api/update", headers=_hdr(owner_id()))).json()
    assert _call(bb, fn)["job"].get("can_restart") is True
