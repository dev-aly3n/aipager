"""design.md success criteria 3, 4 and 5: extending a group, the force
rules (role change, owner granted / demoted / removed) and the last
member rule; plus relabel, remove, unknown member removal, supergroup
moved ids and custom owner-level roles (entrypoints.md semantics)."""

from __future__ import annotations

import pytest

from setup_group_support import GROUP, OWNER, dm_scope, group_scope, m

BASE = [(OWNER, "alice", "owner"), (222, "bob", "user")]


@pytest.fixture
def grp(env):
    env.install(scopes=[dm_scope(), group_scope(BASE)])
    env.daemon_pid = 4242
    return env


def _members(env, chat=GROUP):
    return [(x["id"], x["label"], x["role"])
            for x in env.group(chat)["members"]]


# ---- criterion 3: add a member ----

def test_add_member_exits_zero(grp):
    assert grp.group_cmd(m(333, "carol", "admin")).code == 0


def test_add_member_status_updated(grp):
    assert grp.group_cmd(m(333, "carol", "admin")).json["status"] == "updated"


def test_add_member_changed(grp):
    assert grp.group_cmd(m(333, "carol", "admin")).json["changed"] == [
        "member_added"]


def test_add_member_keeps_the_others(grp):
    grp.group_cmd(m(333, "carol", "admin"))
    assert _members(grp) == BASE + [(333, "carol", "admin")]


def test_add_member_reload_reloaded(grp):
    r = grp.group_cmd(m(333, "carol", "admin"))
    assert r.json["daemon"]["reload"] == "reloaded"


def test_add_member_outcome_reloaded_live(grp):
    r = grp.group_cmd(m(333, "carol", "admin"))
    assert r.json["daemon"]["outcome"] == "reloaded_live"


def test_add_member_exactly_one_reload(grp):
    grp.group_cmd(m(333, "carol", "admin"))
    assert grp.reloads == [1]


def test_add_two_members_appended_in_given_order(grp):
    grp.group_cmd(m(444, "dan", "user"), m(333, "carol", "user"))
    assert [x[0] for x in _members(grp)] == [OWNER, 222, 444, 333]


def test_add_keeps_the_group_label(grp):
    grp.group_cmd(m(333, "carol", "user"))
    assert grp.group()["label"] == "team"


def test_add_keeps_other_scope_keys(env):
    env.install(scopes=[dm_scope(), group_scope(
        BASE, deny_tools=["WebFetch"], root=str(env.home / "teamroot"))])
    env.group_cmd(m(333, "carol", "user"))
    g = env.group()
    assert (g["deny_tools"], g["root"]) == (["WebFetch"],
                                            str(env.home / "teamroot"))


def test_add_keeps_per_member_tool_lists(env):
    scope = group_scope(BASE)
    scope["members"][1]["deny_tools"] = ["WebFetch"]
    env.install(scopes=[dm_scope(), scope])
    env.group_cmd(m(333, "carol", "user"))
    assert env.group()["members"][1].get("deny_tools") == ["WebFetch"]


def test_relabel_needs_no_force(grp):
    r = grp.group_cmd(m(222, "robert", "user"))
    assert (r.code, r.json["changed"]) == (0, ["member_relabeled"])


def test_relabel_written(grp):
    grp.group_cmd(m(222, "robert", "user"))
    assert _members(grp)[1] == (222, "robert", "user")


def test_relabel_onto_a_taken_label_is_refused(grp):
    before = grp.raw()
    r = grp.group_cmd(m(222, "alice", "user"))
    assert (r.code, r.json["error"], grp.raw()) == (
        2, "duplicate_label", before)


def test_new_member_with_a_label_taken_in_the_group_is_refused(grp):
    r = grp.group_cmd(m(333, "bob", "user"))
    assert (r.code, r.json["error"]) == (2, "duplicate_label")


def test_label_rename_of_group(grp):
    r = grp.group_cmd("--label", "crew")
    assert (r.code, r.json["changed"], grp.group()["label"]) == (
        0, ["label"], "crew")


def test_block_edits_adds_write_and_edit(grp):
    r = grp.group_cmd("--block-edits")
    assert (r.json["changed"], sorted(grp.group()["deny_tools"])) == (
        ["deny_tools"], ["Edit", "Write"])


def test_block_edits_again_is_unchanged(grp):
    grp.group_cmd("--block-edits")
    before = grp.raw()
    r = grp.group_cmd("--block-edits")
    assert (r.json["status"], grp.raw()) == ("unchanged", before)


