"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path


from aipager._test_guard import check_write
from aipager.ui import console, ok, step
from aipager.wizard._constants import (
    CLAUDE_SETTINGS,
    HOOK_CMD, STATUSLINE_CMD, HOOK_EVENTS, TOOL_MATCHER_EVENTS,
    PERMISSION_REQUEST_HOOK_TIMEOUT_SECONDS, MODEL_SWITCH_HOOK_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class DepStatus:
    """One row of the dependency check: ``path`` is ``None`` when missing,
    and ``fix`` is the command that installs it."""

    name: str
    path: str | None
    fix: str


def check_deps() -> list[DepStatus]:
    """The four things the daemon needs (dtach, claude, both hook
    scripts), resolved exactly as :func:`_step_deps` shows them. Never
    prints."""
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

    return [
        DepStatus("dtach", dtach_p,
                  "uv tool install --reinstall aipager  # or `brew install dtach`"),
        DepStatus("claude", claude_p,
                  "Install Claude Code: https://docs.anthropic.com/claude/docs/claude-code"),
        DepStatus("aipager-hook", hook_p,
                  "uv tool install --reinstall aipager"),
        DepStatus("aipager-statusline", statusline_p,
                  "uv tool install --reinstall aipager"),
    ]


def _step_deps(step_label: str = "[3/5]") -> bool:
    """Returns True if all required deps are present."""
    from rich.table import Table

    step(f"{step_label}  System dependencies")

    rows = [(d.name, d.path, d.fix) for d in check_deps()]

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
    return all(path for _name, path, _fix in rows)


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
    """Backfill ``timeout`` onto an already-wired aipager hook entry of an
    event that carries one (``PermissionRequest``, and ``PreModelSwitch``
    since roadmap 8.35) — a wizard re-run against a ``settings.json`` written
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
    # Same separate-dict rule as permission_entry above.
    model_switch_entry = {
        "type": "command", "command": hook_path,
        "timeout": MODEL_SWITCH_HOOK_TIMEOUT_SECONDS,
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
            elif event == "PreModelSwitch":
                repointed += _ensure_permission_timeout(
                    entries, HOOK_CMD, MODEL_SWITCH_HOOK_TIMEOUT_SECONDS,
                )
            continue
        if event == "PermissionRequest":
            entries.append({"matcher": "*", "hooks": [permission_entry]})
        elif event == "PreModelSwitch":
            entries.append({"hooks": [model_switch_entry]})
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


@dataclass(frozen=True)
class SettingsPlan:
    """What :func:`apply_settings` would do to ``~/.claude/settings.json``:
    ``new_text`` is the merged file, ``changed`` whether it differs from
    ``existing_text`` (always ``True`` for a file that does not exist
    yet), ``repointed`` how many entries an earlier install left that the
    merge moves to this one."""

    path: Path
    exists: bool
    existing_text: str
    new_text: str
    changed: bool
    repointed: int


def plan_settings() -> SettingsPlan:
    """Read, validate and merge ``settings.json`` in memory. Writes
    nothing and prints nothing. Raises ``ValueError`` for a file that is
    not valid JSON or has the wrong shape, ``OSError`` when it cannot be
    read, with the same messages :func:`_step_settings` shows."""
    path = CLAUDE_SETTINGS
    settings: dict = {}
    existing_text = ""
    exists = path.exists()
    if exists:
        try:
            existing_text = path.read_text()
        except OSError as e:
            raise OSError(f"cannot read {path}: {e}") from e
        try:
            settings = json.loads(existing_text)
        except json.JSONDecodeError as e:
            extra = ""
            if re.search(r"^\s*//|/\*", existing_text):
                extra = ("\n     Looks like the file has // or /* */ comments. "
                         "Claude Code uses strict JSON - strip them.")
            raise ValueError(
                f"{path} is not valid JSON ({e}).{extra}"
            ) from e
        try:
            _validate_settings_schema(settings)
        except ValueError as e:
            raise ValueError(f"{path} schema problem: {e}") from e
    repointed = _merge_hooks(settings)
    new_text = json.dumps(settings, indent=2) + "\n"
    return SettingsPlan(
        path=path, exists=exists, existing_text=existing_text,
        new_text=new_text, changed=(not exists or new_text != existing_text),
        repointed=repointed,
    )


def _backup_settings(plan: SettingsPlan) -> Path | None:
    """Copy the existing file aside (``settings.json.bak.<epoch>``) before
    it changes, or make the parent directory for a new one. Returns the
    backup path, or ``None`` when there was nothing to back up."""
    check_write(plan.path)
    if plan.exists:
        backup = plan.path.with_name(f"{plan.path.name}.bak.{int(time.time())}")
        backup.write_text(plan.existing_text)
        return backup
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    return None


def _write_settings(plan: SettingsPlan) -> None:
    check_write(plan.path)
    try:
        plan.path.write_text(plan.new_text)
    except OSError as e:
        raise OSError(f"cannot write {plan.path}: {e}") from e


def apply_settings(plan: SettingsPlan) -> str | None:
    """Carry out *plan* (no-op when nothing changes): back up, then write.
    Returns the backup's file name, or ``None``. Never prints."""
    if not plan.changed:
        return None
    backup = _backup_settings(plan)
    _write_settings(plan)
    return backup.name if backup is not None else None


def _step_settings(step_label: str = "[4/5]") -> None:
    step(f"{step_label}  Claude Code integration")
    plan = plan_settings()
    if not plan.changed:
        ok(f"{plan.path} already up to date")
        return
    backup = _backup_settings(plan)
    if backup is not None:
        console.print(f"  [muted]• backed up existing settings → {backup.name}[/muted]")
    repointed = plan.repointed
    if repointed:
        console.print(
            f"  [muted]• repointed {repointed} "
            f"entr{'y' if repointed == 1 else 'ies'} from an earlier "
            f"install[/muted]"
        )
    _write_settings(plan)
    ok(f"Patched {plan.path} ({len(HOOK_EVENTS)} hooks + statusLine)")


# ── `aipager uninstall`: take aipager's entries out again (roadmap 8.119) ──
#
# Left behind, every hook event of every Claude Code session runs a missing
# program (measured with Claude Code 2.1.294: exit 127, one
# `hook_non_blocking_error` per event, the turn carries on). Only what
# `_merge_hooks` and claude_bootstrap add is taken out, found the way they
# find it (`_is_aipager_hook`); a user's own hooks, statusLine and every
# other key stay. `skipDangerousModePermissionPrompt`, which the daemon
# also sets, stays too: it cannot be told from the user's own acceptance.

#: Why the file is left exactly as it is, by what went wrong.
_UNPATCH_LINK = "it is a link to another file"
_UNPATCH_UNREADABLE = "it cannot be read"
_UNPATCH_NOT_TEXT = "it is not valid UTF-8 text"
_UNPATCH_NOT_JSON = "it is not valid JSON"
_UNPATCH_BAD_SHAPE = "it is not in the shape Claude Code reads"


class SettingsChanged(OSError):
    """Claude Code's settings changed after uninstall read them: they are
    left alone rather than losing that change."""


@dataclass(frozen=True)
class UnpatchPlan:
    """What :func:`apply_unpatch` would do to ``settings.json``.

    ``new_text`` is the file without aipager's entries, or ``None`` when
    nothing changes; ``hooks`` how many of aipager's hook commands it
    removes; ``status_line`` whether it removes aipager's statusLine;
    ``problem`` why a file that may hold aipager's entries is left exactly
    as it is (``None`` otherwise); ``left`` the hook and statusLine
    commands that still mention aipager's programs after the removal (a
    user's own wrapper, such as the ``aipager-hook*`` scripts
    claude_bootstrap honours): never removed, only named."""

    path: Path
    existing_text: str
    new_text: str | None
    hooks: int = 0
    status_line: bool = False
    problem: str | None = None
    left: tuple[str, ...] = ()


def _drop_aipager_hooks(settings: dict) -> int:
    """Remove every hook command of aipager's from *settings*; a block, an
    event or the whole ``hooks`` key left empty BY THAT goes too. Returns
    how many were removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0

    def ours(hook) -> bool:
        return (isinstance(hook, dict) and isinstance(hook.get("command"), str)
                and _is_aipager_hook(hook["command"], HOOK_CMD))

    removed = 0
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept, removed_here = [], 0
        for block in entries:
            inner = block.get("hooks") if isinstance(block, dict) else None
            mine = sum(1 for h in inner if ours(h)) if isinstance(inner, list) else 0
            if not mine:
                kept.append(block)
                continue
            removed_here += mine
            block["hooks"] = [h for h in inner if not ours(h)]
            if block["hooks"]:
                kept.append(block)
        if removed_here:
            removed += removed_here
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if removed and not hooks:
        del settings["hooks"]
    return removed


def _drop_aipager_status_line(settings: dict) -> bool:
    """Remove the statusLine when it runs aipager's helper."""
    line = settings.get("statusLine")
    if (isinstance(line, dict) and isinstance(line.get("command"), str)
            and _is_aipager_hook(line["command"], STATUSLINE_CMD)):
        del settings["statusLine"]
        return True
    return False


def _commands_mentioning_us(settings: dict) -> tuple[str, ...]:
    """Every hook or statusLine command in *settings* that mentions
    aipager's programs."""
    commands = []
    hooks = settings.get("hooks")
    for entries in (hooks.values() if isinstance(hooks, dict) else ()):
        for block in entries if isinstance(entries, list) else ():
            inner = block.get("hooks") if isinstance(block, dict) else None
            for hook in inner if isinstance(inner, list) else ():
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
    line = settings.get("statusLine")
    if isinstance(line, dict) and isinstance(line.get("command"), str):
        commands.append(line["command"])
    return tuple(dict.fromkeys(
        c for c in commands if HOOK_CMD in c or STATUSLINE_CMD in c))


def plan_unpatch() -> UnpatchPlan:
    """Read ``settings.json`` and work out the file without aipager's
    entries. Writes nothing and never raises. A file that is a link, cannot
    be read, is not JSON or has hooks Claude Code would not read is never
    rewritten; it is reported as a problem only when it may hold aipager's
    entries (it mentions them, or cannot be read at all)."""
    path = CLAUDE_SETTINGS
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return UnpatchPlan(path, "", None)
    except OSError:
        return UnpatchPlan(path, "", None, problem=_UNPATCH_UNREADABLE)
    mentions_us = HOOK_CMD.encode() in data or STATUSLINE_CMD.encode() in data

    def left_as_is(problem: str, text: str = "") -> UnpatchPlan:
        return UnpatchPlan(path, text, None, problem=problem if mentions_us else None)

    if path.is_symlink():
        return left_as_is(_UNPATCH_LINK)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return left_as_is(_UNPATCH_NOT_TEXT)
    try:
        settings = json.loads(text)
    except ValueError:              # not JSON, or an integer too long to read
        return left_as_is(_UNPATCH_NOT_JSON, text)
    except RecursionError:          # nested deeper than Python parses
        return left_as_is(_UNPATCH_BAD_SHAPE, text)
    if not isinstance(settings, dict):
        return left_as_is(_UNPATCH_BAD_SHAPE, text)
    try:
        _validate_settings_schema(settings)
    except ValueError:
        return left_as_is(_UNPATCH_BAD_SHAPE, text)
    hooks = _drop_aipager_hooks(settings)
    status_line = _drop_aipager_status_line(settings)
    left = _commands_mentioning_us(settings)
    if not (hooks or status_line):
        return UnpatchPlan(path, text, None, left=left)
    try:
        # allow_nan=False: a number too big for a float (1e999) would come
        # back as `Infinity`, which Claude Code cannot read.
        new_text = json.dumps(settings, indent=2, ensure_ascii=False,
                              allow_nan=False) + "\n"
        try:
            # The user's own text stays readable (no \u escapes), unless
            # it holds a lone surrogate (half an emoji, escaped in the
            # file), which UTF-8 cannot write.
            new_text.encode("utf-8")
        except UnicodeEncodeError:
            new_text = json.dumps(settings, indent=2, allow_nan=False) + "\n"
    except (ValueError, RecursionError):    # Infinity/NaN, or too deep to write back
        return left_as_is(_UNPATCH_BAD_SHAPE, text)
    return UnpatchPlan(path, text, new_text,
                       hooks=hooks, status_line=status_line, left=left)


def _write_with_mode(target: Path, text: str, mode: int) -> None:
    """Write *text* to a NEW file *target* with *mode* from the moment it
    exists, so a copy of a private file is never readable by others, even
    briefly. Never follows or reuses what is already there."""
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        fh = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        os.fchmod(fd, mode)
        fh.write(text)


def _free_backup_path(path: Path) -> Path:
    """``settings.json.bak.<epoch>``, as the wizard names it, or with
    ``.1``, ``.2``... when a backup of that second is already there."""
    base = path.with_name(f"{path.name}.bak.{int(time.time())}")
    candidate, n = base, 0
    while candidate.exists() or candidate.is_symlink():
        n += 1
        candidate = base.with_name(f"{base.name}.{n}")
    return candidate


def apply_unpatch(plan: UnpatchPlan) -> str | None:
    """Carry out *plan*: back the file up (``settings.json.bak.<epoch>``,
    as the wizard does), then replace it in one step, keeping its mode.
    Returns the backup's file name, or ``None`` when nothing changes.
    Raises :class:`SettingsChanged` when the file changed since *plan* read
    it, and ``OSError`` when the backup or the write fails; the file is
    then as it was."""
    if plan.new_text is None:
        return None
    path = plan.path
    backup = _free_backup_path(path)
    tmp = path.with_name(f"{path.name}.aipager-tmp")
    for target in (path, backup, tmp):
        check_write(target)
    # Claude Code writes this file too: one written since the plan read it
    # is left alone rather than losing that change.
    if path.read_bytes() != plan.existing_text.encode("utf-8"):
        raise SettingsChanged("it changed while uninstall was running")
    mode = path.stat().st_mode & 0o777
    _write_with_mode(backup, plan.existing_text, mode)
    # A temporary file left by a run that was cut short (or a link put in
    # its place) is removed, never written through.
    tmp.unlink(missing_ok=True)
    try:
        _write_with_mode(tmp, plan.new_text, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return backup.name


def unpatch_summary(plan: UnpatchPlan) -> str:
    """``aipager's 16 hooks and its status line``, ``aipager's 1 hook`` or
    ``aipager's status line``, for the uninstall lines."""
    hooks = f"{plan.hooks} hook{'s' if plan.hooks != 1 else ''}"
    if plan.hooks and plan.status_line:
        return f"aipager's {hooks} and its status line"
    return f"aipager's {hooks}" if plan.hooks else "aipager's status line"
