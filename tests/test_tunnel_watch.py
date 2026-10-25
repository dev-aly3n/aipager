"""cloudflared is watched while its link is probed and published
(spec requirements 1 and 2, the incident's items 2 and 3).

The process table, metrics answers and probes come from
tests/tunnel_fakes.py; the exit-status tests run a real, harmless Python
child (never cloudflared), as tests/test_tunnel_keep.py does for the reap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import cloudflared_fetch
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm
from aipager.miniapp import tunnel_state as ts
from tests import tunnel_fakes

HOST1 = "first-words-here.trycloudflare.com"
HOST2 = "second-words-here.trycloudflare.com"
URL1, URL2 = f"https://{HOST1}", f"https://{HOST2}"

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")


@pytest.fixture
def table(monkeypatch):
    tunnel_mod.set_managed_tunnel_url("")
    t = tunnel_fakes.install(monkeypatch)
    t.hosts[40001] = json.dumps({"hostname": HOST1})
    t.hosts[40002] = json.dumps({"hostname": HOST2})
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared",
                        AsyncMock(return_value="/fake/cloudflared"))
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel_mod, "detect_public_url", lambda: None)
    yield t
    tunnel_mod.set_managed_tunnel_url("")


async def _until(pred, limit=2.0):
    end = time.monotonic() + limit
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


def _tunnel_spawns(table):
    return [a for a, _k in table.spawns if a[0] != "sh"]


def _die(table, pid, code=1):
    table.exit_codes[pid] = code
    table.alive.discard(pid)


# ---- the probe is watched ---------------------------------------------------------------

def test_exit_during_probe_publishes_nothing_and_restarts_within_one_interval(
        table, run_async, monkeypatch, caplog):
    probes = []

    async def _first_never_answers(url):
        probes.append(url)
        if url == URL1:
            await asyncio.Event().wait()
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _first_never_answers)
    published = []

    async def on_change(url):
        published.append(url)

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: probes == [URL1])
        _die(table, 4000, code=1)
        died = time.monotonic()
        await _until(lambda: len(_tunnel_spawns(table)) == 2)
        restarted = time.monotonic() - died
        await _until(lambda: published[-1:] == [URL2])
        await m.stop()
        return restarted

    with caplog.at_level(logging.WARNING):
        restarted = run_async(scenario())
    assert URL1 not in published, "a button was published for a dead tunnel"
    assert published == ["", URL2]
    assert restarted < 1.0, f"restart took {restarted:.2f}s with a 0.01s check interval"
    assert any("exited (code=1, stage probe)" in r.getMessage() for r in caplog.records)


def test_resolve_public_url_hides_the_managed_url_until_its_probe_passes(
        table, run_async, monkeypatch):
    # The next tunnel never comes up, so the empty slot after the exit
    # cannot be replaced by a new URL before this test looks at it.
    table.hosts[40002] = '{"hostname":""}'
    gate = {}

    async def _gated(url):
        await gate["e"].wait()
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _gated)

    async def scenario():
        gate["e"] = asyncio.Event()
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: len(_tunnel_spawns(table)) == 1)
        for _ in range(20):
            await asyncio.sleep(0.005)
        during = await tunnel_mod.resolve_public_url()
        gate["e"].set()
        await _until(lambda: tunnel_mod.get_managed_tunnel_url() == URL1)
        after = await tunnel_mod.resolve_public_url()
        _die(table, 4000)
        await _until(lambda: tunnel_mod.get_managed_tunnel_url() == "")
        gone = await tunnel_mod.resolve_public_url()
        await m.stop()
        return during, after, gone

    during, after, gone = run_async(scenario())
    assert during == "", "/app would hand out a link nobody verified"
    assert after == URL1
    assert gone == ""


def test_a_live_tunnel_whose_link_never_answers_is_restarted(table, run_async, monkeypatch):
    table.dead_urls.add(URL1)
    log_path, _err = tm._log_paths()
    statuses = []
    real_write = ts.write_status_file

    def _record(status):
        statuses.append(status)
        return real_write(status)
    monkeypatch.setattr(ts, "write_status_file", _record)

    async def _probe(url):
        table.probes.append(url)
        if url == URL1:
            # What a blocked UDP 7844 leaves in cloudflared's log.
            log_path.write_text(json.dumps({
                "level": "error", "time": "2026-10-25T08:00:00Z",
                "error": "failed to dial to edge with quic: timeout: no recent network "
                         "activity", "message": "Failed to dial a quic connection"}) + "\n")
            return False
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _probe)
    published = []

    async def on_change(url):
        published.append(url)

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: published[-1:] == [URL2])
        await m.stop()

    run_async(scenario())
    assert URL1 not in published
    assert (4000, signal.SIGTERM) in table.signals and 4000 not in table.alive
    assert len(_tunnel_spawns(table)) == 2
    failed = [s for s in statuses if s.phase == ts.RETRYING and s.next_try_at]
    assert failed and failed[0].reason == ts.EDGE_UNREACHABLE


# ---- the publish is watched ---------------------------------------------------------------

def test_exit_during_publish_clears_the_slot_at_once(table, run_async):
    table.hosts[40002] = '{"hostname":""}'        # the next tunnel never comes up
    published = []
    state = {}

    async def on_change(url):
        published.append(url)
        if url:
            state["started"].set()
            await state["release"].wait()

    async def scenario():
        state["started"], state["release"] = asyncio.Event(), asyncio.Event()
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: state["started"].is_set())
        assert tunnel_mod.get_managed_tunnel_url() == URL1
        _die(table, 4000)
        await _until(lambda: tunnel_mod.get_managed_tunnel_url() == "", limit=1.0)
        in_flight = not m._publish_task.done()
        cleared_early = m.current_url == ""
        state["release"].set()
        await _until(lambda: published[-1:] == [""])
        await m.stop()
        return in_flight, cleared_early

    in_flight, cleared_early = run_async(scenario())
    assert in_flight, "the slot was only cleared after the publish ended"
    assert cleared_early
    assert published == [URL1, ""]


def test_exit_during_publish_ends_with_no_button(table, run_async, mk_bot, monkeypatch):
    """The in-flight publish is never cut half way (both chats get the
    button it was sending), then the clear goes out behind it, so every
    chat ends with the commands menu and the bot holds no URL."""
    from telegram import MenuButtonCommands, MenuButtonWebApp

    table.hosts[40002] = '{"hostname":""}'        # the next tunnel never comes up
    bot = mk_bot()
    monkeypatch.setattr(bot, "_miniapp_button_chats", lambda: [111, 222])
    monkeypatch.setattr(bot, "pinned_app_changed", AsyncMock(), raising=False)
    calls = []

    async def _set_menu(*, chat_id, menu_button):
        calls.append((chat_id, type(menu_button)))
        if len(calls) == 1:
            _die(table, 4000)             # dies while its button goes out
            await asyncio.sleep(0.1)      # ten check intervals
    bot._app.bot.set_chat_menu_button = _set_menu

    async def scenario():
        m = tm.TunnelManager(8765, bot.publish_managed_miniapp_button)
        await m.start()
        await _until(lambda: len(calls) >= 4)
        for _ in range(20):
            await asyncio.sleep(0.005)
        await m.stop()

    run_async(scenario())
    assert calls == [(111, MenuButtonWebApp), (222, MenuButtonWebApp),
                     (111, MenuButtonCommands), (222, MenuButtonCommands)]
    assert bot._miniapp_url == ""


# ---- exit status --------------------------------------------------------------------------

def _wait_exited(pid, limit=5.0):
    """Until *pid* (our child) is a zombie or gone, without reaping it."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return
        except OSError:
            return
        time.sleep(0.01)
    raise AssertionError("the child never exited")


