"""Launch Claude Code inside a named dtach session.

The user-facing entry point is ``aipager session <name>`` (wired through
``aipager.cli``), which calls :func:`launch`. If the dtach socket
already exists and a process is listening on it this reattaches;
otherwise it spawns a new session and attaches.

Sets ``CLAUDE_DTACH_SESSION`` inside the spawned session so the aipager
hook scripts can identify which session sent which event.
"""

from __future__ import annotations

import re
import shutil
import socket as _socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from aipager import _dtach_redraw
from aipager.errors import friendly_error, friendly_warn
from aipager.ui import console, ok

_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,50}")


def _resolve_dtach() -> str | None:
    """Return absolute path to the dtach binary, or None if unavailable."""
    try:
        from dtach_bin import path
        return path()
    except (ImportError, FileNotFoundError):
        pass
    return shutil.which("dtach")


def _dtach_works(dtach_path: str) -> tuple[bool, str]:
    """Probe the dtach binary by running ``dtach`` with no args.

    dtach exits 1 and prints "dtach - ..." usage on stderr when called
    with no arguments. The point is to confirm the binary is loadable
    (right arch, libc available) — we don't care about its return code.
    """
    try:
        r = subprocess.run([dtach_path], capture_output=True, text=True, timeout=2)
    except FileNotFoundError:
        return False, "binary missing"
    except OSError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "probe hung"
    blob = (r.stdout + r.stderr).lower()
    if "dtach" in blob:
        return True, ""
    return False, blob.strip().splitlines()[0][:120] if blob else "no output"


def _socket_alive(sock: str) -> bool:
    """Return True iff *something* is currently listening on the dtach socket.

    dtach uses AF_UNIX SOCK_STREAM, so we probe via connect(). A stale
    socket left by a dead process raises ConnectionRefusedError.
    """
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(sock)
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    finally:
        s.close()


def _set_title(name: str) -> None:
    sys.stderr.write(f"\033]0;{name}\007")
    sys.stderr.flush()


def _keep_title(name: str, stop: threading.Event) -> None:
    """Re-emit terminal title every 3s — Claude Code's TUI overrides it."""
    while not stop.is_set():
        _set_title(name)
        if stop.wait(3.0):
            break


def _force_redraw(name: str) -> None:
    """Bounce PTY size 0.8s after attach to force Ink to redraw."""
    time.sleep(0.8)
    _dtach_redraw.redraw(name)


def _validate_name(name: str) -> str | None:
    """Return None if the name is valid, else an error string."""
    if not name:
        return "session name cannot be empty"
    if not _NAME_RE.fullmatch(name):
        return ("session name must be 1-50 chars of [A-Za-z0-9_-]; "
                f"got {name!r}")
    return None


