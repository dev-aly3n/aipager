"""design.md success criteria 1 and 2: create a group on an install with a
DM scope (owner 111), then the same command again is ``unchanged``: exit
0, ``changed: []``, file byte-identical, no reload."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from setup_group_support import (
    GROUP, GROUP_KEYS, GROUP_LABEL, OWNER, dm_scope, m,
)

ARGS = (m(OWNER, "alice", "owner"), m(222, "bob", "user"))


@pytest.fixture
def created(env):
    env.install()
    env.daemon_pid = 4242
    return env, env.group_cmd(*ARGS)


def _default_root():
    return str(Path(os.path.expanduser(
        f"~/aipager-users/group-{GROUP_LABEL}")))


# ---- criterion 1: created ----

def test_create_exits_zero(created):
    assert created[1].code == 0


def test_create_status_created(created):
    assert created[1].json["status"] == "created"


def test_create_json_has_every_documented_key(created):
    assert set(created[1].json) == set(GROUP_KEYS)


def test_create_stdout_is_exactly_one_json_object(created):
    import json
    json.loads(created[1].out)  # raises if anything else is on stdout


def test_create_command_is_group(created):
    assert created[1].json["command"] == "group"


def test_create_file_mode_0600(created):
    env = created[0]
    assert stat.S_IMODE(env.config_path.stat().st_mode) == 0o600


def test_create_writes_a_group_scope(created):
    assert created[0].group()["kind"] == "group"


def test_create_writes_the_members(created):
    g = created[0].group()
    assert [(x["id"], x["label"], x["role"]) for x in g["members"]] == [
        (OWNER, "alice", "owner"), (222, "bob", "user")]


def test_create_default_label(created):
    assert created[0].group()["label"] == GROUP_LABEL


def test_create_root_written_explicitly_as_the_wizard_derives_it(created):
    assert created[0].group()["root"] == _default_root()


def test_create_json_root_matches_the_file(created):
    env, r = created
    assert r.json["root"] == env.group()["root"]


def test_create_dm_scope_unchanged_except_a_pinned_root(created):
    env = created[0]
    dm = next(s for s in env.config()["scopes"] if s["chat_id"] == OWNER)
    dm.pop("root", None)
    assert dm == dm_scope()


def test_create_keeps_the_bot_token(created):
    from setup_group_support import TOKEN
    assert created[0].config()["bot_token"] == TOKEN


def test_create_needs_no_force_for_an_existing_owner(created):
    assert created[1].json["force_reasons"] == []


def test_create_changed_lists_group(created):
    assert created[1].json["changed"][0] == "group"


def test_create_member_changes_are_added(created):
    assert [c["change"] for c in created[1].json["member_changes"]] == [
        "added", "added"]


def test_create_reports_members(created):
    assert created[1].json["members"] == [
        {"id": OWNER, "label": "alice", "role": "owner"},
        {"id": 222, "label": "bob", "role": "user"}]


def test_create_chat_id_in_json(created):
    assert (created[1].json["chat_id"],
            created[1].json["scope_chat_id"]) == (GROUP, GROUP)


def test_create_reloads_a_running_daemon(created):
    assert created[0].reloads == [1]


def test_create_daemon_outcome_reloaded_live(created):
    assert created[1].json["daemon"]["outcome"] == "reloaded_live"


def test_create_never_sends_a_message(created):
    assert created[0].tg.calls_to("sendMessage") == []


def test_create_no_temp_file_left_beside_the_config(created):
    env = created[0]
    left = [p.name for p in env.config_path.parent.iterdir()
            if p.name.startswith(env.config_path.name) and
            p.name != env.config_path.name]
    assert left == []


def test_create_audits_no_owner_grant_for_an_existing_owner(created):
    # 111 already holds owner in the DM scope: not a new grant.
    assert [r for r in created[0].audit()
            if r.get("user_id") == 222] == []


# ---- criterion 2: same command again ----

@pytest.fixture
def rerun(created):
    env, _ = created
    before = env.raw()
    env.reloads.clear()
    st = env.config_path.stat()
    r = env.group_cmd(*ARGS)
    return env, r, before, st


def test_rerun_exits_zero(rerun):
    assert rerun[1].code == 0


def test_rerun_status_unchanged(rerun):
    assert rerun[1].json["status"] == "unchanged"


def test_rerun_changed_is_empty(rerun):
    assert rerun[1].json["changed"] == []


def test_rerun_file_byte_identical(rerun):
    env, _, before, _ = rerun
    assert env.raw() == before


def test_rerun_file_not_rewritten(rerun):
    env, _, _, st = rerun
    assert env.config_path.stat().st_ino == st.st_ino


def test_rerun_sends_no_reload(rerun):
    assert rerun[0].reloads == []


def test_rerun_daemon_outcome_not_needed(rerun):
    assert rerun[1].json["daemon"]["outcome"] == "not_needed"


def test_rerun_pinned_roots_empty(rerun):
    assert rerun[1].json["pinned_roots"] == []


def test_rerun_with_members_in_another_order_is_unchanged(created):
    env, _ = created
    before = env.raw()
    r = env.group_cmd(*reversed(ARGS))
    assert (r.code, r.json["status"], env.raw()) == (0, "unchanged", before)


def test_rerun_naming_only_one_member_is_unchanged(created):
    """Members not named are kept, so naming a subset is a no-op."""
    env, _ = created
    before = env.raw()
    r = env.group_cmd(ARGS[1])
    assert (r.code, r.json["status"], env.raw()) == (0, "unchanged", before)


def test_rerun_with_explicit_default_label_is_unchanged(created):
    env, _ = created
    before = env.raw()
    r = env.group_cmd(*ARGS, "--label", GROUP_LABEL)
    assert (r.json["status"], env.raw()) == ("unchanged", before)


def test_rerun_with_explicit_same_root_is_unchanged(created):
    env, _ = created
    before = env.raw()
    r = env.group_cmd(*ARGS, "--root", _default_root())
    assert (r.json["status"], env.raw()) == ("unchanged", before)


def test_rerun_with_force_is_still_unchanged(created):
    env, _ = created
    before = env.raw()
    r = env.group_cmd(*ARGS, "--force")
    assert (r.code, r.json["status"], env.raw()) == (0, "unchanged", before)


def test_plain_rerun_prints_no_check_mark(created):
    env, _ = created
    r = env.group_cmd(*ARGS, json_out=False)
    assert r.code == 0 and not r.out.startswith("✓")


def test_plain_create_starts_with_check_mark(env):
    env.install()
    r = env.group_cmd(*ARGS, json_out=False)
    assert r.out.startswith("✓")


@pytest.mark.parametrize("line", ["group:", "members:", "root:", "Next:"])
def test_plain_create_lines(env, line):
    env.install()
    r = env.group_cmd(*ARGS, json_out=False)
    assert line in r.out


def test_plain_create_daemon_line_reloaded_live(env):
    env.install()
    env.daemon_pid = 4242
    r = env.group_cmd(*ARGS, json_out=False)
    # column-aligned: "daemon:" then spaces then the outcome
    assert re.search(r"^\s*daemon:\s+reloaded live$", r.out, re.M)
