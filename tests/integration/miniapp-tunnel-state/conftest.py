"""Black-box doubles for the Mini App tunnel state tests.

Written from spec.md, design.md (Success criteria, Guard list) and
entrypoints.md only. Everything here drives the PUBLIC surface:
``TunnelManager(port, on_url_change)``, ``tunnel_state``'s exported
functions, ``resolve_public_url()``, the bot's ``/app`` handler and
``publish_managed_miniapp_button``, and ``doctor.check_miniapp()``. The
only things replaced are the seams entrypoints.md lists
(``spawn_and_discover_url``, ``cloudflared_fetch.ensure_cloudflared``,
``tunnel.probe_public_url``, ``self_restart._popen``/``_kill``,
``tunnel_manager._cmdline``/``_boot_id``/``_monotonic``/
``_SLOW_FLOOR_SECONDS``, ``tunnel_state._daemon_alive``) and config values.

No real cloudflared, network or Telegram: the package-wide guard below
refuses every subprocess spawn and every ``urlopen`` the way the sibling
``manage-miniapp-tunnel`` package does, and the root conftest keeps its own
guards (spawn, network, real-home writes) in force.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.conftest import forbid_every_spawn

# This package's directory has a hyphen (the /ship slug), so its test
# modules cannot import this file by a dotted name; they import the
# helpers below as ``bb`` through this alias instead.
sys.modules.setdefault("miniapp_tunnel_state_bb", sys.modules[__name__])

#: The incident's exact error (README of the 2026-10-25 incident).
DNS_INCOMPLETE_TEXT = (
    "expected at least 2 Cloudflare Regions regions, but SRV only returned 1"
)
DNS_LOOKUP_TEXT = (
    "Could not lookup srv records on _v2-origintunneld._tcp.argotunnel.com: "
    "lookup _v2-origintunneld._tcp.argotunnel.com on 127.0.0.53:53: "
    "server misbehaving"
)
QUICK_TUNNEL_TEXT = (
    'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": '
    "dial tcp: lookup api.trycloudflare.com on 127.0.0.53:53: no such host"
)
EDGE_DIAL_TEXT = (
    "failed to dial to edge with quic: timeout: no recent network activity"
)


def log_line(message: str, error: str | None = None, level: str = "error") -> str:
    rec = {"level": level, "time": "2026-10-25T09:34:32Z", "message": message}
    if error is not None:
        rec["error"] = error
    return json.dumps(rec)


def cf_paths() -> tuple[Path, Path]:
    """Where cloudflared's --logfile and stderr file live: beside the
    tunnel record (entrypoints.md "Config paths"), read late."""
    from aipager import config
    record = Path(config.TUNNEL_STATE_FILE)
    return (record.with_name("aipager-cloudflared.log"),
            record.with_name("aipager-cloudflared.err"))


def write_cf_files(log_lines=(), err_text: str | None = None) -> tuple[Path, Path]:
    log, err = cf_paths()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"{line}\n" for line in log_lines))
    if err_text is not None:
        err.write_text(err_text)
    return log, err


class FakeProcess:
    """The ``proc`` half of ``spawn_and_discover_url``'s result:
    ``returncode``, async ``wait()``, ``terminate()``, ``kill()``.
    ``terminate()`` exits cleanly (-15) unless ``stubborn``."""

    def __init__(self, *, stubborn: bool = False):
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stubborn = stubborn
        self._exited = asyncio.Event()

    def die(self, code: int = 1) -> None:
        if self.returncode is None:
            self.returncode = code
        self._exited.set()

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self.stubborn:
            self.die(-15)

    def kill(self) -> None:
        self.kill_calls += 1
        self.die(-9)

    async def wait(self):
        await self._exited.wait()
        return self.returncode


async def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


async def settle(predicate, *, stable_checks: int = 10, interval: float = 0.02,
                 timeout: float = 6.0):
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


async def forever():
    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def _package_guards(tmp_path, monkeypatch):
    """No real spawn, no real download, no public-URL override, no
    Tailscale; fast but literal timing values (never derived from the
    constants under test)."""
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(tmp_path / "cf-cache"))

    async def _raise_subprocess(*args, **kwargs):
        raise AssertionError(f"a real subprocess spawn reached: {args!r}")

    def _raise_urlopen(*args, **kwargs):
        raise AssertionError(f"a real urlopen reached: {args!r}")

    forbid_every_spawn(monkeypatch, _raise_subprocess)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_urlopen)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.02)
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.2)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 3)
    monkeypatch.setattr("aipager.config.MINIAPP_KEEP_TUNNEL", False, raising=False)
    # Sanity: the root conftest must have moved the tunnel files under tmp.
    from aipager import config
    assert str(config.TUNNEL_STATE_FILE).startswith(str(tmp_path)), config.TUNNEL_STATE_FILE
    assert str(config.TUNNEL_STATUS_FILE).startswith(str(tmp_path)), config.TUNNEL_STATUS_FILE


@pytest.fixture
def fake_binary(monkeypatch):
    monkeypatch.setattr(
        "aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
        AsyncMock(return_value="/fake/cache/cloudflared"),
    )


class Seam:
    """A scripted ``spawn_and_discover_url``: each call pops the next
    step. A step is an exception to raise, a ``(proc, url)`` tuple to
    return, or a callable returning either. Past the script it blocks
    forever (no churn). Records the call times."""

    def __init__(self, steps=()):
        self.steps = list(steps)
        self.calls = 0
        self.times: list[float] = []

    async def __call__(self, binary, port):
        self.calls += 1
        self.times.append(time.monotonic())
        if not self.steps:
            await forever()
        step = self.steps.pop(0)
        if callable(step) and not isinstance(step, BaseException):
            step = step()
        if isinstance(step, BaseException):
            raise step
        if asyncio.iscoroutine(step):
            return await step
        return step


@pytest.fixture
def seam(monkeypatch, fake_binary):
    import aipager.miniapp.tunnel_manager as tm

    s = Seam()
    monkeypatch.setattr(tm, "spawn_and_discover_url", s)
    return s


@pytest.fixture
def make_manager():
    """Build a TunnelManager whose published URLs are recorded."""
    def _mk(on_url_change=None):
        from aipager.miniapp.tunnel_manager import TunnelManager

        published: list[str] = []

        async def _record(url):
            published.append(url)

        mgr = TunnelManager(port=8765, on_url_change=on_url_change or _record)
        mgr.published = published  # test-side bookkeeping only
        return mgr
    return _mk


@pytest.fixture
def status_file_seams(monkeypatch):
    """Make ``read_status_file`` accept a file written by THIS process:
    the same boot, and its pid a live aipager (design.md point 3 names
    ``_daemon_alive``, ``_boot_id`` and ``_is_aipager`` as the seams
    tests replace)."""
    import aipager.miniapp.tunnel_manager as tm
    from aipager.miniapp import tunnel_state

    monkeypatch.setattr(tm, "_boot_id", lambda: "boot-test-1")
    monkeypatch.setattr(tunnel_state, "_daemon_alive", lambda pid: True)
    monkeypatch.setattr(tm, "_is_aipager", lambda pid: True)


def write_status(phase="retrying", reason="dns_incomplete", attempt=3,
                 next_try_at=None, since=None, *, pid=None, boot_id="boot-test-1",
                 v=1, raw=None) -> Path:
    """A status file as the daemon writes it (entrypoints.md "Side
    effects"), at ``config.TUNNEL_STATUS_FILE``."""
    from aipager import config
    path = Path(config.TUNNEL_STATUS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw)
    else:
        now = time.time()
        path.write_text(json.dumps({
            "v": v, "pid": os.getpid() if pid is None else pid, "boot_id": boot_id,
            "phase": phase, "reason": reason, "attempt": attempt,
            "next_try_at": now + 600 if next_try_at is None else next_try_at,
            "since": now - 30 if since is None else since,
        }))
    os.chmod(path, 0o600)
    return path
