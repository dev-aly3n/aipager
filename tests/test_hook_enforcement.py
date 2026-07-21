"""Phase E: PreToolUse hook enforcement (decide())."""

from __future__ import annotations

import json
import time

from aipager.dtach import enforce
from aipager.dtach.enforce import decide


def _transcript(tmp_path, marker: bool):
    p = tmp_path / (f"t_{'tg' if marker else 'term'}.jsonl")
    text = "[via Telegram · @bob · role:user]\nread it" if marker else "read it"
    p.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    return str(p)


def _snap(tmp_path, session, **over):
    base = {
        "bypass_safety": False, "deny_tools": [], "allow_tools": [],
        "deny_paths_no_access": ["~/.claude/**"],
        "deny_paths_no_write": [], "deny_bash_patterns": [r"\bclaude\b"],
    }
    base.update(over)
    (tmp_path / f"{session}.json").write_text(json.dumps(base))


def _data(tmp_path, **over):
    d = {
        "hook_event_name": "PreToolUse",
        "session": "claude-x__g100",
        "tool_name": "Read",
        "tool_input": {"file_path": "~/.claude/projects/o.jsonl"},
        "transcript_path": _transcript(tmp_path, marker=True),
    }
    d.update(over)
    return d


def _patch_snap(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: json.loads((tmp_path / f"{s}.json").read_text())
                        if (tmp_path / f"{s}.json").exists() else None)


def test_terminal_origin_allows(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, transcript_path=_transcript(tmp_path, marker=False))
    assert decide(d) is None  # terminal → unrestricted


def test_telegram_b1_read_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    block = decide(_data(tmp_path))
    assert block and "protected path" in block["reason"]


def test_owner_bypass(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100", bypass_safety=True)
    assert decide(_data(tmp_path)) is None


def test_missing_snapshot_failclosed(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)  # no snapshot file written
    block = decide(_data(tmp_path))  # B1 Read of transcript
    assert block is not None  # fail-closed deny


def test_bash_nested_claude_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, tool_name="Bash",
              tool_input={"command": "claude --resume abc"})
    block = decide(d)
    assert block and "blocked by safety policy" in block["reason"]
    assert "\\b" not in block["reason"]  # must NOT leak the raw regex


def test_normal_edit_allowed(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, tool_name="Edit",
              tool_input={"file_path": "/home/u/proj/app.js"})
    assert decide(d) is None


def test_non_pretooluse_ignored(tmp_path):
    assert decide({"hook_event_name": "Stop"}) is None


def test_deny_json_shape():
    payload = json.loads(enforce.deny_decision_json("X on Y"))
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "X on Y" in hso["permissionDecisionReason"]


# ---- regression: 2nd+ tool call in a Telegram turn (tool_result skip) ----

