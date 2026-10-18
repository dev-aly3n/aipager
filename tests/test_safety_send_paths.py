"""SendUserFile's ``files`` list in the path floor: each entry checked as a
Read, a bare string read as one entry, an unreadable list refused, and the
reasons written in the shapes the daemon already reads."""

from __future__ import annotations

import re

import pytest

from aipager import safety
from aipager.safety import path_targets, path_violation

NO_ACCESS = safety.DENY_PATHS_NO_ACCESS


def _shape_code(reason: str) -> str | None:
    hits = [code for code, shape in safety.REASON_SHAPES
            if re.match(shape, reason)]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def test_send_user_file_entry_on_no_access_is_denied():
    v = path_violation(
        "SendUserFile",
        {"files": ["/srv/ok.png", "~/.config/aipager/aipager.yaml"]},
        NO_ACCESS, ())
    assert v == "SendUserFile on protected path ~/.config/aipager/**"
    assert _shape_code(v) == safety.REASON_PATH_PROTECTED


def test_files_as_a_single_string_is_checked():
    v = path_violation("SendUserFile",
                       {"files": "~/.claude/projects/x.jsonl"}, NO_ACCESS, ())
    assert v == "SendUserFile on protected path ~/.claude/**"


def test_harmless_files_are_allowed():
    assert path_violation("SendUserFile", {"files": ["/srv/a.png", "b.pdf"]},
                          NO_ACCESS, (), cwd="/srv") is None


def test_relative_entry_resolves_against_cwd(tmp_path):
    cwd = str(tmp_path)
    v = path_violation("SendUserFile", {"files": ["secret/x"]},
                       (cwd + "/secret/**",), (), cwd=cwd)
    assert v is not None and v.startswith("SendUserFile on protected path")


@pytest.mark.parametrize("files", [
    [], 7, None, [7], ["ok", None], [""], ["  "], ["a\x00b"], {"x": 1},
], ids=["empty", "int", "missing", "int-entry", "none-entry", "blank",
        "spaces", "nul", "dict"])
def test_unreadable_list_fails_closed(files):
    v = path_violation("SendUserFile", {"files": files}, NO_ACCESS, ())
    assert v == "SendUserFile with an unreadable path"
    assert _shape_code(v) == safety.REASON_PATH_UNREADABLE


def test_member_home_read_bound_applies(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "root").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    roots = (str(home / "root"),)
    inside = str(home / "root" / "a.png")
    outside = str(home / "other.png")
    assert path_violation("SendUserFile", {"files": [inside]}, (), (),
                          home_read_roots=roots) is None
    v = path_violation("SendUserFile", {"files": [inside, outside]}, (), (),
                       home_read_roots=roots)
    assert v == ("SendUserFile denied - this role reads in the home folder "
                 "only inside its own folder")
    assert _shape_code(v) == safety.REASON_OUTSIDE_ROOT_READ


def test_own_reply_file_is_readable_like_read():
    reply = "/tmp/claude-reply-claude-x.txt"
    assert path_violation("SendUserFile", {"files": [reply]}, NO_ACCESS, (),
                          readable=(reply,)) is None


def test_path_keys_has_no_list_tool():
    # tests/test_policy_containment.py reads one string key per tool.
    assert "SendUserFile" not in safety._PATH_KEYS
    assert all(isinstance(v, str) for v in safety._PATH_KEYS.values())


def test_path_targets_shapes():
    assert path_targets("SendUserFile", {"files": "a.png"}) == ["a.png"]
    assert path_targets("SendUserFile", {"files": ["a", "b"]}) == ["a", "b"]
    assert path_targets("SendUserFile", {"files": []}) is None
    assert path_targets("SendUserFile", {}) is None
    assert path_targets("Read", {"file_path": "x"}) == ["x"]
    assert path_targets("Read", {}) == []
    assert path_targets("Read", {"file_path": 3}) is None
    assert path_targets("WebFetch", {"url": "x"}) == []
