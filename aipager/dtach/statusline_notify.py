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
import resource
import socket
import sys
from pathlib import Path

SOCKET_PATH = "/tmp/aipager.sock"

# Address-space cap for the statusline subprocess. Mirrors notify_hook:
# baseline ~34 MB, 1 GB gives ~30× headroom over realistic legitimate
# use while still catching true runaways.
_MEMORY_CAP_BYTES = 1024 * 1024 * 1024

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[aipager-statusline] {msg}", file=sys.stderr)


def _prepare_cap_notifier(session: str) -> tuple[socket.socket | None, bytes]:
    """Pre-open the daemon socket + pre-serialize the cap-hit payload.
    See :mod:`aipager.dtach.notify_hook._prepare_cap_notifier` for the
    full rationale — this is the same pattern for the statusline hook."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        payload = json.dumps({
            "type": "hook_memory_cap_hit",
            "session": session,
            "hook": "aipager-statusline",
        }).encode()
        return sock, payload
    except (OSError, MemoryError):
        return None, b""


def main() -> None:
    session = os.environ.get("CLAUDE_DTACH_SESSION", "")
    cap_sock, cap_payload = _prepare_cap_notifier(session)

    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (_MEMORY_CAP_BYTES, _MEMORY_CAP_BYTES),
        )
    except (ValueError, OSError):
        pass  # some kernels/containers reject rlimit tightening; never wedge claude

    try:
        _run(session)
    except MemoryError:
        if cap_sock is not None:
            try:
                cap_sock.sendto(cap_payload, SOCKET_PATH)
            except OSError:
                pass
        sys.exit(1)


def _run(session: str) -> None:
    """Statusline hook body — separated so ``main()`` can wrap it in a
    single ``try/except MemoryError``."""
    raw = sys.stdin.read()

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
