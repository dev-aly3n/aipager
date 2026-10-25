"""The tunnel's state and the never-give-up retry (spec requirements 3 and
5; design.md success criteria "After MAX fast failures ..." and "/app in
the slow phase ..."; entrypoints.md ``current_status``,
``request_attempt``, ``TUNNEL_RETRY_SLOW_SECONDS``).

Phases as partitions: starting, working, retrying, slow (and no manager).
Boundaries: the MAX-th failure (retrying -> slow), the slow interval's
60 s floor (0, 59, negative, above the floor), the /app limit at 59.9 s
and 60 s. Error guessing: a spin in the forever loop (the OOM history),
a warning flood in the slow phase.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time

import pytest

import aipager.miniapp.tunnel_manager as tm
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_state
from aipager.miniapp.tunnel_manager import TunnelLaunchError

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    FakeProcess, forever, settle, wait_until,
)

URL = "https://slow-retry-heals-words.trycloudflare.com"
MAX = 3


def _fail(reason=None):
    return TunnelLaunchError("simulated launch failure", reason=reason)


def _status():
    return tunnel_state.current_status()


async def _drive(make_manager, body):
    mgr = make_manager()
    await mgr.start()
    try:
        return await body(mgr)
    finally:
        await mgr.stop()


# ---- phases ----------------------------------------------------------------

def test_no_manager_means_no_status():
    assert tunnel_state.current_status() is None


def test_first_launch_is_starting(seam, make_manager, run_async):
    seam.steps = [forever]   # the first launch never finishes

    async def body(mgr):
        await wait_until(lambda: seam.calls >= 1)
        return _status()

    assert run_async(_drive(make_manager, body)).phase == "starting"


def test_a_passed_probe_is_working(seam, make_manager, run_async):
    seam.steps = [(FakeProcess(), URL)]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "working")
        return _status()

    status = run_async(_drive(make_manager, body))
    assert (status.phase, status.reason) == ("working", "")


def test_a_passed_probe_publishes_the_url(seam, make_manager, run_async):
    seam.steps = [(FakeProcess(), URL)]
    holder = {}

    async def body(mgr):
        holder["m"] = mgr
        await wait_until(lambda: URL in mgr.published)

    run_async(_drive(make_manager, body))
    assert URL in holder["m"].published


def test_a_passed_probe_makes_resolve_public_url_return_it(seam, make_manager, run_async):
    seam.steps = [(FakeProcess(), URL)]

    async def body(mgr):
        await wait_until(lambda: mgr.current_url == URL)
        return await tunnel_mod.resolve_public_url()

    assert run_async(_drive(make_manager, body)) == URL


def test_a_failed_launch_is_retrying(seam, make_manager, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    seam.steps = [_fail("dns_incomplete")]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status()

    assert run_async(_drive(make_manager, body)).phase == "retrying"


def test_a_failed_launch_carries_its_reason(seam, make_manager, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    seam.steps = [_fail("dns_incomplete")]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status()

    assert run_async(_drive(make_manager, body)).reason == "dns_incomplete"


def test_a_failed_launch_counts_one_attempt(seam, make_manager, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    seam.steps = [_fail("dns_incomplete")]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status()

    assert run_async(_drive(make_manager, body)).attempt == 1


def test_a_failed_launch_says_when_it_tries_next(seam, make_manager, run_async, monkeypatch):
    """next_try_at is wall-clock epoch seconds: now + the backoff (100 s)."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    seam.steps = [_fail("dns_incomplete")]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status(), time.time()

    status, now = run_async(_drive(make_manager, body))
    assert now + 30 <= status.next_try_at <= now + 300, (status, now)


def test_a_missing_binary_is_binary_missing(make_manager, run_async, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status()

    assert run_async(_drive(make_manager, body)).reason == "binary_missing"


def test_a_missing_binary_never_reaches_a_spawn(make_manager, run_async, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
                        AsyncMock(return_value=None))
    calls = []

    async def seam_fn(binary, port):
        calls.append(binary)
        raise AssertionError("spawned without a binary")

    monkeypatch.setattr(tm, "spawn_and_discover_url", seam_fn)

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase != "starting")

    run_async(_drive(make_manager, body))
    assert calls == []


