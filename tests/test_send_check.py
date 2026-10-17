"""The send floor, the file checks and the strict root bound for files
Claude sends (aipager/send_check.py), and the English reasons Claude reads."""

from __future__ import annotations

import os
import re

import pytest

from aipager import safety, send_check
from aipager.send_check import (
    Refusal, check_files, refusal_reason, root_refusals, turn_rule_reason,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _file(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _codes(refusals):
    return [r.code for r in refusals]


# ---- the floor ------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    ".claude/.credentials.json",
    ".claude/settings.json",
    ".config/aipager/aipager.yaml",
    ".config/aipager/daemon.env",
    ".local/share/aipager/x.json",
    ".local/state/aipager/outbox/1/a.png",
    ".ssh/config",
    ".aws/credentials",
], ids=["claude-credentials", "claude-settings", "aipager-config",
        "daemon-env", "aipager-share", "aipager-state", "ssh", "aws"])
def test_floor_paths_are_refused(home, rel):
    p = _file(home / rel)
    assert _codes(check_files({"files": [p]}, None)) == ["floor"]


def test_claude_json_is_on_the_send_floor(home):
    p = _file(home / ".claude.json", b"{}")
    assert _codes(check_files({"files": [p]}, None)) == ["floor"]
    backup = _file(home / ".claude.json.backup", b"{}")
    assert _codes(check_files({"files": [backup]}, None)) == ["floor"]


def test_tilde_spelling_is_expanded(home):
    _file(home / ".claude.json", b"{}")
    assert _codes(check_files({"files": ["~/.claude.json"]}, "/srv")) == ["floor"]


@pytest.mark.parametrize("path", ["/proc/self/environ", "/sys/kernel/notes",
                                  "/dev/null"], ids=["proc", "sys", "dev"])
def test_kernel_folders_are_refused(path):
    assert _codes(check_files({"files": [path]}, None)) == ["floor"]


def test_aipager_control_files_are_refused(tmp_path):
    assert _codes(check_files(
        {"files": ["/tmp/claude-policy-claude-x.json"]}, None)) == ["floor"]


@pytest.mark.parametrize("name", [
    ".env", ".env.local", "server.pem", "tls.KEY", "id_rsa", "id_ed25519.pub",
    "id_ecdsa", "id_dsa", "cert.p12", "cert.pfx", "vault.kdbx", "store.jks",
    "a.keystore", ".netrc", ".pgpass", ".git-credentials", "credentials",
    "credentials.json", "gcp-credentials-prod.json",
])
def test_secret_shaped_names_are_refused(tmp_path, name):
    p = _file(tmp_path / "proj" / name)
    assert _codes(check_files({"files": [p]}, None)) == ["floor"]


def test_symlink_to_a_floor_file_is_refused(home, tmp_path):
    target = _file(home / ".claude" / ".credentials.json", b"{}")
    link = tmp_path / "proj" / "picture.png"
    link.parent.mkdir()
    link.symlink_to(target)
    assert _codes(check_files({"files": [str(link)]}, None)) == ["floor"]


def test_symlink_to_a_secret_name_is_refused(tmp_path):
    target = _file(tmp_path / "keys" / "server.pem")
    link = tmp_path / "proj" / "picture.png"
    link.parent.mkdir()
    link.symlink_to(target)
    assert _codes(check_files({"files": [str(link)]}, None)) == ["floor"]


# ---- the file checks ------------------------------------------------------

def test_a_plain_file_passes(tmp_path):
    p = _file(tmp_path / "proj" / "chart.png", b"\x89PNG")
    assert check_files({"files": [p]}, None) == []


def test_relative_file_resolves_against_cwd(tmp_path):
    _file(tmp_path / "proj" / "chart.png")
    assert check_files({"files": ["chart.png"]}, str(tmp_path / "proj")) == []
    assert _codes(check_files({"files": ["chart.png"]}, str(tmp_path))) == ["missing"]


def test_a_symlink_to_a_plain_file_passes(tmp_path):
    target = _file(tmp_path / "proj" / "chart.png")
    link = tmp_path / "proj" / "link.png"
    link.symlink_to(target)
    assert check_files({"files": [str(link)]}, None) == []


def test_missing_file_is_refused(tmp_path):
    assert _codes(check_files({"files": [str(tmp_path / "nope.png")]}, None)) == ["missing"]


def test_hard_linked_file_is_refused(tmp_path):
    p = _file(tmp_path / "proj" / "a.png")
    os.link(p, tmp_path / "proj" / "b.png")
    assert _codes(check_files({"files": [p]}, None)) == ["hard_link"]


def test_empty_file_is_refused(tmp_path):
    p = _file(tmp_path / "proj" / "a.txt", b"")
    assert _codes(check_files({"files": [p]}, None)) == ["empty"]


@pytest.mark.parametrize("kind", ["dir", "fifo"])
def test_directory_and_fifo_are_refused(tmp_path, kind):
    p = tmp_path / "thing"
    if kind == "dir":
        p.mkdir()
    else:
        os.mkfifo(p)
    assert _codes(check_files({"files": [str(p)]}, None)) == ["not_file"]


def test_file_over_telegram_limit_is_refused(tmp_path):
    p = tmp_path / "big.bin"
    with open(p, "wb") as fh:
        fh.truncate(send_check.FILE_MAX_BYTES + 1)
    assert _codes(check_files({"files": [str(p)]}, None)) == ["too_big"]
    with open(p, "wb") as fh:
        fh.truncate(send_check.FILE_MAX_BYTES)
    assert check_files({"files": [str(p)]}, None) == []


