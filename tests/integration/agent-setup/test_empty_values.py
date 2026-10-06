"""Iteration 2: an explicitly empty value is never "not given".

entrypoints.md: ``--role owner|admin`` (default owner), ``--chat-id N``
positive integer (required), ``--timeout`` is not valid on setup and is
an integer 0..3600 on detect-chat, ``--token-file PATH`` must be a
readable regular file. An empty string is outside every one of those
domains, so each must exit 2 with its documented error, name the flag,
make no HTTP call and write nothing."""

from __future__ import annotations

import pytest

from agent_setup_support import TOKEN


def _detect(env, *extra):
    return env.run("setup", "detect-chat", *extra, "--json")


# ---- setup: --role '' ----

def test_empty_role_exits_2(env):
    assert env.setup("--role", "").code == 2


def test_empty_role_error_is_bad_role(env):
    assert env.setup("--role", "").json["error"] == "bad_role"


def test_empty_role_fix_names_the_flag(env):
    assert "--role" in (env.setup("--role", "").json["fix"] or "")


def test_empty_role_makes_no_http_call(env):
    env.setup("--role", "")
    assert env.tg.calls == []


def test_empty_role_writes_nothing(env):
    before = env.snapshot()
    env.setup("--role", "")
    assert env.snapshot() == before


def test_empty_role_equals_form_exits_2(env):
    """``--role=`` is the same empty value through argparse's = form."""
    assert env.setup("--role=").json["error"] == "bad_role"


def test_empty_role_on_existing_install_keeps_the_role(env):
    """Error guessing: '' must not silently fall back to the default and
    re-plan an admin install as owner."""
    assert env.setup("--role", "admin").code == 0
    env.setup("--role", "", "--force")
    roles = [m["role"] for s in env.config()["scopes"]
             for m in s.get("members", [])]
    assert roles == ["admin"]


def test_empty_role_plain_mode_exits_2(env):
    assert env.setup("--role", "", json_out=False).code == 2


# ---- setup: --chat-id '' ----

def test_empty_chat_id_error_is_bad_chat_id(env):
    r = env.setup(chat="")
    assert (r.code, r.json["error"]) == (2, "bad_chat_id")


def test_empty_chat_id_fix_names_the_flag(env):
    assert "--chat-id" in (env.setup(chat="").json["fix"] or "")


def test_empty_chat_id_makes_no_http_call(env):
    env.setup(chat="")
    assert env.tg.calls == []


def test_empty_chat_id_writes_nothing(env):
    before = env.snapshot()
    env.setup(chat="")
    assert env.snapshot() == before


# ---- setup: --timeout '' ----

def test_empty_timeout_on_setup_exits_2(env):
    assert env.setup("--timeout", "").code == 2


def test_empty_timeout_on_setup_is_usage(env):
    assert env.setup("--timeout", "").json["error"] == "usage"


def test_empty_timeout_on_setup_makes_no_http_call(env):
    env.setup("--timeout", "")
    assert env.tg.calls == []


def test_empty_timeout_on_setup_writes_nothing(env):
    before = env.snapshot()
    env.setup("--timeout", "")
    assert env.snapshot() == before


# ---- setup: --token-file '' ----

def test_empty_token_file_exits_2(env):
    assert env.setup(token_file="").code == 2


def test_empty_token_file_is_unreadable(env):
    assert env.setup(token_file="").json["error"] == "token_file_unreadable"


def test_empty_token_file_fix_names_the_flag(env):
    assert "--token-file" in (env.setup(token_file="").json["fix"] or "")


def test_empty_token_file_makes_no_http_call(env):
    env.setup(token_file="")
    assert env.tg.calls == []


def test_empty_token_file_writes_nothing(env):
    before = env.snapshot()
    env.setup(token_file="")
    assert env.snapshot() == before


def test_empty_token_file_does_not_read_the_cwd(env, monkeypatch):
    """Error guessing: '' resolved against the working directory must not
    pick up a token lying there."""
    monkeypatch.chdir(env.input_dir)
    (env.input_dir / "token").write_text(TOKEN + "\n")
    assert env.setup(token_file="").code == 2


# ---- detect-chat ----

def test_detect_empty_timeout_is_bad_timeout(env):
    r = _detect(env, "--token-file", env.token_file(), "--timeout", "")
    assert (r.code, r.json["error"]) == (2, "bad_timeout")


def test_detect_empty_timeout_makes_no_http_call(env):
    _detect(env, "--token-file", env.token_file(), "--timeout", "")
    assert env.tg.calls == []


def test_detect_empty_token_file_is_unreadable(env):
    r = _detect(env, "--token-file", "")
    assert (r.code, r.json["error"]) == (2, "token_file_unreadable")


@pytest.mark.parametrize("flag", ["--role", "--chat-id"])
def test_detect_empty_setup_only_flag_is_usage(env, flag):
    r = _detect(env, "--token-file", env.token_file(), flag, "")
    assert (r.code, r.json["error"]) == (2, "usage")


@pytest.mark.parametrize("flag", ["--role", "--chat-id"])
def test_detect_empty_setup_only_flag_makes_no_http_call(env, flag):
    _detect(env, "--token-file", env.token_file(), flag, "")
    assert env.tg.calls == []
