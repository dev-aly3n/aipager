"""Read Claude Code transcript JSONL to extract the last assistant response.

Claude Code writes a JSONL transcript where each line is a JSON object.
Assistant messages have type="assistant" with message.content containing
text blocks. We read only the tail of the file for efficiency.

Transcript discovery: each Claude Code session writes its transcript to
~/.claude/projects/<cwd-slug>/<session-id>.jsonl, where <cwd-slug> is the
session's cwd with "/" replaced by "-". We scan every project subdir
and pick the JSONL with the most recent mtime; the existing 5-second
freshness check disambiguates between concurrent sessions.

This fallback only fires when the hook payload didn't carry
transcript_path and the registry hasn't seen one yet for this session.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)


# Long-context degradation on newer Claude models occasionally causes
# the assistant to type its tool-invocation markup as plain-text content
# instead of using structured tool_use blocks. When that happens the
# raw XML rides along in the transcript's `text` block and — without
# scrubbing — lands verbatim in the Telegram summary. These regexes
# catch the leak patterns we've observed in the wild (Anthropic-style
# tool-use XML: `<invoke ...>...</invoke>`, standalone `<parameter>`
# blocks, `<function_calls>` wrappers) plus orphan opening/closing
# tags left behind by truncated emissions. Real structured tool_use
# lives in its own content block and never comes through this path,
# so no legitimate tool call is at risk.
_INVOKE_BLOCK_RE = re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.DOTALL)
_PARAMETER_BLOCK_RE = re.compile(
    r"<parameter\b[^>]*>.*?</parameter>", re.DOTALL,
)
_FUNCTION_CALLS_BLOCK_RE = re.compile(
    r"<function_calls\b[^>]*>.*?</function_calls>", re.DOTALL,
)
_ORPHAN_TAG_RE = re.compile(
    r"</?(?:invoke|parameter|function_calls)\b[^>]*>",
)
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")


def _strip_leaked_tool_xml(text: str) -> str:
    """Remove leaked tool-invocation XML from assistant text.

    Best-effort: any residual text that looked like a valid reply
    around the XML is preserved. Empty / whitespace-only input is
    returned unchanged. See the module-level regex block for the
    patterns handled.
    """
    if not text or ("<invoke" not in text
                    and "<parameter" not in text
                    and "<function_calls" not in text):
        return text
    cleaned = _FUNCTION_CALLS_BLOCK_RE.sub("", text)
    cleaned = _INVOKE_BLOCK_RE.sub("", cleaned)
    cleaned = _PARAMETER_BLOCK_RE.sub("", cleaned)
    cleaned = _ORPHAN_TAG_RE.sub("", cleaned)
    cleaned = _TRIPLE_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()

# Root of all Claude Code project transcripts on this machine.
_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Cache: session name → (transcript_path, mtime)
# Avoids re-scanning the directory on every poll cycle
_path_cache: dict[str, tuple[str, float]] = {}


def find_transcript(session_name: str) -> str | None:
    """Find the most recently modified transcript JSONL for a session.

    Strategy: when a session just went idle, its transcript was JUST written.
    We find the JSONL that was modified most recently (within last 30s).
    We cache the result so subsequent polls don't re-scan.

    Falls back to cached path if available and file still exists.
    """
    # Check cache first — if we have a path and it was found recently, reuse it
    if session_name in _path_cache:
        cached_path, cache_time = _path_cache[session_name]
        if time.time() - cache_time < 300:  # cache for 5 minutes
            if Path(cached_path).exists():
                return cached_path

    try:
        jsonl_files = sorted(
            _PROJECTS_DIR.glob("*/*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except (FileNotFoundError, PermissionError):
        return None

    if not jsonl_files:
        return None

    # The most recently modified file is likely the one for the session
    # that just went idle. We verify by checking its mtime is very recent
    # (within 5s) to avoid misattributing another session's transcript.
    best = jsonl_files[0]
    mtime = best.stat().st_mtime
    if time.time() - mtime > 5:
        # File wasn't modified recently — probably stale
        # Fall back to cache if available
        if session_name in _path_cache:
            cached_path, _ = _path_cache[session_name]
            if Path(cached_path).exists():
                return cached_path
        return None

    path = str(best)
    _path_cache[session_name] = (path, time.time())
    log.info("[%s] Discovered transcript: %s", session_name, best.name)
    return path


def extract_last_response(transcript_path: str) -> str | None:
    """Return the raw markdown of the last assistant text response.

    Reads only the last 20 lines of the JSONL file (efficient for large
    transcripts), finds the last assistant message, and joins all text
    content blocks.

    Returns None on any error or if no assistant text is found.
    """
    try:
        with open(transcript_path, "r") as f:
            tail = deque(f, maxlen=20)
    except (FileNotFoundError, PermissionError, OSError) as e:
        log.debug("Cannot read transcript %s: %s", transcript_path, e)
        return None

    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant":
            continue

        content = entry.get("message", {}).get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)

        if texts:
            return _strip_leaked_tool_xml("\n\n".join(texts))

    return None


def _content_text(content) -> str:
    """Flatten a transcript entry's message.content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def turn_appears_complete(transcript_path: str) -> bool:
    """Best-effort: does the transcript tail show the agent finished its turn?

    This is a fallback idle detector for the session monitor. The normal
    BUSY→IDLE transition comes from Claude's Stop hook; if that hook is
    missed (e.g. the user interrupts a pending permission then immediately
    sends a new prompt), the session would otherwise animate "Thinking…"
    forever. This lets the monitor recover.

    Conservative by design: returns True ONLY when the last meaningful entry
    clearly marks turn-end — an assistant message that stopped for a reason
    other than ``tool_use``, or a user interrupt marker. A still-thinking
    turn (last entry is the user prompt, no assistant reply yet) or a
    mid-tool turn (assistant ``tool_use`` / a ``tool_result``) returns False,
    so a turn in progress is never cut short.
    """
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "r") as f:
            tail = deque(f, maxlen=40)
    except (FileNotFoundError, PermissionError, OSError):
        return False

    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = entry.get("type")
        # Hook/bookkeeping records carry no turn signal — skip past them.
        if etype in ("system", "file-history-snapshot", "summary"):
            continue
        # Newer claude-code appends sidecar records after the final
        # assistant message (last-prompt, ai-title, mode, permission-mode,
        # …). They never carry a "message" field, while real turn entries
        # (assistant/user) always do — skip anything message-less so new
        # sidecar types can't strand a finished turn in BUSY.
        if "message" not in entry:
            continue

        msg = entry.get("message") or {}
        if etype == "assistant":
            # tool_use → paused to call a tool, still mid-turn.
            # end_turn / stop_sequence / max_tokens / None → turn finished.
            return msg.get("stop_reason") != "tool_use"
        if etype == "user":
            if "Request interrupted" in _content_text(msg.get("content")):
                return True  # user aborted; agent is idle, awaiting input
            # A tool_result (agent will continue) or a fresh prompt (agent
            # hasn't answered yet) both mean the turn is still in progress.
            return False
        # Unknown tail entry — don't risk a premature idle.
        return False

    return False


def last_assistant_preview(transcript_path: str, max_chars: int = 200) -> str:
    """Return a single-line, length-capped preview of the last assistant text.

    Used by the /resume picker and the post-resume confirmation to remind
    the user where they left off. Whitespace is collapsed to single spaces;
    if the text exceeds ``max_chars`` an ellipsis is appended. Returns
    "" on any error (missing transcript, no assistant entries, etc.) so
    callers can render "no preview" unconditionally.
    """
    if not transcript_path:
        return ""
    raw = extract_last_response(transcript_path)
    if not raw:
        return ""
    collapsed = " ".join(raw.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"
