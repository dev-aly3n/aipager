#!/usr/bin/env python3
"""Claude Code notification hook — fire-and-forget UDP datagram to daemon.

Reads JSON from stdin, detects tmux session, sends datagram to
/tmp/claude-remote.sock. No HTTP calls, no file writes, <5ms.
"""

import json
import os
import socket
import subprocess
import sys

SOCKET_PATH = "/tmp/claude-remote.sock"


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    # Detect tmux session name
    if os.environ.get("TMUX"):
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "#{session_name}"],
                capture_output=True, text=True, timeout=2,
            )
            data["tmux_session"] = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    # Fire-and-forget UDP datagram
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(data).encode(), SOCKET_PATH)
        sock.close()
    except OSError:
        pass  # daemon not running — pane_monitor catches it in ≤2s


if __name__ == "__main__":
    main()
