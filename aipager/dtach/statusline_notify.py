#!/usr/bin/env python3
"""StatusLine hook for Claude Code → aipager.

Reads statusLine JSON from stdin and does three things on every tick:
  1. Writes the raw JSON to /tmp/claude-status-<session>.json (or under
     $AIPAGER_INSTANCE_DIR for an isolated instance)
     (hook_receiver reads this for fresh token counts on hook events)
  2. Sends a compact UDP datagram to the daemon control socket
     (see SOCKET_PATH below) with model/ctx/cost
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

# Same precedence as aipager.instance.control_socket_path() — kept
# inlined and stdlib-only, see notify_hook.py's identical comment for why.
# An isolated instance (AIPAGER_INSTANCE_DIR) wins first.
# NOTE: bind the *stripped* runtime dir once and use that same value.
# Reading os.environ["XDG_RUNTIME_DIR"] unstripped while guarding on the
# stripped copy meant a padded value produced a path the daemon never
# bound, silently dropping every hook event.
_INSTANCE_DIR = os.environ.get("AIPAGER_INSTANCE_DIR", "").strip()
_INSTANCE_DIR = os.path.normpath(_INSTANCE_DIR) if _INSTANCE_DIR else ""
_XDG_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "").strip()
SOCKET_PATH = (
    (os.path.join(_INSTANCE_DIR, "aipager.sock") if _INSTANCE_DIR else "")
    or os.environ.get("AIPAGER_SOCKET_PATH", "").strip()
    or (os.path.join(_XDG_RUNTIME_DIR, "aipager.sock") if _XDG_RUNTIME_DIR else "")
    or "/tmp/aipager.sock"
)
# Where the per-session status file goes: aipager.instance.runtime_tmp_dir(),
# inlined (aipager.statusline_file.status_file_path reads the same place).
_STATUS_DIR = _INSTANCE_DIR or "/tmp"

# Address-space cap for the statusline subprocess. Mirrors notify_hook:
# baseline ~34 MB, 1 GB gives ~30× headroom over realistic legitimate
# use while still catching true runaways.
_MEMORY_CAP_BYTES = 1024 * 1024 * 1024

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"

# This status line's own aipager release (see notify_hook.py).
try:
    from aipager._version import __version__ as _AIPAGER_VERSION
except Exception:
    _AIPAGER_VERSION = ""


def _debug(msg: str) -> None:
    if _DEBUG:
        print(f"[aipager-statusline] {msg}", file=sys.stderr)


def _prepare_cap_notifier(session: str, bg_job: str = "") -> tuple[socket.socket | None, bytes]:
    """Pre-open the daemon socket + pre-serialize the cap-hit payload.
    See :mod:`aipager.dtach.notify_hook._prepare_cap_notifier` for the
    full rationale — this is the same pattern for the statusline hook."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        payload = {
            "type": "hook_memory_cap_hit",
            "session": session,
            "hook": "aipager-statusline",
        }
        if bg_job:
            payload["bg_job"] = bg_job
        return sock, json.dumps(payload).encode()
    except (OSError, MemoryError):
        return None, b""


def _resolve_worker(inherited: str) -> dict | None:
    """As :func:`aipager.dtach.notify_hook._resolve_worker`: None for the
    session's own claude, else the background worker's record (any
    failure is foreign)."""
    if not os.environ.get("CLAUDE_JOB_DIR", "").strip():
        return None
    try:
        from aipager.dtach import bg_jobs
        return bg_jobs.resolve_from_env(inherited)
    except Exception:
        return {"role": "foreign", "job": "", "session": inherited}


def main() -> None:
    session = os.environ.get("CLAUDE_DTACH_SESSION", "")
    # A background worker's status line: a linked one is the session
    # parked on its job, a foreign one writes no file and sends nothing
    # (its line is still printed for Claude Code's own status bar).
    worker = _resolve_worker(session)
    foreign = worker is not None and worker.get("role") != "linked"
    bg_job = ""
    if worker is not None and not foreign:
        session, bg_job = worker["session"], worker.get("job", "")
    if foreign:
        cap_sock, cap_payload = None, b""
    else:
        cap_sock, cap_payload = _prepare_cap_notifier(session, bg_job)

    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (_MEMORY_CAP_BYTES, _MEMORY_CAP_BYTES),
        )
    except (ValueError, OSError):
        pass  # some kernels/containers reject rlimit tightening; never wedge claude

    try:
        _run(session, bg_job=bg_job, foreign=foreign)
    except MemoryError:
        if cap_sock is not None:
            try:
                cap_sock.sendto(cap_payload, SOCKET_PATH)
            except OSError:
                pass
        sys.exit(1)


def _run(session: str, *, bg_job: str = "", foreign: bool = False) -> None:
    """Statusline hook body — separated so ``main()`` can wrap it in a
    single ``try/except MemoryError``. A foreign background worker only
    prints its line; a linked one's datagram carries ``bg_job``."""
    raw = sys.stdin.read()

    if raw.strip() and session and not foreign:
        status_file = Path(_STATUS_DIR) / f"claude-status-{session}.json"
        try:
            status_file.write_text(raw)
        except OSError as e:
            _debug(f"could not write {status_file}: {e}")

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
                # Claude Code's conversation id: compaction keeps it, /clear
                # and a new conversation change it (the context notice is
                # sent once per conversation).
                "session_id": data.get("session_id") or "",
            }
            if _AIPAGER_VERSION:
                msg["aipager_version"] = _AIPAGER_VERSION
            if bg_job:
                msg["bg_job"] = bg_job
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
