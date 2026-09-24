"""The UI-free core of aipager's self-update (roadmap 8.36).

Shared by Telegram ``/update``, the Mini App's Updates block and the CLI
``aipager update``. Stdlib only: no aiohttp, no telegram at module level.

Two seams, and nothing in the update code path reaches past them:

* :func:`_run_command` — the ONLY process spawner (installers,
  ``claude update``, ``claude --version``, ``systemctl show``,
  ``systemd-run``, the post-upgrade version probe). Argv lists only,
  never ``shell=True``; the child gets its own process group so a timeout
  kills the whole tree.
* :func:`_http_get` — the ONLY non-Telegram fetch (PyPI, npm,
  downloads.claude.ai). Bounded by a timeout and a byte cap; never raises.

``tests/conftest.py`` replaces both by default, so no test can run a real
installer or reach the network.
"""

from __future__ import annotations

import contextvars
import fcntl
import http.client
import itertools
import json
import logging
import os
import platform
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from aipager import install_source

log = logging.getLogger("aipager.self_update")

# ---------------------------------------------------------------------------
# Constants (patchable; paths are read LATE, inside each function)
# ---------------------------------------------------------------------------

PYPI_URL = "https://pypi.org/pypi/aipager/json"
CLAUDE_RELEASES_BASE = "https://downloads.claude.ai/claude-code-releases"
NPM_DIST_TAGS_URL = (
    "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags"
)

HTTP_TIMEOUT_SECONDS = 5.0
PYPI_MAX_BYTES = 4 * 1024 * 1024
SMALL_MAX_BYTES = 4 * 1024

UPGRADE_TIMEOUT_SECONDS = 600
CLAUDE_UPDATE_TIMEOUT_SECONDS = 300
CLAUDE_VERSION_TIMEOUT_SECONDS = 10
PROBE_TIMEOUT_SECONDS = 60
SYSTEMCTL_TIMEOUT_SECONDS = 5
SCHEDULE_TIMEOUT_SECONDS = 10
KILL_GRACE_SECONDS = 5

GATE_POLL_SECONDS = 2.0
GATE_SETTLE_POLLS = 2
GATE_MAX_WAIT_SECONDS = 600
GATE_DECISION_MAX_SECONDS = 3600

RESTART_DELAY_SECONDS = 5
PROGRESS_EDIT_MIN_SECONDS = 5
HEARTBEAT_GRANULARITY_SECONDS = 30
STATUS_CACHE_SECONDS = 60
MARKER_SETTLE_SECONDS = 6
MARKER_MAX_AGE_SECONDS = 86400

OUTPUT_TAIL_CHARS = 500

UNIT_NAME = "aipager.service"
MACOS_LABEL = "com.aipager.daemon"

UPDATE_LOCK_PATH = Path.home() / ".local" / "share" / "aipager" / "update.lock"
UPDATE_MARKER_PATH = (
    Path.home() / ".local" / "share" / "aipager" / "update-restart.json"
)

_PROC_SELF_CGROUP = "/proc/self/cgroup"
_PROC_ROOT = "/proc"
_CGROUP_ROOT = "/sys/fs/cgroup"

_RELEASE_RE = re.compile(r"^\s*v?(\d+(?:\.\d+){1,3})\s*$")
_CLAUDE_VERSION_RE = re.compile(r"^\s*(\d+\.\d+\.\d+)\s*\(Claude Code\)")
_BARE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PYPI_VERSION_RE = re.compile(r"^[0-9A-Za-z.+!-]{1,40}$")
_DTACH_SOCK_RE = re.compile(r"claude-dtach-(.+)\.sock$")
_PROBE_LINE_RE = re.compile(r"AIPAGER_VERSION=(\S+)")

# The update job whose work is running in this context. The daemon's job
# task sets it; ``asyncio.to_thread`` copies the context into the worker
# thread, so every spawn/claude/restart log line carries the job id.
CURRENT_JOB_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "aipager_update_job_id", default=None)