@linux_only
def test_a_spawned_tunnels_real_exit_code_is_kept(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(tm, "_is_our_tunnel", lambda pid, binary, port: True)
    popen = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    tunnel = tm.Tunnel(popen.pid, 0, sys.executable, 0, popen=popen)
    _wait_exited(popen.pid)

    code = run_async(asyncio.wait_for(tunnel.wait(), timeout=5))

    assert code == 3 and tunnel.status_known
    assert tunnel.poll() == 3


@linux_only
def test_adopted_child_status_is_read_and_unknown_only_when_not_ours(monkeypatch):
    # A kept tunnel that is still this image's child (the in-place exec):
    # no Popen, yet its real status is read from the reap.
    popen = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(5)"])
    _wait_exited(popen.pid)
    adopted = tm.Tunnel(popen.pid, 0, sys.executable, 0)
    assert adopted.poll() == 5 and adopted.status_known
    # One that is not our child (a service restart) and gone: -1, unknown.
    monkeypatch.setattr(tm, "_pid_alive", lambda pid: False)
    stranger = tm.Tunnel(987654, 0, "/fake/cloudflared", 8765)
    assert stranger.poll() == -1 and stranger.status_known is False


def test_an_adopted_tunnel_whose_status_cannot_be_read_logs_status_unknown(
        table, run_async, caplog):
    host = "kept-words-here.trycloudflare.com"
    table.add_tunnel(5000, metrics=40009, host=host)
    tm._write_record({"pid": 5000, "metrics_port": 40009, "port": 8765,
                      "binary": "/fake/cloudflared", "boot_id": "boot-1",
                      "host_sha256": tm._host_digest(host), "owner": 0,
                      "handover_token": "tok"})
    published = []

    async def on_change(url):
        published.append(url)

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: published == [f"https://{host}"])
        table.alive.discard(5000)
        await _until(lambda: "" in published)
        await m.stop()

    with caplog.at_level(logging.INFO):
        run_async(scenario())
    messages = [r.getMessage() for r in caplog.records]
    assert any("status unknown" in m for m in messages)
    assert any("exited (code=unknown, stage run)" in m for m in messages)
    assert not any("code=-1" in m for m in messages)


