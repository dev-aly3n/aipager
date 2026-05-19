"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import json
import re
import shutil
import time


from aipager.ui import console, ok, step
from aipager.wizard._constants import (
    CLAUDE_SETTINGS,
    HOOK_CMD, STATUSLINE_CMD, HOOK_EVENTS, TOOL_MATCHER_EVENTS,
)


def _step_deps(step_label: str = "[3/5]") -> bool:
    """Returns True if all required deps are present."""
    from rich.table import Table

    step(f"{step_label}  System dependencies")

    dtach_p: str | None = None
    try:
        from dtach_bin import path as _dtach_path
        dtach_p = _dtach_path()
    except (ImportError, FileNotFoundError):
        dtach_p = shutil.which("dtach")

    claude_p = shutil.which("claude")
    hook_p = shutil.which(HOOK_CMD)
    statusline_p = shutil.which(STATUSLINE_CMD)

    rows = [
        ("dtach", dtach_p,
         "uv tool install --reinstall aipager  # or `brew install dtach`"),
        ("claude", claude_p,
         "Install Claude Code: https://docs.anthropic.com/claude/docs/claude-code"),
        ("aipager-hook", hook_p,
         "uv tool install --reinstall aipager"),
        ("aipager-statusline", statusline_p,
         "uv tool install --reinstall aipager"),
    ]

    if console.is_terminal:
        t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
        t.add_column(width=3, justify="center")
        t.add_column(no_wrap=True)
        t.add_column(style="hint")
        for name, path, fix in rows:
            mark = ("[ok]✓[/ok]" if path else "[err]✗[/err]")
            detail = path if path else fix
            t.add_row(mark, name, detail)
        console.print(t)
    else:
        for name, path, fix in rows:
            mark = "✓" if path else "✗"
            console.print(f"  {mark} {name}  {path or fix}")

    # Required for the daemon: dtach + claude + both hook scripts.
    return bool(dtach_p and claude_p and hook_p and statusline_p)


def _resolve(cmd: str) -> str:
    """Resolve a console-script to an absolute path; caller pre-checks."""
    return shutil.which(cmd) or cmd


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    for block in entries:
        for hook in block.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == bare_name or cmd.endswith(f"/{bare_name}"):
                return True
    return False


def _validate_settings_schema(settings: dict) -> None:
    hooks = settings.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise ValueError(
            f"settings.json has `hooks` as {type(hooks).__name__}, "
            "but Claude Code expects a dict mapping event names to hook lists."
        )
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError(
                f"settings.json has `hooks.{event}` as "
                f"{type(entries).__name__}, expected a list."
            )


def _merge_hooks(settings: dict) -> None:
    hook_path = _resolve(HOOK_CMD)
    statusline_path = _resolve(STATUSLINE_CMD)
    hooks = settings.setdefault("hooks", {})
    entry = {"type": "command", "command": hook_path}
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        if _has_hook_cmd(entries, HOOK_CMD):
            continue
        if event in TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
    settings["statusLine"] = {"type": "command", "command": statusline_path}


def _step_settings(step_label: str = "[4/5]") -> None:
    step(f"{step_label}  Claude Code integration")
    settings: dict = {}
    existing_text = ""
    if CLAUDE_SETTINGS.exists():
        try:
            existing_text = CLAUDE_SETTINGS.read_text()
        except OSError as e:
            raise OSError(f"cannot read {CLAUDE_SETTINGS}: {e}") from e
        try:
            settings = json.loads(existing_text)
        except json.JSONDecodeError as e:
            extra = ""
            if re.search(r"^\s*//|/\*", existing_text):
                extra = ("\n     Looks like the file has // or /* */ comments. "
                         "Claude Code uses strict JSON — strip them.")
            raise ValueError(
                f"{CLAUDE_SETTINGS} is not valid JSON ({e}).{extra}"
            ) from e
        try:
            _validate_settings_schema(settings)
        except ValueError as e:
            raise ValueError(f"{CLAUDE_SETTINGS} schema problem: {e}") from e
        new_settings = json.loads(existing_text)
        _merge_hooks(new_settings)
        new_text = json.dumps(new_settings, indent=2) + "\n"
        if new_text == existing_text:
            ok(f"{CLAUDE_SETTINGS} already up to date")
            return
        backup = CLAUDE_SETTINGS.with_name(
            f"{CLAUDE_SETTINGS.name}.bak.{int(time.time())}"
        )
        backup.write_text(existing_text)
        console.print(f"  [muted]• backed up existing settings → {backup.name}[/muted]")
    else:
        CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _merge_hooks(settings)
    try:
        CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    except OSError as e:
        raise OSError(f"cannot write {CLAUDE_SETTINGS}: {e}") from e
    ok(f"Patched {CLAUDE_SETTINGS} ({len(HOOK_EVENTS)} hooks + statusLine)")
