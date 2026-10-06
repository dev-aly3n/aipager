"""design.md success criterion 2 and the token rules in entrypoints.md:
the token never appears in argv, stdout, stderr, logs (DEBUG), the JSON,
the environment, or any file other than aipager.yaml's bot_token."""

from __future__ import annotations

import json
import os

import pytest
import yaml

from agent_setup_support import (
    CHAT, HTTP, SECRET, TOKEN, assert_no_secret,
)


def _scan_everything(env, r):
    assert_no_secret(" ".join(r.argv), "argv")
    assert_no_secret(r.out, "stdout")
    assert_no_secret(r.err, "stderr")
    assert_no_secret(r.log, "captured logs")
    environ = "\n".join(f"{k}={v}" for k, v in os.environ.items())
    assert_no_secret(environ, "os.environ")
    for p in env.files_except_input():
        if p == env.config_path:
            doc = yaml.safe_load(p.read_text()) or {}
            doc.pop("bot_token", None)
            assert_no_secret(repr(doc), "aipager.yaml outside bot_token")
            n = p.read_text().count(SECRET)
            if n > 1:
                pytest.fail("token stored more than once in aipager.yaml",
                            pytrace=False)
        else:
            assert_no_secret(p.read_bytes().decode("utf-8", "replace"),
                             f"file {p.name}")


def test_file_json_install_leaks_nothing(env):
    _scan_everything(env, env.setup())


def test_file_plain_install_leaks_nothing(env):
    _scan_everything(env, env.setup(json_out=False))


def test_stdin_json_install_leaks_nothing(env):
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                stdin=TOKEN)
    _scan_everything(env, r)


def test_stdin_plain_install_leaks_nothing(env):
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, stdin=TOKEN)
    _scan_everything(env, r)


def test_json_values_hold_no_token(env):
    r = env.setup()
    assert_no_secret(json.dumps(r.json), "parsed JSON")


def test_unchanged_rerun_leaks_nothing(env):
    env.setup()
    _scan_everything(env, env.setup())


def test_dry_run_leaks_nothing(env):
    _scan_everything(env, env.setup("--dry-run"))


def test_rejected_token_leaks_nothing_json(env):
    env.tg.bots.clear()
    r = env.setup()
    assert r.code == 4
    _scan_everything(env, r)


def test_rejected_token_leaks_nothing_plain(env):
    env.tg.bots.clear()
    _scan_everything(env, env.setup(json_out=False))


def test_chat_not_started_leaks_nothing(env):
    env.tg.set("sendMessage", HTTP(400, "Bad Request: chat not found"))
    _scan_everything(env, env.setup(json_out=False))


def test_test_send_failure_echoing_token_is_scrubbed(env):
    env.tg.set("sendMessage", HTTP(400, f"Bad Request: weird {TOKEN} thing"))
    r = env.setup()
    assert r.code == 1
    _scan_everything(env, r)


def test_internal_error_carrying_token_is_scrubbed_json(env):
    """Error guessing: a shared-code exception whose text holds the token
    must reach neither stream unscrubbed."""
    env.tg.set("sendMessage", RuntimeError(f"boom while using {TOKEN}"))
    r = env.setup()
    _scan_everything(env, r)


def test_internal_error_carrying_token_is_scrubbed_plain(env):
    env.tg.set("sendMessage", RuntimeError(f"boom while using {TOKEN}"))
    _scan_everything(env, env.setup(json_out=False))


def test_internal_error_exits_one(env):
    env.tg.set("sendMessage", RuntimeError(f"boom while using {TOKEN}"))
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "internal_error")


def test_internal_error_in_getme_is_scrubbed(env):
    env.tg.set("getMe", ValueError(f"bad url .../bot{TOKEN}/getMe"))
    _scan_everything(env, env.setup())


def test_internal_error_writes_nothing(env):
    env.tg.set("getMe", ValueError(f"bad url .../bot{TOKEN}/getMe"))
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


# ---- hidden --token / --bot-token ----

