"""send_check's file checks, root bound, reason writers and the safety
list branch (design.md criteria 1, 3, 5; entrypoints.md send_check and
safety.path_violation / path_targets)."""

from __future__ import annotations

import os
import re
import sys

import pytest

from aipager import safety, send_check

kit = sys.modules["_sftc_kit"]
TOOL, FLOOR_WHY, reason_for = kit.TOOL, kit.FLOOR_WHY, kit.reason_for
MIB = 1024 * 1024


def _codes(refusals):
    return [r.code for r in refusals]


def _check(files, cwd):
    return send_check.check_files({"files": files}, str(cwd))


# ---- limits (entrypoints.md constants) -------------------------------------

@pytest.mark.parametrize("name,value", [
    ("SEND_TOOL", "SendUserFile"), ("FILE_MAX_BYTES", 52428800),
    ("PHOTO_MAX_BYTES", 10485760), ("PHOTO_MAX_SIDE_SUM", 10000),
    ("PHOTO_MAX_RATIO", 20)])
def test_published_limit(name, value):
    assert getattr(send_check, name) == value


# ---- the floor list --------------------------------------------------------

@pytest.mark.parametrize("glob", list(safety.DENY_PATHS_NO_ACCESS)
                         + list(safety.CREDENTIAL_PATHS))
def test_send_floor_contains_every_no_access_and_credential_entry(glob):
    assert glob in send_check.SEND_FLOOR


@pytest.mark.parametrize("glob", ["~/.claude.json*", "/proc/**", "/sys/**", "/dev/**"])
def test_send_floor_contains_the_extra_entries(glob):
    assert glob in send_check.SEND_FLOOR


def _concrete(glob: str, home) -> str:
    p = glob.replace("**", "a/b.txt").replace("*", "x").replace("?", "x")
    if p.startswith("~/"):
        p = str(home) + p[1:]
    return p


@pytest.mark.parametrize("glob", [g for g in send_check.SEND_FLOOR if "[" not in g])
def test_every_floor_glob_refuses_a_matching_path(home, proj, glob):
    # Spelled only: the floor is matched before the file is looked at, so
    # nothing under /tmp or the real home is created.
    p = _concrete(glob, home)
    assert _codes(_check([p], proj)) == ["floor"]


def test_aipager_control_file_under_tmp_is_refused(proj):
    assert _codes(_check(["/tmp/claude-policy-claude-frontend.json"], proj)) == ["floor"]


def test_claude_json_is_on_the_floor(home, proj):
    (home / ".claude.json").write_text("{}")
    assert _codes(_check([str(home / ".claude.json")], proj)) == ["floor"]


# ---- secret-shaped names (equivalence classes + case) ----------------------

SECRET_NAMES = [".env", ".env.local", "server.pem", "private.key", "id_rsa",
                "id_rsa.pub", "id_dsa", "id_ecdsa", "id_ed25519", "cert.p12",
                "cert.pfx", "vault.kdbx", "app.jks", "release.keystore", ".netrc",
                ".pgpass", ".git-credentials", "credentials", "credentials.json",
                "gcp-credentials-prod.json", ".ENV", "SERVER.PEM", "Id_Rsa"]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_secret_shaped_name_is_refused(proj, name):
    (proj / name).write_text("x")
    assert _codes(_check([str(proj / name)], proj)) == ["floor"]


@pytest.mark.parametrize("name", ["environment.md", "keyboard.png", "monkey.txt",
                                  "credentials.txt.png", "report.pdf"])
def test_ordinary_name_is_not_treated_as_a_secret(proj, name):
    (proj / name).write_text("x")
    assert _check([str(proj / name)], proj) == []


def test_symlink_whose_target_has_a_secret_name_is_refused(proj):
    (proj / ".env").write_text("x")
    (proj / "notes.txt").symlink_to(proj / ".env")
    assert _codes(_check([str(proj / "notes.txt")], proj)) == ["floor"]


def test_symlink_to_a_floor_file_is_refused(home, proj):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text("x")
    (proj / "a.png").symlink_to(home / ".ssh" / "config")
    assert _codes(_check([str(proj / "a.png")], proj)) == ["floor"]


