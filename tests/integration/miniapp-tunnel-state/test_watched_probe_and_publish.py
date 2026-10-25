"""cloudflared is watched while its URL is probed and published (spec
requirement 1; design.md success criteria 1, 2, 3 and 5).

- exits mid-probe: no button for its URL, the managed slot stays empty,
  the probe stops, its code is logged, the next attempt follows at once;
- exits mid-publish: the slot empties at once, the in-flight publish
  completes, then "" is published, so every chat ends on the commands
  button and ``bot._miniapp_url`` is "";
- runs but its link never answers: it is killed, the reason is
  ``unreachable`` (``edge_unreachable`` with a dial error in its log), and
  the next attempt follows;
- a real child's exit status (3) is logged and the child is reaped within
  one ``TUNNEL_CHECK_SECONDS``; an adopted pid that is not ours reads -1
  with ``status_known`` False.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from telegram import MenuButtonCommands

from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_state

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    EDGE_DIAL_TEXT, FakeProcess, log_line, wait_until, write_cf_files,
)

URL = "https://watched-probe-words.trycloudflare.com"
TM_LOGGER = "aipager.miniapp.tunnel_manager"


class BlockingProbe:
    """A probe that never answers until released; records whether it
    was cancelled (the probe "stops")."""

    def __init__(self, answer: bool = True):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.answer = answer
        self.calls: list[str] = []

    async def __call__(self, url, *a, **k):
        self.calls.append(url)
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.answer


def _messages(caplog, level=None):
    return [r.getMessage() for r in caplog.records
            if r.name.startswith("aipager") and (level is None or r.levelno == level)]


# ---- exit during the probe ---------------------------------------------------

@pytest.fixture
def exit_mid_probe(monkeypatch, seam, make_manager, run_async, caplog):
    """Run the scenario once and hand each test its own observation."""
    proc = FakeProcess()
    seam.steps = [(proc, URL)]
    probe = BlockingProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    obs = {}

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await asyncio.wait_for(probe.entered.wait(), 3.0)
            obs["slot_during_probe"] = tunnel_mod.get_managed_tunnel_url()
            obs["resolve_during_probe"] = await tunnel_mod.resolve_public_url()
            loop = asyncio.get_running_loop()
            died_at = loop.time()
            proc.die(1)
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
            obs["restart_delay"] = loop.time() - died_at
            await asyncio.sleep(0.05)
            obs["slot_after"] = tunnel_mod.get_managed_tunnel_url()
            obs["resolve_after"] = await tunnel_mod.resolve_public_url()
            obs["current_url"] = mgr.current_url
            obs["status"] = tunnel_state.current_status()
        finally:
            await mgr.stop()
        obs["published"] = list(mgr.published)

    run_async(scenario())
    obs["probe"] = probe
    obs["logs"] = _messages(caplog)
    return obs


def test_exit_during_probe_never_publishes_its_url(exit_mid_probe):
    assert URL not in exit_mid_probe["published"]


def test_exit_during_probe_keeps_the_managed_slot_empty_while_probing(exit_mid_probe):
    assert exit_mid_probe["slot_during_probe"] == ""


def test_exit_during_probe_keeps_the_managed_slot_empty_after(exit_mid_probe):
    assert exit_mid_probe["slot_after"] == ""


def test_exit_during_probe_current_url_is_empty(exit_mid_probe):
    assert exit_mid_probe["current_url"] == ""


def test_exit_during_probe_stops_the_probe(exit_mid_probe):
    assert exit_mid_probe["probe"].cancelled


def test_exit_during_probe_restarts_within_one_check_interval(exit_mid_probe):
    """TUNNEL_CHECK_SECONDS is 0.2 here and the backoff 0.01-0.02 s; the
    next attempt must not wait for the probe's own window."""
    assert exit_mid_probe["restart_delay"] < 0.2 + 0.5


