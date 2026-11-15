"""design.md success criterion 14: ``setup detect-group`` finds the newest
group (not already configured) that sent a message in the last 10
minutes, from the daemon's pending records while a daemon runs or from
getUpdates otherwise, without an offset; exit codes 0/4/7/8/10 as for
detect-chat; saves nothing; refuses every other setup flag."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from setup_group_support import (
    BOT, DETECT_KEYS, GROUP, HTTP, NET, OWNER, dm_scope, group_scope, ok,
)

T0 = 1_800_000_000
CONFIGURED = -100777


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        if len(self.sleeps) > 5000:
            raise AssertionError("detect-group looped without end")
        self.mono += s


@pytest.fixture
def clock(env, monkeypatch):
    import aipager.setup_detect as sd
    c = Clock()
    monkeypatch.setattr(sd, "_now", lambda: float(T0))
    monkeypatch.setattr(sd, "_monotonic", lambda: c.mono)
    monkeypatch.setattr(sd, "_sleep", c.sleep)
    return c


@pytest.fixture
def inst(env, clock):
    env.install(scopes=[dm_scope(), group_scope(
        [(OWNER, "alice", "owner")], chat_id=CONFIGURED, label="known")])
    return env


def _gmsg(chat_id, date, *, title="Team", ctype="supergroup", uid=5,
          update_id=None, key="message"):
    return {"update_id": update_id or abs(chat_id) % 100000 + date % 1000,
            key: {"message_id": 1, "date": date,
                  "chat": {"id": chat_id, "type": ctype, "title": title},
                  "from": {"id": uid, "is_bot": False, "first_name": "A"},
                  "text": "/start"}}


def _pmsg(uid, date):
    return {"update_id": uid % 1000 + date % 1000,
            "message": {"message_id": 1, "date": date,
                        "chat": {"id": uid, "type": "private",
                                 "first_name": "A"},
                        "from": {"id": uid, "is_bot": False,
                                 "first_name": "A"}, "text": "/start"}}


def _detect(env, *extra, json_out=True):
    args = ["setup", "detect-group", *extra]
    if json_out:
        args.append("--json")
    return env.run(*args)


# ---- found (telegram source) ----

def test_found_exits_0(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert _detect(inst).code == 0


def test_found_status_and_source(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    d = _detect(inst).json
    assert (d["status"], d["source"]) == ("found", "telegram")


def test_found_candidate_shape(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert _detect(inst).json["candidate"] == {
        "id": GROUP, "title": "Team", "type": "supergroup", "date": T0 - 5}


def test_found_json_keys(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert set(_detect(inst).json) == set(DETECT_KEYS)


def test_found_command_name(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert _detect(inst).json["command"] == "detect-group"


def test_found_bot_username(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert _detect(inst).json["bot_username"] == BOT


def test_found_basic_group(inst):
    inst.tg.set("getUpdates", ok([_gmsg(-4242, T0 - 5, ctype="group")]))
    c = _detect(inst).json["candidate"]
    assert (c["id"], c["type"]) == (-4242, "group")


def test_found_newest_of_several_groups(inst):
    # getUpdates answers in update_id order, oldest first.
    inst.tg.set("getUpdates", ok([
        _gmsg(-1001, T0 - 50, title="old", update_id=1),
        _gmsg(-1003, T0 - 20, title="mid", update_id=2),
        _gmsg(-1002, T0 - 5, title="new", update_id=3)]))
    assert _detect(inst).json["candidate"]["id"] == -1002


def test_newest_group_wins_over_a_later_old_message_of_another(inst):
    """Two messages of the same group around another group's: the group
    with the newest message is the candidate."""
    inst.tg.set("getUpdates", ok([
        _gmsg(-1001, T0 - 50, update_id=1),
        _gmsg(-1002, T0 - 20, update_id=2),
        _gmsg(-1001, T0 - 5, update_id=3)]))
    assert _detect(inst).json["candidate"]["id"] == -1001


def test_found_other_candidates_counted(inst):
    inst.tg.set("getUpdates", ok([
        _gmsg(-1001, T0 - 50, update_id=1),
        _gmsg(-1002, T0 - 5, update_id=2)]))
    assert _detect(inst).json["other_candidates"] >= 1


def test_found_next_step_names_setup_group(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    ns = _detect(inst).json["next_step"]
    assert f"aipager setup group --chat-id {GROUP}" in ns


def test_found_plain_output(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    r = _detect(inst, json_out=False)
    assert r.code == 0 and str(GROUP) in r.out


def test_skips_configured_group(inst):
    inst.tg.set("getUpdates", ok([
        _gmsg(GROUP, T0 - 50, update_id=1),
        _gmsg(CONFIGURED, T0 - 5, update_id=2)]))
    assert _detect(inst).json["candidate"]["id"] == GROUP


def test_only_configured_group_times_out_and_says_so(inst):
    inst.tg.set("getUpdates", ok([_gmsg(CONFIGURED, T0 - 5)]))
    r = _detect(inst, "--timeout", "0")
    d = r.json
    assert (r.code, d["error"]) == (8, "detect_timeout")


def test_only_configured_group_message_mentions_set_up(inst):
    inst.tg.set("getUpdates", ok([_gmsg(CONFIGURED, T0 - 5)]))
    d = _detect(inst, "--timeout", "0").json
    assert "already" in f"{d['message']} {d['fix']}".lower()


def test_private_chats_ignored(inst):
    inst.tg.set("getUpdates", ok([_pmsg(12345, T0 - 5)]))
    assert _detect(inst, "--timeout", "0").code == 8


def test_channel_posts_ignored(inst):
    inst.tg.set("getUpdates", ok([_gmsg(-100555, T0 - 5, ctype="channel",
                                        key="channel_post")]))
    assert _detect(inst, "--timeout", "0").code == 8


def test_lookback_boundary_600s_accepted(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 600)]))
    assert _detect(inst, "--timeout", "0").code == 0


def test_lookback_boundary_601s_refused(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 601)]))
    assert _detect(inst, "--timeout", "0").code == 8


def test_never_sends_offset(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    _detect(inst)
    assert all("offset" not in c.params for c in inst.tg.calls_to(
        "getUpdates"))


def test_never_sends_offset_while_polling(inst, clock):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 900, update_id=77)]))
    _detect(inst, "--timeout", "20")
    calls = inst.tg.calls_to("getUpdates")
    assert len(calls) >= 2 and all("offset" not in c.query for c in calls)


def test_saves_nothing(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    before = inst.snapshot()
    _detect(inst)
    assert inst.snapshot() == before


def test_never_reloads(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    _detect(inst)
    assert inst.reloads == []


def test_never_sends_a_message(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    _detect(inst)
    assert inst.tg.calls_to("sendMessage") == []


def test_only_getme_and_getupdates(inst):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    _detect(inst)
    assert set(inst.tg.methods) <= {"getMe", "getUpdates"}


# ---- exit codes ----

@pytest.mark.parametrize("method", ["getMe", "getUpdates"])
@pytest.mark.parametrize("status", [401, 404])
def test_token_rejected_exit_4(inst, method, status):
    inst.tg.set(method, HTTP(status, "Unauthorized"))
    r = _detect(inst, "--timeout", "0")
    assert (r.code, r.json["error"]) == (4, "token_rejected")


def test_getme_network_error_exit_7(inst):
    inst.tg.set("getMe", NET())
    r = _detect(inst, "--timeout", "0")
    assert (r.code, r.json["error"]) == (7, "telegram_unreachable")


def test_last_poll_network_error_exit_7(inst):
    inst.tg.set("getUpdates", NET())
    r = _detect(inst, "--timeout", "5")
    assert (r.code, r.json["error"]) == (7, "telegram_unreachable")


def test_conflict_exit_10(inst):
    inst.tg.set("getUpdates", HTTP(409, "Conflict: terminated by other "
                                        "getUpdates request"))
    r = _detect(inst, "--timeout", "0")
    assert (r.code, r.json["error"]) == (10, "updates_conflict")


def test_timeout_exit_8(inst):
    r = _detect(inst, "--timeout", "0")
    assert (r.code, r.json["error"]) == (8, "detect_timeout")


def test_timeout_candidate_null(inst):
    assert _detect(inst, "--timeout", "0").json["candidate"] is None


def test_timeout_waits_the_timeout(inst, clock):
    _detect(inst, "--timeout", "30")
    assert 25 <= sum(clock.sleeps) <= 40


def test_full_old_batch_warns_backlog(inst):
    batch = [_gmsg(-100000 - i, T0 - 5000, update_id=i + 1)
             for i in range(100)]
    inst.tg.set("getUpdates", ok(batch))
    r = _detect(inst, "--timeout", "0")
    assert (r.code, "updates_backlog" in r.warning_codes()) == (8, True)


def test_short_old_batch_no_backlog_warning(inst):
    batch = [_gmsg(-100000 - i, T0 - 5000, update_id=i + 1)
             for i in range(99)]
    inst.tg.set("getUpdates", ok(batch))
    r = _detect(inst, "--timeout", "0")
    assert "updates_backlog" not in r.warning_codes()


def test_message_arriving_during_the_wait_is_found(inst, clock):
    seq = [ok([]), ok([]), ok([_gmsg(GROUP, T0 + 3)])]

    def _spec(call):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    inst.tg.set("getUpdates", _spec)
    r = _detect(inst, "--timeout", "60")
    assert (r.code, r.json["candidate"]["id"]) == (0, GROUP)


# ---- --timeout bounds and refused flags ----

@pytest.mark.parametrize("t", ["0", "3600"])
def test_timeout_bounds_accepted(inst, t):
    inst.tg.set("getUpdates", ok([_gmsg(GROUP, T0 - 5)]))
    assert _detect(inst, "--timeout", t).code == 0


@pytest.mark.parametrize("t", ["-1", "3601", "abc", "1.5x", ""])
def test_timeout_out_of_range(inst, t):
    r = _detect(inst, f"--timeout={t}")
    assert (r.code, r.json["error"]) == (2, "bad_timeout")


@pytest.mark.parametrize("extra", [
    ("--chat-id", "-1001"), ("--member", "5:x:user"), ("--force",),
    ("--dry-run",), ("--label", "x"), ("--root", "/tmp/x"),
    ("--block-edits",), ("--remove-member", "5"), ("--role", "user"),
    ("--service",), ("--token-file", "f"), ("--token-stdin",),
])
def test_other_flags_refused(inst, extra):
    r = _detect(inst, *extra)
    assert (r.code, r.json["error"]) == (2, "usage")


def test_refused_flag_makes_no_bot_api_call(inst):
    _detect(inst, "--force")
    assert inst.tg.calls == []


# ---- daemon source ----

def _record(chat_id, title="new team", ctype="supergroup", uid=5):
    from aipager import team
    team.record_pending_user(uid, "u", "U", chat_id,
                             chat_title=title, chat_type=ctype)


def _age_records(seconds):
    """Move every pending record's first_seen back by *seconds*."""
    import aipager.team as team
    from pathlib import Path
    p = Path(team.PENDING_USERS_PATH)
    recs = json.loads(p.read_text())
    for r in recs:
        r["first_seen"] = (datetime.now(timezone.utc) - timedelta(
            seconds=seconds)).isoformat(timespec="seconds")
    p.write_text(json.dumps(recs))


