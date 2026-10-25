"""The managed tunnel never gives up for the run (spec requirement 5),
and its forever loop stays bounded in rate.

Intervals come from config values and the module's own seams
(``_SLOW_FLOOR_SECONDS``, ``_monotonic``), never from patching sleep or
the clock. Every seam fake carries a tripwire against a spin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import cloudflared_fetch
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm
from aipager.miniapp import tunnel_state as ts
from tests import tunnel_fakes

MAX = 3
TRIPWIRE = 40


@pytest.fixture
def loop_config(monkeypatch):
    tunnel_mod.set_managed_tunnel_url("")
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared",
                        AsyncMock(return_value="/fake/cloudflared"))
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", MAX)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel_mod, "detect_public_url", lambda: None)
    yield
    tunnel_mod.set_managed_tunnel_url("")


class _Seam:
    """``spawn_and_discover_url`` that fails, with a tripwire."""

    def __init__(self, real=None):
        self.calls = 0
        self.overuse = 0
        self.succeed_from: int | None = None
        self.real = real            # the real spawn, for a fixed machine

    async def __call__(self, binary, port):
        self.calls += 1
        if self.calls > TRIPWIRE:
            self.overuse += 1
            await asyncio.Event().wait()           # stop the runaway here
        if self.succeed_from is not None and self.calls >= self.succeed_from:
            return await self.real(binary, port)
        raise tm.TunnelLaunchError("simulated failure")


async def _until(pred, limit=3.0):
    end = time.monotonic() + limit
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


async def _settle(read, *, stable=15, interval=0.02, limit=5.0):
    end = time.monotonic() + limit
    last, same = read(), 0
    while time.monotonic() < end:
        await asyncio.sleep(interval)
        now = read()
        same = same + 1 if now == last else 0
        last = now
        if same >= stable:
            return now
    raise AssertionError(f"never settled (last={last!r})")


def test_slow_phase_logs_once_then_quietly(loop_config, monkeypatch, run_async, caplog):
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.03)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.03)
    seam = _Seam()
    monkeypatch.setattr(tm, "spawn_and_discover_url", seam)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: seam.calls >= MAX + 4)
        phase = ts.current_status().phase
        await m.stop()
        return phase

    with caplog.at_level(logging.INFO, logger="aipager.miniapp.tunnel_manager"):
        phase = run_async(scenario())
    assert phase == ts.SLOW
    assert seam.overuse == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == MAX, warnings          # MAX-1 fast failures + the slow start
    assert sum("retried slowly" in w for w in warnings) == 1
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert sum("next slow retry" in i for i in infos) >= 3
    assert sum("slow retry)" in i for i in infos) >= 3


def test_a_zero_slow_interval_never_spins(loop_config, monkeypatch, run_async):
    """TUNNEL_RETRY_SLOW_SECONDS=0 acts as the 60 s floor: after the fast
    budget, no further attempt within the test's window."""
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.0)
    seam = _Seam()
    monkeypatch.setattr(tm, "spawn_and_discover_url", seam)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        final = await _settle(lambda: seam.calls)
        await m.stop()
        return final

    assert run_async(scenario()) == MAX
    assert seam.overuse == 0
    assert tm._slow_interval() == 60.0


@pytest.mark.parametrize("raw,want", [("nan", 60.0), ("-5", 60.0), ("600", 600.0),
                                      ("1e12", 86400.0)])
def test_the_slow_interval_is_clamped(monkeypatch, raw, want):
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", float(raw))
    assert tm._slow_interval() == want


def test_app_attempts_at_most_once_a_minute(loop_config, monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(tm, "_monotonic", lambda: clock["now"])
    seam = _Seam()
    monkeypatch.setattr(tm, "spawn_and_discover_url", seam)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: seam.calls == MAX and m._waiting_slow)
        results = []
        results.append(ts.request_attempt())            # starts one
        await _until(lambda: seam.calls == MAX + 1 and m._waiting_slow)
        results.append(ts.request_attempt())            # too soon
        clock["now"] += 59.0
        results.append(ts.request_attempt())            # still too soon
        clock["now"] += 1.0
        results.append(ts.request_attempt())            # a minute later
        await _until(lambda: seam.calls == MAX + 2 and m._waiting_slow)
        calls = await _settle(lambda: seam.calls)
        await m.stop()
        return results, calls

    results, calls = run_async(scenario())
    assert results == [True, False, False, True]
    assert calls == MAX + 2
    assert seam.overuse == 0


