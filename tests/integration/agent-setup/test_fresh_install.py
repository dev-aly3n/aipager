"""design.md success criterion 1 and 2: a fresh, prompt-free install
from a token file or stdin, with no TTY."""

from __future__ import annotations

import os
import stat

from agent_setup_support import BOT, CHAT, HOOK_EVENTS, TOKEN


def _owner_dm(role="owner"):
    return {"chat_id": CHAT, "kind": "dm", "label": "owner DM",
            "members": [{"id": CHAT, "label": "owner", "role": role}]}


def _scope_core(s):
    return {"chat_id": s.get("chat_id"), "kind": s.get("kind"),
            "label": s.get("label"),
            "members": [{"id": m.get("id"), "label": m.get("label"),
                         "role": m.get("role")} for m in s.get("members", [])]}


def test_fresh_install_from_token_file_exits_zero(env):
    r = env.setup()
    assert r.code == 0


def test_fresh_install_status_is_installed(env):
    assert env.setup().json["status"] == "installed"


def test_fresh_install_yaml_mode_is_0600(env):
    env.setup()
    assert stat.S_IMODE(os.stat(env.config_path).st_mode) == 0o600


def test_fresh_install_yaml_holds_the_token(env):
    env.setup()
    same = env.config().get("bot_token") == TOKEN
    assert same, "aipager.yaml bot_token is not the token from the file"


def test_fresh_install_yaml_has_exactly_one_scope(env):
    env.setup()
    assert len(env.config()["scopes"]) == 1


def test_fresh_install_scope_is_the_owner_dm(env):
    env.setup()
    assert _scope_core(env.config()["scopes"][0]) == _owner_dm()


def test_fresh_install_leaves_no_tmp_file(env):
    env.setup()
    leftovers = [p.name for p in env.config_path.parent.iterdir()
                 if p.name.endswith(".tmp")]
    assert leftovers == []


def test_fresh_install_sends_exactly_one_test_message(env):
    env.setup()
    assert len(env.tg.sent) == 1


def test_fresh_install_test_message_goes_to_the_chat(env):
    env.setup()
    assert env.tg.sent[0].form.get("chat_id") == str(CHAT)


def test_fresh_install_test_message_wording(env):
    env.setup()
    assert env.tg.sent[0].form.get("text", "").startswith(
        "aipager is set up for you.")


def test_fresh_install_reports_test_message_sent(env):
    assert env.setup().json["test_message"] == "sent"


def test_fresh_install_appends_one_grant_owner_audit(env):
    env.setup()
    grants = [a for a in env.audit() if a.get("action") == "grant-owner"]
    assert [g.get("user_id") for g in grants] == [CHAT]


def test_fresh_install_patches_all_hook_events(env):
    env.setup()
    import json
    hooks = json.loads(env.settings_path.read_text()).get("hooks", {})
    assert sorted(e for e in HOOK_EVENTS if e not in hooks) == []


def test_fresh_install_hooks_point_at_aipager_hook(env):
    env.setup()
    import json
    hooks = json.loads(env.settings_path.read_text())["hooks"]
    cmds = {h["command"] for ev in HOOK_EVENTS for grp in hooks[ev]
            for h in grp.get("hooks", [])}
    assert all("aipager-hook" in c for c in cmds) and cmds


def test_fresh_install_sets_status_line(env):
    env.setup()
    import json
    sl = json.loads(env.settings_path.read_text()).get("statusLine", {})
    assert "aipager-statusline" in str(sl.get("command"))


def test_fresh_install_changed_list(env):
    assert env.setup().json["changed"] == ["bot_token", "owner_dm",
                                           "settings_json"]


def test_fresh_install_reports_bot_username(env):
    assert env.setup().json["bot_username"] == BOT


def test_fresh_install_reports_chat_id_as_int(env):
    assert env.setup().json["chat_id"] == CHAT


def test_fresh_install_reports_role_owner_by_default(env):
    assert env.setup().json["role"] == "owner"


