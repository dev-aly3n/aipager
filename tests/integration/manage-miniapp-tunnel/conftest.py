"""Package-wide isolation for the managed-tunnel integration tests.

Three guards, all autouse, because this package specifically exists to
exercise process-spawning and network-downloading code paths on a machine
that has BOTH a live daemon running (``/home/aly/aipager``) AND a history
of two OOM incidents this week from unbounded loops. A test that forgets
to fake a seam here must fail LOUDLY, not silently spawn a real
``cloudflared`` or hit the real network:

1. ``AIPAGER_CLOUDFLARED_CACHE_DIR`` is redirected to ``tmp_path`` so
   ``cloudflared_fetch.cache_dir()`` never writes under the operator's
   real ``~/.local/share/aipager/cloudflared/``.
2. ``asyncio.create_subprocess_exec`` is patched to raise by default.
   Every test that wants a "spawn" must fake
   ``aipager.miniapp.tunnel_manager.spawn_and_discover_url`` itself (the
   documented seam) — nothing in this package should ever reach the real
   ``asyncio.create_subprocess_exec``, so a leak here is a real bug, not
   test noise.
3. ``urllib.request.urlopen`` is patched to raise by default. Tests that
   exercise ``ensure_cloudflared()``'s download path override it locally
   with an explicit fake; nothing here should ever touch the real
   Cloudflare download endpoint.

Both patches also record every call they see (before raising), exposed
via the ``leak_guard`` fixture, so a test can assert zero-calls positively
instead of merely relying on "it would have raised."
"""
from __future__ import annotations

import asyncio
import time
import urllib.request
from dataclasses import dataclass, field

import pytest

# ---------------------------------------------------------------------------
# Shared black-box test doubles, exposed as fixtures rather than imported
# from a plain sibling module -- this package's directory name has a
# hyphen (matching the /ship slug, like its siblings
# ``miniapp-sessions-grid-diff-viewer`` and ``serve-telegram-mini-app``),
# which is not a valid dotted-import segment. Fixture injection sidesteps
# that entirely and is the idiomatic pytest way to share fakes anyway.
# ---------------------------------------------------------------------------


class _FakeProcess:
    """A double for the ``Process`` half of ``spawn_and_discover_url``'s
    ``(process, url)`` return value -- exactly the surface entrypoints.md
    documents: ``returncode``, async ``wait()``, ``terminate()``,
    ``kill()``. Nothing else, so a test can never accidentally depend on
    an attribute the real contract doesn't promise.

    By default ``terminate()`` does NOT make the process exit -- a real
    stubborn child ignoring SIGTERM -- so tests that need "terminate
    works" call ``.die()`` themselves, or use ``FakeCooperativeProcess``.
    """

    def __init__(self):
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._exited = asyncio.Event()

    def die(self, code: int = 1) -> None:
        """Simulate the child exiting on its own (crash, network drop)."""
        self.returncode = code
        self._exited.set()

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        if self.returncode is None:
            self.returncode = -9
        self._exited.set()

    async def wait(self):
        await self._exited.wait()
        return self.returncode


