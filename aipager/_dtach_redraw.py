"""Force a TUI redraw in a dtach session by bouncing the PTY window size.

dtach has no screen buffer — reattaching shows a blank screen. TUI apps
(Ink/React) only redraw on genuine dimension changes due to three
independent same-size guards (Linux kernel, Node.js, Ink).

`redraw(name)` changes the PTY size to (rows-1, cols), waits 50ms, then
restores (rows, cols) — forcing two genuine SIGWINCH signals and a full
redraw.
"""

from __future__ import annotations

import fcntl
import os
import struct
import subprocess
import sys
import termios
import time


def find_pty(session_name: str) -> str | None:
    """Find the PTY slave device for a claude-dtach session's child process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"dtach -n.*/claude-dtach-{session_name}\\.sock"],
            capture_output=True, text=True, timeout=5,
        )
        dtach_pid = result.stdout.strip().split("\n")[0]
        if not dtach_pid:
            return None
    except Exception:
        return None

    try:
        result = subprocess.run(
            ["pgrep", "-P", dtach_pid],
            capture_output=True, text=True, timeout=5,
        )
        child_pid = result.stdout.strip().split("\n")[0]
        if not child_pid:
            return None
    except Exception:
        return None

    try:
        pty = os.readlink(f"/proc/{child_pid}/fd/1")
        if pty.startswith("/dev/pts/"):
            return pty
    except Exception:
        pass
    return None


def bounce_size(pty_path: str) -> bool:
    """Bounce PTY dimensions: (rows-1) then restore. Triggers two SIGWINCHs."""
    try:
        fd = os.open(pty_path, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    try:
        buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpix, ypix = struct.unpack("HHHH", buf)
        if rows <= 1:
            return False
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows - 1, cols, xpix, ypix))
        time.sleep(0.05)
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, xpix, ypix))
        return True
    except Exception:
        return False
    finally:
        os.close(fd)


def redraw(session_name: str) -> bool:
    """Bounce PTY size for the given session — returns True on success."""
    pty = find_pty(session_name)
    if not pty:
        return False
    return bounce_size(pty)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <session-name>", file=sys.stderr)
        return 1
    return 0 if redraw(sys.argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main())