def test_fresh_install_never_calls_require_interactive(env):
    env.setup()
    assert env.interactive_calls == []


def test_fresh_install_never_reaches_a_prompt(env):
    env.setup()
    assert env.traps == []


def test_fresh_install_without_json_exits_zero(env):
    assert env.setup(json_out=False).code == 0


def test_fresh_install_without_json_writes_the_scope(env):
    env.setup(json_out=False)
    assert _scope_core(env.config()["scopes"][0]) == _owner_dm()


def test_fresh_install_without_json_never_prompts(env):
    env.setup(json_out=False)
    assert env.traps == [] and env.interactive_calls == []


# ---- --token-stdin ----

def _stdin_run(env, text=TOKEN + "\n", *extra):
    return env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                   *extra, stdin=text)


def test_token_stdin_exits_zero(env):
    assert _stdin_run(env).code == 0


def test_token_stdin_writes_the_same_scope(env):
    _stdin_run(env)
    assert [_scope_core(s) for s in env.config()["scopes"]] == [_owner_dm()]


def test_token_stdin_stores_the_token(env):
    _stdin_run(env)
    same = env.config().get("bot_token") == TOKEN
    assert same, "aipager.yaml bot_token is not the token from stdin"


def test_token_stdin_yaml_mode_is_0600(env):
    _stdin_run(env)
    assert stat.S_IMODE(os.stat(env.config_path).st_mode) == 0o600


def test_token_stdin_sends_one_test_message(env):
    _stdin_run(env)
    assert len(env.tg.sent) == 1


def test_token_stdin_never_prompts(env):
    _stdin_run(env)
    assert env.traps == [] and env.interactive_calls == []


def test_token_stdin_and_file_give_identical_settings(env):
    _stdin_run(env)
    via_stdin = env.settings_path.read_bytes()
    env.settings_path.unlink()
    env.config_path.unlink()
    env.setup()
    assert env.settings_path.read_bytes() == via_stdin


# ---- role admin (partition of --role) ----

def test_role_admin_writes_admin_member(env):
    env.setup("--role", "admin")
    assert _scope_core(env.config()["scopes"][0]) == _owner_dm("admin")


def test_role_admin_writes_no_grant_owner_audit(env):
    env.setup("--role", "admin")
    assert [a for a in env.audit() if a.get("action") == "grant-owner"] == []


# ---- token file tolerance (equivalence classes of valid input) ----

def test_token_file_with_quotes_and_whitespace_is_accepted(env):
    tf = env.token_file(f'  "{TOKEN}"  \n\n')
    assert env.setup(token_file=tf).code == 0


def test_token_file_with_text_around_one_token_is_accepted(env):
    tf = env.token_file(f"BotFather says: use this token {TOKEN} to access")
    assert env.setup(token_file=tf).code == 0


def test_token_file_exactly_4096_bytes_is_accepted(env):
    body = (TOKEN + "\n").ljust(4096, " ")
    assert len(body.encode()) == 4096
    tf = env.token_file(body)
    assert env.setup(token_file=tf).code == 0


def test_token_stdin_exactly_4096_chars_is_accepted(env):
    body = (TOKEN + "\n").ljust(4096, " ")
    assert _stdin_run(env, body).code == 0


def test_shared_token_file_works_with_warning(env):
    tf = env.token_file(mode=0o644)
    r = env.setup(token_file=tf)
    assert r.code == 0 and "token_file_shared" in [
        w.get("code") for w in r.json["warnings"]]


def test_private_token_file_has_no_shared_warning(env):
    r = env.setup()
    assert "token_file_shared" not in [w.get("code")
                                       for w in r.json["warnings"]]


def test_chat_id_one_is_accepted(env):
    assert env.setup(chat=1).code == 0


def test_chat_id_fifteen_digits_is_accepted(env):
    assert env.setup(chat=999999999999999).code == 0
