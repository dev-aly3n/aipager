"""Restart now's entry points: the ``_:up:rs`` button (admin only,
single-flight, refusals as pop-ups) and ``POST /api/update/restart``
(401, 403, 429, 202, 409, 503); ``GET /api/update`` carries ``waiting``."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import self_update
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP = -100777
ADMIN, MEMBER, STRANGER = 555, 777, 999999


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _scoped(env):
    env.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="user"),
    ))]
    env.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
    env.chat_id = GROUP


def _tap(env, user_id, chat_id, data="_:up:rs"):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = env._mk_message(chat_id)
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.from_user.language_code = None
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def _popups(query):
    return [c.args[0] for c in query.answer.await_args_list if c.args]


# ---- Telegram ---------------------------------------------------------------------------

def test_a_member_who_is_not_an_admin_is_refused(env, run):
    _scoped(env)
    update, query = _tap(env, MEMBER, GROUP)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert "🚫 Only an admin can update aipager." in _popups(query)
    assert env.manager.snapshot() is None and env.calls == []


def test_the_update_handler_itself_rechecks_the_admin(env, run):
    # Below the dispatcher's tap gate: update_flow's own re-check.
    from aipager.bot import update_flow
    _scoped(env)
    update, query = _tap(env, MEMBER, GROUP)

    async def scenario():
        return await update_flow.handle_callback(env.bot, update, query, "_", "up:rs")
    assert run(scenario) is True
    assert "🚫 Only an admin can update aipager." in _popups(query)
    assert env.manager.snapshot() is None and env.calls == []


def test_a_stranger_in_personal_mode_is_refused(env, run):
    update, query = _tap(env, STRANGER, STRANGER)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None and env.calls == []


def test_the_operator_starts_a_restart_on_the_tapped_card(env, run):
    update, query = _tap(env, env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["kind"] == "restart"
    assert len(env.stops) == 1
    query.message.edit_text.assert_awaited()


def test_a_group_admin_starts_a_restart(env, run):
    _scoped(env)
    update, query = _tap(env, ADMIN, GROUP)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "restart_scheduled"


def test_two_quick_taps_start_one_job(env, run):
    env.add_session(status=Status.BUSY)
    first, _ = _tap(env, env.chat_id, env.chat_id)
    second, q2 = _tap(env, env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(first, MagicMock())
        job = env.manager.snapshot()["id"]
        await env.bot._handle_callback(second, MagicMock())
        return job
    job = run(scenario)
    assert env.manager.snapshot()["id"] == job
    assert any(p.startswith("⏳ An update is already running") for p in _popups(q2))


def test_the_lock_held_elsewhere_is_refused(env, run):
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        update, query = _tap(env, env.chat_id, env.chat_id)

        async def scenario():
            await env.bot._handle_callback(update, MagicMock())
        run(scenario)
    finally:
        other.release()
    assert "An update is running elsewhere. Try again once it has finished." in \
        _popups(query)
    assert env.manager.snapshot() is None
    query.message.edit_text.assert_not_awaited()


def test_a_daemon_shutting_down_refuses(env, run):
    env.manager._shutting_down = True
    update, query = _tap(env, env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert "⏳ aipager is shutting down. Try again once it is back." in _popups(query)
    assert env.manager.snapshot() is None


# ---- the Mini App ------------------------------------------------------------------------

def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


def _call(env, run, fn):
    srv = MiniAppServer(env.bot, env.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return run(scenario)


def _post_restart(user_id=None, headers=True):
    async def fn(c):
        r = await c.post("/api/update/restart",
                         headers=_hdr(user_id) if headers else {})
        return r.status, await r.json()
    return fn


def test_restart_route_needs_init_data(env, run):
    assert _call(env, run, _post_restart(headers=False)) == (401, {"error": "unauthorized"})
    assert env.manager.snapshot() is None


def test_restart_route_forbids_a_member(env, run):
    _scoped(env)
    assert _call(env, run, _post_restart(MEMBER)) == (403, {"error": "forbidden"})
    assert env.manager.snapshot() is None


def test_restart_route_is_rate_limited(env, run, monkeypatch):
    monkeypatch.setattr(MiniAppServer, "_allow_write", lambda self, uid: False)
    assert _call(env, run, _post_restart(env.chat_id)) == (429, {"error": "too_many_requests"})
    assert env.manager.snapshot() is None


def test_restart_route_shares_the_write_budget(env, run):
    # 30 writes per minute per person, as every Mini App write: the 31st
    # restart request is refused before it reaches the manager.
    env.add_session(status=Status.BUSY)

    async def burst(c):
        statuses = []
        for _ in range(31):
            r = await c.post("/api/update/restart", headers=_hdr(env.chat_id))
            statuses.append(r.status)
        return statuses
    statuses = _call(env, run, burst)
    assert statuses[0] == 202 and set(statuses[1:30]) == {409} and statuses[30] == 429


def test_restart_route_starts_a_job(env, run):
    async def fn(c):
        r = await c.post("/api/update/restart", headers=_hdr(env.chat_id))
        body = await r.json()
        await env.finish()
        return r.status, body
    status, body = _call(env, run, fn)
    assert status == 202 and body["job"]["kind"] == "restart"
    assert body["job"]["phase"] == "verifying"
    assert len(env.stops) == 1


def test_restart_route_conflicts_while_a_job_runs(env, run):
    env.add_session(status=Status.BUSY)

    async def fn(c):
        a = await c.post("/api/update/restart", headers=_hdr(env.chat_id))
        b = await c.post("/api/update/restart", headers=_hdr(env.chat_id))
        return a.status, b.status, await b.json()
    assert _call(env, run, fn) == (202, 409, {"error": "update_in_progress"})


def test_restart_route_conflicts_when_the_lock_is_held_elsewhere(env, run):
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        assert _call(env, run, _post_restart(env.chat_id)) == (
            409, {"error": "update_in_progress"})
    finally:
        other.release()


def test_restart_route_says_shutting_down(env, run):
    env.manager._shutting_down = True
    assert _call(env, run, _post_restart(env.chat_id)) == (503, {"error": "shutting_down"})


def test_get_update_carries_waiting(env, run):
    async def fn(c):
        before = await (await c.get("/api/update", headers=_hdr(env.chat_id))).json()
        await env.manager.check()
        after = await (await c.get("/api/update", headers=_hdr(env.chat_id))).json()
        return before["waiting"], after["waiting"]
    before, after = _call(env, run, fn)
    assert before is None
    assert after == {"version": "1.1.0", "running": "1.0.1", "source": "installed"}


def test_get_update_shows_can_restart_on_a_finished_update(env, run):
    async def fn(c):
        await env.start_update()
        await env.finish()
        return await (await c.get("/api/update", headers=_hdr(env.chat_id))).json()
    body = _call(env, run, fn)
    assert body["job"]["can_restart"] is True
