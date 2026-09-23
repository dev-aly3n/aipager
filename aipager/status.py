"""Quick snapshot of daemon + sessions state for `aipager status`.

Read-only; never makes Telegram API calls. Pulls everything from local
files written by the daemon and Claude Code's statusLine hook:

- `$XDG_RUNTIME_DIR/aipager.sock`  daemon liveness probe (see config.SOCKET_PATH)
- `$XDG_RUNTIME_DIR/aipager-flood-mute.json`  chats the daemon is
  flood-muting (see config.FLOOD_MUTE_FILE, bot/flood.py)
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
import datetime as _dt
import json
import socket
import time
from pathlib import Path

from aipager.config import BOT_TOKEN, CHAT_ID, SESSION_STATE_FILE, SOCKET_PATH
from aipager.flood_policy import (
    bans_within,
    clamp_warned_until,
    effective_ceiling,
    hourly_limits,
)
from aipager.errors import friendly_error, friendly_warn
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


def read_flood_mutes(path: str | None = None) -> list[dict]:
    """Chats the daemon is flood-muting right now (``bot/flood.py``).

    Read from the signal file the daemon drops beside its socket:
    ``[{"chat_id", "until"}]`` with ``until`` on the wall clock, lapsed
    entries dropped. ``[]`` when the file is missing, unreadable or
    malformed — a mute is never inferred. The path is read late from
    ``config`` so tests (and the conftest isolation fixture) can move it.
    """
    from aipager import config

    try:
        data = json.loads(Path(path or config.FLOOD_MUTE_FILE).read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return []
    entries = data.get("muted") if isinstance(data, dict) else None
    now = time.time()
    out: list[dict] = []
    for entry in entries or []:
        try:
            until = float(entry["until"])
        except (KeyError, TypeError, ValueError):
            continue
        if until > now:
            out.append({"chat_id": entry.get("chat_id"), "until": until})
    return out


def flood_mute_lines(mutes: list[dict]) -> list[str]:
    """One line per muted chat: ``Telegram flood-muted until HH:MM (chat X)``."""
    return [
        "Telegram flood-muted until "
        f"{_dt.datetime.fromtimestamp(m['until']).strftime('%H:%M')} "
        f"(chat {m['chat_id']})"
        for m in mutes
    ]


def read_flood_chats(path: str | None = None) -> list[dict]:
    """Per-chat flood state from the DURABLE file (roadmap 8.28/R9).

    A different file from the two signals above, and deliberately: those
    live on tmpfs and are unlinked whenever nothing is muted or backing
    off, so a HEALTHY chat's earned rate — the thing this is mostly for —
    would never appear in them. This one is under ``$HOME``, is written
    debounced by the daemon and survives both a restart and a reboot.

    Each row: ``chat_id``, ``rate`` (the learned calls/s), ``sustained_used``
    / ``sustained_limit`` (the rolling volume window), ``minimal``,
    ``bans_today``, ``muted_until`` (wall clock, omitted once lapsed) and
    ``stale``. Since 8.30 also: ``hourly_used`` / ``hourly_budget`` (calls
    in the last hour, summed here from the persisted per-minute buckets
    against NOW, and the chat's hourly budget), ``hourly_age``
    (seconds since the file was written — the hour is at most a minute
    stale while calls flow; ``None`` when unknown), ``warning_remaining``
    (seconds left in the post-429 warning regime, clamped to one regime),
    ``bans_7d`` and ``ceiling`` (the rate ceiling those imply). Those are
    computed through ``aipager.flood_policy``, the daemon's own
    arithmetic.

    ``stale`` is True when the document is older than the sustained
    window, in which case the sustained figures describe a window that has
    already rolled and MUST be ignored. Computed here rather than stored,
    because the file is written at most once every few seconds and a
    stored flag would be wrong the moment after it was written.

    ``[]`` for a missing, unreadable, malformed or wrong-``version`` file:
    flood state is never INFERRED, and no state is the normal condition of
    a healthy install that has never been rate-limited.
    """
    from aipager import config

    try:
        data = json.loads(Path(path or config.FLOOD_STATE_FILE).read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict) or data.get("version") != 1:
        return []
    entries = data.get("chats")
    if not isinstance(entries, list):
        return []
    now = time.time()
    try:
        age = max(now - float(data.get("written_at", 0.0)), 0.0)
    except (TypeError, ValueError):
        age = float("inf")
    stale = age > config.FLOOD_SUSTAINED_WINDOW
    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or "chat_id" not in entry:
            continue
        stamps = entry.get("ban_stamps")
        bans_7d = bans_within(stamps, now, config.FLOOD_BAN_MEMORY_DAYS * 86400.0)
        # Clamped to one regime by the daemon's own rule (8.30): a file
        # written before a clock jump must not read "8333h left" here
        # while the daemon, restoring the same file, holds six hours.
        warned = max(clamp_warned_until(entry.get("warned_until"), now) - now,
                     0.0)
        row = {
            "chat_id": entry.get("chat_id"),
            "rate": _float_or(entry.get("rate"), config.FLOOD_START_RATE),
            "sustained_used": int(_float_or(entry.get("sustained_used"), 0)),
            "sustained_limit": int(
                _float_or(entry.get("sustained_limit"),
                          config.FLOOD_SUSTAINED_MAX)),
            "minimal": bool(entry.get("minimal")),
            "bans_today": _bans_today(stamps, now),
            "stale": stale,
            "hourly_used": _hourly_used(entry.get("hourly"), now),
            "hourly_budget": hourly_limits(bans_7d)[0],
            "hourly_age": age if age != float("inf") else None,
            "warning_remaining": warned,
            "bans_7d": bans_7d,
            "ceiling": effective_ceiling(
                max_rate=config.TELEGRAM_PRIVATE_MAX_RATE,
                min_rate=config.FLOOD_MIN_RATE, bans=bans_7d,
                warned=warned > 0.0,
                warned_ceiling=config.FLOOD_WARNED_CEILING),
        }
        muted_until = _float_or(entry.get("muted_until"), 0.0)
        if muted_until > now:
            row["muted_until"] = muted_until
        out.append(row)
    return out


def _float_or(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _hourly_used(buckets, now: float) -> int:
    """Calls in the last hour, from the persisted ``[wall minute start,
    ornament, essential]`` buckets — summed HERE, against the reader's
    own clock, so a file written a minute ago still reports the hour
    ending now. Tolerant of anything a file can hold: junk entries are
    skipped, counts are floored at zero."""
    import math

    if not isinstance(buckets, list):
        return 0
    total = 0
    for item in buckets:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        try:
            start, ornament, essential = (float(v) for v in item)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (start, ornament, essential)):
            continue
        if now - start < 3600.0:
            total += int(max(ornament, 0.0)) + int(max(essential, 0.0))
    return total


def _bans_today(stamps, now: float) -> int:
    """The 24-hour DISPLAY count, through the same arithmetic the daemon
    uses (``flood_policy.bans_within``) rather than a private copy of it —
    the copy that used to live here would have silently disagreed with
    the daemon the day its memory moved to seven days (8.30)."""
    return bans_within(stamps, now, 86400.0)


def flood_chat_lines(chats: list[dict]) -> list[str]:
    """One line per chat, sorted by chat id so the output is stable.

    Reads as a sentence rather than a dump, because the audience is an
    operator asking "why is my bot slow": the rate and the ceiling it may
    climb to, then the volume — the last hour against the chat's hourly
    budget (8.30), labelled with how old the figure is, then the last
    minute — then whatever is wrong. The minute figures are omitted
    entirely when the document is stale — showing a window that has
    already rolled is worse than showing nothing.
    """
    lines: list[str] = []
    for chat in sorted(chats, key=lambda c: str(c.get("chat_id"))):
        parts = [f"Telegram chat {chat['chat_id']}: "
                 f"rate {chat['rate']:.2f}/s"]
        if chat.get("ceiling") is not None:
            parts.append(f" (ceiling {chat['ceiling']:.2f}/s)")
        if chat.get("hourly_budget") is not None:
            parts.append(f", {chat.get('hourly_used', 0)}/{chat['hourly_budget']} "
                         "calls in the last hour")
            if chat.get("hourly_age") is not None:
                parts.append(f" (as of {int(chat['hourly_age'])}s ago)")
        if not chat.get("stale"):
            parts.append(
                f", {chat['sustained_used']}/{chat['sustained_limit']} "
                "in the last minute")
        if chat.get("minimal"):
            parts.append(" — MINIMAL MODE, card updates paused")
        if chat.get("muted_until"):
            parts.append(
                " — flood-muted until "
                f"{_dt.datetime.fromtimestamp(chat['muted_until']).strftime('%H:%M')}")
        remaining = chat.get("warning_remaining") or 0.0
        if remaining > 0.0:
            minutes = int(remaining // 60)
            parts.append(f" — warning regime, {minutes // 60}h {minutes % 60}m left")
        if chat.get("bans_7d"):
            parts.append(f" ({chat['bans_7d']} ban(s) in the last 7 days)")
        lines.append("".join(parts))
    return lines


def read_flood_backoffs(path: str | None = None) -> list[dict]:
    """Chats whose card cadence is currently backed off after a 429
    (``bot/flood_budget.py``, roadmap 8.21).

    Read from the daemon's backoff signal file — a DIFFERENT file from the
    mute's, because ``flood.py`` unlinks that one whenever no mute is
    active. Entries without a numeric ``multiplier`` over 1 are dropped; a
    backoff is never inferred, and ``[]`` is returned when the file is
    missing, unreadable or malformed.

    ``last_429_ago`` is computed HERE, at read time, from the stored wall
    stamp: the file is rewritten at most once every 5 s, so a stored age
    would be stale by the time anyone read it.
    """
    from aipager import config

    try:
        data = json.loads(Path(path or config.FLOOD_BACKOFF_FILE).read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return []
    entries = data.get("backoff") if isinstance(data, dict) else None
    now = time.time()
    out: list[dict] = []
    for entry in entries or []:
        try:
            multiplier = float(entry["multiplier"])
            last_429_at = float(entry.get("last_429_at", now))
        except (KeyError, TypeError, ValueError):
            continue
        if multiplier > 1:
            out.append({
                "chat_id": entry.get("chat_id"),
                "multiplier": multiplier,
                "last_429_ago": max(now - last_429_at, 0.0),
            })
    return out


def flood_backoff_lines(backoffs: list[dict]) -> list[str]:
    """One line per backing-off chat: ``Telegram flood backoff ×4 (chat
    123), last 429 12 s ago``. The multiplier loses a trailing ``.0``."""
    return [
        f"Telegram flood backoff ×{b['multiplier']:g} (chat {b['chat_id']}), "
        f"last 429 {int(b['last_429_ago'])} s ago"
        for b in backoffs
    ]


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


def render_sessions_rich(sessions: list[dict]) -> None:
    """Render the session list as a rich Table to console (TTY path)."""
    from rich.table import Table

    if not sessions:
        console.print("  [muted](no sessions)[/muted]")
        return
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


def render_sessions_plain(sessions: list[dict]) -> None:
    """Render the session list as padded plain text (off-TTY path)."""
    if not sessions:
        console.print("  (no sessions)")
        return
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


def _render_rich(daemon_up: bool, sessions: list[dict], total_cost: float,
                 mutes: list[dict] | None = None,
                 backoffs: list[dict] | None = None,
                 flood_chats: list[dict] | None = None) -> None:
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
    for line in flood_mute_lines(mutes or []):
        console.print(f"  [warn]⚠[/warn]  [warn]{line}[/warn]")
    for line in flood_backoff_lines(backoffs or []):
        console.print(f"  [warn]⚠[/warn]  [warn]{line}[/warn]")
    for line in flood_chat_lines(flood_chats or []):
        console.print(f"  [hint]·[/hint]  [hint]{line}[/hint]")

    if sessions:
        console.print()
        render_sessions_rich(sessions)

    if total_cost > 0:
        console.print()
        console.print(f"  [muted]total cost[/muted]  ${total_cost:.2f}")
    console.print()


def _render_plain(daemon_up: bool, sessions: list[dict], total_cost: float,
                  mutes: list[dict] | None = None,
                  backoffs: list[dict] | None = None,
                  flood_chats: list[dict] | None = None) -> None:
    line = "daemon: " + ("up" if daemon_up else "not running")
    if daemon_up:
        line += f" (chat {CHAT_ID})"
    console.print(line)
    for mute_line in flood_mute_lines(mutes or []):
        console.print(mute_line)
    for backoff_line in flood_backoff_lines(backoffs or []):
        console.print(backoff_line)
    for chat_line in flood_chat_lines(flood_chats or []):
        console.print(chat_line)
    render_sessions_plain(sessions)
    if total_cost > 0:
        console.print(f"  total cost: ${total_cost:.2f}")


def cmd_status(args: argparse.Namespace | None = None) -> int:
    """Entry point for `aipager status`.

    With ``args.json`` (or ``args.as_json``) true, emit JSON instead
    of a rendered table.
    """
    as_json = bool(getattr(args, "as_json", False))
    # Read late: `config` degrades instead of raising, and tests patch it.
    from aipager.config import CONFIG_ERROR

    if not BOT_TOKEN or not CHAT_ID:
        if as_json:
            print(json.dumps({
                "error": "config malformed" if CONFIG_ERROR else "config missing",
                "config_error": CONFIG_ERROR,
                "missing": [k for k, v in
                            (("CLAUDE_TG_BOT_TOKEN", BOT_TOKEN),
                             ("CLAUDE_TG_CHAT_ID", CHAT_ID)) if not v],
            }))
        elif CONFIG_ERROR:
            friendly_error(
                "aipager's config file is malformed.",
                f"  {CONFIG_ERROR}",
                "  Run `aipager doctor` for the full diagnosis.",
            )
        else:
            friendly_error(
                "aipager isn't configured yet.",
                "  Run `aipager config` first.",
            )
        return 2

    daemon_up = _daemon_alive()
    sessions, _live = _gather_sessions()
    total_cost = sum((s["cost_usd"] or 0.0) for s in sessions)
    mutes = read_flood_mutes()
    backoffs = read_flood_backoffs()
    flood_chats = read_flood_chats()

    if as_json:
        print(json.dumps({
            "daemon": {"up": daemon_up, "chat_id": CHAT_ID},
            "config_error": CONFIG_ERROR,
            "flood_muted": mutes,
            "flood_backoff": backoffs,
            "flood_chats": flood_chats,
            "sessions": sessions,
            "total_cost_usd": round(total_cost, 4),
        }, indent=2))
        return 0 if daemon_up else 1

    if CONFIG_ERROR:
        friendly_warn(
            "aipager's config file is malformed.",
            f"  {CONFIG_ERROR}",
            "  The daemon will refuse to start until this is fixed.",
            "",
        )
    if console.is_terminal:
        _render_rich(daemon_up, sessions, total_cost, mutes,
                     backoffs=backoffs, flood_chats=flood_chats)
    else:
        _render_plain(daemon_up, sessions, total_cost, mutes,
                      backoffs=backoffs, flood_chats=flood_chats)

    return 0 if daemon_up else 1


__all__ = [
    "cmd_status",
    "flood_backoff_lines",
    "flood_chat_lines",
    "flood_mute_lines",
    "read_flood_backoffs",
    "read_flood_chats",
    "read_flood_mutes",
    "render_sessions_rich",
    "render_sessions_plain",
    "_gather_sessions",
]