def test_block_edits_keeps_existing_denies(env):
    env.install(scopes=[dm_scope(), group_scope(
        BASE, deny_tools=["WebFetch", "Write"])])
    env.group_cmd("--block-edits")
    assert sorted(env.group()["deny_tools"]) == ["Edit", "WebFetch", "Write"]


def test_changed_order_follows_the_documented_order(grp):
    from setup_group_support import CHANGED_ORDER
    r = grp.group_cmd("--label", "crew", "--block-edits",
                      m(333, "carol", "user"), m(222, "robert", "user"))
    ch = r.json["changed"]
    assert ch == sorted(ch, key=CHANGED_ORDER.index)


# ---- criterion 4: role change needs --force ----

def test_role_change_without_force_exits_6(grp):
    assert grp.group_cmd(m(222, "bob", "admin")).code == 6


def test_role_change_without_force_error(grp):
    assert grp.group_cmd(m(222, "bob", "admin")).json["error"] == "needs_force"


def test_role_change_without_force_reason(grp):
    assert grp.group_cmd(m(222, "bob", "admin")).json["force_reasons"] == [
        "role_changed"]


def test_role_change_without_force_file_untouched(grp):
    before = grp.raw()
    grp.group_cmd(m(222, "bob", "admin"))
    assert grp.raw() == before


def test_role_change_without_force_no_reload(grp):
    grp.group_cmd(m(222, "bob", "admin"))
    assert grp.reloads == []


def test_role_change_without_force_fix_names_force(grp):
    assert "--force" in grp.group_cmd(m(222, "bob", "admin")).json["fix"]


def test_role_change_with_force_exits_0(grp):
    assert grp.group_cmd(m(222, "bob", "admin"), "--force").code == 0


def test_role_change_with_force_written(grp):
    grp.group_cmd(m(222, "bob", "admin"), "--force")
    assert _members(grp)[1] == (222, "bob", "admin")


def test_role_change_with_force_member_change(grp):
    r = grp.group_cmd(m(222, "bob", "admin"), "--force")
    assert r.json["member_changes"] == [{
        "id": 222, "change": "role", "label": "bob", "role": "admin",
        "previous_role": "user"}]


def test_role_downgrade_also_needs_force(grp):
    grp.install(scopes=[dm_scope(), group_scope(
        [(OWNER, "alice", "owner"), (222, "bob", "admin")])])
    r = grp.group_cmd(m(222, "bob", "read_only"))
    assert (r.code, r.json["force_reasons"]) == (6, ["role_changed"])


def test_role_change_combined_with_an_add_writes_nothing(grp):
    before = grp.raw()
    r = grp.group_cmd(m(333, "carol", "user"), m(222, "bob", "admin"))
    assert (r.code, grp.raw()) == (6, before)


# ---- criterion 5: owner granted / removed / demoted, last member ----

def test_new_owner_needs_force(grp):
    r = grp.group_cmd(m(444, "dave", "owner"))
    assert (r.code, r.json["force_reasons"]) == (6, ["owner_granted"])


def test_new_owner_without_force_writes_nothing(grp):
    before = grp.raw()
    grp.group_cmd(m(444, "dave", "owner"))
    assert grp.raw() == before


def test_new_owner_with_force_written(grp):
    r = grp.group_cmd(m(444, "dave", "owner"), "--force")
    assert (r.code, _members(grp)[-1]) == (0, (444, "dave", "owner"))


def test_new_owner_with_force_warns_owner_granted(grp):
    r = grp.group_cmd(m(444, "dave", "owner"), "--force")
    assert "owner_granted" in r.warning_codes()


def test_new_owner_with_force_writes_an_audit_record(grp):
    grp.group_cmd(m(444, "dave", "owner"), "--force")
    assert [a.get("user_id") for a in grp.audit()
            if a.get("action") == "grant-owner" or
            "grant-owner" in str(a)] == [444]


def test_new_owner_audit_record_session_is_config(grp):
    grp.group_cmd(m(444, "dave", "owner"), "--force")
    rec = [a for a in grp.audit() if a.get("user_id") == 444]
    assert rec and rec[0].get("session") == "(config)"


def test_owner_of_another_group_needs_no_force(env):
    other = group_scope([(555, "erin", "owner")], chat_id=-1009,
                        label="other")
    env.install(scopes=[dm_scope(), group_scope(BASE), other])
    r = env.group_cmd(m(555, "erin", "owner"))
    assert (r.code, r.json["force_reasons"]) == (0, [])


