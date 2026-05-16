"""Launch Claude Code inside a named dtach session.

Exposed as the `claude-dtach` console script. Sets CLAUDE_DTACH_SESSION
inside the new session so the aipager hook scripts can identify it.

Usage:
    claude-dtach [-y] <name> [claude args...]

  -y           pass --dangerously-skip-permissions to claude
  <name>       dtach session label (becomes claude-<name>)
  claude args  forwarded to the underlying `claude` command

If the dtach socket already exists, reattaches. Otherwise creates a new
dtach session and attaches.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from aipager import _dtach_redraw


_USAGE = """\
Usage: claude-dtach [-y] <name> [claude args...]

Options:
  -y  Pass --dangerously-skip-permissions to claude

Examples:
  claude-dtach dev              # start claude in dtach session 'claude-dev'
  claude-dtach -y dev           # start with skip-permissions
  claude-dtach auth --resume    # resume session in 'claude-auth'
"""


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


def main() -> int:
    argv = sys.argv[1:]
    skip_perms = False
    if argv and argv[0] == "-y":
        skip_perms = True
        argv = argv[1:]

    if not argv:
        sys.stderr.write(_USAGE)
        return 1

    name = argv[0]
    claude_extra = argv[1:]
    session = f"claude-{name}"
    sock = f"/tmp/claude-dtach-{name}.sock"

    if not shutil.which("dtach"):
        print("Error: dtach not installed.", file=sys.stderr)
        print("  Debian/Ubuntu: sudo apt install dtach", file=sys.stderr)
        print("  macOS:         brew install dtach", file=sys.stderr)
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
            subprocess.run(["dtach", "-a", sock, "-r", "winch", "-E"], check=False)
        finally:
            stop.set()
        return 0

    print(f"Starting Claude in dtach session '{session}'...")
    skip_arg = ["--dangerously-skip-permissions"] if skip_perms else []
    spawn = subprocess.run(
        ["dtach", "-n", sock, "-Ez",
         "env", f"CLAUDE_DTACH_SESSION={session}",
         "claude", *skip_arg,
         "--append-system-prompt", sys_prompt,
         *claude_extra],
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
        subprocess.run(["dtach", "-a", sock, "-r", "winch", "-E"], check=False)
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
