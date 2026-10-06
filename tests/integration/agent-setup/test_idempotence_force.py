"""design.md success criterion 5: same-input re-run is a no-op (no test
send, no write); a different chat, token or role needs --force; --force
replaces the operator DM (groups kept), swaps the token (scopes kept), or
changes the role in place."""

from __future__ import annotations

import pytest

from agent_setup_support import CHAT, OTHER_TOKEN, TOKEN

GROUP = {"kind": "group", "chat_id": -100123456789, "label": "team",
         "members": [{"id": CHAT, "label": "alice", "role": "owner"},
                     {"id": 67890, "label": "bob", "role": "user"}]}
NEW_CHAT = 555000111


def _install(env):
    r = env.setup()
    assert r.code == 0
    return r


def _install_with_group(env):
    _install(env)
    doc = env.config()
    doc["scopes"].append(dict(GROUP))
    env.write_config(doc)


def _core(s):
    return (s.get("chat_id"), s.get("kind"),
            tuple((m.get("id"), m.get("role")) for m in s.get("members", [])))


# ---- no-op ----

def test_rerun_exits_zero(env):
    _install(env)
    assert env.setup().code == 0


def test_rerun_status_unchanged(env):
    _install(env)
    assert env.setup().json["status"] == "unchanged"


def test_rerun_changed_is_empty(env):
    _install(env)
    assert env.setup().json["changed"] == []


def test_rerun_sends_no_test_message(env):
    _install(env)
    env.tg.calls.clear()
    env.setup()
    assert env.tg.sent == []


def test_rerun_still_validates_the_token(env):
    _install(env)
    env.tg.calls.clear()
    env.setup()
    assert len(env.tg.calls_to("getMe")) == 1


def test_rerun_test_message_not_needed(env):
    _install(env)
    assert env.setup().json["test_message"] == "not_needed"


def test_rerun_writes_nothing(env):
    _install(env)
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_rerun_plain_says_nothing_to_change(env):
    _install(env)
    r = env.setup(json_out=False)
    assert r.code == 0 and "Nothing to change" in r.out


def test_rerun_writes_no_second_audit(env):
    _install(env)
    env.setup()
    assert len([a for a in env.audit()
                if a.get("action") == "grant-owner"]) == 1


def test_rerun_settings_status_unchanged(env):
    _install(env)
    assert env.setup().json["settings_json"]["status"] == "unchanged"


def test_rerun_admin_is_unchanged(env):
    env.setup("--role", "admin")
    assert env.setup("--role", "admin").json["status"] == "unchanged"


def test_rerun_with_token_from_stdin_is_unchanged(env):
    _install(env)
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                stdin=TOKEN)
    assert r.json["status"] == "unchanged"


def test_settings_only_drift_is_repaired_without_test_send(env):
    """yaml same, settings.json lost its hooks: settings is re-patched,
    no Telegram message (the send happens only when aipager.yaml would
    change)."""
    _install(env)
    env.settings_path.write_text("{}\n")
    env.tg.calls.clear()
    r = env.setup()
    assert (r.code, r.json["changed"], env.tg.sent) == (
        0, ["settings_json"], [])


def test_settings_only_drift_is_not_reported_unchanged(env):
    _install(env)
    env.settings_path.write_text("{}\n")
    assert env.setup().json["status"] != "unchanged"


# ---- --force gates ----

@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_without_force_exits_6(env, change):
    _install(env)
    r = _changed_run(env, change)
    assert (r.code, r.json["error"]) == (6, "existing_install")


@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_without_force_fix_names_force(env, change):
    _install(env)
    assert "--force" in _changed_run(env, change).json["fix"]


@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_without_force_writes_nothing(env, change):
    _install(env)
    before = env.snapshot()
    _changed_run(env, change)
    assert env.snapshot() == before


@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_without_force_sends_nothing(env, change):
    _install(env)
    env.tg.calls.clear()
    _changed_run(env, change)
    assert env.tg.sent == []


