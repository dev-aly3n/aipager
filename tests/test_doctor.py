"""Tests for aipager.doctor — health checks."""

from __future__ import annotations

import json
import socket

import pytest

from aipager import claude_paths, doctor


# ----- check_config -----

def test_check_config_missing_both(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    r = doctor.check_config()
    assert r.status == doctor.FAIL
    assert "CLAUDE_TG_BOT_TOKEN" in " ".join(r.detail)
    assert "aipager config" in r.fix


def test_check_config_present(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "abc")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1234")
    r = doctor.check_config()
    assert r.status == doctor.OK


# ----- check_token_valid -----

def test_check_token_invalid_returns_fail(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "bad")
    monkeypatch.setattr(doctor, "_http_json",
                        lambda url, timeout=10.0: (None, "HTTP 401: Unauthorized"))
    r = doctor.check_token_valid()
    assert r.status == doctor.FAIL
    assert "401" in " ".join(r.detail)
    assert "aipager config" in r.fix


def test_check_token_valid_returns_ok(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "good")
    monkeypatch.setattr(
        doctor, "_http_json",
        lambda url, timeout=10.0: ({"ok": True, "result": {"username": "mybot"}}, ""),
    )
    r = doctor.check_token_valid()
    assert r.status == doctor.OK
    assert "@mybot" in " ".join(r.detail)


def test_check_token_network_error_is_warn(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "x")
    monkeypatch.setattr(doctor, "_http_json",
                        lambda url, timeout=10.0: (None, "network: timed out"))
    r = doctor.check_token_valid()
    assert r.status == doctor.WARN


# ----- check_chat_reachable -----

def test_check_chat_chat_not_found(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "x")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1")
    monkeypatch.setattr(doctor, "_http_json",
                        lambda url, timeout=10.0: (None, "HTTP 400: chat not found"))
    r = doctor.check_chat_reachable()
    assert r.status == doctor.FAIL
    assert "not reachable" in " ".join(r.detail).lower()


def test_check_chat_reachable_ok(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "x")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1")
    monkeypatch.setattr(doctor, "_http_json",
                        lambda url, timeout=10.0: ({"ok": True, "result": {}}, ""))
    r = doctor.check_chat_reachable()
    assert r.status == doctor.OK


# ----- check_dtach / check_claude / check_hook_scripts -----

def test_check_dtach_missing(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "dtach_bin", None)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor.check_dtach()
    assert r.status == doctor.FAIL
    assert "PATH" in " ".join(r.detail)


def test_check_claude_missing(monkeypatch):
    # The autouse `_no_real_claude_candidates` fixture already makes
    # discovery return zero candidates — no further mocking needed.
    r = doctor.check_claude()
    assert r.status == doctor.FAIL
    assert "no working `claude` binary found" in " ".join(r.detail)


def test_check_hook_scripts_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor.check_hook_scripts()
    assert r.status == doctor.FAIL


def test_check_hook_scripts_present(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    r = doctor.check_hook_scripts()
    assert r.status == doctor.OK


# ----- check_settings_json -----

def test_check_settings_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL


def test_check_settings_invalid_json(monkeypatch, tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text("{ not json")
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert "invalid JSON" in " ".join(r.detail)


def _settings_bytes(tmp_path, monkeypatch, raw: bytes):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_bytes(raw)
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    return p


@pytest.mark.parametrize("raw", [b"[1]", b'"text"', b"null", b"42", b"true"],
                         ids=["list", "string", "null", "number", "bool"])
def test_check_settings_valid_json_that_is_not_an_object_fails(monkeypatch, tmp_path, raw):
    """Valid JSON that is not an object is no settings file: FAIL with the
    restore fix, not an AttributeError the runner shows as a crash."""
    _settings_bytes(tmp_path, monkeypatch, raw)
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert "not a JSON object" in " ".join(r.detail)
    assert "aipager config" in r.fix


@pytest.mark.parametrize("raw, said", [
    (b"\xff\xfe{}", "not UTF-8 text"),
    (b"[" * 100000 + b"]" * 100000, "nested too deep"),
], ids=["not-utf8", "too-deep"])
def test_check_settings_unreadable_content_fails(monkeypatch, tmp_path, raw, said):
    _settings_bytes(tmp_path, monkeypatch, raw)
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert said in " ".join(r.detail)


def test_check_settings_a_folder_in_the_files_place_fails(monkeypatch, tmp_path):
    (tmp_path / ".claude" / "settings.json").mkdir(parents=True)
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert "cannot read" in " ".join(r.detail)


@pytest.mark.parametrize("hooks", [
    {"Stop": ["text"]},
    {"Stop": [{"hooks": "aipager-hook"}]},
    {"Stop": [{"hooks": 5}]},
    {"Stop": [{"hooks": ["aipager-hook"]}]},
    {"Stop": [{"hooks": [{"command": 7}]}]},
    {"Stop": [None, 3, {"hooks": [None, {"command": None}]}]},
], ids=["block-text", "inner-text", "inner-number", "hook-text", "command-number", "nulls"])
def test_check_settings_odd_hook_entries_are_skipped(monkeypatch, tmp_path, hooks):
    """Hook entries of the wrong shape are skipped: no aipager hook is
    found (WARN), and with a real entry beside them it still is (OK)."""
    p = _settings_bytes(tmp_path, monkeypatch,
                        json.dumps({"hooks": hooks, "statusLine": {"x": 1}}).encode())
    r = doctor.check_settings_json()
    assert r.status == doctor.WARN
    assert "no aipager-hook entry found" in r.detail

    real = {"hooks": [{"type": "command", "command": "/x/bin/aipager-hook"}]}
    hooks = {k: [*v, real] for k, v in hooks.items()}
    p.write_text(json.dumps({"hooks": hooks, "statusLine": {"x": 1}}))
    assert doctor.check_settings_json().status == doctor.OK


def test_check_settings_hooks_wrong_type(monkeypatch, tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"hooks": "not-a-dict"}))
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert "hooks key is str" in " ".join(r.detail)


def test_check_settings_complete(monkeypatch, tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "aipager-hook"}]}]
        },
        "statusLine": {"type": "command", "command": "aipager-statusline"},
    }))
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", tmp_path / ".claude")
    r = doctor.check_settings_json()
    assert r.status == doctor.OK


