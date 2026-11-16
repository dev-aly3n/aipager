"""Unit tests for `aipager setup detect-group` (aipager/setup_detect.py).

getMe/getUpdates go through a fake ``telegram_api._http_json``; the
poll loop's clock and sleep through ``setup_detect``'s own seams; the
daemon source reads a pending-users file under tmp.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pytest

from aipager import scope as scope_mod
from aipager.scope import Member, Scope

TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"
SECRET = TOKEN.split(":", 1)[1]
NOW = 1_700_000_000
KEYS = {"command", "status", "ok", "exit_code", "error", "message", "fix",
        "bot_username", "source", "candidate", "other_candidates", "warnings",
        "next_step"}
KNOWN = -100555


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


def _gmsg(chat_id, date, *, kind="supergroup", title="Team", edited=False):
    chat = {"id": chat_id, "type": kind, "title": title}
    key = "edited_message" if edited else "message"
    return {"update_id": abs(chat_id) + date,
            key: {"chat": chat, "date": date, "text": "/start"}}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Env:
    def __init__(self, tmp_path, monkeypatch, capsys):
        from aipager import setup_detect, team
        from aipager.wizard import telegram_api

        self.tmp, self.mp, self.capsys = tmp_path, monkeypatch, capsys
        self.urls: list[str] = []
        self.updates: list = []
        self.polls: list = []
        self.sends: list = []
        self.get_me = ({"ok": True, "result": {"id": 1, "username": "example_bot"}},
                       200, "")

        def _http_json(url):
            self.urls.append(url)
            if url.endswith("/getMe"):
                return self.get_me
            if self.polls:
                return self.polls.pop(0)
            return {"ok": True, "result": list(self.updates)}, 200, ""

        monkeypatch.setattr(telegram_api, "_http_json", _http_json)
        monkeypatch.setattr(telegram_api, "_send_message",
                            lambda *a: self.sends.append(a) or (True, "", 200))
        self.source = "telegram"
        monkeypatch.setattr(telegram_api, "_watch_source", lambda: self.source)
        self.clock = [0.0]
        self.sleeps: list[float] = []

        def _sleep(s):
            self.sleeps.append(s)
            self.clock[0] += s

        monkeypatch.setattr(setup_detect, "_now", lambda: float(NOW))
        monkeypatch.setattr(setup_detect, "_monotonic", lambda: self.clock[0])
        monkeypatch.setattr(setup_detect, "_sleep", _sleep)
        self.pending = tmp_path / "pending-users.json"
        monkeypatch.setattr(team, "PENDING_USERS_PATH", self.pending)

    def install(self):
        dm = Scope(chat_id=111, kind="dm", label="owner DM",
                   members=(Member(id=111, label="owner", role="owner"),))
        known = Scope(chat_id=KNOWN, kind="group", label="known", root="/srv/k",
                      members=(Member(id=111, label="alice", role="owner"),))
        scope_mod.dump_scopes([dm, known], TOKEN, scope_mod.CONFIG_PATH)

    def write_pending(self, records):
        self.pending.write_text(json.dumps(records))

    def run(self, *extra, as_json=True):
        self.capsys.readouterr()
        argv = ["aipager", "setup", "detect-group", *extra]
        if as_json:
            argv.append("--json")
        self.mp.setattr(sys, "argv", argv)
        from aipager import cli
        try:
            cli.main()
            code = "returned"
        except SystemExit as e:
            code = e.code
        out = self.capsys.readouterr()
        doc = None
        if as_json:
            try:
                doc = json.loads(out.out)
            except ValueError:
                doc = None
        return code, doc, out.out, out.err


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, monkeypatch, capsys)
    e.install()
    return e


# ----- telegram source -----

def test_finds_the_newest_new_group(env):
    env.updates = [_gmsg(-100001, NOW - 60, title="Old"),
                   _gmsg(-100002, NOW - 5, title="New")]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 0 and set(doc) == KEYS
    assert doc["command"] == "detect-group" and doc["source"] == "telegram"
    assert doc["candidate"] == {"id": -100002, "title": "New", "type": "supergroup",
                                "date": NOW - 5}
    assert doc["other_candidates"] == 1
    assert "aipager setup group --chat-id -100002 --member ID:LABEL:ROLE" in doc["next_step"]


def test_groups_already_set_up_are_skipped(env):
    env.updates = [_gmsg(-100001, NOW - 60, kind="group"), _gmsg(KNOWN, NOW - 5)]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 0 and doc["candidate"]["id"] == -100001
    assert doc["candidate"]["type"] == "group"


def test_only_known_groups_says_so(env):
    env.updates = [_gmsg(KNOWN, NOW - 5)]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8 and doc["error"] == "detect_timeout"
    assert "already set up" in doc["message"]


def test_old_messages_are_ignored(env):
    env.updates = [_gmsg(-100001, NOW - 601)]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8 and "already set up" not in doc["message"]


def test_private_chats_are_not_groups(env):
    env.updates = [{"update_id": 1, "message": {
        "chat": {"id": 42, "type": "private"}, "date": NOW, "text": "/start"}}]
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8


def test_never_sends_an_offset_or_a_message(env):
    env.updates = [_gmsg(-100001, NOW - 5)]
    env.run("--timeout", "0")
    assert env.sends == []
    assert not any("offset" in u for u in env.urls)
    assert not any("sendMessage" in u for u in env.urls)


def test_polls_until_found(env):
    env.polls = [({"ok": True, "result": []}, 200, ""),
                 ({"ok": True, "result": [_gmsg(-100001, NOW)]}, 200, "")]
    code, doc, _o, _e = env.run("--timeout", "10")
    assert code == 0 and env.sleeps == [2]


@pytest.mark.parametrize("poll,code,error", [
    (({"ok": False}, 409, "Conflict"), 10, "updates_conflict"),
    (({"ok": False}, 401, "Unauthorized"), 4, "token_rejected"),
    ((None, None, "network: down"), 7, "telegram_unreachable"),
    ((None, 502, "bad gateway"), 7, "telegram_unreachable"),
])
def test_failures(env, poll, code, error):
    env.polls = [poll]
    rc, doc, _o, _e = env.run("--timeout", "0")
    assert rc == code and doc["error"] == error


def test_get_me_rejected(env):
    env.get_me = ({"ok": False}, 401, "")
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 4 and doc["error"] == "token_rejected"


def test_backlog_warning(env):
    env.updates = [_gmsg(-100001, NOW - 7200)] * 100
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8
    assert [w["code"] for w in doc["warnings"]] == ["updates_backlog"]


def test_plain_output(env):
    env.updates = [_gmsg(-100002, NOW - 5, title="New")]
    code, _d, out, err = env.run("--timeout", "0", as_json=False)
    assert code == 0 and "  id:       -100002" in out and "  title:    New" in out
    assert SECRET not in out + err


# ----- daemon source -----

@pytest.mark.parametrize("ctype", ["group", "supergroup"])
def test_daemon_source_reports_the_records_chat_type(env, ctype):
    env.source = "daemon"
    env.write_pending([
        {"user_id": 7, "chat_id": -100009, "chat_type": ctype,
         "chat_title": "Crew", "first_seen": _iso(NOW - 30)},
    ])
    _code, doc, _o, _e = env.run("--timeout", "0")
    assert doc["candidate"]["type"] == ctype


def test_daemon_source_reads_the_pending_records(env):
    env.source = "daemon"
    env.write_pending([
        {"user_id": 7, "chat_id": -100009, "chat_type": "supergroup",
         "chat_title": "Crew", "first_seen": _iso(NOW - 30)},
    ])
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 0 and doc["source"] == "daemon"
    assert doc["candidate"] == {"id": -100009, "title": "Crew", "type": "supergroup",
                                "date": None}
    assert not any("getUpdates" in u for u in env.urls)


def test_daemon_source_look_back_and_exclude(env):
    env.source = "daemon"
    env.write_pending([
        {"user_id": 7, "chat_id": -100009, "chat_type": "group",
         "chat_title": "Old", "first_seen": _iso(NOW - 3600)},
        {"user_id": 8, "chat_id": -100010, "chat_type": "group",
         "chat_title": "No time"},
        {"user_id": 9, "chat_id": KNOWN, "chat_type": "supergroup",
         "chat_title": "Known", "first_seen": _iso(NOW - 10)},
    ])
    code, doc, _o, _e = env.run("--timeout", "0")
    assert code == 8 and "already set up" in doc["message"]


def test_wizard_pending_reader_still_reads_every_record(env):
    from aipager.wizard import telegram_api
    env.write_pending([{"user_id": 8, "chat_id": -100010, "chat_type": "group",
                        "chat_title": "No time"}])
    cid, title, _adv = telegram_api._fetch_id_from_pending(want="group")
    assert (cid, title) == (-100010, "No time")


# ----- flags and install -----

@pytest.mark.parametrize("extra", [
    ("--token-file", "/x"), ("--token-stdin",), ("--chat-id", "-1"),
    ("--member", "5:x:user"), ("--force",), ("--dry-run",), ("--role", "owner"),
    ("--service",), ("--label", "x"), ("--root", "/srv/x"), ("--block-edits",),
    ("--remove-member", "5"),
])
def test_refuses_other_flags(env, extra):
    code, doc, _o, _e = env.run(*extra)
    assert code == 2 and doc["error"] == "usage"
    assert env.urls == []


def test_bad_timeout(env):
    code, doc, _o, _e = env.run("--timeout", "-1")
    assert code == 2 and doc["error"] == "bad_timeout"


def test_token_flag_never_printed(env):
    code, doc, out, err = env.run("--token", TOKEN)
    assert code == 2 and doc["error"] == "token_on_command_line"
    assert SECRET not in out + err