def _job_tag() -> str:
    job = CURRENT_JOB_ID.get()
    return f" job={job}" if job is not None else ""


_NPM_TAG = {"latest": "latest", "stable": "stable", "rc": "next"}
_CHANNELS = ("latest", "stable", "rc")

# Bare bot token (the URL form is handled by errors.redact_token) and an
# Anthropic key/OAuth token.
# No leading ``\b``: installer output can glue a token to a word character
# ("p987654321:AA…"), and a missed token is then cut in half by the tail.
_BARE_BOT_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")
_ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

# The post-upgrade probe runs in a FRESH interpreter (``-I``: no cwd on
# sys.path, no PYTHON* env) so it sees what is now on disk, not what this
# process has in memory, and proves the daemon's entry modules import.
_PROBE_SCRIPT = (
    "import importlib.metadata as m\n"
    "import aipager.cli.daemon\n"
    "import aipager.bot\n"
    "print('AIPAGER_VERSION=' + m.version('aipager'))\n"
)


# ---------------------------------------------------------------------------
# Output hygiene
# ---------------------------------------------------------------------------

def redact_output(text: str) -> str:
    """Remove bot tokens, Anthropic tokens and the literal values of the
    secret env vars from ``text``."""
    from aipager.errors import redact_token

    out = redact_token(text or "")
    out = _BARE_BOT_TOKEN_RE.sub("<redacted>", out)
    out = _ANTHROPIC_TOKEN_RE.sub("sk-ant-<redacted>", out)
    for key, value in os.environ.items():
        if install_source._is_secret_key(key) and len(value or "") >= 8:
            out = out.replace(value, "<redacted>")
    return out


def redacted_tail(text, limit: int | None = None) -> str:
    """The last ``limit`` (default ``OUTPUT_TAIL_CHARS``) characters of
    ``text`` AFTER redacting all of it. Redacting first matters: a cut that
    lands inside a token leaves a fragment the patterns no longer match."""
    if not text:
        return ""
    n = OUTPUT_TAIL_CHARS if limit is None else limit
    return redact_output(str(text).strip())[-n:]


def _scrub_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return install_source.spawn_env(extra)


# ---------------------------------------------------------------------------
# The subprocess seam
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    output_tail: str
    timed_out: bool
    error: str | None
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's process group, then SIGKILL after a grace."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


# Children the real seam is waiting on right now, so a daemon shutdown can
# take them down instead of leaving an installer orphaned and writing the
# venv while the next daemon starts (see terminate_running_commands).
_LIVE_CHILDREN: set = set()
_LIVE_LOCK = threading.Lock()


def terminate_running_commands() -> int:
    """Kill the process group of every child the seam is waiting on.

    Blocking (up to two ``KILL_GRACE_SECONDS`` per child): call it from a
    thread. Returns how many children were signalled. Never raises.
    """
    with _LIVE_LOCK:
        procs = list(_LIVE_CHILDREN)
    for proc in procs:
        log.warning("update.shutdown.kill pid=%s argv=%s", getattr(proc, "pid", None),
                    getattr(proc, "args", None))
        try:
            _kill_group(proc)
        except Exception:
            log.warning("could not kill update child", exc_info=True)
    return len(procs)


def running_command_count() -> int:
    with _LIVE_LOCK:
        return len(_LIVE_CHILDREN)


def _tail(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return redacted_tail(text)


def _run_command(argv, *, timeout, env=None, capture=True) -> CommandResult:
    """Run ``argv`` (a list, never a shell string). Never raises.

    ``capture=False`` lets the child write straight to the terminal (the
    CLI shows live installer output). On timeout the whole process group
    gets SIGTERM, then SIGKILL.
    """
    started = time.monotonic()
    argv = [str(a) for a in argv]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError) as e:
        return CommandResult(None, "", False, f"{type(e).__name__}: {e}",
                             time.monotonic() - started)
    with _LIVE_LOCK:
        _LIVE_CHILDREN.add(proc)
    try:
        return _wait_child(proc, started, timeout)
    finally:
        with _LIVE_LOCK:
            _LIVE_CHILDREN.discard(proc)


