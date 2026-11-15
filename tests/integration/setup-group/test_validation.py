"""design.md success criteria 6, 7 and 9: member, role, label, chat id and
root validation; flag refusals per form; the DM form's changed refusals;
``--token`` never echoed. Every refusal writes nothing and never
reaches Telegram before the syntax checks."""

from __future__ import annotations

import pytest

from setup_group_support import (
    GROUP, OWNER, SECRET, TOKEN, assert_no_secret, dm_scope, group_scope, m,
)


@pytest.fixture
def inst(env):
    env.install()
    return env


def _refused(env, r, code, error):
    return (r.code, r.json["error"]) == (code, error)


# ---- criterion 6: shared ids, duplicates, roles ----

@pytest.mark.parametrize("sid", [1087968824, 136817688, 777000])
def test_shared_sender_id_refused(inst, sid):
    r = inst.group_cmd(m(sid, "x", "user"))
    assert _refused(inst, r, 2, "shared_sender_id")


@pytest.mark.parametrize("sid", [1087968824, 136817688, 777000])
def test_shared_sender_id_writes_nothing(inst, sid):
    before = inst.raw()
    inst.group_cmd(m(sid, "x", "user"))
    assert inst.raw() == before


@pytest.mark.parametrize("sid", [1087968823, 1087968825, 777001, 776999])
def test_neighbours_of_shared_ids_are_accepted(inst, sid):
    assert inst.group_cmd(m(sid, "x", "user")).code == 0


