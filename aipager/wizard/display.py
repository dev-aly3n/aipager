"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations



from aipager.ui import console

from aipager.wizard.daemon_io import (
    _detect_daemon_running,
    _read_env_file,
)


def _ask(prompt) -> object:
    """Run a questionary prompt; raise KeyboardInterrupt on Ctrl-C."""
    answer = prompt.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def _spin(message: str):
    """Context manager: spinner if a TTY, plain print otherwise."""
    if console.is_terminal:
        return console.status(f"[muted]{message}[/muted]", spinner="dots")
    # Off-TTY: print a single line and return a no-op context manager.
    console.print(f"  {message}")
    return _NullCtx()


class _NullCtx:
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def _show_current_config() -> None:
    """Print a panel summarizing config.env + team.yaml + daemon state."""
    from rich.panel import Panel
    from aipager.team import TEAM_CONFIG_PATH, TeamConfigError, load_team

    token, chat_id = _read_env_file()
    try:
        team = load_team(TEAM_CONFIG_PATH)
        team_err: str | None = None
    except TeamConfigError as e:
        team = None
        team_err = str(e)

    lines: list[str] = []
    if team is not None:
        lines.append("[title]Mode:[/title]   Team")
        lines.append(f"[title]Chat:[/title]   {chat_id}  ([path]group[/path])")
        lines.append(
            f"[title]Users:[/title]  {len(team.users)} "
            f"({team.admin_count()} admin)"
        )
        for u in team.users.values():
            lines.append(f"          • [path]{u.label}[/path] — {u.role.value}")
        deny = list(team.rules.deny_tools)
        rules_repr = f"deny_tools = {deny}" if deny else "(none)"
        lines.append(f"[title]Rules:[/title]  {rules_repr}")
    elif team_err:
        lines.append("[title]Mode:[/title]   [err]team.yaml malformed[/err]")
        lines.append(f"          [err]{team_err}[/err]")
        lines.append(f"[title]Chat:[/title]   {chat_id}")
    else:
        lines.append("[title]Mode:[/title]   Personal")
        lines.append(f"[title]Chat:[/title]   {chat_id}")

    if token:
        lines.append(f"[title]Token:[/title]  {token[:10]}…")
    else:
        lines.append("[title]Token:[/title]  [err]missing[/err]")

    daemon_pid = _detect_daemon_running()
    if daemon_pid is None:
        lines.append("[title]Daemon:[/title] [muted]not running[/muted]")
    elif daemon_pid > 0:
        lines.append(f"[title]Daemon:[/title] up (PID {daemon_pid})")
    else:
        lines.append("[title]Daemon:[/title] up")

    body = "\n".join(lines)
    if console.is_terminal:
        console.print()
        console.print(Panel(body, title="Current config", border_style="step",
                            expand=False, padding=(0, 1)))
    else:
        console.print("\nCurrent config:")
        console.print(body)
