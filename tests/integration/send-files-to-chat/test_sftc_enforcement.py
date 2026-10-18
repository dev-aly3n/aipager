"""The PreToolUse check of a SendUserFile call, through enforce.decide /
fail_closed / soft_deny_json (design.md success criteria 1-6)."""

from __future__ import annotations

import json
import os

import pytest

from aipager import send_check
from aipager.dtach import enforce

import sys

kit = sys.modules["_sftc_kit"]
FLOOR_WHY, HEAD, TAIL, TOOL, reason_for = (
    kit.FLOOR_WHY, kit.HEAD, kit.TAIL, kit.TOOL, kit.reason_for)

ROLES = ["owner", "admin", "user", "terminal"]

# A spelled floor path per family of design.md criterion 1 (relative to ~).
FLOOR_FILES = {
    "claude-credentials": ".claude/.credentials.json",
    "claude-json": ".claude.json",
    "claude-json-backup": ".claude.json.backup",
    "aipager-config": ".config/aipager/aipager.yaml",
    "aipager-daemon-env": ".config/aipager/daemon.env",
    "aipager-share": ".local/share/aipager/install.json",
    "aipager-state": ".local/state/aipager/languages.json",
    "ssh-config": ".ssh/config",
    "aws": ".aws/config",
    "netrc": ".netrc",
}


def _cwd(kind, proj, root):
    return root / "proj" if kind in ("user", "terminal") else proj


def _make(home, rel):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("secret-ish")
    return p


# ---- criterion 1: the floor holds for every role ----------------------------

@pytest.mark.parametrize("fam", list(FLOOR_FILES), ids=list(FLOOR_FILES))
@pytest.mark.parametrize("kind", ROLES)
def test_floor_file_is_refused_softly_for_every_role(decide, home, proj, root,
                                                     kind, fam):
    p = _make(home, FLOOR_FILES[fam])
    out = decide(kind, TOOL, {"files": [str(p)]}, _cwd(kind, proj, root),
                 root_dir=root)
    assert out == {"tool": TOOL, "reason": reason_for((str(p), FLOOR_WHY)),
                   "soft": True}


@pytest.mark.parametrize("path", ["/proc/self/environ", "/sys/kernel/notes",
                                  "/dev/null"], ids=["proc", "sys", "dev"])
@pytest.mark.parametrize("kind", ROLES)
def test_kernel_folders_are_refused_for_every_role(decide, proj, root, kind, path):
    out = decide(kind, TOOL, {"files": [path]}, _cwd(kind, proj, root),
                 root_dir=root)
    assert out["reason"] == reason_for((path, FLOOR_WHY))


@pytest.mark.parametrize("name", [".env", "server.pem", "id_ed25519"])
@pytest.mark.parametrize("kind", ["owner", "terminal"])
def test_secret_shaped_name_is_refused_for_owner_and_terminal(decide, proj, root,
                                                              kind, name):
    folder = _cwd(kind, proj, root)
    (folder / name).write_text("TOKEN=1")
    out = decide(kind, TOOL, {"files": [str(folder / name)]}, folder,
                 root_dir=root)
    assert out["reason"] == reason_for((str(folder / name), FLOOR_WHY))


def test_tilde_spelled_floor_path_is_refused_for_owner(decide, home, proj):
    _make(home, ".ssh/known_hosts")
    out = decide("owner", TOOL, {"files": ["~/.ssh/known_hosts"]}, proj)
    assert out["reason"] == reason_for(("~/.ssh/known_hosts", FLOOR_WHY))


def test_relative_spelling_into_the_floor_is_refused(decide, home, proj):
    _make(home, ".claude/.credentials.json")
    rel = "../.claude/.credentials.json"
    out = decide("owner", TOOL, {"files": [rel]}, proj)
    assert out["reason"] == reason_for((rel, FLOOR_WHY))


def test_symlink_named_like_a_photo_to_a_floor_file_is_refused(decide, home, proj):
    target = _make(home, ".claude/.credentials.json")
    (proj / "holiday.png").symlink_to(target)
    out = decide("owner", TOOL, {"files": [str(proj / "holiday.png")]}, proj)
    assert out["reason"] == reason_for((str(proj / "holiday.png"), FLOOR_WHY))


def test_one_floor_file_among_good_ones_refuses_the_call(decide, home, proj):
    bad = _make(home, ".ssh/config")
    out = decide("owner", TOOL, {"files": [str(proj / "report.pdf"), str(bad)]},
                 proj)
    assert out["reason"] == reason_for((str(bad), FLOOR_WHY))


