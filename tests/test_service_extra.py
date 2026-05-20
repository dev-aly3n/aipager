"""Additional service.py tests — install/start/stop/status/logs/uninstall
for both Linux (systemd-user) and macOS (launchd) dispatch."""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import MagicMock

import pytest

from aipager import service


# ---- _platform ---------------------------------------------------------

def test_platform_linux(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    assert service._platform() == "linux"


def test_platform_macos(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    assert service._platform() == "macos"


def test_platform_other(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "FreeBSD")
    assert service._platform() == "freebsd"


# ---- _resolve_aipager_bin ----------------------------------------------

def test_resolve_aipager_bin_found(monkeypatch):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/aipager")
    assert service._resolve_aipager_bin() == "/usr/bin/aipager"


def test_resolve_aipager_bin_missing_raises(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda n: None)
    with pytest.raises(FileNotFoundError):
        service._resolve_aipager_bin()


# ---- _render_linux_unit / _render_macos_plist --------------------------

def test_render_linux_unit_includes_bin(monkeypatch):
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/local/bin/aipager")
    unit = service._render_linux_unit()
    assert "/usr/local/bin/aipager" in unit


def test_render_macos_plist_includes_label_and_bin(monkeypatch):
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/local/bin/aipager")
    plist = service._render_macos_plist()
    assert "/usr/local/bin/aipager" in plist
    assert service.MACOS_LABEL in plist


# ---- _run ---------------------------------------------------------------

def test_run_capture_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stdout="hello",
                                                   stderr=""))
    rc, out, err = service._run(["x"])
    assert rc == 0
    assert out == "hello"


def test_run_capture_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=2,
                                                   stdout="",
                                                   stderr="bad"))
    rc, out, err = service._run(["x"])
    assert rc == 2
    assert err == "bad"


def test_run_no_capture(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0))
    rc, out, err = service._run(["x"], capture=False)
    assert rc == 0
    assert out == ""


