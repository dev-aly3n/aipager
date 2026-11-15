"""Error guessing: a concurrent edit of aipager.yaml between the command's
read and its write (design.md approach 5 and Risks: ``config_changed``,
never overwrite a concurrent wizard edit; other scopes edited meanwhile
are kept), plus write failures.

The concurrent edit is made from the fake Telegram's getChat answer:
the Bot API checks run after planning and before the write (design.md
run order), so this is a real interleaving, not a patched internal."""

from __future__ import annotations

import os

import pytest
import yaml

from setup_group_support import (
    GROUP, OWNER, TOKEN, dm_scope, group_scope, m, ok,
)

BASE = [(OWNER, "alice", "owner"), (222, "bob", "user")]
CHAT_OK = ok({"id": GROUP, "type": "supergroup", "title": "Team"})


def _edit_on_getchat(env, mutate):
    def _spec(call):
        doc = yaml.safe_load(env.config_path.read_text())
        mutate(doc)
        env.config_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        os.chmod(env.config_path, 0o600)
        return CHAT_OK
    env.tg.set("getChat", _spec)


def _group_of(doc, cid=GROUP):
    return next(s for s in doc["scopes"] if s["chat_id"] == cid)


@pytest.fixture
def grp(env):
    env.install(scopes=[dm_scope(), group_scope(BASE)])
    env.daemon_pid = 4242
    return env


def _add_eve(doc):
    _group_of(doc)["members"].append({"id": 999, "label": "eve",
                                      "role": "user"})


def test_concurrent_member_edit_exits_1_config_changed(grp):
    _edit_on_getchat(grp, _add_eve)
    r = grp.group_cmd(m(333, "carol", "user"))
    assert (r.code, r.json["error"]) == (1, "config_changed")


def test_concurrent_member_edit_is_kept(grp):
    _edit_on_getchat(grp, _add_eve)
    grp.group_cmd(m(333, "carol", "user"))
    assert [x["id"] for x in grp.group()["members"]] == [OWNER, 222, 999]


def test_concurrent_member_edit_sends_no_reload(grp):
    _edit_on_getchat(grp, _add_eve)
    grp.group_cmd(m(333, "carol", "user"))
    assert grp.reloads == []


def test_concurrent_edit_message_says_run_again(grp):
    _edit_on_getchat(grp, _add_eve)
    d = grp.group_cmd(m(333, "carol", "user")).json
    assert "again" in f"{d['message']} {d['fix']}".lower()


def test_concurrent_role_change_on_disk_is_not_clobbered(grp):
    def _promote(doc):
        _group_of(doc)["members"][1]["role"] = "read_only"
    _edit_on_getchat(grp, _promote)
    grp.group_cmd(m(333, "carol", "user"))
    assert grp.group()["members"][1]["role"] == "read_only"


def test_concurrent_creation_of_a_new_group(env):
    env.install()

    def _create(doc):
        doc["scopes"].append(group_scope([(555, "erin", "user")]))
    _edit_on_getchat(env, _create)
    r = env.group_cmd(m(5, "x", "user"))
    assert (r.code, r.json["error"], [x["id"] for x in
                                      env.group()["members"]]) == (
        1, "config_changed", [555])


def test_concurrent_removal_of_the_group(grp):
    def _drop(doc):
        doc["scopes"] = [s for s in doc["scopes"] if s["chat_id"] != GROUP]
    _edit_on_getchat(grp, _drop)
    r = grp.group_cmd(m(333, "carol", "user"))
    assert (r.code, r.json["error"], grp.group()) == (
        1, "config_changed", None)


def test_concurrent_edit_of_another_scope_is_kept(grp):
    def _relabel_dm(doc):
        _group_of(doc, OWNER)["label"] = "my phone"
    _edit_on_getchat(grp, _relabel_dm)
    r = grp.group_cmd(m(333, "carol", "user"))
    dm = next(s for s in grp.config()["scopes"] if s["chat_id"] == OWNER)
    assert (r.code, dm["label"]) == (0, "my phone")


def test_concurrent_edit_of_another_scope_still_writes_ours(grp):
    def _relabel_dm(doc):
        _group_of(doc, OWNER)["label"] = "my phone"
    _edit_on_getchat(grp, _relabel_dm)
    grp.group_cmd(m(333, "carol", "user"))
    assert [x["id"] for x in grp.group()["members"]] == [OWNER, 222, 333]


def test_concurrent_new_scope_elsewhere_is_kept(grp):
    def _add_scope(doc):
        doc["scopes"].append(group_scope([(7, "z", "user")], chat_id=-1077,
                                         label="other"))
    _edit_on_getchat(grp, _add_scope)
    r = grp.group_cmd(m(333, "carol", "user"))
    assert (r.code, grp.group(-1077) is not None) == (0, True)


def test_concurrent_token_change_is_kept(grp):
    other = "123123123:BBGz9y8x7w6v5u4t3s2r1q0PoIuYtR_-Hg"

    def _swap(doc):
        doc["bot_token"] = other
    _edit_on_getchat(grp, _swap)
    grp.group_cmd(m(333, "carol", "user"))
    if grp.config()["bot_token"] == TOKEN:
        pytest.fail("a concurrent token change was overwritten",
                    pytrace=False)


def test_concurrent_corruption_is_not_overwritten(grp):
    def _spec(call):
        grp.config_path.write_text("scopes: [unclosed\n")
        return CHAT_OK
    grp.tg.set("getChat", _spec)
    r = grp.group_cmd(m(333, "carol", "user"))
    assert (r.code, grp.config_path.read_text()) == (1, "scopes: [unclosed\n")


def test_two_runs_back_to_back_both_land(env):
    """Two agents adding different members one after the other: both
    members end up in the group (the second run re-reads the file)."""
    env.install(scopes=[dm_scope(), group_scope(BASE)])
    env.group_cmd(m(333, "carol", "user"))
    env.group_cmd(m(444, "dan", "user"))
    assert [x["id"] for x in env.group()["members"]] == [
        OWNER, 222, 333, 444]


# ---- write failures ----

def test_unwritable_config_folder_is_write_failed(grp):
    d = grp.config_path.parent
    before = grp.raw()
    os.chmod(d, 0o500)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("chmod does not make the folder unwritable here")
        r = grp.group_cmd(m(333, "carol", "user"))
    finally:
        os.chmod(d, 0o700)
    assert (r.code, r.json["error"], grp.raw(), grp.reloads) == (
        1, "write_failed", before, [])
