"""No WARNING flood (spec requirement 5: "The slow phase logs once when it
begins and quietly (info) on each try, never a warning flood"; design.md
success criterion "After MAX fast failures ...: one WARNING when it
begins, INFO per try"; review-1 rev-iter1-001).

Unlike test_phases_and_slow_retry.py, these run the REAL
``probe_public_url`` and the REAL ``ensure_cloudflared``, because their
own log lines are what flooded:

- the probe asks a loopback TCP port that refuses every connection (a
  socket bound on 127.0.0.1 and never listening: no network, no socket
  under /tmp), with a short probe window;
- the fetch finds no cache and its download fails at the external
  boundary, ``urllib.request.urlopen`` (the pattern the sibling
  manage-miniapp-tunnel package uses).

Partitions of a slow try: a URL that never answers, a missing binary
(two download errors), a launch that fails (TunnelLaunchError) and a
launch that crashes (any other exception). Fast phase: one WARNING per
failed attempt however many probe checks fail (1 or 3). Outside the
manager, the bot's own probe of an override or Tailscale address still
warns, also while the manager's quiet probe runs at the same time.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import urllib.error

import pytest

import aipager.miniapp.tunnel_manager as tm
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_state
from aipager.miniapp.tunnel_manager import TunnelLaunchError

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    FakeProcess, wait_until,
)

# Taken at import, before the root conftest's autouse fixture replaces it
# with an always-reachable fake for each test.
REAL_PROBE = tunnel_mod.probe_public_url


# ---- doubles ---------------------------------------------------------------

@pytest.fixture
def dead_port():
    """A loopback port that refuses every connection at once."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    yield s.getsockname()[1]
    s.close()


@pytest.fixture
def second_dead_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    yield s.getsockname()[1]
    s.close()


def _probe_window(monkeypatch, checks: int, delay: float = 0.01):
    """A short probe window. The probe's own attempt count and delay are
    module constants in tunnel.py (no config value exists for them); the
    test shortens them so a failing probe takes milliseconds, not 90 s."""
    monkeypatch.setattr(tunnel_mod, "PROBE_ATTEMPTS", checks)
    monkeypatch.setattr(tunnel_mod, "PROBE_RETRY_DELAY_SECONDS", delay)


@pytest.fixture
def real_probe(monkeypatch):
    monkeypatch.setattr(tunnel_mod, "probe_public_url", REAL_PROBE)
    _probe_window(monkeypatch, 3)


@pytest.fixture
def offline_fetch(monkeypatch, tmp_path):
    """The real ensure_cloudflared, with an empty cache and no network."""
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(tmp_path / "empty-cf-cache"))

    def _offline(*a, **k):
        raise urllib.error.URLError("simulated offline")

    monkeypatch.setattr("urllib.request.urlopen", _offline)


