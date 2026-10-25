"""Tests for aipager.miniapp.tunnel_manager — spawn_and_discover_url,
_backoff_seconds, and the TunnelManager supervision loop.

No test here spawns a real subprocess: the spawn tests run against
tests/tunnel_fakes.py's process table, which replaces every seam the
tunnel code spawns, probes or signals through (``self_restart._popen``
and ``_kill`` among them, both refused by default). TunnelManager tests additionally
patch `ensure_cloudflared` to a fixed AsyncMock so the restart loop
never touches the real cloudflared_fetch download path (that module has
its own test file) — the one thing under test here is TunnelManager's
OWN restart/backoff/ceiling/shutdown logic, driven entirely through the
documented `spawn_and_discover_url` seam.
"""

from __future__ import annotations

import asyncio
import signal
import stat
import subprocess
from unittest.mock import AsyncMock

import pytest

from tests import tunnel_fakes

from aipager.miniapp import cloudflared_fetch
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm


# ===========================================================================
# Fakes
# ===========================================================================

class _FakeStderr:
    """Feeds canned lines, then EOF (b""). Only a shape the fake
    processes below carry; nothing reads it any more."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProcess:
    """Stand-in for asyncio.subprocess.Process. `terminate()` and
    `kill()` both resolve `wait()` by default (a clean, cooperative
    child) — override `terminate` on an instance to test the
    SIGTERM-doesn't-work escalation path."""

    def __init__(self, stderr_lines: list[bytes] | None = None):
        self.stderr = _FakeStderr(stderr_lines or [])
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.killed = False
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def die(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.die(-15)

    def kill(self) -> None:
        self.killed = True
        self.die(-9)


# ===========================================================================
# _backoff_seconds — pure
# ===========================================================================

def test_backoff_seconds_doubles_then_caps(monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 2.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 60.0)

    assert tm._backoff_seconds(1) == 2.0
    assert tm._backoff_seconds(2) == 4.0
    assert tm._backoff_seconds(3) == 8.0
    assert tm._backoff_seconds(4) == 16.0
    assert tm._backoff_seconds(5) == 32.0
    assert tm._backoff_seconds(6) == 60.0   # would be 64 — capped
    assert tm._backoff_seconds(10) == 60.0  # stays capped


def test_backoff_seconds_respects_a_different_base_and_cap(monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 1.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 3.0)

    assert tm._backoff_seconds(1) == 1.0
    assert tm._backoff_seconds(2) == 2.0
    assert tm._backoff_seconds(3) == 3.0  # would be 4 — capped
    assert tm._backoff_seconds(4) == 3.0


# ===========================================================================
# spawn_and_discover_url
# ===========================================================================

def test_spawns_cloudflared_detached_and_reads_its_url_from_the_metrics(monkeypatch, run_async):
    """Detached: its own session, no death signal, no pipe (a Go program
    exits on SIGPIPE writing to a pipe a restart closed), its metrics
    server on a free local port, and the address read from that server's
    /quicktunnel, never from the process's output. Its stderr goes to a
    regular file (the quick-tunnel request's failure is printed only
    there), never a pipe; stdin and stdout to /dev/null."""
    table = tunnel_fakes.install(monkeypatch)
    table.hosts[40001] = '{"hostname":"yes-consolidation-math-shorter.trycloudflare.com"}'

    tunnel, url = run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))

    assert url == "https://yes-consolidation-math-shorter.trycloudflare.com"
    assert isinstance(tunnel, tm.Tunnel) and tunnel.pid == 4000
    assert tunnel.metrics_port == 40001 and tunnel.host == url[len("https://"):]
    [(argv, kwargs)] = table.spawns
    assert argv[:9] == table.tunnel_argv()
    assert argv[9] == "--logfile" and argv[10].endswith("aipager-cloudflared.log")
    assert kwargs["start_new_session"] is True
    assert "preexec_fn" not in kwargs
    for stream in ("stdin", "stdout"):
        assert kwargs[stream] == subprocess.DEVNULL
    [mode] = table.stderr_modes
    assert stat.S_ISREG(mode), "stderr must be a regular file, never a pipe"
    assert stat.S_IMODE(mode) == 0o600


