"""design.md success criteria 12 and 13: reload outcomes after a write,
the daemon never restarted; the bot token never in stdout or stderr,
plain or JSON, on any path."""

from __future__ import annotations

import json
import os
import re

import pytest
import yaml

from setup_group_support import (
    GROUP, HTTP, NET, OWNER, SECRET, TOKEN, assert_no_secret, dm_scope,
    group_scope, m,
)

ADD = (m(OWNER, "alice", "owner"),)


@pytest.fixture
def inst(env):
    env.install()
    return env


# ---- criterion 12: reload outcomes ----

def test_refused_reload_warns_reload_refused(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = ("refused", "the new config does not load")
    r = inst.group_cmd(*ADD)
    assert "reload_refused" in r.warning_codes()


def test_refused_reload_outcome_not_applied(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = ("refused", "the new config does not load")
    r = inst.group_cmd(*ADD)
    assert (r.json["daemon"]["reload"], r.json["daemon"]["outcome"]) == (
        "refused", "not_applied")


def test_refused_reload_still_exit_0_and_written(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = ("refused", "x")
    r = inst.group_cmd(*ADD)
    assert (r.code, inst.group() is not None) == (0, True)


def test_refused_reload_plain_line(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = ("refused", "the new config does not load")
    r = inst.group_cmd(*ADD, json_out=False)
    assert re.search(r"daemon:\s+not applied: ", r.out)


def _pid_unknown(env):
    """A daemon runs but its PID is unknown: detection says -1 and the
    reload seam, which owns the "cannot tell which process is the daemon"
    fallback, sends nothing (entrypoints.md daemon seams)."""
    env.daemon_pid = -1
    env.reload_outcome = ("not_reloaded",
                          "cannot tell which process is the daemon")


def test_pid_unknown_restart_needed(inst):
    _pid_unknown(inst)
    r = inst.group_cmd(*ADD)
    assert r.json["daemon"]["restart_needed"] is True


def test_pid_unknown_outcome_restart_needed(inst):
    _pid_unknown(inst)
    r = inst.group_cmd(*ADD)
    assert r.json["daemon"]["outcome"] == "restart_needed"


def test_pid_unknown_plain_line(inst):
    _pid_unknown(inst)
    r = inst.group_cmd(*ADD, json_out=False)
    assert re.search(r"daemon:\s+restart needed", r.out)


def test_not_reloaded_restart_needed(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = ("not_reloaded", "no answer")
    r = inst.group_cmd(*ADD)
    assert (r.json["daemon"]["restart_needed"],
            r.json["daemon"]["outcome"]) == (True, "restart_needed")


def test_reload_raising_is_not_reloaded(inst):
    inst.daemon_pid = 4242
    inst.reload_outcome = OSError("boom")
    r = inst.group_cmd(*ADD)
    assert (r.code, r.json["daemon"]["reload"]) == (0, "not_reloaded")


def test_detection_failing_warns_daemon_unknown(inst):
    inst.daemon_pid = PermissionError("cannot read")
    r = inst.group_cmd(*ADD)
    assert "daemon_unknown" in r.warning_codes()


def test_detection_failing_sends_no_reload(inst):
    inst.daemon_pid = PermissionError("cannot read")
    inst.group_cmd(*ADD)
    assert inst.reloads == []


def test_detection_failing_still_writes(inst):
    inst.daemon_pid = PermissionError("cannot read")
    r = inst.group_cmd(*ADD)
    assert (r.code, inst.group() is not None) == (0, True)


def test_no_daemon_outcome(inst):
    r = inst.group_cmd(*ADD)
    assert r.json["daemon"]["outcome"] == "no_daemon"


def test_no_daemon_restart_not_needed(inst):
    inst.reload_outcome = ("not_reloaded", "no daemon")
    r = inst.group_cmd(*ADD)
    assert (r.json["daemon"]["restart_needed"],
            r.json["daemon"]["outcome"]) == (False, "no_daemon")


def test_no_daemon_running_false(inst):
    assert inst.group_cmd(*ADD).json["daemon"]["running"] is False


def test_no_daemon_plain_line(inst):
    r = inst.group_cmd(*ADD, json_out=False)
    assert re.search(r"daemon:\s+no daemon running", r.out)


def test_no_daemon_next_step_mentions_start(inst):
    r = inst.group_cmd(*ADD)
    assert "aipager start" in r.json["next_step"]


def test_refusal_never_detects_a_reload(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    env.daemon_pid = 4242
    env.group_cmd(m(222, "bob", "admin"))
    assert env.reloads == []


def test_never_restarts_or_installs_a_service(inst, monkeypatch):
    import aipager.service as svc
    hits = []
    for name in ("install_service", "restart_service", "stop_service",
                 "start_service"):
        if hasattr(svc, name):
            monkeypatch.setattr(svc, name,
                                lambda *a, _n=name, **k: hits.append(_n))
    inst.daemon_pid = -1
    inst.group_cmd(*ADD)
    assert hits == []


# ---- criterion 13: token never printed ----

def _scan(env, r):
    assert_no_secret(r.out, "stdout")
    assert_no_secret(r.err, "stderr")
    assert_no_secret(r.log, "logs")


PATHS = {
    "created": (ADD, {}),
    "force": ((m(OWNER, "alice", "admin"),), {}),
    "unknown_role": ((m(5, "x", "nope"),), {}),
    "shared": ((m(777000, "x", "user"),), {}),
    "bad_chat": (ADD, {"chat": 0}),
    "bad_root": (ADD + ("--root", "/"), {}),
    "dry": ((m(5, "x", "user"), "--dry-run"), {}),
    "last_member": (("--remove-member", str(OWNER)), {}),
    "usage": ((), {}),
}


@pytest.mark.parametrize("json_out", [True, False], ids=["json", "plain"])
@pytest.mark.parametrize("name", list(PATHS))
def test_token_never_printed_on_any_path(inst, name, json_out):
    inst.daemon_pid = 4242
    if name in ("force", "last_member"):
        inst.install(scopes=[dm_scope(), group_scope(
            [(OWNER, "alice", "owner")])])
    args, kw = PATHS[name]
    r = inst.group_cmd(*args, json_out=json_out, **kw)
    _scan(inst, r)


@pytest.mark.parametrize("json_out", [True, False], ids=["json", "plain"])
def test_token_never_printed_on_unchanged(inst, json_out):
    inst.group_cmd(*ADD)
    _scan(inst, inst.group_cmd(*ADD, json_out=json_out))


@pytest.mark.parametrize("json_out", [True, False], ids=["json", "plain"])
def test_token_never_printed_on_not_set_up_or_malformed(env, json_out):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text(f"bot_token: {TOKEN}\nscopes: [unclosed\n")
    _scan(env, env.group_cmd(*ADD, json_out=json_out))


TG_FAILURES = [
    ("getMe", HTTP(401, f"Unauthorized {TOKEN}")),
    ("getMe", NET(f"cannot reach https://api.telegram.org/bot{TOKEN}/getMe")),
    ("getChat", HTTP(400, f"Bad Request: chat not found bot{TOKEN}")),
    ("getChat", HTTP(403, f"Forbidden {SECRET}")),
    ("getChatMember", NET(f"timeout for /bot{TOKEN}/getChatMember")),
    ("getChat", HTTP(500, f"oops {TOKEN}")),
]


@pytest.mark.parametrize("json_out", [True, False], ids=["json", "plain"])
@pytest.mark.parametrize("method,spec", TG_FAILURES,
                         ids=[f"{a}-{i}" for i, (a, _) in
                              enumerate(TG_FAILURES)])
def test_token_echoed_by_telegram_is_never_printed(inst, method, spec,
                                                   json_out):
    inst.tg.set(method, spec)
    r = inst.group_cmd(*ADD, json_out=json_out)
    _scan(inst, r)


@pytest.mark.parametrize("outcome", [("refused", f"bad {TOKEN}"),
                                     ("not_reloaded", f"x {SECRET}")])
def test_token_in_a_reload_problem_is_never_printed(inst, outcome):
    inst.daemon_pid = 4242
    inst.reload_outcome = outcome
    for json_out in (True, False):
        inst.install()
        _scan(inst, inst.group_cmd(*ADD, json_out=json_out))


def test_token_in_internal_error_is_never_printed(inst, monkeypatch):
    import aipager.wizard.daemon_io as daemon_io

    def _boom():
        raise RuntimeError(f"crash with {TOKEN}")
    monkeypatch.setattr(daemon_io, "_live_reload", _boom)
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 4242)
    for json_out in (True, False):
        inst.install()
        _scan(inst, inst.group_cmd(*ADD, json_out=json_out))


def test_json_values_hold_no_token(inst):
    r = inst.group_cmd(*ADD)
    assert_no_secret(json.dumps(r.json), "parsed JSON")


def test_detect_group_never_prints_token(inst):
    inst.tg.set("getUpdates", HTTP(409, f"Conflict {TOKEN}"))
    for json_out in (True, False):
        args = ["setup", "detect-group", "--timeout", "0"]
        r = inst.run(*args, *(["--json"] if json_out else []))
        _scan(inst, r)


def test_detect_group_401_never_prints_token(inst):
    inst.tg.set("getMe", HTTP(401, f"Unauthorized {TOKEN}"))
    r = inst.run("setup", "detect-group", "--timeout", "0", "--json")
    _scan(inst, r)


def test_token_stored_once_and_nowhere_else(inst):
    inst.group_cmd(*ADD)
    for p in inst.files():
        text = p.read_bytes().decode("utf-8", "replace")
        if p == inst.config_path:
            doc = yaml.safe_load(text)
            doc.pop("bot_token", None)
            assert_no_secret(repr(doc), "aipager.yaml outside bot_token")
            if text.count(SECRET) != 1:
                pytest.fail("token not stored exactly once", pytrace=False)
        else:
            assert_no_secret(text, f"file {p.name}")


def test_token_never_put_into_the_environment(inst):
    inst.group_cmd(*ADD)
    environ = "\n".join(f"{k}={v}" for k, v in os.environ.items())
    assert_no_secret(environ, "os.environ")


def test_group_id_shaped_like_nothing_secret(inst):
    """The chat id is printed (it is no secret)."""
    r = inst.group_cmd(*ADD)
    assert str(GROUP) in r.json["message"] or r.json["chat_id"] == GROUP
