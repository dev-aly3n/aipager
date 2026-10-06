"""design.md success criterion 10: ``setup detect-chat`` prints the
candidate and writes nothing, never sends offset (no query string at
all), exits 9 with the daemon source (same token), polls Telegram with a
different configured token, 8 on timeout, 10 on 409, 7 on network at the
deadline. Lookback 600 s boundary."""

from __future__ import annotations

import pytest

from agent_setup_support import (
    BOT, DETECT_KEYS, HTTP, NET, OTHER_TOKEN, TOKEN, assert_no_secret, ok,
)

T0 = 1_800_000_000


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        if len(self.sleeps) > 5000:
            raise AssertionError("detect-chat looped without end")
        self.mono += s


@pytest.fixture
def clock(env, monkeypatch):
    c = Clock()
    import aipager.setup_detect as sd
    monkeypatch.setattr(sd, "_now", lambda: float(T0))
    monkeypatch.setattr(sd, "_monotonic", lambda: c.mono)
    monkeypatch.setattr(sd, "_sleep", c.sleep)
    return c


def _msg(uid, date, *, first="Ada", last="L", username="ada",
         chat_type="private", key="message", update_id=1):
    chat = {"id": uid if chat_type == "private" else -100999,
            "type": chat_type}
    if chat_type == "private":
        chat.update(first_name=first)
    frm = {"id": uid, "is_bot": False, "first_name": first}
    if last is not None:
        frm["last_name"] = last
        chat["last_name"] = last if chat_type == "private" else None
    if username is not None:
        frm["username"] = username
        if chat_type == "private":
            chat["username"] = username
    chat = {k: v for k, v in chat.items() if v is not None}
    return {"update_id": update_id,
            key: {"message_id": update_id, "date": date, "chat": chat,
                  "from": frm, "text": "/start"}}


def _detect(env, *extra, json_out=True, token_file=None):
    tf = token_file if token_file is not None else env.token_file()
    args = ["setup", "detect-chat", "--token-file", tf, *extra]
    if json_out:
        args.append("--json")
    return env.run(*args)


ADA = 123456789


# ---- found ----

def test_found_exits_zero(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).code == 0


def test_found_status(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["status"] == "found"


def test_found_candidate(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["candidate"] == {
        "id": ADA, "first_name": "Ada", "last_name": "L",
        "username": "ada", "date": T0 - 5}


def test_found_candidate_without_last_name_or_username(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5, last=None,
                                      username=None)]))
    c = _detect(env).json["candidate"]
    assert (c["last_name"], c["username"]) == (None, None)


