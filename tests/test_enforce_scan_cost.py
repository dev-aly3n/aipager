"""The hook's PreToolUse transcript scans, made cheap without changing a
verdict (design: /ship hook-transcript-scan-cost).

``aipager-hook`` scans Claude Code's transcript back to the governing
prompt on every PreToolUse. The scans used to ``json.loads`` every line,
through a reverse reader that was quadratic on long lines: seconds of CPU
per tool call on a 500 MB transcript, and a very long line could push the
hook into its memory cap. Now an owner's turn reads no transcript, the
reader is linear, and a byte-level check decides which lines to parse.

Everything here compares the new code with a frozen copy of main
166a3f6's scans (the oracle below): same return value or same raised
exception type, at several chunk sizes. The few documented shapes Claude
Code never writes, where the new code deliberately differs, are pinned
separately.
"""

from __future__ import annotations

import builtins
import functools
import io
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

from aipager import policy
from aipager import policy_snapshot as ps
from aipager.dtach import enforce

# ===========================================================================
# frozen from main 166a3f6, do not edit
# (git show 166a3f6:aipager/dtach/enforce.py, lines 28-31, 60 and 63-276)
# ===========================================================================

# Marker our deny reasons carry (see deny_decision_json). Once it appears
# in a tool_result this turn, every later tool call is sticky-blocked.
_BLOCK_MARKER = "aipager safety policy"

_TASK_NOTIFICATION_PREFIX = "<task-notification>"


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


def _origin_from_transcript(path: str | None) -> str:
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
            return "terminal"
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


# ===========================================================================
# end of the frozen copy
# ===========================================================================

_THIS = sys.modules[__name__]
_ORACLE_ITER = _iter_lines_reversed
_ORACLE_ORIGIN = _origin_from_transcript
_ORACLE_BLOCKED = _turn_already_blocked


def _oracle_origin_patch(path, note_bodies=()):
    """The frozen origin scan in ``_decide``'s call shape. The frozen copy
    predates ``note_bodies`` (roadmap 8.74); these comparisons write no
    slash-command record, where the bodies are the only thing read."""
    return _ORACLE_ORIGIN(path)
_NEW_RAW = enforce._iter_raw_lines_reversed

CHUNKS = (7, 64, 65536)
SESSION = "claude-scancost.guard"  # can never name a live session


def _outcome(fn, path):
    try:
        return fn(path)
    except Exception as e:  # the raised type is part of the contract
        return "RAISE:" + type(e).__name__


def _old_and_new(monkeypatch, path, chunk):
    """Both scans, oracle and new, reading ``chunk`` bytes at a time."""
    with monkeypatch.context() as m:
        m.setattr(_THIS, "_iter_lines_reversed",
                  functools.partial(_ORACLE_ITER, chunk_bytes=chunk))
        m.setattr(enforce, "_iter_raw_lines_reversed",
                  functools.partial(_NEW_RAW, chunk_bytes=chunk))
        old = (_outcome(_ORACLE_ORIGIN, path), _outcome(_ORACLE_BLOCKED, path))
        new = (_outcome(enforce._origin_from_transcript, path),
               _outcome(enforce._turn_already_blocked, path))
    return old, new


# ===========================================================================
# Corpus generators (stdlib random, fixed seeds)
# ===========================================================================

TG = "[via Telegram · @owner]\nhello"
MARK = "denied: aipager safety policy blocked this"
SEPARATORS = ((",", ":"), (", ", ": "), ("\t,  ", "  :\t"))


def _prompt(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _blocks(texts):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": t} for t in texts]}}


def _tres(content, *, role=True):
    msg = {"role": "user"} if role else {}
    msg["content"] = [{"type": "tool_result", "tool_use_id": "t", "content": content}]
    return {"type": "user", "message": msg}


def _tres_blocks(contents):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": c}
        for i, c in enumerate(contents)]}}


