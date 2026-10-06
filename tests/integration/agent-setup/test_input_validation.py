"""Exit-2 input errors (entrypoints.md exit table, 'usage-class errors:
fix names the exact flag'). Boundary values for --chat-id, --role,
token file size and type, stdin TTY."""

from __future__ import annotations

import os

import pytest

from agent_setup_support import CHAT, TOKEN


def _err(r):
    return r.code, r.json["error"]


def test_no_token_source_exits_2(env):
    r = env.run("setup", "--chat-id", CHAT, "--json")
    assert _err(r) == (2, "missing_token_source")


def test_no_token_source_fix_names_both_flags(env):
    fix = env.run("setup", "--chat-id", CHAT, "--json").json["fix"]
    assert "--token-file" in fix and "--token-stdin" in fix


def test_both_token_sources_conflict(env):
    tf = env.token_file()
    r = env.run("setup", "--token-file", tf, "--token-stdin", "--chat-id",
                CHAT, "--json", stdin=TOKEN)
    assert _err(r) == (2, "token_source_conflict")


def test_missing_chat_id_exits_2_naming_the_flag(env):
    tf = env.token_file()
    r = env.run("setup", "--token-file", tf, "--json")
    assert r.code == 2 and "--chat-id" in r.json["fix"]


def test_missing_chat_id_sends_nothing(env):
    tf = env.token_file()
    env.run("setup", "--token-file", tf, "--json")
    assert env.tg.sent == []


@pytest.mark.parametrize("bad", ["0", "-5", "-1001234567890", "abc", "1.5",
                                 "+5", "", "1e5", "0x10",
                                 "1234567890123456"])
def test_bad_chat_id_exits_2(env, bad):
    r = env.setup(chat=bad)
    assert _err(r) == (2, "bad_chat_id")


@pytest.mark.parametrize("bad", ["0", "abc", "1234567890123456"])
def test_bad_chat_id_fix_names_the_flag(env, bad):
    assert "--chat-id" in env.setup(chat=bad).json["fix"]


def test_negative_chat_id_points_at_aipager_config(env):
    d = env.setup(chat="-1001234567890").json
    assert "aipager config" in f"{d['message']} {d['fix']}"


def test_bad_chat_id_makes_no_http_call(env):
    env.setup(chat="abc")
    assert env.tg.calls == []


def test_bad_chat_id_writes_nothing(env):
    before = env.snapshot()
    env.setup(chat="-5")
    assert env.snapshot() == before


@pytest.mark.parametrize("bad", ["member", "user", "", "root"])
def test_bad_role_exits_2(env, bad):
    r = env.setup("--role", bad)
    assert _err(r) == (2, "bad_role")


def test_bad_role_fix_names_the_flag(env):
    assert "--role" in env.setup("--role", "member").json["fix"]


def test_bad_role_makes_no_http_call(env):
    env.setup("--role", "member")
    assert env.tg.calls == []


def test_timeout_flag_is_rejected_on_setup(env):
    r = env.setup("--timeout", "5")
    assert r.code == 2


def test_timeout_flag_on_setup_writes_nothing(env):
    before = env.snapshot()
    env.setup("--timeout", "5")
    assert env.snapshot() == before


# ---- token file ----

def test_missing_token_file_is_unreadable(env):
    r = env.setup(token_file=env.input_dir / "nope.txt")
    assert _err(r) == (2, "token_file_unreadable")


def test_directory_token_file_is_unreadable(env):
    r = env.setup(token_file=env.input_dir)
    assert _err(r) == (2, "token_file_unreadable")


def test_device_token_file_is_unreadable(env):
    r = env.setup(token_file=os.devnull)
    assert _err(r) == (2, "token_file_unreadable")


def test_fifo_token_file_is_unreadable_without_blocking(env):
    """Error guessing: a FIFO would block a naive read forever."""
    fifo = env.input_dir / "fifo"
    os.mkfifo(fifo)
    r = env.setup(token_file=fifo)
    assert _err(r) == (2, "token_file_unreadable")


def test_unreadable_mode_token_file(env):
    tf = env.token_file(mode=0o000)
    try:
        r = env.setup(token_file=tf)
    finally:
        os.chmod(tf, 0o600)
    assert _err(r) == (2, "token_file_unreadable")


def test_oversize_token_file_is_refused(env):
    tf = env.token_file((TOKEN + "\n").ljust(4097, " "))
    assert env.setup(token_file=tf).code == 2


