"""Launch Claude Code inside a named dtach session.

The user-facing entry point is ``aipager new <name>`` (wired through
``aipager.cli``), which calls :func:`launch`. If the dtach socket
already exists this reattaches; otherwise it spawns a new session and
attaches.

Sets ``CLAUDE_DTACH_SESSION`` inside the spawned session so the aipager
hook scripts can identify which session sent which event.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from aipager import _dtach_redraw


def _resolve_dtach() -> str | None:
    """Return absolute path to the dtach binary, or None if unavailable."""
    try:
        from dtach_bin import path
        return path()
    except (ImportError, FileNotFoundError):
        pass
    return shutil.which("dtach")


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


def launch(name: str, skip_perms: bool = False,
           resume: bool = False,
           claude_args: list[str] | None = None) -> int:
    """Create or reattach a Claude Code session inside dtach.

    If ``resume`` is True and a *new* dtach session is created, ``--continue``
    is prepended to claude's args so it loads the most recent saved
    conversation in the current cwd. When reattaching to an existing dtach
    session, ``resume`` is a no-op (claude is already running its
    conversation in that session).

    Returns a shell-style exit code (0 on success).
    """
    claude_args = list(claude_args) if claude_args else []
    if resume:
        # Prepend so it sits before any user-supplied claude args.
        claude_args.insert(0, "--continue")
    session = f"claude-{name}"
    sock = f"/tmp/claude-dtach-{name}.sock"

    dtach = _resolve_dtach()
    if not dtach:
        print("Error: dtach not installed.", file=sys.stderr)
        print("  Debian/Ubuntu: sudo apt install dtach", file=sys.stderr)
        print("  macOS:         brew install dtach", file=sys.stderr)
        print("  Or reinstall aipager so dtach-bin is bundled:", file=sys.stderr)
        print("    uv tool install --reinstall aipager", file=sys.stderr)
        return 1

    sys_prompt = (
        f'Your session name is "{name}". '
        f'When users address you by this name, respond naturally '
        f'-- it is your name in this session.'
    )

    stop = threading.Event()

    if Path(sock).is_socket():
        print(f"dtach session '{session}' exists — attaching...")
        _set_title(name)
        threading.Thread(target=_keep_title, args=(name, stop), daemon=True).start()
        threading.Thread(target=_force_redraw, args=(name,), daemon=True).start()
        try:
            subprocess.run([dtach, "-a", sock, "-r", "winch", "-E"], check=False)
        finally:
            stop.set()
        return 0

    print(f"Starting Claude in dtach session '{session}'...")
    skip_arg = ["--dangerously-skip-permissions"] if skip_perms else []
    spawn = subprocess.run(
        [dtach, "-n", sock, "-Ez",
         "env", f"CLAUDE_DTACH_SESSION={session}",
         "claude", *skip_arg,
         "--append-system-prompt", sys_prompt,
         *claude_args],
        check=False,
    )
    if spawn.returncode != 0:
        print("dtach failed to start session", file=sys.stderr)
        return 1

    for _ in range(10):
        time.sleep(0.3)
        if Path(sock).is_socket():
            break
    if not Path(sock).is_socket():
        print(f"socket {sock} never appeared after launch", file=sys.stderr)
        return 1

    _set_title(name)
    threading.Thread(target=_keep_title, args=(name, stop), daemon=True).start()
    try:
        subprocess.run([dtach, "-a", sock, "-r", "winch", "-E"], check=False)
    finally:
        stop.set()
    return 0