def _multi_tool_transcript(tmp_path, marker: bool, name="t_multi.jsonl"):
    """A turn where the marked (or unmarked) prompt is followed by a
    tool_result entry — which Claude records as type:"user". Mimics the
    transcript state when the hook fires for the SECOND tool call."""
    p = tmp_path / name
    prompt = ("[via Telegram · @bob · role:user]\ncheck claude version"
              if marker else "check claude version")
    lines = [
        {"type": "user", "message": {"content": prompt}},
        {"type": "assistant",
         "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "claude --version"}}]}},
        # tool result — type:"user", no marker (this is what tricked the bug)
        {"type": "user",
         "message": {"content": [{"type": "tool_result",
                                  "content": "blocked"}]}},
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": "let me retry"}]}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def test_origin_skips_tool_result_entries(tmp_path):
    # The governing prompt carries the marker → telegram, even though the
    # last type:"user" entry is a marker-less tool_result.
    assert enforce._origin_from_transcript(
        _multi_tool_transcript(tmp_path, marker=True)) == "telegram"
    assert enforce._origin_from_transcript(
        _multi_tool_transcript(tmp_path, marker=False, name="t_term.jsonl")
    ) == "terminal"


def test_second_tool_call_bash_still_blocked(tmp_path, monkeypatch):
    """Regression: a 2nd Bash call in a Telegram turn (after a tool_result)
    must still be blocked. Pre-fix this leaked through as 'terminal'."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "cat $(which claude); npm ls -g | grep claude"},
        transcript_path=_multi_tool_transcript(tmp_path, marker=True),
    )
    block = decide(d)
    assert block and "blocked by safety policy" in block["reason"]


def test_second_tool_call_read_still_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Read",
        tool_input={"file_path": "~/.claude/projects/o.jsonl"},
        transcript_path=_multi_tool_transcript(tmp_path, marker=True),
    )
    block = decide(d)
    assert block and "protected path" in block["reason"]


def test_second_tool_call_terminal_still_allowed(tmp_path, monkeypatch):
    """A terminal turn (no marker) stays unrestricted across tool calls."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "claude --version"},
        transcript_path=_multi_tool_transcript(
            tmp_path, marker=False, name="t_term2.jsonl"),
    )
    assert decide(d) is None


# ---- sticky turn-block: one block denies the rest of the turn ----------

def _blocked_turn_transcript(tmp_path, *, marker: bool, cross_turn: bool,
                             name="t_block.jsonl"):
    """A turn where a prior tool call was already blocked (its tool_result
    carries the 'aipager safety policy' marker). If ``cross_turn``, a NEW
    user prompt follows the block (so the block is in the PRIOR turn)."""
    p = tmp_path / name
    prompt = ("[via Telegram · @bob · role:user]\ncheck claude version"
              if marker else "check claude version")
    lines = [
        {"type": "user", "message": {"content": prompt}},
        {"type": "assistant",
         "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "claude --version"}}]}},
        {"type": "user",
         "message": {"content": [{"type": "tool_result",
                                  "content": "aipager safety policy: Bash "
                                             "command blocked by safety policy"}]}},
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": "blocked, dodging"}]}},
    ]
    if cross_turn:
        lines.append({"type": "user",
                      "message": {"content": "[via Telegram · @bob · role:user]"
                                             "\nnow list files"}})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def test_turn_already_blocked_detects_prior_block(tmp_path):
    assert enforce._turn_already_blocked(
        _blocked_turn_transcript(tmp_path, marker=True, cross_turn=False)) is True
    # A fresh prompt after the block clears it (new turn).
    assert enforce._turn_already_blocked(
        _blocked_turn_transcript(tmp_path, marker=True, cross_turn=True,
                                 name="t_block_x.jsonl")) is False


def test_sticky_blocks_nonmatching_workaround(tmp_path, monkeypatch):
    """The glob-dodge: a benign command matching NO pattern is still
    blocked because the turn already had a safety block."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        # matches no deny pattern on its own:
        tool_input={"command": "grep version cla*-code/package.json"},
        transcript_path=_blocked_turn_transcript(
            tmp_path, marker=True, cross_turn=False),
    )
    block = decide(d)
    assert block and "session halted" in block["reason"]


def test_sticky_not_applied_to_terminal_turn(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "grep version cla*-code/package.json"},
        transcript_path=_blocked_turn_transcript(
            tmp_path, marker=False, cross_turn=False, name="t_block_term.jsonl"),
    )
    assert decide(d) is None  # terminal origin → unrestricted


def test_sticky_cleared_by_new_prompt(tmp_path, monkeypatch):
    """After a new user prompt, a benign command runs again (block was
    per-turn, not permanent)."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "grep version cla*-code/package.json"},
        transcript_path=_blocked_turn_transcript(
            tmp_path, marker=True, cross_turn=True, name="t_block_new.jsonl"),
    )
    assert decide(d) is None  # new turn, benign command, no prior block


# ---- tail-reader helper (memory-bounded transcript scan) ----------------

def test_iter_lines_reversed_basic(tmp_path):
    p = tmp_path / "basic.txt"
    p.write_bytes(b"a\nb\nc\nd\ne\n")
    # Trailing newline produces a final empty "line" (b""); callers filter.
    out = [line for line in enforce._iter_lines_reversed(p) if line]
    assert out == ["e", "d", "c", "b", "a"]


def test_iter_lines_reversed_multibyte_utf8(tmp_path):
    # Persian "پ" is U+067E → UTF-8 0xD9 0xBE (2 bytes). Build a file
    # where a small chunk size splits the 2-byte sequence in the middle,
    # so the reader must buffer partial bytes across chunks to decode.
    p = tmp_path / "utf8.txt"
    line_a = "پپپپپپپپ"   # 8 chars × 2 bytes = 16 bytes
    line_b = "سلام"       # 4 chars × 2 bytes = 8 bytes
    p.write_text(f"{line_a}\n{line_b}\n", encoding="utf-8")
    # chunk_bytes=5 guarantees chunks land mid-Persian-character
    out = [line for line in enforce._iter_lines_reversed(p, chunk_bytes=5) if line]
    assert out == [line_b, line_a]


def test_iter_lines_reversed_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    assert list(enforce._iter_lines_reversed(p)) == []


def test_iter_lines_reversed_no_trailing_newline(tmp_path):
    p = tmp_path / "notrail.txt"
    p.write_bytes(b"first\nlast")
    out = [line for line in enforce._iter_lines_reversed(p) if line]
    assert out == ["last", "first"]


def test_iter_lines_reversed_bounded_memory(tmp_path):
    # Generate a 50MB file. Iterate, break after first non-empty line.
    # Only a small tail chunk (64KB by default) should be read from disk.
    p = tmp_path / "big.jsonl"
    with open(p, "wb") as f:
        junk = (b"x" * 1023 + b"\n") * 1024  # 1MB
        for _ in range(50):
            f.write(junk)
        f.write(b'{"type":"user","message":{"content":"tail"}}\n')
    assert p.stat().st_size > 50 * 1024 * 1024

    read_bytes = 0

    class _SpyFile:
        def __init__(self, wrapped):
            self._w = wrapped
        def read(self, n):
            nonlocal read_bytes
            data = self._w.read(n)
            read_bytes += len(data)
            return data
        def __enter__(self):
            self._w.__enter__()
            return self
        def __exit__(self, *a):
            return self._w.__exit__(*a)
        def __getattr__(self, name):
            return getattr(self._w, name)

    # Wrap open() only for this file, spy on read()
    real_open = enforce.open if hasattr(enforce, "open") else open
    import builtins
    original_open = builtins.open

    def spy_open(path_arg, *args, **kwargs):
        f = original_open(path_arg, *args, **kwargs)
        if str(path_arg) == str(p):
            return _SpyFile(f)
        return f

    builtins.open = spy_open
    try:
        it = enforce._iter_lines_reversed(p)
        first = next(iter(line for line in it if line))
    finally:
        builtins.open = original_open

    assert first == '{"type":"user","message":{"content":"tail"}}'
    # Must have read < 1 MB (way under the 50 MB file size).
    assert read_bytes < 1024 * 1024


def test_origin_from_transcript_short_circuits_on_huge_file(tmp_path):
    # 20 MB of unrelated entries + a final Telegram-marker user prompt.
    # Must return "telegram" in well under a second (would time out or
    # OOM the pre-fix implementation).
    p = tmp_path / "huge.jsonl"
    filler = json.dumps({"type": "assistant", "message":
                         {"content": [{"type": "text", "text": "x" * 800}]}})
    with open(p, "wb") as f:
        for _ in range(25 * 1024):  # ~20 MB
            f.write(filler.encode() + b"\n")
        f.write((json.dumps({"type": "user", "message":
                             {"content": "[via Telegram · @a · role:user]\ngo"}})
                 + "\n").encode())
    assert p.stat().st_size > 20 * 1024 * 1024
    t0 = time.perf_counter()
    assert enforce._origin_from_transcript(str(p)) == "telegram"
    assert time.perf_counter() - t0 < 1.0  # well under a second on any box


def test_turn_already_blocked_only_reads_current_turn(tmp_path):
    # 20 MB of filler with the last user prompt at the very end and NO
    # block marker in the current turn → False. Must complete quickly.
    p = tmp_path / "huge_noblk.jsonl"
    filler = json.dumps({"type": "assistant", "message":
                         {"content": [{"type": "text", "text": "y" * 800}]}})
    with open(p, "wb") as f:
        for _ in range(25 * 1024):
            f.write(filler.encode() + b"\n")
        f.write((json.dumps({"type": "user", "message":
                             {"content": "[via Telegram · @a · role:user]\ngo"}})
                 + "\n").encode())
    t0 = time.perf_counter()
    assert enforce._turn_already_blocked(str(p)) is False
    assert time.perf_counter() - t0 < 1.0


def test_origin_from_transcript_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_bytes(b"")
    # Fail-closed: unknown origin → "telegram"
    assert enforce._origin_from_transcript(str(p)) == "telegram"


def test_turn_already_blocked_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_bytes(b"")
    assert enforce._turn_already_blocked(str(p)) is False


def test_origin_from_transcript_missing_file(tmp_path):
    # OSError path — fail-closed to "telegram"
    assert enforce._origin_from_transcript(str(tmp_path / "nope.jsonl")) == "telegram"


def test_turn_already_blocked_missing_file(tmp_path):
    assert enforce._turn_already_blocked(str(tmp_path / "nope.jsonl")) is False