def _asst(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": text}]}}


def _real_tool_result(content, *, id_first=True):
    """A tool-result line the way Claude Code writes one."""
    block = ({"tool_use_id": "toolu_01AbC", "type": "tool_result"} if id_first
             else {"type": "tool_result", "tool_use_id": "toolu_01AbC"})
    block.update({"content": content, "is_error": False})
    return {
        "parentUuid": "0b6f1c2e-1111-2222-3333-444455556666",
        "isSidechain": False, "userType": "external", "cwd": "/home/u/proj",
        "sessionId": "5b1d7363-aaaa-bbbb-cccc-000000000001",
        "version": "2.1.285", "gitBranch": "main", "type": "user",
        "message": {"role": "user", "content": [block]},
        "uuid": "9c8d7e6f-1111-2222-3333-444455556666",
        "timestamp": "2026-09-30T12:00:00.000Z",
        "toolUseResult": {"stdout": content if isinstance(content, str) else "",
                          "stderr": "", "interrupted": False},
    }


def _real_prompt(text):
    return {
        "parentUuid": None, "isSidechain": False, "userType": "external",
        "cwd": "/home/u/proj", "sessionId": "5b1d7363-aaaa-bbbb-cccc-000000000001",
        "version": "2.1.285", "gitBranch": "main", "type": "user",
        "message": {"role": "user", "content": text},
        "uuid": "1a2b3c4d-1111-2222-3333-444455556666",
        "timestamp": "2026-09-30T12:00:00.000Z",
    }


# Characters ``repr`` spells as an escape (non-printable: C0, DEL, C1,
# soft hyphen, a Zs space, a line separator). Several end in the hex
# digit "a", so ``str()`` of a list or non-str tool-result piece turns
# them plus a literal "ipager" into "aipager" (rev-iter1-001).
NONPRINT = ("\x1a", "\x9a", "\u009A", "\x8a", "\u200a", "\x07", "\x7f",
            "\x9f", "\xad", "\u2028")


def _repr_piece(r):
    """A tool result whose non-str pieces put a non-printable character in
    front of a marker word, so ``_tool_result_text`` goes through repr."""
    c = r.choice(NONPRINT)
    word = r.choice(("ipager safety policy", "ipager", "safety policy",
                     "safety " + c + "policy"))
    content = r.choice((
        [[c + word]],
        [[c + "ipager safety policy", 1]],
        [{"type": "text", "text": [c + word]}],
        [{"type": "text", "text": {"k": c + word}}, "safety policy"],
        [c + "ipager", "safety policy"],
        [{"type": "text", "text": "aipager"}, [c + "safety policy"]],
    ))
    return r.choice((_tres(content), _real_tool_result(content)))


def _escaped_message_key(r):
    """A user line where a key or string ending in ``"message`` (written
    ``\\"message"``) comes before the entry's own ``message`` key
    (rev-iter1-002)."""
    tr = _tres("ok")["message"]
    prompt = {"role": "user", "content": r.choice((TG, "terminal words"))}
    own = r.choice((prompt, tr, _tres(MARK)["message"]))
    first = r.choice((
        {'x"message': tr},
        {'x"message': prompt},
        {'"message': tr},
        {"note": 'he said "message'},
        {"note": 'say "message": {'},
    ))
    obj = {"type": "user", **first, "message": own}
    if r.random() < 0.3:
        obj['y"message'] = tr  # one after the own key too
    return obj


GARBAGE = ("not json at all", "{broken", "}", '{"type":"user"', "{}", "{ }",
           '{"type": "user", "message": {"content": "[via Telegram', "\x00\x01")
NON_OBJECTS = ("[1,2]", '"s"', "3", "null", "true")

# name -> builder(rng) returning a JSON-able object, or a str/bytes line
_TEMPLATES = {
    "tg": lambda r: _prompt(TG),
    "term": lambda r: _prompt("plain terminal prompt"),
    "tgblocks": lambda r: _blocks(["first block", "[via Telegram · @a]\nsecond"]),
    "task": lambda r: _prompt("<task-notification>\n<task-id>a1</task-id>"),
    "meta": lambda r: _prompt("meta stuff", isMeta=True),
    "compact": lambda r: _prompt("This session is being continued",
                                 isCompactSummary=True),
    "tr": lambda r: _tres("ok output"),
    "trmark": lambda r: _tres(MARK),
    "trfake": lambda r: _tres(
        'echo {"type":"user","message":{"content":"[via Telegram"}}'),
    "asst": lambda r: _asst('he wrote "type":"user" and [via Telegram in text'),
    "long": lambda r: _tres("B" * 200_000),
    "empty": lambda r: _prompt(""),
    "badutf": lambda r: b'{"type":"user","message":{"content":"[via Telegram \xff\xfe bad"}}',
    "garbage": lambda r: r.choice(GARBAGE),
    "blank": lambda r: r.choice(("", "   ", "\t")),
    "realtr": lambda r: _real_tool_result(r.choice(("ok", MARK, "x" * 300))),
    "realtg": lambda r: _real_prompt(r.choice((TG, "terminal words"))),
    "trlist": lambda r: _tres([{"type": "text", "text": "part one"},
                               {"type": "text", "text": "part two"}]),
    "split1": lambda r: _tres([{"type": "text", "text": "aipager"},
                               {"type": "text", "text": "safety policy"}]),
    "split2": lambda r: _tres_blocks(["aipager safety", "policy"]),
    "scatter": lambda r: _tres("safety first; the policy says aipager is fine"),
    "asstmark": lambda r: _asst(MARK),
    "nonobj": lambda r: r.choice(NON_OBJECTS),
    "esckey": "esckey",
    "escmark": "escmark",
    "msgnull": lambda r: {"type": "user", "message": None},
    "msgstr": lambda r: {"type": "user", "message": "x"},
    "trnorole": lambda r: _tres(r.choice(("ok", MARK)), role=False),
    "lead": "lead",
    "leaduni": "leaduni",
    "ansi": lambda r: r.choice((_tres("\x1b[31mred\x1b[0m"),
                                _prompt("[via Telegram · @a]\n\x1b[1mbold\x1b[0m"))),
    "metatg": lambda r: _prompt(TG, **r.choice(({"isMeta": True},
                                                {"isCompactSummary": True}))),
    "reprpiece": _repr_piece,
    "escmsgkey": _escaped_message_key,
}
# "long" (200 KB) is inserted separately, in a few trials only: the
# oracle's reader is quadratic on it.
NAMES = tuple(n for n in _TEMPLATES if n != "long")
LONG_TRIAL_RATE = 0.02


def _piece(name, rng, seps, ascii_):
    def dump(obj):
        return json.dumps(obj, separators=seps, ensure_ascii=ascii_)

    if name == "esckey":
        text = dump(_prompt(rng.choice((TG, "terminal words"))))
        return text.replace('"type"', '"typ\\u0065"', 1).encode("utf-8")
    if name == "escmark":
        return dump(_tres(MARK)).replace("aipager", "\\u0061ipager", 1).encode("utf-8")
    if name == "lead":
        inner = _piece(rng.choice(("tg", "term", "tr", "trmark")), rng, seps, ascii_)
        return rng.choice((b" ", b"\t", b"  \x0c", b"\x0b")) + inner
    if name == "leaduni":
        inner = _piece(rng.choice(("tg", "term", "tr")), rng, seps, ascii_)
        return rng.choice((" ", "\x1c")).encode("utf-8") + inner
    out = _TEMPLATES[name](rng)
    if isinstance(out, bytes):
        return out
    if isinstance(out, str):
        return out.encode("utf-8")
    return dump(out).encode("utf-8")


class _ShapeError(AssertionError):
    pass


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _ShapeError(f"duplicate keys {keys}")
    return dict(pairs)


def _short_int(s):
    if len(s) >= 1000:
        raise _ShapeError("integer of 1,000 digits or more")
    return int(s)


def _assert_allowed_shape(line: bytes) -> None:
    """The corpus must never contain a documented divergence class
    (design section 5): a nested ``"message"`` key ahead of the top-level
    one, duplicate keys, nesting of 1,000 levels or more, integers of
    1,000 digits or more, or a truthy non-dict top-level ``message`` on a
    non-user line. Checked on every generated line, so a later generator
    edit cannot slip one in and make the differential test flaky or
    wrong."""
    text = line.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                         parse_int=_short_int)
    except json.JSONDecodeError:
        return
    # Walk in serialization order (each key, then its value, then the next
    # key), so the first "message" token met is the first in the bytes.
    first_message = None
    stack = [("value", obj, 0)]
    while stack and first_message is None:
        kind, node, depth = stack.pop()
        if depth >= 1000:
            raise _ShapeError("nesting of 1,000 levels or more")
        if kind == "key":
            if node == "message":
                first_message = (depth, True)
        elif isinstance(node, dict):
            items = []
            for k, v in node.items():
                items += [("key", k, depth), ("value", v, depth + 1)]
            stack.extend(reversed(items))
        elif isinstance(node, list):
            stack.extend(reversed([("value", v, depth + 1) for v in node]))
        elif node == "message":
            first_message = (depth, False)
    _check_depth(obj)
    if first_message is not None and isinstance(obj, dict):
        assert first_message == (0, True) and "message" in obj, (
            f"a 'message' token precedes the top-level key: {text[:200]}")
    if (isinstance(obj, dict) and obj.get("type") != "user"
            and obj.get("message") and not isinstance(obj.get("message"), dict)):
        raise _ShapeError("truthy non-dict message on a non-user line")


