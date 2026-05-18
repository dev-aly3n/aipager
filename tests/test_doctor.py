"""Tests for aipager.doctor — health checks."""

from __future__ import annotations

import json
import socket

from aipager import doctor


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
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor.check_claude()
    assert r.status == doctor.FAIL
    assert "PATH" in " ".join(r.detail)


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
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL


def test_check_settings_invalid_json(monkeypatch, tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text("{ not json")
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    r = doctor.check_settings_json()
    assert r.status == doctor.FAIL
    assert "invalid JSON" in " ".join(r.detail)


def test_check_settings_hooks_wrong_type(monkeypatch, tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"hooks": "not-a-dict"}))
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
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
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
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