@pytest.fixture
def quick_slow(monkeypatch):
    """Into the slow phase after 2 fast failures, then a slow try every 0.3 s."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.3)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.3)


def _warnings(caplog):
    return [r for r in caplog.records
            if r.name.startswith("aipager") and r.levelno >= logging.WARNING]


def _status():
    return tunnel_state.current_status()


def _endless(seam, step, n=200):
    seam.steps = [step for _ in range(n)]


def _dead_tunnel(port):
    def step():
        return FakeProcess(), f"http://127.0.0.1:{port}/"
    return step


async def _run_slow(make_manager, seam, caplog, *, slow_tries=3):
    """Run until the slow phase began, then until ``slow_tries`` more
    tries ended; return (warnings at the start of the slow phase,
    warnings at the end, INFO lines from the manager in between)."""
    mgr = make_manager()
    caplog.set_level(logging.DEBUG)
    await mgr.start()
    try:
        await wait_until(lambda: _status() is not None and _status().phase == "slow",
                         timeout=10.0)
        await asyncio.sleep(0.1)          # let the announcement land
        calls0 = seam.calls
        w0 = len(_warnings(caplog))
        i0 = len(caplog.records)
        await wait_until(lambda: seam.calls >= calls0 + slow_tries + 1, timeout=15.0)
        # the (slow_tries+1)-th call means slow_tries whole tries ended
        w1 = len(_warnings(caplog))
        infos = [r for r in caplog.records[i0:]
                 if r.name == "aipager.miniapp.tunnel_manager" and r.levelno == logging.INFO]
        return w0, w1, infos, [r.getMessage() for r in _warnings(caplog)]
    finally:
        await mgr.stop()


# ---- the bot's own probe (outside the manager) still warns -------------------------

def test_the_bots_own_probe_of_a_dead_address_still_warns(
    monkeypatch, dead_port, run_async, caplog,
):
    """An override or Tailscale address is probed once by the bot: its
    failed checks keep their WARNING."""
    _probe_window(monkeypatch, 2)
    caplog.set_level(logging.DEBUG)
    run_async(REAL_PROBE(f"http://127.0.0.1:{dead_port}/"))
    assert len(_warnings(caplog)) >= 1, [r.getMessage() for r in caplog.records]


def test_the_bots_publish_of_a_dead_override_address_still_warns(
    monkeypatch, dead_port, run_async, caplog, mk_bot,
):
    """MINIAPP_PUBLIC_URL pointing at a dead address: the bot's probe in
    publish_miniapp_button warns as before."""
    url = f"http://127.0.0.1:{dead_port}/"
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", url)
    monkeypatch.setattr(tunnel_mod, "probe_public_url", REAL_PROBE)
    _probe_window(monkeypatch, 2)
    bot = mk_bot()
    caplog.set_level(logging.DEBUG)
    run_async(bot.publish_miniapp_button(url))
    assert len(_warnings(caplog)) >= 1, [r.getMessage() for r in caplog.records]


def test_the_bots_probe_still_warns_while_the_managers_quiet_probe_runs(
    monkeypatch, seam, make_manager, run_async, caplog, quick_slow,
    dead_port, second_dead_port,
):
    """Error guessing: the manager's quiet level must not leak into a
    probe the bot runs at the same moment."""
    monkeypatch.setattr(tunnel_mod, "probe_public_url", REAL_PROBE)
    _probe_window(monkeypatch, 40, delay=0.03)      # each probe lasts > 1 s
    _endless(seam, _dead_tunnel(dead_port))
    bot_url = f"http://127.0.0.1:{second_dead_port}/"

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 1, timeout=5.0)
            await asyncio.sleep(0.2)               # the manager's probe is running
            await REAL_PROBE(bot_url)
        finally:
            await mgr.stop()

    run_async(scenario())
    bot_lines = [r for r in _warnings(caplog) if str(second_dead_port) in r.getMessage()]
    assert bot_lines, [r.getMessage() for r in caplog.records]


# ---- fast phase: one WARNING per failed attempt ---------------------------------------

@pytest.mark.parametrize("checks", [1, 3], ids=["one_probe_check", "three_probe_checks"])
def test_a_never_answering_tunnel_logs_one_warning_per_fast_attempt(
    monkeypatch, seam, make_manager, run_async, caplog, real_probe, dead_port, checks,
):
    _probe_window(monkeypatch, checks)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 5)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    _endless(seam, _dead_tunnel(dead_port))

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: _status() is not None and _status().phase == "retrying",
                             timeout=5.0)
            await asyncio.sleep(0.3)
        finally:
            await mgr.stop()

    run_async(scenario())
    assert len(_warnings(caplog)) == 1, [r.getMessage() for r in _warnings(caplog)]


def test_two_never_answering_fast_attempts_log_two_warnings(
    monkeypatch, seam, make_manager, run_async, caplog, real_probe, dead_port,
):
    """Boundary: the count grows with attempts, never with probe checks."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 5)
    _endless(seam, _dead_tunnel(dead_port), n=2)   # then the third launch blocks

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 3, timeout=5.0)
            await asyncio.sleep(0.3)
        finally:
            await mgr.stop()

    run_async(scenario())
    assert len(_warnings(caplog)) == 2, [r.getMessage() for r in _warnings(caplog)]


# ---- slow phase: no WARNING after the one that announces it --------------------------------

def test_slow_tries_of_a_never_answering_url_log_no_warning(
    seam, make_manager, run_async, caplog, real_probe, quick_slow, dead_port,
):
    _endless(seam, _dead_tunnel(dead_port))
    w0, w1, _infos, msgs = run_async(_run_slow(make_manager, seam, caplog))
    assert w1 == w0, msgs


def test_slow_tries_of_a_never_answering_url_log_at_info(
    seam, make_manager, run_async, caplog, real_probe, quick_slow, dead_port,
):
    _endless(seam, _dead_tunnel(dead_port))
    _w0, _w1, infos, _msgs = run_async(_run_slow(make_manager, seam, caplog))
    assert len(infos) >= 3, [r.getMessage() for r in infos]


