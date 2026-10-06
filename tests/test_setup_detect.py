"""Unit tests for `aipager setup detect-chat` (aipager/setup_detect.py).

getUpdates/getMe go through a fake ``telegram_api._http_json``; the poll
loop's clock and sleep through ``setup_detect``'s own seams.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from aipager import scope as scope_mod
from aipager.scope import Member, Scope


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    """Never let a check here reach the operator's live daemon socket."""
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"
SECRET = TOKEN.split(":", 1)[1]
OTHER = "987654321:BBQw8eRt5yU1iO9pAs3dFg7hJk2lZx4cV6b"
NOW = 1_700_000_000
KEYS = {"command", "status", "ok", "exit_code", "error", "message", "fix",
        "bot_username", "source", "candidate", "other_candidates", "warnings",
        "next_step"}


def _msg(chat_id, date, *, kind="private", first="Ada", username="ada"):
    chat = {"id": chat_id, "type": kind, "first_name": first}
    if username:
        chat["username"] = username
    return {"update_id": chat_id + date,
            "message": {"chat": chat, "date": date, "text": "/start"}}


class Env:
    def __init__(self, tmp_path, monkeypatch, capsys):
        from aipager import setup_detect
        from aipager.wizard import telegram_api

        self.tmp, self.mp, self.capsys = tmp_path, monkeypatch, capsys
        self.urls: list[str] = []
        self.updates: list = []
        self.polls: list = []          # per-poll override: (body, code, err)
        self.sends: list = []

        def _http_json(url):
            self.urls.append(url)
            if url.endswith("/getMe"):
                return {"ok": True, "result": {"username": "example_bot"}}, 200, ""
            if self.polls:
                return self.polls.pop(0)
            return {"ok": True, "result": list(self.updates)}, 200, ""

        monkeypatch.setattr(telegram_api, "_http_json", _http_json)
        monkeypatch.setattr(telegram_api, "_send_message",
                            lambda *a: self.sends.append(a) or (True, "", 200))
        monkeypatch.setattr(telegram_api, "_watch_source", lambda: "telegram")
        self.clock = [0.0]
        self.sleeps: list[float] = []

        def _sleep(s):
            self.sleeps.append(s)
            self.clock[0] += s

        monkeypatch.setattr(setup_detect, "_now", lambda: float(NOW))
        monkeypatch.setattr(setup_detect, "_monotonic", lambda: self.clock[0])
        monkeypatch.setattr(setup_detect, "_sleep", _sleep)
        self.token_file = tmp_path / "token.txt"
        self.token_file.write_text(TOKEN)
        os.chmod(self.token_file, 0o600)

    def run(self, *extra, as_json=True):
        self.capsys.readouterr()
        argv = ["aipager", "setup", "detect-chat", "--token-file",
                str(self.token_file), *extra]
        if as_json:
            argv.append("--json")
        self.mp.setattr(sys, "argv", argv)
        from aipager import cli
        try:
            cli.main()
            code = "returned"
        except SystemExit as e:
            code = e.code
        except Exception as e:
            code = f"raised {type(e).__name__}"
        out = self.capsys.readouterr()
        doc = None
        if as_json:
            try:
                doc = json.loads(out.out)
            except ValueError:
                pass
        return code, doc, out.out, out.err

    def snapshot(self):
        return {str(p): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in sorted(self.tmp.rglob("*")) if p.is_file()}

    def update_urls(self):
        return [u for u in self.urls if "/getUpdates" in u]


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    return Env(tmp_path, monkeypatch, capsys)


def test_found_prints_candidate_and_writes_nothing(env):
    env.updates = [_msg(5555, NOW - 30)]
    before = env.snapshot()
    code, doc, out, err = env.run()
    assert code == 0, "unexpected exit code"
    assert set(doc) == KEYS
    assert doc["status"] == "found" and doc["source"] == "telegram"
    assert doc["candidate"] == {"id": 5555, "first_name": "Ada", "last_name": None,
                                "username": "ada", "date": NOW - 30}
    assert f"--token-file {env.token_file} --chat-id 5555" in doc["next_step"]
    if TOKEN in out + err or SECRET in out + err:
        pytest.fail("token leaked", pytrace=False)
    assert env.snapshot() == before
    assert env.sends == []
    assert not scope_mod.CONFIG_PATH.exists()


