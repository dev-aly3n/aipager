"""Read Claude Code transcript JSONL to extract the last assistant response.

Claude Code writes a JSONL transcript where each line is a JSON object.
Assistant messages have type="assistant" with message.content containing
text blocks. We read only the tail of the file for efficiency.

This module never discovers transcript paths. Callers pass the path stamped
per-session from the hook payload (``TrackedSession.transcript_path``), which
is the only signal that actually ties a file to a session. An earlier
``find_transcript`` helper guessed by scanning ~/.claude/projects for the
most-recently-modified JSONL across every project on the machine; with
concurrent sessions that regularly resolved to somebody else's transcript,
which then reached Telegram. When no stamped path exists, callers fail closed.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque

log = logging.getLogger(__name__)


SYNTHETIC_MODEL = "<synthetic>"

# The text Claude Code records when a turn produced no assistant output.
# The hook payload carries no model field, so hook_receiver can only
# recognise the placeholder by this exact string.
NO_RESPONSE_TEXT = "No response requested."


def is_no_response_entry(entry: dict) -> bool:
    """True if *entry* is Claude Code's "this turn produced no text" marker.

    Claude Code writes assistant entries with ``message.model ==
    "<synthetic>"`` that no model produced, and they split into two groups
    that must be treated oppositely:

    ``isApiErrorMessage`` true
        Rate limits, expired auth, "Prompt is too long", 5xx. These must
        keep flowing through to the notify path — ``_detect_api_error``
        turns them into the error card and the retry button. Filtering
        them would silence every API-failure notification.
    ``isApiErrorMessage`` false
        Only ever ``NO_RESPONSE_TEXT``, recorded when a turn ended without
        the model emitting any text — typically the continuation prompt
        after an auto-compact.

    Only the second group is a placeholder. Discriminating on the flag
    rather than on the English text keeps error reporting intact and
    survives Anthropic rewording the placeholder.
    """
    if entry.get("isApiErrorMessage"):
        return False
    return entry.get("message", {}).get("model") == SYNTHETIC_MODEL


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


def _strip_prose_tool_xml(segment: str) -> str:
    """Apply the leak-strip regex chain to a single prose segment."""
    cleaned = _FUNCTION_CALLS_BLOCK_RE.sub("", segment)
    cleaned = _INVOKE_BLOCK_RE.sub("", cleaned)
    cleaned = _PARAMETER_BLOCK_RE.sub("", cleaned)
    return _ORPHAN_TAG_RE.sub("", cleaned)


def _strip_leaked_tool_xml(text: str) -> str:
    """Remove leaked tool-invocation XML from assistant text, fence-aware.

    Walks the text in triple-backtick-fence-aware chunks so that
    legitimate `<invoke>` / `<parameter>` / `<function_calls>` examples
    inside fenced code blocks (e.g. ```xml … ```) survive intact,
    while degraded-model leaks in prose are stripped. Empirically zero
    real leaks in production have appeared inside fences, so this loses
    nothing on the strip side.

    Splitting rule: `text.split("```")` produces alternating segments —
    index 0 is prose, index 1 is a code-fence body, index 2 is prose,
    and so on. Even indices are sanitized; odd indices are left
    verbatim. If the number of fences is odd (unbalanced / unclosed
    fence at end of text), the trailing "code" segment is
    conservatively preserved as if it were a still-open fence — the
    rare fail-open case, which prefers letting real content through
    over accidentally clipping it.

    Empty / whitespace-only input is returned unchanged. See the
    module-level regex block for the patterns handled.
    """
    if not text or ("<invoke" not in text
                    and "<parameter" not in text
                    and "<function_calls" not in text):
        return text
    parts = text.split("```")
    for i in range(0, len(parts), 2):
        parts[i] = _strip_prose_tool_xml(parts[i])
    cleaned = "```".join(parts)
    cleaned = _TRIPLE_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def extract_last_response(transcript_path: str) -> str | None:
    """Return the raw markdown of the last assistant text response.

    Reads only the last 20 lines of the JSONL file (efficient for large
    transcripts), finds the last assistant message, and joins all text
    content blocks.

    Returns ``""`` when the newest assistant entry is Claude Code's
    no-response placeholder — the turn is over and produced nothing.
    Returns None on any error or if no assistant text is found.

    The placeholder deliberately STOPS the scan rather than being skipped
    over. Continuing would find the newest *real* assistant text, which by
    definition belongs to an earlier turn, and the caller would publish it
    as the answer to the current prompt. A stale-but-plausible answer is
    much harder for a remote operator to catch than an empty one, so the
    empty result is the safe direction. Callers distinguish it from None
    to suppress their own cached-summary fallbacks.
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

        if is_no_response_entry(entry):
            return ""

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


def read_turn_text(transcript_path: str, offset: int) -> tuple[str, int]:
    """Read assistant text blocks appended to *transcript_path* after *offset*.

    Returns ``(text, new_offset)`` where *text* is all assistant text blocks
    found in the bytes starting at *offset*, joined by blank lines and passed
    through ``_strip_leaked_tool_xml``.  *new_offset* is the byte offset of
    the last **complete** line consumed — a trailing line that does not end in
    ``\\n`` (i.e. still being written) is NOT consumed and the offset is not
    advanced past it, so the next call picks it up once it is complete.

    Returns ``("", offset)`` on any error (file not found, permission denied,
    JSON decode, etc.) — the function never raises.
    """
    if not transcript_path:
        return ("", offset)
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        log.debug("read_turn_text: cannot open %s: %s", transcript_path, exc)
        return ("", offset)

    if not raw:
        return ("", offset)

    # Split on newlines, keeping the delimiter, so we can detect a partial
    # trailing line (one that doesn't end in \n yet).
    lines = raw.split(b"\n")
    # The final element is never a complete record: it is b"" when the read
    # ended on a newline, and a half-written line otherwise. Dropping it
    # unconditionally is what keeps the offset exact. Counting the empty one
    # advanced the offset a byte past the newline, so the next append lost
    # its leading "{", failed to parse, and was silently skipped — which
    # stalled the streaming draft after its first chunk.
    lines = lines[:-1]

    texts: list[str] = []
    consumed_bytes = 0
    for line_bytes in lines:
        # Each "line" here excludes the trailing \n; add 1 for the separator.
        consumed_bytes += len(line_bytes) + 1
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        # Skip the no-response placeholder but keep the offset advancing —
        # streaming it would push "No response requested." into the draft
        # as though it were the reply taking shape.
        if is_no_response_entry(entry):
            continue
        content = entry.get("message", {}).get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    texts.append(t)
            elif isinstance(block, str) and block:
                texts.append(block)

    if not texts:
        return ("", offset + consumed_bytes)

    joined = "\n\n".join(texts)
    cleaned = _strip_leaked_tool_xml(joined)
    return (cleaned, offset + consumed_bytes)


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