def test_raises_when_the_process_exits_before_a_url_appears(monkeypatch, run_async):
    table = tunnel_fakes.install(monkeypatch)

    async def _gone_first(port, path):
        table.alive.discard(4000)
        return None
    monkeypatch.setattr(tm, "_query_metrics", _gone_first)

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))


def test_kills_and_raises_on_discovery_timeout(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", 0.02)
    table = tunnel_fakes.install(monkeypatch)
    table.hosts[40001] = '{"hostname":""}'            # still negotiating

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))
    assert (4000, signal.SIGKILL) in table.signals
    assert 4000 not in table.alive


@pytest.mark.parametrize("host", [
    "evil.example.com", "words.trycloudflare.com.evil.example", "evil/words.trycloudflare.com",
], ids=["other-site", "lookalike-suffix", "lookalike-path"])
def test_an_address_that_is_not_a_quick_tunnel_is_never_taken(monkeypatch, run_async, host):
    monkeypatch.setattr("aipager.config.TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", 0.02)
    table = tunnel_fakes.install(monkeypatch)
    table.hosts[40001] = '{"hostname":"%s"}' % host

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))


def test_wraps_a_spawn_oserror(monkeypatch, run_async):
    table = tunnel_fakes.install(monkeypatch)
    table.spawn_error = OSError("no such file or directory")

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))


# ===========================================================================
# TunnelManager
# ===========================================================================

@pytest.fixture(autouse=True)
def _reset_managed_tunnel_url():
    """tunnel.py's managed-URL slot is a bare module-level global —
    reset it around every test so one test's TunnelManager can never
    leak state into the next."""
    tunnel_mod.set_managed_tunnel_url("")
    yield
    tunnel_mod.set_managed_tunnel_url("")


@pytest.fixture(autouse=True)
def _fake_binary(monkeypatch):
    """Every TunnelManager test gets a working `ensure_cloudflared` double
    by default — the binary-fetch path is cloudflared_fetch.py's own
    tests' job, not this file's. Tests that specifically want a fetch
    failure override this explicitly."""
    monkeypatch.setattr(
        cloudflared_fetch, "ensure_cloudflared",
        AsyncMock(return_value="/fake/cloudflared"),
    )


def test_start_returns_immediately_without_awaiting_the_first_url(
    monkeypatch, run_async,
):
    """entrypoints.md: start() 'never awaits the first URL'. Proven here
    by making discovery hang forever and asserting start() still
    returns promptly."""
    calls = {"n": 0}

    async def _hangs(binary, port):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("seam's own tripwire — called more than once")
        await asyncio.Event().wait()  # never resolves
        raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr(tm, "spawn_and_discover_url", _hangs)

    async def _scenario():
        manager = tm.TunnelManager(8765, AsyncMock())
        await asyncio.wait_for(manager.start(), timeout=0.5)
        await manager.stop()  # clean up the still-pending task

    run_async(_scenario())


def test_publishes_the_discovered_url(monkeypatch, run_async):
    proc = _FakeProcess()

    async def _spawn(binary, port):
        return proc, "https://one.trycloudflare.com"
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def _scenario():
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _on_url_change(url):
            await queue.put(url)

        manager = tm.TunnelManager(8765, _on_url_change)
        await manager.start()

        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert first == "https://one.trycloudflare.com"
        assert manager.current_url == "https://one.trycloudflare.com"

        await manager.stop()

    run_async(_scenario())


def test_death_clears_then_restart_republishes_a_new_url(monkeypatch, run_async):
    """Design.md success criteria 2 + 3 together: on death the button is
    cleared (on_url_change("")) BEFORE the backoff sleep, and a restart
    that discovers a DIFFERENT url republishes it — the old url is never
    offered again."""
    proc1 = _FakeProcess()
    proc2 = _FakeProcess()
    outcomes = [
        (proc1, "https://one.trycloudflare.com"),
        (proc2, "https://two.trycloudflare.com"),
    ]

    async def _spawn(binary, port):
        return outcomes.pop(0)
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)

    async def _scenario():
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _on_url_change(url):
            await queue.put(url)

        manager = tm.TunnelManager(8765, _on_url_change)
        await manager.start()

        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert first == "https://one.trycloudflare.com"

        proc1.die(0)  # the child exits unexpectedly

        cleared = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert cleared == ""

        second = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert second == "https://two.trycloudflare.com"
        assert second != first

        await manager.stop()

    run_async(_scenario())


