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
import re
from pathlib import Path
from typing import Iterator

from aipager import safety
from aipager.policy_snapshot import (
    FLOOR_SNAPSHOT,
    read_snapshot,
    reply_context_path,
)

# Marker our deny reasons carry (see deny_decision_json). Once it appears
# in a tool_result this turn, every later tool call is sticky-blocked.
_BLOCK_MARKER = "aipager safety policy"

# A self-triggered continuation turn's prompt (design.md "model Claude
# Code background-agent jobs") — Claude Code wakes a session back up when a
# background agent it launched finishes, with a synthetic UserPromptSubmit
# whose prompt carries this prefix instead of any human-typed text. Both
# scan functions below must skip a transcript entry carrying it, exactly
# the way they already skip tool-result carriers: it is not the prompt
# that governs origin, and it is not the prior-turn boundary for the
# sticky-block scan either. A private module constant, deliberately not
# shared via import with hook_receiver.py / notify_hook.py's own copies —
# see entrypoints.md's "NOT exported" note.
#
# Scope of this guarantee (review-1#rev-iter1-002): the skip below is a
# raw prefix match on the transcript entry's own text, with no signature
# or session-side correlation to the real task-notification Claude Code
# generates. In SCOPED/TEAM mode this is safe by construction —
# `session_ops._inject_prompt` always prepends the "[via Telegram ·
# @label ...]\n" marker before a user's own free-text body, and that
# marker always wins the `startswith`/`lstrip().startswith()` checks here
# and in hook_receiver.py/notify_hook.py — so a Telegram user typing a
# message that literally starts with "<task-notification>" cannot spoof
# a continuation. In PERSONAL mode (`_prompt_marker` returns `""` — no
# team configured, the common default), a user-typed message starting
# with this literal string is genuinely indistinguishable from a real
# continuation. This is accepted, not a new hole: personal mode has no
# role/bypass separation to leak in the first place (no snapshot merge,
# no `bypass_safety` at stake), matching design.md's own success-criteria
# language, which is scoped to "a session whose real prompt carried the
# Telegram marker."
_TASK_NOTIFICATION_PREFIX = "<task-notification>"

# How Claude Code records a slash command in the transcript (roadmap
# 8.74): a non-meta user entry
# ``<command-message>x</command-message>\n<command-name>/x</command-name>\n
# <command-args>…</command-args>``, the expanded body following as an
# ``isMeta`` entry. The text carries no aipager marker (a marker line
# would break the command), so whether it came from Telegram is read from
# the session's snapshot instead: see ``_command_from_telegram``.
_COMMAND_NAME = re.compile(r"<command-name>\s*(/[^<\s]+)\s*</command-name>")