# ----- check_daemon -----

def test_check_daemon_socket_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(tmp_path / "missing.sock"))
    r = doctor.check_daemon()
    assert r.status == doctor.FAIL
    assert "missing" in " ".join(r.detail)


def test_check_daemon_socket_stale_no_listener(monkeypatch, tmp_path):
    """Socket file exists but nothing is bound — sendto raises
    ConnectionRefusedError on Linux."""
    sock_path = tmp_path / "aipager.sock"
    sock_path.touch()
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))

    class FakeSocket:
        def __init__(self, *a, **kw): pass
        def settimeout(self, *_): pass
        def sendto(self, *_): raise ConnectionRefusedError
        def close(self): pass

    monkeypatch.setattr(doctor.socket, "socket", FakeSocket)
    r = doctor.check_daemon()
    assert r.status == doctor.FAIL
    # The fix hint must name the CURRENT socket path (XDG_RUNTIME_DIR
    # under systemd), not a hardcoded /tmp/aipager.sock that would be
    # wrong once the control socket moved.
    assert str(sock_path) in r.fix
    assert "no daemon" in " ".join(r.detail).lower()


def test_check_daemon_listening(monkeypatch, tmp_path):
    """Real AF_UNIX SOCK_DGRAM round-trip — bind a server, doctor pings it."""
    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
        r = doctor.check_daemon()
        assert r.status == doctor.OK
    finally:
        server.close()


# ----- run_all & cmd_doctor smoke -----

def test_run_all_returns_one_per_check(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [
        lambda: doctor.CheckResult(doctor.OK, "a"),
        lambda: doctor.CheckResult(doctor.FAIL, "b", fix="do x"),
    ])
    results = doctor.run_all()
    assert len(results) == 2


def test_cmd_doctor_exits_1_on_fail(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "CHECKS", [
        lambda: doctor.CheckResult(doctor.FAIL, "broken", fix="fix it")
    ])
    rc = doctor.cmd_doctor()
    assert rc == 1
    out = capsys.readouterr().out
    assert "✗" in out
    assert "fix it" in out


def test_cmd_doctor_exits_0_on_warn_only(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "CHECKS", [
        lambda: doctor.CheckResult(doctor.WARN, "softfail")
    ])
    assert doctor.cmd_doctor() == 0


def test_cmd_doctor_exits_0_on_all_ok(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "CHECKS", [
        lambda: doctor.CheckResult(doctor.OK, "alpha"),
        lambda: doctor.CheckResult(doctor.OK, "beta"),
    ])
    assert doctor.cmd_doctor() == 0


# ----- check_team -----

def test_check_team_personal_mode_ok(monkeypatch, tmp_path):
    """No team.yaml → personal mode → OK."""
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH",
                        tmp_path / "missing.yaml")
    r = doctor.check_team()
    assert r.status == doctor.OK
    assert "personal" in " ".join(r.detail).lower()


