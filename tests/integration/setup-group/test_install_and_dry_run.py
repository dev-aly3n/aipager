"""design.md success criteria 8 and 10: the install must exist (exit 11
``not_set_up``, exit 1 ``config_malformed``, nothing written either way);
``--dry-run`` writes nothing, sends no reload, reports the would-be
change and exits with the real run's code."""

from __future__ import annotations

import pytest

from setup_group_support import (
    GROUP, OWNER, TOKEN, dm_scope, group_scope, m,
)

BASE = [(OWNER, "alice", "owner"), (222, "bob", "user")]


# ---- criterion 8: install state ----

@pytest.mark.parametrize("argv", [
    ("setup", "group", "--chat-id", str(GROUP), "--member=5:x:user"),
    ("setup", "detect-group", "--timeout", "0"),
])
def test_no_install_exits_11(env, argv):
    r = env.run(*argv, "--json")
    assert (r.code, r.json["error"]) == (11, "not_set_up")


@pytest.mark.parametrize("argv", [
    ("setup", "group", "--chat-id", str(GROUP), "--member=5:x:user"),
    ("setup", "detect-group", "--timeout", "0"),
])
def test_no_install_writes_nothing(env, argv):
    before = env.snapshot()
    env.run(*argv, "--json")
    assert env.snapshot() == before


@pytest.mark.parametrize("argv", [
    ("setup", "group", "--chat-id", str(GROUP), "--member=5:x:user"),
    ("setup", "detect-group", "--timeout", "0"),
])
def test_no_install_makes_no_bot_api_call(env, argv):
    env.run(*argv, "--json")
    assert env.tg.calls == []


def test_config_env_only_install_is_not_set_up(env):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(f"CLAUDE_TG_BOT_TOKEN={TOKEN}\n"
                              f"CLAUDE_TG_CHAT_ID={OWNER}\n")
    r = env.group_cmd(m(5, "x", "user"))
    assert (r.code, r.json["error"]) == (11, "not_set_up")


def test_config_env_only_fix_names_aipager_config(env):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(f"CLAUDE_TG_BOT_TOKEN={TOKEN}\n"
                              f"CLAUDE_TG_CHAT_ID={OWNER}\n")
    r = env.group_cmd(m(5, "x", "user"))
    assert "aipager config" in (r.json["fix"] or "")


def test_config_env_only_never_migrated(env):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(f"CLAUDE_TG_BOT_TOKEN={TOKEN}\n"
                              f"CLAUDE_TG_CHAT_ID={OWNER}\n")
    before = env.snapshot()
    env.group_cmd(m(5, "x", "user"))
    assert env.snapshot() == before


@pytest.mark.parametrize("text", [
    "scopes: [unclosed\n", "- just\n- a list\n", "bot_token: [1, 2]\n",
])
def test_malformed_config_exits_1(env, text):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text(text)
    r = env.group_cmd(m(5, "x", "user"))
    assert (r.code, r.json["error"]) == (1, "config_malformed")


def test_malformed_config_never_overwritten_even_with_force(env):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text("scopes: [unclosed\n")
    before = env.snapshot()
    env.group_cmd(m(5, "x", "user"), "--force")
    assert env.snapshot() == before


def test_malformed_config_detect_group_exits_1(env):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text("scopes: [unclosed\n")
    r = env.run("setup", "detect-group", "--timeout", "0", "--json")
    assert (r.code, r.json["error"]) == (1, "config_malformed")


def test_install_without_token_is_refused(env):
    env.write_config({"scopes": [dm_scope()]})
    r = env.group_cmd(m(5, "x", "user"))
    assert r.code in (1, 11) and env.group() is None


def test_not_set_up_wins_over_unknown_role(env):
    r = env.group_cmd(m(5, "x", "superuser"))
    assert r.json["error"] == "not_set_up"


def test_config_malformed_wins_over_policy_malformed(env):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text("scopes: [unclosed\n")
    env.write_policy("roles: [bad: yaml\n")
    r = env.group_cmd(m(5, "x", "user"))
    assert r.json["error"] == "config_malformed"


def test_error_document_has_every_key(env):
    from setup_group_support import GROUP_KEYS
    r = env.group_cmd(m(5, "x", "user"))
    assert set(r.json) == set(GROUP_KEYS)


def test_error_document_next_step_is_fix(env):
    r = env.group_cmd(m(5, "x", "user"))
    assert r.json["next_step"] == r.json["fix"]


def test_error_document_status_error_ok_false(env):
    r = env.group_cmd(m(5, "x", "user"))
    assert (r.json["status"], r.json["ok"], r.json["exit_code"]) == (
        "error", False, 11)


def test_members_null_when_stopped_before_planning(env):
    r = env.group_cmd(m(5, "x", "user"))
    assert r.json["members"] is None


def test_plain_error_goes_to_stderr_with_cross(env):
    r = env.group_cmd(m(5, "x", "user"), json_out=False)
    assert (r.code, r.out, r.err.lstrip().startswith("✗")) == (11, "", True)


