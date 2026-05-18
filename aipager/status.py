"""Quick snapshot of daemon + sessions state for `aipager status`.

Read-only; never makes Telegram API calls. Pulls everything from local
files written by the daemon and Claude Code's statusLine hook:

- `/tmp/aipager.sock`          daemon liveness probe
- `/tmp/claude-dtach-*.sock`   live dtach sessions
- `~/.claude/aipager-sessions.json`  persisted session state
- `/tmp/claude-status-claude-{label}.json`  live per-session stats

Exit codes:
  0  daemon is up
  1  daemon socket missing or not reachable
  2  config missing (no BOT_TOKEN / CHAT_ID)
"""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from aipager.config import BOT_TOKEN, CHAT_ID, SESSION_STATE_FILE, SOCKET_PATH
from aipager.errors import friendly_error
from aipager.ui import console


def _read_state() -> dict:
    """Return the parsed state file, or {} on any read failure."""
    path = Path(SESSION_STATE_FILE)
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return {}


def _read_statusline(session_name: str) -> dict:
    """Return parsed `/tmp/claude-status-{session}.json` or {}."""
    path = Path(f"/tmp/claude-status-{session_name}.json")
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return {}


def _live_sessions() -> set[str]:
    """Names (``claude-<label>``) of dtach sessions with a live socket file."""
    out: set[str] = set()
    for sock in Path("/tmp").glob("claude-dtach-*.sock"):
        name = "claude-" + sock.stem.removeprefix("claude-dtach-")
        out.add(name)
    return out


def _daemon_alive() -> bool:
    """Datagram-probe the daemon socket. True iff something is listening."""
    p = Path(SOCKET_PATH)
    if not p.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.sendto(b'{"event":"_status_ping"}', SOCKET_PATH)
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    finally:
        s.close()


def _gather_sessions() -> tuple[list[dict], set[str]]:
    """Returns (session_dicts, live_names).

    Each session_dict has: name, label, status, model, context_pct,
    cost_usd, queue_depth.
    """
    state = _read_state()
    persisted = state.get("sessions", {}) or {}
    live = _live_sessions()

    rows: list[dict] = []
    seen_names: set[str] = set()

    # First pass: emit a row for every persisted session
    for name, sess in persisted.items():
        label = sess.get("label", name.removeprefix("claude-"))
        is_alive = name in live
        busy_msg_id = sess.get("busy_msg_id")
        if not is_alive:
            status = "GONE"
        elif busy_msg_id:
            status = "BUSY"
        else:
            status = "IDLE"
        sl = _read_statusline(name) if is_alive else {}
        ctx = sl.get("context_window", {}) or {}
        cost = sl.get("cost", {}) or {}
        rows.append({
            "name": name,
            "label": label,
            "status": status,
            "model": ((sl.get("model") or {}).get("display_name")
                      or sess.get("model_name") or ""),
            "context_pct": ctx.get("used_percentage"),
            "cost_usd": cost.get("total_cost_usd"),
            "queue_depth": len(sess.get("pending_queue") or []),
        })
        seen_names.add(name)

    # Second pass: emit any live socket that's not in the registry yet
    for name in sorted(live - seen_names):
        sl = _read_statusline(name)
        ctx = sl.get("context_window", {}) or {}
        cost = sl.get("cost", {}) or {}
        rows.append({
            "name": name,
            "label": name.removeprefix("claude-"),
            "status": "IDLE",  # alive but undiscovered → most likely just spawned
            "model": (sl.get("model") or {}).get("display_name", ""),
            "context_pct": ctx.get("used_percentage"),
            "cost_usd": cost.get("total_cost_usd"),
            "queue_depth": 0,
        })

    # Stable order: live first (alphabetical by label), then gone
    def _sort_key(r):
        return (r["status"] == "GONE", r["label"])
    rows.sort(key=_sort_key)
    return rows, live


_STATUS_STYLE = {
    "IDLE": ("ok", "✓"),
    "BUSY": ("step", "⚙"),
    "INTERACTIVE": ("warn", "?"),
    "GONE": ("warn", "⚠"),
}


def _render_rich(daemon_up: bool, sessions: list[dict], total_cost: float) -> None:
    from rich.table import Table

    console.print()
    if daemon_up:
        console.print(
            f"  [ok]✓[/ok]  daemon            "
            f"[hint]chat {CHAT_ID}[/hint]"
        )
    else:
        console.print(
            "  [err]✗[/err]  daemon            "
            "[hint]not running[/hint]"
        )

    if sessions:
        console.print()
        t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
        t.add_column(width=3, justify="center")
        t.add_column(no_wrap=True)
        t.add_column(no_wrap=True)
        t.add_column(style="hint")
        for s in sessions:
            style, glyph = _STATUS_STYLE.get(s["status"], ("muted", "·"))
            metrics_parts: list[str] = []
            if s["model"]:
                metrics_parts.append(s["model"])
            if s["context_pct"] is not None:
                metrics_parts.append(f"{int(s['context_pct'])}% ctx")
            if s["cost_usd"] is not None:
                metrics_parts.append(f"${s['cost_usd']:.2f}")
            if s["queue_depth"]:
                metrics_parts.append(f"queue {s['queue_depth']}")
            t.add_row(
                f"[{style}]{glyph}[/{style}]",
                s["label"],
                s["status"],
                "  ·  ".join(metrics_parts),
            )
        console.print(t)

    if total_cost > 0:
        console.print()
        console.print(f"  [muted]total cost[/muted]  ${total_cost:.2f}")
    console.print()


def _render_plain(daemon_up: bool, sessions: list[dict], total_cost: float) -> None:
    line = "daemon: " + ("up" if daemon_up else "not running")
    if daemon_up:
        line += f" (chat {CHAT_ID})"
    console.print(line)
    for s in sessions:
        parts = [s["label"], s["status"]]
        if s["model"]:
            parts.append(s["model"])
        if s["context_pct"] is not None:
            parts.append(f"{int(s['context_pct'])}% ctx")
        if s["cost_usd"] is not None:
            parts.append(f"${s['cost_usd']:.2f}")
        if s["queue_depth"]:
            parts.append(f"queue:{s['queue_depth']}")
        console.print("  " + "  ".join(parts))
    if total_cost > 0:
        console.print(f"  total cost: ${total_cost:.2f}")


def cmd_status(args: argparse.Namespace | None = None) -> int:
    """Entry point for `aipager status`.

    With ``args.json`` (or ``args.as_json``) true, emit JSON instead
    of a rendered table.
    """
    as_json = bool(getattr(args, "as_json", False))

    if not BOT_TOKEN or not CHAT_ID:
        if as_json:
            print(json.dumps({
                "error": "config missing",
                "missing": [k for k, v in
                            (("CLAUDE_TG_BOT_TOKEN", BOT_TOKEN),
                             ("CLAUDE_TG_CHAT_ID", CHAT_ID)) if not v],
            }))
        else:
            friendly_error(
                "aipager isn't configured yet.",
                "  Run `aipager config` first.",
            )
        return 2

    daemon_up = _daemon_alive()
    sessions, _live = _gather_sessions()
    total_cost = sum((s["cost_usd"] or 0.0) for s in sessions)

    if as_json:
        print(json.dumps({
            "daemon": {"up": daemon_up, "chat_id": CHAT_ID},
            "sessions": sessions,
            "total_cost_usd": round(total_cost, 4),
        }, indent=2))
    elif console.is_terminal:
        _render_rich(daemon_up, sessions, total_cost)
    else:
        _render_plain(daemon_up, sessions, total_cost)

    return 0 if daemon_up else 1


__all__ = ["cmd_status"]
