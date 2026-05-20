"""Hard-safety policy data for multi-scope mode (Phase A: data only).

This module holds the *built-in defaults* for the Telegram-driven
safety boundary and the role permission profiles. It is intentionally
**data-only** in Phase A — nothing here is consulted at enforcement
time yet. The ``PreToolUse`` hook starts using it in a later phase.

See ``researches/multi-scope-mode/02-security-model.md`` for the
rationale behind every entry.
"""

from __future__ import annotations

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
    r"(?:\b|^)rm\b.*\b(\.config/aipager|\.claude|\.local/share/aipager)\b",
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