# ---- criterion 10: dry run ----

@pytest.fixture
def grp(env):
    env.install(scopes=[dm_scope(), group_scope(BASE)])
    env.daemon_pid = 4242
    return env


def test_dry_run_new_group_writes_nothing(env):
    env.install()
    env.daemon_pid = 4242
    before = env.snapshot()
    env.group_cmd(m(5, "x", "user"), "--dry-run")
    assert env.snapshot() == before


def test_dry_run_new_group_status(env):
    env.install()
    r = env.group_cmd(m(5, "x", "user"), "--dry-run")
    assert (r.code, r.json["status"], r.json["dry_run"]) == (
        0, "dry_run", True)


def test_dry_run_reports_would_be_changed(env):
    env.install()
    r = env.group_cmd(m(5, "x", "user"), "--dry-run")
    assert r.json["changed"][0] == "group"


def test_dry_run_reports_would_be_members(grp):
    r = grp.group_cmd(m(333, "carol", "user"), "--dry-run")
    assert [x["id"] for x in r.json["members"]] == [OWNER, 222, 333]


def test_dry_run_sends_no_reload(grp):
    grp.group_cmd(m(333, "carol", "user"), "--dry-run")
    assert grp.reloads == []


def test_dry_run_writes_no_audit(grp):
    grp.group_cmd(m(444, "dave", "owner"), "--force", "--dry-run")
    assert grp.audit() == []


def test_dry_run_same_changed_as_real_run(grp):
    args = (m(333, "carol", "user"), m(222, "robert", "user"),
            "--label", "crew", "--block-edits")
    dry = grp.group_cmd(*args, "--dry-run").json
    real = grp.group_cmd(*args).json
    assert (dry["changed"], dry["members"], dry["member_changes"]) == (
        real["changed"], real["members"], real["member_changes"])


def test_dry_run_same_root_as_real_run(env):
    env.install()
    dry = env.group_cmd(m(5, "x", "user"), "--dry-run").json
    real = env.group_cmd(m(5, "x", "user")).json
    assert dry["root"] == real["root"]


@pytest.mark.parametrize("args,code", [
    ((m(222, "bob", "admin"),), 6),
    ((m(444, "dave", "owner"),), 6),
    (("--remove-member", str(OWNER)), 6),
    ((m(5, "x", "superuser"),), 2),
    ((m(777000, "x", "user"),), 2),
    (("--root", "/"), 2),
    (("--root", "~/.local/bin/x"), 6),
    ((m(333, "carol", "user"),), 0),
    ((m(222, "bob", "user"),), 0),
])
def test_dry_run_exit_code_parity(grp, args, code):
    dry = grp.group_cmd(*args, "--dry-run")
    assert dry.code == code


@pytest.mark.parametrize("args", [
    (m(222, "bob", "admin"),), (m(444, "dave", "owner"),),
    ("--remove-member", str(OWNER)), (m(5, "x", "superuser"),),
])
def test_dry_run_error_matches_real_error(grp, args):
    dry = grp.group_cmd(*args, "--dry-run").json
    real = grp.group_cmd(*args).json
    assert (dry["error"], dry["force_reasons"]) == (
        real["error"], real["force_reasons"])


def test_dry_run_last_member_exits_1(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    r = env.group_cmd("--remove-member", "222", "--dry-run")
    assert (r.code, r.json["error"]) == (1, "last_member")


def test_dry_run_not_set_up_exits_11(env):
    r = env.group_cmd(m(5, "x", "user"), "--dry-run")
    assert r.code == 11


def test_dry_run_with_force_writes_nothing(grp):
    before = grp.raw()
    r = grp.group_cmd(m(222, "bob", "admin"), "--force", "--dry-run")
    assert (r.code, grp.raw()) == (0, before)


def test_dry_run_runs_bot_checks(grp):
    grp.group_cmd(m(333, "carol", "user"), "--dry-run")
    assert "getChat" in grp.tg.methods


def test_dry_run_pinned_roots_preview(env):
    """The preview of other scopes that get an explicit root equals what
    the real write pins."""
    env.install()
    dry = env.group_cmd(m(5, "x", "user"), "--dry-run").json
    env.group_cmd(m(5, "x", "user"))
    dm = next(s for s in env.config()["scopes"] if s["chat_id"] == OWNER)
    pinned = {p["chat_id"]: p["root"] for p in dry["pinned_roots"]}
    assert pinned.get(OWNER) == dm.get("root")


def test_dry_run_unchanged_status(grp):
    r = grp.group_cmd(m(222, "bob", "user"), "--dry-run")
    assert r.json["changed"] == [] and r.code == 0


def test_dry_run_plain_writes_nothing(grp):
    before = grp.raw()
    r = grp.group_cmd(m(333, "carol", "user"), "--dry-run", json_out=False)
    assert (r.code, grp.raw()) == (0, before)
