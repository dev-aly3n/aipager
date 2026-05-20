"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations



from aipager.ui import console

from aipager.wizard.daemon_io import (
    _detect_daemon_running,
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
    """Print a panel summarizing the scopes in aipager.yaml + daemon state."""
    from rich.panel import Panel

    from aipager.scope import ScopeConfigError
    from aipager.wizard.scope_io import read_config

    try:
        scopes, token = read_config()
        cfg_err: str | None = None
    except ScopeConfigError as e:
        scopes, token, cfg_err = [], "", str(e)

    lines: list[str] = []
    if cfg_err:
        lines.append("[err]aipager.yaml malformed:[/err]")
        lines.append(f"   [err]{cfg_err}[/err]")
    elif not scopes:
        lines.append("[muted]No scopes configured yet.[/muted]")
    else:
        for s in scopes:
            n = len(s.members)
            deny = len(s.deny_tools)
            deny_txt = (f" · {deny} deny rule{'s' if deny != 1 else ''}"
                        if deny else "")
            lines.append(
                f'[title]{s.kind}[/title] [path]"{s.label}"[/path] · '
                f"{n} member{'s' if n != 1 else ''}{deny_txt}"
            )
            for m in s.members:
                lines.append(f"   • [path]{m.label}[/path] — {m.role}")

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
