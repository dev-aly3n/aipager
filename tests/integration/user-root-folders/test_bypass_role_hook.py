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


# ==== iteration 3 ====================================================================
# (a) a background agent pinned to a root-bound bypass_safety role without
#     can_manage stays held to the root after an owner's message becomes the
#     session snapshot, on the decision path and on the fail-closed path;
# (b) such a role in a session whose folder is outside its root writes and
#     searches only in its scratchpad, with the documented root_cwd reasons;
# (c) once such a turn is denied, the rest of the turn is halted; owner and
#     terminal-typed turns are never halted.
# Controls: the owner, and a bypass_safety role WITH can_manage ("mgr").

import json  # noqa: E402

from aipager.dtach import enforce  # noqa: E402

MGR = 12
AGENT = "agent-bg-1"


def _owner_now(snap_for):
    """An owner's Telegram message becomes the session snapshot."""
    ps.write_merged_snapshot(SESSION, {**snap_for(kit.OWNER, GROUP),
                                       "turn_origin": "telegram"})


def _pin_of(snap_for, uid):
    ps.write_pin(SESSION, AGENT, {"rules": ps.safety_rules(snap_for(uid, GROUP))})


# ---- (a) pinned background agents ---------------------------------------------------

def test_iter3_pinned_agent_read_outside_root_denied_after_owner_snapshot(
        hook, snap_for, proj, old):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(old / "src" / "main.py")}, proj,
                agent_id=AGENT) is not None


def test_iter3_pinned_agent_write_outside_root_denied_after_owner_snapshot(
        hook, snap_for, proj, old):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook("Write", {"file_path": str(old / "y.py"), "content": "x"}, proj,
                agent_id=AGENT) is not None


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_iter3_pinned_agent_search_outside_root_denied_after_owner_snapshot(
        hook, snap_for, proj, old, tool):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook(tool, {"pattern": "*" if tool == "Glob" else "x", "path": str(old)},
                proj, agent_id=AGENT) is not None


def test_iter3_pinned_agent_full_bypass_role_held_after_owner_snapshot(
        hook, snap_for, proj, old):
    _pin_of(snap_for, FULL)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(old / "src" / "main.py")}, proj,
                agent_id=AGENT) is not None


def test_iter3_pinned_agent_still_reads_inside_root(hook, snap_for, proj):
    (proj / "src" / "a.py").write_text("a\n")
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(proj / "src" / "a.py")}, proj,
                agent_id=AGENT) is None


def test_iter3_pinned_agent_still_writes_in_its_session_folder(hook, snap_for, proj):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook("Write", {"file_path": str(proj / "src" / "n.py"), "content": "x"},
                proj, agent_id=AGENT) is None


def test_iter3_control_owner_pinned_agent_reads_outside_root(hook, snap_for, proj, old):
    _pin_of(snap_for, kit.OWNER)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(old / "src" / "main.py")}, proj,
                agent_id=AGENT) is None


def test_iter3_control_manager_bypass_pinned_agent_reads_outside_root(
        hook, snap_for, proj, old):
    _pin_of(snap_for, MGR)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(old / "src" / "main.py")}, proj,
                agent_id=AGENT) is None


def test_iter3_control_owners_own_call_not_held_by_a_bypass_members_pin(
        hook, snap_for, proj, old):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert hook("Read", {"file_path": str(old / "src" / "main.py")}, proj) is None


def _closed(tool="Read", agent=AGENT):
    data = {"session": SESSION, "tool_name": tool}
    if agent:
        data["agent_id"] = agent
    return enforce.fail_closed(data)


@pytest.mark.parametrize("who", list(BYPASSERS))
def test_iter3_fail_closed_denies_a_bypass_members_pinned_agent(snap_for, who):
    _pin_of(snap_for, BYPASSERS[who])
    _owner_now(snap_for)
    assert _closed() is not None


def test_iter3_fail_closed_control_owner_pinned_agent_allowed(snap_for):
    _pin_of(snap_for, kit.OWNER)
    _owner_now(snap_for)
    assert _closed() is None


def test_iter3_fail_closed_control_manager_bypass_pinned_agent_allowed(snap_for):
    _pin_of(snap_for, MGR)
    _owner_now(snap_for)
    assert _closed() is None


def test_iter3_fail_closed_control_owners_own_call_allowed(snap_for):
    _pin_of(snap_for, ROOTISH)
    _owner_now(snap_for)
    assert _closed(agent=None) is None


# ---- (b) session folder outside the root -----------------------------------------------

@pytest.fixture
def scratch(old):
    return enforce._scratch_root(str(old), kit.CLAUDE_SID) + "/scratchpad"


def test_iter3_outside_session_writes_in_scratchpad(hook, use, old, scratch):
    use(ROOTISH, GROUP)
    assert _write(hook, f"{scratch}/notes.md", old) is None


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_iter3_outside_session_searches_in_scratchpad(hook, use, old, scratch, tool):
    use(ROOTISH, GROUP)
    assert _read(hook, scratch, old, tool) is None


def test_iter3_outside_session_edit_gives_root_reason(hook, use, old):
    use(ROOTISH, GROUP)
    assert (_write(hook, old / "src" / "main.py", old, "Edit") or {}).get("reason") == (
        "Edit denied - this session runs outside this role's own folder, "
        "so this role can only write in its scratchpad")