def test_symlink_to_an_ordinary_file_is_allowed(proj):
    (proj / "link.pdf").symlink_to(proj / "report.pdf")
    assert _check([str(proj / "link.pdf")], proj) == []


# ---- file checks -----------------------------------------------------------

def test_missing_file_is_refused(proj):
    assert _codes(_check([str(proj / "nope.png")], proj)) == ["missing"]


def test_dangling_symlink_is_refused_as_missing(proj):
    (proj / "dangling.png").symlink_to(proj / "gone.png")
    assert _codes(_check([str(proj / "dangling.png")], proj)) == ["missing"]


def test_directory_is_refused(proj):
    (proj / "pics").mkdir()
    assert _codes(_check([str(proj / "pics")], proj)) == ["not_file"]


def test_fifo_is_refused(proj):
    os.mkfifo(proj / "pipe")
    assert _codes(_check([str(proj / "pipe")], proj)) == ["not_file"]


def test_hard_linked_file_is_refused(proj):
    os.link(proj / "report.pdf", proj / "twin.pdf")
    assert _codes(_check([str(proj / "twin.pdf")], proj)) == ["hard_link"]


def test_original_of_a_hard_linked_file_is_refused_too(proj):
    os.link(proj / "report.pdf", proj / "twin.pdf")
    assert _codes(_check([str(proj / "report.pdf")], proj)) == ["hard_link"]


def test_empty_file_is_refused(proj):
    (proj / "empty.txt").write_bytes(b"")
    assert _codes(_check([str(proj / "empty.txt")], proj)) == ["empty"]


def test_one_byte_file_is_allowed(proj):
    (proj / "one.txt").write_bytes(b"x")
    assert _check([str(proj / "one.txt")], proj) == []


def _sparse(path, size):
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_file_of_exactly_50_mib_is_allowed(proj):
    p = _sparse(proj / "big.bin", 50 * MIB)
    assert _check([str(p)], proj) == []


def test_file_one_byte_over_50_mib_is_refused(proj):
    p = _sparse(proj / "big.bin", 50 * MIB + 1)
    assert _codes(_check([str(p)], proj)) == ["too_big"]


def test_too_big_reason_names_telegram_limit(proj):
    p = _sparse(proj / "big.bin", 50 * MIB + 1)
    assert send_check.refusal_reason(_check([str(p)], proj)) == reason_for(
        (str(p), "larger than Telegram's 50 MB limit"))


def test_tilde_path_is_expanded(home, proj):
    (home / "cat.png").write_bytes(b"x")
    assert _check(["~/cat.png"], proj) == []


def test_relative_path_uses_the_given_cwd(proj):
    assert _check(["report.pdf"], proj) == []


def test_surrounding_whitespace_is_trimmed(proj):
    assert _check(["  " + str(proj / "report.pdf") + "\n"], proj) == []


def test_each_bad_file_gets_its_own_refusal(proj):
    (proj / "empty.txt").write_bytes(b"")
    refusals = _check([str(proj / "nope.png"), str(proj / "report.pdf"),
                       str(proj / "empty.txt")], proj)
    assert _codes(refusals) == ["missing", "empty"]


def test_refusal_keeps_the_path_as_given(proj):
    refusals = _check(["nope.png"], proj)
    assert refusals[0].shown == "nope.png"


def test_string_files_is_checked_like_a_one_item_list(proj):
    assert _codes(send_check.check_files({"files": str(proj / "nope.png")},
                                         str(proj))) == ["missing"]


@pytest.mark.parametrize("files", [[], 42, None, {"a": 1}, [42], [""], ["a\x00b"],
                                   [None]],
                         ids=["empty-list", "int", "none", "dict", "int-entry",
                              "empty-entry", "nul-entry", "none-entry"])
def test_unreadable_file_list_is_refused(proj, files):
    assert _codes(send_check.check_files({"files": files}, str(proj))) == [
        "unreadable_list"]