def test_an_unknown_reason_id_is_never_invented(seam, make_manager, run_async, monkeypatch):
    """A failure with no reason and no cloudflared files is ``other``
    (no guess)."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    seam.steps = [_fail()]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        return _status()

    assert run_async(_drive(make_manager, body)).reason == "other"


def test_stop_clears_the_in_memory_state(seam, make_manager, run_async):
    seam.steps = [_fail("dns_incomplete")]

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        await wait_until(lambda: _status() is not None and _status().phase != "starting")
        await mgr.stop()
        return _status()

    assert run_async(scenario()) is None


# ---- the slow phase -----------------------------------------------------------

def _always_fail(seam, n=50, reason="dns_incomplete"):
    seam.steps = [_fail(reason) for _ in range(n)]


def test_after_max_fast_failures_the_phase_is_slow(seam, make_manager, run_async):
    _always_fail(seam)

    async def body(mgr):
        await settle(lambda: seam.calls)
        return _status()

    assert run_async(_drive(make_manager, body)).phase == "slow"


def test_the_slow_phase_keeps_the_reason(seam, make_manager, run_async):
    _always_fail(seam)

    async def body(mgr):
        await settle(lambda: seam.calls)
        return _status()

    assert run_async(_drive(make_manager, body)).reason == "dns_incomplete"


def test_exactly_max_fast_attempts_then_no_more_within_the_slow_interval(
    seam, make_manager, run_async,
):
    _always_fail(seam)

    async def body(mgr):
        return await settle(lambda: seam.calls, stable_checks=40)

    assert run_async(_drive(make_manager, body)) == MAX


def test_the_max_minus_one_th_failure_is_still_fast(seam, make_manager, run_async, monkeypatch):
    """Boundary: one failure short of MAX is ``retrying``, not ``slow``."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 2)
    _always_fail(seam)

    async def body(mgr):
        await settle(lambda: seam.calls)
        return _status()

    assert run_async(_drive(make_manager, body)).phase == "retrying"


def test_the_slow_phase_says_when_it_tries_next(seam, make_manager, run_async):
    """SLOW is 3600 here: next_try_at is about an hour away."""
    _always_fail(seam)

    async def body(mgr):
        await settle(lambda: seam.calls)
        return _status(), time.time()

    status, now = run_async(_drive(make_manager, body))
    assert now + 3000 <= status.next_try_at <= now + 3700, (status, now)


@pytest.mark.parametrize("slow", [0.0, 59.0, -5.0], ids=["zero", "just_under_floor", "negative"])
def test_a_slow_interval_under_the_floor_never_spins(
    seam, make_manager, run_async, monkeypatch, slow,
):
    """Values under 60 act as 60: within 1.5 s of entering the slow phase
    there is no further attempt."""
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", slow)
    _always_fail(seam)

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX)
        loop_time = time.monotonic()
        while time.monotonic() - loop_time < 1.5:
            await asyncio.sleep(0.05)
        return seam.calls

    assert run_async(_drive(make_manager, body)) == MAX


def test_slow_tries_are_spaced_by_the_floor_when_the_value_is_lower(
    seam, make_manager, run_async, monkeypatch,
):
    """With the floor seam at 0.3 s and the value 0, slow tries come no
    faster than every 0.3 s (tripwire against a tight loop)."""
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.3)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.0)
    _always_fail(seam, n=200)

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX + 3, timeout=5.0)
        return list(seam.times)

    times = run_async(_drive(make_manager, body))
    gaps = [b - a for a, b in zip(times[MAX - 1:], times[MAX:])]
    assert gaps and min(gaps) >= 0.25, gaps


def test_slow_tries_follow_the_value_when_it_is_above_the_floor(
    seam, make_manager, run_async, monkeypatch,
):
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.5)
    _always_fail(seam, n=200)

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX + 2, timeout=5.0)
        return list(seam.times)

    times = run_async(_drive(make_manager, body))
    gaps = [b - a for a, b in zip(times[MAX - 1:], times[MAX:])]
    assert gaps and min(gaps) >= 0.45, gaps


