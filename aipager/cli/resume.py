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

    Prefix the name with ``!`` to force Auto mode (--dangerously-skip-permissions)
    regardless of the persisted value (e.g. ``aipager resume !dev``).

    The daemon's session_monitor auto-discovers the new socket within ~2s
    and recovers the registry entry to IDLE; we don't write to the state
    file directly here (the daemon owns it).
    """
    raw_name = (args.name or "").strip().lstrip("@/") if args.name else ""
    if raw_name:
        # Support ! prefix to force Auto mode
        force_auto = raw_name.startswith("!")
        name = raw_name.lstrip("!").lower()
        return _resume_one(name, force_auto=force_auto)
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


def _all_records() -> list[dict]:
    """Every persisted session record, live ones included."""
    from aipager.status import _read_state
    persisted = (_read_state().get("sessions", {}) or {}).values()
    return [sd for sd in persisted if sd.get("name")]


def _resolve_record(label: str,
                    records: list[dict]) -> tuple[dict | None, list[str]]:
    """Resolve a user-typed label to one session record.

    Sessions opened from Telegram are named ``claude-<label>__d<chat_id>``,
    so reconstructing ``claude-<label>`` from user input misses them. Match
    on the record instead: exact name first, then the record's own ``label``.

    Returns ``(record, [])`` on a unique hit, ``(None, [names])`` when the
    label is ambiguous across scopes, and ``(None, [])`` when nothing matches.
    """
    named = [sd for sd in records if sd.get("name")]

    exact = f"claude-{label}"
    for sd in named:
        if sd["name"] == exact:
            return sd, []

    by_label = [sd for sd in named if sd.get("label") == label]
    if len(by_label) == 1:
        return by_label[0], []
    if by_label:
        return None, sorted(
            sd["name"].removeprefix("claude-") for sd in by_label
        )
    return None, []


def _resume_one(label: str, *, force_auto: bool = False) -> int:
    """Resume a single session by label. Returns shell exit code.

    ``force_auto`` overrides the persisted ``skip_perms`` and launches
    with ``--dangerously-skip-permissions`` (Auto mode).
    """
    import asyncio as _asyncio
    from pathlib import Path

    from aipager.dtach import inject as dtach_inject
    from aipager.errors import friendly_error
    from aipager.ui import ok as ui_ok

    sd, ambiguous = _resolve_record(label, _gone_history())

    if sd is None:
        if ambiguous:
            friendly_error(
                f"{label!r} matches more than one session:",
                *(f"    {n}" for n in ambiguous),
                "  Re-run with the full name.",
            )
            return 1
        # Not resumable. It may still be running — _gone_history() excludes
        # live sessions — so resolve against the full set to name its socket.
        live_sd, live_ambiguous = _resolve_record(label, _all_records())
        if live_ambiguous:
            friendly_error(
                f"{label!r} matches more than one session:",
                *(f"    {n}" for n in live_ambiguous),
                "  Re-run with the full name.",
            )
            return 1
        live_label = live_sd["name"].removeprefix("claude-") if live_sd else label
        sock = Path(f"/tmp/claude-dtach-{live_label}.sock")
        if sock.is_socket():
            friendly_error(
                f"session {live_label!r} is already running.",
                f"  Socket: {sock}",
                "  Attach it directly:",
                f"    dtach -a {sock}",
            )
            return 1
        friendly_error(
            f"no session named {label!r} in history.",
            "  Run `aipager resume` with no arg to see what's available.",
        )
        return 1

    # Everything downstream keys off the resolved name, never the raw input:
    # the socket and the label dtach launches under both carry the scope suffix.
    full_label = sd["name"].removeprefix("claude-")
    sock = Path(f"/tmp/claude-dtach-{full_label}.sock")

    if sock.is_socket():
        friendly_error(
            f"session {full_label!r} is already running.",
            f"  Socket: {sock}",
            "  Attach it directly:",
            f"    dtach -a {sock}",
        )
        return 1

    resume_id = sd.get("claude_session_id") or ""
    cwd = sd.get("cwd") or ""
    if not resume_id:
        friendly_error(
            f"session {full_label!r} has no resumable transcript on disk.",
            f"  Start a fresh one with: aipager session {label}",
        )
        return 1
    # Use persisted skip_perms unless force_auto overrides.
    skip_perms = force_auto or bool(sd.get("skip_perms", False))
    ok, err = _asyncio.run(dtach_inject.launch_session(
        full_label, resume_id=resume_id, cwd=cwd or None,
        skip_perms=skip_perms, is_relaunch=True,
    ))
    if not ok:
        friendly_error(f"couldn't resume {full_label!r}: {err}")
        return 1
    mode_str = "Auto (--dangerously-skip-permissions)" if skip_perms else "Ask"
    ui_ok(f"resumed [path]{full_label}[/path] "
          f"(session-id {resume_id[:8]}…, mode={mode_str})")
    preview = sd.get("last_assistant_preview") or ""
    if preview:
        from aipager.ui import console
        console.print()
        console.print("[muted]Last response:[/muted]")
        console.print(f"  {preview}")
    print(f"\nAttach with: dtach -a {sock}")
    return 0


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
                # Pass the full name: the picker already knows exactly which
                # record was chosen, so don't re-resolve a possibly ambiguous
                # short label.
                name = chunk[idx].get("name", "")
                if name:
                    return _resume_one(name.removeprefix("claude-"))
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