def test_unreadable_file_list_is_refused():
    for files in ([], None, 3, [3], [""], ["a\x00"]):
        assert _codes(check_files({"files": files}, None)) == ["unreadable_list"]
    assert _codes(check_files({}, None)) == ["unreadable_list"]


def test_files_as_a_string_is_one_entry(tmp_path):
    p = _file(tmp_path / "a.txt", b"")
    assert _codes(check_files({"files": p}, None)) == ["empty"]


def test_every_failing_file_is_listed_in_order(tmp_path):
    ok = _file(tmp_path / "ok.png")
    empty = _file(tmp_path / "e.txt", b"")
    refusals = check_files({"files": [empty, ok, "/dev/null"]}, None)
    assert [(r.shown, r.code) for r in refusals] == [
        (empty, "empty"), ("/dev/null", "floor")]


# ---- the strict root ------------------------------------------------------

def test_root_inside_is_allowed(home):
    root = home / "aipager-users" / "bob"
    p = _file(root / "a.png")
    assert root_refusals({"files": [p]}, None, str(root)) == []
    assert root_refusals({"files": ["a.png"]}, str(root), str(root)) == []


def test_root_outside_home_is_refused_too(home, tmp_path):
    root = home / "aipager-users" / "bob"
    root.mkdir(parents=True)
    other = _file(tmp_path / "x.png")
    out = root_refusals({"files": ["/etc/hosts", other, str(home / "a.png")]},
                        None, str(root))
    assert _codes(out) == ["outside_root"] * 3


def test_link_inside_root_to_outside_is_refused(home, tmp_path):
    root = home / "aipager-users" / "bob"
    root.mkdir(parents=True)
    target = _file(tmp_path / "elsewhere.png")
    link = root / "pic.png"
    link.symlink_to(target)
    assert _codes(root_refusals({"files": [str(link)]}, None, str(root))) == ["outside_root"]


def test_spelled_path_out_of_the_root_is_refused(home):
    root = home / "r"
    _file(root / "a.png")
    sibling = _file(home / "r2" / "a.png")
    assert _codes(root_refusals({"files": [sibling]}, None, str(root))) == ["outside_root"]


def test_no_usable_root_allows_nothing(tmp_path):
    p = _file(tmp_path / "a.png")
    assert _codes(root_refusals({"files": [p]}, None, None)) == ["outside_root"]


def test_root_check_refuses_an_unreadable_list():
    assert _codes(root_refusals({"files": []}, None, "/srv")) == ["unreadable_list"]


# ---- the reasons Claude reads ---------------------------------------------

def test_reason_for_one_file():
    r = refusal_reason([Refusal("~/.ssh/id_rsa", "floor", send_check._why("floor"))])
    assert r == ("SendUserFile refused, nothing was sent: ~/.ssh/id_rsa (a protected "
                 "file: aipager's settings, Claude's credentials, keys and secrets "
                 "are never sent). Tell the person which file could not be sent "
                 "and why.")


def test_reason_for_two_files():
    r = refusal_reason([Refusal("a.png", "missing", "no such file"),
                        Refusal("b", "empty", "the file is empty")])
    assert r == ("SendUserFile refused, nothing was sent: a.png (no such file); "
                 "b (the file is empty). Tell the person which file could not be "
                 "sent and why.")


def test_unreadable_list_reason():
    assert refusal_reason([Refusal("", "unreadable_list", "")]) == (
        "SendUserFile with no readable file list, nothing was sent. Tell the "
        "person the files could not be sent.")


def test_turn_rule_reason():
    assert turn_rule_reason("SendUserFile on protected path ~/.claude/**") == (
        "SendUserFile refused, nothing was sent: SendUserFile on protected path "
        "~/.claude/**. Tell the person which file could not be sent and why.")


def _reasons() -> dict[str, str]:
    out = {code: refusal_reason([Refusal("/srv/a b.png", code, send_check._why(code))])
           for code in send_check._WHY_WORDS}
    out["unreadable_list"] = refusal_reason([Refusal("", "unreadable_list", "")])
    out["unchecked"] = send_check.SEND_UNCHECKED_REASON
    out["turn_rules"] = turn_rule_reason(
        safety._outside_root_read_reason("SendUserFile"))
    return out


def test_every_reason_matches_exactly_one_shape():
    shapes = send_check.SEND_REASON_SHAPES
    assert len({c for c, _s in shapes}) == len(shapes)
    reasons = _reasons()
    assert set(reasons) == {c for c, _s in shapes}
    for code, reason in reasons.items():
        hits = [c for c, s in shapes if re.match(s, reason)]
        assert hits == [code], (code, reason, hits)
        assert send_check.reason_code(reason) == code


def test_a_two_file_reason_matches_its_last_code():
    r = refusal_reason([Refusal("a", "missing", "no such file"),
                        Refusal("b", "empty", "the file is empty")])
    assert [c for c, s in send_check.SEND_REASON_SHAPES if re.match(s, r)] == ["empty"]


def test_send_shapes_are_not_hard_block_shapes():
    for reason in _reasons().values():
        assert not any(re.match(s, reason) for _c, s in safety.REASON_SHAPES)


def test_no_em_dash_in_what_claude_reads():
    for reason in _reasons().values():
        assert "—" not in reason


def test_sending_row():
    assert send_check.sending_row(1) == "📎 Sending 1 file"
    assert send_check.sending_row(3) == "📎 Sending 3 files"


def test_send_floor_holds_every_no_access_and_credential_path():
    for glob in safety.DENY_PATHS_NO_ACCESS + safety.CREDENTIAL_PATHS:
        assert glob in send_check.SEND_FLOOR
    for glob in ("~/.claude.json*", "/proc/**", "/sys/**", "/dev/**"):
        assert glob in send_check.SEND_FLOOR
