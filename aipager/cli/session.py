"""`aipager session` subcommand — launch / list / kill dtach sessions."""

from __future__ import annotations

import argparse
import asyncio


def _cmd_session(args: argparse.Namespace) -> int:
    # `aipager session` has a positional `name` that can also be a verb:
    #   aipager session ls / list                       → list all sessions
    #   aipager session kill <name>                     → terminate session
    #   aipager session <name> [claude args...]         → launch / attach
    # The verbs `ls`, `list`, `kill` are reserved as session names by
    # `dtach.launcher._validate_name`, so no collision is possible.
    if args.name in ("ls", "list"):
        return _session_ls(args)
    if args.name == "kill":
        return _session_kill(args)
    from aipager.preflight import require_claude, require_config, require_daemon
    require_config()
    require_claude()
    require_daemon()
    from aipager.dtach.launcher import launch
    return launch(name=args.name, claude_args=args.claude_args or [])


def _session_ls(args: argparse.Namespace) -> int:
    """`aipager session ls [-a|--all] [--json]` — list dtach sessions."""
    import json as _json

    rest = list(args.claude_args or [])
    show_all = bool({"-a", "--all"} & set(rest))
    as_json = "--json" in rest

    from aipager.status import (
        _gather_sessions,
        render_sessions_plain,
        render_sessions_rich,
    )
    sessions, _live = _gather_sessions()
    if not show_all:
        sessions = [s for s in sessions if s["status"] != "GONE"]

    if as_json:
        print(_json.dumps({"sessions": sessions}, indent=2))
        return 0

    from aipager.ui import console
    if console.is_terminal:
        console.print()
        render_sessions_rich(sessions)
        console.print()
    else:
        render_sessions_plain(sessions)
    return 0


def _session_kill(args: argparse.Namespace) -> int:
    """`aipager session kill <name> [-y]` — terminate a dtach session."""
    rest = list(args.claude_args or [])
    force = bool({"-y", "--yes"} & set(rest))
    targets = [a for a in rest if a not in ("-y", "--yes")]
    if not targets:
        from aipager.errors import friendly_error
        friendly_error(
            "usage: aipager session kill <name> [-y]",
            "",
            "  Run `aipager session ls` to see active sessions.",
        )
        return 2
    name = targets[0]
    session = name if name.startswith("claude-") else f"claude-{name}"

    from pathlib import Path
    sock = Path(f"/tmp/claude-dtach-{name.removeprefix('claude-')}.sock")
    if not sock.exists():
        from aipager.errors import friendly_error
        friendly_error(
            f"session {name!r} not found.",
            f"  Expected socket at {sock} but it isn't there.",
            "  Run `aipager session ls` to see what's running.",
        )
        return 1

    if not force:
        answer = input(f"Kill session {name!r}? This will terminate the running "
                       f"claude process. [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            return 0

    from aipager.dtach.inject import kill_session
    from aipager.ui import ok as ui_ok
    killed = asyncio.run(kill_session(session))
    if killed:
        ui_ok(f"killed [path]{session}[/path]")
        return 0
    from aipager.errors import friendly_error
    friendly_error(f"could not kill session {name!r}.")
    return 1
