"""PreToolUse safety enforcement (Phase E).

Runs inside the ``aipager-hook`` process (not the daemon). Decides
whether a Telegram-driven tool call must be blocked, by:
- determining the prompt origin from the transcript marker,
- reading the daemon-written policy snapshot for the session,
- running the pure matchers in :mod:`aipager.safety`.

``decide()`` is pure-ish (only reads files) and unit-tested. The hook
turns its result into a Claude Code deny decision + a daemon notify.
"""

from __future__ import annotations

import json
from pathlib import Path

from aipager import safety
from aipager.policy_snapshot import read_snapshot


def _origin_from_transcript(path: str | None) -> str:
    """`"telegram"` if the last user message carries the marker, else
    `"terminal"`. Fail-closed to `"telegram"` when unreadable."""
    if not path:
        return "telegram"
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return "telegram"
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "user":
            continue
        text = _user_text(entry)
        first = text.split("\n", 1)[0].lstrip() if text else ""
        return "telegram" if first.startswith("[via Telegram") else "terminal"
    return "telegram"


def _user_text(entry: dict) -> str:
    """Extract the user message text from a transcript entry."""
    msg = entry.get("message", entry)
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
            if isinstance(block, str):
                return block
    return ""


def decide(data: dict) -> dict | None:
    """Return a block descriptor `{tool, reason}` if the PreToolUse call
    must be denied, else None (allow). Pure aside from file reads."""
    if data.get("hook_event_name") != "PreToolUse":
        return None
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if _origin_from_transcript(data.get("transcript_path")) == "terminal":
        return None  # terminal users are unrestricted

    session = data.get("session", "")
    snap = read_snapshot(session)
    if snap is None:
        # Fail-closed: no snapshot → apply the built-in floor, no bypass.
        snap = {
            "bypass_safety": False,
            "deny_tools": [],
            "allow_tools": [],
            "deny_paths_no_access": list(safety.DENY_PATHS_NO_ACCESS),
            "deny_paths_no_write": list(safety.DENY_PATHS_NO_WRITE),
            "deny_bash_patterns": list(safety.DENY_BASH_PATTERNS),
        }
    if snap.get("bypass_safety"):
        return None  # owner

    no_access = tuple(snap.get("deny_paths_no_access", ()))
    no_write = tuple(snap.get("deny_paths_no_write", ()))
    bash_pats = tuple(snap.get("deny_bash_patterns", ()))
    deny_tools = tuple(snap.get("deny_tools", ()))
    allow_tools = tuple(snap.get("allow_tools", ()))

    reason = (
        safety.path_violation(tool_name, tool_input, no_access, no_write)
        or (safety.bash_violation(tool_input.get("command", ""), bash_pats)
            if tool_name == "Bash" else None)
        or safety.tool_violation(tool_name, deny_tools, allow_tools)
    )
    if reason:
        return {"tool": tool_name, "reason": reason}
    return None


def deny_decision_json(reason: str) -> str:
    """Claude Code PreToolUse deny payload (stdout)."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"aipager safety policy: {reason}",
        }
    })