def test_the_slow_phase_really_keeps_trying(seam, make_manager, run_async, monkeypatch):
    """Never give up for the run: several slow tries happen."""
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.1)
    _always_fail(seam, n=200)

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX + 4, timeout=5.0)
        return seam.calls

    assert run_async(_drive(make_manager, body)) >= MAX + 4


def _tm_records(caplog, level):
    return [r for r in caplog.records
            if r.name == "aipager.miniapp.tunnel_manager" and r.levelno == level]


def test_the_slow_phase_warns_once_however_many_tries(
    seam, make_manager, run_async, monkeypatch, caplog,
):
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.1)
    _always_fail(seam, n=200)
    caplog.set_level(logging.DEBUG)
    marks = {}

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX + 1, timeout=5.0)
        marks["early"] = len(_tm_records(caplog, logging.WARNING))
        await wait_until(lambda: seam.calls >= MAX + 6, timeout=5.0)
        marks["late"] = len(_tm_records(caplog, logging.WARNING))

    run_async(_drive(make_manager, body))
    assert marks["late"] == marks["early"], [r.getMessage() for r in caplog.records]


def test_the_slow_phase_beginning_is_one_warning(
    seam, make_manager, run_async, caplog,
):
    """Exactly one WARNING announces the slow retry (it names cloudflared)."""
    _always_fail(seam)
    caplog.set_level(logging.DEBUG)

    async def body(mgr):
        await settle(lambda: seam.calls)

    run_async(_drive(make_manager, body))
    slow_warnings = [r.getMessage() for r in _tm_records(caplog, logging.WARNING)
                     if "slow" in r.getMessage().lower()]
    assert len(slow_warnings) == 1, [r.getMessage() for r in caplog.records]


def test_each_slow_try_logs_at_info(seam, make_manager, run_async, monkeypatch, caplog):
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.1)
    _always_fail(seam, n=200)
    caplog.set_level(logging.DEBUG)
    marks = {}

    async def body(mgr):
        await wait_until(lambda: seam.calls >= MAX + 1, timeout=5.0)
        marks["early"] = len(_tm_records(caplog, logging.INFO))
        await wait_until(lambda: seam.calls >= MAX + 4, timeout=5.0)
        marks["late"] = len(_tm_records(caplog, logging.INFO))

    run_async(_drive(make_manager, body))
    assert marks["late"] - marks["early"] >= 3, marks


# ---- the config value ---------------------------------------------------------------

