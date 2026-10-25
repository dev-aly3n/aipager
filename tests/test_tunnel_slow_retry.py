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