def test_plain_output_names_the_candidate(env):
    env.updates = [_msg(5555, NOW - 30)]
    code, _doc, out, err = env.run(as_json=False)
    assert code == 0
    assert "5555" in out and "Ada" in out
    assert "Watching Telegram directly" in err


def test_getupdates_url_never_carries_a_query(env):
    env.updates = []
    code, doc, _o, _e = env.run("--timeout", "4")
    assert code == 8 and doc["error"] == "detect_timeout"
    urls = env.update_urls()
    assert len(urls) >= 2
    for u in urls:
        assert "?" not in u and "offset" not in u


def test_lookback_boundary(env):
    from aipager import setup_detect
    from aipager.wizard import telegram_api
    nb = NOW - setup_detect.DETECT_LOOKBACK_SECONDS
    env.updates = [_msg(1, nb - 1), _msg(2, nb)]
    cand, others, _c, _e, _f = telegram_api._newest_private_chat(TOKEN, not_before=nb)
    assert cand is not None, "a message dated exactly not_before must count"
    assert cand["id"] == 2 and others == 0
    env.updates = [_msg(1, nb - 1)]
    cand, others, _c, _e, _f = telegram_api._newest_private_chat(TOKEN, not_before=nb)
    assert cand is None


def test_old_message_is_ignored_by_the_command(env):
    from aipager import setup_detect
    env.updates = [_msg(1, NOW - setup_detect.DETECT_LOOKBACK_SECONDS - 1)]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8


def test_newest_private_wins_and_others_are_counted(env):
    from aipager.wizard import telegram_api
    env.updates = [_msg(1, NOW - 50), _msg(-100, NOW - 5, kind="group"),
                   _msg(2, NOW - 10), _msg(3, NOW - 40)]
    cand, others, _c, _e, _f = telegram_api._newest_private_chat(TOKEN,
                                                             not_before=NOW - 600)
    assert cand["id"] == 2 and others == 2


def test_found_on_a_later_poll(env):
    env.polls = [({"ok": True, "result": []}, 200, "")]
    env.updates = [_msg(5555, NOW + 3)]
    code, doc, _o, _e = env.run("--timeout", "60")
    assert code == 0 and doc["candidate"]["id"] == 5555
    assert env.sleeps == [2]


def test_timeout_zero_is_one_poll(env):
    code, _doc, _o, _e = env.run("--timeout", "0")
    assert code == 8 and len(env.update_urls()) == 1 and env.sleeps == []


def test_conflict_exits_10(env):
    env.polls = [({"ok": False, "description": "Conflict"}, 409, "Conflict")]
    code, doc, _o, _e = env.run("--timeout", "30")
    assert code == 10 and doc["error"] == "updates_conflict"


def test_rejected_token_during_poll_exits_4(env):
    env.polls = [({"ok": False}, 401, "Unauthorized")]
    code, doc, _o, _e = env.run("--timeout", "30")
    assert code == 4


def test_network_failure_at_deadline_exits_7(env):
    env.polls = [(None, None, "network: down")] * 3
    code, doc, _o, _e = env.run("--timeout", "4")
    assert code == 7 and doc["error"] == "telegram_unreachable"


def test_network_failure_then_quiet_is_a_timeout(env):
    env.polls = [(None, None, "network: down")]
    code, doc, _o, _e = env.run("--timeout", "4")
    assert code == 8


@pytest.mark.parametrize("value", ["-1", "abc", "3601", "1.5"])
def test_bad_timeout(env, value):
    code, doc, _o, _e = env.run(f"--timeout={value}")
    assert code == 2 and doc["error"] == "bad_timeout"
    assert env.urls == []