def test_soft_deny_payload_has_no_safety_policy_marker(decide, home, proj):
    p = _make(home, ".claude.json")
    out = decide("owner", TOOL, {"files": [str(p)]}, proj)
    assert "aipager safety policy" not in enforce.soft_deny_json(out["reason"])


def test_soft_deny_payload_shape(decide, home, proj):
    p = _make(home, ".claude.json")
    out = decide("owner", TOOL, {"files": [str(p)]}, proj)
    assert json.loads(enforce.soft_deny_json(out["reason"])) == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse",
                               "permissionDecision": "deny",
                               "permissionDecisionReason": out["reason"]}}


# ---- criterion 4: owner, admin and terminal may send any other file ----------

@pytest.mark.parametrize("kind", ["owner", "admin", "terminal"])
def test_readable_file_under_home_is_allowed(decide, proj, root, kind, home):
    f = home / "pics" / "cat.png"
    f.parent.mkdir()
    f.write_bytes(b"cat")
    assert decide(kind, TOOL, {"files": [str(f)]}, proj, root_dir=root) is None


@pytest.mark.parametrize("kind", ["owner", "admin", "terminal"])
def test_readable_file_outside_home_is_allowed(decide, proj, root, outside, kind):
    assert decide(kind, TOOL, {"files": [str(outside / "x.png")]}, proj,
                  root_dir=root) is None


def test_owner_may_send_several_files_in_one_call(decide, proj, outside):
    assert decide("owner", TOOL, {"files": [str(proj / "report.pdf"),
                                            str(outside / "x.png")]},
                  proj) is None


def test_files_as_a_single_string_is_allowed_for_owner(decide, proj):
    assert decide("owner", TOOL, {"files": str(proj / "report.pdf")}, proj) is None


def test_files_as_a_single_floor_string_is_refused(decide, home, proj):
    p = _make(home, ".ssh/config")
    out = decide("owner", TOOL, {"files": str(p)}, proj)
    assert out["reason"] == reason_for((str(p), FLOOR_WHY))


def test_relative_path_resolves_against_the_payload_cwd(decide, proj):
    assert decide("owner", TOOL, {"files": ["report.pdf"]}, proj) is None


def test_relative_path_missing_in_the_payload_cwd_is_refused(decide, proj, outside):
    out = decide("owner", TOOL, {"files": ["report.pdf"]}, outside)
    assert out["reason"] == reason_for(("report.pdf", "no such file"))


# ---- criterion 3: a member only inside her root ------------------------------

def test_member_may_send_a_file_inside_her_root(decide, root):
    assert decide("user", TOOL, {"files": [str(root / "proj" / "shot.png")]},
                  root / "proj", root_dir=root) is None


def test_member_may_send_from_a_sibling_folder_inside_her_root(decide, root):
    assert decide("user", TOOL, {"files": [str(root / "other" / "notes.md")]},
                  root / "proj", root_dir=root) is None


def test_member_may_send_a_relative_path_inside_her_root(decide, root):
    assert decide("user", TOOL, {"files": ["shot.png"]}, root / "proj",
                  root_dir=root) is None


def test_member_cannot_send_a_home_file_outside_her_root(decide, home, root):
    f = home / "pics" / "cat.png"
    f.parent.mkdir()
    f.write_bytes(b"cat")
    out = decide("user", TOOL, {"files": [str(f)]}, root / "proj", root_dir=root)
    assert out["soft"] is True and out["reason"].startswith(HEAD) \
        and out["reason"].endswith(TAIL)


def test_member_cannot_send_a_file_outside_home(decide, root, outside):
    p = str(outside / "x.png")
    out = decide("user", TOOL, {"files": [p]}, root / "proj", root_dir=root)
    assert out["reason"] == reason_for(
        (p, "outside this person's own folder; they may send only files inside it"))


def test_member_cannot_send_etc_hosts(decide, root):
    if not os.path.isfile("/etc/hosts"):
        pytest.skip("no /etc/hosts here")
    out = decide("user", TOOL, {"files": ["/etc/hosts"]}, root / "proj",
                 root_dir=root)
    assert out["reason"] == reason_for(
        ("/etc/hosts",
         "outside this person's own folder; they may send only files inside it"))


def test_member_cannot_send_through_a_symlink_inside_her_root(decide, root, outside):
    link = root / "proj" / "pic.png"
    link.symlink_to(outside / "x.png")
    out = decide("user", TOOL, {"files": [str(link)]}, root / "proj", root_dir=root)
    assert out["soft"] is True