@pytest.fixture
def daemon(env, monkeypatch):
    import aipager.setup_detect as sd
    c = Clock()
    now = time.time()
    monkeypatch.setattr(sd, "_now", lambda: now)
    monkeypatch.setattr(sd, "_monotonic", lambda: c.mono)
    monkeypatch.setattr(sd, "_sleep", c.sleep)
    env.install(scopes=[dm_scope(), group_scope(
        [(OWNER, "alice", "owner")], chat_id=CONFIGURED, label="known")])
    env.daemon_pid = 4242
    return env


def test_daemon_source_found(daemon):
    _record(GROUP)
    d = _detect(daemon, "--timeout", "0").json
    assert (d["status"], d["source"], d["candidate"]["id"]) == (
        "found", "daemon", GROUP)


def test_daemon_source_date_null(daemon):
    _record(GROUP)
    assert _detect(daemon, "--timeout", "0").json["candidate"]["date"] is None


def test_daemon_source_title(daemon):
    _record(GROUP, title="new team")
    c = _detect(daemon, "--timeout", "0").json["candidate"]
    assert c["title"] == "new team"


def test_daemon_source_type(daemon):
    """Only ``date`` is documented as null from the daemon source; the
    daemon's record carries the chat type."""
    _record(GROUP, title="new team", ctype="supergroup")
    c = _detect(daemon, "--timeout", "0").json["candidate"]
    assert c["type"] == "supergroup"