def test_absent_files_key_is_refused(proj):
    assert _codes(send_check.check_files({}, str(proj))) == ["unreadable_list"]


# ---- the strict root -------------------------------------------------------

def _roots(files, cwd, allowed):
    return _codes(send_check.root_refusals({"files": files}, str(cwd),
                                           None if allowed is None else str(allowed)))


def test_file_inside_root_passes_the_root_check(root):
    assert _roots([str(root / "proj" / "shot.png")], root / "proj", root) == []


def test_relative_file_inside_root_passes(root):
    assert _roots(["../other/notes.md"], root / "proj", root) == []


def test_home_file_outside_root_is_refused(home, root):
    (home / "x.png").write_bytes(b"x")
    assert _roots([str(home / "x.png")], root / "proj", root) == ["outside_root"]


def test_file_outside_home_is_refused_by_the_root(root, outside):
    assert _roots([str(outside / "x.png")], root / "proj", root) == ["outside_root"]


def test_link_inside_root_to_outside_is_refused(root, outside):
    (root / "proj" / "l.png").symlink_to(outside / "x.png")
    assert _roots([str(root / "proj" / "l.png")], root / "proj", root) == [
        "outside_root"]


def test_spelled_path_outside_root_resolving_inside_is_refused(root, outside):
    # spelled AND real path must both be inside
    (outside / "in.png").symlink_to(root / "proj" / "shot.png")
    assert _roots([str(outside / "in.png")], root / "proj", root) == ["outside_root"]


def test_sibling_folder_sharing_a_prefix_is_outside(home, root):
    twin = home / "aipager-users" / "carol2"
    twin.mkdir()
    (twin / "a.png").write_bytes(b"x")
    assert _roots([str(twin / "a.png")], root / "proj", root) == ["outside_root"]


def test_dotdot_escape_from_the_root_is_refused(home, root):
    (home / "x.png").write_bytes(b"x")
    assert _roots([str(root / "proj" / ".." / ".." / ".." / "x.png")],
                  root / "proj", root) == ["outside_root"]


def test_no_allowed_root_refuses_every_entry(root):
    assert _roots([str(root / "proj" / "shot.png"), str(root / "other" / "notes.md")],
                  root / "proj", None) == ["outside_root", "outside_root"]


# ---- reason writers --------------------------------------------------------

WHY = {
    "floor": FLOOR_WHY,
    "missing": "no such file",
    "not_file": "not a regular file",
    "hard_link": "it has more than one hard link",
    "empty": "the file is empty",
}


def test_two_refusals_are_joined_with_a_semicolon(proj):
    (proj / "empty.txt").write_bytes(b"")
    refusals = _check(["nope.png", "empty.txt"], proj)
    assert send_check.refusal_reason(refusals) == reason_for(
        ("nope.png", WHY["missing"]), ("empty.txt", WHY["empty"]))


@pytest.mark.parametrize("code", list(WHY))
def test_each_code_has_its_published_why(proj, home, code):
    if code == "floor":
        name = ".env"
        (proj / name).write_text("x")
    elif code == "missing":
        name = "nope.png"
    elif code == "not_file":
        name = "dir"
        (proj / name).mkdir()
    elif code == "hard_link":
        name = "twin.pdf"
        os.link(proj / "report.pdf", proj / name)
    else:
        name = "empty.txt"
        (proj / name).write_bytes(b"")
    assert send_check.refusal_reason(_check([name], proj)) == reason_for(
        (name, WHY[code]))


def test_outside_root_reason(root, outside):
    p = str(outside / "x.png")
    refusals = send_check.root_refusals({"files": [p]}, str(root / "proj"), str(root))
    assert send_check.refusal_reason(refusals) == reason_for(
        (p, "outside this person's own folder; they may send only files inside it"))


def test_unreadable_list_reason_is_the_whole_sentence(proj):
    assert send_check.refusal_reason(send_check.check_files({"files": 7}, str(proj))) \
        == ("SendUserFile with no readable file list, nothing was sent. "
            "Tell the person the files could not be sent.")


