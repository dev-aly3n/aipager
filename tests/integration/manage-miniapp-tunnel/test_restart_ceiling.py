"""design.md success criterion 4 -- the safety property:

    "After exactly TUNNEL_RESTART_MAX_ATTEMPTS consecutive failed
    attempts the loop stops permanently for the run, publishes no
    button, and logs one WARNING containing both 'cloudflared' and
    'giving up'; the seam is never invoked an (N+1)th time
    (tripwire-verified)."

The fake seam here does not merely count its calls -- past MAX it raises
its own distinct, loud exception AND records the overuse in a list that
survives even if TunnelManager's own error handling swallows the
exception. The test waits for the call count to *stabilize* (unchanged
across several consecutive polls) before asserting equality, specifically
so "checked early -- happened to look right before the runaway kept
going" cannot produce a false pass.

MAX is a small literal (3) chosen independently of the constant's real
default (8) -- never derived from the constant under test, per the /ship
hard constraints (the incident that made a mutation attempt 1,000,000
requests came from exactly that mistake).
"""
from __future__ import annotations

import logging

from aipager.miniapp.tunnel_manager import TunnelLaunchError

MAX = 3


class _SeamCalledPastCeiling(AssertionError):
    pass


def test_seam_never_invoked_past_the_ceiling_even_when_it_would_keep_failing(
    monkeypatch, run_async, wait_until, settle, mock_cloudflared_binary, caplog,
):
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    call_count = {"n": 0}
    overuse = []

    async def fake_seam(binary, port):
        call_count["n"] += 1
        if call_count["n"] > MAX:
            overuse.append(call_count["n"])
            # Raise loudly and distinctly -- if TunnelManager's generic
            # failure handling swallows this, `overuse` still proves it
            # happened, independent of whether the exception surfaces.
            raise _SeamCalledPastCeiling(
                f"seam invoked a {call_count['n']}th time; ceiling is {MAX}"
            )
        raise TunnelLaunchError("simulated launch failure")

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", MAX)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.02)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        changes = []

        async def on_change(url):
            changes.append(url)

        manager = TunnelManager(port=8765, on_url_change=on_change)
        with caplog.at_level(logging.WARNING):
            try:
                await manager.start()
                # Wait for the call count to stop growing -- do NOT stop
                # polling the instant it first reaches MAX, which cannot
                # tell "reached MAX and stopped" apart from "reached MAX
                # and kept going a moment later".
                final_count = await settle(
                    lambda: call_count["n"], stable_checks=15, interval=0.02,
                    timeout=8.0,
                )
            finally:
                await manager.stop()
        return changes, final_count

    changes, final_count = run_async(scenario())

    assert overuse == [], f"seam was invoked past the ceiling: calls {overuse}"
    assert final_count == MAX, (
        f"expected exactly {MAX} attempts, seam was called {final_count} times"
    )
    assert manager_current_url_is_cleared(changes)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "cloudflared" in r.getMessage()
        and "giving up" in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"expected exactly one 'cloudflared...giving up' WARNING, got "
        f"{len(warnings)}: {[r.getMessage() for r in caplog.records]}"
    )


def manager_current_url_is_cleared(changes):
    # No URL was ever discovered (every attempt failed), so no button
    # should ever have been published -- either changes is empty, or if
    # it recorded anything it must never contain a non-empty URL.
    return all(c == "" for c in changes)


def test_ceiling_still_holds_when_a_few_early_attempts_almost_succeed(
    monkeypatch, run_async, FakeProcess, settle, mock_cloudflared_binary, caplog,
):
    """Same property, but with the discovered-then-immediately-died shape
    (a URL is found, published, and then the child dies right away) mixed
    in -- proving the ceiling counts failed *attempts* generically, not
    just a specific failure mode, and is not reset by an intervening
    partial success.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    call_count = {"n": 0}
    overuse = []

    async def fake_seam(binary, port):
        call_count["n"] += 1
        if call_count["n"] > MAX:
            overuse.append(call_count["n"])
            raise _SeamCalledPastCeiling(
                f"seam invoked a {call_count['n']}th time; ceiling is {MAX}"
            )
        if call_count["n"] == 1:
            proc = FakeProcess()
            proc.die(1)  # dies immediately after "success"
            return proc, "https://short-lived-attempt-one.trycloudflare.com"
        raise TunnelLaunchError("simulated launch failure")

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", MAX)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.02)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        with caplog.at_level(logging.WARNING):
            try:
                await manager.start()
                final_count = await settle(
                    lambda: call_count["n"], stable_checks=15, interval=0.02,
                    timeout=8.0,
                )
            finally:
                await manager.stop()
        return final_count

    final_count = run_async(scenario())

    assert overuse == [], f"seam was invoked past the ceiling: calls {overuse}"
    assert final_count == MAX


async def _noop(url):
    pass
