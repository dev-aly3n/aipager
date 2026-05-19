"""`aipager resume` — bring back a previously-gone Claude Code session.

CLI mirror of the Telegram `/resume` flow. Reads ``aipager-sessions.json``
directly (the daemon owns writes; reads are safe). Output is a
human-friendly table + an instruction to ``dtach -a`` the resumed socket.
"""

from __future__ import annotations

import argparse

_RESUME_PAGE_SIZE = 10


def _cmd_resume(args: argparse.Namespace) -> int:
    """`aipager resume [<name>]` — resume a previously-gone Claude session.

    With ``<name>``: directly invoke ``claude --resume`` on that session
    via dtach. Without an arg: render a paginated list of GONE sessions
    and accept ``<number>`` / ``n`` (next) / ``p`` (prev) / ``q`` (quit).

    The daemon's session_monitor auto-discovers the new socket within ~2s
    and recovers the registry entry to IDLE; we don't write to the state
    file directly here (the daemon owns it).
    """
    name = (args.name or "").strip().lstrip("@/").lower() if args.name else ""
    if name:
        return _resume_one(name)
    return _resume_picker_loop()


def _gone_history() -> list[dict]:
    """Read the persisted state file, return GONE sessions newest-first."""
    from aipager.status import _live_sessions, _read_state
    state = _read_state()
    persisted = state.get("sessions", {}) or {}
    live = _live_sessions()
    gone = []
    for n, sd in persisted.items():
        if n in live:
            continue
        if not sd.get("gone_at") and not sd.get("claude_session_id"):
            continue
        gone.append(sd)
    gone.sort(key=lambda s: s.get("gone_at") or 0.0, reverse=True)
    return gone


def _resume_one(label: str) -> int:
    """Resume a single session by label. Returns shell exit code."""
    import asyncio as _asyncio
    from pathlib import Path

    from aipager.dtach import inject as dtach_inject
    from aipager.errors import friendly_error
    from aipager.ui import ok as ui_ok

    sock = Path(f"/tmp/claude-dtach-{label}.sock")

    if sock.is_socket():
        friendly_error(
            f"session {label!r} is already running.",
            f"  Socket: {sock}",
            "  Attach it directly:",
            f"    dtach -a {sock}",
        )
        return 1

    session_name = f"claude-{label}"
    for sd in _gone_history():
        if sd.get("name") != session_name:
            continue
        resume_id = sd.get("claude_session_id") or ""
        cwd = sd.get("cwd") or ""
        if not resume_id:
            friendly_error(
                f"session {label!r} has no resumable transcript on disk.",
                f"  Start a fresh one with: aipager session {label}",
            )
            return 1
        ok, err = _asyncio.run(dtach_inject.launch_session(
            label, resume_id=resume_id, cwd=cwd or None,
        ))
        if not ok:
            friendly_error(f"couldn't resume {label!r}: {err}")
            return 1
        ui_ok(f"resumed [path]{label}[/path] (session-id {resume_id[:8]}…)")
        preview = sd.get("last_assistant_preview") or ""
        if preview:
            from aipager.ui import console
            console.print()
            console.print("[muted]Last response:[/muted]")
            console.print(f"  {preview}")
        print(f"\nAttach with: dtach -a {sock}")
        return 0

    friendly_error(
        f"no session named {label!r} in history.",
        "  Run `aipager resume` with no arg to see what's available.",
    )
    return 1


def _resume_picker_loop() -> int:
    """Paginated picker for `aipager resume` with no name argument."""
    gone = _gone_history()
    if not gone:
        from aipager.ui import console
        console.print("[muted]No previous sessions to resume.[/muted]")
        return 0

    page = 0
    page_size = _RESUME_PAGE_SIZE
    total_pages = (len(gone) + page_size - 1) // page_size

    while True:
        start = page * page_size
        chunk = gone[start:start + page_size]
        print()
        print(f"Previous sessions — page {page + 1}/{total_pages} "
              f"({len(gone)} total)")
        print()
        for i, sd in enumerate(chunk, start=1):
            label = sd.get("label", sd.get("name", "?"))
            gone_at = sd.get("gone_at")
            ago = _fmt_ago_cli(gone_at)
            preview = sd.get("last_assistant_preview") or ""
            row = f"  {i:>2}. {label:<20} {ago}"
            if preview:
                snippet = preview[:60] + ("…" if len(preview) > 60 else "")
                row += f"  — {snippet}"
            print(row)
        print()
        prompt = "Pick a number"
        if page > 0:
            prompt += ", [p]rev"
        if page < total_pages - 1:
            prompt += ", [n]ext"
        prompt += ", [q]uit: "
        try:
            choice = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("q", "quit", ""):
            return 0
        if choice in ("n", "next"):
            if page < total_pages - 1:
                page += 1
            continue
        if choice in ("p", "prev"):
            if page > 0:
                page -= 1
            continue
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(chunk):
                label = chunk[idx].get("label", "")
                if label:
                    return _resume_one(label)
        # Unrecognized input — re-loop with the same page


def _fmt_ago_cli(gone_at) -> str:
    """Short relative timestamp for the CLI picker."""
    import time as _t
    try:
        gone_at = float(gone_at) if gone_at else 0.0
    except (TypeError, ValueError):
        return "?"
    if not gone_at:
        return "?"
    delta = max(0, int(_t.time() - gone_at))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
