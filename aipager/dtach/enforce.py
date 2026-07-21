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
import os
from pathlib import Path
from typing import Iterator

from aipager import safety
from aipager.policy_snapshot import read_snapshot

# Marker our deny reasons carry (see deny_decision_json). Once it appears
# in a tool_result this turn, every later tool call is sticky-blocked.
_BLOCK_MARKER = "aipager safety policy"


def _iter_lines_reversed(
    path: str | Path, chunk_bytes: int = 65536,
) -> Iterator[str]:
    """Yield lines from ``path`` in reverse (last line first).

    Streams the file in ``chunk_bytes`` chunks from EOF backwards; only
    the tail actually consulted lands in memory. Partial bytes at a
    chunk boundary are buffered until the previous chunk is read, so
    multi-byte UTF-8 characters never get split mid-sequence. Files
    with no trailing newline still yield their final line. Blank lines
    are yielded as empty strings — callers filter as needed.

    Malformed UTF-8 falls back to ``errors="replace"`` per line rather
    than raising, matching the caller's existing
    "skip lines we can't parse" semantics.
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        if pos == 0:
            return
        fragment = b""
        while pos > 0:
            read_size = min(chunk_bytes, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + fragment
            parts = buf.split(b"\n")
            if pos > 0:
                # Leading part may continue into the preceding chunk.
                fragment = parts[0]
                complete = parts[1:]
            else:
                fragment = b""
                complete = parts
            for line in reversed(complete):
                try:
                    yield line.decode("utf-8")
                except UnicodeDecodeError:
                    yield line.decode("utf-8", errors="replace")


def _tool_result_text(entry: dict) -> str:
    """Concatenated text of any tool_result blocks in a transcript entry."""
    content = (entry.get("message") or entry).get("content")
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                for piece in c:
                    if isinstance(piece, dict):
                        out.append(str(piece.get("text", "")))
                    else:
                        out.append(str(piece))
    return " ".join(out)


def _is_tool_result(entry: dict) -> bool:
    """True if a transcript entry is a tool-result carrier.

    Claude records tool results as ``type:"user"`` entries whose content
    is a list of ``tool_result`` blocks — they are NOT user prompts and
    must be skipped when locating the prompt that governs origin.
    """
    content = (entry.get("message") or entry).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _origin_from_transcript(path: str | None) -> str:
    """`"telegram"` if the governing user prompt carries the marker, else
    `"terminal"`. Fail-closed to `"telegram"` when unreadable.

    Streams the transcript from EOF backwards and short-circuits on the
    last genuine user *prompt* — tool-result entries (also
    ``type:"user"``) are skipped. Without that skip, every tool call
    after the first in a turn would see a marker-less tool_result as the
    "last user message" and be misread as terminal → a safety bypass.
    """
    if not path:
        return "telegram"
    try:
        for line in _iter_lines_reversed(path):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "user":
                continue
            if _is_tool_result(entry):
                continue  # tool-results are type:"user" but aren't prompts
            text = _user_text(entry)
            first = text.split("\n", 1)[0].lstrip() if text else ""
            return "telegram" if first.startswith("[via Telegram") else "terminal"
    except OSError:
        return "telegram"
    return "telegram"


def _turn_already_blocked(path: str | None) -> bool:
    """True if a tool call in the **current turn** was already blocked by
    the safety policy.

    Streams from EOF backwards: returns True the moment we encounter a
    tool_result carrying the deny marker, and returns False the moment
    we cross the governing user prompt (anything before it belongs to a
    prior turn and doesn't count). This makes a block *sticky* for the
    rest of the turn — once one tool is denied, every later tool call
    is denied too — so an agent can't dodge a pattern with a reworded
    command (e.g. a glob). Per-turn only: a fresh user prompt clears it.
    """
    if not path:
        return False
    try:
        for line in _iter_lines_reversed(path):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _BLOCK_MARKER in _tool_result_text(entry):
                return True
            if entry.get("type") == "user" and not _is_tool_result(entry):
                return False  # crossed into the prior turn; stop scanning
    except OSError:
        return False
    return False


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

    # Sticky turn-block: if any tool was already blocked this turn, deny
    # everything else until the next user prompt — no pattern-dodging
    # workarounds (the agent will otherwise reword the command to evade
    # the specific matcher).
    if _turn_already_blocked(data.get("transcript_path")):
        return {
            "tool": tool_name,
            "reason": ("session halted — a prior tool call this turn was "
                       "blocked by safety policy; start a new request"),
        }

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
