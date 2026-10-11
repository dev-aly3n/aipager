"""The PreToolUse hook holds a restricted member's turn to their root
(design.md success criterion 9; entrypoints.md "Hook").

The snapshot is built as entrypoints.md says (``resolve_snapshot`` with
``root_bound=member_root.snapshot_bound(...)``), written with
``write_merged_snapshot``, and judged by ``enforce.decide`` on a
Telegram-marked transcript. Deny reasons are compared with the English
sentences entrypoints.md documents."""

from __future__ import annotations

import os
import sys

import pytest

from aipager import policy_snapshot as ps
from aipager import safety
from aipager.dtach import enforce
from aipager.scope import Member, Scope

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, BOB, CY, HAL, BOSS = kit.OWNER, kit.ADMIN, kit.BOB, kit.CY, kit.HAL, kit.BOSS
GROUP, SESSION, SID = kit.GROUP, kit.SESSION, kit.CLAUDE_SID

RESTRICTED = {"user": BOB, "read_only": CY}


def WRITE_OUT(tool):
    return (f"{tool} outside the session's folder - a restricted turn writes only "
            "in its project and scratchpad")


def ROOT_CWD_WRITE(tool):
    return (f"{tool} denied - this session runs outside this role's own folder, "
            "so this role can only write in its scratchpad")


def READ_OUT(tool):
    return f"{tool} denied - this role reads in the home folder only inside its own folder"


def ROOT_CWD_SEARCH(tool):
    return (f"{tool} denied - this session runs outside this role's own folder, "
            "so this role can only search in its scratchpad")


@pytest.fixture
def proj(team_root):
    p = team_root / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "app.py").write_text("print('hi')\n")
    return p


@pytest.fixture
def old(home):
    """A folder outside the root, where an older session works."""
    p = home / "aipager"
    (p / "src").mkdir(parents=True)
    (p / "src" / "main.py").write_text("x = 1\n")
    return p


def _read(hook, path, cwd, tool="Read"):
    return hook(tool, {"file_path": str(path)}, cwd)


def _write(hook, path, cwd, tool="Write"):
    inp = {"file_path": str(path), "content": "x"}
    if tool == "Edit":
        inp = {"file_path": str(path), "old_string": "a", "new_string": "b"}
    return hook(tool, inp, cwd)


def _reason(block):
    return block["reason"] if block else None


# ---- writes ------------------------------------------------------------------------------

@pytest.mark.parametrize("role", list(RESTRICTED))
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_outside_root_denied(hook, use, proj, old, role, tool):
    use(RESTRICTED[role], GROUP)
    assert _reason(_write(hook, old / "src" / "main.py", proj, tool)) == WRITE_OUT(tool)


@pytest.mark.parametrize("role", list(RESTRICTED))
def test_write_in_the_home_folder_itself_denied(hook, use, proj, home, role):
    use(RESTRICTED[role], GROUP)
    assert _write(hook, home / ".bashrc", proj) is not None


def test_write_in_a_sibling_folder_inside_root_denied(hook, use, proj, team_root):
    """Writes stay in the session folder: the root does not widen them."""
    use(BOB, GROUP)
    assert _write(hook, team_root / "other" / "a.txt", proj) is not None


def test_write_in_the_session_folder_allowed(hook, use, proj):
    use(BOB, GROUP)
    assert _write(hook, proj / "src" / "new.py", proj) is None


def test_write_in_the_session_folder_by_relative_path_allowed(hook, use, proj):
    use(BOB, GROUP)
    assert _write(hook, "src/new.py", proj) is None


def test_write_climbing_out_by_dotdot_denied(hook, use, proj):
    use(BOB, GROUP)
    assert _write(hook, "../../../aipager/x.py", proj) is not None


@pytest.mark.parametrize("role", list(RESTRICTED))
def test_write_in_old_folder_outside_root_denied(hook, use, old, role):
    use(RESTRICTED[role], GROUP)
    assert _reason(_write(hook, old / "src" / "main.py", old)) == ROOT_CWD_WRITE("Write")


