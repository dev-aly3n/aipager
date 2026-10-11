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


def _decide(tmp_path, tool, tool_input, cwd, agent_id=None, blocked=False,
            terminal=False):
    t = tmp_path / "t.jsonl"
    lines = [{"type": "user", "message": {
        "content": "do it" if terminal
        else "[via Telegram · @sara · role:user]\ndo it"}}]
    if blocked:  # a tool call earlier in this turn was denied
        lines.append({"type": "user", "message": {"content": [{
            "type": "tool_result",
            "content": "aipager safety policy: Write outside the root"}]}})
    t.write_text("".join(json.dumps(x) + "\n" for x in lines))
    data = {
        "hook_event_name": "PreToolUse", "session": SESSION, "session_id": SID,
        "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input,
        "transcript_path": str(t)}
    if agent_id:
        data["agent_id"] = agent_id
    out = enforce.decide(data)
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


# ---- a custom role with bypass_safety but no can_manage (rev-iter1-001) --------------

_BYPASS_NO_MANAGE = "roles:\n  boss:\n    bypass_safety: true\n"


def test_bypass_safety_role_without_manage_held_to_root(tmp_path, home, root):
    """Only owner and admin are unrestricted: a role you define with the
    owner's bypass but without can_manage still writes, searches and
    reads in the home folder only inside its root (and the scratchpad)."""
    role = _pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss")
    assert role.bypass_safety is True and role.can_manage is not True
    (home / "aipager").mkdir()
    (home / "aipager" / "x.py").write_text("x")
    _use(role, root)
    assert ps.read_snapshot(SESSION)["root"] == str(root)
    # Outside the root: denied.
    assert _decide(tmp_path, "Write", {"file_path": str(home / "aipager" / "b"),
                                       "content": "x"}, root / "proj")
    assert _read(tmp_path, home / "aipager" / "x.py", root / "proj") == (
        "Read denied - this role reads in the home folder only inside its own folder")
    assert _decide(tmp_path, "Grep", {"pattern": "x", "path": str(home / "aipager")},
                   root / "proj")
    # Inside the root: anywhere, as for bypass_role_denies.
    assert _decide(tmp_path, "Write", {"file_path": str(root / "other" / "b"),
                                       "content": "x"}, root / "proj") is None
    assert _read(tmp_path, root / "other" / "notes.md", root / "proj") is None
    # Outside the home folder, still readable; its bypass keeps its tools.
    assert _read(tmp_path, "/etc/hostname", root / "proj") is None
    assert _decide(tmp_path, "Bash", {"command": "ls"}, root / "proj") is None


def test_bypass_safety_role_without_manage_held_when_the_check_fails(tmp_path, home,
                                                                     root):
    """A decision that fails is allowed for the owner's bypass, but not
    for a bypass held to a root folder: that fails closed."""
    _use(_pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss"), root)
    data = {"session": SESSION, "tool_name": "Read",
            "tool_input": {"file_path": str(home / "x")}}
    assert enforce.fail_closed(data)["reason"]
    _use(_pol().get_role("owner"), root)
    assert enforce.fail_closed(data) is None


def test_bypass_safety_with_manage_is_the_owner(tmp_path, home, root):
    role = _pol(tmp_path, "roles:\n  boss:\n    bypass_safety: true\n"
                          "    can_manage: true\n").get_role("boss")
    (home / "aipager").mkdir()
    _use(role, root)
    assert "root" not in ps.read_snapshot(SESSION)
    assert _decide(tmp_path, "Write", {"file_path": str(home / "aipager" / "b"),
                                       "content": "x"}, root / "proj") is None


AGENT = "a1b2c3d4e5f60708"


def test_bypass_role_pinned_agent_held_after_an_owner_snapshot(tmp_path, home, root):
    """A background agent launched by a bypass_safety role without
    can_manage is pinned to that role's rooted rights. When the owner's
    message later replaces the session snapshot, the agent is still held
    to the root: its pin is no owner's bypass (rev-iter2-001)."""
    (home / "aipager").mkdir()
    (home / "aipager" / "x.py").write_text("x")
    _use(_pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss"), root)
    assert ps.write_pin(SESSION, AGENT, {
        "rules": ps.safety_rules(ps.read_snapshot(SESSION)), "terminal": False})
    _use(_pol().get_role("owner"))
    assert "root" not in ps.read_snapshot(SESSION)
    read = {"file_path": str(home / "aipager" / "x.py")}
    write = {"file_path": str(home / "aipager" / "y"), "content": "x"}
    # The owner's own calls are unrestricted ...
    assert _decide(tmp_path, "Read", read, root / "proj") is None
    # ... the pinned agent's are not.
    assert _decide(tmp_path, "Read", read, root / "proj", agent_id=AGENT) == (
        "Read denied - this role reads in the home folder only inside its own folder")
    assert _decide(tmp_path, "Write", write, root / "proj", agent_id=AGENT)
    # A failed check: allowed for the owner, refused for the agent.
    data = {"session": SESSION, "tool_name": "Read", "tool_input": read}
    assert enforce.fail_closed(data) is None
    assert enforce.fail_closed({**data, "agent_id": AGENT})["reason"]


def test_bypass_role_in_a_session_outside_its_root(tmp_path, home, root):
    """A session whose folder is outside the root is not this role's: as
    for a built-in user, it writes and searches only in its scratchpad
    there, and the deny reasons say so."""
    old = home / "aipager"
    old.mkdir()
    _use(_pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss"), root)
    assert _decide(tmp_path, "Write", {"file_path": str(old / "y"), "content": "x"},
                   old) == ("Write denied - this session runs outside this role's own "
                            "folder, so this role can only write in its scratchpad")
    assert _decide(tmp_path, "Write", {"file_path": str(root / "other" / "y"),
                                       "content": "x"}, old)
    assert _decide(tmp_path, "Glob", {"pattern": "*", "path": str(old)}, old) == (
        "Glob denied - this session runs outside this role's own folder, so this "
        "role can only search in its scratchpad")
    # In a session inside the root it still writes anywhere in the root.
    assert _decide(tmp_path, "Write", {"file_path": str(root / "other" / "y"),
                                       "content": "x"}, root / "proj") is None


def test_bypass_role_turn_halts_after_a_block(tmp_path, home, root):
    """A root-bound bypass turn that was already denied once stays halted,
    like any held turn: no rewording around the root (rev-iter2-002). The
    owner's bypass is not halted."""
    _use(_pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss"), root)
    inside = {"file_path": str(root / "other" / "y"), "content": "x"}
    assert _decide(tmp_path, "Write", inside, root / "proj") is None
    assert "session halted" in _decide(tmp_path, "Write", inside, root / "proj",
                                       blocked=True)
    assert "session halted" in _decide(tmp_path, "Bash", {"command": "ls"},
                                       root / "proj", blocked=True)
    # A turn typed in the terminal is unrestricted and never halted, even
    # with the member's rooted snapshot still on file.
    assert _decide(tmp_path, "Write", inside, root / "proj", blocked=True,
                   terminal=True) is None
    _use(_pol().get_role("owner"))
    assert _decide(tmp_path, "Write", inside, root / "proj", blocked=True) is None
