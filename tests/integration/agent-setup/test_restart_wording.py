"""Iteration 2: the restart advice names the bot token only when the token
changed. A scope change the daemon could not pick up live still needs a
restart, but the advice must not say the token changed."""

from __future__ import annotations

from agent_setup_support import OTHER_TOKEN


def _installed_then_daemon(env, pid=4242):
    assert env.setup().code == 0
    env.daemon_pid = pid
    env.reloads.clear()
    env.signals.clear()


def _token_change(env, json_out=True):
    return env.setup("--force", token_file=env.token_file(OTHER_TOKEN,
                                                          name="o"),
                     json_out=json_out)


def test_token_change_next_step_names_the_bot_token(env):
    _installed_then_daemon(env)
    assert "bot token" in _token_change(env).json["next_step"].lower()


def test_token_change_next_step_has_stop_and_start(env):
    _installed_then_daemon(env)
    s = _token_change(env).json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


def test_role_change_not_reloaded_restart_needed(env):
    _installed_then_daemon(env, pid=-1)
    env.reload_outcome = ("not_reloaded", None)
    d = env.setup("--force", "--role", "admin").json
    assert d["daemon"]["restart_needed"] is True


def test_role_change_restart_wording_names_no_bot_token(env):
    _installed_then_daemon(env, pid=-1)
    env.reload_outcome = ("not_reloaded", None)
    s = env.setup("--force", "--role", "admin").json["next_step"]
    assert "bot token" not in s.lower()


def test_role_change_restart_wording_still_says_restart(env):
    _installed_then_daemon(env, pid=-1)
    env.reload_outcome = ("not_reloaded", None)
    s = env.setup("--force", "--role", "admin").json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


def test_chat_change_restart_wording_names_no_bot_token(env):
    _installed_then_daemon(env)
    env.reload_outcome = ("not_reloaded", None)
    s = env.setup("--force", chat=777000111).json["next_step"]
    assert "bot token" not in s.lower()


def test_chat_change_plain_restart_wording_names_no_bot_token(env):
    _installed_then_daemon(env)
    env.reload_outcome = ("not_reloaded", None)
    r = env.setup("--force", chat=777000111, json_out=False)
    assert r.code == 0 and "bot token" not in (r.out + r.err).lower()


def test_token_change_plain_names_the_bot_token(env):
    _installed_then_daemon(env)
    r = _token_change(env, json_out=False)
    assert r.code == 0 and "bot token" in (r.out + r.err).lower()

