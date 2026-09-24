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
import os
from datetime import datetime, timezone
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


# Transcript timestamps are millisecond ISO-8601 (``2026-09-03T10:59:17.897Z``);
# the turn-start stamp is ``time.time()`` taken when the hook datagram
# landed. Same machine, same clock — the slack only absorbs the hook's own
# delivery latency, so text written the instant a turn began is never
# mistaken for the previous turn's.
TIMESTAMP_SLACK_SECONDS: float = 2.0


def _entry_timestamp(entry: dict) -> float | None:
    """Epoch seconds of a transcript entry's ``timestamp``, or None when
    the field is absent or unparseable."""
    raw = entry.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"  # fromisoformat accepts "Z" only on 3.11+
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _entry_predates(entry: dict, since: float) -> bool:
    """True when the entry carries a timestamp older than ``since`` (less
    the slack). An entry with no usable timestamp is NOT treated as old:
    the file-level mtime guard in session_monitor already stands in front
    of this one, and refusing every timestamp-less transcript would blind
    the recovery path on any format change."""
    ts = _entry_timestamp(entry)
    return ts is not None and ts < since - TIMESTAMP_SLACK_SECONDS


def extract_last_response(
    transcript_path: str, *, since: float | None = None,
) -> str | None:
    """Return the raw markdown of the last assistant text response.

    Reads only the last 20 lines of the JSONL file (efficient for large
    transcripts), finds the last assistant message, and joins all text
    content blocks.

    Returns ``""`` when the newest assistant entry is Claude Code's
    no-response placeholder — the turn is over and produced nothing.
    Returns None on any error or if no assistant text is found.

    ``since`` (wall-clock epoch seconds) is the current turn's start. When
    given, the text found is accepted only if its own entry is not older
    than that; otherwise ``""`` is returned, exactly as for the
    placeholder. The scan skips assistant entries with no text (a turn
    that ended on tool calls, a background-job re-entry that produced
    nothing), so without this guard the newest text in the file is the
    PREVIOUS turn's answer — and the caller would publish it as the reply
    to the current prompt.

    The placeholder deliberately STOPS the scan rather than being skipped
    over. Continuing would find the newest *real* assistant text, which by
    definition belongs to an earlier turn, and the caller would publish it
    as the answer to the current prompt. A stale-but-plausible answer is
    much harder for a remote operator to catch than an empty one, so the
    empty result is the safe direction. Callers distinguish it from None
    to suppress their own cached-summary fallbacks.
    """
    return extract_last_response_entry(transcript_path, since=since)[0]


def extract_last_response_entry(
    transcript_path: str, *, since: float | None = None,
) -> tuple[str | None, float | None]:
    """``extract_last_response`` plus the wall-clock timestamp of the
    entry the text came from (None when there is no such text, or the
    entry carries no usable timestamp). The already-IDLE late-answer path
    needs it to tell an answer written before the last confirmed delivery
    from a genuinely undelivered one (roadmap 8.39)."""
    try:
        with open(transcript_path, "r") as f:
            tail = deque(f, maxlen=20)
    except (FileNotFoundError, PermissionError, OSError) as e:
        log.debug("Cannot read transcript %s: %s", transcript_path, e)
        return None, None

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
            return "", None

        content = entry.get("message", {}).get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)

        if texts:
            if since is not None and _entry_predates(entry, since):
                log.info(
                    "transcript's newest text (%s) predates the current "
                    "turn — treating the turn as having produced none",
                    entry.get("timestamp"),
                )
                return "", None
            return (_strip_leaked_tool_xml("\n\n".join(texts)),
                    _entry_timestamp(entry))

    return None, None


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


def read_turn_stream(
    transcript_path: str, offset: int,
) -> tuple[list[tuple[str, str]], int]:
    """Read assistant text and tool calls appended after *offset*, in file order.

    Returns ``(items, new_offset)`` where *items* is a list of
    ``("text", block)`` and ``("tool", tool_name)`` pairs in the order they
    appear in the file. Text blocks are NOT joined or cleaned here — that is
    ``read_turn_text``'s job.

    The interleaving is the point: Claude Code fires its PreToolUse hook
    *before* it flushes the assistant entry, so hook arrival order says
    nothing about whether a piece of prose came before or after a tool call.
    The transcript's own byte order does.

    *new_offset* is the byte offset of the last **complete** line consumed — a
    trailing line that does not end in ``\\n`` (i.e. still being written) is NOT
    consumed, so the next call picks it up once it is complete.

    Returns ``([], offset)`` on any error — the function never raises.

    The same walk as :func:`read_turn_blocks` with the message id dropped —
    kept as its own name so the transcript-fallback path's contract (and
    its tests) are untouched. ``"queue"`` items (see :func:`read_turn_blocks`)
    are dropped here rather than passed through: this function's callers
    (``_read_stream_text`` via ``read_turn_text``) treat anything that
    isn't ``"tool"`` as prose text to append to the draft, and a 4-tuple
    has no ``.split()`` — passing one through would raise deep inside the
    live card's rendering path instead of here.
    """
    items, new_offset = read_turn_blocks(transcript_path, offset)
    return ([(kind, value) for kind, value, _mid in items
             if kind != "queue"], new_offset)