def test_set_url_skips_a_redundant_republish_of_the_same_value(run_async):
    published = []

    async def _on_url_change(url):
        published.append(url)

    async def _scenario():
        manager = tm.TunnelManager(8765, _on_url_change)
        await manager._set_url("https://same.trycloudflare.com")
        await manager._set_url("https://same.trycloudflare.com")  # no-op

    run_async(_scenario())
    assert published == ["https://same.trycloudflare.com"]


def test_fast_attempts_stop_at_the_ceiling_then_the_loop_waits_slowly(
    monkeypatch, run_async, caplog,
):
    """The fast budget, with the tripwire the task brief calls for: the
    fake raises if called an (N+1)th time, not merely a counter checked
    after the fact. N=3 is a fixed literal, independent of whatever
    TUNNEL_RESTART_MAX_ATTEMPTS is patched to. The loop no longer gives
    up for the run (spec requirement 5): after N it waits in the slow
    phase (TUNNEL_RETRY_SLOW_SECONDS, left large here), still running,
    having said so once in a WARNING."""
    from aipager.miniapp import tunnel_state

    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 3)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)

    calls = {"n": 0}

    async def _always_fails(binary, port):
        calls["n"] += 1
        if calls["n"] > 3:
            raise AssertionError(
                "spawn_and_discover_url invoked an (N+1)th time - the tripwire",
            )
        raise tm.TunnelLaunchError("simulated failure")
    monkeypatch.setattr(tm, "spawn_and_discover_url", _always_fails)

    published = []

    async def _on_url_change(url):
        published.append(url)

    async def _scenario():
        manager = tm.TunnelManager(8765, _on_url_change)
        with caplog.at_level("WARNING"):
            await manager.start()
            for _ in range(200):
                await asyncio.sleep(0.005)
            still_running = not manager._task.done()
            status = tunnel_state.current_status()
            await manager.stop()
        return still_running, status

    still_running, status = run_async(_scenario())

    assert calls["n"] == 3
    assert still_running, "the loop gave up instead of waiting slowly"
    assert status.phase == tunnel_state.SLOW and status.attempt == 3
    # Every attempt failed before ever discovering a URL. The manager's
    # first answer always goes out once (the daemon leaves the startup
    # clear to it, so a button the last run left is removed), and after
    # that only real changes: one "" publish, not one per attempt.
    assert published == [""]
    messages = [r.getMessage() for r in caplog.records]
    assert sum("cloudflared" in m and "retried slowly" in m for m in messages) == 1


def test_binary_fetch_failure_counts_toward_the_ceiling_without_spawning(
    monkeypatch, run_async,
):
    from aipager.miniapp import tunnel_state

    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 2)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared", fetch)

    async def _must_not_be_called(binary, port):
        raise AssertionError(
            "spawn_and_discover_url must never run when the binary is unavailable",
        )
    monkeypatch.setattr(tm, "spawn_and_discover_url", _must_not_be_called)

    async def _scenario():
        manager = tm.TunnelManager(8765, AsyncMock())
        await manager.start()
        for _ in range(200):
            await asyncio.sleep(0.005)
        assert fetch.await_count == 2
        assert not manager._task.done()
        status = tunnel_state.current_status()
        assert status.phase == tunnel_state.SLOW
        assert status.reason == tunnel_state.BINARY_MISSING
        assert manager.current_url == ""
        await manager.stop()

    run_async(_scenario())