@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_flag_value_is_refused_with_exit_2(env, flag):
    r = env.run("setup", flag, TOKEN, "--chat-id", CHAT, "--json")
    assert r.code == 2


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_flag_error_code(env, flag):
    r = env.run("setup", flag, TOKEN, "--chat-id", CHAT, "--json")
    assert r.json["error"] == "token_on_command_line"


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_flag_value_is_not_echoed(env, flag):
    r = env.run("setup", flag, TOKEN, "--chat-id", CHAT, "--json")
    assert_no_secret(r.out + r.err + r.log, "output of a refused --token")


def test_token_flag_equals_form_is_refused_and_not_echoed(env):
    r = env.run("setup", f"--token={TOKEN}", "--chat-id", CHAT)
    assert r.code == 2
    assert_no_secret(r.out + r.err + r.log, "output of --token=VALUE")


def test_token_flag_refusal_makes_no_http_call(env):
    env.run("setup", "--token", TOKEN, "--chat-id", CHAT, "--json")
    assert env.tg.calls == []


def test_token_flag_refusal_even_with_a_token_file(env):
    """Error guessing: a valid source alongside must not excuse it."""
    tf = env.token_file()
    r = env.run("setup", "--token-file", tf, "--token", TOKEN,
                "--chat-id", CHAT, "--json")
    assert r.code == 2 and r.json["error"] == "token_on_command_line"


def test_token_flag_refusal_writes_nothing(env):
    before = env.snapshot()
    env.run("setup", "--token", TOKEN, "--chat-id", CHAT, "--json")
    assert env.snapshot() == before


# ---- argparse errors are scrubbed ----

def test_unknown_positional_token_is_scrubbed(env):
    tf = env.token_file()
    r = env.run("setup", "--token-file", tf, "--chat-id", CHAT, TOKEN)
    assert r.code == 2
    assert_no_secret(r.out + r.err, "argparse error for a stray token")


def test_invalid_subcommand_token_is_scrubbed(env):
    r = env.run(TOKEN)
    assert r.code == 2
    assert_no_secret(r.out + r.err, "argparse invalid choice")


def test_invalid_setup_action_token_is_scrubbed(env):
    r = env.run("setup", TOKEN, "--json")
    assert r.code == 2
    assert_no_secret(r.out + r.err, "argparse invalid setup action")


def test_unknown_flag_value_token_is_scrubbed(env):
    r = env.run("setup", f"--secret={TOKEN}")
    assert r.code == 2
    assert_no_secret(r.out + r.err, "argparse unknown flag")


# ---- abbreviations are not accepted ----

def test_abbreviated_token_file_flag_is_rejected(env):
    tf = env.token_file()
    r = env.run("setup", "--token-f", tf, "--chat-id", CHAT)
    assert r.code == 2


def test_abbreviated_token_file_flag_makes_no_http_call(env):
    tf = env.token_file()
    env.run("setup", "--token-f", tf, "--chat-id", CHAT)
    assert env.tg.calls == []


def test_abbreviated_dry_run_flag_is_rejected(env):
    tf = env.token_file()
    before = env.snapshot()
    r = env.run("setup", "--token-file", tf, "--chat-id", CHAT, "--dry")
    assert r.code == 2 and env.snapshot() == before


def test_tok_prefix_is_not_taken_for_a_token_flag(env):
    r = env.run("setup", "--tok", TOKEN, "--chat-id", CHAT)
    assert r.code == 2
    assert_no_secret(r.out + r.err, "output of --tok VALUE")


# ---- the environment is never a token source ----

def test_env_var_is_not_a_token_source(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", TOKEN)
    r = env.run("setup", "--chat-id", CHAT, "--json")
    assert (r.code, r.json["error"]) == (2, "missing_token_source")


def test_env_var_is_not_used_over_the_file(env, monkeypatch):
    from agent_setup_support import OTHER_TOKEN
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", OTHER_TOKEN)
    env.setup()
    same = env.config().get("bot_token") == TOKEN
    assert same, "the env var token won over --token-file"