def test_change_without_force_plain_mentions_force(env):
    _install(env)
    r = env.setup(chat=NEW_CHAT, json_out=False)
    assert r.code == 6 and "--force" in r.out + r.err


def _changed_run(env, change, *extra):
    if change == "chat":
        return env.setup(*extra, chat=NEW_CHAT)
    if change == "token":
        return env.setup(*extra, token_file=env.token_file(
            OTHER_TOKEN, name="other.txt"))
    return env.setup("--role", "admin", *extra)


@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_with_force_exits_zero_updated(env, change):
    _install(env)
    r = _changed_run(env, change, "--force")
    assert (r.code, r.json["status"]) == (0, "updated")


@pytest.mark.parametrize("change,item", [("chat", "owner_dm"),
                                         ("token", "bot_token"),
                                         ("role", "role")])
def test_change_with_force_reports_what_changed(env, change, item):
    _install(env)
    assert item in _changed_run(env, change, "--force").json["changed"]


@pytest.mark.parametrize("change", ["chat", "token", "role"])
def test_change_with_force_sends_one_test_message(env, change):
    _install(env)
    env.tg.calls.clear()
    _changed_run(env, change, "--force")
    assert len(env.tg.sent) == 1


def test_force_chat_replaces_the_operator_dm(env):
    _install_with_group(env)
    env.setup("--force", chat=NEW_CHAT)
    dms = [_core(s) for s in env.config()["scopes"] if s["kind"] == "dm"]
    assert dms == [(NEW_CHAT, "dm", ((NEW_CHAT, "owner"),))]


def test_force_chat_keeps_the_group(env):
    _install_with_group(env)
    env.setup("--force", chat=NEW_CHAT)
    groups = [_core(s) for s in env.config()["scopes"] if s["kind"] != "dm"]
    assert groups == [_core(GROUP)]


def test_force_chat_keeps_the_token(env):
    _install_with_group(env)
    env.setup("--force", chat=NEW_CHAT)
    same = env.config()["bot_token"] == TOKEN
    assert same, "a forced chat change replaced the token"


def test_force_chat_keeps_other_top_level_keys(env):
    _install(env)
    doc = env.config()
    doc["chat_migrations"] = {-100111: -100222}
    env.write_config(doc)
    env.setup("--force", chat=NEW_CHAT)
    assert env.config().get("chat_migrations") == {-100111: -100222}


def test_force_chat_keeps_miniapp_settings(env):
    _install(env)
    doc = env.config()
    doc["miniapp"] = {"enabled": False, "port": 8799, "public_url": ""}
    env.write_config(doc)
    env.setup("--force", chat=NEW_CHAT)
    assert env.config().get("miniapp", {}).get("port") == 8799


def test_force_chat_yaml_stays_0600(env):
    import os
    import stat
    _install(env)
    env.setup("--force", chat=NEW_CHAT)
    assert stat.S_IMODE(os.stat(env.config_path).st_mode) == 0o600


def test_force_chat_grants_the_new_owner_in_audit(env):
    _install(env)
    env.setup("--force", chat=NEW_CHAT)
    users = [a.get("user_id") for a in env.audit()
             if a.get("action") == "grant-owner"]
    assert users == [CHAT, NEW_CHAT]


def test_force_token_swaps_the_token(env):
    _install_with_group(env)
    _changed_run(env, "token", "--force")
    same = env.config()["bot_token"] == OTHER_TOKEN
    assert same, "the forced token change did not store the new token"


def test_force_token_keeps_every_scope(env):
    _install_with_group(env)
    before = [_core(s) for s in env.config()["scopes"]]
    _changed_run(env, "token", "--force")
    assert [_core(s) for s in env.config()["scopes"]] == before


def test_force_token_stores_the_old_token_nowhere(env):
    from agent_setup_support import assert_no_secret
    _install(env)
    _changed_run(env, "token", "--force")
    for p in env.files_except_input():
        assert_no_secret(p.read_bytes().decode("utf-8", "replace"),
                         f"file {p.name} after a token swap")