def test_request_attempt_is_refused_outside_the_slow_phase(loop_config, monkeypatch,
                                                           run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 3600.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 3600.0)
    seam = _Seam()
    monkeypatch.setattr(tm, "spawn_and_discover_url", seam)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        before = ts.request_attempt()                   # no manager yet
        await m.start()
        await _until(lambda: seam.calls == 1 and ts.current_status().next_try_at)
        retrying = (ts.current_status().phase, ts.request_attempt())
        for _ in range(10):
            await asyncio.sleep(0.005)
        calls = seam.calls
        await m.stop()
        return before, retrying, calls

    before, retrying, calls = run_async(scenario())
    assert before is False
    assert retrying == (ts.RETRYING, False)
    assert calls == 1


def test_a_success_in_the_slow_phase_heals(loop_config, monkeypatch, run_async):
    table = tunnel_fakes.install(monkeypatch)
    host = "healed-words-here.trycloudflare.com"
    table.hosts[40001] = json.dumps({"hostname": host})
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)
    seam = _Seam(real=tm.spawn_and_discover_url)
    monkeypatch.setattr(tm, "spawn_and_discover_url", seam)
    published = []

    async def on_change(url):
        published.append(url)

    async def scenario():
        m = tm.TunnelManager(8765, on_change)
        await m.start()
        await _until(lambda: m._waiting_slow)
        slow = ts.current_status()
        seam.succeed_from = seam.calls + 1             # the machine was fixed
        assert ts.request_attempt() is True
        await _until(lambda: published[-1:] == [f"https://{host}"])
        healed = ts.current_status()
        url = await tunnel_mod.resolve_public_url()
        await m.stop()
        return slow, healed, url

    slow, healed, url = run_async(scenario())
    assert slow.phase == ts.SLOW and slow.reason == ts.OTHER
    assert healed.phase == ts.WORKING and healed.reason == "" and healed.attempt == 0
    assert url == f"https://{host}"
    assert published == ["", f"https://{host}"]


# ---- the slow phase stays quiet whatever fails (spec requirement 5) -----------------
#
# These run the REAL code that logs on every try: the real probe (failing
# at the aiohttp layer, never the network), the real binary fetch (its
# download failing at urlopen) and a launch that raises. Only the slow
# phase's start may be a WARNING; every later try's lines are INFO.

SLOW_TRIES = 3


class _FailingSession:
    """``aiohttp.ClientSession`` whose GET answers 502 (the live run's
    case) or fails to connect: the probe's two failure lines."""

    mode = "502"
    gets: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, **k):
        type(self).gets.append(url)
        if type(self).mode == "refused":
            raise ConnectionRefusedError("connection refused")
        return _Answer502()


class _Answer502:
    status = 502

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _slow_config(monkeypatch):
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.03)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.03)


def _fail_probe_at_aiohttp(monkeypatch, mode):
    import aiohttp

    from tests.conftest import REAL_PROBE_PUBLIC_URL

    table = tunnel_fakes.install(monkeypatch)
    for i in range(TRIPWIRE + 10):
        table.hosts[40001 + i] = json.dumps({"hostname": f"try-{i}-words.trycloudflare.com"})
    monkeypatch.setattr(tunnel_mod, "probe_public_url", REAL_PROBE_PUBLIC_URL)
    monkeypatch.setattr(tunnel_mod, "PROBE_ATTEMPTS", 3)
    monkeypatch.setattr(tunnel_mod, "PROBE_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(_FailingSession, "mode", mode)
    monkeypatch.setattr(_FailingSession, "gets", [])
    monkeypatch.setattr(aiohttp, "ClientSession", _FailingSession)
    return lambda: len(table.spawns)


def _fail_fetch_at_urlopen(monkeypatch, tmp_path):
    import urllib.error

    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(tmp_path / "cf-cache"))
    monkeypatch.setattr(cloudflared_fetch, "platform_key", lambda: "linux-amd64")
    # The real ensure_cloudflared, not the fixture's AsyncMock.
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared", REAL_ENSURE_CLOUDFLARED)
    opens = []

    def _offline(url, timeout=None):
        opens.append(url)
        if len(opens) > TRIPWIRE:
            raise AssertionError("fetched without bound")
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(cloudflared_fetch.urllib.request, "urlopen", _offline)
    return lambda: len(opens)


