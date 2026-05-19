#!/usr/bin/env python3
"""Claude Code notification hook — fire-and-forget UDP datagram to daemon.

Reads JSON from stdin, detects session name from CLAUDE_DTACH_SESSION env var,
sends datagram to /tmp/aipager.sock. No HTTP calls, <5ms.

Also reads the statusLine JSON file (written by the statusLine hook) to
piggyback accurate token data on every PreToolUse event. The statusLine
fires right before PreToolUse, so the file is always current.
"""

import json
import os
import socket
import sys
from pathlib import Path

SOCKET_PATH = "/tmp/aipager.sock"

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"


def _debug(msg: str) -> None:
    """Print a diagnostic line to stderr when AIPAGER_DEBUG=1.

    Silent by default so we never inject noise into Claude Code's UI.
    """
    if _DEBUG:
        print(f"[aipager-hook] {msg}", file=sys.stderr)


def _read_statusline_tokens(session: str) -> dict | None:
    """Read token data from the statusLine JSON file for this session."""
    status_file = Path(f"/tmp/claude-status-{session}.json")
    try:
        sl = json.loads(status_file.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
        _debug(f"statusline read failed: {type(e).__name__}: {e}")
        return None
    ctx = sl.get("context_window", {})
    cur = ctx.get("current_usage") or {}
    cost = sl.get("cost", {})
    return {
        "context_pct": ctx.get("used_percentage", 0),
        "total_output": ctx.get("total_output_tokens", 0),
        "total_input": ctx.get("total_input_tokens", 0),
        "current_output": cur.get("output_tokens", 0),
        "lines_added": cost.get("total_lines_added", 0),
        "lines_removed": cost.get("total_lines_removed", 0),
    }


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

    # Piggyback statusLine token data on hook events
    if session:
        tokens = _read_statusline_tokens(session)
        if tokens:
            data["sl_tokens"] = tokens

    # Fire-and-forget UDP datagram
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(data).encode(), SOCKET_PATH)
        sock.close()
    except OSError as e:
        _debug(f"daemon socket {SOCKET_PATH} unreachable: {e}")
        # daemon not running — session_monitor catches it


if __name__ == "__main__":
    main()