def _check_depth(obj) -> None:
    stack = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if depth >= 1000:
            raise _ShapeError("nesting of 1,000 levels or more")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)


def _gen_case(rng) -> bytes:
    seps = rng.choice(SEPARATORS)
    ascii_ = rng.random() < 0.5
    eol = b"\r\n" if rng.random() < 0.1 else b"\n"
    lines = [_piece(rng.choice(NAMES), rng, seps, ascii_)
             for _ in range(rng.randint(0, 9))]
    if rng.random() < LONG_TRIAL_RATE:
        lines.insert(rng.randint(0, len(lines)), _piece("long", rng, seps, ascii_))
    for line in lines:
        _assert_allowed_shape(line)
    data = b"".join(line + eol for line in lines)
    if data and rng.random() < 0.2:
        data = data.rstrip(b"\r\n")  # no trailing newline
    return data


def test_the_shape_guard_rejects_every_documented_class():
    nested = json.dumps({"type": "user", "x": {"message": {}}, "message": {}})
    for bad in (
        nested.encode(),
        b'{"type":"user","message":{},"message":{}}',
        b'{"type":"assistant","message":{"content":' + b"[" * 1001 + b"]" * 1001 + b"}}",
        b'{"type":"assistant","n":' + b"1" * 1000 + b"}",
        b'{"type":"system","message":"x"}',
    ):
        with pytest.raises(AssertionError):
            _assert_allowed_shape(bad)
    _assert_allowed_shape(json.dumps(_real_tool_result("ok")).encode())


