"""User-facing console output — single source of truth for color, theme,
and TTY detection.

Restrained aesthetic in the spirit of `cargo` / `uv`: subtle color
(green for success, yellow for warnings, red for errors, dim for
secondary text), no ASCII logo, minimal emoji beyond ✓ ⚠ ✗.

Rules:
- Errors and warnings go to **stderr**. Status and success messages go
  to **stdout**.
- When stdout/stderr is **not a TTY**, rich strips color and renders
  panels as plain text. The user never sees raw ANSI codes in logs,
  pipes, or CI.
- ``NO_COLOR`` / ``FORCE_COLOR`` / ``CLICOLOR`` / ``CLICOLOR_FORCE`` /
  ``TERM=dumb`` are all respected.
- This module is **CLI-only**. The long-running daemon
  (``telegram_bot``, ``hook_receiver``) keeps using ``logging`` with
  plain output so journald/launchd stay scrapeable.
"""

from __future__ import annotations

import os
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

# Restrained palette — three accent colours plus dim.
THEME = Theme({
    "ok": "green",
    "warn": "yellow",
    "err": "red",
    "hint": "dim",
    "title": "bold",
    "path": "cyan",
    "step": "bold cyan",
    "muted": "dim",
})

# Glyphs. Centralised so we can swap to ASCII fallbacks if needed.
GLYPH_OK = "✓"
GLYPH_WARN = "⚠"
GLYPH_ERR = "✗"
GLYPH_ARROW = "→"
GLYPH_BULLET = "•"


def _resolve_color_kwargs() -> dict:
    """Compute ``Console(...)`` kwargs from CLI color env-vars.

    Rich already honors NO_COLOR / FORCE_COLOR / TERM=dumb. We add
    bixense.com's CLICOLOR / CLICOLOR_FORCE for completeness.
    """
    kwargs: dict = {}
    # CLICOLOR=0 → force-off (NO_COLOR equivalent)
    clicolor = os.environ.get("CLICOLOR", "")
    if clicolor == "0":
        kwargs["no_color"] = True
    # CLICOLOR_FORCE=<truthy> → force-on (FORCE_COLOR equivalent)
    clicolor_force = os.environ.get("CLICOLOR_FORCE", "")
    if clicolor_force and clicolor_force != "0":
        kwargs["force_terminal"] = True
    return kwargs


_color_kwargs = _resolve_color_kwargs()

console = Console(theme=THEME, highlight=False, **_color_kwargs)
err_console = Console(theme=THEME, highlight=False, stderr=True, **_color_kwargs)


def is_tty() -> bool:
    """True iff we should draw interactive UI (color, spinners, panels)."""
    return console.is_terminal


# ----- one-liners -----

def ok(*lines: str) -> None:
    """Print a ✓ success line (or block) to stdout."""
    _print_block(console, lines, glyph=GLYPH_OK, glyph_style="ok")


def warn(*lines: str) -> None:
    """Print a ⚠ warning line (or block) to stderr."""
    _print_block(err_console, lines, glyph=GLYPH_WARN, glyph_style="warn")


def err(*lines: str) -> None:
    """Print a ✗ error line (or block) to stderr."""
    _print_block(err_console, lines, glyph=GLYPH_ERR, glyph_style="err")


def info(*lines: str) -> None:
    """Print a plain status line."""
    _print_block(console, lines, glyph="", glyph_style=None)


def hint(text: str) -> None:
    """Print a single dim hint line to stdout."""
    console.print(f"  [hint]{text}[/hint]")


def step(label: str) -> None:
    """Print a step header (used by the wizard and service installer)."""
    console.print(f"\n[step]{label}[/step]")


def rule(title: str = "") -> None:
    """Horizontal divider; restrained, in dim text."""
    console.rule(f"[muted]{title}[/muted]" if title else "", style="dim")


def panel(body: str, *, title: str = "", style: str = "muted") -> None:
    """Render a panel to stdout, or fall back to indented plain text."""
    if is_tty():
        console.print(Panel(body, title=title, border_style=style, expand=False))
    else:
        if title:
            console.print(title)
        for line in body.splitlines():
            console.print(f"  {line}")


# ----- block-style errors with optional bug-report footer -----

def err_block(title: str, body_lines: Iterable[str] = (), *,
              bug: bool = False, issue_url: str | None = None) -> None:
    """Render a ✗ error as a red panel (or plain indented block off-TTY).

    Pass ``bug=True`` together with an ``issue_url`` to append a
    one-line footer linking to the issue tracker. Used for *unexpected*
    errors — not for user-fixable misconfiguration (those don't need a
    bug report).
    """
    body = "\n".join(body_lines)
    if bug and issue_url:
        if body:
            body += "\n"
        body += (
            f"\nIf this looks like a bug, file an issue with the output\n"
            f"of `aipager doctor`:\n  [path]{issue_url}[/path]"
        )
    if err_console.is_terminal:
        err_console.print(Panel(
            body,
            title=f"[err]{GLYPH_ERR} {title}[/err]",
            border_style="err",
            expand=False,
            padding=(0, 1),
        ))
    else:
        err_console.print(f"{GLYPH_ERR} {title}")
        for line in body.splitlines():
            err_console.print(line if line.startswith(" ") else f"  {line}")


def warn_block(title: str, body_lines: Iterable[str] = ()) -> None:
    """Render a ⚠ warning as a yellow panel (or plain block off-TTY)."""
    body = "\n".join(body_lines)
    if err_console.is_terminal:
        err_console.print(Panel(
            body,
            title=f"[warn]{GLYPH_WARN} {title}[/warn]",
            border_style="warn",
            expand=False,
            padding=(0, 1),
        ))
    else:
        err_console.print(f"{GLYPH_WARN} {title}")
        for line in body.splitlines():
            err_console.print(line if line.startswith(" ") else f"  {line}")


# ----- internal -----

def _print_block(c: Console, lines: tuple[str, ...],
                 glyph: str, glyph_style: str | None) -> None:
    if not lines:
        return
    first = lines[0]
    if glyph:
        c.print(f"[{glyph_style}]{glyph}[/{glyph_style}] {first}")
    else:
        c.print(first)
    for line in lines[1:]:
        if line == "":
            c.print("")
        else:
            c.print(line if line.startswith(" ") else f"  {line}")