def test_exit_during_probe_logs_the_real_exit_code(exit_mid_probe):
    assert any(re.search(r"cloudflared exited \(code=1\b", m) for m in exit_mid_probe["logs"]), \
        exit_mid_probe["logs"]


def test_exit_during_probe_state_is_retrying(exit_mid_probe):
    assert exit_mid_probe["status"].phase == "retrying"


def test_resolve_public_url_is_empty_while_the_managed_url_is_probed(exit_mid_probe):
    assert exit_mid_probe["resolve_during_probe"] == ""


def test_resolve_public_url_is_empty_after_the_exit(exit_mid_probe):
    assert exit_mid_probe["resolve_after"] == ""


# ---- exit during the publish, through the real bot -----------------------------

@pytest.fixture
def exit_mid_publish(monkeypatch, seam, make_manager, mk_bot, run_async):
    proc = FakeProcess()
    seam.steps = [(proc, URL)]
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    bot = mk_bot()
    in_publish = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def set_menu(*a, **kw):
        calls.append(kw)
        button = kw.get("menu_button")
        if getattr(button, "web_app", None) is not None and not release.is_set():
            in_publish.set()
            await release.wait()

    bot._app.bot.set_chat_menu_button = AsyncMock(side_effect=set_menu)
    obs = {"calls": calls}

    async def scenario():
        mgr = make_manager(on_url_change=bot.publish_managed_miniapp_button)
        await mgr.start()
        try:
            await asyncio.wait_for(in_publish.wait(), 3.0)
            obs["slot_mid_publish"] = tunnel_mod.get_managed_tunnel_url()
            proc.die(1)
            # One check interval (0.2 s here) to notice; the publish is
            # still blocked inside Telegram.
            await asyncio.sleep(0.5)
            obs["slot_after_death"] = tunnel_mod.get_managed_tunnel_url()
            obs["resolve_after_death"] = await tunnel_mod.resolve_public_url()
            release.set()
            await wait_until(lambda: any(
                isinstance(c.get("menu_button"), MenuButtonCommands) for c in calls),
                timeout=3.0)
            await asyncio.sleep(0.3)
            obs["bot_url"] = bot._miniapp_url
        finally:
            await mgr.stop()

    run_async(scenario())
    return obs


def test_the_slot_holds_the_url_once_the_probe_passed(exit_mid_publish):
    assert exit_mid_publish["slot_mid_publish"] == URL


def test_exit_during_publish_clears_the_slot_at_once(exit_mid_publish):
    assert exit_mid_publish["slot_after_death"] == ""


def test_exit_during_publish_resolve_public_url_is_empty(exit_mid_publish):
    assert exit_mid_publish["resolve_after_death"] == ""


def test_exit_during_publish_ends_with_no_button_in_the_bot(exit_mid_publish):
    assert exit_mid_publish["bot_url"] == ""


def test_exit_during_publish_last_menu_button_per_chat_is_commands(exit_mid_publish):
    last = {}
    for c in exit_mid_publish["calls"]:
        last[c.get("chat_id")] = c.get("menu_button")
    assert last and all(isinstance(b, MenuButtonCommands) for b in last.values()), last


# ---- a live tunnel whose link never answers --------------------------------------

@pytest.mark.parametrize("log_lines,expected", [
    ([], "unreachable"),
    ([log_line("Failed to dial a quic connection", error=EDGE_DIAL_TEXT)],
     "edge_unreachable"),
], ids=["no_dial_error", "dial_error"])
def test_a_live_tunnel_whose_link_never_answers_gets_the_reason(
    monkeypatch, seam, make_manager, run_async, log_lines, expected,
):
    proc = FakeProcess()

    def first():
        write_cf_files(log_lines)
        return proc, URL

    seam.steps = [first]

    async def never_answers(url, *a, **k):
        return False

    monkeypatch.setattr(tunnel_mod, "probe_public_url", never_answers)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
            return tunnel_state.current_status()
        finally:
            await mgr.stop()

    status = run_async(scenario())
    assert status is not None and status.reason == expected, status


