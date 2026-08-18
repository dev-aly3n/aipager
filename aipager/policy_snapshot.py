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


def resolve_snapshot(role, scope, member, style_text: str = "",
                     reply_context: str = "") -> dict:
    """Compute the effective rule sets for a driver (pure).

    ``role`` is a policy.Role (or None), ``scope`` a scope.Scope (or
    None), ``member`` a scope.Member (or None). Deny lists union across
    scope + role + member; the safety floor (paths + bash) always
    applies on top of any role/member additions.

    ``style_text`` is unrelated to safety — it's the precomputed
    `/settings` reply-style instruction block (see
    ``aipager.preferences.style_text``) for the ``UserPromptSubmit``
    hook to print verbatim. Carried on this snapshot rather than a
    second file because it's the same "what does the hook need to know
    about this turn" write, already firing at the right time.

    ``reply_context`` is likewise unrelated to safety — the rendered
    reply-pointer wording (design.md "reply context" feature) for the
    same hook to print alongside ``style_text``. Defaulted to ``""`` so
    a caller that forgets to pass it clears any stale value from a
    prior turn rather than leaking it (the staleness guard — see
    ``write_snapshot`` below and ``session_ops._inject_prompt``).
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
        "style_text": style_text,
        "reply_context": reply_context,
    }


def write_snapshot(session_name: str, role, scope, member,
                   style_text: str = "", reply_context: str = "") -> None:
    """Atomic-write the resolved snapshot for a session (best-effort).

    ``reply_context`` defaults to ``""`` — every one of the (12+)
    ``_inject_prompt`` call sites unrelated to a reply passes nothing,
    so the snapshot is overwritten with an explicit "not a reply" on
    every turn rather than silently keeping a prior turn's value.
    """
    data = resolve_snapshot(role, scope, member, style_text, reply_context)
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


def read_snapshot(session_name: str) -> dict | None:
    """Read a session's snapshot, or None if absent/unreadable."""
    try:
        return json.loads(snapshot_path(session_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_snapshot(session_name: str) -> None:
    try:
        snapshot_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def reply_context_path(session_name: str) -> Path:
    return Path(f"/tmp/claude-reply-{session_name}.txt")


def write_reply_context_file(session_name: str, header: str, full_text: str) -> None:
    """Atomic-write the full text of a replied-to message (best-effort).

    Written only for an immediate (not queued), whole-message (not
    highlighted), reply to an older (not latest) message — see
    ``aipager.bot.session_ops._build_reply_context``. Capped at 4000
    chars (Telegram's own message ceiling is 4096) even if the caller
    already truncated — defense in depth, mirrors ``write_snapshot``'s
    atomic-replace + 0600 pattern.
    """
    content = f"{header}\n\n{full_text[:4000]}" if header else full_text[:4000]
    path = reply_context_path(session_name)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write reply context file %s", path, exc_info=True)


def clear_reply_context_file(session_name: str) -> None:
    try:
        reply_context_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def clear_session_files(session_name: str) -> None:
    """Best-effort removal of both /tmp files this feature writes.

    Called on session GONE (crash / socket-vanish / SessionEnd hook, via
    ``SessionRegistry.transition``) and on explicit ``/kill`` (via
    ``SessionRegistry.remove``) — design.md Part 5. Never called for a
    resumed session: both files are fully overwritten on the next
    ``_inject_prompt`` regardless.
    """
    clear_snapshot(session_name)
    clear_reply_context_file(session_name)
