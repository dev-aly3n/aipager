"""Spawn, supervise and cleanly kill the managed cloudflared quick tunnel.

Deliberately separate from ``tunnel.py``: this module owns *process
management* (spawn, discover the assigned URL, restart on death, kill on
shutdown); ``tunnel.py`` owns *URL resolution* (what is the current
answer to "where does the Mini App live", including the managed slot
this module writes to via ``set_managed_tunnel_url``).

**The supervision loop never gives up, and its rate is bounded.** After
``TUNNEL_RESTART_MAX_ATTEMPTS`` fast attempts (with backoff), it tries
once every ``TUNNEL_RETRY_SLOW_SECONDS`` (never under 60 s) for the rest
of the run, so a machine fixed hours later (its DNS, its firewall) brings
the Mini App back without a restart. Every trip through the loop counts
(a binary-fetch problem, a spawn or discovery failure, a link that never
answers, the child exiting), and the fast budget is refreshed only after
the tunnel has worked for a whole slow interval: a tunnel that dies right
after starting never earns a fast spin. That keeps the property that
mattered after two unrelated OOM incidents caused by unbounded loops: at
most ``MAX`` fast attempts per refresh, one slow try per slow interval,
and one /app-requested try a minute (``tunnel_state.request_attempt``).

**cloudflared is watched the whole time it is held**, while its link is
probed and published too: one exit reaps it within
``TUNNEL_CHECK_SECONDS``, logs its real exit status, takes the address
out of the managed slot at once, and starts the backoff. The managed slot
(``tunnel.set_managed_tunnel_url``) holds an address only once its probe
passed. The state, with the reason when it is not working, is kept in
:mod:`aipager.miniapp.tunnel_state` for /app and ``aipager doctor``.

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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aipager.miniapp import tunnel_state
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
    treats every instance uniformly as one failed attempt - assert on
    the type, never on message text. ``exit_code`` is cloudflared's exit
    status when it exited; ``reason`` a ``tunnel_state`` reason id when
    the raiser already knows it (else the manager reads cloudflared's
    files)."""

    def __init__(self, message: str = "", *, exit_code: int | None = None,
                 reason: str | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.reason = reason


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


def _log_paths() -> tuple[Path, Path]:
    """cloudflared's ``--logfile`` and its stderr file, beside the record."""
    record = _record_path()
    return (record.with_name("aipager-cloudflared.log"),
            record.with_name("aipager-cloudflared.err"))


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
        done, status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            # Kept for Tunnel.poll(): the exit status of a kept tunnel
            # that is still this image's child (the in-place restart).
            try:
                _reaped_codes[pid] = os.waitstatus_to_exitcode(status)
            except ValueError:
                pass
            return False
    except ChildProcessError:
        pass        # not our child: whoever adopted it reaps it
    return True


# Exit statuses `_pid_alive` reaped, by pid, until `Tunnel.poll()` takes
# them: only children of this process image ever land here.
_reaped_codes: dict[int, int] = {}


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
        # The spawned Popen: the one place this pid's real exit status can
        # be read (nothing else may reap it first, or CPython reports 0).
        self._popen = popen
        self.returncode: int | None = None
        # False only when the exit status could not be read (an adopted
        # pid that is not this image's child): returncode is then -1.
        self.status_known = True
        self._stopping = False

    def poll(self) -> int | None:
        """None while running, else the exit status. A spawned tunnel's
        comes from its Popen (the real one, negative for a signal); an
        adopted child's from the status `_pid_alive` reaped; anything
        else is -1 with ``status_known`` False. Never blocks."""
        if self.returncode is not None:
            return self.returncode
        if self._popen is not None:
            code = self._popen.poll()
            if code is not None:
                self.returncode = code
            return code
        if _pid_alive(self.pid):
            return None
        code = _reaped_codes.pop(self.pid, None)
        if code is None:
            self.status_known = False
            code = -1
        self.returncode = code
        return code

    async def wait(self) -> int:
        from aipager.config import TUNNEL_CHECK_SECONDS

        while self.returncode is None:
            if self.poll() is not None:
                break
            # On Linux also that it still is our cloudflared (one /proc read):
            # a zombie nobody reaps has no command line. Only after poll()
            # said it runs, so a child we can reap is never reported as -1.
            if _CHECK_IDENTITY_WHILE_RUNNING and not _is_our_tunnel(
                    self.pid, self.binary, self.port):
                self.status_known = False
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

    log_path, err_path = _log_paths()
    check_write(log_path)
    check_write(err_path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")           # one log per tunnel
        argv += ["--logfile", str(log_path)]
    except OSError:
        pass
    # Its stderr goes to a regular file, emptied per tunnel and readable
    # by this user only: the quick-tunnel request's failure is printed
    # there and nowhere else. A file, never a pipe (see above).
    err_fd: int | None = None
    try:
        err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(err_fd, 0o600)
    except OSError:
        if err_fd is not None:
            os.close(err_fd)
        err_fd = None
    try:
        popen = self_restart._popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=err_fd if err_fd is not None else subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except OSError as exc:
        raise TunnelLaunchError(f"could not start cloudflared: {exc}",
                                reason=tunnel_state.OTHER) from exc
    finally:
        if err_fd is not None:
            os.close(err_fd)
    tunnel = Tunnel(popen.pid, metrics_port, binary, port, popen=popen)

    async def _until_host() -> str:
        while True:
            code = tunnel.poll()
            if code is not None:
                raise TunnelLaunchError("cloudflared exited before reporting a tunnel URL",
                                        exit_code=code)
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
        # And reaped: a planned restart is an in-place execve, so a child
        # left unreaped here is a zombie of the next daemon for its whole
        # life. Milliseconds after a SIGKILL, so stop() stays prompt.
        try:
            await _reap_killed(tunnel)
        except asyncio.CancelledError:
            # Cancelled again while reaping: one last look that never
            # waits, then the original cancellation goes on.
            tunnel.poll()
        raise
    except (asyncio.TimeoutError, TunnelLaunchError) as exc:
        _kill_quietly(tunnel)
        # Reaped here, not by the next spawn: in the slow phase that is
        # minutes away, and a zombie cloudflared would wait for it.
        await _reap_killed(tunnel)
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


# How often _reap_killed looks whether a killed child has exited.
_REAP_POLL_SECONDS = 0.05


async def _reap_killed(tunnel: Tunnel) -> None:
    """After a SIGKILL, reap the spawned child: poll it (never a blocking
    wait on the event loop) for at most ``TUNNEL_KILL_TIMEOUT_SECONDS``.
    A tunnel with no Popen (adopted) is not ours to reap."""
    from aipager.config import TUNNEL_KILL_TIMEOUT_SECONDS

    if tunnel._popen is None:
        return
    deadline = time.monotonic() + TUNNEL_KILL_TIMEOUT_SECONDS
    while tunnel.poll() is None and time.monotonic() < deadline:
        await asyncio.sleep(_REAP_POLL_SECONDS)


async def _quiet_probe(url: str) -> bool:
    """``probe_public_url`` for a tunnel this module holds, its per-check
    lines at INFO: the manager's own line says whether the link answered,
    and in the slow phase it probes on every try (see
    ``tunnel.probe_log_level``; the level is reset before this returns,
    so it never reaches the caller's other work).

    Awaited straight from the caller's task, the level sits in that
    task's context until this returns, and any task created meanwhile
    copies it: no publish or bot-probe task may be started from inside
    this await (``_probe`` runs it as a task of its own; the adoption
    check awaits it with nothing else running)."""
    from aipager.miniapp import tunnel as tunnel_mod

    token = tunnel_mod.probe_log_level.set(logging.INFO)
    try:
        return await tunnel_mod.probe_public_url(url)
    finally:
        tunnel_mod.probe_log_level.reset(token)


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
            elif not await _quiet_probe(f"https://{host}"):
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


# The slow phase never tries more often than this, whatever
# TUNNEL_RETRY_SLOW_SECONDS says: a 0 there must never spin. Read at call
# time (a seam tests replace).
_SLOW_FLOOR_SECONDS = 60.0
# Nor less often than once a day (a huge value must not overflow a timer).
_SLOW_CEILING_SECONDS = 86400.0
# /app may start one attempt in the slow phase at most this often, for the
# whole daemon (not per chat).
_APP_ATTEMPT_MIN_SECONDS = 60.0
# stop() lets a menu-button publish in flight finish for at most this long
# before it cancels it: a cancelled publish can leave chats half updated.
_PUBLISH_SETTLE_SECONDS = 10.0


def _monotonic() -> float:
    """The clock the /app limit uses (a seam tests replace, never time)."""
    return time.monotonic()


def _slow_interval() -> float:
    """``TUNNEL_RETRY_SLOW_SECONDS``, never under ``_SLOW_FLOOR_SECONDS``."""
    from aipager.config import TUNNEL_RETRY_SLOW_SECONDS

    try:
        value = float(TUNNEL_RETRY_SLOW_SECONDS)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:                      # NaN
        value = 0.0
    return min(max(value, _SLOW_FLOOR_SECONDS), _SLOW_CEILING_SECONDS)


@dataclass
class _Verdict:
    """How one attempt ended, as ids (the log line is written from them):
    the reason id; ``kind`` (exited, launch_failed, never_answered,
    crashed); the stage it ended in (launch, probe, publish, run); the
    exit code ("unknown" when it cannot be read, "none" when it did not
    exit); cloudflared's own words or the error (log only); and how long
    it had worked."""

    reason: str
    kind: str
    stage: str
    code: str = "none"
    detail: str = ""
    worked_seconds: float = 0.0


class TunnelManager:
    """Supervises one managed cloudflared tunnel for the life of the
    daemon process, and never gives up on it (see the module docstring).

    ``on_url_change(url)`` is awaited every time the published URL
    changes - including transitions to/from ``""`` - and is expected to
    never raise (the real wiring is ``bot.publish_managed_miniapp_button``,
    which already promises that). It only ever gets a URL whose probe
    passed (or a kept tunnel's, which its adoption probed). It runs as
    its own task, so a cloudflared exit or a ``stop()`` never cancels a
    publish half way through.
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
        self.current_url: str = ""
        # Whether the first answer (an address, or none) went out: the
        # daemon leaves it to this manager, so a stale button from the last
        # run is cleared even when the very first attempt fails.
        self._published = False
        self._last_published = ""
        self._publish_task: asyncio.Task[None] | None = None
        self._status: tunnel_state.TunnelStatus | None = None
        self._wake: asyncio.Event | None = None
        self._waiting_slow = False
        self._last_app_attempt: float | None = None
        self._started = False

    async def start(self) -> None:
        """Begin supervision as a background task and return
        immediately - never awaits the first URL. Calling this twice on
        one instance is unsupported; construct one instance per run."""
        self._started = True
        self._wake = asyncio.Event()
        self._set_status(tunnel_state.STARTING, "", 0, 0.0)
        tunnel_state.set_attempt_hook(self._request_attempt)
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

        A menu-button publish in flight is let finish (at most
        ``_PUBLISH_SETTLE_SECONDS``), never cut half way. ``stop()`` still
        does NOT await ``on_url_change("")`` itself - that is a Telegram
        API call, and by the time the daemon calls this it is already on
        its way out (``miniapp_server.stop()`` and ``bot.stop()`` follow
        immediately after in ``cli/daemon.py``). Process cleanup is the
        guarantee ``stop()`` makes; the in-memory slot, the tunnel state
        and its file are still cleared here so nothing stale survives it.
        """
        from aipager.config import MINIAPP_KEEP_TUNNEL

        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._started:
            tunnel_state.set_attempt_hook(None)
        await self._settle_publish()
        proc = self._proc
        # Never one already being stopped (a failed probe's kill that this
        # stop() cut short): it is on its way out, not worth handing over.
        if (keep and MINIAPP_KEEP_TUNNEL and isinstance(proc, Tunnel) and proc.host
                and not proc._stopping and proc.poll() is None):
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
        if self._started:
            self._status = None
            tunnel_state.publish_status(None)
            tunnel_state.delete_status_file()

    async def _settle_publish(self) -> None:
        task = self._publish_task
        if task is None or task.done():
            return
        done, _pending = await asyncio.wait({task}, timeout=_PUBLISH_SETTLE_SECONDS)
        if not done:
            log.warning("Mini App button publish still running at shutdown - cancelling it")
            task.cancel()
            await asyncio.wait({task}, timeout=_PUBLISH_SETTLE_SECONDS)

    async def _kill_proc(self) -> None:
        from aipager.config import TUNNEL_KILL_TIMEOUT_SECONDS

        proc = self._proc
        if proc is None or proc.returncode is not None:
            self._proc = None
            return
        try:
            proc.terminate()
        except (ProcessLookupError, PermissionError):
            self._proc = None
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
        # Only once it is done: a kill cut short by stop() leaves the
        # process for stop()'s own _kill_proc to try again.
        if self._proc is proc:
            self._proc = None

    async def _call_on_url_change(self, url: str) -> None:
        try:
            await self._on_url_change(url)
        except Exception:
            log.warning("Mini App button publish failed", exc_info=True)

    def _start_publish(self, url: str) -> asyncio.Task[None]:
        self._published = True
        self._last_published = url
        task = asyncio.create_task(self._call_on_url_change(url))
        self._publish_task = task
        return task

    async def _set_url(self, url: str) -> None:
        """Put *url* in the managed slot and publish it, unless it is the
        value already published. The publish is its own task: cancelling
        the caller never cancels it (``stop()`` settles it)."""
        if url == self._last_published and self._published:
            return
        self.current_url = url
        set_managed_tunnel_url(url)
        prev = self._publish_task
        if prev is not None and not prev.done():
            # A publish still going out (its tunnel died meanwhile) is let
            # finish, never cut half way, and this one goes out behind it.
            await asyncio.wait({prev})
        await asyncio.wait({self._start_publish(url)})

    def _set_status(self, phase: str, reason: str, attempt: int, next_try_at: float) -> None:
        prev = self._status
        if prev is not None and (prev.phase, prev.reason, prev.attempt, prev.next_try_at) == (
                phase, reason, attempt, next_try_at):
            return
        since = prev.since if prev is not None and prev.phase == phase else time.time()
        status = tunnel_state.TunnelStatus(phase=phase, reason=reason, attempt=attempt,
                                           next_try_at=next_try_at, since=since)
        self._status = status
        tunnel_state.publish_status(status)
        tunnel_state.write_status_file(status)

    def _request_attempt(self) -> bool:
        """/app's "try now": only while the slow phase waits between tries
        (``_waiting_slow`` is True there and nowhere else), at most once
        per ``_APP_ATTEMPT_MIN_SECONDS`` for the whole daemon."""
        if not self._waiting_slow or self._wake is None:
            return False
        now = _monotonic()
        if (self._last_app_attempt is not None
                and now - self._last_app_attempt < _APP_ATTEMPT_MIN_SECONDS):
            return False
        self._last_app_attempt = now
        log.info("cloudflared: /app asked for a tunnel attempt - trying now")
        self._wake.set()
        return True

    async def _slow_wait(self, interval: float) -> None:
        assert self._wake is not None
        self._wake.clear()
        self._waiting_slow = True
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        finally:
            self._waiting_slow = False

    def _exit_verdict(self, proc, wait_task: asyncio.Task, worked: float,
                      stage: str) -> _Verdict:
        try:
            code = str(wait_task.result())
        except Exception:
            code = "unknown"
        if not getattr(proc, "status_known", True):
            # An adopted pid that is not this image's child: its status
            # cannot be read, and -1 is only aipager's own marker.
            log.info("cloudflared exited (status unknown: not this process's child)")
            code = "unknown"
        if self._proc is proc:
            self._proc = None
        log_path, err_path = _log_paths()
        return _Verdict(tunnel_state.classify(log_path, err_path), "exited", stage, code,
                        tunnel_state.raw_cause(log_path, err_path), worked)

    async def _attempt(self, adopt: bool, *, quiet: bool = False) -> _Verdict:
        """One try: launch (or adopt), probe while watching the process,
        publish while watching it, then hold it until it exits. Returns
        how it ended; never raises (except cancellation). ``quiet`` (the
        slow phase, already announced): the fetch's and a crash's lines
        are INFO; the loop's own line says the try failed."""
        from aipager.miniapp.cloudflared_fetch import ensure_cloudflared

        level = logging.INFO if quiet else logging.WARNING
        log_path, err_path = _log_paths()
        adopted = False
        try:
            kept = await _adopt_kept_tunnel(self._port) if adopt else None
            if kept is not None:
                proc, url = kept
                adopted = True
                log.info("cloudflared: took over the tunnel the last run kept")
            else:
                binary = await ensure_cloudflared(log_level=level)
                if not binary:
                    raise TunnelLaunchError("cloudflared binary unavailable",
                                            reason=tunnel_state.BINARY_MISSING)
                proc, url = await spawn_and_discover_url(binary, self._port)
                if isinstance(proc, Tunnel):
                    _write_record(proc.record(owner=os.getpid()))
        except TunnelLaunchError as exc:
            reason = exc.reason or tunnel_state.classify(log_path, err_path)
            raw = ("" if reason == tunnel_state.BINARY_MISSING
                   else tunnel_state.raw_cause(log_path, err_path))
            code = "none" if exc.exit_code is None else str(exc.exit_code)
            message = str(exc)
            if raw and raw in message:
                raw = ""                    # said once is enough
            # aipager's own words first, then cloudflared's.
            detail = "; ".join(t for t in (message, raw) if t)
            return _Verdict(reason, "launch_failed", "launch", code, detail)
        except Exception as exc:
            log.log(level, "cloudflared: launch attempt crashed", exc_info=True)
            return _Verdict(tunnel_state.OTHER, "crashed", "launch", detail=repr(exc))

        # Held from here: `stop()` finds it in self._proc whatever await
        # it cancels (never cleared in a `finally`, which would race it).
        self._proc = proc
        wait_task = asyncio.create_task(proc.wait())
        try:
            if not adopted:
                # Its adoption already probed a kept tunnel.
                verdict = await self._probe(proc, url, wait_task, log_path, err_path)
                if verdict is not None:
                    return verdict
            return await self._hold(proc, url, wait_task)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.wait({wait_task})

    async def _probe(self, proc, url: str, wait_task: asyncio.Task, log_path: Path,
                     err_path: Path) -> _Verdict | None:
        """Probe *url* while watching the process: None when it answered."""
        probe_task = asyncio.create_task(_quiet_probe(url))
        try:
            await asyncio.wait({probe_task, wait_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not probe_task.done():
                probe_task.cancel()
                await asyncio.wait({probe_task})
        if wait_task.done():
            return self._exit_verdict(proc, wait_task, 0.0, "probe")
        try:
            answered = bool(probe_task.result())
        except Exception:
            answered = False
        if answered:
            return None
        await self._kill_proc()
        if isinstance(proc, Tunnel):
            _delete_record(proc.pid)
        return _Verdict(tunnel_state.classify_unreachable(log_path), "never_answered", "probe",
                        detail=tunnel_state.raw_cause(log_path, err_path, unreachable=True))

    async def _hold(self, proc, url: str, wait_task: asyncio.Task) -> _Verdict:
        """Publish the verified *url* and hold the tunnel until it exits."""
        self.current_url = url
        set_managed_tunnel_url(url)
        self._set_status(tunnel_state.WORKING, "", 0, 0.0)
        started = _monotonic()
        log.info("cloudflared: tunnel URL is %s", url)
        publish = self._start_publish(url)
        await asyncio.wait({publish, wait_task}, return_when=asyncio.FIRST_COMPLETED)
        if wait_task.done() and not publish.done():
            # It died while its button was going out: no surface may hand
            # out the dead address from now on. The loop says it is not
            # working at once, then clears the button the publish leaves
            # once that has finished (never cut: see _set_url).
            self.current_url = ""
            set_managed_tunnel_url("")
            return self._exit_verdict(proc, wait_task, _monotonic() - started, "publish")
        await asyncio.wait({wait_task})
        self.current_url = ""
        set_managed_tunnel_url("")
        return self._exit_verdict(proc, wait_task, _monotonic() - started, "run")

    async def _run_loop(self) -> None:
        from aipager import config

        # The first attempt takes over the tunnel a planned restart kept
        # (same address); every other one starts a new tunnel.
        adopt = True
        budget_used = 0
        slow_announced = False
        while True:
            # Once the slow phase was announced, a try's own lines are INFO.
            verdict = await self._attempt(adopt, quiet=slow_announced)
            adopt = False
            if verdict.worked_seconds >= _slow_interval():
                # It worked for a whole slow interval: a fresh fast budget.
                budget_used = 0
                slow_announced = False
            budget_used += 1
            v = verdict
            reason = v.reason
            fast = budget_used < int(config.TUNNEL_RESTART_MAX_ATTEMPTS)
            phase = tunnel_state.RETRYING if fast else tunnel_state.SLOW
            delay = _backoff_seconds(budget_used) if fast else _slow_interval()
            # Not working from here on: /app and doctor say so at once, and
            # the log says why, also while a publish in flight and the
            # clear go out.
            self._set_status(phase, reason, budget_used, time.time() + delay)
            if fast:
                log.warning("cloudflared %s (code=%s, stage %s) - reason %s [%s]; "
                            "trying again in %.0fs", v.kind, v.code, v.stage, reason,
                            v.detail, delay)
            elif not slow_announced:
                slow_announced = True
                log.warning("cloudflared %s (code=%s, stage %s) - reason %s [%s]; "
                            "%d attempts failed, so the tunnel is now retried slowly, "
                            "every %.0fs", v.kind, v.code, v.stage, reason, v.detail,
                            budget_used, delay)
            else:
                log.info("cloudflared %s (code=%s, stage %s) - reason %s [%s]; "
                         "next slow retry in %.0fs", v.kind, v.code, v.stage, reason,
                         v.detail, delay)
            await self._set_url("")
            # The wait starts now, after the clear.
            self._set_status(phase, reason, budget_used, time.time() + delay)
            if fast:
                await asyncio.sleep(delay)
            else:
                await self._slow_wait(delay)
                log.info("cloudflared: trying the tunnel again (slow retry)")
            self._set_status(phase, reason, budget_used, 0.0)


__all__ = ["TunnelManager", "TunnelLaunchError", "spawn_and_discover_url"]