# ===========================================================================
# 7.1 Differential property test
# ===========================================================================

def test_the_new_scans_match_the_frozen_oracle_on_a_seeded_corpus(
        tmp_path, monkeypatch):
    rng = random.Random(20260930)
    p = tmp_path / "case.jsonl"
    compared = 0
    long_cases = 0
    mismatches = []
    seen = set()
    for trial in range(3000):
        data = _gen_case(rng)
        p.write_bytes(data)
        chunks = (64, 65536) if len(data) > 100_000 else CHUNKS
        long_cases += len(data) > 100_000
        for n in chunks:
            old, new = _old_and_new(monkeypatch, str(p), n)
            compared += 1
            seen.update(old)
            if old != new:
                mismatches.append(("scans", trial, n, old, new, data[:300]))
            if list(enforce._iter_lines_reversed(p, n)) != list(_ORACLE_ITER(p, n)):
                mismatches.append(("str iterator", trial, n, data[:300]))
            expect = data.split(b"\n")[::-1] if data else []
            if list(enforce._iter_raw_lines_reversed(p, n)) != expect:
                mismatches.append(("raw iterator", trial, n, data[:300]))
    assert not mismatches, (len(mismatches), mismatches[:5])
    assert compared >= 8000, compared
    # Not vacuous: the corpus reaches every outcome of both scans.
    assert {"telegram", "terminal", True, False, "RAISE:AttributeError"} <= seen, seen
    assert long_cases >= 20, long_cases


# ===========================================================================
# 7.2 Hand cases: new == oracle, including the raised type
# ===========================================================================

TG_LINE = json.dumps(_prompt("[via Telegram · @o]\nhi"))
TERM_LINE = json.dumps(_prompt("plain terminal prompt"))
DEPTH = 100_000


def _deep(prefix: str) -> str:
    return prefix + "[" * DEPTH + "]" * DEPTH + "}"


HAS_INT_LIMIT = hasattr(sys, "get_int_max_str_digits")

