#!/usr/bin/env python3
"""StatusLine hook for Claude Code → aipager.

Reads statusLine JSON from stdin and does three things on every tick:
  1. Writes the raw JSON to /tmp/claude-status-<session>.json
     (hook_receiver reads this for fresh token counts on hook events)
  2. Sends a compact UDP datagram to /tmp/aipager.sock with model/ctx/cost
     (drives real-time updates in the Telegram busy message)
  3. Emits a short status line to stdout (shown in Claude Code's terminal)

Stdlib-only — runs under any Python 3.10+ install. Failure modes are all
swallowed: a missing daemon, bad JSON, or unwritable /tmp must never break
Claude Code's status bar.
"""

import json
import os
import socket
import sys
from pathlib import Path

SOCKET_PATH = "/tmp/aipager.sock"

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[aipager-statusline] {msg}", file=sys.stderr)


def main() -> None:
    raw = sys.stdin.read()
    session = os.environ.get("CLAUDE_DTACH_SESSION", "")

    if raw.strip() and session:
        try:
            Path(f"/tmp/claude-status-{session}.json").write_text(raw)
        except OSError as e:
            _debug(f"could not write /tmp/claude-status-{session}.json: {e}")

        try:
            data = json.loads(raw)
            ctx = data.get("context_window", {}) or {}
            cur = ctx.get("current_usage") or {}
            cost = data.get("cost", {}) or {}
            msg = {
                "type": "statusline",
                "session": session,
                "context_pct": ctx.get("used_percentage", 0),
                "total_output": ctx.get("total_output_tokens", 0),
                "total_input": ctx.get("total_input_tokens", 0),
                "current_output": cur.get("output_tokens", 0),
                "cost_usd": cost.get("total_cost_usd", 0),
                "model_name": (data.get("model") or {}).get("display_name", ""),
                "lines_added": cost.get("total_lines_added", 0),
                "lines_removed": cost.get("total_lines_removed", 0),
            }
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.sendto(json.dumps(msg).encode(), SOCKET_PATH)
            sock.close()
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            _debug(f"statusline forward failed: {type(e).__name__}: {e}")

    try:
        data = json.loads(raw)
        model = (data.get("model") or {}).get("display_name", "claude")
        pct = (data.get("context_window") or {}).get("used_percentage", 0)
        cost = (data.get("cost") or {}).get("total_cost_usd", 0)
        label = session.removeprefix("claude-") if session else ""
        prefix = f"[{label}] " if label else ""
        sys.stdout.write(f"{prefix}{model} | {int(pct)}% ctx | ${cost:.2f}")
    except (json.JSONDecodeError, KeyError, TypeError):
        sys.stdout.write("")


if __name__ == "__main__":
    main()