def test_found_source_is_telegram(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["source"] == "telegram"


def test_found_bot_username(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["bot_username"] == BOT


def test_found_has_every_key(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert [k for k in DETECT_KEYS if k not in _detect(env).json] == []


def test_found_writes_nothing(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    before = env.snapshot()
    _detect(env)
    assert env.snapshot() == before


def test_found_plain_writes_nothing(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    before = env.snapshot()
    _detect(env, json_out=False)
    assert env.snapshot() == before


def test_found_plain_prints_id_name_and_username(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    r = _detect(env, json_out=False)
    assert all(s in r.out for s in (str(ADA), "Ada", "ada"))


def test_found_sends_no_message(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    _detect(env)
    assert env.tg.sent == []


def test_getupdates_has_no_query_string(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    _detect(env)
    assert [c.query for c in env.tg.calls_to("getUpdates")] == [""]


def test_getupdates_never_sends_offset_while_polling(env, clock):
    env.tg.set("getUpdates", ok([]))
    _detect(env, "--timeout", "10")
    calls = env.tg.calls_to("getUpdates")
    assert len(calls) > 1 and all(
        c.query == "" and "offset" not in c.form for c in calls)


def test_next_step_repeats_the_token_file_path(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    tf = env.token_file()
    ns = _detect(env, token_file=tf).json["next_step"]
    assert f"--token-file {tf}" in ns and f"--chat-id {ADA}" in ns


def test_next_step_with_stdin_says_token_stdin(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    r = env.run("setup", "detect-chat", "--token-stdin", "--json",
                stdin=TOKEN)
    assert "--token-stdin" in r.json["next_step"]


def test_detect_from_stdin_leaks_no_token(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    r = env.run("setup", "detect-chat", "--token-stdin", "--json",
                stdin=TOKEN)
    assert_no_secret(r.out + r.err + r.log, "detect-chat output")


def test_detect_plain_leaks_no_token(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    r = _detect(env, json_out=False)
    assert_no_secret(r.out + r.err + r.log, "detect-chat plain output")


def test_newest_private_message_wins(env, clock):
    env.tg.set("getUpdates", ok([
        _msg(111, T0 - 50, first="Old", update_id=1),
        _msg(ADA, T0 - 5, update_id=2),
    ]))
    assert _detect(env).json["candidate"]["id"] == ADA


def test_other_candidates_counted(env, clock):
    env.tg.set("getUpdates", ok([
        _msg(111, T0 - 50, first="Old", update_id=1),
        _msg(222, T0 - 40, first="Other", update_id=2),
        _msg(ADA, T0 - 5, update_id=3),
    ]))
    assert _detect(env).json["other_candidates"] == 2


def test_same_person_twice_is_not_another_candidate(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 50, update_id=1),
                                 _msg(ADA, T0 - 5, update_id=2)]))
    assert _detect(env).json["other_candidates"] == 0


def test_group_messages_are_ignored(env, clock):
    env.tg.set("getUpdates", ok([_msg(999, T0 - 1, chat_type="supergroup")]))
    assert _detect(env, "--timeout", "0").code == 8


def test_edited_message_counts(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5, key="edited_message")]))
    assert _detect(env).json["candidate"]["id"] == ADA


def test_found_on_a_later_poll(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return ok([_msg(ADA, T0 + 3)]) if len(polls) >= 3 else ok([])
    env.tg.set("getUpdates", _resp)
    r = _detect(env, "--timeout", "60")
    assert (r.code, len(polls)) == (0, 3)


# ---- lookback boundary ----

def test_message_exactly_600s_old_is_accepted(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 600)]))
    assert _detect(env, "--timeout", "0").code == 0


def test_message_601s_old_is_ignored(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 601)]))
    assert _detect(env, "--timeout", "0").code == 8


def test_old_message_is_not_counted_as_other(env, clock):
    env.tg.set("getUpdates", ok([_msg(111, T0 - 5000, update_id=1),
                                 _msg(ADA, T0 - 5, update_id=2)]))
    assert _detect(env).json["other_candidates"] == 0


# ---- timeout / errors ----

def test_timeout_exits_8(env, clock):
    r = _detect(env, "--timeout", "10")
    assert (r.code, r.json["error"]) == (8, "detect_timeout")


def test_timeout_never_really_sleeps(env, clock):
    _detect(env, "--timeout", "10")
    assert clock.sleeps and sum(clock.sleeps) <= 10 + max(clock.sleeps)


def test_timeout_zero_is_one_poll(env, clock):
    _detect(env, "--timeout", "0")
    assert len(env.tg.calls_to("getUpdates")) == 1


def test_timeout_candidate_is_null(env, clock):
    assert _detect(env, "--timeout", "0").json["candidate"] is None


def test_timeout_writes_nothing(env, clock):
    before = env.snapshot()
    _detect(env, "--timeout", "4")
    assert env.snapshot() == before


def test_default_timeout_is_300(env, clock):
    _detect(env)
    assert 290 <= sum(clock.sleeps) <= 310


def test_conflict_409_exits_10(env, clock):
    env.tg.set("getUpdates", HTTP(409, "Conflict: terminated by other "
                                  "getUpdates request"))
    r = _detect(env, "--timeout", "10")
    assert (r.code, r.json["error"]) == (10, "updates_conflict")


def test_network_at_deadline_exits_7(env, clock):
    env.tg.set("getUpdates", NET())
    r = _detect(env, "--timeout", "6")
    assert (r.code, r.json["error"]) == (7, "telegram_unreachable")


def test_network_blip_then_found(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return NET() if len(polls) == 1 else ok([_msg(ADA, T0)])
    env.tg.set("getUpdates", _resp)
    assert _detect(env, "--timeout", "30").code == 0


def test_server_error_keeps_polling(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return HTTP(502, "Bad Gateway") if len(polls) < 3 else ok(
            [_msg(ADA, T0)])
    env.tg.set("getUpdates", _resp)
    assert _detect(env, "--timeout", "30").code == 0


def test_network_then_quiet_at_deadline_is_timeout(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return NET() if len(polls) == 1 else ok([])
    env.tg.set("getUpdates", _resp)
    assert _detect(env, "--timeout", "10").code == 8


def test_bad_token_exits_4(env, clock):
    env.tg.bots.clear()
    r = _detect(env)
    assert (r.code, r.json["error"]) == (4, "token_rejected")


def test_bad_token_never_polls(env, clock):
    env.tg.bots.clear()
    _detect(env)
    assert env.tg.calls_to("getUpdates") == []


def test_getme_unreachable_exits_7(env, clock):
    env.tg.set("getMe", NET())
    assert _detect(env).code == 7


def test_getupdates_401_exits_4(env, clock):
    env.tg.set("getUpdates", HTTP(401, "Unauthorized"))
    assert _detect(env, "--timeout", "5").code == 4


# ---- daemon source ----

def _configured(env, token):
    env.write_config({"bot_token": token, "scopes": [
        {"kind": "dm", "chat_id": 5550001, "label": "owner DM",
         "members": [{"id": 5550001, "label": "owner", "role": "owner"}]}]})


def test_daemon_same_token_exits_9(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    r = _detect(env)
    assert (r.code, r.json["error"]) == (9, "daemon_running")


def test_daemon_same_token_source_daemon(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    assert _detect(env).json["source"] == "daemon"


def test_daemon_same_token_does_not_poll_telegram(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    _detect(env)
    assert env.tg.calls_to("getUpdates") == []


def test_daemon_same_token_advice_names_service_stop(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    d = _detect(env).json
    assert "aipager service stop" in f"{d['message']} {d['fix']}"


def test_daemon_same_token_plain_says_source(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    r = _detect(env, json_out=False)
    assert r.code == 9 and "daemon" in (r.out + r.err).lower()


def test_daemon_same_token_writes_nothing(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    before = env.snapshot()
    _detect(env)
    assert env.snapshot() == before


def test_daemon_same_token_never_signals(env, clock):
    _configured(env, TOKEN)
    env.daemon_pid = 4242
    _detect(env)
    assert (env.signals, env.reloads) == ([], [])


def test_daemon_unknown_configured_token_is_daemon_source(env, clock):
    env.daemon_pid = 4242
    assert _detect(env).json["source"] == "daemon"


def test_daemon_v1_same_token_is_daemon_source(env, clock):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(f"CLAUDE_TG_BOT_TOKEN={TOKEN}\n"
                              "CLAUDE_TG_CHAT_ID=5550001\n")
    env.daemon_pid = 4242
    assert _detect(env).code == 9


def test_daemon_different_token_polls_telegram(env, clock):
    _configured(env, OTHER_TOKEN)
    env.daemon_pid = 4242
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    r = _detect(env)
    assert (r.code, r.json["source"]) == (0, "telegram")


def test_daemon_different_v1_token_polls_telegram(env, clock):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(f"CLAUDE_TG_BOT_TOKEN={OTHER_TOKEN}\n"
                              "CLAUDE_TG_CHAT_ID=5550001\n")
    env.daemon_pid = 4242
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["source"] == "telegram"


def test_no_daemon_with_same_configured_token_polls_telegram(env, clock):
    _configured(env, TOKEN)
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert _detect(env).json["source"] == "telegram"


# ---- flags ----

@pytest.mark.parametrize("flag", [["--chat-id", "5"], ["--role", "owner"],
                                  ["--service"], ["--force"],
                                  ["--dry-run"]])
def test_setup_only_flags_are_rejected(env, clock, flag):
    r = _detect(env, *flag)
    assert r.code == 2


@pytest.mark.parametrize("flag", [["--chat-id", "5"], ["--dry-run"]])
def test_setup_only_flags_make_no_http_call(env, clock, flag):
    _detect(env, *flag)
    assert env.tg.calls == []


@pytest.mark.parametrize("bad", ["-1", "3601", "abc", "1.5", ""])
def test_bad_timeout_exits_2(env, clock, bad):
    r = _detect(env, "--timeout", bad)
    assert (r.code, r.json["error"]) == (2, "bad_timeout")


def test_bad_timeout_fix_names_the_flag(env, clock):
    assert "--timeout" in _detect(env, "--timeout", "3601").json["fix"]


def test_timeout_3600_is_accepted(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0)]))
    assert _detect(env, "--timeout", "3600").code == 0


def test_detect_without_token_source_exits_2(env, clock):
    r = env.run("setup", "detect-chat", "--json")
    assert (r.code, r.json["error"]) == (2, "missing_token_source")


def test_detect_tty_stdin_refused(env, clock):
    r = env.run("setup", "detect-chat", "--token-stdin", "--json",
                stdin=TOKEN, tty=True)
    assert (r.code, r.json["error"]) == (2, "token_stdin_is_tty")


def test_detect_never_prompts(env, clock):
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    _detect(env, json_out=False)
    assert (env.traps, env.interactive_calls) == ([], [])


def test_detect_json_stdout_is_one_object(env, clock):
    import json
    env.tg.set("getUpdates", ok([_msg(ADA, T0 - 5)]))
    assert isinstance(json.loads(_detect(env).out), dict)


@pytest.mark.parametrize("case", ["timeout", "daemon", "conflict", "bad"])
def test_detect_error_json_has_every_key(env, clock, case):
    if case == "daemon":
        env.daemon_pid = 4242
    if case == "conflict":
        env.tg.set("getUpdates", HTTP(409, "Conflict"))
    if case == "bad":
        env.tg.bots.clear()
    d = _detect(env, "--timeout", "0").json
    assert ([k for k in DETECT_KEYS if k not in d], d["ok"],
            d["status"]) == ([], False, "error")


@pytest.mark.parametrize("case", ["found", "timeout", "daemon", "conflict"])
def test_detect_exit_code_matches_json(env, clock, case):
    if case == "found":
        env.tg.set("getUpdates", ok([_msg(ADA, T0)]))
    if case == "daemon":
        env.daemon_pid = 4242
    if case == "conflict":
        env.tg.set("getUpdates", HTTP(409, "Conflict"))
    r = _detect(env, "--timeout", "0")
    assert r.json["exit_code"] == r.code


def test_network_on_single_poll_exits_7(env, clock):
    env.tg.set("getUpdates", NET())
    assert _detect(env, "--timeout", "0").code == 7


def test_rate_limited_keeps_polling(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return HTTP(429, "Too Many Requests: retry after 1") if len(
            polls) < 2 else ok([_msg(ADA, T0)])
    env.tg.set("getUpdates", _resp)
    assert _detect(env, "--timeout", "30").code == 0


def test_future_dated_message_is_accepted(env, clock):
    """Clock skew: a message dated after the command started counts."""
    env.tg.set("getUpdates", ok([_msg(ADA, T0 + 30)]))
    assert _detect(env, "--timeout", "0").code == 0


def test_timeout_json_has_next_step(env, clock):
    d = _detect(env, "--timeout", "0").json
    assert isinstance(d["next_step"], str) and d["next_step"]


def test_detect_daemon_source_reports_source_in_json(env, clock):
    env.daemon_pid = 4242
    d = _detect(env).json
    assert (d["source"], d["candidate"]) == ("daemon", None)


def test_detect_leaves_the_token_file(env, clock):
    tf = env.token_file()
    before = tf.read_bytes()
    _detect(env, "--timeout", "0", token_file=tf)
    assert tf.read_bytes() == before