HAND_CASES = {
    # The three divergences the parse rules close.
    "non_object_after_prompt": (
        TG_LINE + "\n[1,2]\n", ("RAISE:AttributeError", "RAISE:AttributeError")),
    "escaped_key_on_the_newest_prompt": (
        TERM_LINE + '\n{"typ\\u0065":"user","message":{"content":'
        '"[via Telegram · @o]\\nx"}}\n', ("telegram", False)),
    "split_marker_list_content": (
        TG_LINE + "\n" + json.dumps(_real_tool_result(
            [{"type": "text", "text": "aipager"},
             {"type": "text", "text": "safety policy"}])) + "\n",
        ("telegram", True)),
    "split_marker_two_blocks": (
        TG_LINE + "\n" + json.dumps(_tres_blocks(["aipager safety", "policy"])) + "\n",
        ("telegram", True)),
    # Candidate lines still raise exactly as before.
    "deep_user_prompt": (
        _deep('{"type":"user","message":{"content":"x"},"deep":') + "\n",
        ("RAISE:RecursionError", "RAISE:RecursionError")),
    "big_int_user_prompt": (
        '{"type":"user","message":{"content":"x"},"n":' + "7" * 5000 + "}\n",
        ("RAISE:ValueError", "RAISE:ValueError")),
    # A user line with no "message" key is read as its own message.
    "user_line_without_a_message_key": (
        TERM_LINE + '\n{"type":"user","content":"[via Telegram · @o]\\nhi"}\n',
        ("telegram", False)),
    "user_message_is_a_string": (
        TG_LINE + '\n{"type":"user","message":"x"}\n',
        ("RAISE:AttributeError", "RAISE:AttributeError")),
    "system_message_with_the_marker": (
        TG_LINE + '\n{"type":"system","message":"aipager safety policy"}\n',
        ("telegram", "RAISE:AttributeError")),
    # rev-iter1-001: repr of a list or non-str piece spells U+009A as
    # "\x9a", whose "a" completes a literal "ipager" to the marker.
    "repr_escape_completes_the_marker_utf8": (
        TG_LINE + "\n" + json.dumps(_tres([["\u009aipager safety policy"]]),
                                    ensure_ascii=False) + "\n",
        ("telegram", True)),
    "repr_escape_completes_the_marker_uppercase_escape": (
        TG_LINE + "\n" + json.dumps(_tres([["X"]])).replace(
            '"X"', '"\\u009Aipager safety policy"') + "\n",
        ("telegram", True)),
    # Raw UTF-8: JSON's own lowercase "\u009a" would put the bytes
    # "aipager" in the line and not tell the two words apart.
    "repr_escape_in_a_list_text": (
        TG_LINE + "\n" + json.dumps(_tres([{"type": "text", "text": [
            "\u009aipager safety policy"]}]), ensure_ascii=False) + "\n",
        ("telegram", True)),
    # rev-iter1-002: the first '"message"' run sits inside a key that ends
    # in an escaped quote; the entry's own message is a Telegram prompt.
    "escaped_quote_key_before_the_prompt": (
        TERM_LINE + "\n" + json.dumps({
            "type": "user", 'x"message': _tres("ok")["message"],
            "message": {"role": "user", "content": "[via Telegram · @o]\nhi"}})
        + "\n",
        ("telegram", False)),
    # rev-iter1-003: the prompt's own "message" comes first; a nested
    # tool-result-shaped one later in the line must not hide it (kills
    # a .search in place of the anchored .match).
    "nested_tool_result_after_the_prompt": (
        TERM_LINE + "\n" + json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "[via Telegram · @o]\nhi"},
            "x": {"message": _tres("ok")["message"]}}) + "\n",
        ("telegram", False)),
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", list(HAND_CASES))
def test_hand_cases_match_the_oracle(tmp_path, monkeypatch, name, chunk):
    if name == "big_int_user_prompt" and not HAS_INT_LIMIT:
        pytest.skip("no integer digit limit on this Python")
    body, expected = HAND_CASES[name]
    p = tmp_path / "hand.jsonl"
    p.write_text(body, encoding="utf-8")
    old, new = _old_and_new(monkeypatch, str(p), chunk)
    assert old == expected  # anchors the oracle copy itself
    assert new == expected


# ===========================================================================
# 7.3 Documented classes (design section 5): pin the NEW behaviour
# ===========================================================================

_NESTED_TR = {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}

