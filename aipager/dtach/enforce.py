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

from aipager import peer_auth, safety
from aipager.policy_snapshot import (
    LOOP_SENTINELS,
    SCHEDULING_TOOLS,
    adopt_loop_rules,
    floor_snapshot,
    loop_rights,
    read_trigger,
    marked_for_verification,
    merge_snapshots,
    narrowed_snapshot,
    peer_floor_snapshot,
    read_pin,
    read_snapshot,
    receipt_rules,
    reply_context_path,
    safety_floor_lists,
    safety_rules,
    snapshot_lock,
    trigger_slot_free,
    verify_origin_flagged,
    wait_for_loop,
    write_merged_snapshot,
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
    if (not sticky and b"queue-operation" in raw
            and b"cross-session-message" in raw):
        return True  # a message from another session queued in this turn
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


#: The origins Claude Code (2.1.296) gives this session's own input: its
#: person at the terminal (or aipager typing for Telegram), a suggestion
#: they accepted, the composer, the session's own continuation. Decided as
#: before. Every other kind (``peer``, ``channel``, ``remote``, ``plugin``,
#: ``unclassified``, a kind added later) is input from outside this
#: session and never runs on the session's earlier rights.
_LOCAL_KINDS = frozenset({"", "human", "suggestion", "composer",
                          "auto-continuation"})

#: A ``task-notification`` with a ``subkind`` came through Claude Code's
#: bridge (another session, an inbox, a relay), except the session's own
#: scheduled trigger.
_TRUSTED_SUBKINDS = frozenset({"scheduled-trigger"})


def _origin_kind(entry: dict) -> tuple[str, dict]:
    """What Claude Code recorded as a user entry's origin: ``(kind,
    origin)`` (``""`` and ``{}`` when it recorded none). Anything but a
    :data:`_LOCAL_KINDS` kind is never this session's human prompt,
    whatever ``isMeta`` says ("peer message authority")."""
    origin = entry.get("origin")
    if not isinstance(origin, dict):
        return "", {}
    kind = origin.get("kind")
    return (kind if isinstance(kind, str) else "?"), origin


def _own_agent(origin: dict) -> bool:
    """A message from one of this session's own background agents (its
    hand-back, its SendMessage to the main thread, an observer's report):
    Claude Code stamps ``senderTaskId`` from the sending loop, never from
    tool input, and a message from another session never carries it. The
    job's own, like its task notifications."""
    sid = origin.get("senderTaskId")
    return isinstance(sid, str) and bool(sid)


def _foreign_notification(origin: dict) -> bool:
    sub = origin.get("subkind")
    return sub is not None and sub not in _TRUSTED_SUBKINDS


#: How much of the transcript's end the owner short-circuit looks at.
OWNER_TAIL_BYTES = 1024 * 1024

_ORIGIN_OPEN_BYTES = re.compile(rb'"origin"\s*:\s*\{')
_KIND_BYTES = re.compile(rb'"kind"\s*:\s*"([^"\\]{0,64})"')
_SUBKIND_BYTES = re.compile(rb'"subkind"\s*:\s*"([^"\\]{0,64})"')
_QUEUE_LINE_BYTES = re.compile(rb'"type"\s*:\s*"queue-operation"')
#: A scheduled prompt firing: its rights are its own, never the owner
#: turn's before it (the full path reads them).
_FIRE_BYTES = re.compile(rb'"turnOrigin"\s*:\s*"scheduled"')
_SENDER_TASK_BYTES = re.compile(rb'"senderTaskId"\s*:\s*"[^"]')
#: A peer origin's last keys: found even when its body pushed the start
#: of the entry out of the tail.
_PEER_PID_BYTES = re.compile(rb'"verifiedPeerPid"\s*:')


def _outside_origin_bytes(obj: bytes) -> bool:
    """*obj*: the start of a recorded ``origin`` object, up to its first
    ``}``. Outside input unless it names a local kind (a missing or
    unreadable kind included: the stricter answer)."""
    if _SENDER_TASK_BYTES.search(obj):
        return False  # this session's own background agent
    k = _KIND_BYTES.search(obj)
    if k is None:
        return True
    kind = k.group(1).decode("utf-8", "replace")
    if kind == "task-notification":
        s = _SUBKIND_BYTES.search(obj)
        return s is not None and s.group(1).decode("utf-8", "replace") not in _TRUSTED_SUBKINDS
    return kind not in _LOCAL_KINDS


def _outside_input_bytes(data: bytes, *, cut: bool) -> bool:
    """True when *data* (raw transcript bytes) holds input from outside
    this session: a user entry whose origin kind is not local, a task
    notification from elsewhere, a verified peer's origin, a message from
    another session in Claude Code's queue, or a scheduled prompt firing
    (its own rights decide, not the turn's before it). *cut*: *data* starts
    inside a line. Structural only: a quote inside a JSON string is
    escaped, so text that merely mentions such an entry does not match."""
    if cut:
        # Starting inside a line: a message from another session longer
        # than what is read hides its start before it. A long cut line
        # (more than a quarter of the read) is taken as one (it only costs
        # the full path).
        nl = data.find(b"\n")
        first = data[:nl if nl != -1 else len(data)]
        if (b"cross-session-message" in first
                or len(first) > OWNER_TAIL_BYTES // 4):
            return True
    if _PEER_PID_BYTES.search(data) or _FIRE_BYTES.search(data):
        return True
    for m in _ORIGIN_OPEN_BYTES.finditer(data):
        close = data.find(b"}", m.end(), m.end() + 1024)
        if _outside_origin_bytes(data[m.end():close if close != -1 else m.end() + 1024]):
            return True
    i = data.find(b"<cross-session-message")
    while i != -1:
        start = data.rfind(b"\n", 0, i) + 1
        end = data.find(b"\n", i)
        end = len(data) if end == -1 else end
        if _QUEUE_LINE_BYTES.search(data, start, end):
            return True
        i = data.find(b"<cross-session-message", end)
    return False


def _foreign_input_near_end(path: str | None) -> bool:
    """True when the transcript's last :data:`OWNER_TAIL_BYTES` hold input
    from outside this session (:func:`_outside_input_bytes`). Raw bytes,
    no parse: the owner short-circuit stays cheap, and a hit only sends the
    call down the full path, which decides it exactly (an old, accounted
    message costs time, never a wrong answer)."""
    if not path:
        return False
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start_at = max(0, size - OWNER_TAIL_BYTES)
            fh.seek(start_at)
            tail = fh.read()
    except (OSError, ValueError):
        return False
    return _outside_input_bytes(tail, cut=start_at > 0)


#: The most a scheduled prompt's turn reads of its own bytes.
TURN_READ_MAX = 8 * 1024 * 1024


def _turn_region(path: str | None, start, note_bodies=()) -> dict | None:
    """What the transcript holds since byte *start*: the turn's own
    entries, read forward, for a turn a scheduled prompt may have started
    (its pick-up recorded where it began). One boundary for everything:
    entries written before it are earlier turns', whatever they are and
    whenever this turn's own entry gets written.

    Returns ``{"fire", "prompt", "outside", "untrusted", "peer_enqueues",
    "blocked"}``: whether the transcript shows this session's scheduled
    prompt firing (``turnOrigin: "scheduled"``, which Claude Code computes
    and no text can set, or a ``scheduled-trigger`` task notification);
    the first prompt a person sent (``terminal`` or ``telegram``), if any;
    input from outside the session (a channel push, a remote or plugin
    prompt, ...); a continuation relayed from elsewhere; the body hashes of
    messages from another session queued or delivered in it; whether a
    tool call in it was already blocked; ``fires`` (their texts, in order)
    and ``first`` (``"fire"`` or ``"prompt"``: what started the turn). More
    than :data:`TURN_READ_MAX`
    since the start: its first and last halves of that. None when it
    cannot tell (no start, unreadable, shorter than the start): the
    ordinary scan decides then."""
    if not path or not isinstance(start, int) or isinstance(start, bool) or start < 0:
        return None
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            if size < start:
                return None  # rewritten or cut: nothing to go by
            fh.seek(start)
            if size - start <= TURN_READ_MAX:
                data = fh.read()
            else:
                # A long turn: its start (the fire, the first prompt) and
                # its newest part. What lies between was the newest part
                # at an earlier tool call, whose check narrowed the turn
                # for good (the snapshot keeps it).
                half = TURN_READ_MAX // 2
                head = fh.read(half)
                fh.seek(size - half)
                tail = fh.read()
                head = head[:head.rfind(b"\n") + 1]
                tail = tail[tail.find(b"\n") + 1:]
                data = head + tail
    except (OSError, ValueError):
        return None
    out = {"fire": False, "fires": [], "first": None, "prompt": None,
           "outside": False, "untrusted": False, "peer_enqueues": [],
           "blocked": False}
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, RecursionError):
            continue  # a line still being written
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "queue-operation":
            if entry.get("operation") == "enqueue":
                env = peer_auth.parse_envelope(entry.get("content"))
                if env is not None:
                    body = env.get("body")
                    out["peer_enqueues"].append(
                        peer_auth.body_sha256(body) if isinstance(body, str) else "")
            continue
        if etype != "user":
            continue
        if _is_tool_result(entry):
            if _BLOCK_MARKER in _tool_result_text(entry):
                out["blocked"] = True
            continue
        kind, origin = _origin_kind(entry)
        if _own_agent(origin):
            continue
        if kind == "peer":
            # Delivered into this turn: proven like a join, or the floor.
            out["peer_enqueues"].append(_peer_sha(origin) or "")
            continue
        if kind == "task-notification":
            if _foreign_notification(origin):
                out["untrusted"] = True
            elif origin.get("subkind") == "scheduled-trigger":
                _region_fire(out, entry)
            continue
        if kind not in _LOCAL_KINDS:
            out["outside"] = True
            continue
        if entry.get("turnOrigin") == "scheduled":
            _region_fire(out, entry)
            continue
        if _is_injected(entry):
            continue
        text = _user_text(entry)
        if text.lstrip().startswith(_TASK_NOTIFICATION_PREFIX):
            continue
        if out["prompt"] is None:
            marked = (any(ln.lstrip().startswith(peer_auth.TELEGRAM_MARKER)
                          for ln in text.split("\n"))
                      or _command_from_telegram(text, note_bodies))
            out["prompt"] = "telegram" if marked else "terminal"
            if not marked:
                # Only text typed in the terminal can have started a turn a
                # scheduled snapshot was written for (its pick-up found no
                # Telegram message); Telegram text in it joined it.
                out["first"] = out["first"] or "prompt"
    return out


def _region_fire(out: dict, entry: dict) -> None:
    out["fire"] = True
    out["fires"].append(_user_text(entry))
    out["first"] = out["first"] or "fire"


def _fire_rights(session: str, text) -> dict:
    """The rights a scheduled prompt fires with: those recorded for its
    text; with none (a bare /loop's fire carries Claude Code's loop
    instructions, never the scheduled placeholder), the session's bare
    loop record; else the floor. A record never expires or is pushed out
    while the session runs, so only a bare loop's fire, a prompt scheduled
    before aipager recorded them, or a failed write lacks one."""
    rec = read_trigger(session, text)
    if rec is None and isinstance(text, str):
        # A scheduled slash command is written as Claude Code's command
        # record; its record is keyed by the text that was scheduled.
        typed = _typed_command(text)
        if typed is not None:
            rec = read_trigger(session, typed)
    if rec is None:
        rec = read_trigger(session, LOOP_SENTINELS[0])
    return rec if rec is not None else safety_rules(floor_snapshot())


_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def _typed_command(text: str) -> str | None:
    """``/name args`` for Claude Code's record of a slash command
    (``<command-name>/name</command-name>`` and its ``<command-args>``),
    else None."""
    name = _COMMAND_NAME.search(text)
    if name is None:
        return None
    args = _COMMAND_ARGS.search(text)
    return f"{name.group(1)} {args.group(1).strip() if args else ''}".strip()


#: :func:`_scheduled_view`'s answer when a scheduled prompt's turn cannot
#: be read: the ordinary path decides, never reading it as the terminal.
_UNREADABLE = ("unreadable",)


def _scheduled_view(session: str, early, path, bodies, *, adopt: bool):
    """For a turn a scheduled prompt may have started (a ``scheduled`` or
    ``loop_rules`` snapshot): ``(scan, snapshot, waiting)`` read from the
    turn's own region (:func:`_turn_region`), or None (not such a turn,
    the region cannot be read, or a person's prompt started it: the
    ordinary path decides).

    The scan's origin is the snapshot's (it holds the rights of the turn
    that scheduled the prompt); what came from outside, relayed
    continuations and messages from another session in the region still
    narrow it, as in any turn. A bare /loop's rights are adopted once its
    fire is written (*adopt*: under the lock, for good); before that the
    floor holds and *waiting* is True."""
    if not isinstance(early, dict):
        return None
    looping = "loop_rules" in early
    if not (early.get("scheduled") is True or looping):
        return None
    region = _turn_region(path, early.get("turn_start_size"), bodies)
    if region is None:
        return _UNREADABLE
    if region["first"] == "prompt":
        return None  # a person's prompt started it; a fire after is queued
    snap, waiting = early, False
    if looping:
        if region["fire"]:
            adopted = adopt_loop_rules(session) if adopt else None
            if adopted is None:
                # Not written (no lock) or a pin's look: the same rights,
                # for this call only; a refusal is soft until written.
                rights = loop_rights(early)
                adopted = ({**early, **rights, "scheduled": True}
                           if rights is not None else early)
                waiting = adopt
            snap = adopted
        else:
            waiting = True
    if len(region["fires"]) > 1:
        # Fires Claude Code ran after this one with no prompt hook: each
        # with its own rights, never wider than the turn's.
        snap = {**snap, **_strictest([safety_rules(snap),
                                      *(_fire_rights(session, f)
                                        for f in region["fires"][1:])])}
    scan = {"origin": "telegram", "entry_origin": {},
            "peer_enqueues": list(region["peer_enqueues"]),
            "untrusted": region["untrusted"] or region["outside"],
            "blocked": region["blocked"]}
    return scan, snap, waiting


def _strictest(rules: list[dict]) -> dict:
    return safety_rules(merge_snapshots(
        [{**r, "queued_at": float("-inf")} for r in rules]))


#: The last :func:`_classify_turn` result, keyed by the transcript's path,
#: size, mtime and inode and the bodies asked about: one hook process makes
#: one decision, which reads the origin through :func:`_origin_from_transcript`
#: (the seam tests patch) and the rest from the same scan.
_SCAN_CACHE: dict = {}


def _classify_turn(path: str | None, note_bodies=()) -> dict:
    key = None
    if path:
        try:
            st = os.stat(path)
            bodies = (tuple(b for b in note_bodies if isinstance(b, str))
                      if isinstance(note_bodies, (list, tuple)) else ())
            key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino, bodies)
        except OSError:
            key = None
    if key is not None and key in _SCAN_CACHE:
        hit = _SCAN_CACHE[key]
        return {**hit, "peer_enqueues": list(hit["peer_enqueues"])}
    result = _classify_turn_uncached(path, note_bodies)
    if key is not None:
        _SCAN_CACHE.clear()
        _SCAN_CACHE[key] = result
        return {**result, "peer_enqueues": list(result["peer_enqueues"])}
    return result


