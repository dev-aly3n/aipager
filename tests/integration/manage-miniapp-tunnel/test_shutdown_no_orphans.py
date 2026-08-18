"""design.md success criterion 5 -- no orphans.

    "On SIGINT/SIGTERM, manager.stop() is awaited before exit; a live
    fake child receives terminate() and, failing to exit within
    TUNNEL_KILL_TIMEOUT_SECONDS, kill(); calling stop() twice is a
    no-op."

The task brief calls out a race the developer reports finding and fixing:
a live child not terminated if ``stop()`` races a cancel mid-flight.
Probed here specifically: stop() while sleeping in backoff, stop() while
discovery is still in flight (hung seam), and stop() called twice.
"""
from __future__ import annotations

import asyncio

from aipager.miniapp.tunnel_manager import TunnelLaunchError


async def _noop(url):
    pass


def test_stop_terminates_a_live_child_and_escalates_to_kill(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    """A live child that ignores terminate() (FakeProcess's default) must
    be kill()ed after TUNNEL_KILL_TIMEOUT_SECONDS -- proving the
    escalation path actually runs, not just that terminate() was called.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc = FakeProcess()  # terminate() alone never makes it exit

    async def fake_seam(binary, port):
        return proc, "https://stubborn-child-one.trycloudflare.com"

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.1)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await manager.start()
        await wait_until(lambda: manager.current_url != "", timeout=5.0)
        await asyncio.wait_for(manager.stop(), timeout=3.0)

    run_async(scenario())

    assert proc.terminate_calls >= 1, "stop() never sent terminate() to the live child"
    assert proc.kill_calls >= 1, (
        "child ignored terminate() past TUNNEL_KILL_TIMEOUT_SECONDS but "
        "was never kill()ed -- an orphan would survive this"
    )


def test_stop_does_not_kill_a_child_that_exits_cleanly_on_terminate(
    monkeypatch, run_async, FakeCooperativeProcess, wait_until, mock_cloudflared_binary,
):
    """Control case: a well-behaved child that exits on terminate() alone
    must not also be kill()ed -- kill() is escalation, not routine.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc = FakeCooperativeProcess()

    async def fake_seam(binary, port):
        return proc, "https://cooperative-child-one.trycloudflare.com"

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 3.0)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await manager.start()
        await wait_until(lambda: manager.current_url != "", timeout=5.0)
        await asyncio.wait_for(manager.stop(), timeout=3.0)

    run_async(scenario())

    assert proc.terminate_calls == 1
    assert proc.kill_calls == 0, "kill() called on a child that already exited cleanly"


def test_stop_during_backoff_sleep_cancels_cleanly_with_no_further_attempts(
    monkeypatch, run_async, mock_cloudflared_binary,
):
    """stop() called while the manager is asleep between a failed attempt
    and its retry -- the race the developer reports fixing. Must not
    leave the supervision loop making another attempt afterward.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    call_count = {"n": 0}

    async def fake_seam(binary, port):
        call_count["n"] += 1
        raise TunnelLaunchError("simulated failure, forces a backoff sleep")

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    # Long enough that the test can reliably catch it mid-sleep.
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 2.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 2.0)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await manager.start()
        # Give the first (failing) attempt time to run and enter backoff.
        for _ in range(50):
            if call_count["n"] >= 1:
                break
            await asyncio.sleep(0.01)
        assert call_count["n"] == 1, "first attempt never ran before the backoff window"
        seen_after_first_attempt = call_count["n"]

        await asyncio.wait_for(manager.stop(), timeout=3.0)

        # If the race existed, stop() during the sleep would not cancel
        # the loop, and a second attempt would eventually fire.
        await asyncio.sleep(0.3)
        return seen_after_first_attempt

    seen_after_first_attempt = run_async(scenario())

    assert seen_after_first_attempt == 1
    assert call_count["n"] == 1, (
        f"a second spawn attempt ({call_count['n']} total) happened after "
        "stop() was awaited during the backoff sleep"
    )


def test_stop_mid_discovery_returns_promptly_without_hanging(
    monkeypatch, run_async, FakeProcess, mock_cloudflared_binary,
):
    """stop() while the seam itself is still hung (discovery in flight,
    no URL yet, no process handed back to the manager to terminate
    through the normal path). The manager must still return from stop()
    promptly instead of hanging forever waiting on a seam call that will
    never resolve.

    Note this cannot exercise the specific internal race the task brief
    flags ("a live child would not be terminated if cancelled mid-flight"
    during discovery) -- that race lives *inside* the real
    `spawn_and_discover_url`, between the real subprocess actually
    starting and the function returning it to the caller, which this
    seam-replacement approach cannot reach by construction (the seam
    here never creates a real process at all). See test-report-1.json's
    issues for why that half is out of black-box reach.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    tripwire = {"n": 0}
    entered = asyncio.Event()

    async def fake_seam(binary, port):
        tripwire["n"] += 1
        if tripwire["n"] > 1:
            raise AssertionError("seam invoked again while first call still hanging")
        entered.set()
        await asyncio.Event().wait()  # never resolves on its own

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.2)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await manager.start()
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        # stop() itself must not hang just because discovery is stuck.
        await asyncio.wait_for(manager.stop(), timeout=3.0)

    run_async(scenario())  # the outer wait_for calls are the real assertion:
    # if stop() hangs waiting on the stuck discovery call, this test times
    # out and fails loudly rather than passing by accident.
    assert tripwire["n"] == 1


def test_stop_is_idempotent_when_called_twice(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc = FakeProcess()

    async def fake_seam(binary, port):
        return proc, "https://idempotent-stop-check.trycloudflare.com"

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.2)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await manager.start()
        await wait_until(lambda: manager.current_url != "", timeout=5.0)
        await asyncio.wait_for(manager.stop(), timeout=3.0)
        await asyncio.wait_for(manager.stop(), timeout=3.0)  # must not raise or hang

    run_async(scenario())  # no exception == pass
    assert proc.kill_calls <= 1, (
        f"the second stop() call re-killed an already-dead child ({proc.kill_calls}x)"
    )


def test_stop_is_safe_on_an_instance_that_was_never_started(monkeypatch, run_async):
    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await asyncio.wait_for(manager.stop(), timeout=3.0)

    run_async(scenario())  # no exception == pass