def test_daemon_source_never_polls_telegram(daemon):
    _record(GROUP)
    _detect(daemon, "--timeout", "0")
    assert daemon.tg.calls_to("getUpdates") == []


def test_daemon_source_skips_configured(daemon):
    _record(CONFIGURED)
    r = _detect(daemon, "--timeout", "0")
    assert r.code == 8


def test_daemon_source_skips_old_records(daemon):
    _record(GROUP)
    _age_records(11 * 60)
    assert _detect(daemon, "--timeout", "0").code == 8


def test_daemon_source_fresh_record_within_lookback(daemon):
    _record(GROUP)
    _age_records(9 * 60)
    assert _detect(daemon, "--timeout", "0").code == 0


def test_daemon_source_ignores_private_records(daemon):
    _record(12345, title="", ctype="private")
    assert _detect(daemon, "--timeout", "0").code == 8


def test_daemon_source_saves_nothing(daemon):
    _record(GROUP)
    before = daemon.snapshot()
    _detect(daemon, "--timeout", "0")
    assert daemon.snapshot() == before


def test_daemon_source_never_exit_9(daemon):
    r = _detect(daemon, "--timeout", "0")
    assert r.code != 9


def test_daemon_source_unreadable_first_seen_skipped(daemon):
    import aipager.team as team
    from pathlib import Path
    _record(GROUP)
    p = Path(team.PENDING_USERS_PATH)
    recs = json.loads(p.read_text())
    for r in recs:
        r["first_seen"] = "not a date"
    p.write_text(json.dumps(recs))
    assert _detect(daemon, "--timeout", "0").code == 8
