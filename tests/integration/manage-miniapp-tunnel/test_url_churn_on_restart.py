"""design.md success criteria 2 and 3.

2. A second discovered URL (after the first child dies and the manager
   respawns) fires ``on_url_change`` again with the new URL, and the old
   URL is no longer offered by ``resolve_public_url()`` / the managed
   slot anywhere.
3. When the child exits unexpectedly, the manager clears the published
   URL (``on_url_change("")``) *before* respawning, waits a real backoff
   interval, then republishes once a new URL is discovered -- the full
   ordered sequence, not just "eventually a new URL shows up".
"""
from __future__ import annotations

import time

from aipager.miniapp.tunnel import get_managed_tunnel_url, resolve_public_url

URL_1 = "https://alpha-bravo-charlie-delta.trycloudflare.com"
URL_2 = "https://echo-foxtrot-golf-hotel.trycloudflare.com"


def test_churn_republishes_and_retires_the_old_url(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc1 = FakeProcess()
    proc2 = FakeProcess()
    attempts = {"n": 0}

    async def fake_seam(binary, port):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return proc1, URL_1
        return proc2, URL_2

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.05)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        changes = []

        async def on_change(url):
            changes.append(url)

        manager = TunnelManager(port=8765, on_url_change=on_change)
        try:
            await manager.start()
            await wait_until(lambda: manager.current_url == URL_1, timeout=5.0)
            # Kill the first child to force a respawn with a new URL.
            proc1.die(1)
            await wait_until(lambda: manager.current_url == URL_2, timeout=5.0)

            assert await resolve_public_url() == URL_2
            assert get_managed_tunnel_url() == URL_2
            assert URL_1 not in (await resolve_public_url(), get_managed_tunnel_url())
        finally:
            await manager.stop()

        return changes

    changes = run_async(scenario())
    assert URL_1 in changes
    assert URL_2 in changes
    assert changes[-1] == URL_2, f"most recent on_url_change call was not the new URL: {changes}"


def test_death_clears_the_button_before_respawning_and_waits_a_backoff(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    """The ordered sequence: [URL_1, "", URL_2] -- the clear must happen
    before the respawn attempt, not be skipped or coalesced away, and a
    real (if tiny) backoff interval must actually elapse between the
    clear and the second discovery, proving the manager did not just
    retry instantaneously in a tight loop.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc1 = FakeProcess()
    proc2 = FakeProcess()
    attempts = {"n": 0}
    call_times = []

    async def fake_seam(binary, port):
        call_times.append(time.monotonic())
        attempts["n"] += 1
        if attempts["n"] == 1:
            return proc1, URL_1
        return proc2, URL_2

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    # Small but measurable -- large enough that "no sleep happened" is
    # unambiguous against scheduler jitter, tiny enough the test stays fast.
    BACKOFF = 0.15
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", BACKOFF)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 1.0)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        changes = []

        async def on_change(url):
            changes.append(url)

        manager = TunnelManager(port=8765, on_url_change=on_change)
        try:
            await manager.start()
            await wait_until(lambda: manager.current_url == URL_1, timeout=5.0)
            proc1.die(1)
            await wait_until(lambda: manager.current_url == URL_2, timeout=5.0)
        finally:
            await manager.stop()
        return changes

    changes = run_async(scenario())

    assert changes == [URL_1, "", URL_2], (
        f"expected clear-before-respawn ordering, got {changes}"
    )
    assert len(call_times) == 2
    elapsed = call_times[1] - call_times[0]
    assert elapsed >= BACKOFF * 0.5, (
        f"second spawn attempt happened only {elapsed:.3f}s after the first "
        f"death -- expected at least ~{BACKOFF}s of backoff"
    )