def launch(name: str, skip_perms: bool = False,
           resume: bool = False,
           claude_args: list[str] | None = None) -> int:
    """Create or reattach a Claude Code session inside dtach."""
    err = _validate_name(name)
    if err:
        friendly_error(err)
        return 2

    claude_args = list(claude_args) if claude_args else []
    if resume:
        claude_args.insert(0, "--continue")
    session = f"claude-{name}"
    sock = f"/tmp/claude-dtach-{name}.sock"

    dtach = _resolve_dtach()
    if not dtach:
        friendly_error(
            "dtach not installed.",
            "",
            "  aipager bundles dtach via the dtach-bin package. Try:",
            "      uv tool install --reinstall aipager",
            "",
            "  Or install system-wide:",
            "      Debian/Ubuntu:  sudo apt install dtach",
            "      macOS:          brew install dtach",
        )
        return 1

    dtach_ok, why = _dtach_works(dtach)
    if not dtach_ok:
        friendly_error(
            f"dtach binary at {dtach} fails to run.",
            f"  Detail: {why}",
            "",
            "  Probably an architecture / libc mismatch. Reinstall aipager:",
            "      uv tool install --reinstall aipager",
        )
        return 1

    sys_prompt = (
        f'Your session name is "{name}". '
        f'When users address you by this name, respond naturally '
        f'-- it is your name in this session.'
    )

    stop = threading.Event()
    sock_path = Path(sock)

    # Reattach branch — only if the socket is *alive*.
    if sock_path.exists() and _socket_alive(sock):
        console.print(f"[step]→[/step] reattaching to [path]{session}[/path]")
        _set_title(name)
        threading.Thread(target=_keep_title, args=(name, stop), daemon=True).start()
        threading.Thread(target=_force_redraw, args=(name,), daemon=True).start()
        attach_started = time.monotonic()
        try:
            subprocess.run([dtach, "-a", sock, "-r", "winch", "-E"], check=False)
        finally:
            stop.set()
        elapsed = time.monotonic() - attach_started
        if elapsed < 1.0 and not sock_path.exists():
            friendly_warn(
                f"session '{session}' exited immediately ({elapsed:.1f}s).",
                "  The claude process inside dtach may have crashed.",
                "  Try `aipager session <name>` again, or run `claude --version`.",
            )
        return 0

    # Stale-socket cleanup: a socket file exists but nothing's listening.
    if sock_path.exists():
        try:
            sock_path.unlink()
            console.print(f"  [muted](cleaned up stale socket {sock})[/muted]")
        except OSError as e:
            friendly_warn(f"stale socket {sock} could not be removed: {e}")

    console.print(f"[step]→[/step] starting [path]{session}[/path]")
    skip_arg = ["--dangerously-skip-permissions"] if skip_perms else []
    if console.is_terminal:
        spawn_status = console.status(
            "[muted]spawning dtach + claude…[/muted]", spinner="dots"
        )
    else:
        spawn_status = None
    if spawn_status:
        spawn_status.__enter__()
    try:
        spawn = subprocess.run(
            [dtach, "-n", sock, "-Ez",
             "env", f"CLAUDE_DTACH_SESSION={session}",
             "claude", *skip_arg,
             "--append-system-prompt", sys_prompt,
             *claude_args],
            capture_output=True, text=True, check=False,
        )
    finally:
        if spawn_status:
            spawn_status.__exit__(None, None, None)
    if spawn.returncode != 0:
        friendly_error(
            f"dtach failed to start session (exit {spawn.returncode}).",
            *( [f"  stderr: {spawn.stderr.rstrip()}"] if spawn.stderr.strip() else [] ),
            *( [f"  stdout: {spawn.stdout.rstrip()}"] if spawn.stdout.strip() else [] ),
            "",
            "  Try running `claude` directly to see if it works on its own,",
            "  then re-run `aipager session " + name + "`.",
        )
        return 1

    if console.is_terminal:
        wait_status = console.status(
            "[muted]waiting for socket to appear…[/muted]", spinner="dots"
        )
        wait_status.__enter__()
    else:
        wait_status = None
    try:
        for _ in range(10):
            time.sleep(0.3)
            if sock_path.is_socket():
                break
    finally:
        if wait_status:
            wait_status.__exit__(None, None, None)
    if not sock_path.is_socket():
        diag = _claude_version_diag()
        friendly_error(
            f"dtach socket {sock} never appeared after launch.",
            "  This usually means the `claude` process crashed at startup.",
            *( [f"  `claude --version` said: {diag}"] if diag else [] ),
            "",
            "  Run `claude` directly to see the underlying error.",
        )
        return 1

    ok(f"session [path]{session}[/path] ready")
    _set_title(name)
    threading.Thread(target=_keep_title, args=(name, stop), daemon=True).start()
    attach_started = time.monotonic()
    try:
        subprocess.run([dtach, "-a", sock, "-r", "winch", "-E"], check=False)
    finally:
        stop.set()
    elapsed = time.monotonic() - attach_started
    if elapsed < 1.0 and not sock_path.exists():
        friendly_warn(
            f"session '{session}' exited immediately ({elapsed:.1f}s).",
            "  The claude process inside dtach may have crashed.",
            "  Run `claude --version` to check the install.",
        )
    return 0


def _claude_version_diag() -> str:
    """Run ``claude --version`` and return a one-line summary, or "" on success."""
    claude = shutil.which("claude")
    if not claude:
        return "claude not on PATH"
    try:
        r = subprocess.run([claude, "--version"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"claude --version failed: {e}"
    if r.returncode != 0:
        first_line = (r.stderr or r.stdout).splitlines()[:1]
        return f"claude --version exit {r.returncode}: {first_line[0] if first_line else ''}"
    return ""  # claude is fine, the crash is elsewhere
