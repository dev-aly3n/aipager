"""Additional doctor.py tests covering _probe_binary, check_dtach,
check_claude, check_daemon, check_service_installed, _print_results,
and cmd_doctor."""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import MagicMock


from aipager import doctor


# ---- _probe_binary ------------------------------------------------------

def test_probe_binary_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0, stdout="ok 1.0\n",
                                                   stderr=""))
    ok, info = doctor._probe_binary("/usr/bin/x", "--version")
    assert ok is True
    assert "ok 1.0" in info


def test_probe_binary_file_not_found(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("no")
    monkeypatch.setattr(subprocess, "run", _boom)
    ok, info = doctor._probe_binary("/nope", "-V")
    assert ok is False
    assert "not found" in info


def test_probe_binary_timeout(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "x", timeout=3)
    monkeypatch.setattr(subprocess, "run", _boom)
    ok, info = doctor._probe_binary("/x", "-V")
    assert ok is False
    assert "timed out" in info


def test_probe_binary_os_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("EACCES")
    monkeypatch.setattr(subprocess, "run", _boom)
    ok, info = doctor._probe_binary("/x", "-V")
    assert ok is False
    assert "EACCES" in info


def test_probe_binary_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=1,
                                                   stdout="", stderr="bad arg"))
    ok, info = doctor._probe_binary("/x", "-V")
    assert ok is False
    assert "bad arg" in info


# ---- check_dtach --------------------------------------------------------

def test_check_dtach_dtach_bin_works(monkeypatch):
    import sys as _sys
    class _FakeDtachBin:
        @staticmethod
        def path():
            return "/opt/dtach"
    monkeypatch.setitem(_sys.modules, "dtach_bin", _FakeDtachBin)
    monkeypatch.setattr(doctor, "_probe_binary",
                        lambda p, *a, **k: (True, "dtach 0.9"))
    r = doctor.check_dtach()
    assert r.status == doctor.OK


def test_check_dtach_not_found(monkeypatch):
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "dtach_bin", None)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    r = doctor.check_dtach()
    assert r.status == doctor.FAIL


def test_check_dtach_binary_fails_to_exec(monkeypatch):
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "dtach_bin", None)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/dtach")
    monkeypatch.setattr(doctor, "_probe_binary",
                        lambda p, *a, **k: (False, "binary not found"))
    r = doctor.check_dtach()
    assert r.status == doctor.FAIL


def test_check_dtach_v_fails_but_h_works(monkeypatch):
    """Some dtach binaries reject -V but accept -h."""
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "dtach_bin", None)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/dtach")
    calls = []
    def _probe(p, *args, **k):
        calls.append(args)
        if "-V" in args:
            return False, "unknown flag"
        return True, "dtach help"
    monkeypatch.setattr(doctor, "_probe_binary", _probe)
    r = doctor.check_dtach()
    assert r.status == doctor.OK


# ---- check_claude -------------------------------------------------------

def test_check_claude_not_on_path(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    r = doctor.check_claude()
    assert r.status == doctor.FAIL


def test_check_claude_version_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "_probe_binary",
                        lambda p, *a, **k: (False, "exit 1"))
    r = doctor.check_claude()
    assert r.status == doctor.WARN