def test_iter3_outside_session_write_into_root_denied(hook, use, old, proj):
    """Only the scratchpad: even a folder inside its own root is refused
    while the session runs outside it."""
    use(ROOTISH, GROUP)
    assert _write(hook, proj / "src" / "n.py", old) is not None


def test_iter3_outside_session_write_into_root_gives_root_reason(hook, use, old, proj):
    use(ROOTISH, GROUP)
    assert (_write(hook, proj / "src" / "n.py", old) or {}).get("reason") == WRITE_ROOT_CWD


def test_iter3_outside_session_glob_of_root_denied(hook, use, old, proj):
    use(ROOTISH, GROUP)
    assert _read(hook, proj, old, "Glob") is not None


def test_iter3_outside_session_user_gets_the_same_reason(hook, use, old):
    """The built-in user, for comparison: the same sentence."""
    use(kit.BOB, GROUP)
    assert (_write(hook, old / "src" / "main.py", old) or {}).get("reason") == WRITE_ROOT_CWD


def test_iter3_control_owner_writes_in_outside_session_folder(hook, use, old):
    use(kit.OWNER, GROUP)
    assert _write(hook, old / "src" / "main.py", old) is None


def test_iter3_control_manager_bypass_writes_in_outside_session_folder(hook, use, old):
    use(MGR, GROUP)
    assert _write(hook, old / "src" / "main.py", old) is None


# ---- (c) a denied turn stays halted --------------------------------------------------------

def _blocked_turn(tmp_path, *, marker=True, fresh_prompt=False,
                  reason=READ_OUT, name="blocked.jsonl"):
    """A turn whose earlier tool call the hook denied (its tool_result
    carries the aipager safety policy text). ``fresh_prompt``: a new
    message follows, so the block belongs to the previous turn."""
    head = "[via Telegram · @rooty · role:rootish]\n" if marker else ""
    lines = [
        {"type": "user", "message": {"content": head + "look at the old repo"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": f"aipager safety policy: {reason}"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "denied, trying something else"}]}},
    ]
    if fresh_prompt:
        lines.append({"type": "user", "message": {"content": head + "now this"}})
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


@pytest.fixture
def inside(proj):
    f = proj / "src" / "ok.py"
    f.write_text("ok\n")
    return f


def test_iter3_bypass_turn_halted_after_a_deny(hook, use, proj, inside, tmp_path):
    use(ROOTISH, GROUP)
    assert hook("Read", {"file_path": str(inside)}, proj,
                transcript_path=_blocked_turn(tmp_path)) is not None


def test_iter3_bypass_turn_halt_reason_says_halted(hook, use, proj, inside, tmp_path):
    use(ROOTISH, GROUP)
    block = hook("Read", {"file_path": str(inside)}, proj,
                 transcript_path=_blocked_turn(tmp_path)) or {}
    assert "session halted" in str(block.get("reason"))


def test_iter3_bypass_turn_write_in_session_folder_halted(hook, use, proj, tmp_path):
    use(ROOTISH, GROUP)
    assert _write_t(hook, proj / "src" / "n.py", proj, _blocked_turn(tmp_path)) is not None


def test_iter3_bypass_turn_halted_after_a_write_root_deny(hook, use, proj, inside, tmp_path):
    use(ROOTISH, GROUP)
    t = _blocked_turn(tmp_path, reason=WRITE_ROOT_CWD)
    assert hook("Read", {"file_path": str(inside)}, proj, transcript_path=t) is not None


def test_iter3_full_bypass_turn_halted_after_a_deny(hook, use, proj, inside, tmp_path):
    use(FULL, GROUP)
    assert hook("Read", {"file_path": str(inside)}, proj,
                transcript_path=_blocked_turn(tmp_path)) is not None


def test_iter3_control_bypass_turn_not_halted_after_a_fresh_prompt(
        hook, use, proj, inside, tmp_path):
    use(ROOTISH, GROUP)
    t = _blocked_turn(tmp_path, fresh_prompt=True)
    assert hook("Read", {"file_path": str(inside)}, proj, transcript_path=t) is None


def test_iter3_control_bypass_turn_without_a_deny_runs(hook, use, proj, inside):
    use(ROOTISH, GROUP)
    assert hook("Read", {"file_path": str(inside)}, proj) is None


def test_iter3_owner_turn_never_halted(hook, snap_for, proj, inside, tmp_path):
    _owner_now(snap_for)
    t = _blocked_turn(tmp_path)
    assert hook("Read", {"file_path": str(inside)}, proj, transcript_path=t) is None


def test_iter3_manager_bypass_turn_never_halted(hook, use, proj, inside, tmp_path):
    use(MGR, GROUP)
    t = _blocked_turn(tmp_path)
    assert hook("Read", {"file_path": str(inside)}, proj, transcript_path=t) is None


def test_iter3_terminal_typed_turn_never_halted(hook, use, proj, inside, tmp_path):
    """A turn typed in the terminal is the operator's: unrestricted, even
    with a bypass member's snapshot left over and a deny earlier on."""
    use(ROOTISH, GROUP)
    t = _blocked_turn(tmp_path, marker=False)
    assert hook("Read", {"file_path": str(inside)}, proj, transcript_path=t) is None


def _write_t(hook, path, cwd, transcript):
    return hook("Write", {"file_path": str(path), "content": "x"}, cwd,
                transcript_path=transcript)
