"""Hard-safety policy data for multi-scope mode (Phase A: data only).

This module holds the *built-in defaults* for the Telegram-driven
safety boundary and the role permission profiles. It is intentionally
**data-only** in Phase A — nothing here is consulted at enforcement
time yet. The ``PreToolUse`` hook starts using it in a later phase.

See ``researches/multi-scope-mode/02-security-model.md`` for the
rationale behind every entry.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# B1 — no-access paths (block READ *and* WRITE from Telegram).
# These hold other users' data + aipager's own bones. A Telegram session
# has no legitimate reason to touch them.
# ---------------------------------------------------------------------------
DENY_PATHS_NO_ACCESS: tuple[str, ...] = (
    "~/.claude/**",                 # transcripts live in projects/
    "~/.config/aipager/**",
    "~/.local/share/aipager/**",
    "~/.local/state/aipager/**",
)

# ---------------------------------------------------------------------------
# B2 — no-write paths (READ ok, block WRITE). Empty by default; reserved
# for operator-declared project paths.
# ---------------------------------------------------------------------------
DENY_PATHS_NO_WRITE: tuple[str, ...] = ()

# ---------------------------------------------------------------------------
# Bash command patterns denied from Telegram (case-sensitive regex).
# Covers daemon manipulation, escalation, pipe-to-shell, and — critically —
# nested ``claude`` invocations + privilege flags that would let an
# in-session user craft a privileged claude instance.
# ---------------------------------------------------------------------------
DENY_BASH_PATTERNS: tuple[str, ...] = (
    r"^(sudo|su)\b",
    r"\baipager\s+(service|config|start)\b",
    r"\bsystemctl\b.*\baipager\b",
    r"\brm\b.*(\.config/aipager|\.claude|\.local/share/aipager)",
    # Nested claude invocations + privilege flags (the flag is the
    # smoking gun; catches obfuscated binary names too).
    r"\bclaude\b",
    r"--append-system-prompt\b",
    r"--system-prompt\b",
    r"--dangerously-skip-permissions\b",
    r"--resume\b",
    r"--mcp-config\b",
)

# ---------------------------------------------------------------------------
# Built-in role permission profiles, as plain dicts so ``policy.py`` can
# import them without a circular dependency. ``policy.load_policy`` turns
# these into ``Role`` objects and lets ``policy.yaml`` override any field.
#
# Field meanings (full schema lives on ``policy.Role``):
#   bypass_safety       — skip the §3.7 hard boundary entirely (owner only)
#   bypass_role_denies  — ignore deny_tools / allow_tools (admin-style)
#   can_prompt          — may drive prompts
#   can_approve         — may tap permission buttons
# Unspecified list/bool fields fall back to ``policy.Role`` defaults
# (empty lists, auto_approve=False).
# ---------------------------------------------------------------------------
BUILTIN_ROLE_DEFAULTS: dict[str, dict] = {
    "owner": {
        "bypass_safety": True,
        "bypass_role_denies": True,
        "can_prompt": True,
        "can_approve": True,
    },
    "admin": {
        "bypass_safety": False,
        "bypass_role_denies": True,
        "can_prompt": True,
        "can_approve": True,
    },
    "user": {
        "bypass_safety": False,
        "bypass_role_denies": False,
        "can_prompt": True,
        "can_approve": True,
    },
    "read_only": {
        "bypass_safety": False,
        "bypass_role_denies": False,
        "can_prompt": False,
        "can_approve": False,
    },
}


# ---------------------------------------------------------------------------
# Pure matchers (Phase E). Used by the PreToolUse hook to decide whether a
# Telegram-driven tool call must be denied. No I/O, no daemon state — the
# caller supplies the resolved deny lists (from the policy snapshot).
# ---------------------------------------------------------------------------

# Tools whose target is a filesystem path, and the input key holding it.
_PATH_KEYS = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "Glob": "path",
    "Grep": "path",
}
_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _norm(path: str) -> str:
    """Absolute, ~-expanded path for glob matching."""
    return os.path.abspath(os.path.expanduser(str(path)))


def _matches(path: str, glob: str) -> bool:
    """True if a path matches a deny glob.

    Anchored globs (``~/.claude/**``) match by prefix — ``**`` covers the
    dir itself and everything under it. Unanchored globs (``**/*.lock``,
    ``*.lock``) match the basename / any tail.
    """
    target = _norm(path)
    g = os.path.expanduser(glob)
    if g.startswith("/"):
        if g.endswith("**"):
            base = g[:-2].rstrip("/")
            return target == base or target.startswith(base + os.sep)
        return fnmatch.fnmatch(target, g)
    tail = g[3:] if g.startswith("**/") else g
    return (
        fnmatch.fnmatch(target, g)
        or fnmatch.fnmatch(os.path.basename(target), tail)
        or fnmatch.fnmatch(target, "*/" + tail)
    )


def path_violation(
    tool_name: str, tool_input: dict,
    no_access: tuple[str, ...], no_write: tuple[str, ...],
) -> str | None:
    """Reason string if this tool touches a protected path, else None."""
    key = _PATH_KEYS.get(tool_name)
    if not key:
        return None
    raw = (tool_input or {}).get(key)
    if not raw:
        return None
    for glob in no_access:
        if _matches(raw, glob):
            return f"{tool_name} on protected path {glob}"
    if tool_name in _WRITE_TOOLS:
        for glob in no_write:
            if _matches(raw, glob):
                return f"{tool_name} write to protected path {glob}"
    return None


def bash_violation(command: str, patterns: tuple[str, ...]) -> str | None:
    """Reason string if a Bash command matches a deny pattern, else None.

    The reason is intentionally generic — it must NOT echo the matched
    regex. Returning the pattern (e.g. ``/\\bclaude\\b/``) hands an agent
    the exact filter to reverse-engineer a dodge (observed: a glob
    ``cla*-code`` read the same file). The matched pattern is still logged
    server-side for the operator.
    """
    if not command:
        return None
    for pat in patterns:
        try:
            if re.search(pat, command):
                log.info("bash_violation: command blocked by pattern %r", pat)
                return "Bash command blocked by safety policy"
        except re.error:
            continue
    return None


def tool_violation(
    tool_name: str, deny_tools: tuple[str, ...], allow_tools: tuple[str, ...],
) -> str | None:
    """Reason if the tool is denied / not in a non-empty allowlist."""
    if allow_tools and tool_name not in allow_tools:
        return f"{tool_name} not in this role's allow_tools"
    if tool_name in deny_tools:
        return f"{tool_name} is in this role's deny_tools"
    return None