def test_user_elsewhere_granted_owner_needs_force(env):
    other = group_scope([(555, "erin", "user")], chat_id=-1009,
                        label="other")
    env.install(scopes=[dm_scope(), group_scope(BASE), other])
    r = env.group_cmd(m(555, "erin", "owner"))
    assert r.json["force_reasons"] == ["owner_granted"]


def test_new_admin_needs_no_force(grp):
    assert grp.group_cmd(m(444, "dave", "admin")).code == 0


def test_custom_bypass_role_counts_as_owner_level(grp):
    grp.write_policy("roles:\n  boss:\n    bypass_safety: true\n")
    r = grp.group_cmd(m(444, "dave", "boss"))
    assert (r.code, r.json["force_reasons"]) == (6, ["owner_granted"])


def test_custom_role_without_bypass_needs_no_force(grp):
    grp.write_policy("roles:\n  dev:\n    can_prompt: true\n")
    assert grp.group_cmd(m(444, "dave", "dev")).code == 0


def test_promote_member_to_owner_reasons(grp):
    r = grp.group_cmd(m(222, "bob", "owner"))
    assert (r.code, "role_changed" in r.json["force_reasons"]) == (6, True)


def test_promote_member_to_owner_with_force_audits(grp):
    grp.group_cmd(m(222, "bob", "owner"), "--force")
    assert [a.get("user_id") for a in grp.audit()] == [222]


def test_remove_owner_needs_force(grp):
    r = grp.group_cmd("--remove-member", OWNER)
    assert (r.code, r.json["force_reasons"]) == (6, ["owner_removed"])


def test_remove_owner_without_force_untouched(grp):
    before = grp.raw()
    grp.group_cmd("--remove-member", OWNER)
    assert grp.raw() == before


def test_remove_owner_with_force(grp):
    r = grp.group_cmd("--remove-member", OWNER, "--force")
    assert (r.code, [x[0] for x in _members(grp)]) == (0, [222])


def test_remove_owner_with_force_keeps_dm_scope(grp):
    grp.group_cmd("--remove-member", OWNER, "--force")
    assert any(s["chat_id"] == OWNER for s in grp.config()["scopes"])


def test_demote_owner_needs_force(grp):
    r = grp.group_cmd(m(OWNER, "alice", "admin"))
    assert (r.code, set(r.json["force_reasons"])) == (
        6, {"role_changed", "owner_demoted"})


def test_demote_owner_with_force(grp):
    grp.group_cmd(m(OWNER, "alice", "admin"), "--force")
    assert _members(grp)[0] == (OWNER, "alice", "admin")


def test_demote_custom_bypass_role_to_user_is_owner_demoted(env):
    env.write_policy("roles:\n  boss:\n    bypass_safety: true\n")
    env.install(scopes=[dm_scope(), group_scope(
        [(OWNER, "alice", "owner"), (222, "bob", "boss")])])
    r = env.group_cmd(m(222, "bob", "user"))
    assert "owner_demoted" in r.json["force_reasons"]


def test_remove_plain_member_needs_no_force(grp):
    r = grp.group_cmd("--remove-member", 222)
    assert (r.code, r.json["changed"], [x[0] for x in _members(grp)]) == (
        0, ["member_removed"], [OWNER])


def test_remove_member_change_entry(grp):
    r = grp.group_cmd("--remove-member", 222)
    assert [c["change"] for c in r.json["member_changes"]] == ["removed"]


def test_remove_non_member_warns_and_changes_nothing(grp):
    before = grp.raw()
    r = grp.group_cmd("--remove-member", 999)
    assert (r.code, r.json["status"], "not_a_member" in r.warning_codes(),
            grp.raw()) == (0, "unchanged", True, before)


