"""design.md success criterion 8, tested at the boundary entrypoints.md
actually documents:

    "async start() -> None -- begins supervision as a background task
    and returns immediately; never awaits the first URL."

The full criterion as worded in design.md ("`_run_daemon` reaches
`bot.start()`, `hook_receiver.start()` and `session_monitor.start()`
regardless of the seam's behaviour... before `TunnelManager.start()`
returned") is a claim about `aipager/cli/daemon.py`'s internal wiring
and startup ordering. `entrypoints.md` -- this package's black-box
contract -- does not document `_run_daemon`'s call signature or any
public seam for driving daemon startup directly (it is not listed under
"CLI commands", unlike `aipager miniapp enable/disable/status`), and the
task brief explicitly forbids reading `aipager/cli/daemon.py` beyond
what `entrypoints.md` documents. Per this package's own black-box
methodology ("if you need to read source to write a test... flag it in
a missing-coverage issue"), the daemon-level half of criterion 8 is
flagged as `missing-coverage` in test-report-1.json rather than faked.

What IS tested here, hard, is the mechanism criterion 8 depends on:
`TunnelManager.start()` must return near-instantly even when the seam
hangs forever -- proven with the seam patched to hang AND given its own
tripwire (raises if invoked a second time), exactly as the task brief
asks. If startup ever blocked on the first URL, this is precisely the
behaviour that would make it block.
"""
from __future__ import annotations

import asyncio
import time


async def _noop(url):
    pass


def test_start_returns_immediately_even_when_the_seam_hangs_forever(
    monkeypatch, run_async, mock_cloudflared_binary,
):
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    tripwire = {"n": 0}
    entered = asyncio.Event()

    async def hanging_seam(binary, port):
        tripwire["n"] += 1
        if tripwire["n"] > 1:
            raise AssertionError(
                "seam invoked a second time while the first call was still "
                "hanging -- start() must not have returned instantly and "
                "unblocked something that retried"
            )
        entered.set()
        await asyncio.Event().wait()  # never resolves

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", hanging_seam)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        t0 = time.monotonic()
        # start() itself must resolve promptly -- wrap in a generous but
        # bounded wait_for so a real block fails the test instead of
        # hanging the whole suite.
        await asyncio.wait_for(manager.start(), timeout=1.0)
        elapsed = time.monotonic() - t0

        # Give the background task a moment to actually begin its first
        # attempt (proving start() didn't just no-op) before checking
        # that the manager itself is genuinely stuck in discovery, not
        # merely "hasn't started yet".
        await asyncio.wait_for(entered.wait(), timeout=3.0)

        await manager.stop()
        return elapsed

    elapsed = run_async(scenario())
    assert elapsed < 0.5, (
        f"TunnelManager.start() took {elapsed:.3f}s to return while its "
        "seam was hanging -- it awaited the first URL instead of returning "
        "immediately"
    )


def test_start_returns_before_any_url_is_discovered_at_all(
    monkeypatch, run_async, mock_cloudflared_binary, FakeProcess,
):
    """A different angle on the same guarantee: even for a seam that
    WOULD eventually succeed (not hang forever, just slowly), start()
    must not await that success either -- `current_url` should still be
    empty immediately after `start()` returns, in a scenario engineered
    so the seam cannot possibly have completed yet (it is blocked on an
    event the test controls and has not released).
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    release = asyncio.Event()
    proc = FakeProcess()

    async def slow_seam(binary, port):
        await release.wait()
        return proc, "https://eventually-discovered.trycloudflare.com"

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", slow_seam)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        await asyncio.wait_for(manager.start(), timeout=1.0)
        # start() has returned, but the seam is still blocked on
        # `release` -- current_url must reflect "nothing discovered yet".
        assert manager.current_url == "", (
            "current_url was already non-empty immediately after start() "
            "returned, even though the seam has not been released yet"
        )
        release.set()
        await manager.stop()

    run_async(scenario())
