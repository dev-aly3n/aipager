"""Tests for aipager.doctor's runtime-environment additions:
check_claude_auth(), check_service_unit_path(), and `doctor --fix`.
"""

from __future__ import annotations

from aipager import claude_resolve, doctor


def _install(path="/x/claude", version="2.1.235"):
    return claude_resolve.ClaudeInstall(path=path, realpath=path, version=version)


# ----- check_claude_auth: NEVER FAILs -------------------------------------

def test_check_claude_auth_no_binary_is_warn_not_fail(monkeypatch):
    r = doctor.check_claude_auth()
    assert r.status == doctor.WARN
    assert r.status != doctor.FAIL


def test_check_claude_auth_not_logged_in_is_warn(monkeypatch):
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    auth = claude_resolve.AuthStatus(logged_in=False, auth_method="none", source="unknown")
    monkeypatch.setattr(claude_resolve, "detect_auth", lambda *a, **k: auth)

    r = doctor.check_claude_auth()
    assert r.status == doctor.WARN
    assert "not logged in" in " ".join(r.detail)
    assert "claude auth login" in r.fix


def test_check_claude_auth_logged_in_is_ok(monkeypatch):
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    auth = claude_resolve.AuthStatus(logged_in=True, auth_method="oauth_token", source="env")
    monkeypatch.setattr(claude_resolve, "detect_auth", lambda *a, **k: auth)
    # `auth status` saying "logged in" is no longer the last word — a
    # revoked token says the same thing — so the row now depends on a
    # real round-trip as well.
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck("valid"))

    r = doctor.check_claude_auth()
    assert r.status == doctor.OK
    assert "oauth_token" in " ".join(r.detail)


def test_check_claude_auth_warns_when_the_credential_is_rejected(monkeypatch):
    """The blind spot this change closes: present-but-expired used to
    render a green row while every session hung on the login screen."""
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr(
        claude_resolve, "detect_auth",
        lambda *a, **k: claude_resolve.AuthStatus(True, "oauth_token", "env"))
    monkeypatch.setattr(
        claude_resolve, "validate_credential",
        lambda *a, **k: claude_resolve.CredentialCheck("rejected", "401"))

    r = doctor.check_claude_auth()
    assert r.status == doctor.WARN, "an expired credential rendered as OK"
    assert "rejected" in " ".join(r.detail).lower()
    assert r.fix


def test_check_claude_auth_offline_does_not_become_a_warning(monkeypatch):
    """Unknown means unknown. Downgrading a working install to WARN on
    every restart behind a flaky network would train the operator to
    ignore this row."""
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr(
        claude_resolve, "detect_auth",
        lambda *a, **k: claude_resolve.AuthStatus(True, "oauth_token", "env"))
    monkeypatch.setattr(
        claude_resolve, "validate_credential",
        lambda *a, **k: claude_resolve.CredentialCheck("unknown", "probe timed out"))

    r = doctor.check_claude_auth()
    assert r.status == doctor.OK


def test_check_claude_auth_probe_failed_is_warn_not_fail(monkeypatch):
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    auth = claude_resolve.AuthStatus(False, "unknown", "probe-failed", error="timed out")
    monkeypatch.setattr(claude_resolve, "detect_auth", lambda *a, **k: auth)

    r = doctor.check_claude_auth()
    assert r.status == doctor.WARN
    assert "status probe failed" in " ".join(r.detail)


def test_check_claude_auth_uses_build_session_env(monkeypatch):
    """Risk #2: the probe MUST go through build_session_env(), not the
    daemon's own bare os.environ."""
    from aipager import daemon_secrets

    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)

    sentinel = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-from-credential-file"}
    monkeypatch.setattr(daemon_secrets, "build_session_env", lambda **kw: sentinel)

    captured = {}

    def _fake_validate(path, env, **kw):
        captured["validate_env"] = env
        return claude_resolve.CredentialCheck("valid")

    monkeypatch.setattr(claude_resolve, "validate_credential", _fake_validate)

    def _fake_detect_auth(path, version, env, **kw):
        captured["env"] = env
        return claude_resolve.AuthStatus(True, "oauth_token", "env")

    monkeypatch.setattr(claude_resolve, "detect_auth", _fake_detect_auth)
    doctor.check_claude_auth()
    assert captured["env"] is sentinel


