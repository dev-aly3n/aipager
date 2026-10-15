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

**A planned restart keeps the address** (an update's restart, Restart
now, the in-place exec): cloudflared runs detached from the daemon, a
planned stop leaves it running and records it, and the next daemon
adopts it after checking it, so the Mini App, its pinned button and its
menu button keep working through the restart. See "keeping the address
across a restart" below.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Awaitable, Callable

from aipager.miniapp.tunnel import set_managed_tunnel_url

log = logging.getLogger(__name__)

# The address cloudflared assigns: one quick-tunnel hostname.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_HOST_RE = re.compile(r"[a-z0-9-]+\.trycloudflare\.com")

# How often discovery asks cloudflared's metrics server for its hostname.
_DISCOVERY_POLL_SECONDS = 0.5
# A response from the metrics server is a few dozen bytes; never read more.
_METRICS_MAX_BYTES = 8192
_METRICS_TIMEOUT_SECONDS = 2.0


class TunnelLaunchError(Exception):
    """Raised by :func:`spawn_and_discover_url` when cloudflared could
    not be started, exited before reporting a URL, or none appeared
    within ``TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS``. :class:`TunnelManager`
    treats every instance uniformly as one failed attempt toward the
    restart ceiling — assert on the type, never on message text."""


# ---- keeping the address across a restart ------------------------------
#
# cloudflared runs DETACHED from the daemon: its own session (Ctrl+C in
# the daemon's terminal or a tmux pane never reaches it), no death signal
# tied to this process, no pipe to it (a Go program writing to a broken
# pipe on stderr exits on SIGPIPE, and the in-place restart closes every
# pipe this process holds), stdout and stderr to /dev/null, warnings to a
# log file. Its address comes from its local metrics server
# (`GET /quicktunnel` -> {"hostname": ...}), which also lets the NEXT
# daemon check it.
#
# A planned restart (`TunnelManager.stop(keep=True)`) leaves it running
# and records it (pid, metrics port, forwarded port, binary, boot, a hash
# of its hostname: never the address) in TUNNEL_STATE_FILE. The next
# daemon adopts it only when every check passes (`_adopt_kept_tunnel`),
# so the Mini App keeps its address; otherwise it kills it (only once it
# is proven to be that cloudflared) and starts a fresh one, as before.
# A kept tunnel nobody adopts is killed by a detached guard after
# TUNNEL_HANDOVER_SECONDS (`_start_guard`). Every real signal goes through
# `self_restart._kill` and every spawn through `self_restart._popen`, the
# seams tests refuse unless they fake them.

_RECORD_VERSION = 1


def _record_path() -> Path:
    from aipager import config
    return Path(config.TUNNEL_STATE_FILE)


# Linux reads a command line from /proc for free; elsewhere it costs a `ps`.
_CHECK_IDENTITY_WHILE_RUNNING = sys.platform.startswith("linux")


def _host_digest(host: str) -> str:
    return hashlib.sha256(host.encode("utf-8")).hexdigest()