@linux_only
def test_a_dead_child_is_reaped_within_one_check_interval(monkeypatch, run_async):
    """The incident's zombie: a cloudflared that exits while its link is
    probed is reaped within one check interval, not after the probe."""
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.02)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared",
                        AsyncMock(return_value=sys.executable))
    monkeypatch.setattr(tm, "_is_our_tunnel", lambda pid, binary, port: True)
    held = {}

    async def _spawn(binary, port):
        if "popen" in held:
            await asyncio.Event().wait()     # the next attempt never comes up
        popen = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        held["popen"] = popen
        return tm.Tunnel(popen.pid, 0, binary, port, popen=popen), URL1
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def _never_answers(url):
        await asyncio.Event().wait()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _never_answers)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: "popen" in held)
        pid = held["popen"].pid
        await asyncio.get_running_loop().run_in_executor(None, _wait_exited, pid)
        exited = time.monotonic()
        await _until(lambda: held["popen"].returncode is not None, limit=2.0)
        reaped_after = time.monotonic() - exited
        await m.stop()
        return pid, reaped_after

    pid, reaped_after = run_async(scenario())
    assert not os.path.exists(f"/proc/{pid}"), "left a zombie"
    assert reaped_after < 0.5


# ---- cloudflared's stderr file ---------------------------------------------------------------

def test_stderr_file_is_owner_only_and_emptied_per_tunnel(table, run_async, monkeypatch):
    seen = []
    real = __import__("aipager._test_guard", fromlist=["check_write"]).check_write

    def _check(path):
        seen.append(path)
        real(path)
    monkeypatch.setattr("aipager._test_guard.check_write", _check)
    _log, err = tm._log_paths()
    err.parent.mkdir(parents=True, exist_ok=True)
    err.write_text("an old tunnel's last words\n")
    os.chmod(err, 0o644)

    tunnel, _url = run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))

    assert err in seen
    st = os.stat(err)
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
    assert err.read_bytes() == b""
    [(_argv, kwargs)] = table.spawns
    assert isinstance(kwargs["stderr"], int) and kwargs["stderr"] >= 0


# ---- stop() ---------------------------------------------------------------------------------

def test_stop_removes_the_status_file(table, run_async, monkeypatch):
    from aipager import config

    monkeypatch.setattr(tm, "_is_aipager", lambda pid: True)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: m.current_url == URL1)
        on_disk = ts.read_status_file()
        await m.stop()
        return on_disk

    on_disk = run_async(scenario())
    assert on_disk is not None and on_disk.phase == ts.WORKING
    assert not config.TUNNEL_STATUS_FILE.exists()
    assert ts.current_status() is None
    assert ts.request_attempt() is False          # the hook is detached


def test_stop_during_publish_lets_it_finish_and_is_bounded(table, run_async, monkeypatch):
    monkeypatch.setattr(tm, "_PUBLISH_SETTLE_SECONDS", 0.3)
    events = []
    mode = {"hang": False}

    async def on_change(url):
        if not url:
            return
        events.append("started")
        try:
            if mode["hang"]:
                await asyncio.Event().wait()
            await asyncio.sleep(0.1)
            events.append("finished")
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: events == ["started"])
        await asyncio.wait_for(m.stop(), timeout=2.0)
        finished_before_stop_returned = list(events)
        mode["hang"] = True
        events.clear()
        table.hosts[40002] = json.dumps({"hostname": HOST2})
        m2 = tm.TunnelManager(8765, on_change)
        await m2.start()
        await _until(lambda: events == ["started"])
        began = time.monotonic()
        await asyncio.wait_for(m2.stop(), timeout=2.0)
        return finished_before_stop_returned, list(events), time.monotonic() - began

    first, second, took = run_async(scenario())
    assert first == ["started", "finished"], "stop() cut the publish half way"
    assert second == ["started", "cancelled"]
    assert took < 1.5