def _daemon(env):
    from aipager.wizard import telegram_api
    env.mp.setattr(telegram_api, "_watch_source", lambda: "daemon")


def test_daemon_with_the_same_token_exits_9(env):
    _daemon(env)
    scope_mod.dump_scopes([Scope(chat_id=1, kind="dm", label="owner DM",
                                 members=(Member(id=1, label="o", role="owner"),))],
                          TOKEN, scope_mod.CONFIG_PATH)
    before = env.snapshot()
    code, doc, _o, _e = env.run()
    assert code == 9 and doc["error"] == "daemon_running"
    assert doc["source"] == "daemon"
    assert "aipager service stop" in doc["fix"]
    assert env.update_urls() == []
    assert env.snapshot() == before


def test_daemon_with_unknown_config_uses_the_daemon(env):
    _daemon(env)
    code, doc, _o, _e = env.run()
    assert code == 9 and doc["source"] == "daemon"


def test_daemon_with_another_token_polls_telegram(env):
    _daemon(env)
    scope_mod.dump_scopes([Scope(chat_id=1, kind="dm", label="owner DM",
                                 members=(Member(id=1, label="o", role="owner"),))],
                          OTHER, scope_mod.CONFIG_PATH)
    env.updates = [_msg(5555, NOW)]
    code, doc, _o, _e = env.run()
    assert code == 0 and doc["source"] == "telegram"


def test_daemon_with_v1_token_compares_config_env(env):
    from aipager.wizard import daemon_io
    _daemon(env)
    env.mp.setattr(daemon_io, "_read_env_file", lambda: (OTHER, "1"))
    env.updates = [_msg(5555, NOW)]
    code, doc, _o, _e = env.run()
    assert code == 0 and doc["source"] == "telegram"


def test_daemon_source_that_finds_an_id_succeeds(env, monkeypatch):
    from aipager.wizard import telegram_api
    _daemon(env)
    monkeypatch.setattr(telegram_api, "_detect_id",
                        lambda token, *, want, source: (77, "bob", None))
    code, doc, _o, _e = env.run()
    assert code == 0 and doc["candidate"]["id"] == 77
    assert doc["candidate"]["username"] == "bob"


# ----- fix iteration 2 -----

def test_empty_timeout_is_a_bad_timeout(env):
    code, doc, _o, _e = env.run("--timeout", "")
    assert code == 2 and doc["error"] == "bad_timeout"
    assert env.urls == []


def _old_updates(n):
    # Older than the lookback window, so none is a candidate.
    return [_msg(1000 + i, NOW - 3600) for i in range(n)]


def test_full_batch_of_old_updates_says_the_backlog_hides_new_ones(env):
    env.updates = _old_updates(100)
    code, doc, _o, _e = env.run("--timeout", "4")
    assert code == 8 and doc["error"] == "detect_timeout"
    assert [w["code"] for w in doc["warnings"]] == ["updates_backlog"]
    assert "oldest 100" in doc["message"]
    assert "--chat-id" in doc["fix"] and "press Start" not in doc["fix"]


def test_a_batch_under_100_is_a_plain_timeout(env):
    env.updates = _old_updates(99)
    code, doc, _o, _e = env.run("--timeout", "4")
    assert code == 8 and doc["warnings"] == []
    assert "press Start" in doc["fix"]


def test_backlog_is_judged_by_the_last_answered_poll(env):
    full = ({"ok": True, "result": _old_updates(100)}, 200, "")
    env.polls = [full, full]          # then empty batches until the deadline
    code, doc, _o, _e = env.run("--timeout", "6")
    assert code == 8 and doc["warnings"] == []


def test_backlog_shows_in_plain_output(env):
    env.updates = _old_updates(100)
    code, _doc, _o, err = env.run("--timeout", "0", as_json=False)
    assert code == 8 and "oldest 100" in err