def _read_record() -> dict | None:
    """The saved record, or None when there is none or it is unusable."""
    try:
        raw = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("v") != _RECORD_VERSION:
        return None
    try:
        rec = {
            "pid": int(raw["pid"]),
            "metrics_port": int(raw["metrics_port"]),
            "port": int(raw["port"]),
            "binary": str(raw["binary"]),
            "boot_id": str(raw.get("boot_id") or ""),
            "host_sha256": str(raw["host_sha256"]),
            "owner": int(raw.get("owner") or 0),
            "handover_token": str(raw.get("handover_token") or ""),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if (rec["pid"] <= 1 or not 0 < rec["metrics_port"] < 65536
            or not 0 < rec["port"] < 65536 or not rec["binary"]):
        return None
    return rec


def _write_record(rec: dict) -> bool:
    """Write *rec* atomically, readable by this user only. True when it is
    on disk. Never raises (except the test guard's refusal of the
    operator's real home)."""
    from aipager._test_guard import check_write

    path = _record_path()
    check_write(path)
    data = {**rec, "v": _RECORD_VERSION}
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)                # also a stale .tmp a crash left
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            # One line, default separators: the guard looks for
            # `"handover_token": "<token>"` in it.
            fh.write(json.dumps(data, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        log.warning("cloudflared: could not save the tunnel record", exc_info=True)
        return False
    return True


def _delete_record(pid: int | None = None) -> None:
    """Remove the record (only when it names *pid*, if given). Never raises."""
    if pid is not None:
        rec = _read_record()
        if rec is not None and rec["pid"] != pid:
            return
    try:
        _record_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("cloudflared: could not remove the tunnel record", exc_info=True)


# Seams over the process table (tests replace these, never os itself).

def _boot_id() -> str:
    """This boot's id: a recorded pid from another boot means nothing."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=2, check=False)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _pid_alive(pid: int) -> bool:
    """Whether *pid* is a running process. A child of this process image
    that exited is reaped here, so it never lingers as a zombie that looks
    alive (cloudflared is our child, spawned without asyncio, and after the
    in-place restart still is)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        done, _status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass        # not our child: whoever adopted it reaps it
    return True


def _cmdline(pid: int) -> list[str] | None:
    """*pid*'s argv: exact from /proc on Linux, else ``ps`` split on spaces
    (``None`` when it cannot be read)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a] or None
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    return [line] if line else None


def _is_our_tunnel(pid: int, binary: str, port: int) -> bool:
    """Whether *pid* is cloudflared *binary* running our quick tunnel to
    *port* (never a reused pid of something else)."""
    argv = _cmdline(pid)
    if not argv:
        return False
    url = f"http://127.0.0.1:{port}"
    if len(argv) == 1:
        # `ps` on macOS: one line, the binary path may hold spaces.
        line = argv[0]
        if not line.startswith(binary + " "):
            return False
        rest = line[len(binary):].split()
    else:
        if argv[0] != binary:
            return False
        rest = argv[1:]
    if "tunnel" not in rest:
        return False
    return any(rest[i] == "--url" and rest[i + 1] == url for i in range(len(rest) - 1))


def _is_aipager(pid: int) -> bool:
    """Whether *pid* runs aipager itself (``…/bin/aipager start``,
    ``python -m aipager``): a recorded owner whose pid was reused by
    something else, the hook (``aipager-hook``) included, is not a daemon
    holding the tunnel."""
    argv = _cmdline(pid) or []
    if len(argv) == 1:
        argv = argv[0].split()             # `ps` on macOS: one line
    # `.aipager-wrapped`: the Nix package's wrapper runs the real program.
    return any(os.path.basename(arg) in ("aipager", ".aipager-wrapped") for arg in argv)


def _signal_tunnel(pid: int, binary: str, port: int, sig: int) -> bool:
    """Send *sig* to *pid* only if it is proven to be our tunnel. True when sent."""
    from aipager import self_restart

    if not _is_our_tunnel(pid, binary, port):
        return False
    try:
        self_restart._kill(pid, sig)
    except ProcessLookupError:
        return False
    return True


def _free_port() -> int:
    """A free local TCP port for cloudflared's metrics server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _query_metrics(port: int, path: str) -> str | None:
    """``GET http://127.0.0.1:<port><path>`` from cloudflared's metrics
    server: the body of a 200 answer, else None. Bounded in time and size."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=_METRICS_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return None
    raw = b""
    try:
        writer.write(b"GET " + path.encode("ascii") + b" HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()

        async def _read_all() -> bytes:
            data = b""
            while len(data) <= _METRICS_MAX_BYTES:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                data += chunk
            return data

        raw = await asyncio.wait_for(_read_all(), timeout=_METRICS_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    head, _, body = raw.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].split()
    if len(status) < 2 or not status[0].startswith(b"HTTP/1.") or status[1] != b"200":
        return None
    return body.decode("utf-8", "replace")


async def _quick_tunnel_host(metrics_port: int) -> str:
    """The quick-tunnel hostname cloudflared reports, or "" (none yet, no
    answer, or not a trycloudflare.com host)."""
    body = await _query_metrics(metrics_port, "/quicktunnel")
    if not body:
        return ""
    try:
        host = json.loads(body).get("hostname")
    except (ValueError, AttributeError):
        return ""
    return host if isinstance(host, str) and _HOST_RE.fullmatch(host) else ""


class Tunnel:
    """One cloudflared quick tunnel this daemon holds, spawned by it or
    adopted from the daemon before it: the surface :class:`TunnelManager`
    supervises (``returncode``, ``wait()``, ``terminate()``, ``kill()``),
    by pid, because after a restart this process image cannot ``wait()``
    for it. Signals are sent only after the pid is proven to still be it."""

    def __init__(self, pid: int, metrics_port: int, binary: str, port: int,
                 host: str = "", popen=None) -> None:
        self.pid = pid
        self.metrics_port = metrics_port
        self.binary = binary
        self.port = port
        self.host = host
        # The spawned Popen, kept only so it is not collected while running.
        self._popen = popen
        self.returncode: int | None = None
        self._stopping = False

    async def wait(self) -> int:
        from aipager.config import TUNNEL_CHECK_SECONDS

        while self.returncode is None:
            # On Linux also that it still is our cloudflared (one /proc read):
            # a zombie nobody reaps has no command line.
            if not _pid_alive(self.pid) or (
                    _CHECK_IDENTITY_WHILE_RUNNING
                    and not _is_our_tunnel(self.pid, self.binary, self.port)):
                self.returncode = -1
                break
            await asyncio.sleep(0.1 if self._stopping else TUNNEL_CHECK_SECONDS)
        return self.returncode

    def terminate(self) -> None:
        self._stopping = True
        if not _signal_tunnel(self.pid, self.binary, self.port, signal.SIGTERM):
            raise ProcessLookupError(self.pid)

    def kill(self) -> None:
        self._stopping = True
        if not _signal_tunnel(self.pid, self.binary, self.port, signal.SIGKILL):
            raise ProcessLookupError(self.pid)

    def record(self, *, owner: int, handover_token: str = "") -> dict:
        return {"pid": self.pid, "metrics_port": self.metrics_port, "port": self.port,
                "binary": self.binary, "boot_id": _boot_id(),
                "host_sha256": _host_digest(self.host), "owner": owner,
                "handover_token": handover_token}


async def spawn_and_discover_url(binary: str, port: int) -> tuple[Tunnel, str]:
    """Spawn ``cloudflared tunnel --url http://127.0.0.1:<port>
    --no-autoupdate`` detached, with its metrics server on a free local
    port, and ask that server for the assigned ``*.trycloudflare.com``
    hostname until it has one.

    Returns ``(tunnel, url)`` with cloudflared still running. On a spawn
    failure, an early exit, or no URL within
    ``TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS``, cloudflared is killed (if
    still alive) before raising :class:`TunnelLaunchError`: never left
    running unaccounted for.
    """
    from aipager import self_restart
    from aipager.config import TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS

    metrics_port = _free_port()
    argv = [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate",
            "--metrics", f"127.0.0.1:{metrics_port}", "--loglevel", "warn"]
    from aipager._test_guard import check_write

    log_path = _record_path().with_name("aipager-cloudflared.log")
    check_write(log_path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")           # one log per tunnel
        argv += ["--logfile", str(log_path)]
    except OSError:
        pass
    try:
        popen = self_restart._popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as exc:
        raise TunnelLaunchError(f"could not start cloudflared: {exc}") from exc
    tunnel = Tunnel(popen.pid, metrics_port, binary, port, popen=popen)

    async def _until_host() -> str:
        while True:
            if not _pid_alive(tunnel.pid):
                raise TunnelLaunchError("cloudflared exited before reporting a tunnel URL")
            host = await _quick_tunnel_host(metrics_port)
            if host:
                return host
            await asyncio.sleep(_DISCOVERY_POLL_SECONDS)

    try:
        host = await asyncio.wait_for(_until_host(), timeout=TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # CancelledError is a BaseException, NOT an Exception: without this
        # branch, cancelling the supervision task during discovery would
        # leave cloudflared running with nobody holding it (a leaked public
        # tunnel surviving the daemon).
        _kill_quietly(tunnel)
        raise
    except (asyncio.TimeoutError, TunnelLaunchError) as exc:
        _kill_quietly(tunnel)
        if isinstance(exc, TunnelLaunchError):
            raise
        raise TunnelLaunchError(
            f"no tunnel URL within {TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS}s",
        ) from exc
    tunnel.host = host
    return tunnel, f"https://{host}"


def _kill_quietly(tunnel: Tunnel) -> None:
    try:
        tunnel.kill()
    except (ProcessLookupError, PermissionError):
        pass


async def _stop_tunnel(tunnel: Tunnel) -> None:
    """SIGTERM, then SIGKILL after ``TUNNEL_KILL_TIMEOUT_SECONDS``: a
    running tunnel that was proven ours and is not wanted."""
    from aipager.config import TUNNEL_KILL_TIMEOUT_SECONDS

    try:
        tunnel.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(tunnel.wait(), timeout=TUNNEL_KILL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _kill_quietly(tunnel)


async def _adopt_kept_tunnel(port: int) -> tuple[Tunnel, str] | None:
    """Take over the tunnel the previous daemon kept (or left behind when
    it died), when EVERY check passes: same boot, same Mini App port, the
    pid alive and proven to be that cloudflared, its metrics server naming
    the recorded hostname, and the address answering from outside.
    Otherwise kill it once it is proven ours, forget the record, and
    return None (the caller starts a fresh tunnel). Never raises."""
    from aipager.miniapp.tunnel import probe_public_url

    try:
        rec = _read_record()
        if rec is None:
            if _record_path().exists():
                _delete_record()
            return None
        pid = rec["pid"]
        owner = rec["owner"]
        if owner and owner != os.getpid() and _pid_alive(owner) and _is_aipager(owner):
            # Held by a daemon that is still running: never ours to touch.
            return None
        if rec["boot_id"] != _boot_id():
            _delete_record()
            return None
        # A pid that is gone (or a zombie) has no command line to match.
        if not _is_our_tunnel(pid, rec["binary"], rec["port"]):
            _delete_record()
            return None
        tunnel = Tunnel(pid, rec["metrics_port"], rec["binary"], rec["port"])
        host = ""
        refused = True
        if rec["port"] != port:
            log.info("cloudflared: the kept tunnel forwards to another port - not taking it over")
        else:
            host = await _quick_tunnel_host(rec["metrics_port"])
            if not host or _host_digest(host) != rec["host_sha256"]:
                log.info("cloudflared: the kept tunnel does not report the same address - "
                         "not taking it over")
            elif not await probe_public_url(f"https://{host}"):
                log.info("cloudflared: the kept tunnel's address does not answer - "
                         "not taking it over")
            else:
                refused = False
        if refused:
            await _stop_tunnel(tunnel)
            _delete_record()
            return None
        tunnel.host = host
        if not _write_record(tunnel.record(owner=os.getpid())):
            # Unclaimed, its guard would stop it within minutes anyway.
            log.info("cloudflared: could not claim the kept tunnel - starting a new one")
            await _stop_tunnel(tunnel)
            return None
        return tunnel, f"https://{host}"
    except Exception:
        log.warning("cloudflared: checking the kept tunnel failed - starting a new one",
                    exc_info=True)
        return None


# The guard a kept tunnel gets: after the handover window, if no daemon
# claimed it (the record still carries this keep's token) and the pid is
# still that cloudflared, kill it and drop the record. Arguments come in
# as argv ($1 pid, $2 record, $3 token, $4 seconds, $5 binary, $6 url),
# never interpolated into the script. Bytes: shell code, not text anyone
# reads (argv takes bytes as they are).
_GUARD_SCRIPT = rb"""(
sleep "$4"
grep -q "\"handover_token\": \"$3\"" "$2" 2>/dev/null || exit 0
cmd=$(tr '\000' ' ' < "/proc/$1/cmdline" 2>/dev/null) || cmd=$(ps -ww -o command= -p "$1" 2>/dev/null)
case "$cmd" in
  "$5 tunnel --url $6 "*) kill "$1" 2>/dev/null; rm -f "$2" ;;
esac
) </dev/null >/dev/null 2>&1 &
"""


def _start_guard(tunnel: Tunnel, token: str) -> bool:
    """Spawn the detached guard for a kept tunnel. True when it started."""
    from aipager import self_restart
    from aipager.config import TUNNEL_HANDOVER_SECONDS

    argv = ["sh", "-c", _GUARD_SCRIPT, "aipager-tunnel-guard", str(tunnel.pid),
            str(_record_path()), token, str(int(TUNNEL_HANDOVER_SECONDS)), tunnel.binary,
            f"http://127.0.0.1:{tunnel.port}"]
    try:
        outer = self_restart._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True,
                                    close_fds=True)
        # The script backgrounds its body and exits at once: reap it now,
        # so no zombie outlives this process image (the in-place restart).
        outer.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        log.warning("cloudflared: could not start the kept tunnel's guard", exc_info=True)
        return False
    return True


def has_kept_tunnel() -> bool:
    """Whether a tunnel record of this boot exists (one the next start may
    take over): the daemon then leaves the menu button to the tunnel's own
    first answer instead of clearing it. Never raises."""
    try:
        rec = _read_record()
        return rec is not None and rec["boot_id"] == _boot_id()
    except Exception:
        return False


def kill_recorded_tunnel() -> bool:
    """Stop the tunnel the record names, if it is proven to be ours, and
    forget it (``aipager uninstall``). Never raises. True when one was
    stopped."""
    try:
        rec = _read_record()
        if rec is None:
            return False
        stopped = (_pid_alive(rec["pid"]) and _signal_tunnel(
            rec["pid"], rec["binary"], rec["port"], signal.SIGTERM))
        _delete_record()
        return bool(stopped)
    except Exception:
        log.warning("cloudflared: stopping the recorded tunnel failed", exc_info=True)
        return False


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
        # The live tunnel: a `Tunnel` in production (spawned or adopted),
        # anything with returncode/wait/terminate/kill in tests.
        self._proc = None
        self._attempts = 0
        self.current_url: str = ""
        # Whether the first answer (an address, or none) went out: the
        # daemon leaves it to this manager, so a stale button from the last
        # run is cleared even when the very first attempt fails.
        self._published = False

    async def start(self) -> None:
        """Begin supervision as a background task and return
        immediately — never awaits the first URL. Calling this twice on
        one instance is unsupported; construct one instance per run."""
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self, *, keep: bool = False) -> None:
        """Stop supervision and terminate any live child
        (SIGTERM, escalating to SIGKILL after
        ``TUNNEL_KILL_TIMEOUT_SECONDS``). Idempotent: safe on an
        instance never started, whose child already exited, or on which
        ``stop()`` already ran.

        ``keep`` (a planned restart, and ``MINIAPP_KEEP_TUNNEL`` on): a
        live tunnel that reported its address is left running for the
        next daemon to adopt, recorded with a handover token, and given
        its guard (:func:`_start_guard`). Anything else is killed, and
        its record forgotten.

        Deliberately does NOT await ``on_url_change("")`` — that is a
        Telegram API call, and by the time the daemon calls this it is
        already on its way out (``miniapp_server.stop()`` and
        ``bot.stop()`` follow immediately after in ``cli/daemon.py``).
        Process cleanup is the guarantee ``stop()`` makes; the in-memory
        slot is still cleared here so nothing stale survives it.
        """
        from aipager.config import MINIAPP_KEEP_TUNNEL

        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        proc = self._proc
        if (keep and MINIAPP_KEEP_TUNNEL and isinstance(proc, Tunnel) and proc.host
                and proc.returncode is None and _pid_alive(proc.pid)):
            token = secrets.token_hex(8)
            if _write_record(proc.record(owner=0, handover_token=token)) and \
                    _start_guard(proc, token):
                self._proc = None
                log.info("cloudflared: tunnel kept for the restart - the next daemon "
                         "takes it over and the Mini App keeps its address")
            else:
                # No record or no guard: nothing would stop it if no daemon
                # comes back. Stopped like any other stop.
                keep = False
        else:
            keep = False
        if not keep:
            await self._kill_proc()
            if isinstance(proc, Tunnel):
                _delete_record(proc.pid)
        self.current_url = ""
        set_managed_tunnel_url("")

    async def _kill_proc(self) -> None:
        from aipager.config import TUNNEL_KILL_TIMEOUT_SECONDS

        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=TUNNEL_KILL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                # Bounded too: a shutdown never waits forever on a process
                # it cannot reap (one adopted from the last daemon is not
                # this image's child).
                await asyncio.wait_for(proc.wait(), timeout=TUNNEL_KILL_TIMEOUT_SECONDS)
            except (ProcessLookupError, PermissionError, asyncio.TimeoutError):
                pass

    async def _set_url(self, url: str) -> None:
        if url == self.current_url and self._published:
            return
        self._published = True
        self.current_url = url
        set_managed_tunnel_url(url)
        await self._on_url_change(url)

    async def _run_loop(self) -> None:
        from aipager.config import TUNNEL_RESTART_MAX_ATTEMPTS
        from aipager.miniapp.cloudflared_fetch import ensure_cloudflared

        # The first attempt takes over the tunnel a planned restart kept
        # (same address); every other one starts a new tunnel.
        adopt = True
        while self._attempts < TUNNEL_RESTART_MAX_ATTEMPTS:
            try:
                kept = await _adopt_kept_tunnel(self._port) if adopt else None
                adopt = False
                if kept is not None:
                    proc, url = kept
                    log.info("cloudflared: took over the tunnel the last run kept")
                else:
                    binary = await ensure_cloudflared()
                    if not binary:
                        raise TunnelLaunchError("cloudflared binary unavailable")
                    proc, url = await spawn_and_discover_url(binary, self._port)
                    if isinstance(proc, Tunnel):
                        _write_record(proc.record(owner=os.getpid()))
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
