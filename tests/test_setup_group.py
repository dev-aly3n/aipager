"""Unit tests for `aipager setup group` (aipager/setup_group.py).

The command runs in-process through ``aipager.cli.main``. Every Bot API
call goes through the faked ``telegram_api._http_json``; ``sendMessage``
is replaced by a recorder that must never be called. Daemon detection and
the live reload go through the ``daemon_io`` seams; nothing is signalled.
``aipager.yaml``, ``policy.yaml`` and the audit log live under tmp
(tests/conftest.py redirects them).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from aipager import member_root
from aipager import scope as scope_mod
from aipager.scope import Member, Scope

TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"
SECRET = TOKEN.split(":", 1)[1]
BOT = "example_bot"
BOT_ID = 123456789
GID = -1001234567890

GROUP_KEYS = {
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "dry_run", "force", "bot_username", "chat_id", "scope_chat_id", "label",
    "root", "block_edits", "members", "changed", "member_changes",
    "force_reasons", "pinned_roots", "telegram", "daemon", "warnings",
    "next_step",
}


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


class FakeTelegram:
    """Answers getMe, getChat and getChatMember; records every URL."""

    def __init__(self):
        self.get_me = ({"ok": True, "result": {
            "id": BOT_ID, "username": BOT, "can_read_all_group_messages": False}},
            200, "")
        self.get_chat = ({"ok": True, "result": {
            "id": GID, "type": "supergroup", "title": "Team"}}, 200, "")
        self.get_member = ({"ok": True, "result": {
            "status": "administrator", "can_pin_messages": True}}, 200, "")
        self.urls: list[str] = []
        self.sends: list = []

    def http_json(self, url):
        self.urls.append(url)
        method = url.rsplit("/", 1)[1]
        if method == "getMe":
            return self.get_me
        if method.startswith("getChatMember?"):
            return self.get_member
        if method.startswith("getChat?"):
            return self.get_chat
        return {"ok": True, "result": []}, 200, ""

    def send_message(self, *a, **k):
        self.sends.append(a)
        return True, "", 200

    def methods(self):
        return [u.rsplit("/", 1)[1].split("?", 1)[0] for u in self.urls]


class Env:
    def __init__(self, tmp_path, monkeypatch, capsys):
        from aipager.wizard import daemon_io, telegram_api

        self.tmp = tmp_path
        self.mp = monkeypatch
        self.capsys = capsys
        self.tg = FakeTelegram()
        monkeypatch.setattr(telegram_api, "_http_json", self.tg.http_json)
        monkeypatch.setattr(telegram_api, "_send_message", self.tg.send_message)
        self.daemon_pid = None
        self.detect_raises = False

        def _detect():
            if self.detect_raises:
                raise RuntimeError("cannot tell")
            return self.daemon_pid

        monkeypatch.setattr(daemon_io, "_detect_daemon_running", _detect)
        self.reloads: list[int] = []
        self.reload_outcome = ("reloaded", None)

        def _live_reload():
            self.reloads.append(1)
            return self.reload_outcome

        monkeypatch.setattr(daemon_io, "_live_reload", _live_reload)

    @property
    def yaml_path(self) -> Path:
        return scope_mod.CONFIG_PATH

    def install(self, *extra_scopes, owner=111):
        dm = Scope(chat_id=owner, kind="dm", label="owner DM",
                   members=(Member(id=owner, label="owner", role="owner"),))
        scope_mod.dump_scopes([dm, *extra_scopes], TOKEN, scope_mod.CONFIG_PATH)

    def scopes(self):
        return scope_mod.load_scopes(scope_mod.CONFIG_PATH)[0]

    def group(self, cid=GID):
        return next(s for s in self.scopes() if s.chat_id == cid)

    def run(self, *argv):
        self.capsys.readouterr()
        self.mp.setattr(sys, "argv", ["aipager", *argv])
        from aipager import cli
        try:
            cli.main()
            code = "returned"
        except SystemExit as e:
            code = e.code
        except Exception as e:  # recorded, so a leak fails by assertion
            code = f"raised {type(e).__name__}"
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def gj(self, *argv):
        """``aipager setup group --chat-id GID <argv> --json``."""
        code, out, err = self.run("setup", "group", "--chat-id", str(GID),
                                  *argv, "--json")
        try:
            doc = json.loads(out)
        except ValueError:
            doc = None
        return code, doc, out, err

    def audit_lines(self):
        from aipager import audit
        p = Path(audit.AUDIT_LOG_PATH)
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def snapshot(self):
        return {str(p): p.read_bytes() for p in sorted(self.tmp.rglob("*"))
                if p.is_file()}


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, monkeypatch, capsys)
    e.install()
    return e


ALICE = ("--member", "111:alice:owner")
BOB = ("--member", "222:bob:user")


def _policy(text: str) -> None:
    from aipager import policy
    Path(policy.POLICY_PATH).write_text(text)


# ===========================================================================
# create, re-run, extend
# ===========================================================================

def test_create_writes_the_group_like_the_wizard(env):
    before_dm = env.scopes()[0]
    code, doc, _o, _e = env.gj(*ALICE, *BOB)
    assert code == 0 and doc["status"] == "created" and doc["ok"] is True
    assert set(doc) == GROUP_KEYS
    assert doc["changed"] == ["group"] and doc["force_reasons"] == []
    assert stat.S_IMODE(env.yaml_path.stat().st_mode) == 0o600
    g = env.group()
    assert g.kind == "group" and g.label == "group-1001234567890"
    assert [(m.id, m.label, m.role) for m in g.members] == [
        (111, "alice", "owner"), (222, "bob", "user")]
    assert g.root == str(Path(member_root.users_dir()) / "group-group-1001234567890")
    dm = env.scopes()[0]
    assert dm.members == before_dm.members and dm.chat_id == 111
    assert doc["members"] == [{"id": 111, "label": "alice", "role": "owner"},
                              {"id": 222, "label": "bob", "role": "user"}]
    assert doc["bot_username"] == BOT and doc["scope_chat_id"] == GID
    assert doc["daemon"]["outcome"] == "no_daemon"
    assert [w["code"] for w in doc["warnings"]] == ["owner_granted"]


def test_create_audits_each_owner_grant(env):
    env.gj(*ALICE, *BOB)
    grants = [r for r in env.audit_lines() if r.get("action") == "grant-owner"]
    assert [r["user_id"] for r in grants] == [111]


def test_audit_failure_is_a_warning_not_an_error(env, monkeypatch):
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_record_owner_grant", lambda cid: False)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0 and "audit_write_failed" in [w["code"] for w in doc["warnings"]]


def test_same_arguments_again_change_nothing(env):
    env.daemon_pid = 4242
    env.gj(*ALICE, *BOB)
    before = env.yaml_path.read_bytes()
    env.reloads.clear()
    code, doc, _o, _e = env.gj(*ALICE, *BOB)
    assert code == 0 and doc["status"] == "unchanged"
    assert doc["changed"] == [] and doc["pinned_roots"] == []
    assert env.yaml_path.read_bytes() == before
    assert env.reloads == []
    assert doc["daemon"]["reload"] == "not_needed"
    assert doc["daemon"]["outcome"] == "not_needed"


def test_unchanged_run_never_writes(env, monkeypatch):
    env.gj(*ALICE, *BOB)
    from aipager.wizard import scope_io
    calls = []
    monkeypatch.setattr(scope_io, "put_group_scope",
                        lambda *a, **k: calls.append(1))
    code, doc, _o, _e = env.gj(*ALICE, *BOB)
    assert code == 0 and calls == []


def test_adding_a_member_keeps_the_others_and_reloads(env):
    env.gj(*ALICE, *BOB)
    env.daemon_pid = 4242
    env.reloads.clear()
    code, doc, _o, _e = env.gj("--member", "333:carol:admin")
    assert code == 0 and doc["status"] == "updated"
    assert doc["changed"] == ["member_added"]
    assert [m.id for m in env.group().members] == [111, 222, 333]
    assert doc["daemon"] == {"running": True, "reload": "reloaded",
                             "restart_needed": False, "outcome": "reloaded_live"}
    assert env.reloads == [1]
    assert doc["member_changes"] == [{"id": 333, "change": "added", "label": "carol",
                                      "role": "admin", "previous_role": None}]


def test_relabel_needs_no_force(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--member", "222:robert:user")
    assert code == 0 and doc["changed"] == ["member_relabeled"]
    assert [m.label for m in env.group().members] == ["alice", "robert"]


def test_label_may_hold_colons(env):
    code, doc, _o, _e = env.gj(*ALICE, "--member", "222:dev:ops:user")
    assert code == 0
    assert [m.label for m in env.group().members] == ["alice", "dev:ops"]


def test_role_change_needs_force(env):
    env.gj(*ALICE, *BOB)
    before = env.yaml_path.read_bytes()
    code, doc, _o, _e = env.gj("--member", "222:bob:admin")
    assert code == 6 and doc["error"] == "needs_force"
    assert doc["force_reasons"] == ["role_changed"]
    assert "--force" in doc["fix"]
    assert env.yaml_path.read_bytes() == before
    code, doc, _o, _e = env.gj("--member", "222:bob:admin", "--force")
    assert code == 0 and doc["changed"] == ["role"]
    assert env.group().members[1].role == "admin"
    assert doc["member_changes"][0]["previous_role"] == "user"


def test_role_change_keeps_per_member_rules(env):
    g = Scope(chat_id=GID, kind="group", label="team", root="/srv/team",
              members=(Member(id=111, label="alice", role="owner"),
                       Member(id=222, label="bob", role="user",
                              deny_tools=("WebFetch",), allow_tools=("Read",))))
    env.install(g)
    code, doc, _o, _e = env.gj("--member", "222:bob:admin", "--force")
    assert code == 0
    bob = env.group().members[1]
    assert bob.role == "admin" and bob.deny_tools == ("WebFetch",)
    assert bob.allow_tools == ("Read",)


def test_new_owner_who_is_not_owner_anywhere_needs_force(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--member", "444:dave:owner")
    assert code == 6 and doc["force_reasons"] == ["owner_granted"]
    code, doc, _o, _e = env.gj("--member", "444:dave:owner", "--force")
    assert code == 0
    assert 444 in [r["user_id"] for r in env.audit_lines()
                   if r.get("action") == "grant-owner"]


def test_operator_as_owner_needs_no_force(env):
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0 and doc["force_reasons"] == []


def test_custom_role_with_bypass_safety_counts_as_owner(env):
    _policy("roles:\n  boss:\n    bypass_safety: true\n    can_manage: true\n")
    code, doc, _o, _e = env.gj(*ALICE, "--member", "555:eve:boss")
    assert code == 6 and doc["force_reasons"] == ["owner_granted"]


def test_removing_an_owner_needs_force(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--remove-member", "111")
    assert code == 6 and doc["force_reasons"] == ["owner_removed"]
    code, doc, _o, _e = env.gj("--remove-member", "111", "--force")
    assert code == 0 and doc["changed"] == ["member_removed"]
    assert [m.id for m in env.group().members] == [222]


def test_demoting_an_owner_needs_force(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--member", "111:alice:admin")
    assert code == 6
    assert doc["force_reasons"] == ["role_changed", "owner_demoted"]


def test_removing_a_user_needs_no_force(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--remove-member", "222")
    assert code == 0 and [m.id for m in env.group().members] == [111]


def test_removing_the_last_member_is_refused_even_with_force(env):
    env.gj(*BOB)
    before = env.yaml_path.read_bytes()
    code, doc, _o, _e = env.gj("--remove-member", "222", "--force")
    assert code == 1 and doc["error"] == "last_member"
    assert "aipager config" in doc["fix"]
    assert env.yaml_path.read_bytes() == before


def test_removing_a_non_member_is_a_warning(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--remove-member", "999")
    assert code == 0 and doc["status"] == "unchanged"
    assert "not_a_member" in [w["code"] for w in doc["warnings"]]


def test_label_collision_with_an_existing_member_is_refused(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--member", "333:bob:user")
    assert code == 2 and doc["error"] == "duplicate_label"


def test_swapping_two_labels_in_one_run_is_fine(env):
    env.gj(*ALICE, *BOB)
    code, doc, _o, _e = env.gj("--member", "111:bob:owner",
                               "--member", "222:alice:user")
    assert code == 0
    assert [m.label for m in env.group().members] == ["bob", "alice"]


def test_new_group_needs_a_member(env):
    code, doc, _o, _e = env.gj()
    assert code == 2 and doc["error"] == "usage"
    assert not any(s.chat_id == GID for s in env.scopes())


def test_no_manager_warning(env):
    code, doc, _o, _e = env.gj(*BOB)
    assert code == 0 and "no_manager" in [w["code"] for w in doc["warnings"]]
    code, doc, _o, _e = env.gj(*ALICE)
    assert "no_manager" not in [w["code"] for w in doc["warnings"]]


def test_label_and_block_edits(env):
    code, doc, _o, _e = env.gj(*ALICE, "--label", "team", "--block-edits")
    assert code == 0
    g = env.group()
    assert g.label == "team" and g.deny_tools == ("Write", "Edit")
    assert doc["block_edits"] is True


def test_block_edits_adds_the_missing_tool_and_never_removes(env):
    g = Scope(chat_id=GID, kind="group", label="team", root="/srv/team",
              members=(Member(id=111, label="alice", role="owner"),),
              deny_tools=("WebFetch", "Edit"))
    env.install(g)
    code, doc, _o, _e = env.gj("--block-edits")
    assert code == 0 and doc["changed"] == ["deny_tools"]
    assert env.group().deny_tools == ("WebFetch", "Edit", "Write")
    code, doc, _o, _e = env.gj()
    assert doc["status"] == "unchanged"
    assert env.group().deny_tools == ("WebFetch", "Edit", "Write")


def test_rename_keeps_the_members_folder(env):
    g = Scope(chat_id=GID, kind="group", label="team",
              members=(Member(id=111, label="alice", role="owner"),))
    env.install(g)
    old_root = str(Path(member_root.users_dir()) / "group-team")
    code, doc, _o, _e = env.gj("--label", "crew")
    assert code == 0 and doc["changed"] == ["label"]
    assert env.group().label == "crew" and env.group().root == old_root


def test_root_is_set_and_stored_absolute(env, tmp_path):
    env.gj(*ALICE)
    code, doc, _o, _e = env.gj("--root", "~/work/team")
    assert code == 0 and doc["changed"] == ["root"]
    assert env.group().root == str(Path.home() / "work" / "team")


def test_bad_root_is_refused(env):
    code, doc, _o, _e = env.gj(*ALICE, "--root", "/")
    assert code == 2 and doc["error"] == "bad_root"
    assert env.tg.urls == []


def test_root_in_a_code_loading_folder_needs_force(env):
    code, doc, _o, _e = env.gj(*ALICE, "--root", "~/.local/bin/x")
    assert code == 6 and doc["force_reasons"] == ["root_code_loading"]
    code, doc, _o, _e = env.gj(*ALICE, "--root", "~/.local/bin/x", "--force")
    assert code == 0


def test_root_overlapping_another_scope_needs_force(env):
    other = Scope(chat_id=-100777, kind="group", label="other", root="/srv/shared",
                  members=(Member(id=111, label="alice", role="owner"),))
    env.install(other)
    code, doc, _o, _e = env.gj(*ALICE, "--root", "/srv/shared/sub")
    assert code == 6 and doc["force_reasons"] == ["root_overlap"]


def test_moved_defaults_of_other_scopes_are_pinned(env):
    # Two groups with no root whose labels differ only in case share a
    # default name, so both carry their chat id. Renaming one would move
    # the other's folder back to the plain name: it is pinned instead.
    a = Scope(chat_id=-100777, kind="group", label="team",
              members=(Member(id=111, label="alice", role="owner"),))
    b = Scope(chat_id=GID, kind="group", label="Team",
              members=(Member(id=111, label="alice", role="owner"),))
    env.install(a, b)
    a_root = member_root.effective_root(a, env.scopes())
    assert a_root.endswith("group-team-n100777")
    code, doc, _o, _e = env.gj("--label", "crew", "--dry-run")
    assert doc["pinned_roots"] == [{"chat_id": -100777, "root": a_root}]
    code, doc, _o, _e = env.gj("--label", "crew")
    assert code == 0 and doc["pinned_roots"] == [{"chat_id": -100777, "root": a_root}]
    assert env.group(-100777).root == a_root


def test_a_supergroups_other_id_finds_the_group(env):
    g = Scope(chat_id=-100999, kind="group", label="team", root="/srv/team",
              members=(Member(id=111, label="alice", role="owner"),))
    env.install(g)
    raw = yaml.safe_load(env.yaml_path.read_text())
    raw["chat_migrations"] = {GID: -100999}
    env.yaml_path.write_text(yaml.safe_dump(raw))
    code, doc, _o, _e = env.gj(*BOB)
    assert code == 0 and doc["status"] == "updated"
    assert doc["scope_chat_id"] == -100999
    assert "group_moved" in [w["code"] for w in doc["warnings"]]
    assert [m.id for m in env.group(-100999).members] == [111, 222]


def test_a_dm_scope_is_not_a_group(env):
    raw = yaml.safe_load(env.yaml_path.read_text())
    raw["chat_migrations"] = {GID: -100555}
    env.yaml_path.write_text(yaml.safe_dump(raw))
    from aipager.setup_group import GroupRequest, MemberSpec, plan_group
    from aipager.wizard import group_rules
    dm_like = Scope(chat_id=-100555, kind="dm", label="odd",
                    members=(Member(id=-100555, label="x", role="user"),))
    plan = plan_group([dm_like], {GID: -100555},
                      GroupRequest(GID, (MemberSpec(5, "e", "user"),)),
                      group_rules.load_roles_strict())
    assert "not_a_group_scope" in plan.errors


# ===========================================================================
# input checks
# ===========================================================================

@pytest.mark.parametrize("uid", [1087968824, 136817688, 777000])
def test_shared_sender_ids_are_refused(env, uid):
    code, doc, _o, _e = env.gj("--member", f"{uid}:x:user")
    assert code == 2 and doc["error"] == "shared_sender_id"


@pytest.mark.parametrize("member,error", [
    ("111", "bad_member"), ("111:alice", "bad_member"), (":alice:user", "bad_member"),
    ("111::user", "bad_member"), ("111:alice:", "bad_member"),
    ("abc:alice:user", "bad_member_id"), ("-5:alice:user", "bad_member_id"),
    ("0:alice:user", "bad_member_id"),
])
def test_malformed_members_are_refused(env, member, error):
    code, doc, _o, _e = env.gj(f"--member={member}")
    assert code == 2 and doc["error"] == error
    assert env.tg.urls == []


def test_duplicate_member_ids_and_labels(env):
    code, doc, _o, _e = env.gj("--member", "5:a:user", "--member", "5:b:user")
    assert code == 2 and doc["error"] == "duplicate_member"
    code, doc, _o, _e = env.gj("--member", "5:a:user", "--member", "6:a:user")
    assert code == 2 and doc["error"] == "duplicate_label"


def test_unknown_role_lists_the_roles(env):
    code, doc, _o, _e = env.gj("--member", "5:x:superuser")
    assert code == 2 and doc["error"] == "unknown_role"
    assert "owner, admin, user, read_only" in doc["message"]


def test_custom_role_is_accepted(env):
    _policy("roles:\n  reviewer:\n    can_prompt: true\n")
    code, doc, _o, _e = env.gj("--member", "5:x:reviewer")
    assert code == 0 and env.group().members[0].role == "reviewer"


def test_malformed_policy_is_refused(env):
    _policy("roles: [this is not a mapping\n")
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 1 and doc["error"] == "policy_malformed"
    assert "aipager policy validate" in doc["fix"]


def test_policy_d_is_read(env):
    from aipager import policy
    d = Path(policy.POLICY_D_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / "10-extra.yaml").write_text("roles:\n  helper:\n    can_prompt: true\n")
    code, doc, _o, _e = env.gj("--member", "5:x:helper")
    assert code == 0


@pytest.mark.parametrize("chat,error", [("123", "bad_chat_id"), ("0", "bad_chat_id"),
                                        ("abc", "bad_chat_id"), ("-", "bad_chat_id")])
def test_bad_chat_ids(env, chat, error):
    code, out, _e = env.run("setup", "group", "--chat-id", chat, *ALICE, "--json")
    doc = json.loads(out)
    assert code == 2 and doc["error"] == error


def test_positive_chat_id_points_at_dm_setup(env):
    code, out, _e = env.run("setup", "group", "--chat-id", "123", *ALICE, "--json")
    assert "aipager setup --chat-id" in json.loads(out)["fix"]


def test_missing_chat_id(env):
    code, out, _e = env.run("setup", "group", *ALICE, "--json")
    assert code == 2 and json.loads(out)["error"] == "usage"


def test_bad_remove_member(env):
    code, doc, _o, _e = env.gj("--remove-member", "abc")
    assert code == 2 and doc["error"] == "bad_member_id"
    code, doc, _o, _e = env.gj(*ALICE, "--remove-member", "111")
    assert code == 2 and doc["error"] == "usage"


def test_bad_label(env):
    code, doc, _o, _e = env.gj(*ALICE, "--label", "  ")
    assert code == 2 and doc["error"] == "bad_label"


@pytest.mark.parametrize("extra", [
    ("--token-file", "/nonexistent"), ("--token-stdin",), ("--role", "owner"),
    ("--service",), ("--timeout", "5"),
])
def test_group_refuses_other_forms_flags(env, extra):
    before = env.snapshot()
    code, doc, _o, _e = env.gj(*ALICE, *extra)
    assert code == 2 and doc["error"] == "usage"
    assert env.snapshot() == before and env.tg.urls == []


def test_token_on_the_command_line_is_refused_and_never_printed(env):
    code, out, err = env.run("setup", "group", "--chat-id", str(GID), *ALICE,
                             "--token", TOKEN, "--json")
    assert code == 2 and json.loads(out)["error"] == "token_on_command_line"
    assert SECRET not in out + err


@pytest.mark.parametrize("flag", [("--member", "5:x:user"), ("--remove-member", "5"),
                                  ("--label", "x"), ("--root", "/srv/x"),
                                  ("--block-edits",)])
def test_dm_setup_refuses_group_flags(env, flag, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text(TOKEN)
    os.chmod(tok, 0o600)
    code, out, _e = env.run("setup", "--token-file", str(tok), "--chat-id", "111",
                            *flag, "--json")
    doc = json.loads(out)
    assert code == 2 and doc["error"] == "usage"
    assert "aipager setup group" in doc["message"]


def test_detect_chat_refuses_group_flags(env, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text(TOKEN)
    os.chmod(tok, 0o600)
    code, out, _e = env.run("setup", "detect-chat", "--token-file", str(tok),
                            "--member", "5:x:user", "--json")
    doc = json.loads(out)
    assert code == 2 and "--member" in doc["message"]
    assert "detect-chat" in doc["message"]


def test_timeout_message_names_both_detect_actions(env, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text(TOKEN)
    os.chmod(tok, 0o600)
    code, out, _e = env.run("setup", "--token-file", str(tok), "--chat-id", "111",
                            "--timeout", "5", "--json")
    doc = json.loads(out)
    assert code == 2 and "detect-chat" in doc["message"]
    assert "detect-group" in doc["message"]


def test_abbreviated_flags_are_refused(env):
    code, out, err = env.run("setup", "group", "--chat-id", str(GID),
                             "--memb", "5:x:user")
    assert code == 2 and "unrecognized arguments" in err


# ===========================================================================
# the install
# ===========================================================================

@pytest.mark.parametrize("argv", [
    ("setup", "group", "--chat-id", str(GID), "--member", "5:x:user", "--json"),
    ("setup", "detect-group", "--timeout", "0", "--json"),
])
def test_no_install_is_not_set_up(tmp_path, monkeypatch, capsys, argv):
    e = Env(tmp_path, monkeypatch, capsys)
    code, out, _err = e.run(*argv)
    doc = json.loads(out)
    assert code == 11 and doc["error"] == "not_set_up"
    assert not e.yaml_path.exists() and e.tg.urls == []


def test_old_config_env_install_is_not_set_up(tmp_path, monkeypatch, capsys):
    from aipager.wizard import _constants
    e = Env(tmp_path, monkeypatch, capsys)
    _constants.CONFIG_ENV.parent.mkdir(parents=True, exist_ok=True)
    _constants.CONFIG_ENV.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nCHAT_ID=111\n")
    code, doc, out, err = e.gj(*ALICE)
    assert code == 11 and doc["error"] == "not_set_up"
    assert "config.env" in doc["message"] and "aipager config" in doc["fix"]
    assert not e.yaml_path.exists()
    assert SECRET not in out + err


def test_malformed_yaml_is_refused(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, monkeypatch, capsys)
    e.yaml_path.write_text("schema_version: 3\nbot_token: x\nscopes: oops\n")
    before = e.yaml_path.read_bytes()
    code, doc, _o, _e = e.gj(*ALICE)
    assert code == 1 and doc["error"] == "config_malformed"
    assert e.yaml_path.read_bytes() == before


# ===========================================================================
# priority, dry run
# ===========================================================================

def test_unknown_role_wins_over_last_member(env):
    env.gj(*BOB)
    code, doc, _o, _e = env.gj("--remove-member", "222", "--member", "5:x:nope")
    assert code == 2 and doc["error"] == "unknown_role"


def test_last_member_wins_over_needs_force(env):
    env.gj(*ALICE)
    code, doc, _o, _e = env.gj("--remove-member", "111")
    assert code == 1 and doc["error"] == "last_member"


def test_dry_run_writes_nothing_and_reports(env):
    env.daemon_pid = 4242
    before = env.snapshot()
    code, doc, _o, _e = env.gj(*ALICE, *BOB, "--dry-run")
    assert code == 0 and doc["status"] == "dry_run" and doc["dry_run"] is True
    assert doc["changed"] == ["group"]
    assert [m["id"] for m in doc["members"]] == [111, 222]
    assert env.snapshot() == before and env.reloads == []


def test_dry_run_exits_like_the_real_run(env):
    env.gj(*ALICE, *BOB)
    before = env.yaml_path.read_bytes()
    code, doc, _o, _e = env.gj("--member", "222:bob:admin", "--dry-run")
    assert code == 6 and doc["error"] == "needs_force"
    assert doc["changed"] == ["role"]
    assert env.yaml_path.read_bytes() == before


def test_dry_run_still_makes_the_read_only_checks(env):
    env.gj(*ALICE, "--dry-run")
    assert "getChat" in env.tg.methods()


# ===========================================================================
# the write: concurrent edits, real-home guard
# ===========================================================================

def test_a_concurrent_edit_of_the_group_is_never_overwritten(env, monkeypatch):
    env.gj(*ALICE, *BOB)
    from aipager import setup_group
    real = setup_group._bot_checks

    def _edit_meanwhile(token, chat_id):
        # Someone (the wizard) changes the same group between the plan and
        # the write.
        scopes = env.scopes()
        out = [Scope(s.chat_id, s.kind, s.label, s.members[:1], s.deny_tools, s.root)
               if s.chat_id == GID else s for s in scopes]
        scope_mod.dump_scopes(out, TOKEN, scope_mod.CONFIG_PATH)
        return real(token, chat_id)

    monkeypatch.setattr(setup_group, "_bot_checks", _edit_meanwhile)
    env.daemon_pid = 4242
    env.reloads.clear()
    code, doc, _o, _e = env.gj("--member", "333:carol:user")
    assert code == 1 and doc["error"] == "config_changed"
    assert [m.id for m in env.group().members] == [111]
    assert env.reloads == [] and doc["changed"] == []


def test_a_concurrent_create_of_the_group_is_never_overwritten(env, monkeypatch):
    from aipager import setup_group
    real = setup_group._bot_checks

    def _create_meanwhile(token, chat_id):
        env.install(Scope(chat_id=GID, kind="group", label="theirs", root="/srv/t",
                          members=(Member(id=9, label="zed", role="user"),)))
        return real(token, chat_id)

    monkeypatch.setattr(setup_group, "_bot_checks", _create_meanwhile)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 1 and doc["error"] == "config_changed"
    assert env.group().label == "theirs"


def test_other_scopes_changed_meanwhile_are_kept(env, monkeypatch):
    from aipager import setup_group
    real = setup_group._bot_checks
    extra = Scope(chat_id=-100321, kind="group", label="late", root="/srv/late",
                  members=(Member(id=9, label="zed", role="user"),))

    def _add_other(token, chat_id):
        env.install(extra)
        return real(token, chat_id)

    monkeypatch.setattr(setup_group, "_bot_checks", _add_other)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0
    assert {s.chat_id for s in env.scopes()} == {111, -100321, GID}


def test_check_write_guards_both_targets(env, monkeypatch):
    from aipager import _test_guard, audit
    seen = []

    def _guard(path):
        seen.append(Path(path))
        raise _test_guard.RealHomeWriteError(str(path)) if hasattr(
            _test_guard, "RealHomeWriteError") else PermissionError(str(path))

    monkeypatch.setattr(_test_guard, "check_write", _guard)
    before = env.yaml_path.read_bytes()
    code, doc, _o, _e = env.gj(*ALICE)
    assert code != 0
    assert seen and seen[0] == Path(scope_mod.CONFIG_PATH)
    assert env.yaml_path.read_bytes() == before
    seen.clear()

    def _only_audit(path):
        seen.append(Path(path))
        if Path(path) == Path(audit.AUDIT_LOG_PATH):
            raise PermissionError(str(path))

    monkeypatch.setattr(_test_guard, "check_write", _only_audit)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code != 0 and env.yaml_path.read_bytes() == before


# ===========================================================================
# reload outcomes
# ===========================================================================

def test_reload_refused(env):
    env.daemon_pid = 4242
    env.reload_outcome = ("refused", "policy.yaml is broken")
    code, doc, out, _e = env.gj(*ALICE)
    assert code == 0 and doc["daemon"]["outcome"] == "not_applied"
    assert "reload_refused" in [w["code"] for w in doc["warnings"]]


def test_pid_unknown_asks_for_a_restart(env):
    env.daemon_pid = -1
    env.reload_outcome = ("not_reloaded", None)
    code, doc, _o, _e = env.gj(*ALICE)
    assert doc["daemon"]["restart_needed"] is True
    assert doc["daemon"]["outcome"] == "restart_needed"
    assert "Restart the daemon" in doc["next_step"]


def test_detection_failing_means_no_reload(env):
    env.detect_raises = True
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0 and env.reloads == []
    assert "daemon_unknown" in [w["code"] for w in doc["warnings"]]
    assert doc["daemon"]["restart_needed"] is True


def test_reload_that_raises_is_a_restart(env, monkeypatch):
    from aipager.wizard import daemon_io
    env.daemon_pid = 4242

    def _boom():
        raise OSError("nope")

    monkeypatch.setattr(daemon_io, "_live_reload", _boom)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0 and doc["daemon"]["outcome"] == "restart_needed"


def test_no_daemon(env):
    code, doc, _o, _e = env.gj(*ALICE)
    assert doc["daemon"]["outcome"] == "no_daemon"
    assert doc["daemon"]["running"] is False
    assert "Start aipager" in doc["next_step"]


@pytest.mark.parametrize("outcome,pid,line", [
    (("reloaded", None), 4242, "daemon:   reloaded live"),
    (("refused", "broken"), 4242, "daemon:   not applied: "),
    (("not_reloaded", None), -1, "daemon:   restart needed"),
    (("reloaded", None), None, "daemon:   no daemon running"),
])
def test_plain_mode_names_the_reload_outcome(env, outcome, pid, line):
    env.daemon_pid = pid
    env.reload_outcome = outcome
    code, out, err = env.run("setup", "group", "--chat-id", str(GID), *ALICE)
    assert code == 0 and line in out
    assert out.startswith("✓ Group group-1001234567890")
    assert "  members:  alice (111, owner)" in out
    assert "Next: " in out


# ===========================================================================
# Telegram checks (read-only, warnings only)
# ===========================================================================

NOT_FOUND = ({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
             400, "Bad Request: chat not found")
FORBIDDEN = ({"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member"},
             403, "Forbidden: bot is not a member")


@pytest.mark.parametrize("setup,warning", [
    (lambda tg: setattr(tg, "get_chat", NOT_FOUND), "group_not_found"),
    (lambda tg: setattr(tg, "get_chat", FORBIDDEN), "bot_not_in_group"),
    (lambda tg: setattr(tg, "get_chat", (
        {"ok": False, "description": "Bad Request: group chat was upgraded",
         "parameters": {"migrate_to_chat_id": -100999}}, 400, "x")), "group_upgraded"),
    (lambda tg: setattr(tg, "get_chat", (
        {"ok": True, "result": {"id": GID, "type": "channel"}}, 200, "")),
     "chat_not_a_group"),
    (lambda tg: setattr(tg, "get_member", (
        {"ok": True, "result": {"status": "left"}}, 200, "")), "bot_not_in_group"),
    (lambda tg: setattr(tg, "get_member", (
        {"ok": True, "result": {"status": "kicked"}}, 200, "")), "bot_not_in_group"),
    (lambda tg: setattr(tg, "get_member", (
        {"ok": True, "result": {"status": "member"}}, 200, "")), "bot_not_admin"),
    (lambda tg: setattr(tg, "get_member", (
        {"ok": True, "result": {"status": "restricted"}}, 200, "")), "bot_not_admin"),
    (lambda tg: setattr(tg, "get_member", (
        {"ok": True, "result": {"status": "administrator",
                                "can_pin_messages": False}}, 200, "")), "bot_cannot_pin"),
    (lambda tg: setattr(tg, "get_me", (
        {"ok": False, "description": "Unauthorized"}, 401, "")), "token_rejected"),
    (lambda tg: setattr(tg, "get_me", (None, None, "network: down")),
     "telegram_checks_skipped"),
    (lambda tg: setattr(tg, "get_chat", (None, 502, "bad gateway")),
     "telegram_checks_skipped"),
    (lambda tg: setattr(tg, "get_member", (None, 429, "slow down")),
     "telegram_checks_skipped"),
])
def test_bot_api_answers_map_to_warnings(env, setup, warning):
    setup(env.tg)
    code, doc, _o, _e = env.gj(*ALICE)
    assert code == 0 and doc["status"] == "created"
    assert warning in [w["code"] for w in doc["warnings"]]
    assert env.tg.sends == []


@pytest.mark.parametrize("status,pin", [("creator", None), ("administrator", True)])
def test_admin_with_pin_rights_gives_no_warning(env, status, pin):
    result = {"status": status}
    if pin is not None:
        result["can_pin_messages"] = pin
    env.tg.get_member = ({"ok": True, "result": result}, 200, "")
    code, doc, _o, _e = env.gj(*ALICE)
    tg_codes = {"bot_not_admin", "bot_cannot_pin", "bot_not_in_group"}
    assert not tg_codes & {w["code"] for w in doc["warnings"]}
    assert doc["telegram"]["bot_can_pin"] is True
    assert doc["telegram"]["checked"] is True


def test_telegram_doc_reports_privacy_mode(env):
    code, doc, _o, _e = env.gj(*ALICE)
    assert doc["telegram"] == {"checked": True, "chat_type": "supergroup",
                               "chat_title": "Team", "bot_status": "administrator",
                               "bot_can_pin": True, "privacy_mode": True}


def test_a_network_error_on_getme_skips_the_rest(env):
    env.tg.get_me = (None, None, "network: down")
    code, doc, _o, _e = env.gj(*ALICE)
    assert env.tg.methods() == ["getMe"]
    assert doc["telegram"]["checked"] is False
    assert [w["code"] for w in doc["warnings"]].count("telegram_checks_skipped") == 1


def test_a_network_error_on_getchat_skips_getchatmember(env):
    env.tg.get_chat = (None, None, "network: down")
    env.gj(*ALICE)
    assert env.tg.methods() == ["getMe", "getChat"]


def test_bot_checks_ask_about_the_bot_itself(env):
    env.gj(*ALICE)
    member_url = next(u for u in env.tg.urls if "getChatMember?" in u)
    assert f"chat_id={GID}" in member_url and f"user_id={BOT_ID}" in member_url


def test_person_only_next_step(env):
    env.tg.get_member = ({"ok": True, "result": {"status": "member"}}, 200, "")
    env.daemon_pid = 4242
    code, doc, _o, _e = env.gj(*ALICE)
    assert "make it an admin with the right to pin messages" in doc["next_step"]


def test_no_send_message_ever(env):
    env.gj(*ALICE, *BOB)
    env.gj(*ALICE, *BOB)
    env.gj("--member", "333:carol:user", "--dry-run")
    assert env.tg.sends == []
    assert "sendMessage" not in env.tg.methods()


# ===========================================================================
# the token is never printed
# ===========================================================================

@pytest.mark.parametrize("json_out", [True, False])
def test_token_never_printed(env, monkeypatch, json_out):
    from aipager.wizard import telegram_api

    def _leaky(url):
        env.tg.urls.append(url)
        return None, None, f"network: could not reach {url}"

    monkeypatch.setattr(telegram_api, "_http_json", _leaky)
    tail = ("--json",) if json_out else ()
    outputs = []
    for argv in [(*ALICE,), (*ALICE, "--dry-run"), ("--member", "5:x:nope"),
                 ("--member", "222:bob:admin")]:
        outputs.append(env.run("setup", "group", "--chat-id", str(GID), *argv, *tail))
    from aipager import setup_group

    def _boom(*a, **k):
        raise RuntimeError(f"broke with {TOKEN}")

    monkeypatch.setattr(setup_group, "plan_group", _boom)
    outputs.append(env.run("setup", "group", "--chat-id", str(GID), *BOB, *tail))
    for code, out, err in outputs:
        assert TOKEN not in out + err and SECRET not in out + err
    assert outputs[-1][0] == 1


# ===========================================================================
# plan_group, directly
# ===========================================================================

def _plan(scopes, *members, remove=(), label=None, root=None, block=False, moved=None):
    from aipager.setup_group import GroupRequest, MemberSpec, plan_group
    from aipager.wizard import group_rules
    req = GroupRequest(GID, tuple(MemberSpec(*m) for m in members), tuple(remove),
                       label, root, block)
    return plan_group(scopes, moved or {}, req, group_rules.load_roles_strict())


def _dm(cid=111):
    return Scope(chat_id=cid, kind="dm", label="owner DM",
                 members=(Member(id=cid, label="owner", role="owner"),))


def test_plan_new_group():
    plan = _plan([_dm()], (111, "alice", "owner"), (222, "bob", "user"))
    assert plan.changes == ["group"] and plan.existing is None
    assert plan.force_reasons == [] and plan.owner_grants == [111]
    assert plan.scope.root.endswith("group-group-1001234567890")


def test_plan_changes_in_order():
    g = Scope(chat_id=GID, kind="group", label="team", root="/srv/team",
              members=(Member(111, "alice", "owner"), Member(222, "bob", "user"),
                       Member(333, "carol", "user")))
    plan = _plan([_dm(), g], (222, "robert", "admin"), (444, "dave", "user"),
                 remove=(333,), label="crew", root="/srv/crew", block=True)
    assert plan.changes == ["label", "root", "deny_tools", "member_added",
                            "member_relabeled", "role", "member_removed"]
    assert plan.force_reasons == ["role_changed"]


def test_plan_unchanged():
    g = Scope(chat_id=GID, kind="group", label="team", root="/srv/team",
              members=(Member(111, "alice", "owner"),))
    plan = _plan([_dm(), g], (111, "alice", "owner"))
    assert plan.changes == [] and plan.scope == g and plan.pinned_roots == []


def test_plan_owner_grant_promotion_warns_and_audits():
    g = Scope(chat_id=GID, kind="group", label="team", root="/srv/team",
              members=(Member(111, "alice", "owner"), Member(222, "bob", "user")))
    plan = _plan([_dm(), g], (222, "bob", "owner"))
    assert plan.owner_grants == [222]
    assert plan.force_reasons == ["role_changed"]
    assert "owner_granted" in [w["code"] for w in plan.warnings]


@pytest.mark.parametrize("json_out", [True, False])
def test_a_token_typed_into_a_flag_is_never_printed(env, json_out):
    """An error message that quotes a flag's value (here --root) must not
    show the token pasted into it: the error line is scrubbed too."""
    tail = ("--json",) if json_out else ()
    code, out, err = env.run("setup", "group", "--chat-id", str(GID), *ALICE,
                             "--root", TOKEN, *tail)
    assert code == 2
    assert TOKEN not in out + err and SECRET not in out + err
    assert "bad_root" in out if json_out else "can't be a members' root folder" in err