def test_a_never_answering_url_announces_the_slow_phase_with_one_warning(
    seam, make_manager, run_async, caplog, real_probe, quick_slow, dead_port,
):
    _endless(seam, _dead_tunnel(dead_port))
    run_async(_run_slow(make_manager, seam, caplog))
    slow = [r.getMessage() for r in _warnings(caplog) if "slow" in r.getMessage().lower()]
    assert len(slow) == 1, [r.getMessage() for r in _warnings(caplog)]


def test_a_never_answering_url_stays_quiet_through_many_slow_tries(
    seam, make_manager, run_async, caplog, real_probe, quick_slow, dead_port, monkeypatch,
):
    """Error guessing: a long stretch of slow tries (each a full probe)."""
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.1)
    _endless(seam, _dead_tunnel(dead_port))
    w0, w1, _infos, msgs = run_async(_run_slow(make_manager, seam, caplog, slow_tries=8))
    assert w1 == w0, msgs


@pytest.mark.parametrize("error", [
    urllib.error.URLError("simulated offline"),
    OSError("network unreachable"),
], ids=["url_error", "os_error"])
def test_slow_tries_with_a_missing_binary_log_no_warning(
    monkeypatch, tmp_path, make_manager, run_async, caplog, quick_slow, error,
):
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(tmp_path / "empty-cf-cache"))
    fetches = []

    def _offline(*a, **k):
        fetches.append(1)
        raise error

    monkeypatch.setattr("urllib.request.urlopen", _offline)

    class Counter:
        calls = 0

    counter = Counter()
    real_ensure = __import__("aipager.miniapp.cloudflared_fetch",
                             fromlist=["ensure_cloudflared"]).ensure_cloudflared

    async def counted(*a, **k):
        counter.calls += 1
        return await real_ensure(*a, **k)

    monkeypatch.setattr("aipager.miniapp.cloudflared_fetch.ensure_cloudflared", counted)
    w0, w1, _infos, msgs = run_async(_run_slow(make_manager, counter, caplog))
    assert w1 == w0, msgs


def test_a_missing_binary_in_the_slow_phase_is_still_binary_missing(
    monkeypatch, make_manager, run_async, caplog, quick_slow, offline_fetch,
):
    """The quiet fetch still reports why: the reason stays binary_missing."""
    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: _status() is not None and _status().phase == "slow",
                             timeout=10.0)
            return _status().reason
        finally:
            await mgr.stop()

    assert run_async(scenario()) == "binary_missing"


@pytest.mark.parametrize("make_error", [
    lambda: TunnelLaunchError("simulated launch failure", reason="dns_incomplete"),
    lambda: TunnelLaunchError("simulated launch failure"),
    lambda: RuntimeError("simulated crash inside the launch"),
    lambda: OSError(8, "Exec format error"),
], ids=["launch_error_with_reason", "launch_error_no_reason", "crash", "spawn_oserror"])
def test_slow_tries_with_a_failing_launch_log_no_warning(
    seam, make_manager, run_async, caplog, quick_slow, make_error,
):
    _endless(seam, make_error)
    w0, w1, _infos, msgs = run_async(_run_slow(make_manager, seam, caplog))
    assert w1 == w0, msgs


def test_a_crashing_launch_in_the_slow_phase_logs_no_traceback_at_warning(
    seam, make_manager, run_async, caplog, quick_slow,
):
    _endless(seam, lambda: RuntimeError("simulated crash inside the launch"))

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: _status() is not None and _status().phase == "slow",
                             timeout=10.0)
            await asyncio.sleep(0.1)
            start = len(caplog.records)
            calls0 = seam.calls
            await wait_until(lambda: seam.calls >= calls0 + 3, timeout=10.0)
            return caplog.records[start:]
        finally:
            await mgr.stop()

    later = run_async(scenario())
    assert not [r for r in later if r.exc_info and r.levelno >= logging.WARNING], \
        [r.getMessage() for r in later]


def test_a_crashing_launch_still_retries_in_the_slow_phase(
    seam, make_manager, run_async, quick_slow,
):
    """A crash is a failed attempt, not the end of the loop."""
    _endless(seam, lambda: RuntimeError("simulated crash inside the launch"))

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: seam.calls >= 5, timeout=10.0)
            return seam.calls
        finally:
            await mgr.stop()

    assert run_async(scenario()) >= 5