def _wait_child(proc: subprocess.Popen, started: float, timeout) -> CommandResult:
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        partial = b""
        try:
            partial, _ = proc.communicate(timeout=1)
        except Exception:
            pass
        return CommandResult(None, _tail(partial), True,
                             f"timed out after {timeout}s",
                             time.monotonic() - started)
    except BaseException:
        # KeyboardInterrupt in the CLI: the child is in its own session and
        # would not see the terminal's SIGINT, so take it down with us.
        _kill_group(proc)
        raise
    return CommandResult(proc.returncode, _tail(out), False, None,
                         time.monotonic() - started)


def run_command(argv, *, timeout, env=None, capture=True) -> CommandResult:
    """Public entry to the seam: logs argv (absolute paths, no secrets),
    return code and duration."""
    argv = [str(a) for a in argv]
    result = _run_command(argv, timeout=timeout, env=env, capture=capture)
    log.info("update.spawn%s argv=%s rc=%s timed_out=%s duration=%.1fs%s",
             _job_tag(), argv, result.returncode, result.timed_out, result.duration,
             f" error={result.error}" if result.error else "")
    return result


# ---------------------------------------------------------------------------
# The HTTP seam
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: float, max_bytes: int) -> bytes | None:
    """GET ``url``; the body, or None on any failure or oversize body."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"aipager/{running_version()} (self-update check)",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                return None
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, OSError, TimeoutError,
            http.client.HTTPException, ValueError):
        return None
    except Exception:
        return None
    if len(data) > max_bytes:
        return None
    return data


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def running_version() -> str:
    """The version of the code THIS process loaded."""
    from aipager import __version__
    return __version__


def installed_version() -> str | None:
    """The version currently recorded on disk for this interpreter."""
    try:
        from importlib.metadata import version
        return version("aipager")
    except Exception:
        return None


def parse_version(s) -> tuple[int, ...] | None:
    """Numeric release tuple of a plain release, else None (pre-releases,
    dev/post/local versions and garbage all return None)."""
    if not isinstance(s, str):
        return None
    m = _RELEASE_RE.match(s)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest, current) -> bool:
    """True iff ``latest`` is a strictly newer plain release than
    ``current``. Unparseable or pre-release on either side → False."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def latest_aipager_version() -> str | None:
    body = _http_get(PYPI_URL, timeout=HTTP_TIMEOUT_SECONDS,
                     max_bytes=PYPI_MAX_BYTES)
    if body is None:
        return None
    try:
        data = json.loads(body)
        value = data["info"]["version"]
    except Exception:
        return None
    if not isinstance(value, str) or not _PYPI_VERSION_RE.match(value):
        return None
    return value


