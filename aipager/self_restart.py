"""Replace the running daemon with the installed aipager (no service unit).

A daemon that no service manager restarts (a foreground ``aipager start``
in a terminal or tmux, Docker under tini, launchd) finishes an update by
restarting IN PLACE: the normal SIGTERM shutdown runs to its end, then
:func:`after_shutdown` ``execve``s the same interpreter with the argv,
environment and folder ``aipager start`` was launched with. Same PID,
same terminal (fds 0-2), same container PID; dtach sessions are not our
children and never notice.

When the exec cannot happen, the detached respawn takes over
(:func:`spawn_waiter`, also the voice extra's restart): a ``sh`` waits for
this PID to end, then runs the same launch argv, its output in the
self-restart log.

Every process replacement goes through three module-level seams,
``_execve``, ``_popen`` and ``_kill`` (``tests/conftest.py`` replaces all
three with refusers, and ``tests/test_self_update_spawn_seam.py`` sweeps
this file so no direct call can be added). Stdlib only, English logs
only: nothing here is shown to a person, and nothing here may import
``aipager.i18n``.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("aipager.self_restart")

# ---- seams (tests replace these, never os/subprocess themselves) -----------
_execve = os.execve
_popen = subprocess.Popen
_kill = os.kill
# Best effort before the exec; a seam so a test can never move pytest's cwd.
_chdir = os.chdir

#: How long a freshly spawned waiter must survive to count as started.
WAITER_SETTLE_SECONDS = 0.3

# Where this process's open fds are listed (Linux, then macOS).
_FD_DIRS = ("/proc/self/fd", "/dev/fd")


@dataclass(frozen=True)
class ExecTarget:
    """What the restarted daemon is: this interpreter, the launch argv,
    the launch environment and folder."""

    executable: str
    argv: list[str]
    env: dict[str, str] = field(repr=False)
    cwd: str | None = None


@dataclass
class _Request:
    target: ExecTarget
    frm: str
    to: str
    steps: str


class WaiterResult(tuple):
    """The detached respawn's outcome: the pair ``(ok, error)`` (it unpacks
    and indexes as one), plus ``stage``, where it failed: ``"open"`` (the
    log file), ``"spawn"`` (the ``sh``) or ``"exited"`` (it died at once),
    None when it started; and ``pid``."""

    def __new__(cls, ok: bool, error: str | None = None, stage: str | None = None,
                pid: int | None = None) -> WaiterResult:
        self = super().__new__(cls, (ok, error))
        self.stage = stage
        self.pid = pid
        return self

    @property
    def ok(self) -> bool:
        return self[0]

    @property
    def error(self) -> str | None:
        return self[1]


# ---- module state (one daemon per process) ----------------------------------
_launch: dict | None = None
_stop_cb: Callable[[], None] | None = None
_pending: _Request | None = None
# A real stop (SIGINT/SIGTERM) arrived: no restart is taken any more.
_stopping = False


def _reset() -> None:
    """Forget everything (tests; a fresh daemon run)."""
    global _launch, _stop_cb, _pending, _stopping
    _launch = None
    _stop_cb = None
    _pending = None
    _stopping = False


def _launch_argv() -> list[str]:
    orig = getattr(sys, "orig_argv", None)
    if orig:
        return [str(a) for a in orig]
    return [sys.executable, *sys.argv]


def capture_launch() -> None:
    """Snapshot the launch argv, environment and folder. The first thing
    ``aipager start`` does, before config loading or migrations can touch
    any of them."""
    global _launch
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    _launch = {"argv": _launch_argv(), "env": dict(os.environ), "cwd": cwd}


def _snapshot() -> dict:
    if _launch is not None:
        return _launch
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    return {"argv": _launch_argv(), "env": dict(os.environ), "cwd": cwd}


# Interpreter options that take the next argument when given alone.
_ARG_OPTIONS = frozenset("WX")


def _script_of(argv: list[str]) -> tuple[bool, int | None]:
    """``(usable, index)`` for an interpreter argv: ``(True, None)`` for
    the ``-m``/``-c`` forms, ``(True, i)`` when ``argv[i]`` is the script,
    ``(False, None)`` when no form can be told (stdin, nothing after the
    options)."""
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("--"):
            # The one long option that takes a value.
            i += 2 if arg == "--check-hash-based-pycs" else 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            flags = arg[1:]
            for pos, ch in enumerate(flags):
                if ch in "cm":
                    return True, None
                if ch in _ARG_OPTIONS:
                    if pos == len(flags) - 1:
                        i += 1          # its value is the next argument
                    break
            i += 1
            continue
        break
    if i >= len(argv) or argv[i] == "-":
        return False, None
    return True, i


def _interpreter() -> str:
    """This process's interpreter (a seam: tests point it elsewhere)."""
    return sys.executable