def test_stop_terminates_a_live_child_and_is_idempotent(monkeypatch, run_async):
    proc = _FakeProcess()

    async def _spawn(binary, port):
        return proc, "https://one.trycloudflare.com"
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def _scenario():
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _on_url_change(url):
            await queue.put(url)

        manager = tm.TunnelManager(8765, _on_url_change)
        await manager.start()
        await asyncio.wait_for(queue.get(), timeout=1.0)

        await asyncio.wait_for(manager.stop(), timeout=1.0)
        assert proc.terminate_calls == 1
        assert manager.current_url == ""
        assert tunnel_mod.get_managed_tunnel_url() == ""

        await asyncio.wait_for(manager.stop(), timeout=1.0)  # idempotent
        assert proc.terminate_calls == 1

    run_async(_scenario())


def test_stop_escalates_to_kill_when_terminate_is_ignored(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.02)
    proc = _FakeProcess()
    # Simulate a wedged child: terminate() is recorded but does NOT
    # resolve wait() — only kill() does.
    proc.terminate = lambda: setattr(proc, "terminate_calls", proc.terminate_calls + 1)

    async def _spawn(binary, port):
        return proc, "https://one.trycloudflare.com"
    monkeypatch.setattr(tm, "spawn_and_discover_url", _spawn)

    async def _scenario():
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _on_url_change(url):
            await queue.put(url)

        manager = tm.TunnelManager(8765, _on_url_change)
        await manager.start()
        await asyncio.wait_for(queue.get(), timeout=1.0)

        await asyncio.wait_for(manager.stop(), timeout=1.0)
        assert proc.terminate_calls == 1
        assert proc.killed is True

    run_async(_scenario())


def test_stop_mid_backoff_cancels_cleanly_with_no_dangling_task(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 1000.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 1000.0)

    attempt_started = asyncio.Event()

    async def _always_fails(binary, port):
        attempt_started.set()
        raise tm.TunnelLaunchError("simulated failure")
    monkeypatch.setattr(tm, "spawn_and_discover_url", _always_fails)

    async def _scenario():
        manager = tm.TunnelManager(8765, AsyncMock())
        await manager.start()
        await asyncio.wait_for(attempt_started.wait(), timeout=1.0)
        # A handful of scheduling ticks — a fixed literal, not derived
        # from the (huge) backoff constant — to let the loop actually
        # reach `await asyncio.sleep(1000.0)` before we cancel it.
        for _ in range(5):
            await asyncio.sleep(0)

        task_ref = manager._task
        assert task_ref is not None and not task_ref.done()

        # If cancellation didn't work, this would hang for the full
        # 1000s backoff instead of returning — the timeout below is
        # what actually proves "cancels cleanly", not just "returns".
        await asyncio.wait_for(manager.stop(), timeout=1.0)

        assert task_ref.done()
        assert manager._task is None

    run_async(_scenario())


def test_stop_before_start_is_a_safe_noop(run_async):
    async def _scenario():
        manager = tm.TunnelManager(8765, AsyncMock())
        await manager.stop()
        assert manager.current_url == ""

    run_async(_scenario())


# ===== cancellation during URL discovery must not leak the child ==========

def test_cancelling_during_discovery_kills_the_child(run_async, monkeypatch):
    """The window between spawning cloudflared and discovering its URL is
    ~7-20 seconds in reality, and it happens on every spawn AND every
    restart. If the supervision task is cancelled in that window,
    CancelledError (a BaseException, not an Exception) bypasses the
    TimeoutError/TunnelLaunchError handler, and TunnelManager._proc has
    not been assigned yet, so stop() has no handle to terminate.

    Without the fix that leaks a live cloudflared holding a PUBLIC tunnel
    open past daemon shutdown.
    """
    table = tunnel_fakes.install(monkeypatch)
    table.hosts[40001] = '{"hostname":""}'            # never yields a URL

    async def _run():
        task = asyncio.create_task(tm.spawn_and_discover_url("/fake/cloudflared", 8765))
        await asyncio.sleep(0.01)                     # inside the discovery wait
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    run_async(_run())
    assert (4000, signal.SIGKILL) in table.signals, "cancelled mid-discovery: tunnel leaked"