def test_member_cannot_send_from_a_folder_that_only_shares_a_prefix(decide, home, root):
    twin = home / "aipager-users" / "carol2"
    twin.mkdir()
    (twin / "a.png").write_bytes(b"x")
    out = decide("user", TOOL, {"files": [str(twin / "a.png")]}, root / "proj",
                 root_dir=root)
    assert out["soft"] is True


def test_member_floor_file_inside_her_root_is_still_refused(decide, root):
    (root / "proj" / ".env").write_text("TOKEN=1")
    out = decide("user", TOOL, {"files": [str(root / "proj" / ".env")]},
                 root / "proj", root_dir=root)
    assert out["reason"] == reason_for((str(root / "proj" / ".env"), FLOOR_WHY))


# ---- criterion 2: a refusal never halts the turn -----------------------------

def _refused_result(reason):
    return {"type": "user", "message": {"content": [{
        "type": "tool_result", "tool_use_id": "toolu_01", "is_error": True,
        "content": reason}]}}


@pytest.mark.parametrize("kind", ["owner", "user"])
def test_next_read_after_a_refused_send_is_allowed(decide, home, proj, root, kind):
    p = _make(home, ".ssh/config")
    cwd = _cwd(kind, proj, root)
    first = decide(kind, TOOL, {"files": [str(p)]}, cwd, root_dir=root)
    target = root / "proj" / "shot.png" if kind == "user" else proj / "report.pdf"
    assert decide(kind, "Read", {"file_path": str(target)}, cwd, root_dir=root,
                  prior=[_refused_result(first["reason"])]) is None


@pytest.mark.parametrize("kind", ["owner", "user"])
def test_next_send_after_a_refused_send_is_allowed(decide, home, proj, root, kind):
    p = _make(home, ".ssh/config")
    cwd = _cwd(kind, proj, root)
    first = decide(kind, TOOL, {"files": [str(p)]}, cwd, root_dir=root)
    target = root / "proj" / "shot.png" if kind == "user" else proj / "report.pdf"
    assert decide(kind, TOOL, {"files": [str(target)]}, cwd, root_dir=root,
                  prior=[_refused_result(first["reason"])]) is None


def test_send_in_an_already_halted_turn_stays_a_hard_deny(decide, root):
    halted = _refused_result("aipager safety policy: Write outside the root")
    out = decide("user", TOOL, {"files": [str(root / "proj" / "shot.png")]},
                 root / "proj", root_dir=root, prior=[halted])
    assert out is not None and "soft" not in out \
        and out["reason"].startswith("session halted")


# ---- criterion 5/6: file checks and failing closed --------------------------

def test_malformed_file_list_is_refused_softly(decide, proj):
    out = decide("owner", TOOL, {"files": [42]}, proj)
    assert out == {"tool": TOOL, "soft": True, "reason": (
        "SendUserFile with no readable file list, nothing was sent. "
        "Tell the person the files could not be sent.")}


def test_missing_files_key_is_refused_softly(decide, proj):
    out = decide("owner", TOOL, {"caption": "hi"}, proj)
    assert out["soft"] is True


def test_send_check_error_denies_even_for_the_owner(decide, proj, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(send_check, "check_files", boom)
    out = decide("owner", TOOL, {"files": [str(proj / "report.pdf")]}, proj)
    assert out == {"tool": TOOL, "reason": send_check.SEND_UNCHECKED_REASON,
                   "soft": True}


def test_fail_closed_denies_a_send_for_the_owner(decide, tmp_path, proj):
    SESSION, SID, use_role = kit.SESSION, kit.SID, kit.use_role
    use_role("owner")
    t = tmp_path / "t2.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @alice · role:owner]\nsend"}}) + "\n")
    out = enforce.fail_closed({
        "hook_event_name": "PreToolUse", "session": SESSION, "session_id": SID,
        "cwd": str(proj), "tool_name": TOOL,
        "tool_input": {"files": [str(proj / "report.pdf")]},
        "transcript_path": str(t)})
    assert out == {"tool": TOOL, "reason": send_check.SEND_UNCHECKED_REASON,
                   "soft": True}


def test_unchecked_reason_text():
    assert send_check.SEND_UNCHECKED_REASON == (
        "SendUserFile could not be checked, so nothing was sent. "
        "Tell the person the files could not be sent.")


def test_other_tools_are_not_touched_by_the_send_floor(decide, proj):
    # A non-send tool for the owner keeps today's behaviour (allowed).
    assert decide("owner", "Read", {"file_path": str(proj / "report.pdf")},
                  proj) is None
