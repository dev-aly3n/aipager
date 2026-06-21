"""Idempotent first-run setup for Claude Code's user files.

Two state files block claude-code's TUI on first launch and can't be
dismissed over Telegram (the user can't see the dialog to tap "Yes"):

1. ``~/.claude/settings.json`` — without ``skipDangerousModePermissionPrompt``,
   claude shows a "WARNING: Bypass Permissions mode" picker every time
   it's launched with ``--dangerously-skip-permissions`` (which aipager
   uses for sessions started as ``/new !name``). The picker defaults to
   "No, exit" — the user's first prompt over Telegram lands as Enter
   on that, and claude exits before responding.

2. ``~/.claude.json`` — without ``hasTrustDialogAccepted`` for the
   working directory, claude shows a "Do you trust this folder?" picker
   on launch in any new cwd. Same Telegram failure mode.

Run on every ``aipager start`` because both flags are user-state that
claude-code's wizard sets when the user accepts the prompt
interactively — a Telegram-only user (containerized friend deploy,
SSH-less host) never sees those prompts, so aipager has to write the
acceptance for them.

Best-effort: failures are logged at DEBUG and skipped so a missing
``~/.claude`` directory or non-writable filesystem never blocks daemon
startup.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


_SETTINGS = Path.home() / ".claude" / "settings.json"
_CLAUDE_JSON = Path.home() / ".claude.json"


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


def bootstrap_claude_settings(workdir: str | None = None) -> None:
    """Write the two acceptance flags claude-code's wizard sets
    interactively. Idempotent; safe to call on every daemon start.

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