class _FakeCooperativeProcess(_FakeProcess):
    """A double whose ``terminate()`` behaves like a well-behaved real
    child and actually exits, so ``kill()`` is never needed -- the
    control case against ``FakeProcess``'s kill-required default, for
    criterion 5's "clean stop, no escalation" half.
    """

    def terminate(self) -> None:
        super().terminate()
        self.die(-15)


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01):
    """Poll ``predicate()`` until truthy or ``timeout`` (a fixed literal
    the caller supplies, never derived from the constant under test --
    see the /ship hard constraints) elapses. Raises ``TimeoutError`` on
    expiry so a stuck manager fails the test loudly and quickly rather
    than hanging the suite.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


async def _settle(predicate, *, stable_checks: int = 8, interval: float = 0.02,
                   timeout: float = 5.0):
    """Wait until ``predicate()`` (typically a call counter) stops
    changing for ``stable_checks`` consecutive polls, then return its
    final value. Used instead of "stop polling the instant the count
    first reaches N" -- checking too early cannot distinguish "reached N
    and stopped" from "reached N and kept going a moment later", which is
    exactly the false-pass shape a runaway-loop bug produces.
    """
    deadline = time.monotonic() + timeout
    last = predicate()
    stable = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        current = predicate()
        if current == last:
            stable += 1
            if stable >= stable_checks:
                return current
        else:
            stable = 0
            last = current
    raise TimeoutError(f"value never stabilized within {timeout}s (last={last!r})")


@pytest.fixture
def FakeProcess():
    return _FakeProcess


@pytest.fixture
def FakeCooperativeProcess():
    return _FakeCooperativeProcess


@pytest.fixture
def wait_until():
    return _wait_until


@pytest.fixture
def settle():
    return _settle


@pytest.fixture
def mock_cloudflared_binary(monkeypatch):
    """Make the per-attempt "fetch/verify the binary" step of
    ``TunnelManager`` always succeed with a fake path, so a test can
    isolate the ``spawn_and_discover_url`` seam it actually wants to
    exercise. Discovered empirically (running these tests against the
    real ``TunnelManager``): each attempt calls
    ``cloudflared_fetch.ensure_cloudflared()`` *before* the spawn seam,
    so patching the seam alone is not enough -- the binary-fetch step
    must also resolve, or every attempt fails at that earlier step and
    the seam is never reached at all. Tests for cloudflared_fetch's own
    failure modes (checksum mismatch, offline, unsupported platform)
    deliberately do NOT use this fixture.
    """
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
        AsyncMock(return_value="/fake/cache/cloudflared"),
    )


class UnexpectedSubprocessSpawn(AssertionError):
    """A test let a real ``asyncio.create_subprocess_exec`` call through."""


class UnexpectedNetworkCall(AssertionError):
    """A test let a real ``urllib.request.urlopen`` call through."""


@dataclass
class LeakGuard:
    subprocess_calls: list = field(default_factory=list)
    urlopen_calls: list = field(default_factory=list)


@pytest.fixture(autouse=True)
def _no_public_url_override_by_default(monkeypatch):
    """``aipager.config.MINIAPP_PUBLIC_URL`` is computed once, at process
    import time, from this machine's real
    ``~/.config/aipager/`` config -- which (per the orchestrator's own
    verification notes) currently holds a real, stale tunnel hostname.
    Discovered empirically: a first draft of these tests asserted
    ``resolve_public_url()`` returned a freshly-discovered fake URL and
    instead got back that real stale hostname, because the module-level
    constant was already baked in before any fixture ran and no test
    here had overridden it. ``tests/test_bot_app_command.py`` shows this
    is the established, expected pattern in this codebase (explicit
    ``monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", ...)`` per
    test) -- not something ``_isolate_home_paths`` covers, since that
    patches the *path* the value would be re-read from, not the
    already-computed value itself.

    Autouse to "no override" so every test in this package starts from
    the documented default precedence unless it explicitly opts into
    testing the override path itself (which then sets its own value,
    taking precedence over this fixture).
    """
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")


@pytest.fixture(autouse=True)
def leak_guard(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cloudflared-cache"
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(cache_dir))

    guard = LeakGuard()

    async def _raise_subprocess(*args, **kwargs):
        guard.subprocess_calls.append((args, kwargs))
        raise UnexpectedSubprocessSpawn(
            f"real asyncio.create_subprocess_exec reached with args={args!r} "
            "— fake aipager.miniapp.tunnel_manager.spawn_and_discover_url instead"
        )

    def _raise_urlopen(*args, **kwargs):
        guard.urlopen_calls.append((args, kwargs))
        raise UnexpectedNetworkCall(
            f"real urllib.request.urlopen reached with args={args!r} — fake "
            "it explicitly in the test if this call is expected"
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_subprocess)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_urlopen)

    return guard