def test_write_in_a_prefix_sibling_session_folder_says_root(hook, use, team_root):
    sib = team_root.parent / (team_root.name + "2")
    sib.mkdir(parents=True)
    use(BOB, GROUP)
    assert _reason(_write(hook, sib / "a.txt", sib)) == ROOT_CWD_WRITE("Write")


@pytest.mark.parametrize("where", ["inside", "outside"])
def test_scratchpad_stays_writable(hook, use, proj, old, where):
    use(BOB, GROUP)
    cwd = proj if where == "inside" else old
    scratch = enforce._scratch_root(str(cwd), SID)
    assert _write(hook, f"{scratch}/scratchpad/notes.md", cwd) is None


# ---- reads ------------------------------------------------------------------------------

@pytest.mark.parametrize("role", list(RESTRICTED))
def test_read_home_outside_root_denied(hook, use, proj, old, role):
    use(RESTRICTED[role], GROUP)
    assert _reason(_read(hook, old / "src" / "main.py", proj)) == READ_OUT("Read")


def test_read_home_outside_root_by_tilde_denied(hook, use, proj, old):
    use(BOB, GROUP)
    assert _reason(_read(hook, "~/aipager/src/main.py", proj)) == READ_OUT("Read")


def test_read_a_plain_home_file_denied(hook, use, proj, home):
    use(BOB, GROUP)
    assert _reason(_read(hook, home / "notes.txt", proj)) == READ_OUT("Read")


def test_read_by_dotdot_out_of_the_root_denied(hook, use, proj):
    use(BOB, GROUP)
    assert _read(hook, "../../../aipager/src/main.py", proj) is not None


def test_read_in_a_prefix_sibling_of_the_root_denied(hook, use, proj, team_root):
    sib = team_root.parent / (team_root.name + "2")
    sib.mkdir(parents=True)
    use(BOB, GROUP)
    assert _reason(_read(hook, sib / "a.txt", proj)) == READ_OUT("Read")


def test_read_another_members_root_under_home_denied(hook, use, proj, hal_root):
    use(BOB, GROUP)
    assert _read(hook, hal_root / "secret.md", proj) is not None


def test_lsp_home_outside_root_denied(hook, use, proj, old):
    # Claude Code's LSP tool names its file ``filePath``.
    use(BOB, GROUP)
    block = hook("LSP", {"operation": "hover", "filePath": str(old / "src" / "main.py"),
                         "line": 1, "character": 1}, proj)
    assert _reason(block) == READ_OUT("LSP")


@pytest.mark.parametrize("tool,extra", [("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*.py"})])
def test_search_home_outside_root_denied(hook, use, proj, old, tool, extra):
    use(BOB, GROUP)
    assert hook(tool, {**extra, "path": str(old)}, proj) is not None


@pytest.mark.parametrize("role", list(RESTRICTED))
@pytest.mark.parametrize("tool,extra", [("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*.py"})])
def test_search_in_old_folder_says_root(hook, use, old, role, tool, extra):
    use(RESTRICTED[role], GROUP)
    assert _reason(hook(tool, dict(extra), old)) == ROOT_CWD_SEARCH(tool)


@pytest.mark.parametrize("tool,extra", [("Grep", {"pattern": "print"}), ("Glob", {"pattern": "**/*.py"})])
def test_search_inside_the_session_folder_allowed(hook, use, proj, tool, extra):
    use(BOB, GROUP)
    assert hook(tool, dict(extra), proj) is None


def test_read_sibling_folder_inside_root_allowed(hook, use, proj, team_root):
    (team_root / "other").mkdir()
    (team_root / "other" / "a.txt").write_text("x\n")
    use(BOB, GROUP)
    assert _read(hook, team_root / "other" / "a.txt", proj) is None


def test_read_in_the_session_folder_allowed(hook, use, proj):
    use(BOB, GROUP)
    assert _read(hook, "src/app.py", proj) is None