def test_a_live_tunnel_whose_link_never_answers_is_killed(
    monkeypatch, seam, make_manager, run_async,
):
    proc = FakeProcess()
    seam.steps = [(proc, URL)]

    async def never_answers(url, *a, **k):
        return False

    monkeypatch.setattr(tunnel_mod, "probe_public_url", never_answers)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
            return proc.returncode
        finally:
            await mgr.stop()

    assert run_async(scenario()) is not None, "the unanswering cloudflared was left running"


def test_a_live_tunnel_whose_link_never_answers_gets_no_button(
    monkeypatch, seam, make_manager, run_async,
):
    proc = FakeProcess()
    seam.steps = [(proc, URL)]

    async def never_answers(url, *a, **k):
        return False

    monkeypatch.setattr(tunnel_mod, "probe_public_url", never_answers)
    holder = {}

    async def scenario():
        mgr = make_manager()
        holder["m"] = mgr
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
        finally:
            await mgr.stop()

    run_async(scenario())
    assert URL not in holder["m"].published


def test_a_stubborn_unanswering_tunnel_is_still_ended(
    monkeypatch, seam, make_manager, run_async,
):
    """Error guessing: SIGTERM ignored. TUNNEL_KILL_TIMEOUT_SECONDS is 0.2."""
    proc = FakeProcess(stubborn=True)
    seam.steps = [(proc, URL)]

    async def never_answers(url, *a, **k):
        return False

    monkeypatch.setattr(tunnel_mod, "probe_public_url", never_answers)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
            return proc.kill_calls
        finally:
            await mgr.stop()

    assert run_async(scenario()) >= 1


# ---- real exit status and the reap ----------------------------------------------

@pytest.fixture
def real_child_tunnel(monkeypatch, seam, make_manager, run_async, caplog, tmp_path):
    """A REAL child process (the Python interpreter, never cloudflared)
    wrapped in a ``Tunnel`` the way the manager spawns one, that sleeps
    while its URL is probed and then exits 3."""
    import aipager.miniapp.tunnel_manager as tm
    from aipager import self_restart
    from tests.tunnel_fakes import ProcessTable

    marker = tmp_path / "child-started"
    popen = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time, pathlib; pathlib.Path(sys.argv[1]).touch(); "
         "time.sleep(0.6); sys.exit(3)", str(marker)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    argv = ProcessTable().tunnel_argv("/fake/cloudflared", 8765, 40001)

    def cmdline(pid):
        if pid == popen.pid and popen.returncode is None:
            return list(argv)
        return None

    def kill(pid, sig):
        # Only ever our own child, and only while it is unreaped.
        if pid != popen.pid or popen.returncode is not None:
            raise ProcessLookupError(pid)
        os.kill(pid, sig)

    monkeypatch.setattr(tm, "_cmdline", cmdline)
    monkeypatch.setattr(self_restart, "_kill", kill)
    tunnel = tm.Tunnel(popen.pid, 40001, "/fake/cloudflared", 8765,
                       host="real-child-words.trycloudflare.com", popen=popen)
    seam.steps = [(tunnel, URL)]
    probe = BlockingProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    obs = {"pid": popen.pid}

    def zombie_or_alive() -> bool:
        try:
            stat = Path(f"/proc/{popen.pid}/stat").read_text()
        except OSError:
            return False
        return bool(stat)

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await asyncio.wait_for(probe.entered.wait(), 3.0)
            # Wait for the child's own exit (it sleeps 0.6 s), then give
            # the manager ONE check interval (0.2 s) plus scheduling slack.
            loop = asyncio.get_running_loop()
            start = loop.time()
            while loop.time() - start < 5.0:
                state = None
                try:
                    state = Path(f"/proc/{popen.pid}/stat").read_text().split(") ")[1][0]
                except (OSError, IndexError):
                    break
                if state == "Z":
                    break
                await asyncio.sleep(0.01)
            obs["exited_at"] = loop.time()
            await asyncio.sleep(0.2 + 0.4)
            obs["still_in_proc_table"] = zombie_or_alive()
            obs["tunnel_poll"] = tunnel.poll()
            obs["status_known"] = getattr(tunnel, "status_known", None)
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
        finally:
            await mgr.stop()
            if popen.returncode is None:
                try:
                    popen.kill()
                except OSError:
                    pass
                popen.wait(timeout=5)

    run_async(scenario())
    obs["logs"] = _messages(caplog)
    return obs


