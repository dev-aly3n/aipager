"""Iteration 2: a custom role with ``bypass_safety`` but without
``can_manage`` is held to its root by the hook, like every role without
can_manage (spec.md decision 1 and 5; entrypoints.md "Hook"; review-1
rev-iter1-001).

The snapshot is built as entrypoints.md documents and judged by
``enforce.decide`` on a Telegram-marked turn."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from aipager import policy
from aipager import policy_snapshot as ps
from aipager.scope import Member

kit = sys.modules["_userroot_kit"]
GROUP, SESSION = kit.GROUP, kit.SESSION
ROOTISH, FULL = 10, 11

READ_OUT = ("Read denied - this role reads in the home folder only inside its own "
            "folder")
WRITE_ROOT_CWD = ("Write denied - this session runs outside this role's own folder, "
                  "so this role can only write in its scratchpad")


@pytest.fixture
def pol(tmp_path):
    d = tmp_path / "pol2"
    d.mkdir(exist_ok=True)
    f = d / "policy.yaml"
    f.write_text(
        "roles:\n"
        "  rootish:\n"
        "    can_prompt: true\n"
        "    can_manage: false\n"
        "    bypass_safety: true\n"
        "  fullish:\n"
        "    can_prompt: true\n"
        "    can_manage: false\n"
        "    bypass_safety: true\n"
        "    bypass_role_denies: true\n"
        "  managerish:\n"
        "    can_prompt: true\n"
        "    can_manage: true\n"
        "    bypass_safety: true\n"
    )
    return policy.load_policy(f, d / "none.d")


@pytest.fixture
def scopes(scopes):
    out = []
    for s in scopes:
        if s.chat_id == GROUP:
            s = replace(s, members=s.members + (
                Member(id=ROOTISH, label="rooty", role="rootish"),
                Member(id=FULL, label="fully", role="fullish"),
                Member(id=12, label="mgr", role="managerish"),
            ))
        out.append(s)
    return out


@pytest.fixture
def proj(team_root):
    p = team_root / "proj"
    (p / "src").mkdir(parents=True)
    return p


@pytest.fixture
def old(home):
    p = home / "aipager"
    (p / "src").mkdir(parents=True)
    (p / "src" / "main.py").write_text("x = 1\n")
    return p


def _read(hook, path, cwd, tool="Read"):
    if tool == "Read":
        return hook(tool, {"file_path": str(path)}, cwd)
    return hook(tool, {"pattern": "*" if tool == "Glob" else "x", "path": str(path)}, cwd)


def _write(hook, path, cwd, tool="Write"):
    inp = {"file_path": str(path), "content": "x"}
    if tool == "Edit":
        inp = {"file_path": str(path), "old_string": "a", "new_string": "b"}
    return hook(tool, inp, cwd)


BYPASSERS = {"bypass_safety": ROOTISH, "bypass_safety+role_denies": FULL}


def test_the_role_really_lacks_can_manage(pol):
    assert pol.get_role("rootish").can_manage is False


def test_snapshot_of_a_bypass_role_without_manage_carries_root(snap_for):
    assert "root" in snap_for(ROOTISH, GROUP)


@pytest.mark.parametrize("who", list(BYPASSERS))
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_bypass_role_write_outside_root_denied(hook, use, proj, old, who, tool):
    use(BYPASSERS[who], GROUP)
    assert _write(hook, old / "src" / "main.py", proj, tool) is not None


@pytest.mark.parametrize("who", list(BYPASSERS))
def test_bypass_role_read_under_home_outside_root_denied(hook, use, proj, old, who):
    use(BYPASSERS[who], GROUP)
    assert _read(hook, old / "src" / "main.py", proj) is not None


def test_bypass_role_read_outside_root_gives_the_root_reason(hook, use, proj, old):
    use(ROOTISH, GROUP)
    assert (_read(hook, old / "src" / "main.py", proj) or {}).get("reason") == READ_OUT


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_bypass_role_search_outside_root_denied(hook, use, proj, old, tool):
    use(ROOTISH, GROUP)
    assert _read(hook, old, proj, tool) is not None


def test_bypass_role_old_session_folder_write_gives_root_reason(hook, use, old):
    use(ROOTISH, GROUP)
    assert (_write(hook, old / "src" / "main.py", old) or {}).get("reason") == WRITE_ROOT_CWD


SEARCH_ROOT_CWD = ("{} denied - this session runs outside this role's own folder, "
                   "so this role can only search in its scratchpad")


@pytest.mark.parametrize("who", list(BYPASSERS))
def test_bypass_role_old_session_folder_write_is_denied(hook, use, old, who):
    use(BYPASSERS[who], GROUP)
    assert _write(hook, old / "src" / "main.py", old) is not None


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_bypass_role_old_session_folder_search_gives_root_reason(hook, use, old, tool):
    use(ROOTISH, GROUP)
    assert (_read(hook, old, old, tool) or {}).get("reason") == SEARCH_ROOT_CWD.format(tool)


def test_bypass_role_reading_ssh_key_outside_root_denied(hook, use, proj, home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("k\n")
    use(ROOTISH, GROUP)
    assert _read(hook, home / ".ssh" / "id_ed25519", proj) is not None


def test_bypass_role_reads_another_folder_inside_root(hook, use, proj, team_root):
    (team_root / "other").mkdir()
    (team_root / "other" / "a.txt").write_text("a\n")
    use(ROOTISH, GROUP)
    assert _read(hook, team_root / "other" / "a.txt", proj) is None


def test_bypass_role_writes_in_its_session_folder(hook, use, proj):
    use(ROOTISH, GROUP)
    assert _write(hook, proj / "src" / "new.py", proj) is None


def test_bypass_role_reads_system_paths(hook, use, proj):
    use(ROOTISH, GROUP)
    assert _read(hook, "/etc/hostname", proj) is None


def test_bypass_role_merged_with_owner_note_keeps_root(hook, snap_for, proj, old):
    """An owner's message and a bypass member's message in one turn: the
    root holds (a root merged with no root keeps the root)."""
    ps.write_merged_snapshot(SESSION, ps.merge_snapshots(
        [snap_for(kit.OWNER, GROUP), snap_for(ROOTISH, GROUP)]))
    assert _read(hook, old / "src" / "main.py", proj) is not None


# ---- the can_manage boundary --------------------------------------------------------

def test_bypass_role_with_manage_has_no_root(snap_for):
    assert "root" not in snap_for(12, GROUP)


def test_bypass_role_with_manage_reads_outside_root(hook, use, proj, old):
    use(12, GROUP)
    assert _read(hook, old / "src" / "main.py", proj) is None
