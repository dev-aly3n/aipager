"""The hook holds a role without ``can_manage`` to its members' root
folder (``root`` in the turn's snapshot): writes only in the session
folder when it is inside the root (and the scratchpad), and no Read,
LSP, Glob or Grep in the home folder outside the root. System paths stay
readable; another scope's root is never reachable; owner and admin are
unchanged. Runs ``enforce.decide`` against a fake home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import member_root, policy, safety
from aipager import policy_snapshot as ps
from aipager.dtach import enforce

SESSION = "claude-proj"
SID = "5b1d7363-aaaa-bbbb-cccc-0000000000aa"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    return h


@pytest.fixture
def root(home):
    r = home / "aipager-users" / "sara"
    (r / "proj").mkdir(parents=True)
    (r / "other").mkdir()
    (r / "other" / "notes.md").write_text("x")
    return r


def _pol(tmp_path=None, text=None):
    if text is None:
        return policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))
    f = tmp_path / "policy.yaml"
    f.write_text(text)
    return policy.load_policy(f, tmp_path / "none.d")


def _use(role, root=None, deny=()):
    bound = member_root.RootBound(str(root), tuple(deny)) if root is not None else None
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(role, None, None,
                                                          root_bound=bound))


def _decide(tmp_path, tool, tool_input, cwd):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @sara · role:user]\ndo it"}}) + "\n")
    out = enforce.decide({
        "hook_event_name": "PreToolUse", "session": SESSION, "session_id": SID,
        "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input,
        "transcript_path": str(t)})
    return (out or {}).get("reason")


def _read(tmp_path, path, cwd):
    return _decide(tmp_path, "Read", {"file_path": str(path)}, cwd)


ROLES = ["user", "read_only"]


@pytest.mark.parametrize("name", ROLES)
def test_read_home_outside_root_denied(tmp_path, home, root, name):
    (home / "aipager").mkdir()
    (home / "aipager" / "x.py").write_text("x")
    _use(_pol().get_role(name), root)
    assert _read(tmp_path, home / "aipager" / "x.py", root / "proj") == (
        "Read denied - this role reads in the home folder only inside its own folder")
    assert _decide(tmp_path, "LSP", {"filePath": str(home / "aipager" / "x.py")},
                   root / "proj").startswith("LSP denied - this role reads")
    assert _read(tmp_path, "~/.bashrc", root / "proj")


def test_read_sibling_folder_inside_root_allowed(tmp_path, home, root):
    _use(_pol().get_role("user"), root)
    assert _read(tmp_path, root / "other" / "notes.md", root / "proj") is None
    assert _read(tmp_path, "../other/notes.md", root / "proj") is None


def test_system_path_readable(tmp_path, home, root):
    _use(_pol().get_role("user"), root)
    assert _read(tmp_path, "/etc/hostname", root / "proj") is None


def test_scratchpad_under_home_readable(tmp_path, home, root, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(home / ".tmp"))
    _use(_pol().get_role("user"), root)
    scratch = enforce._scratch_root(str(root / "proj"), SID)
    assert scratch.startswith(str(home))
    assert _read(tmp_path, f"{scratch}/scratchpad/a.txt", root / "proj") is None
    assert _decide(tmp_path, "Write", {"file_path": f"{scratch}/scratchpad/a.txt",
                                       "content": "x"}, root / "proj") is None


def test_reply_context_readable(tmp_path, home, root, monkeypatch):
    reply = home / "rt" / f"claude-reply-{SESSION}.txt"
    monkeypatch.setattr(enforce, "reply_context_path", lambda n: reply)
    _use(_pol().get_role("user"), root)
    assert _read(tmp_path, reply, root / "proj") is None
    assert _read(tmp_path, home / "rt" / "claude-reply-other.txt", root / "proj")


def test_a_symlink_in_the_root_to_a_home_file_is_denied(tmp_path, home, root):
    (home / "private.txt").write_text("x")
    (root / "proj" / "link.txt").symlink_to(home / "private.txt")
    _use(_pol().get_role("user"), root)
    assert _read(tmp_path, root / "proj" / "link.txt", root / "proj").startswith(
        "Read denied - this role reads in the home folder")


def test_other_root_outside_home_denied(tmp_path, home, root):
    other = tmp_path / "srv" / "lee"
    other.mkdir(parents=True)
    (other / "a.txt").write_text("x")
    _use(_pol().get_role("user"), root, deny=(str(other),))
    assert _read(tmp_path, other / "a.txt", root / "proj") == (
        f"Read on protected path {other}/**")
    # Not a root: still readable (outside the home folder).
    (tmp_path / "srv" / "pub.txt").write_text("x")
    assert _read(tmp_path, tmp_path / "srv" / "pub.txt", root / "proj") is None


def test_write_inside_the_session_folder_allowed(tmp_path, home, root):
    _use(_pol().get_role("user"), root)
    assert _decide(tmp_path, "Write", {"file_path": str(root / "proj" / "a.py"),
                                       "content": "x"}, root / "proj") is None
    # Only the session folder, not the whole root (as before).
    assert _decide(tmp_path, "Write", {"file_path": str(root / "other" / "a.py"),
                                       "content": "x"}, root / "proj").startswith(
        "Write outside the session's folder")


def test_write_in_old_folder_outside_root_denied(tmp_path, home, root):
    old = home / "usertest"
    old.mkdir()
    _use(_pol().get_role("user"), root)
    assert _decide(tmp_path, "Write", {"file_path": str(old / "a.py"), "content": "x"},
                   old) == ("Write denied - this session runs outside this role's own "
                            "folder, so this role can only write in its scratchpad")


def test_search_in_old_folder_says_root(tmp_path, home, root):
    old = home / "usertest"
    old.mkdir()
    _use(_pol().get_role("user"), root)
    assert _decide(tmp_path, "Grep", {"pattern": "x"}, old) == (
        "Grep denied - this session runs outside this role's own folder, so this "
        "role can only search in its scratchpad")
    assert _decide(tmp_path, "Grep", {"pattern": "x"}, root / "proj") is None


def test_sentinel_holds_reads_to_the_session_folder(tmp_path, home, root):
    snap = {**ps.resolve_snapshot(_pol().get_role("user"), None, None),
            "root": safety.ROOT_SENTINEL}
    ps.write_merged_snapshot(SESSION, snap)
    assert _read(tmp_path, root / "proj" / "a", root / "proj") is None
    assert _read(tmp_path, root / "other" / "notes.md", root / "proj")
    assert _decide(tmp_path, "Write", {"file_path": str(root / "proj" / "a"),
                                       "content": "x"}, root / "proj") is None


def test_custom_bypass_role_without_manage_held_to_root(tmp_path, home, root):
    role = _pol(tmp_path, "roles:\n  lead:\n    bypass_role_denies: true\n").get_role("lead")
    (home / "aipager").mkdir()
    _use(role, root)
    # Writes anywhere in its root, not outside; no home reads outside it.
    assert _decide(tmp_path, "Write", {"file_path": str(root / "other" / "b"),
                                       "content": "x"}, root / "proj") is None
    assert _decide(tmp_path, "Write", {"file_path": str(home / "aipager" / "b"),
                                       "content": "x"}, root / "proj")
    assert _read(tmp_path, home / "aipager" / "b", root / "proj")
    assert _decide(tmp_path, "Grep", {"pattern": "x", "path": str(home / "aipager")},
                   root / "proj")


@pytest.mark.parametrize("name", ["owner", "admin"])
def test_owner_admin_unrestricted(tmp_path, home, root, name):
    (home / "aipager").mkdir()
    (home / "aipager" / "x.py").write_text("x")
    _use(_pol().get_role(name), root)            # the bound is ignored for them
    assert "root" not in ps.read_snapshot(SESSION)
    assert _read(tmp_path, home / "aipager" / "x.py", root / "proj") is None
    assert _decide(tmp_path, "Write", {"file_path": str(home / "aipager" / "y.py"),
                                       "content": "x"}, home / "aipager") is None


def test_without_a_root_a_user_reads_as_before(tmp_path, home, root):
    (home / "aipager").mkdir()
    (home / "aipager" / "x.py").write_text("x")
    _use(_pol().get_role("user"))
    assert _read(tmp_path, home / "aipager" / "x.py", root / "proj") is None


def test_root_bounds_helper(home, root):
    write, reads, root_cwd = enforce._root_bounds(
        str(root / "proj"), SID, str(root), True, False, ("/r/reply",))
    assert write == (str(root / "proj"), enforce._scratch_root(str(root / "proj"), SID))
    assert reads[0] == str(root) and reads[-1] == "/r/reply" and root_cwd is False
    write, _reads, root_cwd = enforce._root_bounds(
        str(home), SID, str(root), True, True)
    assert root_cwd is False and write == (enforce._scratch_root(str(home), SID),)