def _iter_raw_lines_reversed(
    path: str | Path, chunk_bytes: int = 65536,
) -> Iterator[bytes]:
    """Yield the raw lines of ``path`` last first, as bytes.

    Yields exactly ``data.split(b"\\n")`` reversed: the empty piece after
    a final newline is yielded, ``\\r`` is kept, and a file with no
    trailing newline still yields its final line. An empty file yields
    nothing (not ``[b""]``), as the scans have always expected.

    Linear in the bytes read. The file is read from EOF backwards in
    ``chunk_bytes`` chunks through the builtin ``open`` (so a caller that
    stops early reads only the tail), and newlines are found with
    ``rfind`` inside each chunk. A line longer than a chunk is kept as a
    list of pieces and joined once, when its start is found; the old
    reader re-copied and re-split a growing fragment on every chunk,
    which was quadratic in the line's length and took seconds on one
    40 MB line. Peak memory is about twice the longest line plus a chunk.
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        pending: list[bytes] = []  # pieces of the open line, newest first
        while pos > 0:
            read_size = min(chunk_bytes, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size)
            end = len(buf)
            nl = buf.rfind(b"\n", 0, end)
            while nl != -1:
                if pending:
                    pending.append(buf[nl + 1:end])
                    line = b"".join(reversed(pending))
                    pending = []  # drop the pieces before the consumer runs
                    yield line
                    del line
                else:
                    yield buf[nl + 1:end]
                end = nl
                nl = buf.rfind(b"\n", 0, end)
            pending.append(buf[:end])
        if pending:
            line = b"".join(reversed(pending))
            pending = []
            yield line


def _iter_lines_reversed(
    path: str | Path, chunk_bytes: int = 65536,
) -> Iterator[str]:
    """Yield lines from ``path`` in reverse (last line first).

    The same lines as :func:`_iter_raw_lines_reversed`, decoded. Streams
    the file in ``chunk_bytes`` chunks from EOF backwards; only the tail
    actually consulted lands in memory. A line is decoded only once it is
    complete, so multi-byte UTF-8 characters never get split
    mid-sequence. Files with no trailing newline still yield their final
    line. Blank lines are yielded as empty strings — callers filter as
    needed.

    Malformed UTF-8 falls back to ``errors="replace"`` per line rather
    than raising, matching the caller's existing
    "skip lines we can't parse" semantics.
    """
    for raw in _iter_raw_lines_reversed(path, chunk_bytes=chunk_bytes):
        try:
            yield raw.decode("utf-8")
        except UnicodeDecodeError:
            yield raw.decode("utf-8", errors="replace")


# ---- which lines the scans parse ----------------------------------------
#
# Both scans walk the transcript back to the governing prompt and used to
# ``json.loads`` every line on the way, seconds of CPU per tool call on a
# long turn. ``_needs_parse`` decides from the raw bytes whether a line
# can change a scan's answer; only those lines go through the old
# per-line logic, unchanged. Every skip below is safe for anything Claude
# Code writes (its serializer is JSON.stringify: no duplicate keys, no
# ``\\u``-escaped ASCII, the entry's own ``"message"`` key comes before
# any nested one). Three documented classes of line Claude Code never
# writes are skipped where the old code parsed them:
#   1. a user line whose FIRST ``"message"`` key is a nested object laid
#      out like a tool result while the entry itself is a prompt;
#   2. duplicate keys where the layout check looks: a second
#      ``"message"``, a second ``"content"`` in the message, or a second
#      ``"type"`` in its first block. The check reads the first,
#      ``json.loads`` keeps the last, so a tool result there can hide a
#      prompt;
#   3. a line that is no prompt candidate but made the old parse raise
#      something other than JSONDecodeError (nesting ~10,000 deep, an
#      integer over 4,300 digits, a truthy non-dict ``"message"`` in the
#      sticky scan). The skip is per scan: the origin scan skips such a
#      line whatever words it holds; the sticky scan skips it only when
#      one of the marker words is missing. The old code denied those
#      only through that accident, so on a terminal turn such a line
#      holding all three words now allows where the old code denied.
# Anything else is parsed, so it behaves (and raises) exactly as before.

# First byte ``bytes.strip()`` would keep: ``\S`` on a bytes pattern is
# exactly its whitespace set (space, \t, \n, \r, \x0b, \x0c).
_FIRST_NON_SPACE = re.compile(rb"\S")
# A ``\u``-escaped printable ASCII character: the only way a JSON writer
# can spell a key or value (``"typ\u0065"``, ``\u0061ipager``) without
# its literal bytes, which every other rule below looks for.
_ASCII_ESCAPE = re.compile(rb"\\u00[2-7][0-9a-fA-F]")
# A key ``type`` with value ``user``. Text inside a JSON string has its
# quotes escaped (``\"``), so user-controlled text cannot form this run.
_USER_TYPE = re.compile(rb'"type"\s*:\s*"user"')
# A tool-result carrier, matched (anchored, ``.match``) at the line's
# FIRST ``"message"``: a user message whose content's first block is a
# tool_result (Claude Code may write ``tool_use_id`` before ``type``). A
# genuine prompt (string content, text or image blocks) never matches.
# Anchored, because a prompt's own ``"message"`` comes first and a nested
# tool-result-shaped one later in the line must not hide it.
_TOOL_RESULT_LAYOUT = re.compile(
    rb'"message"\s*:\s*\{\s*"role"\s*:\s*"user"\s*,\s*"content"\s*:\s*\[\s*\{\s*'
    rb'(?:"tool_use_id"\s*:\s*"[^"\\]*"\s*,\s*)?"type"\s*:\s*"tool_result"'
)
# The words of _BLOCK_MARKER, as bytes that must appear in the line.
# ``_tool_result_text`` joins a tool result's pieces with a space, so
# ``"aipager"`` + ``"safety policy"`` carries the marker without its
# contiguous bytes. It also applies ``str()`` to a list or non-str piece,
# i.e. Python ``repr``, which spells a non-printable character as an
# escape (U+009A becomes ``\x9a``). An escape starts with a backslash and
# only its trailing hex digits (0-9, a-f) can join the literal text after
# it, so a word whose FIRST letter is a hex digit can be completed by an
# escape the raw line never spells: ``\x9a`` + ``ipager`` reads
# "aipager". That is why the third word is ``ipager``. "safety" and
# "policy" start with non-hex letters, so every letter of them is literal
# in the line (or hidden by an ASCII escape, which _ASCII_ESCAPE catches).
_MARKER_WORDS = (b"safety", b"policy", b"ipager")
# Unrolled for _needs_parse (a generator per line costs more than the
# check itself on a long turn), rarest-looking word first:
# 1 = "policy", 2 = "safety", 3 = "ipager".
_MARKER_WORD_1, _MARKER_WORD_2, _MARKER_WORD_3 = (
    _MARKER_WORDS[1], _MARKER_WORDS[0], _MARKER_WORDS[2])


def _needs_parse(raw: bytes, sticky: bool) -> bool:
    """True when ``raw`` could change the scan's answer, so it must go
    through the old per-line logic; False only when skipping it provably
    cannot (see the block comment above for the documented exceptions).

    - Blank (only ``bytes.strip()`` whitespace): the old code stripped
      and skipped it. Not ``raw.strip()``: that copies a 40 MB line.
    - First non-space byte not ``{``: garbage, non-object JSON, or a line
      led by whitespace only ``str.strip`` knows (NBSP, ``\\x1c``). The
      old code parsed it and skipped a decode error or raised on a
      non-dict; parsing keeps that outcome exactly.
    - An ASCII ``\\u`` escape: a key or value could be hidden from every
      byte rule, so parse.
    - A ``"type":"user"`` pair that is not a tool-result layout at the
      first ``"message"``: a prompt candidate (or a line the old helpers
      raise on). Without the pair (and without escapes) no object in the
      line is a user entry, so the origin scan ignores it and the sticky
      scan ignores it unless it holds the marker. With the layout, the
      entry is a tool result: never a prompt nor a turn boundary.
    - The first ``"message"`` byte run is trusted as the entry's own
      key only when the byte before it is not a backslash. A quote
      inside a JSON string is always written ``\\"`` (or as ``\\u0022``,
      which the escape rule catches), so ``"x\\"message":{...}`` puts
      that run inside a key or string, not at a key. A real closing
      quote after an escaped backslash (``\\\\"``) cannot be followed
      directly by ``message"`` in valid JSON, so with no backslash in
      front, a layout match is a real key named ``message``. With one,
      the line is parsed (invalid JSON is then skipped as before).
    - Sticky scan only: all three marker words present. Without one of
      them ``_BLOCK_MARKER in _tool_result_text(entry)`` is False: every
      letter of that text comes verbatim from the line, except a hex
      digit a ``repr`` escape supplies, which is why the third word is
      ``ipager`` (see _MARKER_WORDS).
    """
    if raw[:1] != b"{":  # the usual case skips the regex
        first = _FIRST_NON_SPACE.search(raw)
        if first is None:
            return False
        if raw[first.start()] != 0x7B:  # "{"
            return True
    if b"\\u00" in raw and _ASCII_ESCAPE.search(raw):
        return True
    if (sticky and _MARKER_WORD_1 in raw and _MARKER_WORD_2 in raw
            and _MARKER_WORD_3 in raw):
        return True
    if b'"user"' in raw and _USER_TYPE.search(raw):
        at = raw.find(b'"message"')
        if (at == -1 or raw[at - 1:at] == b"\\"
                or not _TOOL_RESULT_LAYOUT.match(raw, at)):
            return True
    return False


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


def _is_injected(entry: dict) -> bool:
    """True for a ``type:"user"`` entry Claude Code wrote itself rather
    than a prompt someone sent: the compact summary ("This session is
    being continued…", ``isCompactSummary``) and meta entries
    (``isMeta``). Neither governs a turn's origin nor ends it (roadmap
    8.51): read as a prompt, a compact summary carries no Telegram marker
    and made an auto-compacted restricted turn run as "terminal", i.e.
    unrestricted, and it cleared the turn's sticky block."""
    return bool(entry.get("isCompactSummary") or entry.get("isMeta"))


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


def _origin_from_transcript(path: str | None, note_bodies=()) -> str:
    """`"telegram"` if the governing user prompt carries the marker on
    ANY line of its (possibly multi-block) text, else `"terminal"`.
    Fail-closed to `"telegram"` when unreadable.

    Streams the transcript from EOF backwards and short-circuits on the
    last genuine user *prompt* — tool-result entries (also
    ``type:"user"``) are skipped. Without that skip, every tool call
    after the first in a turn would see a marker-less tool_result as the
    "last user message" and be misread as terminal → a safety bypass.

    Checking every line (not just the first) matters once Claude can
    batch several queued Telegram messages into one prompt (design.md
    "queue handoff"): the marker line ``_inject_prompt`` prepends is only
    guaranteed to be the first line of THAT message's own text, which
    can land anywhere in the concatenated ``_user_text`` once several
    messages' bodies are joined — checking only line 1 would misread a
    Telegram-originated batch as terminal (a safety bypass) whenever the
    marker isn't in the very first block.

    A governing prompt with no marker that is a Claude Code slash-command
    record (``<command-name>/x</command-name>``) is Telegram when
    ``note_bodies`` (the canonical snapshot's ``note_bodies``: the
    Telegram messages its UserPromptSubmit consumed) holds a body whose
    first token is ``/x`` (roadmap 8.74; prompt-type commands fire that
    hook, 8.37). A slash command is typed raw, so before this a
    restricted member's ``/deliver …`` ran as an unrestricted terminal
    prompt. Any other markerless prompt stays terminal. Pure: the caller
    reads the snapshot.
    """
    if not path:
        return "telegram"
    try:
        for raw in _iter_raw_lines_reversed(path):
            if not _needs_parse(raw, sticky=False):
                continue
            line = raw.decode("utf-8", "replace").strip()
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
            if _is_injected(entry):
                continue  # written by Claude Code, not a governing prompt
            text = _user_text(entry)
            if text.lstrip().startswith(_TASK_NOTIFICATION_PREFIX):
                # A self-triggered continuation, not a governing prompt —
                # keep scanning backward for the real one (design.md "model
                # Claude Code background-agent jobs"). Without this skip,
                # every continuation turn would be misread as the LAST
                # prompt, its markerless text returning "terminal" and
                # running the continuation unrestricted regardless of the
                # original prompt's own origin — spec.md's documented
                # safety leak.
                continue
            if not text:
                return "terminal"
            for block_line in text.split("\n"):
                if block_line.lstrip().startswith("[via Telegram"):
                    return "telegram"
            if _command_from_telegram(text, note_bodies):
                return "telegram"
            return "terminal"
    except OSError:
        return "telegram"
    return "telegram"


def _command_from_telegram(text: str, note_bodies) -> bool:
    """True when ``text`` is a markerless prompt one of ``note_bodies``
    sent: a slash-command record (``<command-name>/x``) and a body whose
    first whitespace token is exactly ``/x``, or a plain prompt equal to a
    body (slash text Claude Code took as a plain prompt, e.g. a path).
    Malformed bodies (not a list of strings) are ignored; a match makes
    the prompt Telegram, which can only add rules, never remove them."""
    if not isinstance(note_bodies, (list, tuple)):
        return False
    bodies = [b for b in note_bodies if isinstance(b, str) and b.strip()]
    if not bodies:
        return False
    names = {m.group(1) for m in _COMMAND_NAME.finditer(text)}
    whole = text.strip()
    for body in bodies:
        if body.strip() == whole:
            return True
        first = body.split(None, 1)
        if first and first[0] in names:
            return True
    return False


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
        for raw in _iter_raw_lines_reversed(path):
            if not _needs_parse(raw, sticky=True):
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _BLOCK_MARKER in _tool_result_text(entry):
                return True
            if entry.get("type") == "user" and not _is_tool_result(entry):
                if _is_injected(entry):
                    continue  # not a turn boundary (8.51)
                if _user_text(entry).lstrip().startswith(
                    _TASK_NOTIFICATION_PREFIX,
                ):
                    # A continuation entry is not the prior-turn boundary —
                    # the sticky block (if any) survives across it, exactly
                    # as it survives a PreToolUse/PostToolUse pair within
                    # the same turn. Keep scanning backward.
                    continue
                return False  # crossed into the prior turn; stop scanning
    except OSError:
        return False
    return False


def _user_text(entry: dict) -> str:
    """Extract the user message text from a transcript entry.

    Concatenates EVERY text block, not just the first. When Claude
    batches several queued messages into one prompt (design.md "queue
    handoff" — the actual batching format is a confirmed unknown, see
    intent.md), the resulting ``content`` can carry multiple text blocks
    — one per original message — rather than a single one. Returning
    only the first would silently drop the marker line whenever it
    isn't in that first block, misreading a Telegram-originated turn as
    terminal (a safety bypass). Blocks are joined with ``"\\n"`` so
    ``_origin_from_transcript``'s per-line scan still finds a marker
    that started a block, wherever in the batch it landed.
    """
    msg = entry.get("message", entry)
    content = msg.get("content", "")
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


# Claude Code's per-uid temp root, where every session gets
# ``<root>/<encoded project dir>/<session id>/scratchpad``.
_ENCODED_DIR_MAX = 200  # Claude Code hashes longer names; not derivable


def _claude_temp_root() -> str:
    """``<temp dir>/claude-<uid>`` as Claude Code 2.1.283 builds it: the
    temp dir is ``$CLAUDE_CODE_TMPDIR``, else Node's ``os.tmpdir()``
    (``$TMPDIR``, else ``/tmp`` — on macOS ``$TMPDIR`` is under
    ``/var/folders``). The hook inherits Claude Code's environment."""
    base = (os.environ.get("CLAUDE_CODE_TMPDIR")
            or os.environ.get("TMPDIR") or "/tmp").rstrip("/") or "/"
    return os.path.join(base, f"claude-{os.getuid()}")


def _scratch_root(cwd: str | None, session_id) -> str:
    """The session's own Claude Code temp directory when it can be
    derived from the payload (project dir encoded as Claude Code does:
    every non-alphanumeric character becomes ``-``), else the per-uid
    root that holds it."""
    root = _claude_temp_root()
    if cwd and isinstance(session_id, str) and re.fullmatch(
        r"[A-Za-z0-9-]+", session_id,
    ):
        enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        if len(enc) <= _ENCODED_DIR_MAX:
            return f"{root}/{enc}/{session_id}"
    return root


def _write_roots(cwd: str | None, session_id) -> tuple[str, ...]:
    """Where a confined turn may write: the session's cwd (when the
    payload carries one — none means scratchpad only, fail closed) and
    its scratchpad."""
    roots = [cwd] if cwd and os.path.isabs(cwd) else []
    roots.append(_scratch_root(cwd, session_id))
    return tuple(roots)


_FAIL_CLOSED_REASON = "this tool call could not be checked, so it was denied"


def fail_closed(data: dict) -> dict | None:
    """The answer when the decision itself failed (an exception — e.g. a
    NUL byte in a path makes ``expanduser`` raise). Deny, unless the
    session's snapshot can be read and grants the owner's bypass: an
    owner's turn keeps working, anyone else's is refused rather than
    let through unchecked (it used to be allowed)."""
    try:
        snap = read_snapshot(str(data.get("session") or ""))
    except Exception:
        snap = None
    if isinstance(snap, dict) and snap.get("bypass_safety") is True:
        return None
    return {"tool": str(data.get("tool_name") or ""),
            "reason": _FAIL_CLOSED_REASON}


def _readable_snapshot(session) -> dict | None:
    """The session's snapshot when it can be read and is a dict, else
    None. Never raises: it only decides whether the owner short-circuit
    applies, and every failure must leave the old path to fail exactly as
    it did. Looks ``read_snapshot`` up in this module at call time, so a
    test's patch of ``enforce.read_snapshot`` applies here too."""
    try:
        snap = read_snapshot(session)
    except Exception:
        return None
    return snap if isinstance(snap, dict) else None


def decide(data: dict) -> dict | None:
    """Return a block descriptor `{tool, reason}` if the PreToolUse call
    must be denied, else None (allow). Pure aside from file reads. Any
    exception while deciding is a deny (:func:`fail_closed`)."""
    if data.get("hook_event_name") != "PreToolUse":
        return None
    try:
        return _decide(data)
    except Exception:
        return fail_closed(data)


def _decide(data: dict) -> dict | None:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    # Owner short-circuit: an owner's turn is allowed whatever the origin,
    # so read no transcript at all. The old sequence below allows it in
    # every branch too (terminal; telegram with the bypass; a scan error,
    # through fail_closed). Anything short of a readable dict granting
    # the bypass falls through to that sequence unchanged, its own
    # snapshot read and exceptions included.
    session = data.get("session", "")
    early = _readable_snapshot(session)
    if early is not None and early.get("bypass_safety"):
        return None

    # The Telegram messages this turn consumed: they tell a slash command
    # sent from Telegram (typed raw, no marker) from one typed in the
    # terminal (roadmap 8.74). Only a scope-mode snapshot's count: in
    # personal and legacy team mode every Telegram prompt is the
    # operator's and stays unrestricted. An unreadable snapshot names
    # none, which leaves a markerless prompt terminal as before; with no
    # snapshot there is normally no Telegram turn to hold.
    bodies = (early.get("note_bodies")
              if early is not None and early.get("scope_mode") is True
              else None)
    # A Telegram message joined this turn (roadmap 8.77). Absorbed into a
    # running turn, it is no prompt of its own in the transcript, so a
    # turn typed in the terminal would still read as terminal and run the
    # message's text with no rules. Telegram until the next fresh turn.
    joined = early is not None and early.get("joined_from_telegram") is True
    if not joined and _origin_from_transcript(
            data.get("transcript_path"), bodies or ()) == "terminal":
        return None  # terminal users are unrestricted

    session = data.get("session", "")
    snap = read_snapshot(session)
    if snap is None:
        # Fail-closed: no snapshot → apply the built-in floor, no bypass.
        # Same object policy_snapshot.merge_snapshots([]) returns for the
        # "no outstanding notes" case — one constant, two fallback sites.
        snap = dict(FLOOR_SNAPSHOT)
    if snap.get("bypass_safety"):
        return None  # owner

    # Sticky turn-block: if any tool was already blocked this turn, deny
    # everything else until the next user prompt — no pattern-dodging
    # workarounds (the agent will otherwise reword the command to evade
    # the specific matcher).
    if _turn_already_blocked(data.get("transcript_path")):
        return {
            "tool": tool_name,
            "reason": ("session halted - a prior tool call this turn was "
                       "blocked by safety policy; start a new request"),
        }

    no_access = tuple(snap.get("deny_paths_no_access", ()))
    no_write = tuple(snap.get("deny_paths_no_write", ()))
    bash_pats = tuple(snap.get("deny_bash_patterns", ()))
    deny_tools = tuple(snap.get("deny_tools", ()))
    allow_tools = tuple(snap.get("allow_tools", ()))

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    # Roles without the owner's bypass or an admin's role-deny bypass
    # write only inside the session's folder and scratchpad (roadmap
    # 8.50). The folder is the hook payload's cwd — Claude Code's, never
    # the tool input's. A snapshot that predates the field is confined.
    write_roots = (
        _write_roots(cwd, data.get("session_id"))
        if snap.get("confine_writes") is not False else None
    )
    # The one control file a turn may read: its own session's reply
    # context, which the prompt tells Claude to Read (session_ops).
    readable = (str(reply_context_path(session)),) if session else ()

    reason = (
        safety.path_violation(tool_name, tool_input, no_access, no_write,
                              cwd=cwd, write_roots=write_roots,
                              readable=readable)
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
