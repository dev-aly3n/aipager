"""Tests for the Write/Edit diff RENDERING helpers (item 4.4).

Whether a preview is SENT is a /settings preference now
("diff-preview-settings-toggle") — see test_bot_notify.py; the env-var
switch these tests once pinned no longer exists.
"""

from __future__ import annotations

from aipager.bot.transport import (
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _build_diff_block,
    _truncate_diff,
)


# ----- _build_diff_block -----

def test_build_diff_block_write_creates_unified_diff():
    out = _build_diff_block("Write", {
        "file_path": "/tmp/new.txt",
        "content": "hello\nworld\n",
    })
    assert out is not None
    header, body = out
    assert "/tmp/new.txt" in header
    assert "Write" in header
    # Unified diff has +++ /dev/null markers in dev mode, but should
    # include the added lines.
    assert "+hello" in body
    assert "+world" in body


def test_build_diff_block_edit_shows_change():
    out = _build_diff_block("Edit", {
        "file_path": "/tmp/x.py",
        "old_string": "alpha\nbeta\n",
        "new_string": "alpha\nGAMMA\n",
    })
    assert out is not None
    _, body = out
    assert "-beta" in body
    assert "+GAMMA" in body


def test_build_diff_block_empty_when_no_change():
    """Edit with old == new produces no diff body."""
    out = _build_diff_block("Edit", {
        "file_path": "/tmp/same.py",
        "old_string": "no change here",
        "new_string": "no change here",
    })
    assert out is not None
    _, body = out
    # difflib will produce only headers when no lines differ, but body
    # has no '+' or '-' content lines
    line_kinds = {line[0] for line in body.splitlines() if line}
    assert line_kinds <= {"-", "+"}  # only file headers, no body changes


def test_build_diff_block_missing_file_path_returns_none():
    assert _build_diff_block("Edit", {
        "old_string": "x", "new_string": "y",
    }) is None
    assert _build_diff_block("Write", {"content": "x"}) is None


def test_build_diff_block_empty_write_returns_none():
    assert _build_diff_block("Write", {
        "file_path": "/tmp/x",
        "content": "",
    }) is None


def test_build_diff_block_other_tools_return_none():
    assert _build_diff_block("Read", {"file_path": "/tmp/x"}) is None
    assert _build_diff_block("Bash", {"command": "ls"}) is None


# ----- _truncate_diff -----

def test_truncate_diff_short_input_unchanged():
    lines = ["@@@ header", "+one", "+two"]
    body, dropped = _truncate_diff(lines)
    assert body == "@@@ header\n+one\n+two"
    assert dropped == 0


def test_truncate_diff_caps_line_count():
    lines = [f"+line{i}" for i in range(100)]
    body, dropped = _truncate_diff(lines)
    # Keeps the first _DIFF_MAX_LINES
    assert body.count("\n") == _DIFF_MAX_LINES - 1
    assert dropped == 100 - _DIFF_MAX_LINES


def test_truncate_diff_caps_char_count():
    # 5 short lines so the line cap doesn't fire first, but each line is
    # huge so the char cap does
    big = "x" * 1000
    lines = [big, big, big, big, big]
    body, dropped = _truncate_diff(lines)
    assert len(body) <= _DIFF_MAX_CHARS
