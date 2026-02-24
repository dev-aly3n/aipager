#!/usr/bin/env python3
"""Claude Code notification hook — fire-and-forget UDP datagram to daemon.

Reads JSON from stdin, detects session name from CLAUDE_DTACH_SESSION env var,
sends datagram to /tmp/claude-remote.sock. No HTTP calls, no file writes, <5ms.
"""

import json
import os
import socket
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

    # Detect session name from env var set by claude-dtach launcher
    session = os.environ.get("CLAUDE_DTACH_SESSION", "")
    if session:
        data["session"] = session

    # Fire-and-forget UDP datagram
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(data).encode(), SOCKET_PATH)
        sock.close()
    except OSError:
        pass  # daemon not running — session_monitor catches it


if __name__ == "__main__":
    main()