def test_oversize_token_file_makes_no_http_call(env):
    tf = env.token_file((TOKEN + "\n").ljust(4097, " "))
    env.setup(token_file=tf)
    assert env.tg.calls == []


def test_undecodable_token_file_is_unreadable(env):
    tf = env.token_file(b"\xff\xfe\x00" + TOKEN.encode())
    assert env.setup(token_file=tf).code == 2


@pytest.mark.parametrize("garbage", ["hello world", "", "   \n",
                                     "123:short", "not:a token at all"])
def test_garbage_token_file_is_malformed(env, garbage):
    tf = env.token_file(garbage)
    assert _err(env.setup(token_file=tf)) == (2, "token_malformed")


def test_garbage_token_file_makes_no_http_call(env):
    tf = env.token_file("hello world")
    env.setup(token_file=tf)
    assert env.tg.calls == []


def test_garbage_token_file_content_is_not_echoed(env):
    tf = env.token_file("SuperSecretPassphrase-xyzzy")
    r = env.setup(token_file=tf)
    assert "xyzzy" not in r.out + r.err


def test_unreadable_token_file_error_names_the_path(env):
    p = env.input_dir / "nope.txt"
    d = env.setup(token_file=p).json
    assert str(p) in f"{d['message']} {d['fix']}"


# ---- token stdin ----

def test_token_stdin_tty_is_refused(env):
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                stdin=TOKEN, tty=True)
    assert _err(r) == (2, "token_stdin_is_tty")


def test_token_stdin_tty_is_not_read(env):
    env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
            stdin=TOKEN, tty=True)
    assert env.traps == []


def test_token_stdin_tty_makes_no_http_call(env):
    env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
            stdin=TOKEN, tty=True)
    assert env.tg.calls == []


def test_token_stdin_oversize_is_malformed(env):
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                stdin=(TOKEN + "\n").ljust(4097, " "))
    assert _err(r) == (2, "token_malformed")


def test_token_stdin_empty_is_malformed(env):
    r = env.run("setup", "--token-stdin", "--chat-id", CHAT, "--json",
                stdin="")
    assert _err(r) == (2, "token_malformed")


def test_every_usage_error_writes_nothing(env):
    before = env.snapshot()
    env.run("setup", "--chat-id", CHAT, "--json")
    env.setup(chat="abc")
    env.setup("--role", "x")
    env.setup(token_file=env.input_dir / "nope")
    env.run("setup", "--token-stdin", "--chat-id", CHAT, stdin=TOKEN,
            tty=True)
    assert env.snapshot() == before


# ---- more error guessing ----

def test_empty_token_file_path_is_unreadable(env):
    assert _err(env.setup(token_file="")) == (2, "token_file_unreadable")


def test_token_file_with_bom_and_crlf_is_accepted(env):
    tf = env.token_file(("﻿" + TOKEN + "\r\n").encode("utf-8"))
    assert env.setup(token_file=tf).code == 0


def test_token_file_holding_an_api_url_is_accepted(env):
    """'extra text around one token is tolerated': a pasted API URL."""
    tf = env.token_file(f"https://api.telegram.org/bot{TOKEN}/getMe\n")
    assert env.setup(token_file=tf).code == 0


def test_token_file_is_left_in_place(env):
    tf = env.token_file()
    before = tf.read_bytes()
    env.setup()
    assert tf.read_bytes() == before


@pytest.mark.parametrize("bad", ["abc", "-5", "0"])
def test_bad_chat_id_json_chat_id_is_null(env, bad):
    """Values unknown at the point of failure are null, never the raw
    flag text."""
    assert env.setup(chat=bad).json["chat_id"] is None


def test_usage_error_bot_username_is_null(env):
    assert env.setup(chat="abc").json["bot_username"] is None


def test_usage_error_test_message_not_attempted(env):
    assert env.setup(chat="abc").json["test_message"] == "not_attempted"


def test_deps_missing_test_message_not_attempted(env):
    env.missing.add("dtach")
    assert env.setup().json["test_message"] == "not_attempted"


def test_token_flag_fix_names_the_token_sources(env):
    fix = env.run("setup", "--token", TOKEN, "--chat-id", CHAT,
                  "--json").json["fix"]
    assert "--token-file" in fix and "--token-stdin" in fix


def test_bad_role_json_role_is_null(env):
    assert env.setup("--role", "boss").json["role"] is None
