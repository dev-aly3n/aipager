"""Idempotent first-run setup for Claude Code's user files.

Three things block a fresh container/headless aipager install from
working over Telegram, because the wizard (``aipager config``) is
normally what writes them:

1. ``~/.claude/settings.json`` — without ``skipDangerousModePermissionPrompt``,
   claude shows a "WARNING: Bypass Permissions mode" picker every time
   it's launched with ``--dangerously-skip-permissions`` (which aipager
   uses for sessions started as ``/new !name``). The picker defaults to
   "No, exit" — the user's first prompt over Telegram lands as Enter
   on that, and claude exits before responding.

2. ``~/.claude.json`` — without ``hasTrustDialogAccepted`` for the
   working directory, claude shows a "Do you trust this folder?" picker
   on launch in any new cwd. Same Telegram failure mode.

3. ``~/.claude/settings.json`` ``hooks`` + ``statusLine`` — without the
   ``aipager-hook`` wired into ``UserPromptSubmit``/``Stop``/etc.,
   the daemon never learns each session's transcript path and
   ``claude_session_id``, so ``/resume`` has nothing to resume to and
   safety/policy enforcement (PreToolUse) doesn't run.

Run on every ``aipager start`` because these are all user-state that
the wizard sets when the user accepts the prompts interactively — a
Telegram-only user (containerized friend deploy, SSH-less host) never
sees those prompts, so aipager has to write the acceptance for them.

Best-effort: failures are logged at DEBUG and skipped so a missing
``~/.claude`` directory or non-writable filesystem never blocks daemon
startup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)


_SETTINGS = Path.home() / ".claude" / "settings.json"
_CLAUDE_JSON = Path.home() / ".claude.json"

# Mirror the wizard's hook surface so containerized deploys get the
# same coverage as `aipager config`. Kept in sync with
# ``aipager.wizard._constants`` (the wizard is the canonical source
# for users who run it; this module is the fallback for users who
# don't).
_HOOK_CMD = "aipager-hook"
_STATUSLINE_CMD = "aipager-statusline"
_HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PermissionRequest",
    "Notification", "Stop", "SubagentStop", "PreCompact",
)
_TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600 if path.name.startswith(".") else 0o644)
    except OSError:
        pass
    os.replace(tmp, path)


def _ensure_bypass_accepted() -> bool:
    """Return True if the settings file was modified."""
    settings = _load(_SETTINGS)
    if settings.get("skipDangerousModePermissionPrompt") is True:
        return False
    settings["skipDangerousModePermissionPrompt"] = True
    _atomic_write(_SETTINGS, settings)
    return True


def _ensure_workdir_trusted(workdir: str) -> bool:
    """Return True if .claude.json was modified."""
    data = _load(_CLAUDE_JSON)
    projects = data.setdefault("projects", {})
    if not isinstance(projects, dict):
        return False
    entry = projects.get(workdir)
    if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
        return False
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpContextUris", [])
    entry.setdefault("mcpServers", {})
    entry.setdefault("hasClaudeMdExternalIncludesWarningShown", False)
    entry["hasTrustDialogAccepted"] = True
    projects[workdir] = entry
    _atomic_write(_CLAUDE_JSON, data)
    return True


def _resolve(cmd: str) -> str | None:
    """Resolve an aipager helper script to an absolute path.

    Tries PATH, then the bin dir next to the running Python interpreter
    (true for pip / uv tool / pipx / Docker installs). Returns None if
    we can't find it — Claude Code does NOT augment PATH when running
    hook commands, so a bare name silently breaks the hook.
    """
    found = shutil.which(cmd)
    if found:
        return found
    candidate = Path(sys.executable).parent / cmd
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    """Detect whether the aipager hook (or a user's wrapper around it)
    is already wired for this event.

    Matches any of:
    - Command is literally ``bare_name`` (e.g. ``aipager-hook``).
    - Command's basename starts with ``bare_name`` — catches wrapper
      scripts like ``aipager-hook-capped.sh`` /
      ``aipager-hook.wrapped`` that users deploy for rate-limits,
      memory caps, logging, etc. Documented convention: name your
      wrapper ``aipager-hook*`` and aipager will honor it instead
      of injecting a duplicate entry.
    """
    if not bare_name:
        return False
    for block in entries:
        for hook in (block or {}).get("hooks", []):
            cmd = (hook or {}).get("command", "") or ""
            if not cmd:
                continue
            if cmd == bare_name:
                return True
            basename = Path(cmd).name
            if basename.startswith(bare_name):
                return True
    return False


def _ensure_hooks_and_statusline() -> bool:
    """Wire aipager-hook into every hook event + statusLine. Idempotent."""
    hook_path = _resolve(_HOOK_CMD)
    statusline_path = _resolve(_STATUSLINE_CMD)
    if not hook_path:
        log.debug("claude bootstrap: %s not on PATH; skipping hook wiring", _HOOK_CMD)
        return False

    settings = _load(_SETTINGS)
    changed = False

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
        changed = True
    entry = {"type": "command", "command": hook_path}
    for event in _HOOK_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
            changed = True
        if _has_hook_cmd(entries, _HOOK_CMD):
            continue
        if event in _TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
        changed = True

    # statusLine — keep an existing working entry; otherwise install ours.
    existing_sl = settings.get("statusLine") or {}
    existing_cmd = (existing_sl.get("command", "")
                    if isinstance(existing_sl, dict) else "")
    sl_good = bool(
        existing_cmd and (
            shutil.which(existing_cmd)
            or (os.path.isabs(existing_cmd)
                and os.path.exists(existing_cmd)
                and os.access(existing_cmd, os.X_OK))
        )
    )
    if not sl_good and statusline_path:
        settings["statusLine"] = {"type": "command", "command": statusline_path}
        changed = True

    if changed:
        _atomic_write(_SETTINGS, settings)
    return changed


def bootstrap_claude_settings(workdir: str | None = None) -> None:
    """Write the acceptance flags + hooks that claude-code's wizard
    would normally configure interactively. Idempotent; safe to call
    on every daemon start.

    ``workdir`` defaults to the daemon's cwd (which is also the default
    cwd for spawned sessions — see ``dtach/inject.py:_PROJECT_DIR``).
    """
    if workdir is None:
        workdir = os.environ.get("AIPAGER_WORK_DIR", os.getcwd())
    try:
        if _ensure_bypass_accepted():
            log.info("claude bootstrap: set skipDangerousModePermissionPrompt=true in %s", _SETTINGS)
    except Exception:
        log.debug("claude bootstrap: failed to patch settings.json", exc_info=True)
    try:
        if _ensure_workdir_trusted(workdir):
            log.info("claude bootstrap: trusted %s in %s", workdir, _CLAUDE_JSON)
    except Exception:
        log.debug("claude bootstrap: failed to patch .claude.json", exc_info=True)
    try:
        if _ensure_hooks_and_statusline():
            log.info("claude bootstrap: wired %s hooks + statusLine into %s",
                     _HOOK_CMD, _SETTINGS)
    except Exception:
        log.debug("claude bootstrap: failed to wire hooks", exc_info=True)