DOCUMENTED = {
    # 1: the first "message" is a nested tool-result-shaped object.
    "nested_message_first": (
        TERM_LINE + "\n" + json.dumps({"type": "user", "x": {"message": _NESTED_TR},
                                       "message": {"role": "user", "content": TG}}),
        ("terminal", False), ("telegram", False)),
    # 2: duplicate keys; json.loads keeps the last "message".
    "duplicate_message_keys": (
        TERM_LINE + '\n{"type":"user","message":' + json.dumps(_NESTED_TR)
        + ',"message":' + json.dumps({"role": "user", "content": TG}) + "}",
        ("terminal", False), ("telegram", False)),
    # 2 (review-2): a duplicate "content" in the message, or a duplicate
    # "type" in its first block; the layout check reads the first, json
    # keeps the last.
    "duplicate_content_keys": (
        TERM_LINE + '\n{"type":"user","message":{"role":"user","content":'
        '[{"type":"tool_result","content":"ok"}],"content":' + json.dumps(TG) + "}}",
        ("terminal", False), ("telegram", False)),
    "duplicate_block_type_keys": (
        TERM_LINE + '\n{"type":"user","message":{"role":"user","content":'
        '[{"type":"tool_result","type":"text","text":' + json.dumps(TG) + "}]}}",
        ("terminal", False), ("telegram", False)),
    # 3: non-candidate lines the old parse raised on.
    "deep_assistant_line": (
        TG_LINE + "\n" + _deep('{"type":"assistant","message":{"content":') + "}",
        ("telegram", False), ("RAISE:RecursionError", "RAISE:RecursionError")),
    "big_int_assistant_line": (
        TG_LINE + '\n{"type":"assistant","n":' + "7" * 5000 + "}",
        ("telegram", False), ("RAISE:ValueError", "RAISE:ValueError")),
    "system_message_string": (
        TG_LINE + '\n{"type":"system","message":"x"}',
        ("telegram", False), ("telegram", "RAISE:AttributeError")),
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", list(DOCUMENTED))
def test_documented_classes_take_the_new_answer(tmp_path, monkeypatch, name, chunk):
    body, new_expected, old_expected = DOCUMENTED[name]
    p = tmp_path / "doc.jsonl"
    p.write_text(body + "\n", encoding="utf-8")
    old, new = _old_and_new(monkeypatch, str(p), chunk)
    assert new == new_expected
    if name != "big_int_assistant_line" or HAS_INT_LIMIT:
        assert old == old_expected


# ===========================================================================
# 7.4 Schema pin for the tool-result layout
# ===========================================================================

def _layout_matches(obj, seps) -> bool:
    line = json.dumps(obj, separators=seps).encode()
    return enforce._TOOL_RESULT_LAYOUT.match(line, line.find(b'"message"')) is not None


@pytest.mark.parametrize("seps", SEPARATORS)
@pytest.mark.parametrize("id_first", [True, False])
def test_a_real_tool_result_line_matches_the_layout(seps, id_first):
    assert _layout_matches(_real_tool_result("ok", id_first=id_first), seps)
    assert _layout_matches(_real_tool_result(
        [{"type": "text", "text": "a"}], id_first=id_first), seps)


@pytest.mark.parametrize("seps", SEPARATORS)
@pytest.mark.parametrize("content", [
    "[via Telegram · @o]\nhi",
    [{"type": "text", "text": "[via Telegram · @o]\nhi"}],
    [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                  "data": "iVBORw0KGgo="}},
     {"type": "text", "text": "what is this"}],
])
def test_a_genuine_prompt_never_matches_the_layout(seps, content):
    assert not _layout_matches(_real_prompt(content), seps)


# ===========================================================================
# 7.5 Owner short-circuit
# ===========================================================================

def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))
    return pol.get_role(name)


def _payload(transcript, tool="Bash", tool_input=None, cwd="/home/u/proj"):
    return {"hook_event_name": "PreToolUse", "session": SESSION,
            "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000001", "cwd": cwd,
            "tool_name": tool, "tool_input": tool_input or {"command": "ls"},
            "transcript_path": str(transcript)}


def _spy_open(monkeypatch, target):
    """Record every open() of ``target`` and the bytes read through it."""
    opened = []
    read = [0]
    original = builtins.open

    class _Spy:
        def __init__(self, f):
            self._f = f

        def read(self, *a):
            data = self._f.read(*a)
            read[0] += len(data)
            return data

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

        def __getattr__(self, name):
            return getattr(self._f, name)

    def spy(path_arg, *a, **k):
        f = original(path_arg, *a, **k)
        if str(path_arg) == str(target):
            opened.append(str(path_arg))
            return _Spy(f)
        return f

    monkeypatch.setattr(builtins, "open", spy)
    return opened, read


@pytest.mark.parametrize("bypass", [True, 1])
@pytest.mark.parametrize("transcript", ["telegram", "missing"])
def test_an_owner_turn_reads_no_transcript(tmp_path, monkeypatch, bypass, transcript):
    path = tmp_path / "t.jsonl"
    if transcript == "telegram":
        path.write_text(TG_LINE + "\n" + json.dumps(_tres(MARK)) + "\n")
    iterated = []

    def refuse(*a, **k):
        iterated.append(a)
        raise AssertionError("the transcript was iterated")

    monkeypatch.setattr(enforce, "read_snapshot", lambda s: {"bypass_safety": bypass})
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed", refuse)
    monkeypatch.setattr(enforce, "_iter_lines_reversed", refuse)
    opened, read = _spy_open(monkeypatch, path)
    for tool, inp in (("Bash", {"command": "ls"}),
                      ("Read", {"file_path": "~/.claude/x"})):
        assert enforce.decide(_payload(path, tool, inp)) is None
    assert iterated == []
    assert opened == [] and read[0] == 0


def _snap_dict(**over):
    snap = ps.resolve_snapshot(_builtin("user"), None, None)
    snap.pop("bypass_safety")
    snap.update(over)
    return snap


SNAPSHOTS = {
    "missing": None,
    "corrupt": b"{not json",
    "undecodable": b"\xff\xfe",
    "null": b"null",
    "list": b"[1]",
    "number": b"3",
    "string": b'"x"',
    "no_bypass": "no_bypass",
    "bypass_false": "bypass_false",
    "read_raises": "read_raises",
}


