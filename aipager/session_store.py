"""Per-session metadata folders + SESSION.md generation (Phase D).

Each session gets a folder at
``~/.local/share/aipager/sessions/<kind>-<abs(chat)>/<label>/`` holding:
- ``SESSION.md`` — the roster + rules, read into claude's system prompt
  at launch so it knows who can address it (without touching the user's
  global ``~/.claude/CLAUDE.md``).
- ``.aipager/meta.json`` — daemon-managed scope metadata.

The folder is regenerated from the current roster on every launch
(``/new`` and resume), so it always reflects the live config.

See ``researches/multi-scope-mode/01-architecture.md`` §3.6.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

SESSIONS_ROOT = Path.home() / ".local" / "share" / "aipager" / "sessions"


def session_folder(scope_chat_id: int, scope_kind: str, label: str) -> Path:
    """Path to a session's metadata folder. ``<kind>-<abs(chat)>/<label>``."""
    kind = scope_kind or ("group" if scope_chat_id < 0 else "dm")
    scope_seg = f"{kind}-{abs(scope_chat_id)}"
    return SESSIONS_ROOT / scope_seg / label


def _role_line(member, policy) -> str:
    """One '- **label** (role — …)' bullet describing a member."""
    role = policy.get_role(member.role) if policy else None
    if role and not role.can_prompt:
        note = "observer; cannot drive prompts"
    elif role and role.bypass_role_denies:
        note = "can call any tool"
    else:
        denies = set(getattr(member, "deny_tools", ()))
        if role:
            denies |= set(role.deny_tools)
        note = ("denied: " + ", ".join(sorted(denies))) if denies else "standard"
    return f"- **{member.label}** ({member.role} — {note})"


def build_session_md(scope, policy, label: str) -> str:
    """Render the SESSION.md body from a scope's roster + policy."""
    roster = "\n".join(_role_line(m, policy) for m in scope.members) or "- (none)"
    return (
        f"# Session: {label}\n"
        f'Scope: {scope.kind} "{scope.label}" (chat {scope.chat_id})\n\n'
        f"## Who can address me\n\n"
        f"{roster}\n\n"
        f"## Notes for me\n\n"
        "When users address me, respond naturally — they are real people, "
        "not roles. The `[via Telegram · @X · role:Y]` prefix on each "
        "prompt is just a routing hint; do not parrot it back in your "
        "response.\n\n"
        "This session is driven via Telegram. The following paths are "
        "blocked at the daemon level regardless of role; do not attempt "
        "them, and explain politely if asked:\n"
        "- Reading OR writing under `~/.claude/**`, `~/.config/aipager/**`, "
        "`~/.local/share/aipager/**`, `~/.local/state/aipager/**`.\n"
        "- Dangerous Bash patterns (sudo, systemctl aipager, rm on those "
        "paths, nested `claude`). See `aipager doctor --safety-check` for "
        "the full policy.\n"
    )


def write_session_files(scope, policy, label: str) -> str:
    """Create/refresh the session folder; write SESSION.md + meta.json.

    Returns the SESSION.md body (to feed --append-system-prompt).
    """
    folder = session_folder(scope.chat_id, scope.kind, label)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass

    body = build_session_md(scope, policy, label)
    md_path = folder / "SESSION.md"
    _atomic_write(md_path, body, 0o600)

    meta_dir = folder / ".aipager"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "scope_chat_id": scope.chat_id,
        "scope_kind": scope.kind,
        "label": label,
        "members": [m.id for m in scope.members],
    }
    _atomic_write(meta_dir / "meta.json", json.dumps(meta, indent=2) + "\n", 0o600)
    return body


def _atomic_write(path: Path, text: str, mode: int) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)
