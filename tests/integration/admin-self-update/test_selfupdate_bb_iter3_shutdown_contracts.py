"""Iteration 3 contracts, black-box: nothing restarts or starts once the
daemon's shutdown has begun, the shutdown is bounded as a whole, and bot
tokens never leak through an output tail however the 500-char cap or the
surrounding text cuts them.

Sources: design.md Success criteria (SC-4/SC-5/SC-8/SC-9, spec safety
rules: never restart without the operator's intent, one daemon, never two
updates at once, secrets never printed); entrypoints.md (`UpdateManager`,
`start()` errors, POST /api/update, `run_command`, `deliver_update_marker`,
the marker file and its keys, `LINUX_UNIT_TEMPLATE`); review-2.md
rev-iter2-001/-002/-003 as restated by the orchestrator; docs/commands.md
("Once a shutdown has begun, nothing is restarted and nothing new starts").
Public names used beyond entrypoints.md: `UpdateManager.shutdown()`,
`update_flow.SHUTDOWN_GRACE_SECONDS` / `SHUTDOWN_DEADLINE_SECONDS`,
`self_update.terminate_running_commands` (docstrings only).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import threading
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import self_update, service, updater
from aipager.bot import update_flow
from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

# A bot token whose secret half has no run of the fillers used around it.
TOKEN_ID = "987654321"
TOKEN_SECRET = "AAHzQ9xKpLm2Vw7RtY4nBc8DjFe6GhS1uIo3"
TOKEN = f"{TOKEN_ID}:{TOKEN_SECRET}"


def _marker():
    return self_update.UPDATE_MARKER_PATH


def _marker_data():
    return json.loads(_marker().read_text())


# ============================================================================
# A fake installer the test finishes or the shutdown kills
# ============================================================================

class _Installer:
    """Blocks in the spawn seam until the test lets it finish (success) or
    the shutdown's kill stops it (-9). Does NOT fake the live-child count:
    the public seam counts a call as in flight from entry."""

    def __init__(self, world, monkeypatch, *, target="aipager", hook=None):
        self.world = world
        self.target = target
        self.hook = hook
        self.started = threading.Event()
        self.release = threading.Event()
        self.killed = False
        self.kill_calls = 0
        self._real = world._run_command
        monkeypatch.setattr(self_update, "_run_command", self._run)
        monkeypatch.setattr(self_update, "terminate_running_commands", self._terminate)

    def _is_target(self, argv):
        if self.target == "aipager":
            return self.world.is_upgrade(argv)
        return argv[1:] == ["update"]

    def _run(self, argv, *, timeout, env=None, capture=True):
        argv = [str(a) for a in argv]
        if not self._is_target(argv):
            return self._real(argv, timeout=timeout, env=env, capture=capture)
        self.world.calls.append({"argv": argv, "timeout": timeout, "env": env})
        self.started.set()
        self.release.wait(5.0)
        if self.killed:
            return self_update.CommandResult(returncode=-9, output_tail="Killed",
                                             timed_out=False, error=None)
        if self.hook:
            self.hook()
        if self.target == "claude":
            self.world.claude_version = self.world.claude_after
            return self_update.CommandResult(returncode=0, output_tail="ok",
                                             timed_out=False, error=None)
        return self_update.CommandResult(returncode=0, output_tail="upgraded",
                                         timed_out=False, error=None)

    def _terminate(self):
        self.kill_calls += 1
        self.killed = True
        self.release.set()
        return 1

    def close(self):
        self.release.set()


@pytest.fixture
def graceful(monkeypatch):
    """A grace long enough that "finishes within the grace" is certain."""
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 2.0)


@pytest.fixture
def installer(world, monkeypatch, graceful):
    inst = _Installer(world, monkeypatch)
    yield inst
    inst.close()


@pytest.fixture
def claude_installer(world, monkeypatch, graceful):
    inst = _Installer(world, monkeypatch, target="claude")
    yield inst
    inst.close()


async def _start(bot, h, kind="aipager"):
    msg = h.status_message(h.DM)
    res = await bot.updates.start(kind, chat_id=h.DM, user_id=h.OPERATOR,
                                  origin="chat", status_message=msg)
    assert res.ok, res.error
    return msg


async def _finish_during_shutdown(bot, h, inst, kind="aipager"):
    """Start a job, let its installer block, begin the shutdown, and only
    then let the installer finish by itself (inside the grace). Returns
    (status message, len(world.calls) when shutdown began)."""
    msg = await _start(bot, h, kind)
    await h.wait_for(inst.started.is_set, 5.0)
    assert inst.started.is_set()
    n_before = len(inst.world.calls)
    task = asyncio.ensure_future(bot.updates.shutdown())
    await asyncio.sleep(0)            # shutdown()'s first line has run
    inst.release.set()                # the installer ends by itself
    await task
    await h.wait_phase(bot, h.TERMINAL, 2.0)
    await h.wait_for(lambda: False, 0.1)
    return msg, n_before


def _new_daemon(mk_bot, h):
    reg = SessionRegistry()
    h.add_session(reg, "alpha", status=Status.IDLE)
    bot = mk_bot(reg)
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", h.DM), 805))
    return bot, reg


# ============================================================================
# rev-iter2-001: an install that completes after shutdown began
# ============================================================================

def test_install_finishing_in_grace_schedules_no_restart(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert world.schedule_calls() == []


def test_install_finishing_in_grace_is_not_killed(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert installer.kill_calls == 0


def test_install_finishing_in_grace_ends_the_job_done(world, personal_bot, h, run_async, installer):
    async def go():
        await _finish_during_shutdown(personal_bot, h, installer)
        return (personal_bot.updates.snapshot() or {}).get("phase")
    assert run_async(go()) == "done"


def test_install_finishing_in_grace_writes_a_marker(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert _marker().exists()


def test_install_finishing_in_grace_marker_points_to_the_new_version(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert _marker_data().get("to") == h.LATEST


def test_install_finishing_in_grace_marker_records_the_old_version(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert _marker_data().get("from") == h.RUNNING


def test_install_finishing_in_grace_marker_names_the_requesting_chat(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert _marker_data().get("chat_id") == h.DM


def test_install_finishing_in_grace_spawns_nothing_new(world, personal_bot, h, run_async, installer):
    """No systemctl show, no systemd-run, no probe through the seam once
    the shutdown has begun."""
    async def go():
        _, n = await _finish_during_shutdown(personal_bot, h, installer)
        return [c["argv"] for c in world.calls[n:]]
    assert run_async(go()) == []


def test_install_finishing_in_grace_does_not_announce_a_restart(world, personal_bot, h, run_async, installer):
    async def go():
        msg, _ = await _finish_during_shutdown(personal_bot, h, installer)
        return h.all_text(personal_bot, msg)
    assert "restarting in" not in run_async(go()).lower()


def test_install_finishing_in_grace_status_names_the_new_version(world, personal_bot, h, run_async, installer):
    async def go():
        msg, _ = await _finish_during_shutdown(personal_bot, h, installer)
        return h.all_text(personal_bot, msg)
    assert h.LATEST in run_async(go())


def test_install_finishing_in_grace_releases_the_lock(world, personal_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    assert h.lock_is_free()


def test_install_finishing_in_grace_is_announced_by_the_next_daemon(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    world.running = h.LATEST
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert f"aipager updated {h.RUNNING} → {h.LATEST}" in "\n".join(h.texts_of(bot._app.bot))


def test_install_finishing_in_grace_next_daemon_does_not_say_interrupted(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_finish_during_shutdown(personal_bot, h, installer))
    world.running = h.LATEST
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "interrupted" not in "\n".join(h.texts_of(bot._app.bot)).lower()


def test_install_finishing_in_grace_after_restart_now_bypass_schedules_no_restart(world, personal_bot, h, run_async, installer):
    """Restart now skips the post-install gate; it must not skip the
    shutdown check before scheduling."""
    s = h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res = await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                               origin="chat", status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("restart-now", res.job["id"])
        await h.wait_for(installer.started.is_set, 5.0)
        task = asyncio.ensure_future(personal_bot.updates.shutdown())
        await asyncio.sleep(0)
        installer.release.set()
        await task
        await h.wait_for(lambda: False, 0.1)
        s.status = Status.IDLE
    run_async(go())
    assert world.schedule_calls() == []


# ---- the shutdown lands in the post-install gate wait ---------------------

def _post_install_gate(world, personal_bot, h, run_async, *, idle_after=False):
    """The install completes while no shutdown is running; a session turns
    busy during the install, so the job waits in the post-install gate
    when the shutdown begins."""
    state = {}

    def hook():
        state["s"] = h.add_session(personal_bot.registry, "late", status=Status.BUSY)
    world.upgrade_hook = hook

    async def go():
        await _start(personal_bot, h)
        await h.wait_for(lambda: bool(world.upgrade_calls())
                         and (personal_bot.updates.snapshot() or {}).get("phase") == "waiting_for_idle",
                         5.0)
        assert world.upgrade_calls(), "installer never ran"
        await personal_bot.updates.shutdown()
        if idle_after:
            state["s"].status = Status.IDLE     # the gate would now open
        await h.wait_for(lambda: False, 0.3)
    run_async(go())


def test_shutdown_in_post_install_gate_schedules_no_restart(world, personal_bot, h, run_async):
    _post_install_gate(world, personal_bot, h, run_async)
    assert world.schedule_calls() == []


def test_shutdown_in_post_install_gate_then_idle_schedules_no_restart(world, personal_bot, h, run_async):
    _post_install_gate(world, personal_bot, h, run_async, idle_after=True)
    assert world.schedule_calls() == []


def test_shutdown_in_post_install_gate_leaves_the_update_marker(world, personal_bot, h, run_async):
    """B is already on disk: the next start runs B and should say so."""
    _post_install_gate(world, personal_bot, h, run_async)
    assert _marker().exists() and _marker_data().get("to") == h.LATEST


def test_shutdown_in_post_install_gate_next_daemon_announces_the_update(world, personal_bot, mk_bot, h, run_async):
    _post_install_gate(world, personal_bot, h, run_async)
    world.running = h.LATEST
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert f"aipager updated {h.RUNNING} → {h.LATEST}" in "\n".join(h.texts_of(bot._app.bot))


def test_shutdown_in_post_install_gate_restart_now_schedules_nothing(world, personal_bot, h, run_async):
    """Error guess: a stale Restart now tap arriving after the shutdown."""
    _post_install_gate(world, personal_bot, h, run_async)
    snap = personal_bot.updates.snapshot() or {}
    if snap.get("id") is not None:
        personal_bot.updates.control("restart-now", snap["id"])

    async def settle():
        await h.wait_for(lambda: False, 0.2)
    run_async(settle())
    assert world.schedule_calls() == []


# ---- Claude Code, and Both ------------------------------------------------

def test_claude_update_finishing_in_grace_ends_the_job_done(world, personal_bot, h, run_async, claude_installer):
    async def go():
        await _finish_during_shutdown(personal_bot, h, claude_installer, kind="claude")
        return (personal_bot.updates.snapshot() or {}).get("phase")
    assert run_async(go()) == "done"


def test_claude_update_finishing_in_grace_spawns_nothing_new(world, personal_bot, h, run_async, claude_installer):
    """Not even the `claude --version` probe after the update."""
    async def go():
        _, n = await _finish_during_shutdown(personal_bot, h, claude_installer, kind="claude")
        return [c["argv"] for c in world.calls[n:]]
    assert run_async(go()) == []


def test_claude_update_killed_by_shutdown_spawns_no_version_probe(world, personal_bot, h, run_async, claude_installer, monkeypatch):
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.1)

    async def go():
        await _start(personal_bot, h, "claude")
        await h.wait_for(claude_installer.started.is_set, 5.0)
        n = len(world.calls)
        await personal_bot.updates.shutdown()
        await h.wait_for(lambda: False, 0.2)
        return [c["argv"] for c in world.calls[n:]]
    assert run_async(go()) == []


def test_both_claude_finishing_in_grace_does_not_start_the_aipager_install(world, personal_bot, h, run_async, claude_installer):
    run_async(_finish_during_shutdown(personal_bot, h, claude_installer, kind="both"))
    assert world.upgrade_calls() == []


def test_both_claude_finishing_in_grace_schedules_no_restart(world, personal_bot, h, run_async, claude_installer):
    run_async(_finish_during_shutdown(personal_bot, h, claude_installer, kind="both"))
    assert world.schedule_calls() == []


def test_both_claude_finishing_in_grace_leaves_no_aipager_update_marker(world, personal_bot, h, run_async, claude_installer):
    """aipager was never installed, so the next start must not claim A → B."""
    run_async(_finish_during_shutdown(personal_bot, h, claude_installer, kind="both"))
    assert not _marker().exists() or _marker_data().get("to") != h.LATEST


# ---- a pending restart IS the shutdown ------------------------------------

def test_shutdown_with_pending_restart_does_not_stop_the_restart_timer(world, personal_bot, h, run_async):
    async def go():
        _, _, phase = await h.run_job(personal_bot, "aipager")
        assert phase == "restart_scheduled"
        n = len(world.calls)
        await personal_bot.updates.shutdown()
        return [c["argv"] for c in world.calls[n:]]
    assert not any("stop" in argv for argv in run_async(go()))


def test_shutdown_with_pending_restart_schedules_no_second_restart(world, personal_bot, h, run_async):
    async def go():
        await h.run_job(personal_bot, "aipager")
        await personal_bot.updates.shutdown()
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert len(world.schedule_calls()) == 1


# ============================================================================
# rev-iter2-002: start() refuses once the shutdown has begun
# ============================================================================

async def _start_after_shutdown(bot, h, kind):
    await bot.updates.shutdown()
    return await bot.updates.start(kind, chat_id=h.DM, user_id=h.OPERATOR,
                                   origin="chat", status_message=h.status_message(h.DM))


@pytest.mark.parametrize("kind", ["claude", "aipager", "both"])
def test_start_after_shutdown_is_refused(world, personal_bot, h, run_async, kind):
    res = run_async(_start_after_shutdown(personal_bot, h, kind))
    assert res.ok is False


@pytest.mark.parametrize("kind", ["claude", "aipager", "both"])
def test_start_after_shutdown_error_is_shutting_down(world, personal_bot, h, run_async, kind):
    res = run_async(_start_after_shutdown(personal_bot, h, kind))
    assert res.error == "shutting_down"


@pytest.mark.parametrize("kind", ["claude", "aipager", "both"])
def test_start_after_shutdown_spawns_nothing(world, personal_bot, h, run_async, kind):
    async def go():
        await _start_after_shutdown(personal_bot, h, kind)
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.calls == []


def test_start_after_shutdown_creates_no_job(world, personal_bot, h, run_async):
    run_async(_start_after_shutdown(personal_bot, h, "claude"))
    assert personal_bot.updates.snapshot() is None


def test_start_after_shutdown_takes_no_lock(world, personal_bot, h, run_async):
    run_async(_start_after_shutdown(personal_bot, h, "aipager"))
    assert h.lock_is_free()


def test_start_while_shutdown_is_in_progress_is_shutting_down(world, personal_bot, h, run_async, installer):
    """Boundary: the shutdown has begun but not returned (it is waiting on
    a running installer). The refusal is `shutting_down`, not
    `update_in_progress`."""
    async def go():
        await _start(personal_bot, h)
        await h.wait_for(installer.started.is_set, 5.0)
        task = asyncio.ensure_future(personal_bot.updates.shutdown())
        await asyncio.sleep(0)
        res = await personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                               origin="chat",
                                               status_message=h.status_message(h.DM))
        installer.release.set()
        await task
        return res.error
    assert run_async(go()) == "shutting_down"


def test_start_before_shutdown_is_still_accepted(world, personal_bot, h, run_async):
    """Equivalence partner: the refusal is tied to the shutdown."""
    async def go():
        res = await personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                               origin="chat",
                                               status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, h.TERMINAL)
        return res.ok
    assert run_async(go()) is True


def test_start_while_busy_without_shutdown_stays_update_in_progress(world, personal_bot, h, run_async):
    """The other refusal keeps its own error."""
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        res = await personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                               origin="chat",
                                               status_message=h.status_message(h.DM))
        return res.error
    assert run_async(go()) == "update_in_progress"


def test_control_after_shutdown_on_a_cancelled_gate_job_runs_no_installer(world, personal_bot, h, run_async):
    """Error guess: Restart now tapped on a job the shutdown cancelled in
    its pre-install gate."""
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res = await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                               origin="chat",
                                               status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.shutdown()
        personal_bot.updates.control("restart-now", res.job["id"])
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert world.upgrade_calls() == [] and world.schedule_calls() == []


# ---- the chat buttons ------------------------------------------------------

def _tap(data, *, user_id, chat_id, h):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = h.status_message(chat_id, 42)
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private" if chat_id > 0 else "supergroup"
    return update, query


def _tap_after_shutdown(bot, h, run_async, data):
    update, query = _tap(data, user_id=h.OPERATOR, chat_id=h.DM, h=h)

    async def go():
        await bot.updates.shutdown()
        await bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    return query


@pytest.mark.parametrize("data", ["_:up:cc", "_:up:ap", "_:up:both"])
def test_tap_after_shutdown_runs_nothing(world, personal_bot, h, run_async, data):
    _tap_after_shutdown(personal_bot, h, run_async, data)
    assert world.calls == []


def test_tap_after_shutdown_says_aipager_is_shutting_down(world, personal_bot, h, run_async):
    query = _tap_after_shutdown(personal_bot, h, run_async, "_:up:cc")
    text = "\n".join(h.texts_of(personal_bot._app.bot, query, query.message))
    assert "shutting down" in text.lower()


# ---- the Mini App: 503, other refusals stay 409 ---------------------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP = -100
ADMIN = 555
MEMBER = 777


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


@pytest.fixture
def team_server(mk_bot, h, world, monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    reg = SessionRegistry()
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="developer"),
    ))
    bot = mk_bot(reg, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", GROUP), 803))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, reg, port=8769)


def _post(srv, run_async, path, user_id, body=None, *, shutdown=True):
    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            if shutdown:
                await srv.bot.updates.shutdown()
            kw = {"headers": _hdr(user_id)}
            if body is not None:
                kw["json"] = body
            resp = await client.post(path, **kw)
            try:
                payload = await resp.json()
            except Exception:
                payload = None
            await asyncio.sleep(0.2)
            return resp.status, payload
        finally:
            await client.close()
    return run_async(go())


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_miniapp_start_after_shutdown_is_503(team_server, run_async, action):
    status, _ = _post(team_server, run_async, f"/api/update/{action}", ADMIN)
    assert status == 503


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_miniapp_start_after_shutdown_body_is_shutting_down(team_server, run_async, action):
    _, body = _post(team_server, run_async, f"/api/update/{action}", ADMIN)
    assert body == {"error": "shutting_down"}


@pytest.mark.parametrize("action", ["claude", "aipager", "both"])
def test_miniapp_start_after_shutdown_runs_nothing(team_server, run_async, world, action):
    _post(team_server, run_async, f"/api/update/{action}", ADMIN)
    assert world.calls == []


def test_miniapp_non_admin_after_shutdown_is_still_403(team_server, run_async):
    """Auth comes first: a shutdown does not tell a non-admin anything new."""
    status, _ = _post(team_server, run_async, "/api/update/claude", MEMBER)
    assert status == 403


def test_miniapp_refused_source_without_shutdown_stays_409(team_server, run_async, world):
    world.origin = "editable"
    status, body = _post(team_server, run_async, "/api/update/aipager", ADMIN, shutdown=False)
    assert (status, body) == (409, {"error": "not_upgradable"})


def test_miniapp_control_without_job_after_shutdown_is_not_a_success(team_server, run_async):
    status, _ = _post(team_server, run_async, "/api/update/cancel", ADMIN, {"job_id": 1})
    assert status in (409, 503)


# ============================================================================
# No new spawn once the shutdown has begun (the public seam)
# ============================================================================

def test_run_command_after_shutdown_spawns_nothing(world, personal_bot, run_async):
    run_async(personal_bot.updates.shutdown())
    self_update.run_command(["/abs/tools/pipx", "upgrade", "aipager"], timeout=5)
    assert world.calls == []


def test_run_command_after_shutdown_reports_an_error(world, personal_bot, run_async):
    run_async(personal_bot.updates.shutdown())
    res = self_update.run_command(["/abs/tools/pipx", "upgrade", "aipager"], timeout=5)
    assert res.error


def test_run_command_after_shutdown_reports_no_success(world, personal_bot, run_async):
    run_async(personal_bot.updates.shutdown())
    res = self_update.run_command(["/abs/tools/pipx", "upgrade", "aipager"], timeout=5)
    assert res.returncode != 0


def test_run_command_after_shutdown_does_not_raise(world, personal_bot, run_async):
    run_async(personal_bot.updates.shutdown())
    self_update.run_command(["/abs/tools/systemd-run", "--user"], timeout=5)


def test_run_command_before_shutdown_still_spawns(world):
    """Equivalence partner (and a check that the conftest reset the
    shutdown state between tests)."""
    self_update.run_command(["/abs/tools/pipx", "upgrade", "aipager"], timeout=5)
    assert len(world.calls) == 1


# ============================================================================
# rev-iter2-003: the shutdown is bounded as a whole
# ============================================================================

def test_shutdown_deadline_constant_is_eight_seconds():
    assert update_flow.SHUTDOWN_DEADLINE_SECONDS == 8.0


def test_shutdown_deadline_fits_inside_the_units_stop_timeout():
    m = re.search(r"TimeoutStopSec=(\d+)", service.LINUX_UNIT_TEMPLATE)
    limit = float(m.group(1)) if m else 15.0
    assert update_flow.SHUTDOWN_DEADLINE_SECONDS < limit


def test_shutdown_grace_fits_inside_the_deadline():
    assert update_flow.SHUTDOWN_GRACE_SECONDS < update_flow.SHUTDOWN_DEADLINE_SECONDS


class _HangingKill:
    """A kill that does not come back on its own (e.g. a child ignoring
    SIGTERM with a long per-child grace)."""

    def __init__(self, inst):
        self.inst = inst
        self.gate = threading.Event()

    def __call__(self):
        self.inst.kill_calls += 1
        self.gate.wait(4.0)
        self.inst.killed = True
        self.inst.release.set()
        return 1

    def close(self):
        self.gate.set()


async def _timed_shutdown(bot, h, inst):
    await _start(bot, h)
    await h.wait_for(inst.started.is_set, 5.0)
    t0 = time.monotonic()
    await bot.updates.shutdown()
    return time.monotonic() - t0


def test_shutdown_is_bounded_when_the_kill_hangs(world, personal_bot, h, run_async, installer, monkeypatch):
    monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.6)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.2)
    hang = _HangingKill(installer)
    monkeypatch.setattr(self_update, "terminate_running_commands", hang)
    try:
        elapsed = run_async(_timed_shutdown(personal_bot, h, installer))
    finally:
        hang.close()
        installer.close()
    assert elapsed < 0.6 + 1.0


def test_shutdown_is_bounded_when_the_grace_exceeds_the_deadline(world, personal_bot, h, run_async, installer, monkeypatch):
    """The grace is one of the waits sharing the overall deadline."""
    monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 4.0)
    elapsed = run_async(_timed_shutdown(personal_bot, h, installer))
    assert elapsed < 0.5 + 1.0


def test_shutdown_past_its_deadline_still_does_not_raise(world, personal_bot, h, run_async, installer, monkeypatch):
    monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.1)
    hang = _HangingKill(installer)
    monkeypatch.setattr(self_update, "terminate_running_commands", hang)
    try:
        run_async(_timed_shutdown(personal_bot, h, installer))
    finally:
        hang.close()
        installer.close()


def test_shutdown_past_its_deadline_schedules_no_restart(world, personal_bot, h, run_async, installer, monkeypatch):
    """The installer finishes only after the shutdown gave up waiting."""
    monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(self_update, "terminate_running_commands", lambda: 0)

    async def go():
        await _timed_shutdown(personal_bot, h, installer)
        installer.release.set()          # "finishes" late, after the shutdown
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert world.schedule_calls() == []


def test_shutdown_with_default_deadline_and_killable_installer_is_quick(world, personal_bot, h, run_async, installer, monkeypatch):
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.2)
    elapsed = run_async(_timed_shutdown(personal_bot, h, installer))
    assert elapsed < update_flow.SHUTDOWN_DEADLINE_SECONDS


# ============================================================================
# Bot tokens never leak through a tail
# ============================================================================

def _fragments(secret, n=8):
    return {secret[i:i + n] for i in range(len(secret) - n + 1)}


def _leaks(text):
    return sorted(f for f in _fragments(TOKEN_SECRET) if f in text)


def _cut_tail(cut_at):
    """Output whose last OUTPUT_TAIL_CHARS begin `cut_at` chars into TOKEN."""
    n = self_update.OUTPUT_TAIL_CHARS
    after = n - (len(TOKEN) - cut_at)
    assert after >= 1
    # A newline ends the token: letters glued after it would be part of a
    # token-shaped word, and redacting that whole word is fair.
    return "p" * 6000 + TOKEN + "\n" + "q" * (after - 1)


CUTS = [1, 4, 8, len(TOKEN_ID) - 1, len(TOKEN_ID), len(TOKEN_ID) + 1,
        len(TOKEN_ID) + 5, len(TOKEN_ID) + 15, len(TOKEN) - 12]


def _claude_tail(world, monkeypatch, tail, rc=1):
    real = world._run_command

    def fake(argv, *, timeout, env=None, capture=True):
        if [str(a) for a in argv][1:] == ["update"]:
            world.calls.append({"argv": [str(a) for a in argv], "timeout": timeout, "env": env})
            return self_update.CommandResult(returncode=rc, output_tail=tail,
                                             timed_out=False, error=None)
        return real(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", fake)


@pytest.mark.parametrize("cut_at", CUTS)
def test_claude_tail_cut_inside_the_token_leaks_no_secret(world, personal_bot, h, run_async, monkeypatch, cut_at):
    _claude_tail(world, monkeypatch, _cut_tail(cut_at))
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


@pytest.mark.parametrize("cut_at", CUTS)
def test_aipager_tail_cut_inside_the_token_leaks_no_secret(world, personal_bot, h, run_async, cut_at):
    world.upgrade_rc = 1
    world.upgrade_output = _cut_tail(cut_at)
    _, msg, _ = run_async(h.run_job(personal_bot, "aipager"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


@pytest.mark.parametrize("cut_at", CUTS)
def test_cli_tail_cut_inside_the_token_leaks_no_secret(world, capsys, cut_at):
    world.upgrade_rc = 1
    world.upgrade_output = _cut_tail(cut_at)
    updater.cmd_update()
    out = capsys.readouterr()
    assert _leaks(out.out + out.err) == []


def test_timed_out_upgrade_tail_cut_inside_the_token_leaks_no_secret(world, personal_bot, h, run_async, monkeypatch):
    real = world._run_command

    def fake(argv, *, timeout, env=None, capture=True):
        res = real(argv, timeout=timeout, env=env, capture=capture)
        if world.is_upgrade([str(a) for a in argv]):
            return self_update.CommandResult(returncode=None, timed_out=True, error=None,
                                             output_tail=_cut_tail(len(TOKEN_ID) + 1))
        return res
    monkeypatch.setattr(self_update, "_run_command", fake)
    _, msg, _ = run_async(h.run_job(personal_bot, "aipager"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


GLUED = ["x", "abc", "Token", "_", "A1"]


@pytest.mark.parametrize("prefix", GLUED)
def test_claude_tail_token_glued_to_a_word_leaks_no_secret(world, personal_bot, h, run_async, monkeypatch, prefix):
    _claude_tail(world, monkeypatch, f"auth failed for {prefix}{TOKEN} retry")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


@pytest.mark.parametrize("prefix", GLUED)
def test_aipager_tail_token_glued_to_a_word_leaks_no_secret(world, personal_bot, h, run_async, prefix):
    world.upgrade_rc = 1
    world.upgrade_output = f"error: bad credential {prefix}{TOKEN}"
    _, msg, _ = run_async(h.run_job(personal_bot, "aipager"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


@pytest.mark.parametrize("prefix", GLUED)
def test_cli_tail_token_glued_to_a_word_leaks_no_secret(world, capsys, prefix):
    world.upgrade_rc = 1
    world.upgrade_output = f"error: bad credential {prefix}{TOKEN}"
    updater.cmd_update()
    out = capsys.readouterr()
    assert _leaks(out.out + out.err) == []


def test_token_glued_and_cut_at_once_leaks_no_secret(world, personal_bot, h, run_async, monkeypatch):
    n = self_update.OUTPUT_TAIL_CHARS
    glued = "word" + TOKEN
    tail = "p" * 6000 + glued + "q" * (n - len(TOKEN) + 3)
    _claude_tail(world, monkeypatch, tail)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


def test_token_at_the_very_end_of_the_tail_leaks_no_secret(world, personal_bot, h, run_async, monkeypatch):
    """Boundary: nothing after the token."""
    _claude_tail(world, monkeypatch, "p" * 6000 + TOKEN)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


def test_two_tokens_one_cut_one_whole_leak_no_secret(world, personal_bot, h, run_async, monkeypatch):
    n = self_update.OUTPUT_TAIL_CHARS
    first = TOKEN[len(TOKEN_ID) + 2:]            # the window starts here
    filler = n - len(first) - len(" again ") - len(TOKEN) - 40
    tail = "p" * 6000 + TOKEN[:len(TOKEN_ID) + 2] + first + "q" * filler \
        + " again " + TOKEN + "q" * 40
    _claude_tail(world, monkeypatch, tail)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _leaks(h.all_text(personal_bot, msg)) == []


def test_tail_without_a_token_keeps_its_text(world, personal_bot, h, run_async, monkeypatch):
    """Equivalence partner: redaction does not eat ordinary output that
    merely looks like `digits:word`."""
    _claude_tail(world, monkeypatch, "error at 12:30 in step 3: exit 1 KEEP-ME")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert "KEEP-ME" in h.all_text(personal_bot, msg)


def test_tail_cut_inside_the_token_still_ends_with_the_output_end(world, personal_bot, h, run_async, monkeypatch):
    """Redacting first must not lose the tail's final line."""
    _claude_tail(world, monkeypatch, _cut_tail(len(TOKEN_ID) + 5)[:-10] + "FINAL-LINE")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert "FINAL-LINE" in h.all_text(personal_bot, msg)
