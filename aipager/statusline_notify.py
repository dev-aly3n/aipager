#!/usr/bin/env python3
"""StatusLine hook notifier — sends token data to daemon via UDP datagram.

Reads Claude Code's statusLine JSON from stdin, adds session info,
sends to /tmp/claude-remote.sock. Runs in <5ms.
"""

import json
import os
import socket
import sys

SOCKET_PATH = "/tmp/claude-remote.sock"


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session = os.environ.get("CLAUDE_DTACH_SESSION", "")
    if not session:
        return

    # Extract just what we need (keep datagram small)
    ctx = data.get("context_window", {})
    cur = ctx.get("current_usage") or {}
    msg = {
        "type": "statusline",
        "session": session,
        "context_pct": ctx.get("used_percentage", 0),
        "total_output": ctx.get("total_output_tokens", 0),
        "total_input": ctx.get("total_input_tokens", 0),
        "current_output": cur.get("output_tokens", 0),  # per-response output
        "cost_usd": data.get("cost", {}).get("total_cost_usd", 0),
        "model_name": data.get("model", {}).get("display_name", ""),
    }

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(msg).encode(), SOCKET_PATH)
        sock.close()
    except OSError:
        pass


if __name__ == "__main__":
    main()