def test_shared_sender_id_refused_in_an_existing_group(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    r = env.group_cmd(m(777000, "tg", "user"))
    assert _refused(env, r, 2, "shared_sender_id")


def test_duplicate_member_ids(inst):
    r = inst.group_cmd(m(5, "a", "user"), m(5, "b", "user"))
    assert _refused(inst, r, 2, "duplicate_member")


def test_duplicate_labels(inst):
    r = inst.group_cmd(m(5, "a", "user"), m(6, "a", "user"))
    assert _refused(inst, r, 2, "duplicate_label")


def test_duplicate_labels_after_strip(inst):
    r = inst.group_cmd(m(5, "a", "user"), m(6, " a ", "user"))
    assert r.code == 2


def test_unknown_role(inst):
    r = inst.group_cmd(m(5, "x", "superuser"))
    assert _refused(inst, r, 2, "unknown_role")


@pytest.mark.parametrize("role", ["owner", "admin", "user", "read_only"])
def test_unknown_role_message_names_the_builtins(inst, role):
    r = inst.group_cmd(m(5, "x", "superuser"))
    assert role in r.json["message"]


def test_unknown_role_writes_nothing(inst):
    before = inst.raw()
    inst.group_cmd(m(5, "x", "superuser"))
    assert inst.raw() == before


def test_role_names_are_case_sensitive(inst):
    assert inst.group_cmd(m(5, "x", "Admin")).code == 2


def test_custom_role_accepted(inst):
    inst.write_policy("roles:\n  reviewer:\n    can_prompt: true\n")
    r = inst.group_cmd(m(5, "x", "reviewer"))
    assert (r.code, inst.group()["members"][0]["role"]) == (0, "reviewer")


def test_custom_role_named_in_unknown_role_message(inst):
    inst.write_policy("roles:\n  reviewer:\n    can_prompt: true\n")
    r = inst.group_cmd(m(5, "x", "nope"))
    assert "reviewer" in r.json["message"]


def test_custom_role_from_policy_d_accepted(inst):
    import aipager.policy as p
    from pathlib import Path
    d = Path(p.POLICY_D_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / "extra.yaml").write_text("roles:\n  tester:\n    can_prompt: true\n")
    assert inst.group_cmd(m(5, "x", "tester")).code == 0


def test_malformed_policy_exits_1(inst):
    inst.write_policy("roles: [this is: not valid\n")
    r = inst.group_cmd(m(5, "x", "user"))
    assert _refused(inst, r, 1, "policy_malformed")


def test_malformed_policy_fix_names_validate(inst):
    inst.write_policy("roles: [this is: not valid\n")
    r = inst.group_cmd(m(5, "x", "user"))
    assert "aipager policy validate" in r.json["fix"]


def test_policy_malformed_wins_over_unknown_role(inst):
    inst.write_policy("roles: [this is: not valid\n")
    r = inst.group_cmd(m(5, "x", "superuser"))
    assert r.json["error"] == "policy_malformed"


def test_unknown_role_wins_over_needs_force(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    r = env.group_cmd(m(222, "bob", "admin"), m(5, "x", "superuser"))
    assert r.json["error"] == "unknown_role"


# ---- member syntax: partitions and boundaries ----

@pytest.mark.parametrize("spec", [
    "5", "5:x", ":x:user", "5::user", "5:x:", "::", "",
])
def test_member_missing_parts_is_bad_member_or_label(inst, spec):
    r = inst.group_cmd(f"--member={spec}")
    assert r.code == 2 and r.json["error"] in (
        "bad_member", "bad_label", "bad_member_id")


@pytest.mark.parametrize("spec", ["5", "5:x", "nocolons"])
def test_member_without_three_parts_is_bad_member(inst, spec):
    r = inst.group_cmd(f"--member={spec}")
    assert _refused(inst, r, 2, "bad_member")


@pytest.mark.parametrize("uid", ["0", "-5", "abc", "1.5", "0x10", " "])
def test_bad_member_id(inst, uid):
    r = inst.group_cmd(f"--member={uid}:x:user")
    assert r.code == 2 and r.json["error"] in ("bad_member_id", "bad_member")


def test_member_id_one_is_accepted(inst):
    assert inst.group_cmd(m(1, "x", "user")).code == 0


def test_large_member_id_is_accepted(inst):
    # Telegram user ids exceed 2**32 today.
    assert inst.group_cmd(m(8_000_000_000, "x", "user")).code == 0


def test_label_holding_colons_is_kept_whole(inst):
    r = inst.group_cmd("--member=5:a:b:c:user")
    assert (r.code, inst.group()["members"][0]["label"]) == (0, "a:b:c")


def test_label_with_url_like_colons(inst):
    r = inst.group_cmd("--member=5:https://x:user")
    assert (r.code, inst.group()["members"][0]) == (
        0, {"id": 5, "label": "https://x", "role": "user"})


@pytest.mark.parametrize("label", ["a\x07b", "a\nb", "tab\there"])
def test_bad_member_label(inst, label):
    r = inst.group_cmd(f"--member=5:{label}:user")
    assert _refused(inst, r, 2, "bad_label")


def test_blank_member_label_refused(inst):
    r = inst.group_cmd("--member=5:   :user")
    assert r.code == 2 and r.json["error"] in ("bad_label", "bad_member")


def test_unicode_label_accepted(inst):
    r = inst.group_cmd(m(5, "élodie", "user"))
    assert (r.code, inst.group()["members"][0]["label"]) == (0, "élodie")


# ---- --chat-id: partitions and boundaries ----

@pytest.mark.parametrize("cid", ["-1", "-123456789012345"])
def test_chat_id_boundaries_accepted(inst, cid):
    assert inst.group_cmd(m(5, "x", "user"), chat=cid).code == 0


@pytest.mark.parametrize("cid", [
    "-1234567890123456", "0", "-0x1", "-1e5", "--5", "- 5", "abc", "-",
])
def test_chat_id_refused(inst, cid):
    r = inst.run("setup", "group", f"--chat-id={cid}", m(5, "x", "user"),
                 "--json")
    assert _refused(inst, r, 2, "bad_chat_id")


def test_positive_chat_id_fix_points_at_dm_setup(inst):
    r = inst.group_cmd(m(5, "x", "user"), chat=12345)
    assert (r.json["error"], "aipager setup --chat-id" in r.json["fix"]) == (
        "bad_chat_id", True)


def test_missing_chat_id(inst):
    r = inst.run("setup", "group", m(5, "x", "user"), "--json")
    assert r.code == 2 and r.json["error"] in ("usage", "bad_chat_id")


def test_syntax_refusal_makes_no_bot_api_call(inst):
    inst.group_cmd(m(5, "x", "user"), chat=0)
    assert inst.tg.calls == []


def test_syntax_refusal_needs_no_install(env):
    """Syntax checks come before any file read: no aipager.yaml still
    gives bad_chat_id, not not_set_up."""
    r = env.group_cmd(m(5, "x", "user"), chat=0)
    assert r.json["error"] == "bad_chat_id"


# ---- --label ----

@pytest.mark.parametrize("label", ["", "   ", "a\x1bb"])
def test_bad_group_label(inst, label):
    r = inst.run("setup", "group", "--chat-id", GROUP, m(5, "x", "user"),
                 f"--label={label}", "--json")
    assert _refused(inst, r, 2, "bad_label")


def test_group_label_used(inst):
    inst.group_cmd(m(5, "x", "user"), "--label", "team")
    assert inst.group()["label"] == "team"


def test_group_label_sets_the_default_root(inst):
    import os
    inst.group_cmd(m(5, "x", "user"), "--label", "team")
    assert inst.group()["root"] == os.path.expanduser(
        "~/aipager-users/group-team")


# ---- criterion 7: root ----

def test_root_slash_is_bad_root(inst):
    r = inst.group_cmd(m(5, "x", "user"), "--root", "/")
    assert _refused(inst, r, 2, "bad_root")


def test_root_home_itself_is_bad_root(inst):
    r = inst.group_cmd(m(5, "x", "user"), "--root", "~")
    assert _refused(inst, r, 2, "bad_root")


def test_root_bad_writes_nothing(inst):
    before = inst.raw()
    inst.group_cmd(m(5, "x", "user"), "--root", "/")
    assert inst.raw() == before


@pytest.mark.parametrize("sub", [".local/bin/x", "bin/x", ".config/systemd/x",
                                 ".config/autostart/x", ".local/bin"])
def test_root_code_loading_needs_force(inst, sub):
    r = inst.group_cmd(m(5, "x", "user"), "--root", f"~/{sub}")
    assert (r.code, r.json["force_reasons"]) == (6, ["root_code_loading"])


def test_root_code_loading_with_force_is_written(inst):
    import os
    r = inst.group_cmd(m(5, "x", "user"), "--root", "~/.local/bin/x",
                       "--force")
    assert (r.code, inst.group()["root"]) == (
        0, os.path.expanduser("~/.local/bin/x"))


def test_root_beside_code_loading_folder_needs_no_force(inst):
    r = inst.group_cmd(m(5, "x", "user"), "--root", "~/.local/binx")
    assert r.code == 0


def test_root_is_stored_absolute(inst):
    import os
    inst.group_cmd(m(5, "x", "user"), "--root", "~/work/team")
    assert inst.group()["root"] == os.path.expanduser("~/work/team")


def test_root_overlap_with_another_scope_needs_force(env):
    other = env.home / "shared"
    env.install(scopes=[dm_scope(root=str(other))])
    r = env.group_cmd(m(5, "x", "user"), "--root", str(other / "sub"))
    assert (r.code, r.json["force_reasons"]) == (6, ["root_overlap"])


def test_root_equal_to_another_scope_root_needs_force(env):
    other = env.home / "shared"
    env.install(scopes=[dm_scope(root=str(other))])
    r = env.group_cmd(m(5, "x", "user"), "--root", str(other))
    assert r.json["force_reasons"] == ["root_overlap"]


def test_root_containing_another_scope_root_needs_force(env):
    other = env.home / "shared" / "inner"
    env.install(scopes=[dm_scope(root=str(other))])
    r = env.group_cmd(m(5, "x", "user"), "--root", str(env.home / "shared"))
    assert r.json["force_reasons"] == ["root_overlap"]


def test_root_sibling_prefix_is_no_overlap(env):
    env.install(scopes=[dm_scope(root=str(env.home / "shared"))])
    r = env.group_cmd(m(5, "x", "user"), "--root", str(env.home / "shared2"))
    assert r.code == 0


def test_root_overlap_with_another_groups_default_root(env):
    """A first group gets the default root, written explicitly; a second
    group aimed at that same folder overlaps it."""
    env.install()
    env.group_cmd(m(5, "x", "user"))
    first_root = env.group()["root"]
    r = env.group_cmd(m(6, "y", "user"), "--root", first_root, chat=-1009)
    assert r.json["force_reasons"] == ["root_overlap"]


# ---- criterion 9: flag refusals per form ----

@pytest.mark.parametrize("extra", [
    ("--token-file", "f"), ("--token-stdin",), ("--role", "user"),
    ("--service",), ("--timeout", "5"),
])
def test_group_refuses_flag(inst, extra):
    r = inst.group_cmd(m(5, "x", "user"), *extra)
    assert r.code == 2


@pytest.mark.parametrize("extra", [
    ("--token-file", "f"), ("--token-stdin",), ("--role", "user"),
    ("--service",), ("--timeout", "5"),
])
def test_group_flag_refusal_writes_nothing(inst, extra):
    before = inst.raw()
    inst.group_cmd(m(5, "x", "user"), *extra)
    assert inst.raw() == before


def test_group_token_file_refusal_reads_no_file(inst, tmp_path):
    """--token-file is refused, never read: the file may hold anything."""
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    r = inst.group_cmd(m(5, "x", "user"), "--token-file", f)
    assert_no_secret(r.out + r.err, "output")


@pytest.mark.parametrize("flag", ["--member", "--remove-member", "--label",
                                  "--root"])
def test_dm_form_refuses_group_flags(inst, tmp_path, flag):
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    val = "5:x:user" if flag == "--member" else (
        "5" if flag == "--remove-member" else "x")
    r = inst.run("setup", "--token-file", f, "--chat-id", "5", flag, val,
                 "--json")
    assert (r.code, r.json["error"]) == (2, "usage")


def test_dm_form_refuses_block_edits(inst, tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    r = inst.run("setup", "--token-file", f, "--chat-id", "5",
                 "--block-edits", "--json")
    assert (r.code, r.json["error"]) == (2, "usage")


def test_dm_form_negative_chat_id_names_setup_group(inst, tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    r = inst.run("setup", "--token-file", f, "--chat-id", "-100", "--json")
    d = r.json
    assert (r.code, d["error"], "aipager setup group" in
            f"{d['message']} {d['fix']}") == (2, "bad_chat_id", True)


def test_dm_form_negative_chat_id_plain_names_setup_group(inst, tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    r = inst.run("setup", "--token-file", f, "--chat-id", "-100")
    assert r.code == 2 and "aipager setup group" in r.err


def test_detect_chat_refuses_group_flags(inst, tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN)
    r = inst.run("setup", "detect-chat", "--token-file", f,
                 "--member", "5:x:user", "--json")
    assert r.code == 2


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_on_command_line_refused(inst, flag):
    r = inst.group_cmd(m(5, "x", "user"), flag, TOKEN)
    assert _refused(inst, r, 2, "token_on_command_line")


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_on_command_line_never_printed(inst, flag):
    r = inst.group_cmd(m(5, "x", "user"), flag, TOKEN)
    assert_no_secret(r.out + r.err + r.log, "output")


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_equals_form_never_printed_plain(inst, flag):
    r = inst.run("setup", "group", "--chat-id", GROUP, m(5, "x", "user"),
                 f"{flag}={TOKEN}")
    assert r.code == 2
    assert_no_secret(r.out + r.err + r.log, "output")


def test_token_on_detect_group_refused_and_never_printed(inst):
    r = inst.run("setup", "detect-group", "--token", TOKEN, "--json")
    assert r.code == 2
    assert_no_secret(r.out + r.err + r.log, "output")


def test_token_typed_into_member_flag_never_printed(inst):
    r = inst.group_cmd(f"--member={TOKEN}")
    assert r.code == 2
    assert_no_secret(r.out + r.err + r.log, "output")


def test_token_typed_into_label_flag_never_printed(inst):
    r = inst.group_cmd(m(5, "x", "user"), f"--label={TOKEN}")
    assert_no_secret(r.out + r.err + r.log, "output")


def test_token_typed_into_chat_id_never_printed(inst):
    r = inst.run("setup", "group", f"--chat-id={TOKEN}", m(5, "x", "user"),
                 "--json")
    assert r.code == 2
    assert_no_secret(r.out + r.err + r.log, "output")


def test_secret_part_alone_in_root_never_printed(inst):
    r = inst.group_cmd(m(5, "x", "user"), f"--root=/{SECRET}")
    assert_no_secret(r.out + r.err, "output")


@pytest.mark.parametrize("abbr", ["--mem", "--remove", "--lab", "--block"])
def test_abbreviated_flags_refused(inst, abbr):
    r = inst.run("setup", "group", "--chat-id", GROUP, abbr, "5:x:user")
    assert r.code == 2


def test_unknown_action_refused(inst):
    r = inst.run("setup", "groups", "--chat-id", GROUP)
    assert r.code == 2


def test_help_names_group_flags(env):
    r = env.run("setup", "--help")
    assert all(f in r.out for f in ("--member", "--remove-member",
                                    "--label", "--root", "--block-edits"))


def test_no_prompt_without_a_terminal_on_refusal(inst):
    # Traps in conftest fail the test if any prompt is reached.
    assert inst.group_cmd(m(5, "x", "nope")).code == 2


def test_owner_member_of_dm_listed_as_known(inst):
    assert inst.group_cmd(m(OWNER, "alice", "owner")).code == 0
