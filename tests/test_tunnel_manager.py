"""Tests for aipager.miniapp.tunnel_manager — spawn_and_discover_url,
_backoff_seconds, and the TunnelManager supervision loop.

No test here spawns a real subprocess: `asyncio.create_subprocess_exec`
is always monkeypatched to a fake. TunnelManager tests additionally
patch `ensure_cloudflared` to a fixed AsyncMock so the restart loop
never touches the real cloudflared_fetch download path (that module has
its own test file) — the one thing under test here is TunnelManager's
OWN restart/backoff/ceiling/shutdown logic, driven entirely through the
documented `spawn_and_discover_url` seam.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import cloudflared_fetch
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm


# ===========================================================================
# Fakes
# ===========================================================================

class _FakeStderr:
    """Feeds canned lines, then EOF (b"")."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _HangingStderr:
    """Never yields a line — simulates a wedged discovery read."""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


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


def _fake_exec_returning(proc: _FakeProcess, captured: dict | None = None):
    async def _exec(*args, **kwargs):
        if captured is not None:
            captured["args"] = args
            captured["kwargs"] = kwargs
        return proc
    return _exec


def _run_and_drain(coro):
    """Like the shared `run_async` fixture, but also cancels and closes
    out any task still pending when the coroutine finishes, then closes
    the loop.

    A successful `spawn_and_discover_url()` schedules a background
    `_drain_stderr` task via `asyncio.create_task` and returns
    immediately — it never awaits that task itself (by design: draining
    stderr is a whole-process-lifetime concern, not part of discovery).
    `run_async`'s plain `run_until_complete` stops the loop the instant
    the awaited coroutine resolves, before the scheduled task ever gets
    a turn to run, and never closes the loop either — left like that,
    the loop is garbage-collected later (mid some unrelated test, since
    GC timing isn't deterministic) still holding a pending task, which
    surfaces as a `BaseEventLoop.__del__` unraisable-exception warning
    attributed to whatever test happened to be running at the time.
    Only needed for tests that reach a successful discovery.
    """
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        return result
    finally:
        loop.close()


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

def test_finds_the_url_inside_the_real_box_art_banner(monkeypatch):
    """The real cloudflared banner is NOT a bare URL — see design.md's
    ORCHESTRATOR VERIFICATION. A line-equality or startswith match would
    fail this; only the regex-search approach passes it."""
    banner = (
        b"2026-08-18T18:59:25Z INF |  "
        b"https://yes-consolidation-math-shorter.trycloudflare.com   |\n"
    )
    proc = _FakeProcess([
        b"2026-08-18T18:59:18Z INF Requesting new quick Tunnel on trycloudflare.com...\n",
        banner,
    ])
    captured: dict = {}
    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec",
                        _fake_exec_returning(proc, captured))

    result_proc, url = _run_and_drain(tm.spawn_and_discover_url("/fake/cloudflared", 8765))

    assert url == "https://yes-consolidation-math-shorter.trycloudflare.com"
    assert result_proc is proc
    assert captured["args"] == (
        "/fake/cloudflared", "tunnel", "--url", "http://127.0.0.1:8765",
        "--no-autoupdate",
    )
    assert captured["kwargs"]["stdout"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == asyncio.subprocess.PIPE


def test_raises_when_the_process_exits_before_a_url_appears(monkeypatch, run_async):
    proc = _FakeProcess([b"some unrelated log line\n"])  # then EOF, no URL
    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec",
                        _fake_exec_returning(proc))

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))


def test_kills_and_raises_on_discovery_timeout(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", 0.02)
    proc = _FakeProcess()
    proc.stderr = _HangingStderr()
    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec",
                        _fake_exec_returning(proc))

    with pytest.raises(tm.TunnelLaunchError):
        run_async(tm.spawn_and_discover_url("/fake/cloudflared", 8765))
    assert proc.killed is True


def test_wraps_a_spawn_oserror(monkeypatch, run_async):
    async def _raise(*a, **k):
        raise OSError("no such file or directory")
    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec", _raise)

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


def test_gives_up_after_exactly_the_ceiling_and_never_calls_the_seam_again(
    monkeypatch, run_async, caplog,
):
    """Design.md success criterion 4, with the tripwire the task brief
    calls for: the fake raises if called an (N+1)th time, not merely a
    counter checked after the fact. N=3 is a fixed literal, independent
    of whatever TUNNEL_RESTART_MAX_ATTEMPTS is patched to — the patched
    value IS 3, chosen here, not read back from the constant."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 3)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)

    calls = {"n": 0}

    async def _always_fails(binary, port):
        calls["n"] += 1
        if calls["n"] > 3:
            raise AssertionError(
                "spawn_and_discover_url invoked an (N+1)th time — the tripwire",
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
            await asyncio.wait_for(manager._task, timeout=1.0)

    run_async(_scenario())

    assert calls["n"] == 3
    # Every attempt failed before ever discovering a URL, so the
    # manager's "" -> "" transition is never a REAL change —
    # on_url_change is correctly never called at all here (design.md:
    # it fires only when the discovered URL differs from the previous
    # one). "publishes no button" means exactly this: no publish call,
    # not a redundant empty one.
    assert published == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("cloudflared" in m and "giving up" in m for m in messages)


def test_binary_fetch_failure_counts_toward_the_ceiling_without_spawning(
    monkeypatch, run_async,
):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 2)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.0)
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared", AsyncMock(return_value=None))

    async def _must_not_be_called(binary, port):
        raise AssertionError(
            "spawn_and_discover_url must never run when the binary is unavailable",
        )
    monkeypatch.setattr(tm, "spawn_and_discover_url", _must_not_be_called)

    async def _scenario():
        manager = tm.TunnelManager(8765, AsyncMock())
        await manager.start()
        await asyncio.wait_for(manager._task, timeout=1.0)
        assert manager.current_url == ""

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
    CancelledError — a BaseException, not an Exception — bypasses the
    TimeoutError/TunnelLaunchError handler, and TunnelManager._proc has
    not been assigned yet, so stop() has no handle to terminate.

    Without the fix that leaks a live cloudflared holding a PUBLIC tunnel
    open past daemon shutdown.
    """
    import asyncio

    from aipager.miniapp import tunnel_manager as tm

    killed = []

    class _FakeProc:
        returncode = None
        def kill(self):
            killed.append(True)
        def terminate(self):
            pass
        async def wait(self):
            return 0

    fake = _FakeProc()

    async def _never_yields_a_url(*_a, **_k):
        # stderr that stays silent — exactly a tunnel still negotiating.
        await asyncio.sleep(3600)

    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec",
                        _make_spawn_returning(fake), raising=False)

    async def _run():
        # Drive the REAL spawn_and_discover_url, then cancel it mid-wait.
        task = asyncio.create_task(tm.spawn_and_discover_url("/nonexistent/cloudflared", 8765))
        await asyncio.sleep(0)          # let it reach the discovery wait
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    run_async(_run())
    assert killed, "cancelled mid-discovery without killing the child — tunnel leaked"


def _make_spawn_returning(proc):
    async def _spawn(*_a, **_k):
        class _Stderr:
            async def readline(self):
                import asyncio as _a2
                await _a2.sleep(3600)   # never yields a URL
                return b""
        proc.stderr = _Stderr()
        return proc
    return _spawn