def test_turn_rule_reason_wraps_the_rule():
    assert send_check.turn_rule_reason("SendUserFile on protected path ~/x/**") == (
        "SendUserFile refused, nothing was sent: SendUserFile on protected path "
        "~/x/**. Tell the person which file could not be sent and why.")


def _all_reasons(proj, root, outside):
    out = []
    (proj / ".env").write_text("x")
    (proj / "d").mkdir()
    (proj / "e").write_bytes(b"")
    os.link(proj / "report.pdf", proj / "h")
    _sparse(proj / "b", 50 * MIB + 1)
    for name in [".env", "nope", "d", "e", "h", "b"]:
        out.append(send_check.refusal_reason(_check([name], proj)))
    out.append(send_check.refusal_reason(_check(["nope", "e"], proj)))
    out.append(send_check.refusal_reason(send_check.root_refusals(
        {"files": [str(outside / "x.png")]}, str(root / "proj"), str(root))))
    out.append(send_check.refusal_reason(send_check.check_files({"files": 7}, str(proj))))
    out.append(send_check.turn_rule_reason("SendUserFile with an unreadable path"))
    out.append(send_check.SEND_UNCHECKED_REASON)
    return out


def test_every_reason_matches_exactly_one_shape(proj, root, outside):
    counts = [sum(1 for _c, shape in send_check.SEND_REASON_SHAPES
                  if re.match(shape, r))
              for r in _all_reasons(proj, root, outside)]
    assert counts == [1] * len(counts)


# ---- the busy card row -----------------------------------------------------

@pytest.mark.parametrize("n,row", [(1, "📎 Sending 1 file"), (2, "📎 Sending 2 files"),
                                   (10, "📎 Sending 10 files"),
                                   (11, "📎 Sending 11 files")])
def test_sending_row(n, row):
    assert send_check.sending_row(n) == row


# ---- safety.path_violation / path_targets list branch ----------------------

def _pv(tool_input, cwd, **kw):
    return safety.path_violation(TOOL, tool_input, safety.DENY_PATHS_NO_ACCESS, (),
                                 cwd=str(cwd), **kw)


def test_path_keys_table_has_no_send_entry():
    assert TOOL not in safety._PATH_KEYS


@pytest.mark.parametrize("tool,inp,want", [
    (TOOL, {"files": ["a.png", "b.pdf"]}, ["a.png", "b.pdf"]),
    (TOOL, {"files": "a.png"}, ["a.png"]),
    (TOOL, {"files": 42}, None),
    (TOOL, {}, None),
    ("Read", {"file_path": "x.py"}, ["x.py"]),
    ("Bash", {"command": "ls"}, []),
], ids=["list", "string", "int", "absent", "read", "non-path"])
def test_path_targets(tool, inp, want):
    assert safety.path_targets(tool, inp) == want


def test_send_entry_on_no_access_is_denied(home, proj):
    assert _pv({"files": [str(proj / "report.pdf"), str(home / ".claude" / "x")]},
               proj) == "SendUserFile on protected path ~/.claude/**"


def test_send_single_string_on_no_access_is_denied(home, proj):
    assert _pv({"files": str(home / ".claude" / "x")}, proj) == (
        "SendUserFile on protected path ~/.claude/**")


def test_send_of_ordinary_files_passes_path_violation(proj):
    assert _pv({"files": [str(proj / "report.pdf")]}, proj) is None


@pytest.mark.parametrize("files", [[], 3, ["ok", 3], ["a\x00b"], [""]],
                         ids=["empty", "int", "mixed", "nul", "blank"])
def test_unreadable_send_list_in_path_violation(proj, files):
    assert _pv({"files": files}, proj) == "SendUserFile with an unreadable path"


def test_home_read_root_bound_applies_to_each_entry(home, root):
    (home / "x.png").write_bytes(b"x")
    assert _pv({"files": [str(root / "proj" / "shot.png"), str(home / "x.png")]},
               root / "proj", home_read_roots=(str(root),)) == (
        "SendUserFile denied - this role reads in the home folder only inside "
        "its own folder")
