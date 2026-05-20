"""Per-session policy snapshot (Phase E).

The daemon resolves a session driver's *effective* safety rules and
writes them to ``/tmp/claude-policy-<session>.json`` on each Telegram
prompt. The PreToolUse hook (which can't see daemon memory) reads this
snapshot to decide whether to block a tool call. Origin is determined
separately by the hook (from the transcript marker); the snapshot just
carries the resolved rule sets + the owner bypass flag.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from aipager import safety

log = logging.getLogger(__name__)


def snapshot_path(session_name: str) -> Path:
    return Path(f"/tmp/claude-policy-{session_name}.json")


def resolve_snapshot(role, scope, member) -> dict:
    """Compute the effective rule sets for a driver (pure).

    ``role`` is a policy.Role (or None), ``scope`` a scope.Scope (or
    None), ``member`` a scope.Member (or None). Deny lists union across
    scope + role + member; the safety floor (paths + bash) always
    applies on top of any role/member additions.
    """
    bypass_safety = bool(role and role.bypass_safety)
    bypass_role_denies = bool(role and role.bypass_role_denies)

    deny_tools: set[str] = set()
    allow_tools: set[str] = set()
    no_access: set[str] = set(safety.DENY_PATHS_NO_ACCESS)
    no_write: set[str] = set(safety.DENY_PATHS_NO_WRITE)
    bash: set[str] = set(safety.DENY_BASH_PATTERNS)

    if not bypass_role_denies:
        if scope:
            deny_tools |= set(scope.deny_tools)
        if role:
            deny_tools |= set(role.deny_tools)
            allow_tools |= set(role.allow_tools)
            no_access |= set(role.deny_paths_no_access)
            no_write |= set(role.deny_paths_no_write)
            bash |= set(role.deny_bash_patterns)
        if member:
            deny_tools |= set(getattr(member, "deny_tools", ()))
            allow_tools |= set(getattr(member, "allow_tools", ()))

    return {
        "origin": "telegram",
        "bypass_safety": bypass_safety,
        "deny_tools": sorted(deny_tools),
        "allow_tools": sorted(allow_tools),
        "deny_paths_no_access": sorted(no_access),
        "deny_paths_no_write": sorted(no_write),
        "deny_bash_patterns": sorted(bash),
    }


def write_snapshot(session_name: str, role, scope, member) -> None:
    """Atomic-write the resolved snapshot for a session (best-effort)."""
    data = resolve_snapshot(role, scope, member)
    path = snapshot_path(session_name)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write policy snapshot %s", path, exc_info=True)


def clear_snapshot(session_name: str) -> None:
    try:
        snapshot_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass
