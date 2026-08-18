"""Spawn, supervise and cleanly kill the managed cloudflared quick tunnel.

Deliberately separate from ``tunnel.py``: this module owns *process
management* (spawn, discover the assigned URL, restart on death, kill on
shutdown); ``tunnel.py`` owns *URL resolution* (what is the current
answer to "where does the Mini App live", including the managed slot
this module writes to via ``set_managed_tunnel_url``).

**The supervision loop has a hard, non-resetting lifetime ceiling on
restart attempts** (``TUNNEL_RESTART_MAX_ATTEMPTS``, default 8). Every
trip through the loop — whether the failure was a binary-fetch problem,
a spawn/discovery failure, or the child simply exiting after running
fine for hours — increments the same counter, and the counter is never
reset back down on a subsequent success. This is deliberately simpler,
and strictly safer, than "N failures in a row": it gives a mechanically
provable bound on total spawn attempts for the process's lifetime —
exactly the property that matters after two unrelated OOM incidents
this week were caused by unbounded loops — at the cost of a very
long-lived daemon eventually going quiet about the tunnel (Tailscale
auto-detect and the manual override both still work) until the next
``aipager start``. See design.md's "Alternatives considered" for why
reset-on-success was rejected.

**``--no-autoupdate`` is non-negotiable** in the spawned argv — without
it, cloudflared's own auto-update can silently swap the
freshly-verified binary for an unverified one at runtime, defeating
``cloudflared_fetch.py``'s whole checksum guarantee.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import platform
import re
import signal
from typing import Awaitable, Callable

from aipager.miniapp.tunnel import set_managed_tunnel_url

log = logging.getLogger(__name__)

# cloudflared writes ALL of its log output — including the quick-tunnel
# banner carrying the assigned hostname — to stderr, not stdout
# (empirically confirmed against the real 2026.8.2 binary; see
# design.md's ORCHESTRATOR VERIFICATION section). The banner is box-art,
# not a bare URL:
#
#   2026-08-18T18:59:25Z INF |  https://yes-consolidation-math-shorter.trycloudflare.com   |
#
# so matching is a regex against the hostname pattern only — a
# line-equality or startswith check would never match.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelLaunchError(Exception):
    """Raised by :func:`spawn_and_discover_url` when cloudflared could
    not be started, exited before printing a URL, or none appeared
    within ``TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS``. :class:`TunnelManager`
    treats every instance uniformly as one failed attempt toward the
    restart ceiling — assert on the type, never on message text."""


def _resolve_prctl():
    """Bind libc's ``prctl`` ONCE, at import time, in the parent.

    Deliberately not done inside the child: ``ctypes.CDLL()`` performs a
    ``dlopen`` and a non-trivial amount of Python work, and ``preexec_fn``
    runs after ``fork()`` in a child that has only the forking thread. If
    any other thread held the dynamic-loader lock at the moment of the
    fork, that ``dlopen`` can deadlock the child forever — and this daemon
    genuinely is multi-threaded in the relevant sense: it uses
    ``run_in_executor`` for the Tailscale probe and for the cloudflared
    download. Resolving here leaves the child doing a single already-bound
    C call, which is the only shape that is safe post-fork.
    """
    if platform.system() != "Linux":
        return None
    try:
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _resolve_prctl()


def _set_pdeathsig() -> None:
    """``preexec_fn`` for the spawned child: ask the kernel to deliver
    SIGTERM to it if this process dies without ``stop()`` ever running
    (most plausibly a ``kill -9`` of the daemon). Linux-only defense in
    depth — NOT a substitute for explicit cleanup, only a belt for the
    one case explicit cleanup structurally cannot reach. Argument-free
    by design: ``preexec_fn`` is called with no arguments in the child,
    after ``fork()`` and before ``exec()``.

    The body is one pre-bound call; see :func:`_resolve_prctl`.
    """
    try:
        _PRCTL(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG
    except Exception:
        pass  # best-effort only; explicit stop() is the real guarantee


# None when prctl could not be bound (every non-Linux platform, or a libc
# without it), which asyncio/subprocess treat identically to "no
# preexec_fn at all" — safe to pass unconditionally.
_PREEXEC_FN = _set_pdeathsig if _PRCTL is not None else None


async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
    """Keep reading (and discarding, at DEBUG) stderr for the rest of
    the child's life once its URL has been found, so the pipe never
    fills and blocks cloudflared."""
    if proc.stderr is None:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            log.debug("cloudflared: %s", line.decode(errors="replace").rstrip())
    except (asyncio.CancelledError, ValueError):
        return


async def spawn_and_discover_url(
    binary: str, port: int,
) -> tuple[asyncio.subprocess.Process, str]:
    """Spawn ``cloudflared tunnel --url http://127.0.0.1:<port>
    --no-autoupdate`` and read its assigned
    ``https://*.trycloudflare.com`` hostname from stderr, line by line.

    Returns ``(process, url)`` on the first match, with the child still
    running and a background task quietly draining stderr for the rest
    of its life. On a spawn failure, an early exit, or no URL within
    ``TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS``, the child is killed (if
    still alive) before raising :class:`TunnelLaunchError` — never left
    running unaccounted for.
    """
    from aipager.config import TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "tunnel", "--url", f"http://127.0.0.1:{port}",
            "--no-autoupdate",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_PREEXEC_FN,
        )
    except OSError as exc:
        raise TunnelLaunchError(f"could not start cloudflared: {exc}") from exc

    async def _read_until_url() -> str:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                raise TunnelLaunchError(
                    "cloudflared exited before printing a tunnel URL",
                )
            match = _URL_RE.search(line.decode(errors="replace"))
            if match:
                return match.group(0)

    try:
        url = await asyncio.wait_for(
            _read_until_url(), timeout=TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        # CancelledError is a BaseException, NOT an Exception, so the
        # handler below does not catch it. Without this branch, cancelling
        # the supervision task while it waits here — the whole ~7-20s
        # discovery window, on every spawn AND every restart — leaves
        # cloudflared running with nobody holding a handle to it:
        # TunnelManager._proc is only assigned after this function
        # returns, so stop() has nothing to terminate. That is a leaked
        # public tunnel surviving daemon shutdown.
        #
        # kill() without awaiting: we are already being cancelled, so
        # awaiting here risks hanging the shutdown path. asyncio's child
        # watcher reaps it.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        raise
    except (asyncio.TimeoutError, TunnelLaunchError) as exc:
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        if isinstance(exc, TunnelLaunchError):
            raise
        raise TunnelLaunchError(
            f"no tunnel URL within {TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS}s",
        ) from exc

    asyncio.create_task(_drain_stderr(proc))
    return proc, url


def _backoff_seconds(attempt: int) -> float:
    """Backoff before the ``attempt``'th (1-indexed) restart —
    ``min(BASE * 2**(attempt-1), MAX)``. Pure: no clock, no I/O, so it
    is unit-tested directly. Tests make backoff instant by
    monkeypatching ``TUNNEL_RESTART_BACKOFF_BASE_SECONDS`` /
    ``_MAX_SECONDS`` on ``aipager.config`` — never by patching
    ``asyncio.sleep``, which would unpace every other loop sharing this
    process's event loop (see config.py's own warning; this exact
    mistake OOM-killed the machine twice already).
    """
    from aipager.config import (
        TUNNEL_RESTART_BACKOFF_BASE_SECONDS,
        TUNNEL_RESTART_BACKOFF_MAX_SECONDS,
    )
    return min(
        TUNNEL_RESTART_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
        TUNNEL_RESTART_BACKOFF_MAX_SECONDS,
    )


class TunnelManager:
    """Supervises one managed cloudflared tunnel for the life of the
    daemon process.

    ``on_url_change(url)`` is awaited every time the discovered URL
    differs from the previous one — including transitions to/from
    ``""`` — and is expected to never raise (the real wiring is
    ``bot.publish_miniapp_button``, which already promises that).
    """

    def __init__(
        self, port: int, on_url_change: Callable[[str], Awaitable[None]],
    ) -> None:
        self._port = port
        self._on_url_change = on_url_change
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._attempts = 0
        self.current_url: str = ""

    async def start(self) -> None:
        """Begin supervision as a background task and return
        immediately — never awaits the first URL. Calling this twice on
        one instance is unsupported; construct one instance per run."""
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop supervision and terminate any live child
        (SIGTERM, escalating to SIGKILL after
        ``TUNNEL_KILL_TIMEOUT_SECONDS``). Idempotent: safe on an
        instance never started, whose child already exited, or on which
        ``stop()`` already ran.

        Deliberately does NOT await ``on_url_change("")`` — that is a
        Telegram API call, and by the time the daemon calls this it is
        already on its way out (``miniapp_server.stop()`` and
        ``bot.stop()`` follow immediately after in ``cli/daemon.py``).
        Process cleanup is the guarantee ``stop()`` makes; the in-memory
        slot is still cleared here so nothing stale survives it.
        """
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._kill_proc()
        self.current_url = ""
        set_managed_tunnel_url("")

    async def _kill_proc(self) -> None:
        from aipager.config import TUNNEL_KILL_TIMEOUT_SECONDS

        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=TUNNEL_KILL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

    async def _set_url(self, url: str) -> None:
        if url == self.current_url:
            return
        self.current_url = url
        set_managed_tunnel_url(url)
        await self._on_url_change(url)

    async def _run_loop(self) -> None:
        from aipager.config import TUNNEL_RESTART_MAX_ATTEMPTS
        from aipager.miniapp.cloudflared_fetch import ensure_cloudflared

        while self._attempts < TUNNEL_RESTART_MAX_ATTEMPTS:
            try:
                binary = await ensure_cloudflared()
                if not binary:
                    raise TunnelLaunchError("cloudflared binary unavailable")
                proc, url = await spawn_and_discover_url(binary, self._port)
                # Deliberately NOT cleared in a `finally` here: this is
                # the one window (awaiting a live child) where `stop()`
                # needs to find a real process to terminate. If this
                # `await proc.wait()` is cancelled by `stop()`, control
                # never reaches the lines below — `stop()`'s own
                # `_kill_proc()` is what acts on `self._proc` in that
                # case. Clearing it here in a `finally` would race
                # `stop()` and leak the child (verified the hard way;
                # see implementation.md).
                self._proc = proc
                await self._set_url(url)
                log.info("cloudflared: tunnel URL is %s", url)
                returncode = await proc.wait()
                log.warning(
                    "cloudflared exited (code=%s) — restarting", returncode,
                )
            except TunnelLaunchError as exc:
                log.warning("cloudflared: launch attempt failed: %s", exc)

            await self._set_url("")
            self._attempts += 1
            if self._attempts >= TUNNEL_RESTART_MAX_ATTEMPTS:
                break
            await asyncio.sleep(_backoff_seconds(self._attempts))

        log.warning(
            "cloudflared: giving up after %d failed attempts — the managed "
            "Mini App tunnel will not be retried for the rest of this run",
            self._attempts,
        )


__all__ = ["TunnelManager", "TunnelLaunchError", "spawn_and_discover_url"]