def _load_json(path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def claude_channel_info(realpath: str | None = None) -> tuple[str, str]:
    """``(method, channel)`` for the Claude Code install.

    ``channel`` is Claude Code's own ``autoUpdatesChannel`` (latest|stable|
    rc; anything else → latest), so the "latest" we show is the version
    ``claude update`` would actually install. ``method`` comes from
    ``installMethod`` in ``~/.claude.json``, else from where the resolved
    binary lives.
    """
    from aipager import claude_bootstrap

    settings = _load_json(claude_bootstrap._SETTINGS)
    channel = settings.get("autoUpdatesChannel")
    if channel not in _CHANNELS:
        channel = "latest"

    method = "unknown"
    raw = _load_json(claude_bootstrap._CLAUDE_JSON).get("installMethod")
    if isinstance(raw, str):
        if raw == "native":
            method = "native"
        elif raw.startswith("npm") or raw in ("global", "local"):
            method = "npm"
    if method == "unknown" and realpath:
        versions_dir = str(Path.home() / ".local" / "share" / "claude" / "versions")
        if realpath.startswith(versions_dir + os.sep):
            method = "native"
        elif "node_modules" in realpath.split(os.sep):
            method = "npm"
    return method, channel


def latest_claude_version(method: str, channel: str) -> str | None:
    if channel not in _CHANNELS:
        return None
    if method == "native":
        body = _http_get(f"{CLAUDE_RELEASES_BASE}/{channel}",
                         timeout=HTTP_TIMEOUT_SECONDS, max_bytes=SMALL_MAX_BYTES)
        if body is None:
            return None
        text = body.decode("utf-8", errors="replace").strip()
        return text if _BARE_VERSION_RE.match(text) else None
    if method == "npm":
        body = _http_get(NPM_DIST_TAGS_URL, timeout=HTTP_TIMEOUT_SECONDS,
                         max_bytes=SMALL_MAX_BYTES)
        if body is None:
            return None
        try:
            value = json.loads(body).get(_NPM_TAG[channel])
        except Exception:
            return None
        if isinstance(value, str) and _BARE_VERSION_RE.match(value):
            return value
        return None
    return None


def _claude_version_at(path: str) -> str | None:
    res = run_command([path, "--version"], timeout=CLAUDE_VERSION_TIMEOUT_SECONDS,
                      env=_scrub_env())
    if res.returncode != 0:
        return None
    m = _CLAUDE_VERSION_RE.match(res.output_tail.splitlines()[0]
                                 if res.output_tail else "")
    return m.group(1) if m else None


def current_claude() -> tuple[str, str] | None:
    """``(path, version)`` of the claude aipager launches, or None.

    The path comes from the resolver (memoised); the version is read
    fresh through the seam, since the memo can be stale after an update.
    """
    from aipager import claude_resolve

    resolved = claude_resolve.try_resolve_claude_binary()
    if resolved is None:
        return None
    path = os.path.abspath(resolved.chosen.path)
    version = _claude_version_at(path) or resolved.chosen.version
    return path, version


def probe_installed_version(python: str) -> tuple[str | None, bool, str | None]:
    """``(version, importable, error)`` from a FRESH interpreter."""
    res = run_command([python, "-I", "-c", _PROBE_SCRIPT],
                      timeout=PROBE_TIMEOUT_SECONDS, env=_scrub_env())
    version = None
    for line in reversed((res.output_tail or "").splitlines()):
        m = _PROBE_LINE_RE.search(line)
        if m:
            version = m.group(1)
            break
    if res.returncode == 0 and version:
        return version, True, None
    err = res.error or (redacted_tail(res.output_tail, 300) if res.output_tail else
                        f"exit {res.returncode}")
    return version, False, err


@dataclass
class ClaudeUpdateResult:
    before: str | None
    after: str | None
    returncode: int | None
    timed_out: bool
    output_tail: str
    error: str | None = None
    path: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None


def run_claude_update() -> ClaudeUpdateResult:
    """``<absolute claude> update`` with a timeout; never restarts a session."""
    from aipager import claude_resolve

    cur = current_claude()
    if cur is None:
        log.info("update.claude.failed%s reason=claude-not-found", _job_tag())
        return ClaudeUpdateResult(None, None, None, False, "",
                                  error="Claude Code was not found")
    path, before = cur
    log.info("update.claude.start%s path=%s before=%s", _job_tag(), path, before)
    res = run_command([path, "update"], timeout=CLAUDE_UPDATE_TIMEOUT_SECONDS,
                      env=_scrub_env())
    after = _claude_version_at(path)
    try:
        # New sessions must launch (and log) the new binary, and the memo
        # is not refreshed by resolve_claude_binary(force=True).
        claude_resolve.refresh_claude_binary()
    except Exception:
        log.debug("claude resolver refresh failed", exc_info=True)
    if res.timed_out:
        log.info("update.claude.timeout%s path=%s", _job_tag(), path)
    elif res.returncode != 0 or res.error:
        log.info("update.claude.failed%s rc=%s error=%s", _job_tag(), res.returncode,
                 res.error)
    else:
        log.info("update.claude.done%s before=%s after=%s", _job_tag(), before, after)
    return ClaudeUpdateResult(before, after, res.returncode, res.timed_out,
                              res.output_tail, res.error, path)


# ---------------------------------------------------------------------------
# The update lock
# ---------------------------------------------------------------------------

class UpdateLock:
    """Cross-process, non-blocking ``flock`` on ``UPDATE_LOCK_PATH``.

    flock conflicts between different open file descriptions — including
    two in the same process — so this one mechanism serialises the CLI
    against the daemon and two daemon jobs against each other. The kernel
    drops it when the holder exits.
    """

    def __init__(self, path: Path | str | None = None):
        self._path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock, or return False. NOT re-entrant: a holder asking
        again is refused, so a second job on the same object cannot slip
        in while the first still owns the lock (e.g. a pending restart)."""
        if self._fd is not None:
            return False
        path = Path(self._path if self._path is not None else UPDATE_LOCK_PATH)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            log.warning("update lock %s could not be opened", path, exc_info=True)
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The restart gate
# ---------------------------------------------------------------------------

def restart_blockers(registry, *, now: float | None = None,
                     held_count: int | None = None) -> list[str]:
    """Why a daemon restart would interrupt something, or ``[]``.

    Looks at EVERY session (a restart affects every scope). UNKNOWN and
    GONE sessions never block. Held answers block too: a restart drops
    them.
    """
    from aipager.state import Status

    if now is None:
        now = time.monotonic()
    reasons: list[str] = []
    sessions = registry.all_sessions() if registry is not None else {}
    for name in sorted(sessions):
        sess = sessions[name]
        if sess.status in (Status.GONE, Status.UNKNOWN):
            continue
        why: list[str] = []
        if sess.status == Status.BUSY:
            why.append("running a turn")
        if sess.status == Status.INTERACTIVE:
            why.append("waiting on a question")
        if sess.job_background_open():
            why.append("background agent running")
        if (sess.busy_msg_id or 0) > 0:
            why.append("live busy card")
        if sess.work_in_flight(now):
            why.append("tool call in flight")
        if sess.pending_permission is not None:
            why.append("permission prompt open")
        if why:
            reasons.append(f"{sess.label or sess.name}: {', '.join(why)}")
    if held_count is None:
        try:
            from aipager.bot.held import HELD
            held_count = HELD.count()
        except Exception:
            held_count = 0
    if held_count:
        noun = "answer" if held_count == 1 else "answers"
        reasons.append(f"{held_count} held {noun} not yet delivered")
    return reasons


# ---------------------------------------------------------------------------
# The restart plan
# ---------------------------------------------------------------------------

@dataclass
class RestartPlan:
    mode: str                       # systemd|launchd|foreground
    automatic: bool
    reason: str | None = None
    manual_command: str | None = None
    at_risk: list[str] = field(default_factory=list)
    killmode: str | None = None

    def to_dict(self) -> dict:
        return {"mode": self.mode, "automatic": self.automatic,
                "reason": self.reason, "manual_command": self.manual_command}


def _system() -> str:
    """``platform.system()`` behind a module seam, so tests can simulate
    macOS without patching the global ``platform`` module."""
    return platform.system()


def _own_cgroup() -> str | None:
    try:
        lines = [ln for ln in Path(_PROC_SELF_CGROUP).read_text().splitlines()
                 if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    return lines[-1].split(":", 2)[-1].strip()


def _under_unit_cgroup() -> bool:
    cg = _own_cgroup()
    return bool(cg) and cg.rstrip("/").endswith("/" + UNIT_NAME)


def _parse_systemctl_show(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _systemctl_show(*props: str) -> dict[str, str] | None:
    systemctl = install_source.resolve_tool("systemctl")
    if systemctl is None:
        return None
    argv = [systemctl, "--user", "show"]
    for p in props:
        argv += ["-p", p]
    argv.append(UNIT_NAME)
    res = run_command(argv, timeout=SYSTEMCTL_TIMEOUT_SECONDS, env=_scrub_env())
    if res.returncode != 0:
        return None
    return _parse_systemctl_show(res.output_tail)


def _session_label(registry, name: str) -> str:
    sess = registry.get(name) if registry is not None else None
    if sess is not None and getattr(sess, "label", None):
        return sess.label
    return name.removeprefix("claude-")


def _sessions_in_cgroup(registry, control_group: str | None) -> list[str] | None:
    """Labels of sessions whose dtach master sits in ``control_group``,
    or None when that cannot be read."""
    if not control_group:
        return None
    procs = Path(_CGROUP_ROOT + control_group) / "cgroup.procs"
    try:
        pids = [p.strip() for p in procs.read_text().split() if p.strip()]
    except OSError:
        return None
    names: set[str] = set()
    for pid in pids:
        try:
            raw = (Path(_PROC_ROOT) / pid / "cmdline").read_bytes()
        except OSError:
            continue
        for arg in raw.split(b"\0"):
            m = _DTACH_SOCK_RE.search(arg.decode("utf-8", errors="replace"))
            if m:
                names.add("claude-" + m.group(1))
    return sorted({_session_label(registry, n) for n in names})


def _live_labels(registry) -> list[str]:
    from aipager.state import Status

    if registry is None:
        return []
    return sorted(
        s.label or s.name for s in registry.all_sessions().values()
        if s.status != Status.GONE
    )


def _killmode_is_safe(killmode: str | None) -> bool:
    return killmode in ("process", "none")


def killmode_fix_text() -> str:
    return ("the service unit's KillMode would kill every session the daemon "
            "launched. Run `aipager service install` on the machine first "
            "(it sets KillMode=process), then restart")


def restart_plan(registry=None) -> RestartPlan:
    """How (and whether) this daemon can restart itself. Never raises."""
    try:
        return _restart_plan(registry)
    except Exception:
        log.warning("restart plan detection failed", exc_info=True)
        return RestartPlan("foreground", False,
                           "could not tell how this daemon is run; restart it yourself")


def _restart_plan(registry) -> RestartPlan:
    system = _system()
    if system == "Linux" and _under_unit_cgroup():
        show = _systemctl_show("MainPID", "KillMode", "ControlGroup")
        if show is not None and show.get("MainPID") != str(os.getpid()):
            # A unit exists, but this process is not (or cannot be shown
            # to be) its main PID: fail closed, like KillMode.
            return RestartPlan(
                "foreground", False,
                "no service unit is running this daemon; restart it yourself")
        cmd = f"systemctl --user restart {UNIT_NAME}"
        killmode = (show or {}).get("KillMode") or None
        if show is not None and _killmode_is_safe(killmode):
            return RestartPlan("systemd", True, None, cmd, [], killmode)
        at_risk = _sessions_in_cgroup(
            registry, (show or {}).get("ControlGroup") or _own_cgroup())
        if at_risk is None:
            live = _live_labels(registry)
            at_risk = [f"may include: {', '.join(live)}"] if live else []
        reason = (f"KillMode={killmode}: " if killmode else
                  "KillMode could not be read: ") + killmode_fix_text()
        return RestartPlan("systemd", False, reason, cmd, at_risk, killmode)
    if system == "Darwin" and os.environ.get("XPC_SERVICE_NAME") == MACOS_LABEL:
        return RestartPlan(
            "launchd", False,
            "launchd restarts are not automatic; restart it yourself",
            f"launchctl kickstart -k gui/{os.getuid()}/{MACOS_LABEL}")
    return RestartPlan(
        "foreground", False,
        "no service unit is running this daemon; restart it yourself")


_UNIT_SEQ = itertools.count(1)


def schedule_restart(plan: RestartPlan) -> tuple[bool, str]:
    """Schedule ONE detached ``systemctl --user restart aipager.service``
    ``RESTART_DELAY_SECONDS`` from now, in a transient unit outside this
    daemon's cgroup so it survives our exit. Only for an automatic
    systemd plan."""
    if plan is None or plan.mode != "systemd" or not plan.automatic:
        return False, "this daemon cannot restart itself automatically"
    systemd_run = install_source.resolve_tool("systemd-run")
    systemctl = install_source.resolve_tool("systemctl")
    if systemd_run is None or systemctl is None:
        return False, "systemd-run or systemctl not found"
    unit = (f"aipager-update-restart-{int(time.time())}-{os.getpid()}-"
            f"{next(_UNIT_SEQ)}")
    argv = [systemd_run, "--user", f"--on-active={RESTART_DELAY_SECONDS}s",
            f"--unit={unit}", "--collect", "--quiet",
            systemctl, "--user", "restart", UNIT_NAME]
    res = run_command(argv, timeout=SCHEDULE_TIMEOUT_SECONDS, env=_scrub_env())
    if res.ok:
        log.info("update.restart.scheduled%s unit=%s delay=%ss", _job_tag(), unit,
                 RESTART_DELAY_SECONDS)
        return True, unit
    detail = res.error or res.output_tail or f"exit {res.returncode}"
    log.info("update.restart.failed%s detail=%s", _job_tag(), detail)
    return False, detail


def cancel_scheduled_restart(unit: str | None) -> bool:
    """Best-effort stop of the transient timer :func:`schedule_restart`
    created, so a late-firing timer cannot restart the daemon in the middle
    of a later job. Absolute ``systemctl``, bounded. Never raises."""
    if not unit or not unit.startswith("aipager-update-restart-"):
        return False
    systemctl = install_source.resolve_tool("systemctl")
    if systemctl is None:
        return False
    res = run_command([systemctl, "--user", "stop", f"{unit}.timer"],
                      timeout=SYSTEMCTL_TIMEOUT_SECONDS, env=_scrub_env())
    log.info("update.restart.timer_stopped%s unit=%s ok=%s", _job_tag(), unit, res.ok)
    return res.ok


def cli_restart_instruction() -> list[str]:
    """What the CLI tells the user to run after an upgrade on this machine.

    The CLI never restarts anything itself.
    """
    from aipager import service

    system = _system()
    if system == "Linux" and Path(service.LINUX_UNIT_PATH).exists():
        lines = []
        show = _systemctl_show("KillMode")
        if show is None or not _killmode_is_safe(show.get("KillMode")):
            lines.append("run `aipager service install` first — it sets "
                         "KillMode=process so the restart keeps your sessions")
        lines.append(f"then restart the daemon: systemctl --user restart {UNIT_NAME}"
                     if lines else
                     f"restart the daemon: systemctl --user restart {UNIT_NAME}")
        return lines
    if system == "Darwin" and Path(service.MACOS_PLIST_PATH).exists():
        return [f"restart the daemon: launchctl kickstart -k "
                f"gui/{os.getuid()}/{MACOS_LABEL}"]
    return ["restart your `aipager start` to run the new version"]


# ---------------------------------------------------------------------------
# The post-restart marker
# ---------------------------------------------------------------------------

def write_marker(data: dict) -> None:
    """Atomically write the marker (mode 0600). Raises OSError on failure."""
    path = Path(UPDATE_MARKER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def clear_marker() -> None:
    try:
        Path(UPDATE_MARKER_PATH).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("could not remove the update marker", exc_info=True)


def read_and_clear_marker() -> dict | None:
    """Claim the marker: read it and delete it (send-once)."""
    path = Path(UPDATE_MARKER_PATH)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("could not read the update marker", exc_info=True)
        return None
    clear_marker()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "CURRENT_JOB_ID", "CommandResult", "ClaudeUpdateResult", "RestartPlan",
    "UpdateLock", "cancel_scheduled_restart", "claude_channel_info", "clear_marker", "cli_restart_instruction",
    "current_claude", "installed_version", "is_newer",
    "latest_aipager_version", "latest_claude_version", "parse_version",
    "probe_installed_version", "read_and_clear_marker", "redact_output",
    "redacted_tail",
    "restart_blockers", "restart_plan", "run_claude_update", "run_command",
    "running_command_count", "running_version", "schedule_restart",
    "terminate_running_commands", "write_marker",
]