# ---- nothing outlives its attempt -----------------------------------------------------------

def test_the_probe_stops_when_cloudflared_exits_first(table, run_async, monkeypatch):
    """Spec requirement 1, "the probe stops": a dead tunnel's probe is
    cancelled at once, not left running for its whole window (logging the
    dead URL and overlapping the next attempt's probe)."""
    state = {"started": False, "cancelled_at": None}

    async def _probe(url):
        if url == URL1:
            state["started"] = True
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["cancelled_at"] = time.monotonic()
                raise
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _probe)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: state["started"])
        _die(table, 4000)
        died = time.monotonic()
        await _until(lambda: m.current_url == URL2)
        cancelled_at = state["cancelled_at"]
        await m.stop()
        return died, cancelled_at

    died, cancelled_at = run_async(scenario())
    assert cancelled_at is not None, "the dead tunnel's probe kept running"
    assert cancelled_at - died < 1.0


def _pending_tasks():
    me = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


@pytest.mark.parametrize("stage", ["probe", "hold"])
def test_a_planned_stop_leaves_no_task_of_the_manager_running(table, run_async,
                                                              monkeypatch, stage):
    """stop(keep=True) during the probe or while the tunnel is held: the
    tunnel is kept for the next daemon, and no task the manager made (the
    process watch, the probe) keeps running in this one."""
    monkeypatch.setattr("aipager.config.MINIAPP_KEEP_TUNNEL", True)
    gate = {}

    async def _probe(url):
        if stage == "probe":
            await gate["never"].wait()
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _probe)

    async def scenario():
        gate["never"] = asyncio.Event()
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        if stage == "probe":
            await _until(lambda: len(_tunnel_spawns(table)) == 1)
            for _ in range(10):
                await asyncio.sleep(0.005)
        else:
            await _until(lambda: m.current_url == URL1)
            for _ in range(10):
                await asyncio.sleep(0.005)
        await m.stop(keep=True)
        for _ in range(3):
            await asyncio.sleep(0)
        return _pending_tasks()

    pending = run_async(scenario())
    assert 4000 in table.alive and table.guards(), "the tunnel was not kept"
    assert pending == [], f"tasks left running after stop(): {pending}"


# ---- a discovery timeout leaves no zombie ---------------------------------------------------

@linux_only
def test_a_discovery_timeout_reaps_the_killed_child(monkeypatch, run_async):
    """No URL in time: cloudflared is killed AND reaped by this attempt,
    not by whichever spawn comes next (in the slow phase, minutes later)."""
    from aipager import self_restart

    monkeypatch.setattr("aipager.config.TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 3.0)
    children = []

    def _popen(argv, **kwargs):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        children.append(child)
        return child

    def _kill(pid, sig):
        assert [c.pid for c in children] == [pid], "a signal for a process not ours"
        os.kill(pid, sig)

    async def _no_answer(port, path):
        return None
    monkeypatch.setattr(self_restart, "_popen", _popen)
    monkeypatch.setattr(self_restart, "_kill", _kill)
    monkeypatch.setattr(tm, "_is_our_tunnel", lambda pid, binary, port: True)
    monkeypatch.setattr(tm, "_free_port", lambda: 40001)
    monkeypatch.setattr(tm, "_query_metrics", _no_answer)
    try:
        with pytest.raises(tm.TunnelLaunchError):
            run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))
        [child] = children
        # Read before anything else could reap it: the attempt did.
        returncode = child.returncode
        zombie = os.path.exists(f"/proc/{child.pid}")
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert returncode == -signal.SIGKILL, "the killed cloudflared was never reaped"
    assert not zombie


# ---- the state says "not working" as soon as cloudflared is gone ----------------------------