def _install_snapshot(monkeypatch, variant):
    target = ps.snapshot_path(SESSION)
    target.parent.mkdir(parents=True, exist_ok=True)
    value = SNAPSHOTS[variant]
    if variant == "read_raises":
        def boom(s):
            raise OSError("gone")
        monkeypatch.setattr(enforce, "read_snapshot", boom)
    elif variant == "no_bypass":
        target.write_text(json.dumps(_snap_dict()))
    elif variant == "bypass_false":
        target.write_text(json.dumps(_snap_dict(bypass_safety=False)))
    elif value is not None:
        target.write_bytes(value)


def _write_transcript(tmp_path, kind):
    p = tmp_path / f"{kind}.jsonl"
    lines = {"terminal": [TERM_LINE],
             "telegram": [TG_LINE],
             "telegram_marker": [TG_LINE, json.dumps(_tres(MARK))]}[kind]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


TOOLS = {"read_claude": ("Read", {"file_path": "~/.claude/x"}),
         "bash_ls": ("Bash", {"command": "ls"})}


def _decide_both(monkeypatch, data):
    new = enforce.decide(dict(data))
    with monkeypatch.context() as m:
        m.setattr(enforce, "_readable_snapshot", lambda s: None)
        m.setattr(enforce, "_origin_from_transcript", _oracle_origin_patch)
        m.setattr(enforce, "_turn_already_blocked", _ORACLE_BLOCKED)
        old = enforce.decide(dict(data))
    return old, new


@pytest.mark.parametrize("tool", list(TOOLS))
@pytest.mark.parametrize("transcript", ["terminal", "telegram", "telegram_marker"])
@pytest.mark.parametrize("variant", list(SNAPSHOTS))
def test_a_non_owner_snapshot_keeps_the_old_verdict(
        tmp_path, monkeypatch, variant, transcript, tool):
    _install_snapshot(monkeypatch, variant)
    name, inp = TOOLS[tool]
    data = _payload(_write_transcript(tmp_path, transcript), name, inp)
    old, new = _decide_both(monkeypatch, data)
    assert new == old


@pytest.mark.parametrize("variant,transcript,expected", [
    ("undecodable", "terminal", None),
    ("read_raises", "terminal", None),
    ("list", "terminal", None),
    ("number", "terminal", None),
    ("string", "terminal", None),
    ("undecodable", "telegram",
     {"tool": "Bash", "reason": "this tool call could not be checked, so it was denied"}),
    ("list", "telegram",
     {"tool": "Bash", "reason": "this tool call could not be checked, so it was denied"}),
])
def test_unreadable_snapshots_are_anchored(tmp_path, monkeypatch, variant,
                                           transcript, expected):
    _install_snapshot(monkeypatch, variant)
    data = _payload(_write_transcript(tmp_path, transcript))
    old, new = _decide_both(monkeypatch, data)
    assert new == expected
    assert old == expected


@pytest.mark.parametrize("bypass,expected_allowed", [
    (True, True), (1, False), ("yes", False), (False, False), (None, False)])
def test_fail_closed_allows_only_a_real_true_bypass(monkeypatch, bypass,
                                                   expected_allowed):
    # The one accepted short-circuit divergence (design 4.1) is a truthy
    # but not True bypass; fail_closed itself must keep requiring True.
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: {"bypass_safety": bypass})
    result = enforce.fail_closed({"session": SESSION, "tool_name": "Bash"})
    assert (result is None) is expected_allowed


def _note(role, **over):
    note = ps.resolve_snapshot(_builtin(role), None, None)
    note.update(over)
    return note


@pytest.mark.parametrize("notes", [
    [],
    [_note("owner")],
    [_note("owner"), _note("user")],
    [_note("owner", bypass_safety=1)],
    [_note("owner", bypass_safety="yes")],
    [_note("owner", bypass_safety=None)],
    [{k: v for k, v in _note("owner").items() if k != "bypass_safety"}],
])
def test_merged_snapshots_always_carry_a_bool_bypass(notes):
    assert type(ps.merge_snapshots(notes)["bypass_safety"]) is bool


# ===========================================================================
# 7.6 Linear reverse iterator
# ===========================================================================

