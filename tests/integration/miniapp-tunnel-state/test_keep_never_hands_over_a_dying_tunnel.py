"""``stop(keep=True)`` never keeps a tunnel that a failed probe is already
killing (review-1 nit on ``stop(keep=True)``; entrypoints.md
``TunnelManager.stop(keep=False)``, ``MINIAPP_KEEP_TUNNEL``; spec
requirement 1: no tunnel is left behind for a link that never answered).

Everything goes through the fake process table (tests/tunnel_fakes.py:
the seams ``self_restart._popen``/``_kill``, ``tunnel_manager._pid_alive``,
``_cmdline``, ``_boot_id``, ``_query_metrics``, ``_free_port`` and
``tunnel.probe_public_url``). What a keep leaves for the next daemon is
observed from outside: the handover record at ``config.TUNNEL_STATE_FILE``,
a guard launched (``sh``), and whether the process still runs.

Partitions: a cloudflared that ignores SIGTERM for a while (the kill is
in progress when the planned restart lands), one that no signal ends,
and the control case (a healthy tunnel IS kept, so the negatives are not
vacuous).
"""
from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path

import pytest

from aipager.miniapp import tunnel as tunnel_mod

from miniapp_tunnel_state_bb import wait_until  # noqa: I001

HOST = "dying-tunnel-words.trycloudflare.com"
URL = f"https://{HOST}"
PID = 4000          # the fake table's first pid


@pytest.fixture
def table(monkeypatch, fake_binary):
    from tests.tunnel_fakes import install

    t = install(monkeypatch)
    t.hosts[40001] = json.dumps({"hostname": HOST})
    monkeypatch.setattr("aipager.config.MINIAPP_KEEP_TUNNEL", True, raising=False)
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.05)
    # A wide kill window, so the planned restart lands inside it.
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.8)
    # One attempt then a long wait: no second tunnel muddies the picture.
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    return t


def _record() -> Path:
    from aipager import config
    return Path(config.TUNNEL_STATE_FILE)


def _keep_during_failed_probe_kill(table, make_manager, run_async):
    table.dead_urls.add(URL)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: (PID, signal.SIGTERM) in table.signals, timeout=5.0)
            in_kill = PID in table.alive
        finally:
            await asyncio.wait_for(mgr.stop(keep=True), timeout=10.0)
        return in_kill

    return run_async(scenario())


@pytest.fixture
def stubborn_kept(table, make_manager, run_async):
    table.stubborn.add(PID)
    in_kill = _keep_during_failed_probe_kill(table, make_manager, run_async)
    return {"in_kill": in_kill, "table": table}


def test_the_restart_landed_inside_the_kill(stubborn_kept):
    """Control: SIGTERM was sent and the process still ran at stop()."""
    assert stubborn_kept["in_kill"] is True


def test_a_tunnel_being_killed_gets_no_handover_guard(stubborn_kept):
    assert stubborn_kept["table"].guards() == []


def test_a_tunnel_being_killed_leaves_no_handover_record(stubborn_kept):
    assert not _record().exists()


def test_a_tunnel_being_killed_is_ended_anyway(stubborn_kept):
    assert PID not in stubborn_kept["table"].alive


def test_a_tunnel_being_killed_gets_the_sigkill(stubborn_kept):
    assert (PID, signal.SIGKILL) in stubborn_kept["table"].signals


def test_a_tunnel_no_signal_ends_is_never_handed_over(table, make_manager, run_async):
    """Error guessing: the dying process never goes away; stop(keep=True)
    still returns and still hands nothing over."""
    table.unkillable.add(PID)
    _keep_during_failed_probe_kill(table, make_manager, run_async)
    assert table.guards() == []


def test_a_tunnel_no_signal_ends_leaves_no_handover_record(table, make_manager, run_async):
    table.unkillable.add(PID)
    _keep_during_failed_probe_kill(table, make_manager, run_async)
    assert not _record().exists()


def test_the_managed_url_is_empty_after_keeping_a_dying_tunnel(
    table, make_manager, run_async,
):
    table.stubborn.add(PID)
    _keep_during_failed_probe_kill(table, make_manager, run_async)
    assert tunnel_mod.get_managed_tunnel_url() == ""


# ---- control: a healthy tunnel IS kept --------------------------------------------

@pytest.fixture
def healthy_kept(table, make_manager, run_async):
    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: URL in mgr.published, timeout=5.0)
        finally:
            await asyncio.wait_for(mgr.stop(keep=True), timeout=10.0)

    run_async(scenario())
    return table


def test_control_a_healthy_tunnel_is_kept_alive(healthy_kept):
    assert PID in healthy_kept.alive


def test_control_a_healthy_tunnel_gets_a_handover_record(healthy_kept):
    assert _record().exists()


def test_control_a_healthy_tunnel_gets_a_guard(healthy_kept):
    assert len(healthy_kept.guards()) == 1
