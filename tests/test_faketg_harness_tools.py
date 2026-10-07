"""The fake-Telegram harness's pure parts (tests/e2e/fake_telegram), with
no daemon: the instance config, the daemon environment and its isolation
check, the shims, the socket-length check, the stand-in claude, and the
kill helpers' refusal to signal anything that is not the instance's.

These guards are what keep a test daemon away from the operator's real
install, so each has a test that fails when the guard is removed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aipager import claude_resolve, scope
from tests.e2e.fake_telegram import instance as fti
from tests.e2e.fake_telegram import standin_claude
from tests.e2e.faketg import conftest as ftc


@pytest.fixture
def root():
    r = fti.make_root()
    try:
        yield r
    finally:
        shutil.rmtree(r, ignore_errors=True)


def test_make_root_is_a_short_apg_folder_in_tmp(root):
    assert root.parent == Path("/tmp") and root.name.startswith("apg-")
    assert not root.name.startswith("claude-")
    assert not str(root).startswith(fti.real_home())
    fti.assert_socket_lengths(root / "i", ["ft1", "ft14"])


def test_config_ids_roles_and_loads(tmp_path):
    cfg = fti.build_config("/x/claude")
    p = tmp_path / "aipager.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))
    scopes, token = scope.load_scopes(p)
    assert token == fti.FAKE_TOKEN
    by_kind = {s.kind: s for s in scopes}
    assert by_kind["dm"].chat_id == fti.DM_ID
    assert by_kind["group"].chat_id == fti.GROUP_ID
    roles = {m.id: str(getattr(m.role, "value", m.role)) for m in by_kind["group"].members}
    assert roles == {fti.ALICE: "owner", fti.BOB: "user", fti.CAROL: "read_only",
                     fti.DAVE: "admin"}
    assert scope.load_miniapp(p)["enabled"] is False
    assert scope.load_default_mode(p) == "ask"
    assert scope.load_claude_path(p) == "/x/claude"
    every_id = [fti.DM_ID, fti.GROUP_ID, fti.SUPERGROUP_ID, *fti.MEMBERS]
    assert fti.REAL_CHAT_ID not in every_id and -fti.REAL_CHAT_ID not in every_id


@pytest.mark.parametrize("bad", ["member", "dm", "group"])
def test_config_refuses_the_operators_real_chat_id(bad):
    members = dict(fti.MEMBERS)
    kw = {}
    if bad == "member":
        members[fti.REAL_CHAT_ID] = ("op", "user")
    elif bad == "dm":
        kw["dm_id"] = fti.REAL_CHAT_ID
    else:
        kw["group_id"] = -fti.REAL_CHAT_ID
    with pytest.raises(AssertionError, match="real chat id"):
        fti.build_config("/x/claude", members, **kw)


def _env(root, mode="standin", base=None, **extra):
    base_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": fti.real_home(),
        "CLAUDE_CODE_OAUTH_TOKEN": "x", "CLAUDE_TG_CHAT_ID": "256113222",
        "CLAUDE_TG_BOT_TOKEN": "1:real", "AIPAGER_SOCKET_PATH": "/run/x.sock",
        "CREDENTIALS_DIRECTORY": "/run/credentials/aipager.service",
        "OBSERVER_BOTS": "a:b", "MINIAPP_ENABLED": "1", "XDG_RUNTIME_DIR": "/run/user/1",
        "PYTHONPATH": "/elsewhere", "LANG": "C.UTF-8",
        "ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "http://elsewhere",
    }
    base_env.update(extra)
    return fti.daemon_env(base_env, root=root, base_url=base or "http://127.0.0.1:4321",
                          claude_bin=str(root / "b" / "claude"), repo=fti.REPO,
                          python=sys.executable, claude_mode=mode)


def test_daemon_env_is_built_from_scratch(root):
    env = _env(root)
    assert env["PATH"].split(os.pathsep)[0] == str(root / "b")
    assert env["PATH"].split(os.pathsep)[1] == str(Path(sys.executable).parent)
    assert env["CLAUDE_TG_CHAT_ID"] == "" and env["OBSERVER_BOTS"] == ""
    assert env["CLAUDE_TG_BOT_TOKEN"] == fti.FAKE_TOKEN
    for gone in ("CREDENTIALS_DIRECTORY", "CLAUDE_CODE_OAUTH_TOKEN", "AIPAGER_SOCKET_PATH",
                 "XDG_RUNTIME_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        assert gone not in env, gone
    assert env["HOME"] == str(root / "h") and env["AIPAGER_INSTANCE_DIR"] == str(root / "i")
    assert env["AIPAGER_WORK_DIR"] == str(root / "h" / "proj")
    assert env["PYTHONPATH"] == str(fti.REPO) and env["MINIAPP_ENABLED"] == "0"
    assert env["LANG"] == "C.UTF-8"
    assert env["PYTEST_CURRENT_TEST"]
    assert fti.env_problems(env, root) == []
    fti.assert_env_isolated(env, root)


def test_standin_env_drops_every_real_claude_from_path(root, tmp_path):
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "claude").write_text("#!/bin/sh\n")
    (real / "claude").chmod(0o755)
    env = _env(root, PATH=f"{real}:/usr/bin")
    assert str(real) not in env["PATH"].split(os.pathsep)
    env = _env(root, mode="real", PATH=f"{real}:/usr/bin")
    assert str(real) in env["PATH"].split(os.pathsep)


@pytest.mark.parametrize("mutate,problem", [
    (lambda e, r: e.update(PATH="/usr/bin:" + e["PATH"]), "shim folder is not first"),
    (lambda e, r: e.update(HOME=fti.real_home()), "HOME"),
    (lambda e, r: e.update(AIPAGER_INSTANCE_DIR="/tmp"), "AIPAGER_INSTANCE_DIR"),
    (lambda e, r: e.update(CLAUDE_TG_CHAT_ID="256113222"), "CLAUDE_TG_CHAT_ID"),
    (lambda e, r: e.pop("OBSERVER_BOTS"), "OBSERVER_BOTS"),
    (lambda e, r: e.update(CREDENTIALS_DIRECTORY="/run/c"), "CREDENTIALS_DIRECTORY"),
    (lambda e, r: e.update(AIPAGER_TELEGRAM_API_BASE="https://api.telegram.org"), "API base"),
    (lambda e, r: e.update(MINIAPP_ENABLED="1"), "Mini App"),
])
def test_env_isolation_check_catches_each_leak(root, mutate, problem):
    env = _env(root)
    mutate(env, root)
    problems = fti.env_problems(env, root)
    assert any(problem in p for p in problems), problems
    with pytest.raises(AssertionError, match="not isolated"):
        fti.assert_env_isolated(env, root)


def test_shims_run_this_repo(root):
    fti.write_shims(root / "b", sys.executable, fti.REPO, "standin")
    hook = (root / "b" / "aipager-hook").read_text()
    assert hook.startswith("#!/bin/sh\n")
    assert sys.executable in hook and "aipager.dtach.notify_hook import main" in hook
    assert f"PYTHONPATH={fti.REPO}" in hook
    assert "statusline_notify import main" in (root / "b" / "aipager-statusline").read_text()
    assert "aipager.cli import main" in (root / "b" / "aipager").read_text()
    assert str(fti.STANDIN) in (root / "b" / "claude").read_text()
    for name in ("aipager", "aipager-hook", "aipager-statusline", "claude"):
        assert os.access(root / "b" / name, os.X_OK)
    fti.write_shims(root / "c", sys.executable, fti.REPO, "real")
    assert not (root / "c" / "claude").exists()


def test_socket_length_check_refuses_a_long_folder(tmp_path):
    with pytest.raises(AssertionError, match="socket path too long"):
        fti.assert_socket_lengths(tmp_path / ("x" * 90), ["ft1"])


def test_standin_cli_answers_like_claude(root):
    fti.write_shims(root / "b", sys.executable, fti.REPO, "standin")
    claude = str(root / "b" / "claude")
    out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and claude_resolve._VERSION_RE.match(out.stdout.strip())
    install, why = claude_resolve._verify_candidate(claude)
    assert install is not None, why
    auth = subprocess.run([claude, "auth", "status"], capture_output=True, text=True,
                          timeout=30)
    assert json.loads(auth.stdout)["loggedIn"] is True
    probe = subprocess.run([claude, "-p", "say ok"], capture_output=True, text=True,
                           timeout=30)
    assert probe.returncode == 0 and probe.stdout.strip() == "ok"


def test_standin_turn_writes_a_transcript_aipager_reads(tmp_path, monkeypatch):
    from aipager import transcript
    from aipager.dtach import enforce
    home, inst, proj = tmp_path / "h", tmp_path / "i", tmp_path / "p"
    for d in (home / ".claude", inst, proj):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(inst))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-ftt__d1")
    monkeypatch.chdir(proj)
    s = standin_claude.StandIn(["--dangerously-skip-permissions"])
    s.run_turn("[via Telegram · @alice]\nReply with exactly: OK")
    assert transcript.extract_last_response(s.transcript) == "OK"
    s.run_turn("Use the Write tool to create ftt.txt containing hi")
    assert (proj / "ftt.txt").read_text() == "hi"
    entries = [json.loads(line) for line in Path(s.transcript).read_text().splitlines()]
    users = [enforce._user_text(e) for e in entries if e["type"] == "user"
             and not enforce._is_tool_result(e)]
    assert users[0].startswith("[via Telegram · @alice]")
    log = [json.loads(line) for line in (inst / "standin-claude-ftt__d1.jsonl").read_text()
           .splitlines()]
    assert [e["event"] for e in log] == ["prompt", "stop", "prompt", "decision", "stop"]
    assert log[3]["decision"] == "allowed"


def _sleeper(env=None):
    return subprocess.Popen(["sleep", "30"], env=env)


_SLEEP = "import time; time.sleep(30)"


def _named(argv0: str, *args: str, env=None):
    """A sleeping python whose /proc cmdline is ``argv0 -c <sleep> args``
    (``argv0`` may be ``dtach``: nothing real is run). Waits for the exec
    so the cmdline checked is the new one."""
    p = subprocess.Popen([argv0, "-c", _SLEEP, *args], executable=sys.executable,
                         env=env, stdin=subprocess.DEVNULL)
    want = os.fsencode(argv0) + b"\0-c\0"
    fti.wait_until(lambda: fti._read(f"/proc/{p.pid}/cmdline").startswith(want), 10,
                   f"the {argv0} test process to exec")
    return p


def test_kill_helpers_refuse_a_process_that_is_not_the_instances(root):
    inst = root / "i"
    inst.mkdir()
    sock = str(inst / "claude-dtach-ft1__g4000000001.sock")
    clean = {k: v for k, v in os.environ.items() if k != "AIPAGER_INSTANCE_DIR"}
    procs = []
    try:
        # Strangers: a bare process, ones that only NAME the folder (an
        # operator's tail -f on the log, a grep for a socket), a dtach on
        # a socket outside the folder or on a non-socket file in it, and
        # one whose instance variable is a different folder.
        strangers = [
            _sleeper(clean),
            _named("tail", str(inst / "daemon.log"), env=clean),
            _named("grep", "x", sock, env=clean),
            _named("dtach", "-a", str(inst / "sub" / "claude-dtach-ft1__d1.sock"), env=clean),
            _named("dtach", "-a", str(inst / "daemon.log"), env=clean),
            _named("sleeper", env=dict(clean, AIPAGER_INSTANCE_DIR=str(inst / "sub"))),
        ]
        procs += strangers
        # Ours: the daemon's tree (exact instance variable) and the
        # harness's own dtach attach on a session socket in the folder.
        ours = [_sleeper(dict(clean, AIPAGER_INSTANCE_DIR=str(inst))),
                _named("dtach", "-a", sock, "-E", "-r", "winch", "-z", env=clean)]
        procs += ours
        time.sleep(0.2)
        for p in strangers:
            assert not fti.pid_references(p.pid, inst), fti._read(f"/proc/{p.pid}/cmdline")
            assert fti.kill_pid_if_ours(p.pid, inst) is False
        for p in ours:
            assert fti.pid_references(p.pid, inst)
        assert fti.kill_pid_if_ours(os.getpid(), inst) is False
        assert sorted(fti.pids_referencing(inst)) == sorted(p.pid for p in ours)
        assert sorted(fti.kill_pids_referencing(inst)) == sorted(p.pid for p in ours)
        for p in ours:
            p.wait(10)
        assert [p.pid for p in strangers if p.poll() is not None] == []
    finally:
        # Only the PIDs this test spawned, by PID.
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(10)


def test_opt_in_and_credential_skips(monkeypatch):
    monkeypatch.delenv("AIPAGER_E2E_FAKETG", raising=False)
    assert "opt-in" in ftc.why_skip()
    monkeypatch.setenv("AIPAGER_E2E_FAKETG", "1")
    monkeypatch.setenv("AIPAGER_E2E_FAKETG_CLAUDE", "standin")
    assert ftc.why_skip() is None
    monkeypatch.setenv("AIPAGER_E2E_FAKETG_CLAUDE", "real")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert ftc.why_skip() == ftc.SKIP_NO_CREDENTIAL
    monkeypatch.delenv("AIPAGER_E2E_FAKETG_CLAUDE")
    assert ftc.why_skip() == ftc.SKIP_NO_CREDENTIAL


def test_harness_names_are_recognised():
    names = ["claude-ft1__g4000000001", "claude-x__d900000002", "claude-real__d256113222",
             "claude-ft12", "claude-ft8r", "claude-ftp-sync", "claude-ftx", "claude-dev",
             "claude-1000"]
    assert ftc.harness_names_in(names) == sorted([
        "claude-ft1__g4000000001", "claude-ft12", "claude-ft8r", "claude-x__d900000002"])


def test_real_install_check_catches_a_changed_file_and_a_dead_daemon(tmp_path, monkeypatch):
    """The session guard, on stand-in files (never the operator's)."""
    f = tmp_path / "aipager.yaml"
    f.write_text("a")
    monkeypatch.setattr(ftc, "_real_files", lambda: [f])
    sleeper = _sleeper()
    try:
        monkeypatch.setattr(ftc, "RECORDED", {"files": {str(f): ftc._fingerprint(f)},
                                               "pids": [sleeper.pid]})
        ftc.check_real_install()
        f.write_text("b")
        with pytest.raises(AssertionError, match="real files changed"):
            ftc.check_real_install()
        f.write_text("a")
        os.utime(f, ns=(ftc.RECORDED["files"][str(f)][1],) * 2)
        ftc.check_real_install()
        sleeper.kill()
        sleeper.wait(10)
        with pytest.raises(AssertionError, match="real daemon died"):
            ftc.check_real_install()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(10)


# A made-up OAuth-token shape and a bot-token shape (neither is real).
_OAUTH = "sk-ant-oat01-" + "Zx9_-" * 12
_BOT = "7000000009:" + "AAFakeSecretPartForRedaction_-x"
_ODD = "odd-shaped-secret-value-0042"


def _leaky() -> str:
    return (f"auth {_OAUTH} then https://api.telegram.org/bot{_BOT}/getMe "
            f"and {_BOT} and {_ODD} end")


def _assert_clean(text: str) -> None:
    for secret in (_OAUTH, _OAUTH[len("sk-ant-"):], _BOT, _BOT.split(":")[1], _ODD):
        assert secret not in text, "a credential survived redaction"
    assert "end" in text


def test_redact_removes_oauth_bot_and_literal_env_tokens(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    out = fti.redact(_leaky())
    assert "sk-ant-<redacted>" in out
    for secret in (_OAUTH, _OAUTH[len("sk-ant-"):], _BOT, _BOT.split(":")[1]):
        assert secret not in out
    assert _ODD in out  # not token-shaped and not the configured credential
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _ODD)
    _assert_clean(fti.redact(_leaky()))


def test_every_harness_output_path_is_redacted(root, tmp_path, monkeypatch):
    """log_tail, wait_until's message, the fake's last-calls tails, the
    teardown's KEEP_LOG copy: none carries a credential."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _ODD)
    inst = fti.TestInstance()
    inst.root = root
    inst.inst_dir.mkdir()
    inst.log_path.write_text("start\n" + _leaky() + "\n")
    _assert_clean(inst.log_tail())
    with pytest.raises(AssertionError) as exc:
        fti.wait_until(lambda: False, 0, f"x {_OAUTH}", detail=_leaky)
    _assert_clean(str(exc.value))

    from tests.e2e.fake_telegram.server import FakeBotApi
    api = FakeBotApi(fti.FAKE_TOKEN, fti.BOT_ID, fti.BOT_USERNAME)
    api._record("sendMessage", {"chat_id": fti.GROUP_ID, "text": _leaky()}, {}, 200, {})
    with pytest.raises(AssertionError) as exc:
        api.wait_for(lambda c: False, timeout=0)
    _assert_clean(str(exc.value))
    with pytest.raises(AssertionError) as exc:
        api.wait_button(fti.GROUP_ID, "Allow", timeout=0)
    _assert_clean(str(exc.value))

    keep = tmp_path / "kept"
    monkeypatch.setenv("AIPAGER_E2E_FAKETG_KEEP_LOG", str(keep))
    inst.stop()
    copies = list(keep.iterdir())
    assert len(copies) == 1
    _assert_clean(copies[0].read_text())
    assert not root.exists()


def _jl(*entries) -> list[str]:
    return [json.dumps(e) for e in entries]


def test_transcript_prompts_counts_queued_and_absorbed_messages_once():
    lines = _jl(
        {"type": "user", "message": {"role": "user", "content": "first"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
        # Sent mid-turn and absorbed by the running turn: no user record.
        {"type": "queue-operation", "operation": "enqueue", "content": "absorbed one"},
        {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn",
         "content": "absorbed one"},
        # Sent mid-turn, then run as its own turn: enqueue + a user record.
        {"type": "queue-operation", "operation": "enqueue", "content": "queued two"},
        {"type": "queue-operation", "operation": "dequeue"},
        {"type": "user", "message": {"content": [{"type": "text", "text": "queued two"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        {"type": "user", "message": {"content": "queued two"}},
    ) + ["not json"]
    assert fti.transcript_prompts(lines) == ["first", "absorbed one", "queued two",
                                             "queued two"]


def test_real_mode_prompts_are_per_session_and_survive_a_new_transcript(root):
    inst = fti.TestInstance(claude_mode="real")
    inst.root = root
    proj = inst.home / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    registry = inst.home / ".claude" / "aipager-sessions.json"

    def point(name_to_path):
        registry.write_text(json.dumps({"sessions": {
            n: {"transcript_path": str(p)} for n, p in name_to_path.items()}}))

    a1, a2, b = proj / "a1.jsonl", proj / "a2.jsonl", proj / "b.jsonl"
    a1.write_text("\n".join(_jl({"type": "user", "message": {"content": "alpha"}})) + "\n")
    b.write_text("\n".join(_jl({"type": "user", "message": {"content": "bravo"}})) + "\n")
    assert inst.prompts_seen("claude-ft6__g4000000001") == []
    point({"claude-ft6__g4000000001": a1, "claude-ft7__g4000000001": b})
    assert inst.prompts_seen("claude-ft6__g4000000001") == ["alpha"]
    assert inst.prompts_seen("claude-ft7__g4000000001") == ["bravo"]
    # A restart (Auto switch) gives the session a new transcript.
    a2.write_text("\n".join(_jl({"type": "user", "message": {"content": "again"}})) + "\n")
    point({"claude-ft6__g4000000001": a2, "claude-ft7__g4000000001": b})
    assert inst.prompts_seen("claude-ft6__g4000000001") == ["alpha", "again"]


def test_flow_label_of_a_session_name():
    from tests.e2e.faketg import flows
    assert flows.label_of("claude-ft6__g4000000001") == "ft6"
    assert flows.label_of("claude-ft8r__d900000001") == "ft8r"


def test_teardown_reports_a_process_working_inside_the_root_without_killing_it(
        root, monkeypatch):
    """A process with no instance variable but a working directory inside
    the root is not killed, is named by the teardown, and keeps the
    folder from being removed under it."""
    work = root / "h" / "proj"
    work.mkdir(parents=True)
    (root / "i").mkdir()
    clean = {k: v for k, v in os.environ.items() if k != "AIPAGER_INSTANCE_DIR"}
    p = subprocess.Popen(["sleep", "30"], cwd=work, env=clean)
    outside = subprocess.Popen(["sleep", "30"], cwd=root.parent, env=clean)
    try:
        fti.wait_until(lambda: fti._read(f"/proc/{p.pid}/cmdline").startswith(b"sleep\0"),
                       10, "the sleeper to exec")
        assert not fti.pid_references(p.pid, root / "i")
        found = fti.pids_with_cwd_under(root)
        assert p.pid in found
        assert outside.pid not in found
        # A sibling sharing the name prefix is not inside.
        sibling_dir = root / "hx"
        sibling_dir.mkdir()
        sibling = subprocess.Popen(["sleep", "30"], cwd=sibling_dir, env=clean)
        try:
            fti.wait_until(lambda: os.readlink(f"/proc/{sibling.pid}/cwd") == str(sibling_dir),
                           10, "the sibling sleeper to start")
            assert sibling.pid not in fti.pids_with_cwd_under(root / "h")
            assert p.pid in fti.pids_with_cwd_under(root / "h")
        finally:
            sibling.kill()
            sibling.wait(10)
        with monkeypatch.context() as m:
            m.chdir(work)  # the test process itself is never reported
            assert os.getpid() not in fti.pids_with_cwd_under(root)
        inst = fti.TestInstance()
        inst.root = root
        with pytest.raises(AssertionError) as exc:
            inst.stop()
        assert f"working directory inside the instance (not killed): [{p.pid}]" \
            in str(exc.value)
        with pytest.raises(subprocess.TimeoutExpired):
            p.wait(0.5)  # still running: the report-only check never kills
        assert root.exists()
    finally:
        for q in (p, outside):
            if q.poll() is None:
                q.kill()
                q.wait(10)