def test_a_4mb_line_is_read_in_linear_time(tmp_path):
    line = b"x" * (4 * 1024 * 1024)
    p = tmp_path / "long.jsonl"
    p.write_bytes(b"head\n" + line + b"\ntail\n")
    t0 = time.perf_counter()
    raw = list(enforce._iter_raw_lines_reversed(p, chunk_bytes=1024))
    text = list(enforce._iter_lines_reversed(p, chunk_bytes=1024))
    elapsed = time.perf_counter() - t0
    assert raw == [b"", b"tail", line, b"head"]
    assert text == ["", "tail", line.decode(), "head"]
    assert elapsed < 1.0, elapsed


RAW_CONTRACT = [
    (b"a\nb\n", [b"", b"b", b"a"]),
    (b"", []),
    (b"\n", [b"", b""]),
    (b"first\nlast", [b"last", b"first"]),
    (b"a\r\nb\r\n", [b"", b"b\r", b"a\r"]),
    (b"abc\n" * 3, [b"", b"abc", b"abc", b"abc"]),   # newline ends each 4-byte chunk
    (b"\nabc" * 3, [b"abc", b"abc", b"abc", b""]),   # newline starts each 4-byte chunk
    (b"x" * 1000 + b"\n" + b"y" * 999, [b"y" * 999, b"x" * 1000]),
]


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 7, 64, 65536])
@pytest.mark.parametrize("data,expected", RAW_CONTRACT)
def test_the_raw_iterator_yields_split_lines_reversed(tmp_path, data, expected, chunk):
    p = tmp_path / "raw.bin"
    p.write_bytes(data)
    got = list(enforce._iter_raw_lines_reversed(p, chunk_bytes=chunk))
    assert got == expected
    assert got == (data.split(b"\n")[::-1] if data else [])


def test_the_raw_iterator_yields_bytes_and_the_wrapper_str(tmp_path):
    p = tmp_path / "mixed.bin"
    p.write_bytes(b"ok\n\xff bad\n\xd9\xbe\n")
    assert list(enforce._iter_raw_lines_reversed(p, chunk_bytes=3)) == [
        b"", b"\xd9\xbe", b"\xff bad", b"ok"]
    assert list(enforce._iter_lines_reversed(p, chunk_bytes=3)) == [
        "", "پ", "� bad", "ok"]


# ===========================================================================
# 7.7 Hook end to end
# ===========================================================================

def _long_turn(tmp_path, first_line: str, marker: bool) -> Path:
    """A prompt, optionally a deny marker, then about 5 MB of the current
    turn's tool results and assistant text."""
    p = tmp_path / "turn.jsonl"
    filler = []
    for i in range(1800):
        filler.append(json.dumps(_real_tool_result(f"line {i} " + "o" * 2000)))
        filler.append(json.dumps(_asst(f"step {i} " + "a" * 700)))
    lines = [first_line]
    if marker:
        lines.append(json.dumps(_real_tool_result(
            "aipager safety policy: protected path (deny_paths_no_access)")))
    p.write_text("\n".join(lines + filler) + "\n", encoding="utf-8")
    assert p.stat().st_size > 5_000_000
    return p


def _hook_stdout(monkeypatch, tmp_path, capsys, payload, oracle: bool) -> str:
    from aipager.dtach import notify_hook
    capsys.readouterr()
    with monkeypatch.context() as m:
        m.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
        m.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        if oracle:
            m.setattr(enforce, "_origin_from_transcript", _oracle_origin_patch)
            m.setattr(enforce, "_turn_already_blocked", _ORACLE_BLOCKED)
        notify_hook._run(SESSION, [b""])
    return capsys.readouterr().out


@pytest.mark.parametrize("case", ["halted", "denied", "owner", "terminal"])
def test_the_hook_prints_what_it_printed_before(tmp_path, monkeypatch, capsys, case):
    project = tmp_path / "proj"
    project.mkdir()
    role = "owner" if case == "owner" else "user"
    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(_builtin(role), None, None))
    assert ps.read_snapshot(SESSION)["bypass_safety"] is (role == "owner")
    first = TERM_LINE if case == "terminal" else TG_LINE
    transcript = _long_turn(tmp_path, first, marker=case in ("halted", "owner"))
    tool = (("Read", {"file_path": str(project / "a")}) if case == "halted"
            else ("Read", {"file_path": "~/.claude/x"}) if case in ("denied", "terminal")
            else ("Bash", {"command": "ls"}))
    payload = _payload(transcript, *tool, cwd=str(project))
    new = _hook_stdout(monkeypatch, tmp_path, capsys, payload, oracle=False)
    old = _hook_stdout(monkeypatch, tmp_path, capsys, payload, oracle=True)
    assert new == old
    if case == "halted":
        assert '"deny"' in new and "session halted" in new
    elif case == "denied":
        assert '"deny"' in new and "session halted" not in new
    else:
        assert new == ""
