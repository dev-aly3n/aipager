"""One "Check for updates" button, then one "Update" button (roadmap 8.43).

Operator request 2026-09-25: the three always-on update buttons (Update
Claude Code / Update aipager / Both) are replaced, in the Mini App and in
`/update`, by one "Check for updates" button. Nothing is looked up until
it is tapped. The check shows one line per product and ONE button that
updates only the products that have an update. Both surfaces take that
decision from the same function (`update_flow.update_offer`), and every
start goes through `UpdateManager.start_offer`, which re-derives the offer
from the last check instead of trusting the button.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import self_update
from aipager.bot import update_flow
from aipager.install_source import InstallSource
from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -100777
ADMIN, MEMBER, STRANGER = 555, 777, 999999
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
OUT_OF_DATE = "This menu is out of date, send /update again"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


# ----- helpers --------------------------------------------------------------------

class _Role:
    def __init__(self, bypass):
        self.bypass_safety = bypass
        self.can_prompt = True
        self.bypass_role_denies = False


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


def _cmd_update(env, user_id, chat_id):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "/update"
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.message.reply_text = AsyncMock(return_value=env.message)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


def _tap(env, data, user_id, chat_id):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = env._mk_message(chat_id)
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def _toasts(query):
    return [c.args[0] for c in query.answer.await_args_list if c.args]


async def _drain(env):
    for task in list(env.manager._tasks):
        if task is not env.manager._job_task:
            await task


def _only_claude_newer(env):
    env.pypi_latest = env.running            # aipager up to date


def _only_aipager_newer(env):
    env.claude_latest = env.claude_version   # Claude Code up to date


def _nothing_newer(env):
    _only_claude_newer(env)
    _only_aipager_newer(env)


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
            return await fn(client, srv)
        finally:
            await client.close()
    return run(scenario)


# ----- the shared decision ------------------------------------------------------------

def _status(env, run):
    async def scenario():
        return await env.manager.check()
    return run(scenario)


def test_offer_lines_show_the_arrow_and_up_to_date(env, run):
    _only_aipager_newer(env)
    offer = update_flow.update_offer(_status(env, run))
    assert offer.lines == ["aipager 0.7.13 → 0.7.14", "Claude Code 2.1.281 (up to date)"]
    assert offer.kind == "aipager"
    assert offer.label == "Update aipager"


def test_offer_covers_only_claude_when_only_claude_is_newer(env, run):
    _only_claude_newer(env)
    offer = update_flow.update_offer(_status(env, run))
    assert offer.lines == ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 → 2.1.290"]
    assert (offer.kind, offer.label) == ("claude", "Update Claude Code")
    assert offer.restart is None      # no aipager update, no restart note


def test_offer_is_both_when_both_are_newer(env, run):
    offer = update_flow.update_offer(_status(env, run))
    assert (offer.kind, offer.label) == ("both", "Update both")
    assert offer.restart == "Restart: automatic, once no turn is running."


def test_nothing_newer_offers_nothing(env, run):
    _nothing_newer(env)
    offer = update_flow.update_offer(_status(env, run))
    assert offer.kind is None and offer.label is None
    assert offer.summary == "Everything is up to date."


def test_failed_lookup_says_couldnt_check(env, run):
    env.claude_latest = None
    _only_claude_newer(env)
    offer = update_flow.update_offer(_status(env, run))
    assert offer.lines == ["aipager 0.7.13 (up to date)",
                           "Claude Code 2.1.281 (couldn't check)"]
    assert offer.kind is None
    # A failed check is not "everything is up to date".
    assert offer.summary != "Everything is up to date."


def test_newer_aipager_that_cannot_be_updated_here_is_not_offered(env, run):
    _only_aipager_newer(env)
    env.source = InstallSource(kind="editable", prefix="/src", python="/src/python",
                               reason="this is an editable (development) install")
    offer = update_flow.update_offer(_status(env, run))
    assert offer.kind is None
    assert "can't update from here" in offer.lines[0]


def test_new_user_facing_text_has_no_em_dash(env, run):
    for tweak in (lambda e: None, _nothing_newer):
        tweak(env)
        status = _status(env, run)
        text, _ = update_flow.render_check(status, show_paths=True)
        assert "—" not in text
        payload = update_flow.check_payload(status, show_paths=True)
        assert "—" not in json.dumps(payload, ensure_ascii=False)
    assert "—" not in update_flow.CHECK_PROMPT_TEXT
    assert "—" not in OUT_OF_DATE


# ----- Telegram --------------------------------------------------------------------------

def test_update_cmd_sends_one_check_button_and_looks_nothing_up(env, run):
    update = _cmd_update(env, env.chat_id, env.chat_id)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
        await _drain(env)
    run(scenario)
    call = update.message.reply_text.await_args
    markup = call.kwargs["reply_markup"]
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [("🔄 Check for updates", "_:up:chk")]
    assert env.fetches == [] and env.calls == []


def test_check_tap_shows_lines_and_one_update_button(env, run):
    _only_claude_newer(env)
    update, query = _tap(env, "_:up:chk", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert "Checking…" in _toasts(query)
    text = env.last_text()
    assert "aipager 0.7.13 (up to date)" in text
    assert "Claude Code 2.1.281 → 2.1.290" in text
    assert env.last_markup_data() == ["_:up:go:cc", "_:up:x"]
    labels = [b.text for row in env.edits[-1]["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Update Claude Code", "Cancel"]
    assert "Restart:" not in text


def test_check_tap_with_both_newer_offers_update_both(env, run):
    update, query = _tap(env, "_:up:chk", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert env.last_markup_data() == ["_:up:go:both", "_:up:x"]
    assert "Restart: automatic, once no turn is running." in env.last_text()


def test_check_tap_with_nothing_newer_offers_no_update(env, run):
    _nothing_newer(env)
    update, query = _tap(env, "_:up:chk", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert "Everything is up to date." in env.last_text()
    assert env.last_markup_data() == ["_:up:chk"]


def test_check_tap_failed_lookup_says_couldnt_check(env, run):
    env.claude_latest = None
    update, query = _tap(env, "_:up:chk", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert "Claude Code 2.1.281 (couldn't check)" in env.last_text()
    assert env.last_markup_data() == ["_:up:go:ap", "_:up:x"]


def test_update_tap_runs_only_the_offered_product(env, run):
    _only_claude_newer(env)
    chk, _ = _tap(env, "_:up:chk", env.chat_id, env.chat_id)
    go, _ = _tap(env, "_:up:go:cc", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(chk, MagicMock())
        await _drain(env)
        await env.bot._handle_callback(go, MagicMock())
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["kind"] == "claude"
    assert env.manager.snapshot()["phase"] == "done"
    assert env.upgrade_calls() == []


def test_update_tap_that_disagrees_with_the_check_starts_nothing(env, run):
    """A button whose product set no longer matches the last check (the
    check found only Claude Code; the tap says both) starts nothing."""
    _only_claude_newer(env)
    chk, _ = _tap(env, "_:up:chk", env.chat_id, env.chat_id)
    go, go_query = _tap(env, "_:up:go:both", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(chk, MagicMock())
        await _drain(env)
        await env.bot._handle_callback(go, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None
    call = go_query.edit_message_text.await_args
    assert "out of date" in call.args[0]
    markup = call.kwargs["reply_markup"]
    assert [b.callback_data for row in markup.inline_keyboard for b in row] == ["_:up:chk"]


def test_update_tap_without_a_check_starts_nothing(env, run):
    go, _ = _tap(env, "_:up:go:ap", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(go, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None
    assert env.calls == [] and env.fetches == []


def test_update_tap_after_the_check_expired_starts_nothing(env, run, monkeypatch):
    chk, _ = _tap(env, "_:up:chk", env.chat_id, env.chat_id)
    go, _ = _tap(env, "_:up:go:both", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(chk, MagicMock())
        await _drain(env)
        monkeypatch.setattr(update_flow, "OFFER_MAX_AGE_SECONDS", -1.0)
        await env.bot._handle_callback(go, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None


def test_a_finished_job_voids_the_check(env, run):
    """After a job the versions have moved: an Update button from before it
    must not start a second job."""
    _only_claude_newer(env)

    async def scenario():
        await env.manager.check()
        res = await env.manager.start_offer("claude", chat_id=env.chat_id,
                                            user_id=env.chat_id, origin="chat",
                                            status_message=env.message)
        assert res.ok
        await env.finish()
        return await env.manager.start_offer("claude", chat_id=env.chat_id,
                                             user_id=env.chat_id, origin="chat")
    res = run(scenario)
    assert (res.ok, res.error) == (False, "check_expired")


@pytest.mark.parametrize("old", ["_:up:cc", "_:up:ap", "_:up:both"])
def test_old_update_buttons_fail_safe(old, env, run):
    update, query = _tap(env, old, env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None
    assert env.calls == [] and env.fetches == []
    assert OUT_OF_DATE in _toasts(query)


@pytest.mark.parametrize("data", ["_:up:chk", "_:up:go:cc", "_:up:go:ap", "_:up:go:both"])
def test_new_buttons_keep_the_admin_gate(data, env, run):
    _scoped(env)
    update, query = _tap(env, data, MEMBER, GROUP)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert env.manager.snapshot() is None
    assert env.calls == [] and env.fetches == []
    assert "🚫 Only the admin can update aipager." in _toasts(query)


def test_the_group_admin_can_check_and_sees_no_path(env, run):
    _scoped(env)
    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)
    update, _ = _tap(env, "_:up:chk", ADMIN, GROUP)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
    run(scenario)
    assert "aipager 0.7.13 → 0.7.14" in env.last_text()
    assert "/home/op/aipager" not in env.last_text()
    assert "pipx, from a local path" in env.last_text()


def test_check_while_an_update_runs_says_so(env, run):
    env.upgrade_release = threading.Event()
    update, _ = _tap(env, "_:up:chk", env.chat_id, env.chat_id)

    async def scenario():
        await env.manager.check()
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        fetched = len(env.fetches)
        await env.bot._handle_callback(update, MagicMock())
        await _drain(env)
        env.upgrade_release.set()
        await env.finish()
        return fetched
    fetched = run(scenario)
    assert "already running" in update.callback_query.edit_message_text.await_args.args[0]
    assert len(env.fetches) == fetched


# ----- the lock -------------------------------------------------------------------------

def _gate_reached(env):
    return (env.manager.snapshot() or {}).get("phase") == "waiting_for_idle"


def test_the_lock_still_prevents_two_updates_at_once(env, run):
    _only_aipager_newer(env)
    env.add_session(status=Status.BUSY)   # the aipager job waits on the gate

    async def scenario():
        await env.manager.check()
        first = await env.manager.start_offer("aipager", chat_id=env.chat_id,
                                              user_id=env.chat_id, origin="chat",
                                              status_message=env.message)
        second = await env.manager.start_offer("aipager", chat_id=env.chat_id,
                                               user_id=env.chat_id, origin="miniapp")
        await env.until(lambda: _gate_reached(env))
        assert env.manager.control("cancel", first.job["id"]).ok
        await env.finish()
        return first, second
    first, second = run(scenario)
    assert first.ok
    assert (second.ok, second.error) == (False, "update_in_progress")


# ----- the Mini App routes -------------------------------------------------------------

def test_page_load_makes_no_version_lookup(env, run):
    async def fn(c, srv):
        r = await c.get("/api/update", headers=_hdr(env.chat_id))
        return r.status, await r.json()
    status, body = _call(env, run, fn)
    assert status == 200
    # Roadmap 8.45 added the in-process restart-watch fields (was exactly
    # {"job": None}); still nothing looked up.
    assert body["job"] is None
    assert set(body) == {"job", "version", "restarted", "url_changes_on_restart"}
    assert env.fetches == [] and env.calls == []


def test_check_route_returns_the_shared_lines_and_offer(env, run):
    _only_claude_newer(env)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return r.status, await r.json()
    status, body = _call(env, run, fn)
    assert status == 200
    check = body["check"]
    assert check["lines"] == ["aipager 0.7.13 (up to date)",
                              "Claude Code 2.1.281 → 2.1.290"]
    assert check["offer"] == {"kind": "claude", "label": "Update Claude Code"}
    assert check["restart"] is None
    assert check["summary"] is None
    assert body["job"] is None


def test_check_route_nothing_newer(env, run):
    _nothing_newer(env)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return await r.json()
    check = _call(env, run, fn)["check"]
    assert check["offer"] is None
    assert check["summary"] == "Everything is up to date."


def test_check_route_hides_paths_for_a_group(env, run):
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


def test_check_route_keeps_paths_in_a_private_scope(env, run):
    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return await r.json()
    assert "/home/op/aipager" in _call(env, run, fn)["check"]["source"]


def test_start_route_runs_the_offered_update(env, run):
    _only_claude_newer(env)

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "claude"})
        body = await r.json()
        await env.finish()
        return r.status, body
    status, body = _call(env, run, fn)
    assert status == 202 and body["job"]["kind"] == "claude"
    assert env.upgrade_calls() == []


def test_start_route_refuses_a_kind_the_check_did_not_offer(env, run):
    _only_claude_newer(env)

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "both"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (409, {"error": "offer_changed"})
    assert env.manager.snapshot() is None


def test_start_route_without_a_check_is_refused(env, run):
    async def fn(c, srv):
        r = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "both"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (409, {"error": "check_expired"})
    assert env.fetches == [] and env.calls == []


def test_start_route_rejects_a_bad_body(env, run):
    async def fn(c, srv):
        a = await c.post("/api/update/start", headers=_hdr(env.chat_id), data="nope")
        b = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "reboot"})
        return a.status, b.status
    assert _call(env, run, fn) == (400, 400)


@pytest.mark.parametrize("old", ["claude", "aipager", "both"])
def test_old_start_routes_fail_safe(old, env, run):
    async def fn(c, srv):
        r = await c.post(f"/api/update/{old}", headers=_hdr(env.chat_id))
        return r.status, await r.json()
    assert _call(env, run, fn) == (410, {"error": "menu_out_of_date"})
    assert env.manager.snapshot() is None
    assert env.calls == [] and env.fetches == []


@pytest.mark.parametrize("path", ["/api/update/check", "/api/update/start"])
def test_check_and_start_routes_keep_the_admin_gate(path, env, run):
    _scoped(env)

    async def fn(c, srv):
        r = await c.post(path, headers=_hdr(MEMBER), json={"kind": "both"})
        s = await c.post(path, headers=_hdr(STRANGER), json={"kind": "both"})
        return r.status, s.status
    assert _call(env, run, fn) == (403, 403)
    assert env.fetches == [] and env.calls == []
    assert env.manager.snapshot() is None


def test_check_route_is_rate_limited(env, run, monkeypatch):
    monkeypatch.setattr(MiniAppServer, "_allow_write", lambda self, uid: False)

    async def fn(c, srv):
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return r.status
    assert _call(env, run, fn) == 429
    assert env.fetches == []


def test_two_starts_from_the_mini_app_take_the_lock_once(env, run):
    _only_aipager_newer(env)
    env.add_session(status=Status.BUSY)

    async def fn(c, srv):
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        a = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "aipager"})
        b = await c.post("/api/update/start", headers=_hdr(env.chat_id),
                         json={"kind": "aipager"})
        body = await a.json()
        await env.until(lambda: _gate_reached(env))
        assert env.manager.control("cancel", body["job"]["id"]).ok
        await env.finish()
        return a.status, b.status, await b.json()
    assert _call(env, run, fn) == (202, 409, {"error": "update_in_progress"})


def test_check_route_runs_real_lookups(env, run):
    """The check itself does look versions up (and fresh, not cached)."""
    async def fn(c, srv):
        await env.manager.status()     # warm the cache
        before = len(env.fetches)
        await c.post("/api/update/check", headers=_hdr(env.chat_id))
        return before
    before = _call(env, run, fn)
    assert self_update.PYPI_URL in env.fetches[before:]


# ----- review iter 1: a check that spans a job is not remembered ----------------------

def test_a_check_spanning_a_job_end_is_not_remembered(env, run, monkeypatch):
    """rev-iter1-001: a check whose lookups started before a job ended and
    finished after it must not store its pre-job versions, or the Update
    button for the product just updated would work again."""
    _only_claude_newer(env)
    real_status = update_flow.UpdateManager.status

    async def slow_status(self, *, force=False):
        data = await real_status(self, force=force)
        # A job starts and finishes while this lookup is in flight.
        res = await self.start("claude", chat_id=env.chat_id, user_id=env.chat_id,
                               origin="chat", status_message=env.message)
        assert res.ok
        await env.finish()
        return data

    async def scenario():
        monkeypatch.setattr(update_flow.UpdateManager, "status", slow_status)
        await env.manager.check()
        monkeypatch.setattr(update_flow.UpdateManager, "status", real_status)
        return await env.manager.start_offer("claude", chat_id=env.chat_id,
                                             user_id=env.chat_id, origin="chat")
    res = run(scenario)
    assert (res.ok, res.error) == (False, "check_expired")
    assert len([c for c in env.calls if c[-1:] == ["update"]]) == 1


def test_check_route_while_an_update_runs_is_409_and_looks_nothing_up(env, run):
    """rev-iter1-002: the Mini App refuses a check during a job, as the chat
    does."""
    env.upgrade_release = threading.Event()

    async def fn(c, srv):
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        fetched = len(env.fetches)
        r = await c.post("/api/update/check", headers=_hdr(env.chat_id))
        body = await r.json()
        env.upgrade_release.set()
        await env.finish()
        return r.status, body, len(env.fetches) - fetched
    assert _call(env, run, fn) == (409, {"error": "update_in_progress"}, 0)