def _crash_the_launch(monkeypatch):
    calls = []

    async def _spawn(binary, port):
        calls.append(1)
        if len(calls) > TRIPWIRE:
            await asyncio.Event().wait()
        raise RuntimeError("an unexpected bug in the launch")
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)
    return lambda: len(calls)


REAL_ENSURE_CLOUDFLARED = cloudflared_fetch.ensure_cloudflared

# The per-try line each failure writes, besides the loop's own.
PER_TRY_LINE = {
    "probe_502": "answered 502",
    "probe_refused": "is unreachable",
    "binary_missing": "download failed",
    "launch_crash": "launch attempt crashed",
}


@pytest.mark.parametrize("failure", list(PER_TRY_LINE))
def test_the_slow_phase_logs_one_warning_whatever_fails(loop_config, monkeypatch, run_async,
                                                       caplog, tmp_path, failure):
    _slow_config(monkeypatch)
    if failure == "probe_502":
        tries = _fail_probe_at_aiohttp(monkeypatch, "502")
    elif failure == "probe_refused":
        tries = _fail_probe_at_aiohttp(monkeypatch, "refused")
    elif failure == "binary_missing":
        tries = _fail_fetch_at_urlopen(monkeypatch, tmp_path)
    else:
        tries = _crash_the_launch(monkeypatch)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: tries() >= MAX + SLOW_TRIES + 1, limit=10.0)
        status = ts.current_status()
        await m.stop()
        return status

    with caplog.at_level(logging.INFO):
        status = run_async(scenario())
    assert status.phase == ts.SLOW
    assert tries() <= TRIPWIRE
    records = [r for r in caplog.records if r.name.startswith("aipager")]
    start = [i for i, r in enumerate(records) if "retried slowly" in r.getMessage()]
    assert len(start) == 1, [r.getMessage() for r in records]
    assert records[start[0]].levelno == logging.WARNING
    after = records[start[0] + 1:]
    loud = [r.getMessage() for r in after if r.levelno >= logging.WARNING]
    assert loud == [], f"the slow phase logged warnings: {loud}"
    per_try = [r for r in after if PER_TRY_LINE[failure] in r.getMessage()]
    assert len(per_try) >= SLOW_TRIES, "the failure never ran through the real code"
    assert all(r.levelno == logging.INFO for r in per_try)
    assert sum("next slow retry" in r.getMessage() for r in after) >= SLOW_TRIES


@pytest.mark.parametrize("mode", ["502", "refused"])
def test_the_managers_probe_lines_are_info_also_in_the_fast_phase(loop_config, monkeypatch,
                                                                  run_async, caplog, mode):
    """Each fast failure is one WARNING, the manager's own line: its
    probe's per-check lines (each with the tunnel's URL) are INFO."""
    tries = _fail_probe_at_aiohttp(monkeypatch, mode)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)

    async def scenario():
        m = tm.TunnelManager(8765, AsyncMock())
        await m.start()
        await _until(lambda: tries() == MAX and m._waiting_slow)
        await m.stop()

    with caplog.at_level(logging.INFO):
        run_async(scenario())
    records = [r for r in caplog.records if r.name.startswith("aipager")]
    warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == MAX, warnings
    assert all(w.startswith("cloudflared never_answered") for w in warnings), warnings
    probe_lines = [r for r in records if r.name == "aipager.miniapp.tunnel"]
    assert len(probe_lines) >= MAX * 3
    assert all(r.levelno == logging.INFO for r in probe_lines)


def test_the_bots_own_probe_still_warns(run_async, monkeypatch, caplog, real_probe_public_url):
    """Out of scope and unchanged: the probe of an override or Tailscale
    address (the bot's, not the manager's) warns, also when it runs right
    after a manager probe in the same task: ``_quiet_probe`` resets the
    level before it returns. (Between tasks the level cannot leak at all:
    each task runs in its own copy of the context.)"""
    _fail_probe_at_aiohttp(monkeypatch, "502")

    async def scenario():
        assert await tm._quiet_probe("https://managed-words.trycloudflare.com") is False
        return await tunnel_mod.probe_public_url("https://override.example")

    with caplog.at_level(logging.INFO, logger="aipager.miniapp.tunnel"):
        assert run_async(scenario()) is False
    own = [r for r in caplog.records if "override.example" in r.getMessage()]
    managed = [r for r in caplog.records if "managed-words" in r.getMessage()]
    assert len(own) == 4 and all(r.levelno == logging.WARNING for r in own)
    assert len(managed) == 4 and all(r.levelno == logging.INFO for r in managed)
