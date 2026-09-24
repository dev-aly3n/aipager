"""Iteration 2 contracts, black-box: a pending restart counts as busy;
the update lock is not re-entrant and only its owning job releases it;
the restart watchdog gives up on a restart that never came.

SC-9 (a second update while one runs is refused -- a scheduled but not yet
happened restart is still "running"), SC-4/SC-7 (exactly one restart
scheduled), SC-8 (no stale marker announces a phantom update).
Sources: design.md Success criteria; entrypoints.md (`UpdateManager.start`,
`busy`, `UpdateLock`, callback data, HTTP routes, `cmd_update`); review-1.md
rev-iter1-001 / rev-iter1-002 as restated by the orchestrator.
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

from aipager import self_update, updater
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.miniapp.server import MiniAppServer
from aipager.state import SessionRegistry, Status

START_BUTTONS = ("_:up:cc", "_:up:ap", "_:up:both")


def _marker():
    return self_update.UPDATE_MARKER_PATH


async def _to_pending(bot, h):
    """Drive a real aipager job to `restart_scheduled` and return its id."""
    res, msg, phase = await h.run_job(bot, "aipager")
    assert phase == "restart_scheduled", phase
    return res.job["id"], msg


def _tap(data, h, *, user_id=None, chat_id=None):
    chat_id = chat_id or h.DM
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = h.status_message(chat_id, 710)
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id or h.OPERATOR
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private" if chat_id > 0 else "supergroup"
    return update, query


def _cmd(mk_update, h):
    upd = mk_update("/update", user_id=h.OPERATOR, chat_id=h.DM)
    reply = h.status_message(h.DM, 950)
    upd.message.chat = MagicMock()
    upd.message.chat.id = h.DM
    upd.message.reply_text.return_value = reply
    upd.effective_message = upd.message
    upd.effective_chat.type = "private"
    return upd, reply


# ============================================================================
# A pending restart counts as busy
# ============================================================================

def test_manager_is_busy_while_restart_pending(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        return personal_bot.updates.busy
    assert run_async(go()) is True


def test_start_claude_refused_while_restart_pending(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        return await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
    res = run_async(go())
    assert (res.ok, res.error) == (False, "update_in_progress")


@pytest.mark.parametrize("kind", ["aipager", "both"])
def test_start_aipager_kinds_refused_while_restart_pending(world, personal_bot, h, run_async, kind):
    async def go():
        await _to_pending(personal_bot, h)
        return await personal_bot.updates.start(
            kind, chat_id=h.DM, user_id=h.OPERATOR, origin="miniapp")
    res = run_async(go())
    assert (res.ok, res.error) == (False, "update_in_progress")


def test_refused_start_while_pending_runs_no_claude_update(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.claude_update_calls() == []


def test_refused_start_while_pending_runs_no_second_installer(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert len(world.upgrade_calls()) == 1


def test_refused_start_while_pending_schedules_no_second_restart(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert len(world.schedule_calls()) == 1


def test_refused_start_while_pending_keeps_the_lock_held(world, personal_bot, h, run_async):
    """The refused job must not run a `finally` that frees the flock."""
    async def go():
        await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
        return h.lock_is_free()
    assert run_async(go()) is False


def test_refused_start_while_pending_keeps_the_marker(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
        return _marker().exists()
    assert run_async(go()) is True


def test_refused_start_while_pending_leaves_the_job_pending(world, personal_bot, h, run_async):
    async def go():
        job_id, _ = await _to_pending(personal_bot, h)
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 711))
        await h.wait_for(lambda: False, 0.2)
        snap = personal_bot.updates.snapshot() or {}
        return snap.get("id") == job_id, snap.get("phase")
    assert run_async(go()) == (True, "restart_scheduled")


def test_repeated_starts_while_pending_all_refused(world, personal_bot, h, run_async):
    """Error guess: hammering the start path (double taps, retries)."""
    async def go():
        await _to_pending(personal_bot, h)
        results = []
        for kind in ("claude", "aipager", "both", "claude", "aipager"):
            r = await personal_bot.updates.start(
                kind, chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
                status_message=h.status_message(h.DM, 712))
            results.append(r.error)
        return results
    assert run_async(go()) == ["update_in_progress"] * 5


def test_update_command_while_pending_is_refused_with_a_reply(world, personal_bot, mk_update, h, run_async):
    """Refused, and the reply says why (an update/restart is under way).
    entrypoints.md's wording "An update is already running" is not required
    verbatim here: the pending-restart reply is its own refusal text."""
    upd, reply = _cmd(mk_update, h)

    async def go():
        await _to_pending(personal_bot, h)
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    text = "\n".join(h.texts_of(upd.message, reply)).lower()
    assert "restart" in text and "update" in text


def test_update_command_while_pending_runs_no_version_check(world, personal_bot, mk_update, h, run_async):
    upd, reply = _cmd(mk_update, h)

    async def go():
        await _to_pending(personal_bot, h)
        before = len(world.urls)
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.2)
        return len(world.urls) - before
    assert run_async(go()) == 0


def test_update_command_while_pending_says_no_checking_versions(world, personal_bot, mk_update, h, run_async):
    upd, reply = _cmd(mk_update, h)

    async def go():
        await _to_pending(personal_bot, h)
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert "Checking versions" not in "\n".join(h.texts_of(upd.message, reply))


def test_update_command_while_pending_offers_no_start_buttons(world, personal_bot, mk_update, h, run_async):
    upd, reply = _cmd(mk_update, h)

    async def go():
        await _to_pending(personal_bot, h)
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    datas = [d for _, d in h.buttons_of(upd.message, reply)]
    assert not any(d in START_BUTTONS for d in datas)


@pytest.mark.parametrize("data", START_BUTTONS)
def test_start_button_tap_while_pending_runs_nothing(world, personal_bot, h, run_async, data):
    async def go():
        await _to_pending(personal_bot, h)
        before = len(world.calls)
        update, _q = _tap(data, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.2)
        new = world.calls[before:]
        return [c["argv"] for c in new
                if world.is_upgrade(c["argv"]) or c["argv"][1:] == ["update"]
                or c["argv"][0].endswith("systemd-run")]
    assert run_async(go()) == []


def test_start_button_tap_while_pending_keeps_the_lock_held(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        update, _q = _tap("_:up:cc", h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.2)
        return h.lock_is_free()
    assert run_async(go()) is False


def test_cli_update_refused_while_restart_pending(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        return updater.cmd_update()
    assert run_async(go()) == 1


def test_cli_update_runs_no_installer_while_restart_pending(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        updater.cmd_update()
    run_async(go())
    assert len(world.upgrade_calls()) == 1


def test_cli_refusal_while_pending_keeps_the_marker(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        updater.cmd_update()
        return _marker().exists()
    assert run_async(go()) is True


# ---- Mini App ----------------------------------------------------------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def server(mk_bot, h, world, monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    reg = SessionRegistry()
    bot = mk_bot(reg)
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", h.DM), 804))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, reg, port=8769)


def _http(srv, run_async, h, method, path, body=None):
    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            await _to_pending(srv.bot, h)
            kw = {"headers": {"X-Telegram-Init-Data": _init_data(h.OPERATOR)}}
            if body is not None:
                kw["json"] = body
            resp = await getattr(client, method)(path, **kw)
            try:
                payload = await resp.json()
            except Exception:
                payload = None
            return resp.status, payload
        finally:
            await client.close()
    return run_async(go())


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_miniapp_start_while_restart_pending_is_409(server, run_async, h, action):
    status, body = _http(server, run_async, h, "post", f"/api/update/{action}")
    assert (status, body) == (409, {"error": "update_in_progress"})


def test_miniapp_get_while_pending_reports_restart_scheduled(server, run_async, h):
    _, body = _http(server, run_async, h, "get", "/api/update")
    assert (body.get("job") or {}).get("phase") == "restart_scheduled"


def test_miniapp_refusal_while_pending_runs_no_second_installer(server, run_async, h, world):
    _http(server, run_async, h, "post", "/api/update/aipager")
    assert len(world.upgrade_calls()) == 1


# ============================================================================
# The lock: not re-entrant, only its owner releases it
# ============================================================================

def test_lock_is_not_reentrant_on_the_same_object():
    lock = self_update.UpdateLock()
    assert lock.try_acquire() is True
    try:
        assert lock.try_acquire() is False
    finally:
        lock.release()


def test_refused_reacquire_does_not_drop_the_lock():
    lock = self_update.UpdateLock()
    lock.try_acquire()
    try:
        lock.try_acquire()
        assert self_update.UpdateLock().try_acquire() is False
    finally:
        lock.release()


def test_refused_reacquire_keeps_held_true():
    lock = self_update.UpdateLock()
    lock.try_acquire()
    try:
        lock.try_acquire()
        assert lock.held is True
    finally:
        lock.release()


def test_release_by_a_non_owner_does_not_free_the_lock():
    owner = self_update.UpdateLock()
    owner.try_acquire()
    try:
        self_update.UpdateLock().release()
        assert self_update.UpdateLock().try_acquire() is False
    finally:
        owner.release()


def test_failed_acquirer_release_does_not_free_the_lock():
    owner = self_update.UpdateLock()
    owner.try_acquire()
    loser = self_update.UpdateLock()
    try:
        assert loser.try_acquire() is False
        loser.release()
        assert self_update.UpdateLock().try_acquire() is False
    finally:
        owner.release()


def test_double_release_by_owner_does_not_raise():
    lock = self_update.UpdateLock()
    lock.try_acquire()
    lock.release()
    lock.release()
    assert lock.held is False


def test_owner_can_reacquire_after_release():
    lock = self_update.UpdateLock()
    lock.try_acquire()
    lock.release()
    try:
        assert lock.try_acquire() is True
    finally:
        lock.release()


def test_refused_daemon_start_does_not_release_the_cli_lock(world, personal_bot, h, run_async):
    """Only the owning job releases the lock: a daemon start refused because
    the CLI holds it must leave the CLI's lock alone."""
    cli = self_update.UpdateLock()
    assert cli.try_acquire()
    try:
        async def go():
            await personal_bot.updates.start(
                "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
                status_message=h.status_message(h.DM))
            await h.wait_for(lambda: False, 0.2)
        run_async(go())
        assert self_update.UpdateLock().try_acquire() is False
    finally:
        cli.release()