def test_read_the_root_folder_itself_allowed(hook, use, proj, team_root):
    (team_root / "README.md").write_text("x\n")
    use(BOB, GROUP)
    assert _read(hook, team_root / "README.md", proj) is None


@pytest.mark.parametrize("path", ["/etc/hostname", "/usr/share/doc/README", "/opt/x/y"])
def test_system_path_readable(hook, use, proj, path):
    use(BOB, GROUP)
    assert _read(hook, path, proj) is None


def test_system_path_readable_from_old_folder(hook, use, old):
    use(BOB, GROUP)
    assert _read(hook, "/etc/hostname", old) is None


def test_protected_path_still_denied(hook, use, proj, home):
    use(BOB, GROUP)
    assert _reason(_read(hook, home / ".ssh" / "id_ed25519", proj)) == \
        "Read on protected path ~/.ssh/**"


def test_scratchpad_under_home_readable(hook, use, proj, home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(home / "ctmp"))
    use(BOB, GROUP)
    scratch = enforce._scratch_root(str(proj), SID)
    assert _read(hook, f"{scratch}/scratchpad/notes.md", proj) is None


def test_scratchpad_under_home_writable(hook, use, proj, home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(home / "ctmp"))
    use(BOB, GROUP)
    scratch = enforce._scratch_root(str(proj), SID)
    assert _write(hook, f"{scratch}/scratchpad/notes.md", proj) is None


def test_reply_context_readable(hook, use, proj, home, monkeypatch):
    rc = home / "rc" / f"claude-reply-{SESSION}.json"
    rc.parent.mkdir()
    rc.write_text("{}")
    monkeypatch.setattr(enforce, "reply_context_path", lambda name: rc)
    monkeypatch.setattr(ps, "reply_context_path", lambda name: rc)
    use(BOB, GROUP)
    assert _read(hook, rc, proj) is None


def test_symlink_inside_root_to_home_file_denied(hook, use, proj, home):
    (home / "secret.txt").write_text("s\n")
    os.symlink(home / "secret.txt", proj / "link.txt")
    use(BOB, GROUP)
    assert _read(hook, proj / "link.txt", proj) is not None


def test_symlinked_folder_inside_root_to_home_denied(hook, use, proj, old):
    os.symlink(old, proj / "mirror")
    use(BOB, GROUP)
    assert _read(hook, proj / "mirror" / "src" / "main.py", proj) is not None


def test_bash_stays_denied_to_user(hook, use, proj):
    use(BOB, GROUP)
    assert hook("Bash", {"command": "ls"}, proj) is not None


# ---- other scopes' roots ------------------------------------------------------------------

@pytest.fixture
def far_root(tmp_path):
    return tmp_path / "srv" / "other"


@pytest.fixture
def with_far(scopes, far_root):
    return scopes + [Scope(chat_id=50, kind="dm", label="zed DM",
                           members=(Member(id=50, label="zed", role="user"),),
                           root=str(far_root))]


def test_other_root_outside_home_denied(hook, use, proj, far_root, with_far):
    use(BOB, GROUP, with_far)
    assert _reason(_read(hook, far_root / "a.txt", proj)) == \
        f"Read on protected path {far_root}/**"


def test_other_root_outside_home_denied_by_realpath(hook, use, proj, far_root, with_far,
                                                     tmp_path):
    far_root.mkdir(parents=True)
    alias = tmp_path / "alias"
    os.symlink(far_root, alias)
    use(BOB, GROUP, with_far)
    assert _read(hook, alias / "a.txt", proj) is not None


def test_other_root_does_not_cover_a_prefix_sibling(hook, use, proj, far_root, with_far):
    use(BOB, GROUP, with_far)
    assert _read(hook, str(far_root) + "2/a.txt", proj) is None


def test_own_root_is_not_in_the_deny_list(hook, use, proj, with_far, team_root):
    use(BOB, GROUP, with_far)
    (team_root / "x.md").write_text("x\n")
    assert _read(hook, team_root / "x.md", proj) is None


