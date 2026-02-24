"""Read Claude Code transcript JSONL to extract the last assistant response.

Claude Code writes a JSONL transcript where each line is a JSON object.
Assistant messages have type="assistant" with message.content containing
text blocks. We read only the tail of the file for efficiency.

Transcript discovery: each Claude Code session writes its transcript to
~/.claude/projects/<project-hash>/<session-id>.jsonl. We find the right
one by checking which file was most recently modified.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

# Claude Code project transcript directory for this project
_TRANSCRIPT_DIR = Path.home() / ".claude" / "projects" / "-home-god-creature"

# Cache: tmux session name → (transcript_path, mtime)
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
            _TRANSCRIPT_DIR.glob("*.jsonl"),
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
            return "\n\n".join(texts)

    return None