def test_run_file_not_found(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("missing")
    monkeypatch.setattr(subprocess, "run", _boom)
    rc, out, err = service._run(["nope"])
    assert rc == 127
    assert "not found" in err


# ---- _systemd_user_available --------------------------------------------

def test_systemd_user_available_no_systemctl(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda n: None)
    avail, _ = service._systemd_user_available()
    assert avail is False


def test_systemd_user_available_offline(monkeypatch):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/systemctl")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (0, "offline\n", ""))
    avail, _ = service._systemd_user_available()
    assert avail is False


def test_systemd_user_available_running(monkeypatch):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/systemctl")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (0, "running\n", ""))
    avail, _ = service._systemd_user_available()
    assert avail is True


def test_systemd_user_available_127_unreachable(monkeypatch):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/systemctl")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (127, "", "not found"))
    avail, _ = service._systemd_user_available()
    assert avail is False


# ---- _backup_existing ---------------------------------------------------

def test_backup_existing_creates_timestamped(tmp_path):
    target = tmp_path / "aipager.service"
    target.write_text("[Unit]\n")
    service._backup_existing(target)
    backups = list(tmp_path.glob("aipager.service.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "[Unit]\n"


def test_backup_existing_missing_is_noop(tmp_path):
    service._backup_existing(tmp_path / "no")  # no raise


def test_backup_existing_oserror_warns(tmp_path, monkeypatch, capsys):
    target = tmp_path / "x"
    target.write_text("hi")
    # write_text on the backup raises
    real_write = target.__class__.write_text
    def _maybe_boom(self, content):
        if "bak" in str(self):
            raise OSError("EROFS")
        return real_write(self, content)
    monkeypatch.setattr(target.__class__, "write_text", _maybe_boom)
    service._backup_existing(target)  # must not raise


# ---- _install_linux paths ----------------------------------------------

def test_install_linux_no_systemd_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (False, "not available"))
    rc = service._install_linux()
    assert rc == 2
    assert "systemd-user is not available" in capsys.readouterr().err


def test_install_linux_success(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH",
                        tmp_path / "aipager.service")
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/bin/aipager")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    rc = service._install_linux()
    assert rc == 0


def test_install_linux_enable_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH",
                        tmp_path / "aipager.service")
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/bin/aipager")
    calls = []
    def _run(cmd, **k):
        calls.append(cmd)
        if "enable" in cmd:
            return (1, "", "permission denied")
        return (0, "", "")
    monkeypatch.setattr(service, "_run", _run)
    rc = service._install_linux()
    assert rc == 1


def test_install_linux_daemon_reload_failure_warns_then_continues(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "LINUX_UNIT_PATH",
                        tmp_path / "aipager.service")
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/bin/aipager")
    def _run(cmd, **k):
        if "daemon-reload" in cmd:
            return (1, "", "reload failed")
        return (0, "", "")
    monkeypatch.setattr(service, "_run", _run)
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    rc = service._install_linux()
    # Even if daemon-reload fails, install proceeds
    assert rc == 0


# ---- _check_linger ------------------------------------------------------

def test_check_linger_no_loginctl(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda n: None)
    service._check_linger()  # no raise


def test_check_linger_no_user_env(monkeypatch):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/loginctl")
    monkeypatch.setenv("USER", "")
    service._check_linger()  # no raise


def test_check_linger_linger_no_warns(monkeypatch, capsys):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/loginctl")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (0, "Linger=no\n", ""))
    service._check_linger()
    err = capsys.readouterr().err
    assert "Linger=no" in err or "enable-linger" in err


def test_check_linger_linger_yes_silent(monkeypatch, capsys):
    monkeypatch.setattr(service.shutil, "which",
                        lambda n: "/usr/bin/loginctl")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(service, "_run",
                        lambda *a, **k: (0, "Linger=yes\n", ""))
    service._check_linger()
    # No warning printed
    assert "Linger=no" not in capsys.readouterr().err


# ---- _post_install_probe ------------------------------------------------

def test_post_install_probe_daemon_up(monkeypatch):
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    from aipager.doctor import CheckResult, OK
    monkeypatch.setattr("aipager.doctor.check_daemon",
                        lambda: CheckResult(OK, "daemon", detail=[]))
    service._post_install_probe()  # no warning


def test_post_install_probe_daemon_down_warns(monkeypatch, capsys):
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    from aipager.doctor import CheckResult, FAIL
    monkeypatch.setattr("aipager.doctor.check_daemon",
                        lambda: CheckResult(FAIL, "daemon", detail=["no"]))
    service._post_install_probe()
    err = capsys.readouterr().err
    assert "didn't come up" in err


# ---- _install_macos paths ----------------------------------------------

def test_install_macos_no_launchctl(monkeypatch, capsys):
    monkeypatch.setattr(service.shutil, "which", lambda n: None)
    rc = service._install_macos()
    assert rc == 2


def test_install_macos_success(monkeypatch, tmp_path):
    monkeypatch.setattr(service.shutil, "which", lambda n: "/usr/bin/launchctl")
    monkeypatch.setattr(service, "MACOS_PLIST_PATH",
                        tmp_path / "com.aipager.daemon.plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "aipager.log")
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/bin/aipager")
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    rc = service._install_macos()
    assert rc == 0


def test_install_macos_bootstrap_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service.shutil, "which", lambda n: "/usr/bin/launchctl")
    monkeypatch.setattr(service, "MACOS_PLIST_PATH",
                        tmp_path / "com.aipager.daemon.plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "aipager.log")
    monkeypatch.setattr(service, "_resolve_aipager_bin",
                        lambda: "/usr/bin/aipager")
    def _run(cmd, **k):
        if "bootstrap" in cmd:
            return (5, "", "permission denied")
        return (0, "", "")
    monkeypatch.setattr(service, "_run", _run)
    rc = service._install_macos()
    assert rc == 5


# ---- _require_installed_* ----------------------------------------------

def test_require_installed_linux_exists(monkeypatch, tmp_path):
    target = tmp_path / "aipager.service"
    target.touch()
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", target)
    assert service._require_installed_linux() is True


def test_require_installed_linux_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "no")
    assert service._require_installed_linux() is False
    assert "isn't installed" in capsys.readouterr().err


def test_require_installed_macos_exists(monkeypatch, tmp_path):
    target = tmp_path / "plist"
    target.touch()
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", target)
    assert service._require_installed_macos() is True


def test_require_installed_macos_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", tmp_path / "no")
    assert service._require_installed_macos() is False


# ---- start/stop/status/uninstall ---------------------------------------

def test_start_linux_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_linux", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._start_linux() == 0


def test_start_linux_not_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_linux", lambda: False)
    assert service._start_linux() == 2


def test_stop_linux_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_linux", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._stop_linux() == 0


def test_status_linux_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_linux", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._status_linux() == 0


def test_start_macos_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_macos", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._start_macos() == 0


def test_stop_macos_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_macos", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._stop_macos() == 0


def test_status_macos_installed(monkeypatch):
    monkeypatch.setattr(service, "_require_installed_macos", lambda: True)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._status_macos() == 0


def test_uninstall_linux(monkeypatch, tmp_path):
    target = tmp_path / "aipager.service"
    target.touch()
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", target)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._uninstall_linux() == 0
    assert not target.exists()


def test_uninstall_macos(monkeypatch, tmp_path):
    target = tmp_path / "plist"
    target.touch()
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", target)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    assert service._uninstall_macos() == 0
    assert not target.exists()


# ---- _no_log_source ----------------------------------------------------

def test_no_log_source(capsys):
    rc = service._no_log_source()
    assert rc == 2
    assert "log source" in capsys.readouterr().err.lower()


# ---- _logs_linux / _logs_macos -----------------------------------------

def test_logs_linux_no_unit_returns_no_log_source(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", tmp_path / "missing")
    assert service._logs_linux() == 2


def test_logs_linux_runs_journalctl(monkeypatch, tmp_path):
    target = tmp_path / "aipager.service"
    target.touch()
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", target)
    runs = []
    monkeypatch.setattr(service, "_run",
                        lambda cmd, **k: runs.append(cmd) or (0, "", ""))
    assert service._logs_linux(follow=True, lines=50) == 0
    assert any("journalctl" in str(r) for r in runs)


def test_logs_macos_no_files_returns_no_log_source(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", tmp_path / "no_plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "no_log")
    assert service._logs_macos() == 2


def test_logs_macos_runs_tail(monkeypatch, tmp_path):
    plist = tmp_path / "plist"
    plist.touch()
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", plist)
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "log.txt")
    runs = []
    monkeypatch.setattr(service, "_run",
                        lambda cmd, **k: runs.append(cmd) or (0, "", ""))
    assert service._logs_macos() == 0
    assert any("tail" in str(r) for r in runs)


# ---- cmd_logs ---------------------------------------------------------

def test_cmd_logs_linux(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr(service, "_logs_linux", lambda **k: 7)
    assert service.cmd_logs() == 7


def test_cmd_logs_macos(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "macos")
    monkeypatch.setattr(service, "_logs_macos", lambda **k: 8)
    assert service.cmd_logs() == 8


def test_cmd_logs_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "freebsd")
    rc = service.cmd_logs()
    assert rc == 1
    assert "not supported" in capsys.readouterr().err


# ---- cmd_service -------------------------------------------------------

def test_cmd_service_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "freebsd")
    rc = service.cmd_service(argparse.Namespace(service_cmd="install"))
    assert rc == 1


def test_cmd_service_unknown_subcommand(monkeypatch, capsys):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    rc = service.cmd_service(argparse.Namespace(service_cmd="bogus"))
    assert rc == 1


def test_cmd_service_install_calls_require_config(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    # _DISPATCH was built at module-import time; patch the bound function
    monkeypatch.setitem(service._DISPATCH["linux"], "install", lambda: 0)
    called = []
    monkeypatch.setattr("aipager.preflight.require_config",
                        lambda: called.append(1))
    rc = service.cmd_service(argparse.Namespace(service_cmd="install"))
    assert rc == 0
    assert called == [1]


def test_cmd_service_start_skips_require_config(monkeypatch):
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setitem(service._DISPATCH["linux"], "start", lambda: 0)
    called = []
    monkeypatch.setattr("aipager.preflight.require_config",
                        lambda: called.append(1))
    rc = service.cmd_service(argparse.Namespace(service_cmd="start"))
    assert rc == 0
    assert called == []  # NOT called for start
