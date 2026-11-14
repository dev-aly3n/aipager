"""The prompt-free group rules (aipager/wizard/group_rules.py), and that
the wizard's typed paths and `aipager setup group` refuse with the same
text: one code path for both."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

from aipager import scope as scope_mod
from aipager.scope import Member, Scope
from aipager.wizard import group_rules as gr

TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"
GID = -1001234567890


# ----- the validators -----

@pytest.mark.parametrize("cid,code", [(-1, None), (-1001234567890, None),
                                      (0, "bad_chat_id"), (5, "bad_chat_id")])
def test_group_chat_id_problem(cid, code):
    p = gr.group_chat_id_problem(cid)
    assert (p.code if p else None) == code


@pytest.mark.parametrize("uid,existing,code", [
    (5, set(), None), (0, set(), "bad_member_id"), (-5, set(), "bad_member_id"),
    (1087968824, set(), "shared_sender_id"), (136817688, set(), "shared_sender_id"),
    (777000, set(), "shared_sender_id"), (5, {5}, "duplicate_member"),
])
def test_member_id_problem(uid, existing, code):
    p = gr.member_id_problem(uid, existing)
    assert (p.code if p else None) == code


@pytest.mark.parametrize("label,existing,code", [
    ("alice", set(), None), ("dev:ops", set(), None), ("", set(), "bad_label"),
    ("   ", set(), "bad_label"), ("a\nb", set(), "bad_label"),
    ("a\x7fb", set(), "bad_label"), ("alice", {"alice"}, "duplicate_label"),
])
def test_label_problem(label, existing, code):
    p = gr.label_problem(label, existing)
    assert (p.code if p else None) == code


def test_default_group_label():
    assert gr.default_group_label(-1001234567890) == "group-1001234567890"


def test_role_names_and_problem(tmp_path):
    from aipager import policy
    Path(policy.POLICY_PATH).write_text(
        "roles:\n  zeta:\n    can_prompt: true\n  alpha:\n    can_manage: true\n")
    pol = gr.load_roles_strict()
    names = gr.role_names(pol)
    assert names == ["owner", "admin", "user", "read_only", "zeta", "alpha"]
    assert gr.role_problem("zeta", names) is None
    p = gr.role_problem("nope", names)
    assert p.code == "unknown_role" and "owner, admin, user, read_only, zeta, alpha" in p.message
    assert gr.owner_level("owner", pol) and not gr.owner_level("admin", pol)
    assert gr.can_manage("alpha", pol) and not gr.can_manage("zeta", pol)
    assert not gr.owner_level("missing", pol) and not gr.can_manage("missing", pol)


def test_load_roles_strict_raises_instead_of_falling_back():
    from aipager import policy
    Path(policy.POLICY_PATH).write_text("roles: [broken\n")
    with pytest.raises(policy.PolicyError):
        gr.load_roles_strict()


def test_load_roles_strict_reads_the_paths_at_call_time(tmp_path, monkeypatch):
    from aipager import policy
    other = tmp_path / "elsewhere.yaml"
    other.write_text("roles:\n  moved:\n    can_prompt: true\n")
    monkeypatch.setattr(policy, "POLICY_PATH", other)
    assert "moved" in gr.load_roles_strict().roles


def test_root_checks(tmp_path):
    home = Path.home()
    s = Scope(chat_id=GID, kind="group", label="g", members=())
    other = Scope(chat_id=-100777, kind="group", label="o", root="/srv/shared",
                  members=())
    problem, path, loader, overlap = gr.root_checks("/", s, [other])
    assert problem and path is None
    problem, path, loader, overlap = gr.root_checks(" ~/.local/bin/x ", s, [other])
    assert problem is None and path == str(home / ".local/bin/x")
    assert loader == "~/.local/bin" and overlap is None
    problem, path, loader, overlap = gr.root_checks("/srv/shared/sub", s, [other])
    assert loader is None and overlap is other


def test_old_names_still_importable():
    from aipager.wizard import root_flow, scope_flows, team_setup
    assert team_setup.SHARED_SENDER_REFUSAL == gr.SHARED_SENDER_REFUSAL
    assert scope_flows.OWNER_WARNING == gr.OWNER_WARNING
    assert root_flow.CODE_LOADING == gr.CODE_LOADING
    assert root_flow.code_loading_folder is gr.code_loading_folder
    assert root_flow._root_overlap is gr.root_overlap


# ----- one code path: the wizard and the command say the same -----

class Cmd:
    """`aipager setup group` through cli.main on a tmp install."""

    def __init__(self, monkeypatch, capsys):
        from aipager.wizard import daemon_io, telegram_api
        self.mp, self.capsys = monkeypatch, capsys
        monkeypatch.setattr(telegram_api, "_http_json",
                            lambda url: (None, None, "network: off"))
        monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: None)
        monkeypatch.setattr(daemon_io, "_live_reload", lambda: ("not_reloaded", None))
        dm = Scope(chat_id=111, kind="dm", label="owner DM",
                   members=(Member(id=111, label="owner", role="owner"),))
        scope_mod.dump_scopes([dm], TOKEN, scope_mod.CONFIG_PATH)

    def message(self, *argv):
        self.capsys.readouterr()
        self.mp.setattr(sys, "argv", ["aipager", "setup", "group", *argv, "--json"])
        from aipager import cli
        with contextlib.suppress(SystemExit):
            cli.main()
        return json.loads(self.capsys.readouterr().out)["message"]


@pytest.fixture
def cmd(monkeypatch, capsys):
    return Cmd(monkeypatch, capsys)


def _stub_ask(monkeypatch, module, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(module, "_ask", _ask)


@pytest.fixture
def team_setup(monkeypatch):
    from aipager.wizard import team_setup
    monkeypatch.setattr(team_setup, "_spin", lambda msg: contextlib.nullcontext())
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    return team_setup


@pytest.mark.parametrize("typed,existing,flag", [
    ("1087968824", set(), "1087968824:x:user"),
    ("-5", set(), "-5:x:user"),
    ("222", {222}, None),
])
def test_member_id_refusals_match(team_setup, cmd, monkeypatch, typed, existing, flag):
    warned = []
    monkeypatch.setattr(team_setup, "friendly_warn", lambda *a: warned.append(a[0]))
    _stub_ask(monkeypatch, team_setup, ["manual", typed, "5151", "carol"])
    out = team_setup._capture_user_identity(1, existing_ids=existing,
                                            existing_labels=set(), token="tok")
    assert out == {"id": 5151, "label": "carol"}
    assert len(warned) == 1
    if flag is None:
        # duplicate: the command's message for the same id carries the text
        msg = cmd.message("--chat-id", str(GID), "--member", "222:a:user",
                          "--member", "222:b:user")
    else:
        msg = cmd.message("--chat-id", str(GID), f"--member={flag}")
    assert msg.endswith(warned[0])


def test_label_refusal_matches(team_setup, cmd, monkeypatch):
    warned = []
    monkeypatch.setattr(team_setup, "friendly_warn", lambda *a: warned.append(a[0]))
    _stub_ask(monkeypatch, team_setup, ["alice", "carol"])
    out = team_setup._finalize_user(5, "x", {"alice"})
    assert out == {"id": 5, "label": "carol"}
    msg = cmd.message("--chat-id", str(GID), "--member", "5:alice:user",
                      "--member", "6:alice:user")
    assert msg.endswith(warned[0])


def test_group_id_refusal_matches(cmd, monkeypatch):
    from aipager.wizard import first_run
    printed = []
    monkeypatch.setattr(first_run.err_console, "print",
                        lambda s, *a, **k: printed.append(s))
    monkeypatch.setattr(first_run, "_test_send", lambda t, c: (True, ""))
    monkeypatch.setattr(first_run, "_spin", lambda m: contextlib.nullcontext())
    _stub_ask(monkeypatch, first_run, ["manual", "12345", "manual", "-100", True])
    assert first_run._step_chat_id("tok", "bot", mode="team") == -100
    assert any(gr.GROUP_ID_NEGATIVE in p for p in printed)
    msg = cmd.message("--chat-id", "12345", "--member", "5:a:user")
    assert gr.GROUP_ID_NEGATIVE in msg


def test_role_list_matches(cmd, monkeypatch):
    from aipager import policy
    from aipager.wizard import scope_flows
    Path(policy.POLICY_PATH).write_text("roles:\n  reviewer:\n    can_prompt: true\n")
    wizard_names = [c.value for c in scope_flows._role_choices()]
    msg = cmd.message("--chat-id", str(GID), "--member", "5:a:nope")
    assert f"Available roles: {', '.join(wizard_names)}." in msg


def test_wizard_role_list_still_falls_back(monkeypatch):
    from aipager import policy
    from aipager.wizard import scope_flows
    Path(policy.POLICY_PATH).write_text("roles: [broken\n")
    assert [c.value for c in scope_flows._role_choices()] == list(gr.BUILTIN_ORDER)


def test_root_refusal_matches(cmd, monkeypatch):
    from aipager.wizard import root_flow
    warned = []
    monkeypatch.setattr(root_flow, "friendly_warn", lambda *a: warned.append(a[0]))
    answers = iter(["/", "/srv/team"])
    s = Scope(chat_id=GID, kind="group", label="g", members=())
    assert root_flow.ask_root(s, [], lambda q: next(answers)) == "/srv/team"
    problem = warned[0].removeprefix("That folder can't be a root: ").rstrip(".")
    msg = cmd.message("--chat-id", str(GID), "--member", "5:a:user", "--root", "/")
    assert problem in msg


def test_wizard_still_confirms_a_code_loading_root(monkeypatch):
    from aipager.wizard import root_flow
    answers = iter(["~/.local/bin/x", False, "/srv/team"])
    s = Scope(chat_id=GID, kind="group", label="g", members=())
    assert root_flow.ask_root(s, [], lambda q: next(answers)) == "/srv/team"


def test_wizard_still_confirms_an_overlapping_root(monkeypatch):
    from aipager.wizard import root_flow
    other = Scope(chat_id=-100777, kind="group", label="o", root="/srv/shared",
                  members=())
    answers = iter(["/srv/shared/x", False, "/srv/team"])
    s = Scope(chat_id=GID, kind="group", label="g", members=())
    assert root_flow.ask_root(s, [other], lambda q: next(answers)) == "/srv/team"