def read_turn_blocks(
    transcript_path: str, offset: int,
) -> tuple[list[tuple[str, str, str]], int]:
    """:func:`read_turn_stream` plus the assistant message each item came
    from: ``("text", block, message_id)`` / ``("tool", tool_name,
    message_id)``, *message_id* being the entry's ``message.id`` (``""``
    when absent). A ``{"type": "queue-operation", ...}`` line (design.md
    "turn anchor follows consumption") yields
    ``("queue", (operation, reason, content, timestamp), "")`` instead —
    still a 3-tuple, so every ``for kind, value, mid in items:`` unpack
    keeps working; here ``value`` is itself a 4-tuple and ``mid`` is
    always ``""`` (a queue-operation line carries no ``message.id``).

    Claude Code writes one line per content block, every line of one
    message carrying the same id, and flushes a message's lines together
    once its tool-result round ends. The id is what ties a sentence the
    MessageDisplay hook already delivered (it carries the same
    ``message_id``) to the tool_use blocks of ITS message — the exact
    "which rows did this sentence introduce" the busy card anchors on
    ("transcript-exact-sentence-anchors"). Same offset, partial-line and
    error rules as :func:`read_turn_stream`.
    """
    if not transcript_path:
        return ([], offset)
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        log.debug("read_turn_stream: cannot open %s: %s", transcript_path, exc)
        return ([], offset)

    if not raw:
        return ([], offset)

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

    items: list[tuple[str, str, str]] = []
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
        etype = entry.get("type")
        if etype == "queue-operation":
            # design.md "turn anchor follows consumption": Claude Code
            # writes one of these at the moment it enqueues/dequeues/
            # drops/absorbs a piece of queued input. Carried as its own
            # 3-tuple item — `value` is itself a 4-tuple
            # (operation, reason, content, timestamp) — so every
            # existing `for kind, value, mid in items:` unpack keeps
            # working; `mid` is always "" since this line carries no
            # `message.id`. Callers that don't know about "queue" treat
            # it as inert (see read_turn_stream's filter below).
            items.append(("queue", (
                entry.get("operation", ""), entry.get("reason"),
                entry.get("content", ""), entry.get("timestamp"),
            ), ""))
            continue
        if etype != "assistant":
            continue
        # Skip the no-response placeholder but keep the offset advancing —
        # streaming it would push "No response requested." into the draft
        # as though it were the reply taking shape.
        if is_no_response_entry(entry):
            continue
        message = entry.get("message", {})
        if not isinstance(message, dict):
            continue
        mid = message.get("id", "")
        mid = mid if isinstance(mid, str) else ""
        content = message.get("content", [])
        if isinstance(content, str):
            # A bare-string content is one text block, not a run of
            # one-character blocks.
            content = [content]
        elif not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                kind = block.get("type")
                if kind == "text":
                    t = _strip_leaked_tool_xml(block.get("text", ""))
                    if t:
                        items.append(("text", t, mid))
                elif kind == "tool_use":
                    items.append(("tool", block.get("name", ""), mid))
            elif isinstance(block, str) and block:
                t = _strip_leaked_tool_xml(block)
                if t:
                    items.append(("text", t, mid))

    return (items, offset + consumed_bytes)


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
    items, new_offset = read_turn_stream(transcript_path, offset)
    texts = [t for kind, t in items if kind == "text"]
    if not texts:
        return ("", new_offset)
    return ("\n\n".join(texts), new_offset)


def is_synthetic_entry(entry: dict) -> bool:
    """True for an assistant entry no model produced (``message.model ==
    "<synthetic>"``): an API error (``isApiErrorMessage`` true — expired
    auth, rate limit, 5xx) or the no-response placeholder. Neither is
    something Claude *said*, so a "where did it leave off" preview must
    look past them (roadmap 8.34). The turn-summary path
    (:func:`extract_last_response`) deliberately does NOT use this: there
    an API error is the turn's outcome and raises the error card."""
    message = entry.get("message")
    return isinstance(message, dict) and message.get("model") == SYNTHETIC_MODEL


# Backward tail read for :func:`last_real_assistant_text`. A reply can sit
# behind a long run of tool traffic (tool results carry whole files), so
# the reader walks back chunk by chunk, but never past the byte cap — a
# transcript with no real reply in its last few MiB gets the empty state
# rather than a full-file scan.
_TAIL_CHUNK_BYTES = 64 * 1024
_TAIL_MAX_BYTES = 4 * 1024 * 1024