def _config_value(tmp_path, env_value=None):
    """Import aipager.config in a fresh interpreter under a throwaway HOME
    (the real home is never read) and print the value."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_TG_", "TUNNEL_", "MINIAPP_", "AIPAGER_"))}
    env["HOME"] = str(tmp_path / "fresh-home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "fresh-home" / ".config")
    env.pop("CLAUDE_CONFIG_DIR", None)
    if env_value is not None:
        env["TUNNEL_RETRY_SLOW_SECONDS"] = env_value
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import json, sys; sys.path.insert(0, sys.argv[1]); import aipager.config as c; "
         "print(json.dumps(c.TUNNEL_RETRY_SLOW_SECONDS))",
         os.environ.get("PYTHONPATH", "").split(os.pathsep)[0] or os.getcwd()],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_slow_interval_defaults_to_600(tmp_path):
    assert _config_value(tmp_path) == 600


def test_the_slow_interval_is_env_overridable(tmp_path):
    assert _config_value(tmp_path, "120") == 120


# ---- /app's attempt in the slow phase ------------------------------------------------

class Clock:
    def __init__(self, start=10_000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(tm, "_monotonic", c)
    return c


def test_request_attempt_without_a_manager_is_refused():
    assert tunnel_state.request_attempt() is False


def test_request_attempt_while_starting_is_refused(seam, make_manager, run_async, clock):
    seam.steps = [forever]

    async def body(mgr):
        await wait_until(lambda: seam.calls >= 1)
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is False


def test_request_attempt_while_working_is_refused(seam, make_manager, run_async, clock):
    seam.steps = [(FakeProcess(), URL)]

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "working")
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is False


def test_request_attempt_while_retrying_fast_is_refused(
    seam, make_manager, run_async, clock, monkeypatch,
):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    _always_fail(seam)

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "retrying")
        refused = tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        return refused, seam.calls

    assert run_async(_drive(make_manager, body)) == (False, 1)


def _into_slow(seam):
    _always_fail(seam)

    async def go():
        await settle(lambda: seam.calls)
        assert _status().phase == "slow"
    return go


def test_request_attempt_in_the_slow_phase_is_accepted(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is True


def test_request_attempt_in_the_slow_phase_starts_one_attempt_at_once(
    seam, make_manager, run_async, clock,
):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        return await settle(lambda: seam.calls)

    assert run_async(_drive(make_manager, body)) == MAX + 1


def test_a_second_request_at_once_is_refused(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is False


def test_a_second_request_at_once_starts_nothing(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        tunnel_state.request_attempt()
        return await settle(lambda: seam.calls)

    assert run_async(_drive(make_manager, body)) == MAX + 1


def test_a_request_59_9_s_later_is_refused(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        clock.now += 59.9
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is False


def test_a_request_60_s_later_is_accepted(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        clock.now += 60.0
        return tunnel_state.request_attempt()

    assert run_async(_drive(make_manager, body)) is True


def test_a_request_60_s_later_starts_another_attempt(seam, make_manager, run_async, clock):
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        tunnel_state.request_attempt()
        await settle(lambda: seam.calls)
        clock.now += 60.0
        tunnel_state.request_attempt()
        return await settle(lambda: seam.calls)

    assert run_async(_drive(make_manager, body)) == MAX + 2


def test_a_burst_of_requests_starts_at_most_one_attempt(seam, make_manager, run_async, clock):
    """Concurrency guess: twenty /app taps in the same instant."""
    go = _into_slow(seam)

    async def body(mgr):
        await go()
        for _ in range(20):
            tunnel_state.request_attempt()
        return await settle(lambda: seam.calls)

    assert run_async(_drive(make_manager, body)) == MAX + 1


def test_a_success_in_the_slow_phase_heals_to_working(seam, make_manager, run_async, clock):
    _always_fail(seam, n=MAX)
    seam.steps.append((FakeProcess(), URL))

    async def body(mgr):
        await settle(lambda: seam.calls)
        assert _status().phase == "slow"
        tunnel_state.request_attempt()
        await wait_until(lambda: _status() is not None and _status().phase == "working")
        return _status()

    status = run_async(_drive(make_manager, body))
    assert (status.phase, status.reason, status.attempt) == ("working", "", 0)


def test_a_success_in_the_slow_phase_publishes_the_button(
    seam, make_manager, run_async, clock,
):
    _always_fail(seam, n=MAX)
    seam.steps.append((FakeProcess(), URL))
    holder = {}

    async def body(mgr):
        holder["m"] = mgr
        await settle(lambda: seam.calls)
        tunnel_state.request_attempt()
        await wait_until(lambda: URL in mgr.published)

    run_async(_drive(make_manager, body))
    assert holder["m"].published[-1] == URL


def test_a_success_in_the_slow_phase_is_what_resolve_public_url_returns(
    seam, make_manager, run_async, clock,
):
    _always_fail(seam, n=MAX)
    seam.steps.append((FakeProcess(), URL))

    async def body(mgr):
        await settle(lambda: seam.calls)
        tunnel_state.request_attempt()
        await wait_until(lambda: mgr.current_url == URL)
        return await tunnel_mod.resolve_public_url()

    assert run_async(_drive(make_manager, body)) == URL


def test_a_slow_try_without_app_heals_too(seam, make_manager, run_async, monkeypatch):
    """Fixing the machine heals by itself: the timed slow try succeeds."""
    monkeypatch.setattr(tm, "_SLOW_FLOOR_SECONDS", 0.1)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 0.2)
    _always_fail(seam, n=MAX + 1)
    seam.steps.append((FakeProcess(), URL))

    async def body(mgr):
        await wait_until(lambda: _status() is not None and _status().phase == "working",
                         timeout=5.0)
        return _status().phase

    assert run_async(_drive(make_manager, body)) == "working"