# ----- check_service_unit_path --------------------------------------------

def test_check_service_unit_path_not_linux_is_ok(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.check_service_unit_path()
    assert r.status == doctor.OK


def test_check_service_unit_path_not_installed_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setattr("aipager.service.LINUX_UNIT_PATH", tmp_path / "missing.service")
    r = doctor.check_service_unit_path()
    assert r.status == doctor.OK


def test_check_service_unit_path_missing_environment_path_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    unit = tmp_path / "aipager.service"
    unit.write_text("[Service]\nExecStart=/x start\n")
    monkeypatch.setattr("aipager.service.LINUX_UNIT_PATH", unit)
    r = doctor.check_service_unit_path()
    assert r.status == doctor.FAIL
    assert "no Environment=PATH=" in " ".join(r.detail)


def test_check_service_unit_path_fails_when_path_missing_claude_dir(monkeypatch, tmp_path):
    """criterion 16."""
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    unit = tmp_path / "aipager.service"
    unit.write_text("[Service]\nEnvironment=PATH=/usr/bin:/bin\n")
    monkeypatch.setattr("aipager.service.LINUX_UNIT_PATH", unit)

    resolved = claude_resolve.ResolvedClaude(chosen=_install("/home/x/.local/bin/claude"))
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)

    r = doctor.check_service_unit_path()
    assert r.status == doctor.FAIL
    assert "/home/x/.local/bin" in " ".join(r.detail)


def test_check_service_unit_path_ok_when_dir_present(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    unit = tmp_path / "aipager.service"
    unit.write_text(
        "[Service]\nEnvironment=PATH=/home/x/.local/bin:/usr/bin:/bin\n"
    )
    monkeypatch.setattr("aipager.service.LINUX_UNIT_PATH", unit)

    resolved = claude_resolve.ResolvedClaude(chosen=_install("/home/x/.local/bin/claude"))
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)

    r = doctor.check_service_unit_path()
    assert r.status == doctor.OK


def test_check_service_unit_path_warns_when_claude_cannot_resolve(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    unit = tmp_path / "aipager.service"
    unit.write_text("[Service]\nEnvironment=PATH=/usr/bin\n")
    monkeypatch.setattr("aipager.service.LINUX_UNIT_PATH", unit)
    # No resolve_claude_binary mock — autouse fixture makes it raise.
    r = doctor.check_service_unit_path()
    assert r.status == doctor.WARN


# ----- CHECKS wiring: auth never fails the overall run ---------------------

def test_cmd_doctor_not_logged_in_does_not_force_exit_1(monkeypatch, capsys):
    """The overall `aipager doctor` exit code must not go non-zero just
    because auth is absent — the non-negotiable applied end-to-end."""
    monkeypatch.setattr(doctor, "CHECKS", [
        doctor.check_claude_auth,
    ])
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    auth = claude_resolve.AuthStatus(logged_in=False, auth_method="none", source="unknown")
    monkeypatch.setattr(claude_resolve, "detect_auth", lambda *a, **k: auth)

    assert doctor.cmd_doctor() == 0


# ----- doctor --fix: credential discovery ----------------------------------

def test_fix_daemon_credential_skips_when_already_populated(monkeypatch, tmp_path, capsys):
    p = tmp_path / "daemon.env"
    p.write_text("CLAUDE_CODE_OAUTH_TOKEN=already-here\n")
    monkeypatch.setattr("aipager.daemon_secrets.DAEMON_ENV_PATH", p)

    doctor._fix_daemon_credential()
    out = capsys.readouterr().out
    assert "already has content" in out


def test_fix_daemon_credential_declines_leaves_file_absent(monkeypatch, tmp_path, capsys):
    p = tmp_path / "daemon.env"
    monkeypatch.setattr("aipager.daemon_secrets.DAEMON_ENV_PATH", p)
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    doctor._fix_daemon_credential()
    assert not p.exists()


def test_fix_daemon_credential_accepts_calls_ensure_daemon_env(monkeypatch, tmp_path):
    p = tmp_path / "daemon.env"
    monkeypatch.setattr("aipager.daemon_secrets.DAEMON_ENV_PATH", p)
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    called = []
    monkeypatch.setattr("aipager.service.ensure_daemon_env",
                        lambda: called.append(1))

    doctor._fix_daemon_credential()
    assert called == [1]


# ----- doctor --fix: claude_path pinning -----------------------------------

def test_fix_claude_path_single_install_needs_no_prompt(monkeypatch, capsys):
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)

    def _no_input(*a, **k):
        raise AssertionError("input() must not be called with only one install")
    monkeypatch.setattr("builtins.input", _no_input)

    doctor._fix_claude_path()
    assert "nothing to disambiguate" in capsys.readouterr().out