def test_check_team_malformed_yaml_fails(monkeypatch, tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text("mode: team\nusers: [\n")  # unclosed
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    r = doctor.check_team()
    assert r.status == doctor.FAIL
    assert "malformed" in " ".join(r.detail).lower()


def test_check_team_chat_id_mismatch_fails(monkeypatch, tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text(
        "mode: team\n"
        "group_id: -100123\n"
        "users:\n"
        "  - {id: 1, label: a, role: admin}\n"
    )
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    monkeypatch.setattr("aipager.config.CHAT_ID", "-999")  # mismatch
    r = doctor.check_team()
    assert r.status == doctor.FAIL
    assert "CHAT_ID" in " ".join(r.detail)


def test_check_team_no_admin_warns(monkeypatch, tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text(
        "mode: team\n"
        "group_id: -100123\n"
        "users:\n"
        "  - {id: 1, label: a, role: developer}\n"
    )
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    r = doctor.check_team()
    assert r.status == doctor.WARN
    assert "admin" in " ".join(r.detail).lower()


def test_check_team_empty_deny_tools_warns(monkeypatch, tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text(
        "mode: team\n"
        "group_id: -100123\n"
        "users:\n"
        "  - {id: 1, label: a, role: admin}\n"
    )
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    r = doctor.check_team()
    assert r.status == doctor.WARN
    assert "deny_tools" in " ".join(r.detail)


def test_check_team_healthy_returns_ok(monkeypatch, tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text(
        "mode: team\n"
        "group_id: -100123\n"
        "users:\n"
        "  - {id: 1, label: a, role: admin}\n"
        "rules:\n"
        "  deny_tools: [Write]\n"
    )
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    r = doctor.check_team()
    assert r.status == doctor.OK


# ----- check_settings_json follows CLAUDE_CONFIG_DIR -----

def _ours(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "aipager-hook"}]}]},
        "statusLine": {"type": "command", "command": "aipager-statusline"},
    }))


@pytest.fixture
def no_config_dir(monkeypatch, tmp_path):
    from aipager import daemon_secrets
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    return daemon_secrets.DAEMON_ENV_PATH


def _daemon_env(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_settings_json_checks_the_config_dirs_file(no_config_dir, tmp_path):
    cfg = tmp_path / "claude-config"
    _daemon_env(no_config_dir, f"CLAUDE_CONFIG_DIR={cfg}\n")
    _ours(claude_paths.DEFAULT_CONFIG_HOME / "settings.json")
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL          # the sessions' file is missing

    (cfg).mkdir()
    (cfg / "settings.json").write_text(json.dumps({"hooks": {}}))
    assert doctor.check_settings_json().status == doctor.WARN

    _ours(cfg / "settings.json")
    r = doctor.check_settings_json()
    assert r.status == doctor.OK
    assert any(str(cfg / "settings.json") in d and "daemon.env" in d for d in r.detail)


def test_settings_json_says_when_the_value_comes_from_this_shell(
        no_config_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "claude-config"
    _ours(cfg / "settings.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    r = doctor.check_settings_json()
    assert r.status == doctor.OK            # no service installed
    assert any("this shell" in d for d in r.detail)


def test_settings_json_says_when_the_value_comes_from_claude_codes_settings(
        no_config_dir, tmp_path, monkeypatch):
    """A settings env block that names the start home itself moves nothing,
    but it is where the value came from: not this shell."""
    home = claude_paths.DEFAULT_CONFIG_HOME
    _ours(home / "settings.json")
    data = json.loads((home / "settings.json").read_text())
    data["env"] = {"CLAUDE_CONFIG_DIR": str(home)}
    (home / "settings.json").write_text(json.dumps(data))
    r = doctor.check_settings_json()
    assert r.status == doctor.OK
    detail = " ".join(r.detail)
    assert "CLAUDE_CONFIG_DIR from Claude Code's settings" in detail
    assert "this shell" not in detail


def test_settings_json_warns_for_shell_only_value_with_a_service(
        no_config_dir, tmp_path, monkeypatch):
    from aipager import service
    cfg = tmp_path / "claude-config"
    _ours(cfg / "settings.json")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    service.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.LINUX_UNIT_PATH.write_text("[Unit]\n")
    r = doctor.check_settings_json()
    assert r.status == doctor.WARN
    assert "daemon.env" in r.fix


def test_settings_json_warns_for_relative_config_dir(no_config_dir, tmp_path):
    _daemon_env(no_config_dir, "CLAUDE_CONFIG_DIR=relative/config\n")
    r = doctor.check_settings_json()
    assert r.status == doctor.WARN
    assert any("not an absolute path" in d for d in r.detail)


def test_settings_json_shows_a_settings_layer_move_as_a_detail(no_config_dir, tmp_path):
    """Measured with 2.1.303: Claude Code follows a CLAUDE_CONFIG_DIR in its
    settings env; aipager does too, so it is a detail, not a warning."""
    moved = tmp_path / "moved"
    settings = claude_paths.DEFAULT_CONFIG_HOME / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"env": {"CLAUDE_CONFIG_DIR": str(moved)}}))
    _ours(moved / "settings.json")
    r = doctor.check_settings_json()
    assert r.status == doctor.OK
    assert any(str(moved) in d and "aipager follows it" in d for d in r.detail)


def test_settings_json_with_nothing_set_is_todays_row(no_config_dir):
    _ours(claude_paths.DEFAULT_CONFIG_HOME / "settings.json")
    r = doctor.check_settings_json()
    assert (r.status, r.title, r.detail, r.fix) == (
        doctor.OK, "Claude Code settings.json", [], None)


def test_claude_env_sees_a_managed_base_url(no_config_dir):
    from aipager import claude_bootstrap, claude_resolve
    managed = claude_paths.MANAGED_SETTINGS_DIR / "managed-settings.json"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}}))
    env = claude_bootstrap.claude_env({})
    assert claude_resolve.uses_custom_endpoint(env)