def test_a_spawned_tunnels_real_exit_code_is_logged(real_child_tunnel):
    assert any(re.search(r"cloudflared exited \(code=3\b", m)
               for m in real_child_tunnel["logs"]), \
        real_child_tunnel["logs"]


def test_a_spawned_tunnels_real_exit_code_is_kept_on_the_tunnel(real_child_tunnel):
    assert real_child_tunnel["tunnel_poll"] == 3


def test_a_spawned_tunnels_status_is_known(real_child_tunnel):
    assert real_child_tunnel["status_known"] is True


def test_a_dead_child_is_reaped_within_one_check_interval(real_child_tunnel):
    """No zombie beyond one TUNNEL_CHECK_SECONDS while its URL is probed."""
    assert real_child_tunnel["still_in_proc_table"] is False


def test_a_spawned_tunnels_exit_is_never_logged_as_unknown(real_child_tunnel):
    assert not any("status unknown" in m for m in real_child_tunnel["logs"])


# ---- an adopted pid that is not our child ------------------------------------

def test_adopted_pid_not_our_child_reads_minus_one(monkeypatch):
    import aipager.miniapp.tunnel_manager as tm

    alive = {7001}
    monkeypatch.setattr(tm, "_pid_alive", lambda pid: pid in alive)
    tunnel = tm.Tunnel(7001, 40001, "/fake/cloudflared", 8765, host="h.trycloudflare.com")
    alive.clear()
    assert tunnel.poll() == -1


def test_adopted_pid_not_our_child_status_is_unknown(monkeypatch):
    import aipager.miniapp.tunnel_manager as tm

    alive = {7002}
    monkeypatch.setattr(tm, "_pid_alive", lambda pid: pid in alive)
    tunnel = tm.Tunnel(7002, 40001, "/fake/cloudflared", 8765, host="h.trycloudflare.com")
    alive.clear()
    tunnel.poll()
    assert tunnel.status_known is False


def test_adopted_pid_still_running_polls_none(monkeypatch):
    import aipager.miniapp.tunnel_manager as tm

    monkeypatch.setattr(tm, "_pid_alive", lambda pid: True)
    tunnel = tm.Tunnel(7003, 40001, "/fake/cloudflared", 8765, host="h.trycloudflare.com")
    assert tunnel.poll() is None


def test_an_adopted_tunnel_that_dies_is_logged_as_status_unknown(
    monkeypatch, seam, make_manager, run_async, caplog,
):
    """The manager's log line for an adopted-style pid it cannot read:
    "status unknown", never a made-up code."""
    import aipager.miniapp.tunnel_manager as tm
    from tests.tunnel_fakes import ProcessTable

    table = ProcessTable()
    table.add_tunnel(7004)
    monkeypatch.setattr(tm, "_pid_alive", table.pid_alive)
    monkeypatch.setattr(tm, "_cmdline", table.cmdline)
    from aipager import self_restart
    monkeypatch.setattr(self_restart, "_kill", table.kill)
    tunnel = tm.Tunnel(7004, 40001, "/fake/cloudflared", 8765, host="h.trycloudflare.com")
    seam.steps = [(tunnel, URL)]
    probe = BlockingProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await asyncio.wait_for(probe.entered.wait(), 3.0)
            table.alive.discard(7004)
            await wait_until(lambda: seam.calls >= 2, timeout=3.0)
        finally:
            await mgr.stop()

    run_async(scenario())
    logs = _messages(caplog)
    assert any("cloudflared exited" in m and "status unknown" in m for m in logs), logs

