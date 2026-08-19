"""Tests for aipager.service — template rendering, dispatch, and the
new error-handling paths (stderr capture, missing-binary, unit backup,
service-not-installed precheck).

Does NOT run systemctl or launchctl. The actual integration with the OS
service manager must be tested manually on real Linux/macOS machines.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from aipager import service
# Bound at import time, before the autouse `_no_real_login_shell_probe`
# fixture (tests/conftest.py) patches `service._discover_token_via_
# login_shell` to a refusing stub for every OTHER test in the suite.
# The tests below are the one place that deliberately exercises the
# real function (with `subprocess.run` itself mocked) — calling this
# name bypasses the patched module attribute entirely.
from aipager.service import (
    _discover_token_via_login_shell as _real_discover_token_via_login_shell,
)


def test_linux_unit_renders_with_resolved_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    out = service._render_linux_unit()
    assert "[Unit]" in out
    assert "ExecStart=/fake/bin/aipager start" in out
    assert "WantedBy=default.target" in out


# ----- criterion 12: the rendered unit's exact required/forbidden shape --

def test_rendered_unit_matches_the_contract(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    out = service._render_linux_unit()

    assert "LoadCredential=claude_oauth:%h/.config/aipager/daemon.env" in out
    assert "Environment=PATH=" in out
    # StartLimitIntervalSec must be inside [Unit], not [Service] (moved
    # in systemd v229 — under [Service] it's silently ignored).
    unit_section, _, rest = out.partition("[Service]")
    assert "StartLimitIntervalSec=0" in unit_section
    assert "StartLimitIntervalSec=0" not in rest

    assert "After=network-online.target" not in out
    assert "ExecStartPre" not in out
    assert "EnvironmentFile" not in out
    assert "Restart=always" in out
    assert "Restart=on-failure" not in out


def test_resolved_path_value_puts_local_bin_first_and_dedups(monkeypatch):
    monkeypatch.setattr(service.Path, "home", lambda: Path("/home/x"))
    monkeypatch.setenv("PATH", "/usr/bin:/home/x/.local/bin:/bin")
    value = service._resolved_path_value()
    parts = value.split(service.os.pathsep)
    assert parts[0] == "/home/x/.local/bin"
    assert parts.count("/home/x/.local/bin") == 1
    assert "/usr/bin" in parts
    assert "/bin" in parts


def test_resolved_path_value_prepends_local_bin_when_absent(monkeypatch):
    monkeypatch.setattr(service.Path, "home", lambda: Path("/home/x"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    value = service._resolved_path_value()
    assert value == "/home/x/.local/bin:/usr/bin:/bin"


def test_macos_plist_renders_with_resolved_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    out = service._render_macos_plist()
    assert "<?xml" in out
    assert "<key>Label</key>" in out
    assert f"<string>{service.MACOS_LABEL}</string>" in out
    assert "<string>/fake/bin/aipager</string>" in out
    assert "<string>start</string>" in out
    assert "<key>RunAtLoad</key>" in out
    assert "<true/>" in out


def test_resolve_bin_raises_when_not_on_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        service._resolve_aipager_bin()


def test_platform_detection(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    assert service._platform() == "linux"
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    assert service._platform() == "macos"
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    assert service._platform() == "windows"


def test_dispatch_table_covers_both_platforms():
    for plat in ("linux", "macos"):
        for sub in ("install", "start", "stop", "status", "logs", "uninstall"):
            assert sub in service._DISPATCH[plat], f"missing {plat}/{sub}"


def test_paths_use_home(real_home_paths):
    # Asserts the production constants, not the tmp redirects the
    # autouse isolation fixture installs.
    home = Path.home()
    for name in ("LINUX_UNIT_PATH", "MACOS_PLIST_PATH", "MACOS_LOG_PATH"):
        assert real_home_paths[f"aipager.service.{name}"].is_relative_to(home)


# ----- _run -----

def test_run_handles_missing_binary(monkeypatch):
    monkeypatch.setattr(service.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    rc, out, err = service._run(["nonexistent-thing"])
    assert rc == 127
    assert "not found" in err


def test_run_captures_stderr(monkeypatch):
    class _R:
        returncode = 1
        stdout = "out"
        stderr = "err"

    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: _R())
    rc, out, err = service._run(["x"])
    assert rc == 1
    assert out == "out"
    assert err == "err"


# ----- _systemd_user_available -----

def test_systemd_user_unavailable_when_systemctl_missing(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    ok, reason = service._systemd_user_available()
    assert ok is False
    assert "systemctl" in reason


def test_systemd_user_unavailable_when_state_is_offline(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (1, "offline\n", ""))
    ok, reason = service._systemd_user_available()
    assert ok is False
    assert reason == "offline"


def test_systemd_user_available_when_running(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "running\n", ""))
    ok, reason = service._systemd_user_available()
    assert ok is True


# ----- _install_linux abort paths -----

def test_install_linux_aborts_when_systemd_missing(monkeypatch, capsys):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (False, "systemctl not on PATH"))
    rc = service._install_linux()
    assert rc == 2
    err = capsys.readouterr().err
    assert "systemd-user" in err
    assert "tmux" in err


def test_install_linux_relays_systemctl_stderr(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "aipager.service")
    monkeypatch.setattr(service, "_render_linux_unit", lambda: "[Unit]\n")
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)

    calls: list[list[str]] = []

    def _fake_run(cmd, **_kw):
        calls.append(cmd)
        if "daemon-reload" in cmd:
            return 0, "", ""
        if "enable" in cmd:
            return 5, "", "Unit aipager.service not loaded\n"
        return 0, "", ""

    monkeypatch.setattr(service, "_run", _fake_run)
    rc = service._install_linux()
    assert rc == 5
    err = capsys.readouterr().err
    assert "exit 5" in err
    assert "Unit aipager.service not loaded" in err


def test_install_linux_backs_up_existing(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "aipager.service")
    monkeypatch.setattr(service, "_render_linux_unit", lambda: "[Unit]\nnew\n")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)

    (tmp_path / "aipager.service").write_text("[Unit]\nold\n")
    # --yes: this test is about the backup mechanism, not the prompt —
    # see the diff-and-ask section below for prompt-specific coverage.
    rc = service._install_linux(yes=True)
    assert rc == 0
    backups = [p for p in tmp_path.iterdir() if ".bak." in p.name]
    assert len(backups) == 1
    assert backups[0].read_text() == "[Unit]\nold\n"


# ----- _check_linger -----

def test_check_linger_warns_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/loginctl")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "Linger=no\n", ""))
    service._check_linger()
    err = capsys.readouterr().err
    assert "loginctl enable-linger alice" in err


def test_check_linger_silent_when_enabled(monkeypatch, capsys):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/loginctl")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "Linger=yes\n", ""))
    service._check_linger()
    assert capsys.readouterr().err == ""


# ----- require_installed prechecks -----

def test_require_installed_linux_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "missing.service")
    assert service._require_installed_linux() is False
    err = capsys.readouterr().err
    assert "isn't installed" in err
    assert "aipager service install" in err


def test_require_installed_linux_present(monkeypatch, tmp_path):
    p = tmp_path / "x.service"
    p.touch()
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", p)
    assert service._require_installed_linux() is True


def test_start_linux_aborts_if_not_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "missing.service")
    rc = service._start_linux()
    assert rc == 2


# ----- cmd_service unknown subcommand -----

def test_cmd_service_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "freebsd")
    args = argparse.Namespace(service_cmd="install")
    rc = service.cmd_service(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "Unsupported platform" in err


def test_cmd_service_unknown_subcommand(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    args = argparse.Namespace(service_cmd="bogus")
    rc = service.cmd_service(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "Unknown service subcommand" in err


# ----- cmd_logs -----

def test_cmd_logs_linux_no_unit_returns_friendly_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "missing.service")
    rc = service.cmd_logs()
    assert rc == 2
    err = capsys.readouterr().err
    assert "No daemon log source" in err
    assert "aipager service install" in err


def test_cmd_logs_linux_passes_follow_and_lines(monkeypatch, tmp_path):
    unit = tmp_path / "aipager.service"
    unit.touch()
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit)
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kw):
        calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(service, "_run", _fake_run)

    # default — follow=True, lines=10
    service.cmd_logs()
    assert calls[0] == [
        "journalctl", "--user", "-u", "aipager.service", "-n", "10", "--follow",
    ]

    # explicit non-follow with custom line count
    service.cmd_logs(follow=False, lines=50)
    assert calls[1] == [
        "journalctl", "--user", "-u", "aipager.service", "-n", "50",
    ]


def test_cmd_logs_macos_no_plist_returns_friendly_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "macos")
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", tmp_path / "missing.plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "missing.log")
    rc = service.cmd_logs()
    assert rc == 2
    assert "No daemon log source" in capsys.readouterr().err


def test_cmd_logs_macos_passes_follow_and_lines(monkeypatch, tmp_path):
    plist = tmp_path / "com.aipager.daemon.plist"
    plist.touch()
    log = tmp_path / "aipager.log"
    monkeypatch.setattr(service, "_platform", lambda: "macos")
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", plist)
    monkeypatch.setattr(service, "MACOS_LOG_PATH", log)
    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_run", lambda cmd, **_kw: calls.append(cmd) or (0, "", ""))

    service.cmd_logs(follow=False, lines=25)
    assert calls[0] == ["tail", "-n", "25", str(log)]

    service.cmd_logs(follow=True, lines=100)
    assert calls[1] == ["tail", "-n", "100", "-f", str(log)]


def test_cmd_logs_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "windows")
    rc = service.cmd_logs()
    assert rc == 1
    assert "not supported on windows" in capsys.readouterr().err


# ===== daemon.env: ensure_daemon_env() ====================================

def test_ensure_daemon_env_noop_if_already_exists(monkeypatch, tmp_path):
    p = tmp_path / "daemon.env"
    p.write_text("CLAUDE_CODE_OAUTH_TOKEN=already-here\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", p)
    result = service.ensure_daemon_env()
    assert result == p
    assert p.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=already-here\n"


def test_ensure_daemon_env_copies_forward_from_legacy_config_env(monkeypatch, tmp_path):
    """criterion 14: a legacy config.env holding a token, with no
    daemon.env yet, produces daemon.env at 0600 with the value copied
    forward, before rendering."""
    daemon_env = tmp_path / "daemon.env"
    config_env = tmp_path / "config.env"
    config_env.write_text(
        "CLAUDE_TG_BOT_TOKEN=123:abc\n"
        "CLAUDE_CODE_OAUTH_TOKEN=sk-legacy-token\n"
    )
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", config_env)

    result = service.ensure_daemon_env()

    assert result == daemon_env
    assert daemon_env.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=sk-legacy-token\n"
    assert (daemon_env.stat().st_mode & 0o777) == 0o600


def test_ensure_daemon_env_prefers_config_env_over_retired_copies(monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    config_env = tmp_path / "config.env"
    retired = tmp_path / "config.env.retired.100"
    config_env.write_text("CLAUDE_CODE_OAUTH_TOKEN=live-token\n")
    retired.write_text("CLAUDE_CODE_OAUTH_TOKEN=stale-token\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", config_env)

    service.ensure_daemon_env()
    assert "live-token" in daemon_env.read_text()


def test_ensure_daemon_env_falls_back_to_newest_retired_copy(monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    config_env = tmp_path / "config.env"  # absent
    older = tmp_path / "config.env.retired.100"
    newer = tmp_path / "config.env.retired.200"
    older.write_text("CLAUDE_CODE_OAUTH_TOKEN=older-token\n")
    newer.write_text("CLAUDE_CODE_OAUTH_TOKEN=newer-token\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", config_env)

    service.ensure_daemon_env()
    assert "newer-token" in daemon_env.read_text()


def test_ensure_daemon_env_recognizes_anthropic_api_key(monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    config_env = tmp_path / "config.env"
    config_env.write_text("ANTHROPIC_API_KEY=sk-ant-abc\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", config_env)

    service.ensure_daemon_env()
    assert daemon_env.read_text() == "ANTHROPIC_API_KEY=sk-ant-abc\n"


def test_ensure_daemon_env_falls_back_to_login_shell_probe(monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", tmp_path / "no-config.env")
    monkeypatch.setattr(service, "_discover_token_via_login_shell",
                        lambda: "sk-from-shell")

    service.ensure_daemon_env()
    assert daemon_env.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=sk-from-shell\n"


def test_ensure_daemon_env_writes_empty_file_as_last_resort(monkeypatch, tmp_path, capsys):
    daemon_env = tmp_path / "daemon.env"
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", tmp_path / "no-config.env")
    monkeypatch.setattr(service, "_discover_token_via_login_shell", lambda: None)

    result = service.ensure_daemon_env()

    assert result == daemon_env
    assert daemon_env.exists()
    assert daemon_env.read_text() == ""
    assert (daemon_env.stat().st_mode & 0o777) == 0o600
    assert "No Claude credential found" in capsys.readouterr().err


# ===== _discover_token_via_login_shell — behaviour only, mocked exec ====

def test_discover_token_via_login_shell_strips_and_returns(monkeypatch):
    class _R:
        stdout = "sk-value\n"
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: _R())
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert _real_discover_token_via_login_shell() == "sk-value"


def test_discover_token_via_login_shell_empty_output_is_none(monkeypatch):
    class _R:
        stdout = "\n"
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: _R())
    assert _real_discover_token_via_login_shell() is None


def test_discover_token_via_login_shell_timeout_is_none(monkeypatch):
    def _boom(*a, **k):
        raise service.subprocess.TimeoutExpired(cmd="sh", timeout=10)
    monkeypatch.setattr(service.subprocess, "run", _boom)
    assert _real_discover_token_via_login_shell() is None


def test_discover_token_via_login_shell_oserror_is_none(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no such shell")
    monkeypatch.setattr(service.subprocess, "run", _boom)
    assert _real_discover_token_via_login_shell() is None


# ===== _extract_token_line ==================================================

def test_extract_token_line_finds_first_match(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        "# comment\n\nCLAUDE_TG_BOT_TOKEN=123:abc\n"
        "CLAUDE_CODE_OAUTH_TOKEN=sk-tok\nANTHROPIC_API_KEY=sk-ant\n"
    )
    assert service._extract_token_line(p) == "CLAUDE_CODE_OAUTH_TOKEN=sk-tok"


def test_extract_token_line_none_when_no_token_present(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("CLAUDE_TG_BOT_TOKEN=123:abc\n")
    assert service._extract_token_line(p) is None


def test_extract_token_line_missing_file_returns_none(tmp_path):
    assert service._extract_token_line(tmp_path / "nope.env") is None


# ===== _migrate_token_from_old_unit =========================================

def test_migrate_token_from_old_unit_copies_environment_file_token(
        monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    old_env_file = tmp_path / "config.env"
    old_env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-from-old-unit\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)

    unit_text = (
        "[Service]\n"
        f"EnvironmentFile=-{old_env_file}\n"
    )
    service._migrate_token_from_old_unit(unit_text)

    assert daemon_env.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=sk-from-old-unit\n"
    old_env_file_after = old_env_file.read_text()
    assert old_env_file_after == "CLAUDE_CODE_OAUTH_TOKEN=sk-from-old-unit\n", (
        "the old file must never be deleted or altered"
    )


def test_migrate_token_from_old_unit_expands_percent_h(monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    home = tmp_path / "home"
    (home / ".config" / "aipager").mkdir(parents=True)
    old_env_file = home / ".config" / "aipager" / "config.env"
    old_env_file.write_text("ANTHROPIC_API_KEY=sk-ant-old\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr(service.Path, "home", lambda: home)

    unit_text = "EnvironmentFile=-%h/.config/aipager/config.env\n"
    service._migrate_token_from_old_unit(unit_text)

    assert daemon_env.read_text() == "ANTHROPIC_API_KEY=sk-ant-old\n"


def test_migrate_token_from_old_unit_noop_when_daemon_env_already_exists(
        monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    daemon_env.write_text("CLAUDE_CODE_OAUTH_TOKEN=keep-me\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)

    old_env_file = tmp_path / "config.env"
    old_env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=should-not-overwrite\n")
    service._migrate_token_from_old_unit(f"EnvironmentFile=-{old_env_file}\n")

    assert daemon_env.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=keep-me\n"


def test_migrate_token_from_old_unit_noop_when_no_environment_file_line(
        monkeypatch, tmp_path):
    daemon_env = tmp_path / "daemon.env"
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    service._migrate_token_from_old_unit("[Service]\nExecStart=/x start\n")
    assert not daemon_env.exists()


# ===== diff-and-ask: _install_linux() on a differing existing unit ========

def _prep_install_linux(monkeypatch, tmp_path, *, existing: str, rendered: str):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    unit_path = tmp_path / "aipager.service"
    unit_path.write_text(existing)
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_render_linux_unit", lambda: rendered)
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)
    monkeypatch.setattr(service, "_migrate_token_from_old_unit", lambda text: None)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    return unit_path


def test_install_linux_no_existing_unit_writes_without_prompting(
        monkeypatch, tmp_path):
    """criterion 13 (write branch): no existing unit → write directly,
    no prompt at all."""
    unit_path = tmp_path / "aipager.service"  # does not exist yet
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_render_linux_unit", lambda: "[Unit]\nnew\n")
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)

    def _no_input(*a, **k):
        raise AssertionError("input() must not be called with no existing unit")
    monkeypatch.setattr("builtins.input", _no_input)

    rc = service._install_linux()
    assert rc == 0
    assert unit_path.read_text() == "[Unit]\nnew\n"


def test_install_linux_byte_identical_is_a_noop(monkeypatch, tmp_path, capsys):
    unit_path = _prep_install_linux(
        monkeypatch, tmp_path, existing="[Unit]\nsame\n", rendered="[Unit]\nsame\n",
    )

    def _no_input(*a, **k):
        raise AssertionError("input() must not be called when nothing changed")
    monkeypatch.setattr("builtins.input", _no_input)

    rc = service._install_linux()
    assert rc == 0
    assert unit_path.read_text() == "[Unit]\nsame\n"
    assert "already up to date" in capsys.readouterr().out


def test_install_linux_declines_prompt_leaves_unit_unchanged(monkeypatch, tmp_path):
    """criterion 13 (decline branch): differing unit, no --yes, answer
    'n' → abort without writing."""
    unit_path = _prep_install_linux(
        monkeypatch, tmp_path,
        existing="[Unit]\nold\n", rendered="[Unit]\nnew\n",
    )
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    rc = service._install_linux(yes=False)

    assert rc == 0
    assert unit_path.read_text() == "[Unit]\nold\n"
    backups = [p for p in tmp_path.iterdir() if ".bak." in p.name]
    assert backups == []


def test_install_linux_accepts_prompt_backs_up_and_overwrites(monkeypatch, tmp_path):
    unit_path = _prep_install_linux(
        monkeypatch, tmp_path,
        existing="[Unit]\nold\n", rendered="[Unit]\nnew\n",
    )
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    rc = service._install_linux(yes=False)

    assert rc == 0
    assert unit_path.read_text() == "[Unit]\nnew\n"
    backups = [p for p in tmp_path.iterdir() if ".bak." in p.name]
    assert len(backups) == 1
    assert backups[0].read_text() == "[Unit]\nold\n"


def test_install_linux_yes_flag_skips_prompt_and_overwrites(monkeypatch, tmp_path):
    """criterion 13 (--yes branch): differing unit, --yes → backs up and
    overwrites without ever calling input()."""
    unit_path = _prep_install_linux(
        monkeypatch, tmp_path,
        existing="[Unit]\nold\n", rendered="[Unit]\nnew\n",
    )

    def _no_input(*a, **k):
        raise AssertionError("input() must not be called with --yes")
    monkeypatch.setattr("builtins.input", _no_input)

    rc = service._install_linux(yes=True)

    assert rc == 0
    assert unit_path.read_text() == "[Unit]\nnew\n"
    backups = [p for p in tmp_path.iterdir() if ".bak." in p.name]
    assert len(backups) == 1


def test_install_linux_migrates_token_from_old_unit_before_overwriting(
        monkeypatch, tmp_path):
    old_config_env = tmp_path / "config.env"
    old_config_env.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-from-old\n")
    unit_path = tmp_path / "aipager.service"
    unit_path.write_text(f"EnvironmentFile=-{old_config_env}\n")
    daemon_env = tmp_path / "daemon.env"

    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_render_linux_unit", lambda: "[Unit]\nnew\n")
    monkeypatch.setattr(service, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.setattr("aipager.config._XDG_CONFIG", tmp_path / "no-config.env")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)

    rc = service._install_linux(yes=True)

    assert rc == 0
    assert daemon_env.read_text() == "CLAUDE_CODE_OAUTH_TOKEN=sk-from-old\n"


# ===== cmd_service routes --yes through to install =========================

def test_cmd_service_install_passes_yes_flag(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr("aipager.preflight.require_config", lambda: None)
    captured = {}

    def _fake_install(*, yes=False):
        captured["yes"] = yes
        return 0

    monkeypatch.setitem(service._DISPATCH["linux"], "install", _fake_install)
    args = argparse.Namespace(service_cmd="install", yes=True)
    rc = service.cmd_service(args)
    assert rc == 0
    assert captured["yes"] is True


def test_cmd_service_install_defaults_yes_to_false(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr("aipager.preflight.require_config", lambda: None)
    captured = {}

    def _fake_install(*, yes=False):
        captured["yes"] = yes
        return 0

    monkeypatch.setitem(service._DISPATCH["linux"], "install", _fake_install)
    args = argparse.Namespace(service_cmd="install")  # no --yes attr at all
    rc = service.cmd_service(args)
    assert rc == 0
    assert captured["yes"] is False