def test_force_token_bot_username_is_the_new_bot(env):
    _install(env)
    assert _changed_run(env, "token", "--force").json[
        "bot_username"] == "other_bot"


def test_force_token_does_not_grant_owner_again(env):
    _install(env)
    _changed_run(env, "token", "--force")
    assert len([a for a in env.audit()
                if a.get("action") == "grant-owner"]) == 1


def test_force_role_changes_only_that_member(env):
    _install_with_group(env)
    env.setup("--role", "admin", "--force")
    assert [_core(s) for s in env.config()["scopes"]] == [
        (CHAT, "dm", ((CHAT, "admin"),)), _core(GROUP)]


def test_force_role_keeps_the_member_label(env):
    _install(env)
    doc = env.config()
    doc["scopes"][0]["members"][0]["label"] = "alice"
    env.write_config(doc)
    env.setup("--role", "admin", "--force")
    assert env.config()["scopes"][0]["members"][0]["label"] == "alice"


def test_force_role_back_to_owner_grants_owner(env):
    env.setup("--role", "admin")
    env.setup("--force")
    assert [a.get("user_id") for a in env.audit()
            if a.get("action") == "grant-owner"] == [CHAT]


def test_force_same_input_is_still_unchanged(env):
    _install(env)
    r = env.setup("--force")
    assert (r.json["status"], env.tg.sent[1:]) == ("unchanged", [])


# ---- installs without an operator DM / ambiguous ----

def test_group_only_install_needs_force_to_add_the_dm(env):
    env.write_config({"bot_token": TOKEN, "scopes": [dict(GROUP)]})
    assert env.setup().code == 6


def test_group_only_install_with_force_adds_the_dm(env):
    env.write_config({"bot_token": TOKEN, "scopes": [dict(GROUP)]})
    env.setup("--force")
    assert sorted(_core(s) for s in env.config()["scopes"]) == sorted(
        [_core(GROUP), (CHAT, "dm", ((CHAT, "owner"),))])


def _two_owner_dms():
    return [
        {"kind": "dm", "chat_id": 111, "label": "owner DM",
         "members": [{"id": 111, "label": "owner", "role": "owner"}]},
        {"kind": "dm", "chat_id": 222, "label": "second DM",
         "members": [{"id": 222, "label": "owner2", "role": "owner"}]},
    ]


def test_ambiguous_operator_dm_refuses_even_with_force(env):
    env.write_config({"bot_token": TOKEN, "scopes": _two_owner_dms()})
    r = env.setup("--force", chat=NEW_CHAT)
    assert (r.code, r.json["error"]) == (1, "ambiguous_install")


def test_ambiguous_operator_dm_writes_nothing(env):
    env.write_config({"bot_token": TOKEN, "scopes": _two_owner_dms()})
    before = env.snapshot()
    env.setup("--force", chat=NEW_CHAT)
    assert env.snapshot() == before


def test_ambiguous_operator_dm_fix_points_at_aipager_config(env):
    env.write_config({"bot_token": TOKEN, "scopes": _two_owner_dms()})
    assert "aipager config" in env.setup("--force", chat=NEW_CHAT).json["fix"]


def test_forced_rewrite_tightens_a_loose_yaml_to_0600(env):
    import os
    import stat
    _install(env)
    os.chmod(env.config_path, 0o644)
    env.setup("--force", chat=NEW_CHAT)
    assert stat.S_IMODE(os.stat(env.config_path).st_mode) == 0o600


def test_existing_install_error_names_the_old_chat(env):
    _install(env)
    d = env.setup(chat=NEW_CHAT).json
    assert str(CHAT) in f"{d['message']} {d['fix']}"


def test_existing_install_token_error_says_bot_token(env):
    _install(env)
    d = _changed_run(env, "token").json
    assert "bot token" in f"{d['message']} {d['fix']}".lower()