def test_refused_start_during_running_job_keeps_first_jobs_lock(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 701))
        await h.wait_for(lambda: False, 0.2)
        return h.lock_is_free()
    assert run_async(go()) is False


# ============================================================================
# The restart watchdog
# ============================================================================

@pytest.fixture
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(self_update, "RESTART_DELAY_SECONDS", 0)
    monkeypatch.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 0.3)


async def _watchdog_fired(bot, h):
    await _to_pending(bot, h)
    await h.wait_phase(bot, {"failed", "done", "cancelled"}, timeout=5.0)


def _scheduled_unit(world):
    for c in world.schedule_calls():
        for a in c["argv"]:
            if a.startswith("--unit="):
                return a.split("=", 1)[1]
    return None


def test_watchdog_moves_the_job_to_failed(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        return (personal_bot.updates.snapshot() or {}).get("phase")
    assert run_async(go()) == "failed"


def test_watchdog_releases_the_lock(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        return h.lock_is_free()
    assert run_async(go()) is True


def test_watchdog_clears_the_marker(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        return _marker().exists()
    assert run_async(go()) is False


def test_watchdog_makes_the_manager_idle(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        return personal_bot.updates.busy
    assert run_async(go()) is False


def test_watchdog_stops_the_pending_restart_timer(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    unit = _scheduled_unit(world)
    stops = [c["argv"] for c in world.calls
             if c["argv"][0].rsplit("/", 1)[-1] == "systemctl" and "stop" in c["argv"]]
    assert unit and any(any(a.startswith(unit) and a.endswith(".timer") for a in argv)
                        for argv in stops), (unit, stops)


def test_watchdog_timer_stop_uses_absolute_systemctl(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    stops = [c["argv"] for c in world.calls
             if c["argv"][0].rsplit("/", 1)[-1] == "systemctl" and "stop" in c["argv"]]
    assert stops and all(argv[0].startswith("/") for argv in stops)


def test_watchdog_tells_the_admin_the_manual_command(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        _, msg = await _to_pending(personal_bot, h)
        await h.wait_phase(personal_bot, "failed", timeout=5.0)
        await h.wait_for(lambda: False, 0.1)
        return h.all_text(personal_bot, msg)
    assert "systemctl --user restart aipager.service" in run_async(go())


def test_watchdog_edits_a_new_text_after_the_restarting_message(world, personal_bot, h, run_async, fast_watchdog):
    """The admin is told something new once the restart is given up on."""
    def last(msg):
        texts = [t for t in h.texts_of(msg, personal_bot._app.bot) if len(t) > 20]
        return texts[-1] if texts else ""

    async def go():
        _, msg = await _to_pending(personal_bot, h)
        at_pending = last(msg)
        await h.wait_phase(personal_bot, "failed", timeout=5.0)
        await h.wait_for(lambda: False, 0.1)
        return at_pending, last(msg)
    at_pending, after = run_async(go())
    assert after and after != at_pending


def test_new_update_is_accepted_after_the_watchdog(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _watchdog_fired(personal_bot, h)
        return await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 720))
    assert run_async(go()).ok is True


def test_watchdog_does_not_fire_before_its_deadline(world, personal_bot, h, run_async, monkeypatch):
    """Boundary, just inside: well before the deadline the restart is still
    pending, the marker is present and the lock is held."""
    monkeypatch.setattr(self_update, "RESTART_DELAY_SECONDS", 0)
    monkeypatch.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 30)

    async def go():
        await _to_pending(personal_bot, h)
        await h.wait_for(lambda: False, 0.4)
        return ((personal_bot.updates.snapshot() or {}).get("phase"),
                _marker().exists(), h.lock_is_free())
    assert run_async(go()) == ("restart_scheduled", True, False)


def test_stale_watchdog_does_not_release_a_later_jobs_lock(world, personal_bot, h, run_async, fast_watchdog):
    """Error guess: after the watchdog fires and a new job starts, no
    leftover timer from the old job may free the new job's lock."""
    async def go():
        await _watchdog_fired(personal_bot, h)
        h.add_session(personal_bot.registry, "w", status=Status.BUSY)
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 721))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: False, 0.8)       # > the old watchdog's delay
        return ((personal_bot.updates.snapshot() or {}).get("phase"), h.lock_is_free())
    assert run_async(go()) == ("waiting_for_idle", False)


def test_watchdog_sends_nothing_into_a_muted_chat(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        _, msg = await _to_pending(personal_bot, h)
        edits_before = msg.edit_text.await_count
        sends_before = personal_bot._app.bot.send_message.await_count
        edits2_before = personal_bot._app.bot.edit_message_text.await_count
        MUTE.mute(h.DM, 3600)
        await h.wait_phase(personal_bot, "failed", timeout=5.0)
        await h.wait_for(lambda: False, 0.1)
        return (msg.edit_text.await_count - edits_before
                + personal_bot._app.bot.send_message.await_count - sends_before
                + personal_bot._app.bot.edit_message_text.await_count - edits2_before)
    assert run_async(go()) == 0


def test_watchdog_on_muted_chat_still_releases_the_lock(world, personal_bot, h, run_async, fast_watchdog):
    async def go():
        await _to_pending(personal_bot, h)
        MUTE.mute(h.DM, 3600)
        await h.wait_phase(personal_bot, "failed", timeout=5.0)
        return h.lock_is_free()
    assert run_async(go()) is True


# ============================================================================
# The voice "Restart daemon now" button
# ============================================================================

VOICE_RESTART = "__voice__:restart"


def _voice_restart(bot, h):
    update, query = _tap(VOICE_RESTART, h)
    return update, query


def test_voice_restart_schedules_when_nothing_runs(world, personal_bot, h, run_async):
    """Positive control: with no update running the tap does schedule."""
    async def go():
        update, _q = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: bool(world.schedule_calls()), 2.0)
    run_async(go())
    assert len(world.schedule_calls()) == 1


def test_voice_restart_refused_while_update_waits_for_idle(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        update, _q = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert world.schedule_calls() == []


def test_voice_restart_refused_while_restart_pending(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        update, _q = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert len(world.schedule_calls()) == 1


def test_voice_restart_refused_while_cli_holds_the_lock(world, personal_bot, h, run_async):
    cli = self_update.UpdateLock()
    assert cli.try_acquire()
    try:
        async def go():
            update, _q = _voice_restart(personal_bot, h)
            await personal_bot._handle_callback(update, MagicMock())
            await h.wait_for(lambda: False, 0.3)
        run_async(go())
        assert world.schedule_calls() == []
    finally:
        cli.release()


def test_voice_restart_refusal_while_pending_mentions_the_update(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        update, query = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.3)
        return "\n".join(h.texts_of(query, query.message))
    assert "update" in run_async(go()).lower()


def test_voice_restart_refusal_while_cli_holds_lock_is_explained(world, personal_bot, h, run_async):
    cli = self_update.UpdateLock()
    assert cli.try_acquire()
    try:
        async def go():
            update, query = _voice_restart(personal_bot, h)
            await personal_bot._handle_callback(update, MagicMock())
            await h.wait_for(lambda: False, 0.3)
            return "\n".join(h.texts_of(query, query.message))
        assert "update" in run_async(go()).lower()
    finally:
        cli.release()


def test_voice_restart_refusal_leaves_the_pending_marker(world, personal_bot, h, run_async):
    async def go():
        await _to_pending(personal_bot, h)
        update, _q = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.3)
        return _marker().exists()
    assert run_async(go()) is True


def test_voice_restart_refusal_keeps_the_cli_lock(world, personal_bot, h, run_async):
    cli = self_update.UpdateLock()
    assert cli.try_acquire()
    try:
        async def go():
            update, _q = _voice_restart(personal_bot, h)
            await personal_bot._handle_callback(update, MagicMock())
            await h.wait_for(lambda: False, 0.3)
        run_async(go())
        assert self_update.UpdateLock().try_acquire() is False
    finally:
        cli.release()


def test_voice_restart_allowed_again_after_the_watchdog(world, personal_bot, h, run_async, fast_watchdog):
    """Boundary, just outside: once the pending restart is given up on,
    the voice restart is no longer blocked by it."""
    async def go():
        await _watchdog_fired(personal_bot, h)
        n = len(world.schedule_calls())
        update, _q = _voice_restart(personal_bot, h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: len(world.schedule_calls()) > n, 2.0)
        return len(world.schedule_calls()) - n
    assert run_async(go()) == 1