def exec_target() -> ExecTarget | None:
    """What an in-place restart would exec, or None when it cannot: the
    interpreter is gone or not executable."""
    exe = _interpreter()
    if not exe or not os.path.isabs(exe) or not os.path.isfile(exe) \
            or not os.access(exe, os.X_OK):
        return None
    snap = _snapshot()
    argv = list(snap["argv"])
    cwd = snap.get("cwd")
    usable, index = _script_of(argv) if argv else (False, None)
    args = list(sys.argv[1:])
    if usable and index is not None:
        script = argv[index]
        args = argv[index + 1:]
        path = script if os.path.isabs(script) or not cwd else os.path.join(cwd, script)
        usable = os.path.exists(path)
    if not usable:
        # The launch script went away (an upgrade can replace the console
        # script): the module form, which the wizard still recognises
        # (basename ``aipager`` followed by ``start``).
        argv = [exe, "-m", "aipager", *args]
    return ExecTarget(executable=exe, argv=argv, env=dict(snap["env"]), cwd=cwd)


# ---- the request, between the update job and the daemon's main ----------------

def attach(stop_cb: Callable[[], None]) -> None:
    """The daemon's stop (``_run_daemon``'s ``stop.set``)."""
    global _stop_cb, _stopping
    _stop_cb = stop_cb
    _stopping = False


def detach() -> None:
    global _stop_cb
    _stop_cb = None


def pending() -> bool:
    return _pending is not None


def request(target: ExecTarget, *, frm: str, to: str, steps: str) -> bool:
    """Ask the daemon to stop and then exec *target*. False (nothing
    stops) with no daemon attached, after a real stop signal, or with a
    request already pending."""
    global _pending
    if _stop_cb is None or _stopping or _pending is not None:
        log.info("restart.request.refused attached=%s stopping=%s pending=%s",
                 _stop_cb is not None, _stopping, _pending is not None)
        return False
    _pending = _Request(target=target, frm=str(frm), to=str(to), steps=str(steps))
    log.info("restart.requested from=%s to=%s", frm, to)
    try:
        _stop_cb()
    except Exception:
        log.warning("restart.request.stop_failed", exc_info=True)
        _pending = None
        return False
    return True


def cancel(reason: str) -> None:
    """A real stop arrived (Ctrl+C, ``docker stop``, ``systemctl stop``):
    it always wins over a pending restart, and no new one is taken."""
    global _pending, _stopping
    _stopping = True
    if _pending is not None:
        log.info("restart.cancelled reason=%s", reason)
        _pending = None


def take() -> _Request | None:
    """Claim the pending request (once)."""
    global _pending
    req, _pending = _pending, None
    return req


def stop_daemon() -> None:
    """Stop this daemon the clean way: the attached stop, else SIGTERM."""
    if _stop_cb is not None:
        _stop_cb()
        return
    _kill(os.getpid(), signal.SIGTERM)


# ---- the fallback: a detached respawn ----------------------------------------------

def _waiter_command(target: ExecTarget, pid: int) -> str:
    words = [shlex.quote(target.executable), *(shlex.quote(a) for a in target.argv[1:])]
    return "; ".join([
        " ".join(["while", "kill", "-0", str(pid), "2>/dev/null"]),
        " ".join(["do", "sleep", "0.2"]),
        "done",
        " ".join(["exec", *words]),
    ])