def test_check_claude_happy(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(doctor, "_probe_binary",
                        lambda p, *a, **k: (True, "claude 1.0"))
    r = doctor.check_claude()
    assert r.status == doctor.OK


# ---- check_daemon -------------------------------------------------------

def test_check_daemon_no_socket(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: False):
        r = doctor.check_daemon()
    assert r.status == doctor.FAIL


def test_check_daemon_socket_refused(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: True):
        fake_sock = MagicMock()
        fake_sock.sendto.side_effect = ConnectionRefusedError
        monkeypatch.setattr(doctor.socket, "socket",
                            lambda *a, **k: fake_sock)
        r = doctor.check_daemon()
    assert r.status == doctor.FAIL


def test_check_daemon_socket_other_oserror_warns(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: True):
        fake_sock = MagicMock()
        fake_sock.sendto.side_effect = OSError("EPERM")
        monkeypatch.setattr(doctor.socket, "socket",
                            lambda *a, **k: fake_sock)
        r = doctor.check_daemon()
    assert r.status == doctor.WARN


def test_check_daemon_socket_reachable(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: True):
        fake_sock = MagicMock()
        fake_sock.sendto.return_value = None  # success
        monkeypatch.setattr(doctor.socket, "socket",
                            lambda *a, **k: fake_sock)
        r = doctor.check_daemon()
    assert r.status == doctor.OK


# ---- check_service_installed -------------------------------------------

def test_check_service_installed_linux_with_unit(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: True):
        r = doctor.check_service_installed()
    assert r.status == doctor.OK


def test_check_service_installed_linux_without_unit(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: False):
        r = doctor.check_service_installed()
    assert r.status == doctor.WARN


def test_check_service_installed_darwin_with_plist(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    from pathlib import Path
    from unittest.mock import patch
    with patch.object(Path, "exists", lambda self: True):
        r = doctor.check_service_installed()
    assert r.status == doctor.OK


def test_check_service_installed_other_platform(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "FreeBSD")
    r = doctor.check_service_installed()
    assert r.status == doctor.WARN


# ---- _print_results / _print_fixes / _print_summary --------------------

def test_print_results_with_fix(capsys):
    results = [
        doctor.CheckResult(doctor.FAIL, "test thing",
                           detail=["broken"], fix="run command"),
    ]
    doctor._print_results(results)
    doctor._print_fixes(results)
    doctor._print_summary(results)
    out = capsys.readouterr().out
    assert "broken" in out
    assert "run command" in out


def test_print_results_with_ok_status(capsys):
    results = [
        doctor.CheckResult(doctor.OK, "thing", detail=["good"]),
    ]
    doctor._print_results(results)
    out = capsys.readouterr().out
    assert "good" in out or "✓" in out


def test_print_summary_all_ok(capsys):
    results = [doctor.CheckResult(doctor.OK, "x", detail=[])]
    doctor._print_summary(results)
    out = capsys.readouterr().out
    assert "ok" in out.lower() or "✓" in out


def test_print_summary_with_failures(capsys):
    results = [
        doctor.CheckResult(doctor.OK, "x", detail=[]),
        doctor.CheckResult(doctor.FAIL, "y", detail=["broken"]),
    ]
    doctor._print_summary(results)
    out = capsys.readouterr().out
    assert "fail" in out.lower() or "✗" in out or "1" in out


# ---- cmd_doctor ---------------------------------------------------------

def test_cmd_doctor_all_ok_returns_0(monkeypatch):
    monkeypatch.setattr(doctor, "run_all", lambda: [
        doctor.CheckResult(doctor.OK, "config", detail=["ok"]),
    ])
    rc = doctor.cmd_doctor(argparse.Namespace())
    assert rc == 0


def test_cmd_doctor_with_failures_returns_1(monkeypatch):
    monkeypatch.setattr(doctor, "run_all", lambda: [
        doctor.CheckResult(doctor.FAIL, "config", detail=["broken"]),
    ])
    rc = doctor.cmd_doctor(argparse.Namespace())
    assert rc == 1


def test_cmd_doctor_safety_check_renders(capsys):
    rc = doctor.cmd_doctor(argparse.Namespace(safety_check=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Safety policy" in out
    assert "~/.claude/**" in out
    assert "owner (bypass_safety" in out


def test_cmd_doctor_multiscope_note(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_all", lambda: [
        doctor.CheckResult(doctor.OK, "config", detail=["ok"]),
    ])
    monkeypatch.setattr("aipager.config.SCOPES", [object(), object()],
                        raising=False)
    doctor.cmd_doctor(argparse.Namespace())
    assert "Multiple scopes share one filesystem" in capsys.readouterr().out


def test_cmd_doctor_no_note_single_scope(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_all", lambda: [
        doctor.CheckResult(doctor.OK, "config", detail=["ok"]),
    ])
    monkeypatch.setattr("aipager.config.SCOPES", [object()], raising=False)
    doctor.cmd_doctor(argparse.Namespace())
    assert "Multiple scopes" not in capsys.readouterr().out
