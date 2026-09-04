"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path


from aipager.ui import console, ok, step
from aipager.wizard._constants import (
    CLAUDE_SETTINGS,
    HOOK_CMD, STATUSLINE_CMD, HOOK_EVENTS, TOOL_MATCHER_EVENTS,
    PERMISSION_REQUEST_HOOK_TIMEOUT_SECONDS,
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

    # The resolver, not a bare shutil.which("claude") — so this table
    # shows the exact binary the daemon will actually launch.
    from aipager import claude_resolve
    _resolved = claude_resolve.try_resolve_claude_binary()
    claude_p = _resolved.chosen.path if _resolved else None
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
    """Resolve a console-script to an absolute path.

    Tries PATH first; if that misses, checks the bin directory next
    to the running Python interpreter (true for pip / uv tool /
    pipx installs — console-scripts live in ``<venv>/bin/`` next to
    the interpreter that imported this module). Falls back to the
    bare name only when neither succeeds — Claude Code does NOT
    augment PATH when running hook commands, so the bare-name
    fallback typically means a broken settings.json entry.
    """
    found = shutil.which(cmd)
    if found:
        return found
    candidate = Path(sys.executable).parent / cmd
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return cmd


def _is_aipager_hook(cmd: str, bare_name: str) -> bool:
    """True when *cmd* invokes our console script, wherever it lives."""
    return cmd == bare_name or cmd.endswith(f"/{bare_name}")


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    for block in entries:
        if not isinstance(block, dict):
            continue
        for hook in block.get("hooks") or ():
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command")
            if isinstance(cmd, str) and _is_aipager_hook(cmd, bare_name):
                return True
    return False


def _repoint_hook_cmds(entries: list, bare_name: str, resolved: str) -> int:
    """Point every aipager hook in *entries* at *resolved*. Returns the count.

    Matching an entry by basename alone was enough to consider the event
    "already wired", so a hook kept pointing at whichever install first
    wrote it — for good. Installing a second way (pipx after pip, a venv
    then a system install, a moved home) left events split across builds:
    observed in the wild with 10 of 15 events on a stale editable venv
    while the rest ran the current one, which silently broke a feature and
    looked correctly configured the whole time.

    Only rewrites when *resolved* is absolute. A bare name means
    ``_resolve`` found nothing on PATH or beside the interpreter, and
    Claude Code does not augment PATH for hooks — overwriting a working
    absolute path with it would break the very hook we are fixing. That is
    the same failure the statusLine block below guards against.
    """
    if not os.path.isabs(resolved):
        return 0
    changed = 0
    for block in entries:
        if not isinstance(block, dict):
            continue
        for hook in block.get("hooks") or ():
            # settings.json is hand-editable; a malformed entry must be
            # stepped over, not crash the wizard mid-write.
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command")
            if not isinstance(cmd, str):
                continue
            if _is_aipager_hook(cmd, bare_name) and cmd != resolved:
                hook["command"] = resolved
                changed += 1
    return changed


def _ensure_permission_timeout(entries: list, bare_name: str, timeout_seconds: int) -> int:
    """Backfill ``timeout`` onto an already-wired ``PermissionRequest``
    hook entry — a wizard re-run against a ``settings.json`` written
    before this ship (design.md "answer PermissionRequest hooks with a
    decision instead of keystrokes"). Mirrors :func:`_repoint_hook_cmds`'s
    shape (same malformed-entry tolerance, same "count of hook dicts
    changed" return) so `_step_settings`'s "repointed N entries" message
    naturally covers this too.

    Idempotent: an entry whose ``timeout`` already equals
    *timeout_seconds* is left untouched, so a second `_merge_hooks()`
    call against already-correct settings produces byte-identical JSON.
    """
    changed = 0
    for block in entries:
        if not isinstance(block, dict):
            continue
        for hook in block.get("hooks") or ():
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command")
            if not isinstance(cmd, str) or not _is_aipager_hook(cmd, bare_name):
                continue
            if hook.get("timeout") != timeout_seconds:
                hook["timeout"] = timeout_seconds
                changed += 1
    return changed


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


def _merge_hooks(settings: dict) -> int:
    hook_path = _resolve(HOOK_CMD)
    statusline_path = _resolve(STATUSLINE_CMD)
    hooks = settings.setdefault("hooks", {})
    entry = {"type": "command", "command": hook_path}
    # PermissionRequest gets its OWN, separate dict — never `entry`
    # above. The loop below appends `entry` by REFERENCE into every
    # other event's `hooks` list; adding a "timeout" key to that shared
    # object would silently give EVERY hook event a 30s timeout, not
    # just PermissionRequest (design.md "load-bearing implementation
    # gotcha" — a real regression this separate dict exists to prevent).
    permission_entry = {
        "type": "command", "command": hook_path,
        "timeout": PERMISSION_REQUEST_HOOK_TIMEOUT_SECONDS,
    }
    repointed = 0
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        if _has_hook_cmd(entries, HOOK_CMD):
            # Already wired — but possibly to a different install. Bring it
            # to this one rather than leaving the event on a stale build.
            repointed += _repoint_hook_cmds(entries, HOOK_CMD, hook_path)
            if event == "PermissionRequest":
                repointed += _ensure_permission_timeout(
                    entries, HOOK_CMD, PERMISSION_REQUEST_HOOK_TIMEOUT_SECONDS,
                )
            continue
        if event == "PermissionRequest":
            entries.append({"matcher": "*", "hooks": [permission_entry]})
        elif event in TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
    # Idempotency for statusLine — don't clobber a working entry.
    # Hooks have `_has_hook_cmd` dedup; mirror that here. If we
    # unconditionally overwrote, a wizard re-run with the venv off
    # PATH would replace a working absolute path with a broken bare
    # name (the failure mode that surfaces as `/status` rendering
    # `Model —` / `Ctx 0%` / `Cost —`).
    existing_sl = settings.get("statusLine") or {}
    existing_cmd = (existing_sl.get("command", "")
                    if isinstance(existing_sl, dict) else "")
    if not isinstance(existing_cmd, str):
        # Hand-edited file: a non-string command would blow up in
        # shutil.which below. Treat it as absent so the entry is rewritten
        # rather than crashing the wizard mid-run.
        existing_cmd = ""
    sl_already_good = bool(
        existing_cmd and (
            shutil.which(existing_cmd)
            or (os.path.isabs(existing_cmd)
                and os.path.exists(existing_cmd)
                and os.access(existing_cmd, os.X_OK))
        )
    )
    # "Working" is not the same as "ours and current": a statusLine left
    # behind by an earlier install still resolves and still runs — it just
    # runs the wrong build, the same split this function now repairs for
    # hooks. Repoint when the existing entry is our own console script at a
    # different path, and we have an absolute path to move it to.
    sl_is_ours_but_stale = (
        _is_aipager_hook(existing_cmd, STATUSLINE_CMD)
        and existing_cmd != statusline_path
        and os.path.isabs(statusline_path)
    )
    if not sl_already_good or sl_is_ours_but_stale:
        settings["statusLine"] = {
            "type": "command", "command": statusline_path,
        }
    # Reported by the caller, not here: `_step_settings` runs this twice —
    # once on a throwaway copy to decide whether anything changed, once for
    # the real write — so printing from inside would say it twice, the first
    # time before the "backed up" line.
    return repointed + int(sl_is_ours_but_stale)


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
        # Dry run on a copy purely to decide whether anything changed — its
        # return value is discarded; the real write below reports.
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
    repointed = _merge_hooks(settings)
    if repointed:
        console.print(
            f"  [muted]• repointed {repointed} "
            f"entr{'y' if repointed == 1 else 'ies'} from an earlier "
            f"install[/muted]"
        )
    try:
        CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    except OSError as e:
        raise OSError(f"cannot write {CLAUDE_SETTINGS}: {e}") from e
    ok(f"Patched {CLAUDE_SETTINGS} ({len(HOOK_EVENTS)} hooks + statusLine)")