def spawn_waiter(target: ExecTarget, log_path) -> WaiterResult:
    """Start a detached ``sh`` that waits for this PID to end, then runs
    *target* with its output appended to *log_path*. Never raises."""
    try:
        fd = os.open(os.fspath(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError as e:
        log.warning("restart.respawn.log_failed path=%s error=%s", log_path, e)
        return WaiterResult(False, str(e), "open")
    try:
        proc = _popen(
            ["/bin/sh", "-c", _waiter_command(target, os.getpid())],
            stdout=fd, stderr=fd, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
            env=target.env, cwd=target.cwd or None,
        )
    except (OSError, ValueError) as e:
        log.warning("restart.respawn.spawn_failed error=%s", e)
        return WaiterResult(False, str(e), "spawn")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    time.sleep(WAITER_SETTLE_SECONDS)
    if proc.poll() is not None:
        log.warning("restart.respawn.exited rc=%s", proc.returncode)
        return WaiterResult(False, f"rc={proc.returncode}", "exited", proc.pid)
    return WaiterResult(True, None, None, proc.pid)


# ---- after the shutdown: exec, or hand over ---------------------------------------

def _flush_everything() -> None:
    seen = set()
    loggers = [logging.getLogger()] + [
        lg for lg in logging.Logger.manager.loggerDict.values()
        if isinstance(lg, logging.Logger)]
    for lg in loggers:
        for handler in getattr(lg, "handlers", ()):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                handler.flush()
            except Exception:
                pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass


def _open_fds() -> list[int]:
    for d in _FD_DIRS:
        try:
            return sorted(int(n) for n in os.listdir(d) if n.isdigit())
        except OSError:
            continue
    return []


def _close_on_exec_above_stdio() -> None:
    """Every fd above 2 is marked close-on-exec (Python opens them that way
    already; this makes "nothing leaks into the new image" explicit).
    Nothing is closed: a failed exec can still fall back."""
    for fd in _open_fds():
        if fd <= 2:
            continue
        try:
            os.set_inheritable(fd, False)
        except OSError:
            pass


def _manual_command(steps: str) -> str:
    """The command that starts aipager again by hand, for the log."""
    if steps == "systemd":
        words = ("systemctl", "--user", "restart", "aipager.service")
    elif steps == "launchd":
        words = ("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.aipager.daemon")
    elif steps == "docker":
        words = ("docker", "restart", "<container>")
    else:
        words = ("aipager", "start")
    return " ".join(words)


def _log_manual_steps(steps: str) -> None:
    # English, the daemon log only: what to run now that nothing restarts.
    log.error("restart.failed: aipager stopped and could not start again; start it "
              "yourself: %s", _manual_command(steps))


def _runnable(exe: str) -> bool:
    return os.path.isfile(exe) and os.access(exe, os.X_OK)


def after_shutdown(release_lock: Callable[[], None]) -> int:
    """The last step of ``aipager start``, once the daemon has shut down
    cleanly: exec the pending restart, else the respawn fallback. Returns
    the exit code (0: nothing pending, cancelled, or handed over to the
    respawn; 1: nothing could start, the manual steps are logged). Does
    not return after a successful exec. An interpreter that is gone is
    never handed to a respawn: it would only fail the same way, later and
    out of sight."""
    req = take()
    if req is None:
        return 0
    target = req.target
    exe = target.executable
    if not _runnable(exe):
        log.error("restart.exec.failed error=the interpreter %s is gone", exe)
        _log_manual_steps(req.steps)
        return 1
    log.info("restart.exec from=%s to=%s executable=%s argv=%s",
             req.frm, req.to, exe, target.argv)
    _flush_everything()
    try:
        release_lock()
    except Exception:
        log.warning("restart.exec.lock_release_failed", exc_info=True)
    _close_on_exec_above_stdio()
    if target.cwd:
        try:
            _chdir(target.cwd)
        except OSError:
            pass
    try:
        _execve(exe, target.argv, target.env)
    except OSError as e:
        log.error("restart.exec.failed error=%s", e)
    else:
        # Only a test's fake returns from an exec.
        return 0
    if not _runnable(exe):
        log.error("restart.exec.failed error=the interpreter %s is gone", exe)
        _log_manual_steps(req.steps)
        return 1
    from aipager import instance

    log_path = instance.self_restart_log_path()
    res = spawn_waiter(target, log_path)
    if res.ok:
        log.info("restart.respawn.started pid=%s log=%s", res.pid, log_path)
        # The respawn runs out of sight: what to do if it never comes back.
        log.warning("restart.respawn: if aipager is not back in a minute, see %s and "
                    "start it yourself: %s", log_path, _manual_command(req.steps))
        return 0
    _log_manual_steps(req.steps)
    return 1

__all__ = [
    "ExecTarget", "WaiterResult", "after_shutdown", "attach", "cancel", "capture_launch",
    "detach", "exec_target", "pending", "request", "spawn_waiter", "stop_daemon", "take",
]