def test_the_state_is_not_working_while_a_dead_tunnels_publish_finishes(table, run_async):
    """It dies while its button goes out: /app and doctor must stop saying
    "working" at once, not when the publish and the clear are done."""
    table.hosts[40002] = '{"hostname":""}'        # the next tunnel never comes up
    state = {}

    async def on_change(url):
        if url:
            state["started"].set()
            await state["release"].wait()

    async def scenario():
        state["started"], state["release"] = asyncio.Event(), asyncio.Event()
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: state["started"].is_set())
        working = ts.current_status().phase
        _die(table, 4000, code=1)
        await _until(lambda: ts.current_status().phase != ts.WORKING, limit=1.0)
        during = ts.current_status()
        still_publishing = not m._publish_task.done()
        state["release"].set()
        await m.stop()
        return working, during, still_publishing

    working, during, still_publishing = run_async(scenario())
    assert working == ts.WORKING
    assert still_publishing
    assert during.phase == ts.RETRYING and during.reason == ts.OTHER
    assert during.attempt == 1


def test_the_state_is_not_working_while_the_clear_goes_out(table, run_async):
    table.hosts[40002] = '{"hostname":""}'        # the next tunnel never comes up
    state = {}

    async def on_change(url):
        if not url and "release" in state:
            state["clearing"].set()
            await state["release"].wait()

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: m.current_url == URL1 and m._publish_task.done())
        state["clearing"], state["release"] = asyncio.Event(), asyncio.Event()
        _die(table, 4000, code=1)
        await _until(lambda: state["clearing"].is_set())
        during = ts.current_status()
        state["release"].set()
        await m.stop()
        return during

    during = run_async(scenario())
    assert during.phase == ts.RETRYING, "doctor and /app said working during the clear"
    assert during.reason == ts.OTHER



def test_the_clear_goes_out_only_after_the_dead_tunnels_publish(table, run_async):
    """The manager's own order, whatever the callback does: the clear
    starts once the publish in flight has ended, never beside it (a
    callback with no lock of its own would otherwise end on the dead URL)."""
    table.hosts[40002] = '{"hostname":""}'        # the next tunnel never comes up
    events = []
    state = {}

    async def on_change(url):
        events.append(("start", url))
        if url:
            state["started"].set()
            await state["release"].wait()
        events.append(("end", url))

    async def scenario():
        state["started"], state["release"] = asyncio.Event(), asyncio.Event()
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: state["started"].is_set())
        _die(table, 4000)
        await _until(lambda: ts.current_status().phase != ts.WORKING, limit=1.0)
        for _ in range(20):
            await asyncio.sleep(0.005)
        state["release"].set()
        await _until(lambda: ("end", "") in events)
        await m.stop()

    run_async(scenario())
    assert events == [("start", URL1), ("end", URL1), ("start", ""), ("end", "")]

# ---- the failure line says cloudflared's words once -----------------------------------------

def test_the_failure_line_never_repeats_cloudflareds_words(table, run_async, monkeypatch,
                                                          caplog):
    raw = "expected at least 2 Cloudflare Regions regions, but SRV only returned 1"
    log_path, err_path = tm._log_paths()
    calls = []

    async def _spawn(binary, port):
        calls.append(1)
        if len(calls) > 1:
            await asyncio.Event().wait()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({"level": "error", "error": raw,
                                        "message": "Initiating shutdown"}) + "\n")
        err_path.write_text(raw + "\n")
        # aipager's own message already holds cloudflared's words.
        raise tm.TunnelLaunchError(raw, exit_code=1)
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: len(calls) == 2)
        await m.stop()

    with caplog.at_level(logging.WARNING, logger="aipager.miniapp.tunnel_manager"):
        run_async(scenario())
    [line] = [r.getMessage() for r in caplog.records if "reason dns_incomplete" in r.getMessage()]
    assert line.count(raw) == 1, line


def test_the_failure_line_keeps_both_when_they_differ(table, run_async, monkeypatch, caplog):
    raw = "expected at least 2 Cloudflare Regions regions, but SRV only returned 1"
    log_path, _err = tm._log_paths()
    calls = []

    async def _spawn(binary, port):
        calls.append(1)
        if len(calls) > 1:
            await asyncio.Event().wait()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({"level": "error", "error": raw,
                                        "message": "Initiating shutdown"}) + "\n")
        raise tm.TunnelLaunchError("cloudflared exited before reporting a tunnel URL",
                                   exit_code=1)
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: len(calls) == 2)
        await m.stop()

    with caplog.at_level(logging.WARNING, logger="aipager.miniapp.tunnel_manager"):
        run_async(scenario())
    [line] = [r.getMessage() for r in caplog.records if "reason dns_incomplete" in r.getMessage()]
    assert f"[cloudflared exited before reporting a tunnel URL; {raw}]" in line