def test_remove_last_member_exits_1(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    r = env.group_cmd("--remove-member", 222)
    assert (r.code, r.json["error"]) == (1, "last_member")


def test_remove_last_member_with_force_still_refused(env):
    env.install(scopes=[dm_scope(), group_scope([(OWNER, "alice", "owner")])])
    before = env.raw()
    r = env.group_cmd("--remove-member", OWNER, "--force")
    assert (r.code, r.json["error"], env.raw()) == (1, "last_member", before)


def test_remove_all_members_at_once_is_last_member(grp):
    r = grp.group_cmd("--remove-member", OWNER, "--remove-member", 222,
                      "--force")
    assert (r.code, r.json["error"]) == (1, "last_member")


def test_remove_all_but_add_one_is_allowed(grp):
    r = grp.group_cmd("--remove-member", 222, m(333, "carol", "user"))
    assert (r.code, [x[0] for x in _members(grp)]) == (0, [OWNER, 333])


def test_last_member_fix_points_at_aipager_config(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    r = env.group_cmd("--remove-member", 222)
    assert "aipager config" in r.json["fix"]


def test_last_member_wins_over_needs_force(env):
    # Priority: last_member before needs_force.
    env.install(scopes=[dm_scope(), group_scope([(OWNER, "alice", "owner")])])
    r = env.group_cmd("--remove-member", OWNER)
    assert r.json["error"] == "last_member"


def test_same_id_in_member_and_remove_member_is_usage(grp):
    r = grp.group_cmd(m(222, "bob", "user"), "--remove-member", 222)
    assert (r.code, r.json["error"]) == (2, "usage")


def test_new_group_needs_a_member(env):
    env.install()
    before = env.raw()
    r = env.group_cmd()
    assert (r.code, r.json["error"], env.raw()) == (2, "usage", before)


def test_new_group_with_only_remove_member_is_refused(env):
    env.install()
    r = env.group_cmd("--remove-member", 5)
    assert r.code != 0 and env.group() is None


def test_new_group_without_manager_warns_no_manager(env):
    env.install()
    r = env.group_cmd(m(222, "bob", "user"))
    assert (r.code, "no_manager" in r.warning_codes()) == (0, True)


def test_new_group_with_owner_no_no_manager_warning(env):
    env.install()
    r = env.group_cmd(m(OWNER, "alice", "owner"))
    assert "no_manager" not in r.warning_codes()


def test_dm_chat_id_target_is_not_a_group_scope(env):
    """A group command aimed at a DM scope's id is refused. A DM id is
    positive, so the syntax check refuses first (bad_chat_id); either
    way nothing is written."""
    env.install()
    before = env.raw()
    r = env.group_cmd(m(222, "bob", "user"), chat=OWNER)
    assert (r.code, env.raw()) == (2, before)


def test_negative_dm_scope_id_is_not_a_group_scope(env):
    """A DM-kind scope stored under a negative id (hand edit) is refused
    with exit 1 not_a_group_scope."""
    env.install(scopes=[dm_scope(), dm_scope(uid=-5005, label="odd")])
    r = env.group_cmd(m(222, "bob", "user"), chat=-5005)
    assert (r.code, r.json["error"]) == (1, "not_a_group_scope")


# ---- supergroup upgrade: the other id ----

@pytest.fixture
def moved(env):
    env.install(scopes=[dm_scope(), group_scope(BASE, chat_id=-1004)],
                extra={"chat_migrations": {-1003: -1004}})
    return env


def test_old_id_extends_the_moved_group(moved):
    r = moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert (r.code, [x[0] for x in _members(moved, -1004)]) == (
        0, [OWNER, 222, 333])


def test_old_id_never_creates_a_second_scope(moved):
    moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert moved.group(-1003) is None


def test_old_id_warns_group_moved(moved):
    r = moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert "group_moved" in r.warning_codes()


def test_old_id_reports_scope_chat_id(moved):
    r = moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert (r.json["chat_id"], r.json["scope_chat_id"]) == (-1003, -1004)


def test_old_id_bot_checks_use_the_scope_id(moved):
    moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert {c.params.get("chat_id") for c in moved.tg.calls
            if c.method in ("getChat", "getChatMember")} == {"-1004"}


def test_old_id_same_members_is_unchanged(moved):
    before = moved.raw()
    r = moved.group_cmd(m(222, "bob", "user"), chat=-1003)
    assert (r.json["status"], moved.raw()) == ("unchanged", before)


def test_old_id_keeps_migration_record(moved):
    moved.group_cmd(m(333, "carol", "user"), chat=-1003)
    assert moved.config().get("chat_migrations") == {-1003: -1004}


# ---- shared sender id across scopes ----

def test_same_member_id_in_two_groups_is_allowed(env):
    other = group_scope([(222, "bob", "user")], chat_id=-1009, label="o")
    env.install(scopes=[dm_scope(), other])
    r = env.group_cmd(m(222, "bob", "user"))
    assert r.code == 0


def test_same_member_id_twice_in_one_command_is_refused(env):
    env.install()
    r = env.group_cmd(m(222, "bob", "user"), m(222, "rob", "user"))
    assert (r.code, r.json["error"]) == (2, "duplicate_member")