def _entry_text(entry: dict) -> str:
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", [])
    if isinstance(content, str):
        content = [content]
    elif not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                texts.append(text)
        elif isinstance(block, str):
            texts.append(block)
    return "\n\n".join(t for t in texts if t)


def last_real_assistant_text(transcript_path: str) -> str | None:
    """Return the newest assistant text a model actually wrote, or None.

    Walks the file backwards from its end in ``_TAIL_CHUNK_BYTES`` steps
    (seek-based: cost is bounded by how far back the reply sits, capped
    at ``_TAIL_MAX_BYTES``, not by the file's size). Synthetic entries
    (:func:`is_synthetic_entry`) and text-less assistant entries are
    skipped, so a trailing API error falls back to the reply before it.
    A trailing line with no newline yet is still being written and is
    ignored. Returns None on any error or when nothing real is found.

    A line longer than a chunk is assembled from its pieces once, when
    its start is found — never re-split per chunk — so a multi-MiB tool
    result in front of the reply costs one pass over its bytes.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            pos = end
            # Pieces of the line being assembled, in reverse file order.
            pending: list[bytes] = []
            tail_dropped = False
            while pos > 0 and end - pos < _TAIL_MAX_BYTES:
                step = min(_TAIL_CHUNK_BYTES, pos)
                pos -= step
                fh.seek(pos)
                parts = fh.read(step).split(b"\n")
                if len(parts) == 1:
                    pending.append(parts[0])
                    continue
                # parts[-1] + pending ends where the previously seen
                # newline was — or, before any newline was seen, it is
                # the half-written last line: never a complete record.
                last = parts[-1] + b"".join(reversed(pending))
                candidates = parts[1:-1]
                if tail_dropped:
                    candidates.append(last)
                tail_dropped = True
                # parts[0] may start mid-line unless this is the file's start.
                if pos > 0:
                    pending = [parts[0]]
                else:
                    pending = []
                    candidates.insert(0, parts[0])
                for raw in reversed(candidates):
                    text = _real_text_of_line(raw)
                    if text:
                        return text
            if pos == 0 and pending and tail_dropped:
                return _real_text_of_line(b"".join(reversed(pending))) or None
    except (FileNotFoundError, PermissionError, OSError) as e:
        log.debug("Cannot read transcript %s: %s", transcript_path, e)
    return None


def _real_text_of_line(raw: bytes) -> str:
    """The model-written text of one JSONL line, or "" — never raises: a
    malformed line must not break the Mini App's poll or the picker."""
    line = raw.strip()
    if not line or b'"assistant"' not in line:
        return ""
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return ""
    if is_synthetic_entry(entry):
        return ""
    return _strip_leaked_tool_xml(_entry_text(entry)).strip()


def _collapse_preview(raw: str | None, max_chars: int) -> str:
    if not raw:
        return ""
    collapsed = " ".join(raw.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def last_assistant_preview(transcript_path: str, max_chars: int = 200) -> str:
    """Return a single-line, length-capped preview of the last assistant text.

    Used by the /resume picker, the post-resume confirmation and the GONE
    snapshot to remind the user where they left off. Built on
    :func:`last_real_assistant_text`, so a synthetic API error or
    no-response placeholder is never the preview — the real reply before
    it is. Whitespace is collapsed to single spaces; if the text exceeds
    ``max_chars`` an ellipsis is appended. Returns "" on any error
    (missing transcript, no real assistant text, etc.) so callers can
    render "no preview" unconditionally.
    """
    if not transcript_path:
        return ""
    return _collapse_preview(last_real_assistant_text(transcript_path), max_chars)


# (path, max_chars) → (size, mtime_ns, preview). The Mini App polls a
# session's detail every 2.5 s per open page; one os.stat per poll is the
# whole cost while the transcript is unchanged. Bounded: cleared whole
# when it outgrows the cap (entries are cheap to rebuild).
_PREVIEW_CACHE_MAX = 256
_preview_cache: dict[tuple[str, int], tuple[int, int, str]] = {}


def cached_last_assistant_preview(
    transcript_path: str, max_chars: int = 200,
) -> str:
    """:func:`last_assistant_preview`, re-read only when the transcript's
    (size, mtime) changed since the last call for the same path."""
    if not transcript_path:
        return ""
    try:
        st = os.stat(transcript_path)
    except OSError:
        _preview_cache.pop((transcript_path, max_chars), None)
        return ""
    key = (transcript_path, max_chars)
    hit = _preview_cache.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    preview = _collapse_preview(last_real_assistant_text(transcript_path), max_chars)
    if len(_preview_cache) >= _PREVIEW_CACHE_MAX and key not in _preview_cache:
        _preview_cache.clear()
    _preview_cache[key] = (st.st_size, st.st_mtime_ns, preview)
    return preview