def _fire_origin(out: dict, entry: dict) -> dict:
    """A scheduled prompt of this session firing (``turnOrigin:
    "scheduled"``, computed by Claude Code, or a ``scheduled-trigger``
    notification) governs its own turn: origin ``scheduled``, never the
    turn before it (that one may have been typed in the terminal)."""
    out["origin"], out["entry_origin"] = "scheduled", {"text": _user_text(entry)}
    return out


def _classify_turn_uncached(path: str | None, note_bodies=()) -> dict:
    """One backward pass to the prompt that governs this turn.

    Returns ``{"origin", "entry_origin", "peer_enqueues", "untrusted"}``:

    - ``origin``: ``telegram`` / ``terminal`` as before (marker, slash
      command named by a Telegram body, roadmap 8.74), ``peer`` for a
      message from another session, ``channel`` for an MCP channel push,
      ``foreign`` for any other input from outside the session,
      ``scheduled`` for this session's own scheduled prompt firing
      (``entry_origin["text"]``: its text).
      Fail-closed ``telegram`` when unreadable or when no prompt is found.
    - ``entry_origin``: the governing entry's recorded origin (for a peer:
      its kernel-verified pid, start time and body).
    - ``peer_enqueues``: the body hashes of messages from another session
      queued AFTER the governing prompt, i.e. joined into this turn.
    - ``untrusted``: a continuation recorded as text from outside the
      session (a ``task-notification`` with a bridge ``subkind``) came
      after it.
    (A turn a scheduled prompt may have started is read from its own bytes
    instead: :func:`_turn_region`.)

    Tool results, compact summaries and other meta entries are passed
    over as before (8.51), and so are this session's own continuations
    (``<task-notification>``)."""
    out = {"origin": "telegram", "entry_origin": {}, "peer_enqueues": [],
           "untrusted": False}
    if not path:
        return out
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
            etype = entry.get("type")
            if etype == "queue-operation":
                if entry.get("operation") == "enqueue":
                    env = peer_auth.parse_envelope(entry.get("content"))
                    if env is not None:
                        body = env.get("body")
                        out["peer_enqueues"].append(
                            peer_auth.body_sha256(body)
                            if isinstance(body, str) else "")
                continue
            if etype != "user":
                continue
            if _is_tool_result(entry):
                continue
            kind, origin = _origin_kind(entry)
            if _own_agent(origin):
                continue  # this session's own background agent: the job's
            if kind == "peer":
                out["origin"], out["entry_origin"] = "peer", origin
                return out
            if kind == "task-notification":
                if _foreign_notification(origin):
                    out["untrusted"] = True
                elif origin.get("subkind") == "scheduled-trigger":
                    return _fire_origin(out, entry)
                continue
            if kind not in _LOCAL_KINDS:
                # An MCP channel push, a remote caller, a plugin's prompt, a
                # kind this code does not know: never the session's own.
                out["origin"] = "channel" if kind == "channel" else "foreign"
                out["entry_origin"] = origin
                return out
            if entry.get("turnOrigin") == "scheduled":
                return _fire_origin(out, entry)
            if _is_injected(entry):
                continue
            text = _user_text(entry)
            if text.lstrip().startswith(_TASK_NOTIFICATION_PREFIX):
                continue
            if not text:
                out["origin"] = "terminal"
                return out
            for block_line in text.split("\n"):
                if block_line.lstrip().startswith(peer_auth.TELEGRAM_MARKER):
                    out["origin"] = "telegram"
                    return out
            if _command_from_telegram(text, note_bodies):
                out["origin"] = "telegram"
                return out
            out["origin"] = "terminal"
            return out
    except OSError:
        return {"origin": "telegram", "entry_origin": {}, "peer_enqueues": [],
                "untrusted": False}
    return out


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
    reads the snapshot. A message from another session or an MCP channel
    push reads as ``peer`` / ``channel`` (:func:`_classify_turn`), never
    terminal.
    """
    return _classify_turn(path, note_bodies)["origin"]


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


def _turn_already_blocked(path: str | None, own_peer_sha: str | None = None) -> bool:
    """True if a tool call in the **current turn** was already blocked by
    the safety policy.

    Streams from EOF backwards: returns True the moment we encounter a
    tool_result carrying the deny marker, and returns False the moment
    we cross the governing user prompt (anything before it belongs to a
    prior turn and doesn't count). This makes a block *sticky* for the
    rest of the turn — once one tool is denied, every later tool call
    is denied too — so an agent can't dodge a pattern with a reworded
    command (e.g. a glob). Per-turn only: a fresh user prompt clears it.

    Input from outside the session is not a boundary ("peer message
    authority"): another session's message absorbed into the turn must not
    clear its block. Only the message that started this very turn
    (*own_peer_sha*, the snapshot's own peer check) is.
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
                kind, origin = _origin_kind(entry)
                if kind == "peer":
                    if own_peer_sha is not None and _peer_sha(origin) == own_peer_sha:
                        return False  # the message that started this turn
                    continue  # joined the turn: its block stays
                if kind == "task-notification":
                    continue  # a continuation of this turn's job
                if kind not in _LOCAL_KINDS:
                    continue  # outside input never ends the block
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


def _cwd_is_wide(cwd: str | None) -> bool:
    """The session runs in ``/``, the home folder or a folder above it
    (roadmap 8.79): its folder is then no project a confined turn may
    write or search in."""
    return bool(cwd) and os.path.isabs(cwd) and safety.is_wide_folder(cwd)


def _write_roots(cwd: str | None, session_id,
                 wide: bool | None = None) -> tuple[str, ...]:
    """Where a confined turn may write (and search): the session's cwd
    (when the payload carries one — none means scratchpad only, fail
    closed) and its scratchpad. A cwd that is ``/``, the home folder or
    an ancestor of it is left out (roadmap 8.79): with it, "the project"
    would be the whole home folder, shell startup files and
    ``~/.ssh/authorized_keys`` included. The scratchpad stays. ``wide``:
    :func:`_cwd_is_wide` already worked out by the caller."""
    if wide is None:
        wide = _cwd_is_wide(cwd)
    roots = [cwd] if cwd and os.path.isabs(cwd) and not wide else []
    roots.append(_scratch_root(cwd, session_id))
    return tuple(roots)


def _root_bounds(cwd: str | None, session_id, root: str, confined: bool,
                 wide_cwd: bool, readable: tuple[str, ...] = (),
                 ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """A turn held to a members' root folder (*root*: a path, or
    ``safety.ROOT_SENTINEL`` when two roots that do not nest were merged):
    ``(write_roots, home_read_roots, root_cwd)``.

    - The session folder counts only when it is absolute, not wide and
      inside the root (any such folder under the sentinel).
    - Confined: writes in that folder and the scratchpad, as before.
      Unconfined (a custom role with ``bypass_role_denies`` but no
      ``can_manage``): writes and searches anywhere in the root (the
      session folder under the sentinel) and the scratchpad.
    - Reads under the home folder: the root (the session folder under
      the sentinel), the scratchpad and *readable* (the reply file).
    - ``root_cwd``: a confined turn whose session folder is outside the
      root, so its deny reasons say why."""
    scratch = _scratch_root(cwd, session_id)
    wide = wide_cwd if confined else _cwd_is_wide(cwd)
    usable = bool(cwd) and os.path.isabs(cwd) and not wide
    sentinel = root == safety.ROOT_SENTINEL
    if sentinel:
        allow = cwd if usable else None
    else:
        allow = safety._realpath(root)
    cwd_ok = usable and (sentinel or safety.under_root(cwd, root))
    if confined:
        write_roots = ((cwd,) if cwd_ok else ()) + (scratch,)
    else:
        write_roots = ((allow,) if allow else ()) + (scratch,)
    home_read_roots = ((allow,) if allow else ()) + (scratch,) + tuple(readable)
    root_cwd = confined and usable and not cwd_ok
    return write_roots, home_read_roots, root_cwd


def _sticky_halt(data: dict, scan: dict, info: dict,
                 tool_name: str) -> dict | None:
    """Sticky turn-block: if any tool was already blocked this turn, deny
    everything else until the next user prompt — no pattern-dodging
    workarounds (the agent will otherwise reword the command to evade
    the specific matcher). ``None`` when the turn was not blocked."""
    own = info.get("snapshot")
    own_peer = own.get("peer") if isinstance(own, dict) else None
    own_sha = (own_peer.get("body_sha256")
               if isinstance(own_peer, dict) else None)
    # One argument unless this turn is another session's message: the
    # scan's seam keeps its one-argument form (tests swap in an oracle).
    if (scan["blocked"] if "blocked" in scan
            else _turn_already_blocked(data.get("transcript_path"), own_sha)
            if isinstance(own_sha, str)
            else _turn_already_blocked(data.get("transcript_path"))):
        if info.get("peer_waiting") or info.get("loop_waiting"):
            # The message that started this turn is not in the transcript
            # yet, so the scan may be reading the previous turn's block.
            return {"tool": tool_name, "reason": peer_auth.SOFT_DENY_REASON,
                    "soft": True}
        return {
            "tool": tool_name,
            "reason": ("session halted - a prior tool call this turn was "
                       "blocked by safety policy; start a new request"),
        }
    return None


def _bypass_root_violation(rules: dict, data: dict, session) -> str | None:
    """A ``bypass_safety`` turn held to a members' root folder (``root``
    in *rules*: a custom role with ``bypass_safety`` but no
    ``can_manage``): its file tools write and search only in the root and
    the scratchpad, read in the home folder only there (and the session's
    own reply file), and never touch a no-access path of its snapshot
    (the floor and every other scope's root). ``None`` with no root: the
    owner's bypass, unchanged."""
    root = rules.get("root")
    if root is None:
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    readable = (str(reply_context_path(session)),) if session else ()
    # A session whose folder is outside the root is not this role's: as
    # for a built-in ``user``, it writes and searches only in the
    # scratchpad there, and its deny reasons say so.
    elsewhere = (bool(cwd) and os.path.isabs(cwd) and not _cwd_is_wide(cwd)
                 and root != safety.ROOT_SENTINEL
                 and not safety.under_root(cwd, root))
    write_roots, home_read_roots, root_cwd = _root_bounds(
        cwd, data.get("session_id"), root, elsewhere, False, readable)
    return safety.path_violation(
        data.get("tool_name", ""), data.get("tool_input", {}) or {},
        tuple(rules.get("deny_paths_no_access", ())),
        tuple(rules.get("deny_paths_no_write", ())),
        cwd=cwd, write_roots=write_roots, readable=readable,
        home_read_roots=home_read_roots, root_cwd=root_cwd)


_FAIL_CLOSED_REASON = "this tool call could not be checked, so it was denied"


def fail_closed(data: dict) -> dict | None:
    """The answer when the decision itself failed (an exception — e.g. a
    NUL byte in a path makes ``expanduser`` raise). Deny, unless the
    session's snapshot can be read and grants the owner's bypass: an
    owner's turn keeps working, anyone else's is refused rather than
    let through unchecked (it used to be allowed).

    The bypass is NOT honoured on a snapshot whose origin must still be
    checked ("peer message authority": a message from another session, a
    pending check, a turn no Telegram message accounts for) nor for a
    background agent pinned to rights without it: those are exactly the
    turns the failed check was about."""
    try:
        snap = read_snapshot(str(data.get("session") or ""))
    except Exception:
        snap = None
    if (isinstance(snap, dict) and snap.get("bypass_safety") is True
            and snap.get("root") is None   # a root-bound bypass fails closed
            and not marked_for_verification(snap)
            and not verify_origin_flagged(str(data.get("session") or ""))):
        pin = None
        agent_id = data.get("agent_id")
        if agent_id:
            try:
                pin = read_pin(str(data.get("session") or ""), agent_id)
            except Exception:
                pin = {"rules": {}}
        if pin is None or (isinstance(pin.get("rules"), dict)
                           and pin["rules"].get("bypass_safety") is True
                           # a root-bound pin fails closed too
                           and pin["rules"].get("root") is None):
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
    exception while deciding is a deny (:func:`fail_closed`). The rules it
    allowed under and what the hook reports (:func:`decide_full`) are kept
    for :func:`take_decide_extra`."""
    global _decide_extra
    block, rules, info = decide_full(data)
    _decide_extra = (rules, info)
    return block


#: What the last :func:`decide` allowed under and reports, taken once by
#: the hook (:func:`take_decide_extra`).
_decide_extra: tuple = (None, {})


def take_decide_extra() -> tuple:
    """``(rules, info)`` of the last :func:`decide`, once; ``(None, {})``
    when there is none (it raised, or was replaced in a test)."""
    global _decide_extra
    extra, _decide_extra = _decide_extra, (None, {})
    return extra


def decide_full(data: dict) -> tuple[dict | None, dict | None, dict]:
    """:func:`decide`, plus what the hook needs besides the answer:
    ``(block, rules, info)``.

    - ``rules``: the rules the call was allowed under (``None`` when
      denied). For a turn typed in the terminal they are
      :data:`aipager.peer_auth.UNRESTRICTED_RULES`. The sender's hook
      writes them into a SendMessage receipt.
    - ``info``: what the hook reports. ``origin`` (the turn's),
      ``context`` (a line for Claude, after a peer check),
      ``peer_resolved`` (for the daemon), and ``snapshot`` (the one
      decided under).

    Never raises: any exception is a :func:`fail_closed` answer."""
    global _decide_error, _cur
    _decide_error = None  # only this call's error, never an earlier one's
    _cur = {"rules": None, "info": {}}
    if data.get("hook_event_name") != "PreToolUse":
        return None, None, {}
    try:
        block = _decide(data)
    except Exception as exc:
        _decide_error = exc  # for the hook's problem report, after its answer
        block = fail_closed(data)
        # An owner allowed by fail_closed: its rules are the owner's.
        return block, (None if block else peer_auth.UNRESTRICTED_RULES), _cur["info"]
    return block, (None if block else _cur["rules"]), _cur["info"]


#: Filled by :func:`_decide` for the call in progress: the rules it
#: allowed under and what the hook reports. ``_decide`` keeps its old
#: contract (the verdict only), which tests rely on.
_cur: dict = {"rules": None, "info": {}}


#: The error the last :func:`decide` failed on (it answers with
#: :func:`fail_closed` and never raises), taken by the hook to report it.
#: Its traceback holds the frames' locals (the tool input): fine in the
#: one-shot hook process; the daemon must never call :func:`decide`.
_decide_error: Exception | None = None


def take_decide_error() -> Exception | None:
    """The error the last :func:`decide` swallowed, once; None if none."""
    global _decide_error
    error, _decide_error = _decide_error, None
    return error


# ---- Messages between sessions ("peer message authority") -----------------

def _peer_sha(origin: dict) -> str | None:
    body = origin.get("body") if isinstance(origin, dict) else None
    return peer_auth.body_sha256(body) if isinstance(body, str) else None


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def _with_floor(rules: dict) -> dict:
    """*rules* with the effective path and command floor added back for a
    turn without the owner's bypass (a receipt's lists are the sender's;
    this machine's policy.yaml additions always apply)."""
    if rules.get("bypass_safety") is True:
        return rules
    floor = safety_floor_lists()
    out = dict(rules)
    for f in ("deny_paths_no_access", "deny_paths_no_write",
              "deny_bash_patterns"):
        out[f] = sorted(set(out.get(f) or []) | set(floor[f]))
    return out


def _driver_of(receipts: list[dict]) -> dict:
    if not receipts:
        return {"class": "unknown", "labels": []}
    d = receipts[0].get("driver")
    return d if isinstance(d, dict) else {"class": "unknown", "labels": []}


#: Tool calls step 0 waits for Claude Code to write the message that
#: started the turn (its transcript is written lazily) before it settles
#: on "not verified".
PEER_WAIT_CALLS = 8


def _resolve_pending_peer(session: str, scan: dict, info: dict) -> dict | None:
    """Step 0: verify the sender of the message that started this turn and
    give the turn exactly its rights. Under the session's snapshot lock,
    so a message joining meanwhile is either already kept apart in
    ``pending_joins`` (merged in below) or joins the result. Returns the
    snapshot to decide under, or None when the lock could not be taken
    (the caller denies this one call softly).

    The transcript may not show the message yet (Claude Code writes it
    lazily): then this call is decided under the peer floor, a refusal is
    soft (``info["peer_waiting"]``), and the check waits, for at most
    :data:`PEER_WAIT_CALLS` calls."""
    with snapshot_lock(session, timeout=5.0) as held:
        if not held:
            return None
        cur = read_snapshot(session)
        if not isinstance(cur, dict):
            return None
        peer = cur.get("peer")
        if not (isinstance(peer, dict) and peer.get("status") == "pending"):
            return cur  # another tool call resolved it meanwhile
        want_sha = peer.get("body_sha256")
        if (scan.get("origin") != "peer"
                or _peer_sha(scan.get("entry_origin") or {}) != want_sha):
            # Not written yet: the scan still finds the turn before (a
            # previous message from another session, in a hand-off back
            # and forth, included).
            waited = peer.get("waited") if isinstance(peer.get("waited"), int) else 0
            if waited + 1 < PEER_WAIT_CALLS:
                new = dict(cur)
                new["peer"] = {**peer, "waited": waited + 1}
                write_merged_snapshot(session, new)
                joins = (cur.get("pending_joins")
                         if isinstance(cur.get("pending_joins"), list) else [])
                info["peer_waiting"] = True
                return {**new, **_with_floor(safety_rules(merge_snapshots(
                    [peer_floor_snapshot(), *(j for j in joins if isinstance(j, dict))])))}
        origin = scan.get("entry_origin") if scan.get("origin") == "peer" else {}
        receipts: list[dict] = []
        want = peer.get("body_sha256")
        pid = _as_int(origin.get("verifiedPeerPid")) if origin else None
        if (origin and isinstance(want, str) and _peer_sha(origin) == want
                and pid is not None):
            receipts = peer_auth.claim_receipts(
                want, pid=pid, start=_as_int(origin.get("verifiedPeerProcStart")),
                exclude_session=session)
        rules, status = receipt_rules(session, receipts)
        joins = cur.get("pending_joins") if isinstance(cur.get("pending_joins"), list) else []
        final = _with_floor(safety_rules(merge_snapshots(
            [rules, *(j for j in joins if isinstance(j, dict))])))
        new = dict(cur)
        new.update(final)
        sender = receipts[0].get("sender_session") if receipts and status == "verified" else None
        driver = _driver_of(receipts) if status == "verified" else {"class": "unknown", "labels": []}
        new["peer"] = {**peer, "status": status, "sender_session": sender,
                       "driver": driver,
                       "via": receipts[0].get("via") if receipts else None}
        new["pending_joins"] = []
        if status == "verified":
            notice = peer_auth.notice_verified(peer_auth.short_session(sender), driver)
        else:
            notice = peer_auth.NOTICE_UNVERIFIED
        new["peer_notice"] = notice
        write_merged_snapshot(session, new)
        info["context"] = notice
        info["peer_resolved"] = {"status": status, "sender_session": sender,
                                 "driver": driver,
                                 "bypass": final.get("bypass_safety") is True}
        return new


def _peer_accounted(snap: dict, scan: dict) -> bool:
    """True when *snap* already holds the rights of the peer prompt that
    governs this turn: its own resolved check, or a join of the same body
    (a message queued behind a turn, then run as its own turn without a
    UserPromptSubmit)."""
    sha = _peer_sha(scan.get("entry_origin") or {})
    joined = [j for j in snap.get("joined_peers") or () if isinstance(j, dict)]
    if sha is None:
        # No body recorded: nothing ties it to any check. Only the guard's
        # own floor (a record with no body) accounts for it.
        return any(j.get("guard") is True and j.get("body_sha256") is None
                   for j in joined)
    peer = snap.get("peer")
    if (isinstance(peer, dict) and peer.get("status") in ("verified", "unverified")
            and peer.get("body_sha256") == sha):
        return True
    return any(j.get("body_sha256") == sha for j in joined)


def _accounted_shas(snap) -> set:
    out: set = set()
    if not isinstance(snap, dict):
        return out
    peer = snap.get("peer")
    if isinstance(peer, dict) and isinstance(peer.get("body_sha256"), str):
        out.add(peer["body_sha256"])
    for j in snap.get("joined_peers") or ():
        if isinstance(j, dict) and isinstance(j.get("body_sha256"), str):
            out.add(j["body_sha256"])
    return out


def _narrow_to(session: str, rules_list: list[dict], record: list[dict] | None,
               *, mark_peer: bool) -> dict:
    """Narrow the session's snapshot with *rules_list* (strictest wins,
    never wider), under its lock, record *record* in ``joined_peers`` and
    write it. Returns the snapshot to decide under. Without the lock it
    writes nothing (the call is decided under the narrowed rules, and the
    next call tries again)."""
    with snapshot_lock(session, timeout=5.0) as held:
        cur = read_snapshot(session)
        new = narrowed_snapshot(cur, rules_list)
        if not held:
            new["verify_origin"] = True
            return new
        new["verify_origin"] = True
        if mark_peer:
            new["joined_from_peer"] = True
        if record:
            prev = new.get("joined_peers") if isinstance(new.get("joined_peers"), list) else []
            new["joined_peers"] = [*prev, *record][-16:]
        if new != cur:
            write_merged_snapshot(session, new)
        return new


#: Governing prompts from outside the session that no receipt can vouch for.
_OUTSIDE_ORIGINS = ("channel", "foreign")


def _guard_peer_origin(session: str, snap, scan: dict) -> dict | None:
    """The turn's governing prompt is not this session's own human prompt,
    and the snapshot does not hold its proven rights: hold it to the peer
    floor. Returns the narrowed snapshot, or None when nothing applies."""
    origin = scan.get("origin")
    if origin in _OUTSIDE_ORIGINS or scan.get("untrusted"):
        return _narrow_to(session, [peer_floor_snapshot()], None, mark_peer=True)
    if origin == "peer" and not (isinstance(snap, dict) and _peer_accounted(snap, scan)):
        sha = _peer_sha(scan.get("entry_origin") or {})
        return _narrow_to(session, [peer_floor_snapshot()],
                          [{"body_sha256": sha, "status": "unverified",
                            "sender_session": None, "guard": True}],
                          mark_peer=True)
    return None


def _missed_joins(session: str, snap, scan: dict) -> dict | None:
    """Messages from another session queued into this turn that no
    UserPromptSubmit accounted for: narrow to their proven rights (by body
    hash; joining can only narrow) or the peer floor."""
    seen = _accounted_shas(snap)
    missed = [s for s in scan.get("peer_enqueues") or () if s not in seen]
    if not missed:
        return None
    with snapshot_lock(session, timeout=5.0) as held:
        if not held:
            # A receipt is claimed only when its result can be written:
            # this call runs on the peer floor, the next one tries again.
            new = narrowed_snapshot(read_snapshot(session), [peer_floor_snapshot()])
            new["verify_origin"] = True
            return new
        cur = read_snapshot(session)
        seen = _accounted_shas(cur)
        missed = [s for s in missed if s not in seen]
        if not missed:
            return cur
        rules_list: list[dict] = []
        record: list[dict] = []
        for sha in dict.fromkeys(missed):
            receipts = (peer_auth.claim_receipts(sha, exclude_session=session)
                        if sha else [])
            rules, status = receipt_rules(session, receipts)
            rules_list.append(rules)
            record.append({"body_sha256": sha or None, "status": status,
                           "sender_session": (receipts[0].get("sender_session")
                                              if receipts else None),
                           "missed": True})
        new = narrowed_snapshot(cur, rules_list)
        new["verify_origin"] = True
        new["joined_from_peer"] = True
        prev = new.get("joined_peers") if isinstance(new.get("joined_peers"), list) else []
        new["joined_peers"] = [*prev, *record][-16:]
        write_merged_snapshot(session, new)
        return new


def _target_is_aipager_session(to) -> bool:
    """True when a SendMessage target names a Claude session aipager runs
    (its process carries ``CLAUDE_DTACH_SESSION``). Only the ``uds:`` form
    names a process; any other form cannot be checked."""
    if not isinstance(to, str) or not to.startswith("uds:"):
        return False
    m = re.search(r"(?:^|/)(\d{1,10})\.sock$", to)
    if not m:
        return False
    try:
        env = Path(f"/proc/{int(m.group(1))}/environ").read_bytes()
    except (OSError, ValueError):
        return False
    return any(part.startswith(b"CLAUDE_DTACH_SESSION=") and len(part) > 21
               for part in env.split(b"\0"))


def _pin_keeps_owner(pin) -> bool:
    """No pin, or a readable pin of the owner's own rights: the owner
    short-circuit may still apply (the strictest of two bypasses is a
    bypass)."""
    if pin is None:
        return True
    rules = pin.get("rules") if isinstance(pin, dict) else None
    return (peer_auth.well_formed_rules(rules)
            and rules.get("bypass_safety") is True
            # a pin of a role held to its root is no owner's bypass
            and safety.normalise_root(rules.get("root")) is None)


def _pin_rules(pin) -> dict | None:
    if not isinstance(pin, dict):
        return None
    rules = pin.get("rules")
    if not peer_auth.well_formed_rules(rules):
        return safety_rules(peer_floor_snapshot())  # unreadable pin: fail closed
    return safety_rules(rules)


def turn_rules(data: dict) -> dict:
    """The rules the session's CURRENT turn runs under, as a background
    agent launched now must be pinned to (phase C): the snapshot's, or
    unrestricted for a turn typed in the terminal. Fails closed to the
    peer floor."""
    try:
        session = data.get("session", "") or ""
        snap = _readable_snapshot(session)
        bodies = (snap.get("note_bodies")
                  if snap is not None and snap.get("scope_mode") is True else None)
        view = _scheduled_view(session, snap, data.get("transcript_path"),
                               bodies or (), adopt=False)
        unreadable = view is _UNREADABLE
        if view is not None and not unreadable:
            # A scheduled prompt's turn: its own rights (the floor while a
            # bare /loop's fire is unconfirmed), narrowed by what came from
            # outside in it.
            vscan, vsnap, _waiting = view
            seen = _accounted_shas(vsnap)
            if vscan["untrusted"] or any(s not in seen for s in vscan["peer_enqueues"]):
                return safety_rules(merge_snapshots([vsnap, peer_floor_snapshot()]))
            return safety_rules(vsnap)
        scan = _classify_turn(data.get("transcript_path"), bodies or ())
        if scan["origin"] == "scheduled":
            scan, snap = _fire_view(session, snap, scan, data.get("transcript_path"))
        peerish = marked_for_verification(snap) and (
            (snap or {}).get("turn_origin") == "peer" or (snap or {}).get("peer")
            or (snap or {}).get("joined_from_peer") or (snap or {}).get("joined_peers"))
        joined = snap is not None and (snap.get("joined_from_telegram") is True
                                       or snap.get("scheduled") is True
                                       or (unreadable and "loop_rules" in snap))
        if scan["origin"] == "terminal" and not joined and not peerish:
            return dict(peer_auth.UNRESTRICTED_RULES)
        if snap is None or (isinstance(snap.get("peer"), dict)
                            and snap["peer"].get("status") == "pending"):
            return safety_rules(peer_floor_snapshot() if snap else floor_snapshot())
        if scan["origin"] in _OUTSIDE_ORIGINS or scan["untrusted"]:
            return safety_rules(peer_floor_snapshot())
        return safety_rules(snap)
    except Exception:
        return safety_rules(peer_floor_snapshot())


def _pure_terminal(snap) -> bool:
    """A turn typed in the terminal (or no prompt anyone accounts for) that
    nothing joined: its snapshot is only a placeholder (the hook runs a
    typed turn with no rules)."""
    return (isinstance(snap, dict) and snap.get("turn_origin") == "terminal"
            and snap.get("joined_from_telegram") is not True
            and not (snap.get("peer") or snap.get("joined_from_peer")
                     or snap.get("joined_peers")))


def _fire_view(session: str, early, scan: dict, path):
    """A scheduled prompt's fire the ordinary scan found governing the turn:
    ``(scan, snapshot)``.

    It governs only inside the snapshot's own turn (the bytes since its
    pick-up recorded where the turn began): a fire from an earlier turn is
    still the newest prompt only because this turn's own is not written
    yet, and the snapshot decides alone. Inside, the fire's rights are
    those recorded for its text (else the floor), never wider than the
    snapshot (which carries what joined or narrowed the turn); a pure
    terminal turn's placeholder counts as no limit. What came into the
    turn from outside still narrows it (the scan's joins and untrusted
    input). With no start recorded, the strictest of the two. The
    snapshot's own state (a check still pending) is kept."""
    scan = {**scan, "origin": "telegram"}
    base = early if isinstance(early, dict) else floor_snapshot()
    region = _turn_region(path, base.get("turn_start_size"))
    if region is not None and not region["fire"]:
        return scan, base
    texts = (region["fires"] if region is not None
             else [(scan.get("entry_origin") or {}).get("text")])
    fires = [_fire_rights(session, text) for text in texts]
    if region is not None:
        # What came into the turn from outside narrows the fire as any turn
        # (the guards below).
        scan["untrusted"] = scan.get("untrusted") or region["outside"] or region["untrusted"]
        scan["peer_enqueues"] = list(dict.fromkeys(
            [*(scan.get("peer_enqueues") or ()), *region["peer_enqueues"]]))
    own = (dict(peer_auth.UNRESTRICTED_RULES)
           if region is not None and _pure_terminal(base) else safety_rules(base))
    return scan, {**base, **_strictest([own, *fires])}


def _decide(data: dict):
    info = _cur["info"]
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    session = data.get("session", "")
    path = data.get("transcript_path")
    agent_id = data.get("agent_id") or ""

    if (tool_name in SCHEDULING_TOOLS and isinstance(tool_input, dict)
            and not trigger_slot_free(session, tool_input.get("prompt"))):
        # Its rights could not be recorded: it would fire with none known.
        return {"tool": tool_name, "reason": peer_auth.SCHEDULE_FULL_REASON,
                "soft": True}

    early = _readable_snapshot(session)
    pin = read_pin(session, agent_id) if (session and agent_id) else None
    marked = marked_for_verification(early) or verify_origin_flagged(session)

    # Owner short-circuit: an owner's turn is allowed whatever the origin,
    # so the transcript is not scanned. Not for a turn whose origin must
    # still be checked ("peer message authority": a message from another
    # session, a turn no Telegram message accounts for), nor for an agent
    # pinned to other rights, nor when the transcript's end shows input
    # from outside the session (a message from another session absorbed
    # into this turn or run as the next one with no UserPromptSubmit, which
    # would otherwise ride the owner's rights): those take the full path,
    # which accounts for each such message exactly.
    # Never for a role held to a members' root folder (a custom role with
    # bypass_safety but no can_manage): its turn takes the full path,
    # which holds its file tools to the root.
    if (early is not None and early.get("bypass_safety")
            and early.get("root") is None
            and not marked and _pin_keeps_owner(pin)
            and not _foreign_input_near_end(path)):
        info["origin"] = "telegram"
        info["snapshot"] = early
        owner_rules = safety_rules(early)
        owner_rules["bypass_safety"] = True
        _cur["rules"] = owner_rules
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
    scan = _classify_turn(path, bodies or ())
    scan["origin"] = _origin_from_transcript(path, bodies or ())
    # A turn this session's own scheduled prompt may have started (a /loop,
    # a cron): read from its own bytes, whatever the turns before it were.
    view = _scheduled_view(session, early, path, bodies or (), adopt=True)
    unreadable = view is _UNREADABLE
    from_view = view is not None and not unreadable
    if from_view:
        scan, early, waiting = view
        # Maybe a bare /loop whose entry is not written yet: the floor for
        # now, refused softly, for a few of the turn's own calls (a
        # background agent of an earlier job spends none of them).
        if waiting and (agent_id or wait_for_loop(session, PEER_WAIT_CALLS)):
            info["loop_waiting"] = True
    elif scan["origin"] == "scheduled":
        scan, early = _fire_view(session, early, scan, path)
        from_view = True
    info["origin"] = scan["origin"]

    snap = early
    # Step 0: a message from another session started this turn and its
    # sender is not verified yet: verify it before anything else.
    if (isinstance(snap, dict) and isinstance(snap.get("peer"), dict)
            and snap["peer"].get("status") == "pending"):
        resolved = _resolve_pending_peer(session, scan, info)
        if resolved is None:
            return {"tool": tool_name, "reason": peer_auth.SOFT_DENY_REASON,
                    "soft": True}
        snap = resolved
    if not info.get("peer_waiting"):
        # (While the check waits, the scan still reads the turn before:
        # its entries say nothing about this one, and a queue line there
        # may be this very message, whose receipt the check needs.)
        guarded = _guard_peer_origin(session, snap, scan)
        if guarded is not None:
            snap = guarded
        missed = _missed_joins(session, snap, scan)
        if missed is not None:
            snap = missed

    # A Telegram message joined this turn (roadmap 8.77). Absorbed into a
    # running turn, it is no prompt of its own in the transcript, so a
    # turn typed in the terminal would still read as terminal and run the
    # message's text with no rules. Telegram until the next fresh turn.
    # The same for a message from another session ("peer message
    # authority", C3).
    joined = snap is not None and (snap.get("joined_from_telegram") is True
                                   or snap.get("scheduled") is True
                                   or (unreadable and "loop_rules" in snap))
    peerish = snap is not None and bool(
        snap.get("turn_origin") == "peer" or snap.get("peer")
        or snap.get("joined_from_peer") or snap.get("joined_peers"))
    if scan["origin"] == "terminal" and not joined and not peerish:
        current = dict(peer_auth.UNRESTRICTED_RULES)  # terminal users are unrestricted
    else:
        if snap is early and not from_view:
            # The old second read, kept as it was: a snapshot that cannot
            # be read now, or is not an object, makes the decision fail
            # (fail_closed) exactly as before.
            snap = read_snapshot(session)
            if snap is not None:
                snap.get("bypass_safety")
        if snap is None:
            # Fail-closed: no snapshot → apply the floor, no bypass: the
            # built-in one plus policy.yaml's ``safety:`` section, which the
            # daemon writes for the hook (roadmap 8.96; the built-ins alone
            # when that file cannot be read). The same answer
            # policy_snapshot.merge_snapshots([]) gives for "no outstanding
            # notes". Only this fallback reads that file.
            snap = floor_snapshot()
        current = safety_rules(snap)
        for f in ("deny_paths_no_access", "deny_paths_no_write",
                  "deny_bash_patterns", "deny_tools", "allow_tools"):
            current[f] = list(snap.get(f) or [])
    info["snapshot"] = snap

    # Phase C: a background agent is held to the strictest of the rights
    # it was launched with (its pin) and the session's current ones.
    pinned = _pin_rules(pin)
    if pinned is not None:
        current = safety_rules(merge_snapshots([current, pinned]))
        if current.get("bypass_safety") is not True:
            current = _with_floor(current)
    if current.get("bypass_safety") is True:
        # Owner (or the terminal): no checks, unless the role also lacks
        # can_manage. Only owner and admin are unrestricted, so such a
        # role keeps its bypass for tools and Bash patterns but its file
        # tools stay inside its members' root folder, and a turn of it
        # that was already blocked stays halted like any held turn.
        if current.get("root") is not None:
            halted = _sticky_halt(data, scan, info, tool_name)
            if halted is not None:
                return halted
        reason = _bypass_root_violation(current, data, session)
        if reason:
            return {"tool": tool_name, "reason": reason}
        _cur["rules"] = current
        return None
    snap = current

    halted = _sticky_halt(data, scan, info, tool_name)
    if halted is not None:
        return halted

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
    confined = snap.get("confine_writes") is not False
    wide_cwd = confined and _cwd_is_wide(cwd)
    write_roots = (
        _write_roots(cwd, data.get("session_id"), wide_cwd)
        if confined else None
    )
    # The one control file a turn may read: its own session's reply
    # context, which the prompt tells Claude to Read (session_ops).
    readable = (str(reply_context_path(session)),) if session else ()
    # A role without can_manage is held to its members' root folder: no
    # write outside it, no read in the home folder outside it.
    home_read_roots = None
    root_cwd = False
    root = snap.get("root")
    if root is not None:
        write_roots, home_read_roots, root_cwd = _root_bounds(
            cwd, data.get("session_id"), root, confined, wide_cwd, readable)

    reason = (
        safety.path_violation(tool_name, tool_input, no_access, no_write,
                              cwd=cwd, write_roots=write_roots,
                              readable=readable,
                              wide_cwd=wide_cwd,
                              home_read_roots=home_read_roots,
                              root_cwd=root_cwd)
        or (safety.bash_violation(tool_input.get("command", ""), bash_pats)
            if tool_name == "Bash" else None)
        or safety.tool_violation(tool_name, deny_tools, allow_tools)
    )
    if (not reason and tool_name == "SendMessage" and confined
            and not _target_is_aipager_session(tool_input.get("to"))):
        # A confined role a policy gave SendMessage may only write to a
        # session aipager runs, where its message is held to its own
        # rights. A Claude session aipager does not manage would run the
        # text with no rules at all.
        reason = safety.SEND_UNMANAGED_REASON
    if reason and (info.get("peer_waiting") or info.get("loop_waiting")):
        # Decided under the peer floor only because the sender is not
        # checked yet: a soft refusal, which does not halt the turn.
        return {"tool": tool_name, "reason": peer_auth.SOFT_DENY_REASON,
                "soft": True}
    if reason:
        return {"tool": tool_name, "reason": reason}
    _cur["rules"] = safety_rules(snap)
    return None


def soft_deny_json(reason: str) -> str:
    """A deny that does NOT halt the turn: no "aipager safety policy"
    marker, so the sticky block (:func:`_turn_already_blocked`) does not
    see it. Used only while a peer check is still running (C8)."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })


def deny_decision_json(reason: str) -> str:
    """Claude Code PreToolUse deny payload (stdout)."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"aipager safety policy: {reason}",
        }
    })
