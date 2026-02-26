#!/usr/bin/env python3
"""Force a TUI redraw in a dtach session by bouncing the PTY window size.

dtach has no screen buffer — reattaching shows a blank screen.
TUI apps (Ink/React) only redraw on genuine dimension changes due to
three independent same-size guards (Linux kernel, Node.js, Ink).

This script changes the PTY size to (rows-1, cols), waits 50ms, then
restores (rows, cols) — forcing two genuine SIGWINCH signals and a
full redraw.

Usage: dtach-redraw.py <session-name>
  e.g.: dtach-redraw.py jim
"""

import fcntl
import os
import struct
import subprocess
import sys
import termios
import time


def find_pty(session_name: str) -> str | None:
    """Find the PTY slave device for a claude-dtach session's child process."""
    # Find dtach host PID: "dtach -n /tmp/claude-dtach-<name>.sock ..."
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

    # Find first child PID (the claude process)
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

    # Read the PTY slave path from the child's stdout fd
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
        # Get current size
        buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpix, ypix = struct.unpack("HHHH", buf)

        if rows <= 1:
            return False

        # Step 1: shrink by 1 row → kernel sends SIGWINCH (size genuinely changed)
        fcntl.ioctl(
            fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", rows - 1, cols, xpix, ypix),
        )
        time.sleep(0.05)

        # Step 2: restore original → kernel sends SIGWINCH again → Ink redraws correctly
        fcntl.ioctl(
            fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, xpix, ypix),
        )
        return True
    except Exception:
        return False
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <session-name>", file=sys.stderr)
        sys.exit(1)

    pty = find_pty(sys.argv[1])
    if pty and bounce_size(pty):
        sys.exit(0)
    sys.exit(1)
