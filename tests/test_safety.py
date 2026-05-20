"""Phase E: pure safety matchers."""

from __future__ import annotations

from aipager import safety
from aipager.safety import bash_violation, path_violation, tool_violation

NO_ACCESS = safety.DENY_PATHS_NO_ACCESS
BASH = safety.DENY_BASH_PATTERNS


# ---- path_violation -----------------------------------------------------

def test_read_transcript_blocked():
    v = path_violation("Read", {"file_path": "~/.claude/projects/x.jsonl"},
                       NO_ACCESS, ())
    assert v and "protected path" in v


def test_read_aipager_config_blocked():
    assert path_violation("Read", {"file_path": "~/.config/aipager/aipager.yaml"},
                          NO_ACCESS, ()) is not None


def test_edit_project_file_allowed():
    assert path_violation("Edit", {"file_path": "~/projects/web/app.js"},
                          NO_ACCESS, ()) is None


def test_glob_does_not_match_unrelated():
    assert path_violation("Glob", {"path": "/tmp/whatever"}, NO_ACCESS, ()) is None


def test_b2_write_only_block():
    no_write = ("**/*.lock",)
    # write blocked
    assert path_violation("Write", {"file_path": "/x/poetry.lock"}, (), no_write)
    # read allowed (B2 = write-only)
    assert path_violation("Read", {"file_path": "/x/poetry.lock"}, (), no_write) is None


def test_non_path_tool_ignored():
    assert path_violation("WebFetch", {"url": "http://x"}, NO_ACCESS, ()) is None


def test_dir_glob_matches_dir_itself():
    # ~/.claude/** should also block ~/.claude
    assert path_violation("Read", {"file_path": "~/.claude"}, NO_ACCESS, ()) is not None


# ---- bash_violation -----------------------------------------------------

def test_bash_sudo_blocked():
    assert bash_violation("sudo rm -rf /", BASH) is not None


def test_bash_nested_claude_blocked():
    assert bash_violation("claude --resume abc", BASH) is not None
    assert bash_violation("claude -p 'hi'", BASH) is not None


def test_bash_append_system_prompt_flag_blocked():
    assert bash_violation('foo --append-system-prompt "x"', BASH) is not None


def test_bash_rm_protected_blocked():
    assert bash_violation("rm -rf ~/.config/aipager", BASH) is not None


def test_bash_innocent_allowed():
    assert bash_violation("ls -la && npm test", BASH) is None


# ---- tool_violation -----------------------------------------------------

def test_deny_tools():
    assert tool_violation("Bash", ("Bash",), ()) is not None
    assert tool_violation("Read", ("Bash",), ()) is None


def test_allow_tools_whitelist():
    assert tool_violation("Bash", (), ("Read", "Grep")) is not None
    assert tool_violation("Read", (), ("Read", "Grep")) is None