def test_fix_claude_path_no_binary_resolves(monkeypatch, capsys):
    doctor._fix_claude_path()  # autouse fixture -> ClaudeNotFoundError
    assert "no claude binary resolves" in capsys.readouterr().out


def test_fix_claude_path_blank_answer_skips(monkeypatch, tmp_path, capsys):
    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/a/claude", "2.1.235"),
        others=(_install("/b/claude", "2.1.100"),),
    )
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr("builtins.input", lambda *_: "")

    called = []
    monkeypatch.setattr("aipager.scope.dump_claude_path",
                        lambda *a, **k: called.append(1))

    doctor._fix_claude_path()
    assert called == []
    assert "skipped" in capsys.readouterr().out


def test_fix_claude_path_picks_the_chosen_install(monkeypatch, tmp_path):
    """criterion 11's write path via --fix: picking index 0 (the
    resolver's own chosen install) round-trips through dump_claude_path."""
    from aipager import scope as _scope

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text("schema_version: 3\nbot_token: TOK\nscopes: []\n")
    monkeypatch.setattr(_scope, "CONFIG_PATH", cfg)

    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/a/claude", "2.1.235"),
        others=(_install("/b/claude", "2.1.100"),),
    )
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr("builtins.input", lambda *_: "0")

    doctor._fix_claude_path()

    assert _scope.load_claude_path(cfg) == "/a/claude"


def test_fix_claude_path_picks_the_other_install(monkeypatch, tmp_path):
    from aipager import scope as _scope

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text("schema_version: 3\nbot_token: TOK\nscopes: []\n")
    monkeypatch.setattr(_scope, "CONFIG_PATH", cfg)

    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/a/claude", "2.1.235"),
        others=(_install("/b/claude", "2.1.100"),),
    )
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr("builtins.input", lambda *_: "1")

    doctor._fix_claude_path()

    assert _scope.load_claude_path(cfg) == "/b/claude"


def test_fix_claude_path_out_of_range_is_skipped(monkeypatch, tmp_path, capsys):
    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/a/claude"),
        others=(_install("/b/claude"),),
    )
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr("builtins.input", lambda *_: "99")

    called = []
    monkeypatch.setattr("aipager.scope.dump_claude_path",
                        lambda *a, **k: called.append(1))

    doctor._fix_claude_path()
    assert called == []
    assert "out of range" in capsys.readouterr().out


def test_fix_claude_path_non_numeric_is_skipped(monkeypatch, capsys):
    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/a/claude"), others=(_install("/b/claude"),),
    )
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr("builtins.input", lambda *_: "abc")

    doctor._fix_claude_path()
    assert "not a number" in capsys.readouterr().out


# ----- cmd_doctor_fix orchestration -----------------------------------------

def test_cmd_doctor_fix_runs_both_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(doctor, "_fix_daemon_credential", lambda: calls.append("cred"))
    monkeypatch.setattr(doctor, "_fix_claude_path", lambda: calls.append("path"))
    assert doctor.cmd_doctor_fix() == 0
    assert calls == ["cred", "path"]


def test_cmd_doctor_dispatches_to_fix_when_flag_set(monkeypatch):
    import argparse
    called = []
    monkeypatch.setattr(doctor, "cmd_doctor_fix", lambda: called.append(1) or 0)
    rc = doctor.cmd_doctor(argparse.Namespace(fix=True))
    assert rc == 0
    assert called == [1]
