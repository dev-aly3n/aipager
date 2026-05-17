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


def test_linux_unit_renders_with_resolved_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    out = service._render_linux_unit()
    assert "[Unit]" in out
    assert "ExecStart=/fake/bin/aipager start" in out
    assert "ExecStartPre=-/bin/rm -f /tmp/aipager.sock" in out
    assert "EnvironmentFile=-%h/.config/aipager/config.env" in out
    assert "WantedBy=default.target" in out


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


def test_paths_use_home():
    home = Path.home()
    assert service.LINUX_UNIT_PATH.is_relative_to(home)
    assert service.MACOS_PLIST_PATH.is_relative_to(home)
    assert service.MACOS_LOG_PATH.is_relative_to(home)


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

    (tmp_path / "aipager.service").write_text("[Unit]\nold\n")
    rc = service._install_linux()
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