# ---- owner / admin, and the custom roles -------------------------------------------------

@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_owner_admin_unrestricted_read(hook, use, proj, old, who):
    use(who, GROUP)
    assert _read(hook, old / "src" / "main.py", proj) is None


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_owner_admin_unrestricted_write(hook, use, old, who):
    use(who, GROUP)
    assert _write(hook, old / "src" / "main.py", old) is None


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_owner_admin_unrestricted_search(hook, use, old, who):
    use(who, GROUP)
    assert hook("Grep", {"pattern": "x"}, old) is None


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_owner_admin_read_another_scopes_root(hook, use, proj, far_root, with_far, who):
    use(who, GROUP, with_far)
    assert _read(hook, far_root / "a.txt", proj) is None


def test_helper_custom_role_held_to_root(hook, use, proj, old):
    use(HAL, GROUP)
    assert _read(hook, old / "src" / "main.py", proj) is not None


def test_custom_bypass_role_without_manage_held_to_root(hook, use, proj, old):
    use(BOSS, GROUP)
    assert _write(hook, old / "src" / "main.py", proj) is not None


def test_custom_bypass_role_without_manage_reads_held_to_root(hook, use, proj, old):
    use(BOSS, GROUP)
    assert _read(hook, old / "src" / "main.py", proj) is not None


def test_custom_bypass_role_without_manage_writes_anywhere_in_root(hook, use, proj, team_root):
    use(BOSS, GROUP)
    assert _write(hook, team_root / "other" / "a.txt", proj) is None


# ---- the "session folder only" bound (two different roots merged) ----------------------

@pytest.fixture
def sentinel(snap_for):
    snap = {**snap_for(BOB, GROUP), "root": safety.ROOT_SENTINEL}
    ps.write_merged_snapshot(SESSION, snap)


@pytest.fixture
def cwd_any(home):
    p = home / "work" / "p"
    (p / "src").mkdir(parents=True)
    return p


def test_sentinel_reads_in_the_session_folder(hook, sentinel, cwd_any):
    assert _read(hook, cwd_any / "src" / "a.py", cwd_any) is None


def test_sentinel_denies_reads_elsewhere_under_home(hook, sentinel, cwd_any, old):
    assert _read(hook, old / "src" / "main.py", cwd_any) is not None


def test_sentinel_writes_in_the_session_folder(hook, sentinel, cwd_any):
    assert _write(hook, cwd_any / "src" / "a.py", cwd_any) is None


def test_sentinel_keeps_system_paths_readable(hook, sentinel, cwd_any):
    assert _read(hook, "/etc/hostname", cwd_any) is None


def test_sentinel_in_the_home_folder_reads_nothing_there(hook, sentinel, home):
    assert _read(hook, home / "notes.txt", home) is not None


# ---- a root-less snapshot (today's behaviour) ---------------------------------------------

def test_a_snapshot_without_root_reads_home_as_today(hook, proj, old, pol):
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(pol.get_role("user"), None, None))
    assert _read(hook, old / "src" / "main.py", proj) is None


def test_a_root_not_yet_created_still_bounds_reads(hook, use, scopes, home):
    """Sara's default root does not exist yet; her turn is still held."""
    cwd = home / "somewhere"
    cwd.mkdir()
    use(kit.SARA, kit.SARA)
    assert _read(hook, home / "notes.txt", cwd) is not None


def test_a_symlinked_root_bounds_by_its_real_folder(hook, use, scopes, home, pol):
    from dataclasses import replace
    real = home / "real-team"
    (real / "p").mkdir(parents=True)
    (real / "q").mkdir()
    os.symlink(real, home / "team-link")
    sl = [replace(s, root=str(home / "team-link")) if s.chat_id == GROUP else s for s in scopes]
    use(BOB, GROUP, sl)
    assert _read(hook, real / "q" / "a.txt", real / "p") is None
