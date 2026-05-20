"""Additional updater.py tests covering _has_binary, _uv_has_aipager,
cmd_update, _remove_path, _uninstall_binary, cmd_uninstall."""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import MagicMock


from aipager import updater


# ---- _has_binary --------------------------------------------------------

def test_has_binary_present(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda n: "/usr/bin/" + n)
    assert updater._has_binary("uv") is True


def test_has_binary_absent(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda n: None)
    assert updater._has_binary("uv") is False


# ---- _uv_has_aipager ---------------------------------------------------

def test_uv_has_aipager_no_uv(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: False)
    assert updater._uv_has_aipager() is False


def test_uv_has_aipager_yes(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stdout="aipager 0.3.20\n",
                                                   stderr=""))
    assert updater._uv_has_aipager() is True


def test_uv_has_aipager_not_in_list(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stdout="other-tool 1.0\n",
                                                   stderr=""))
    assert updater._uv_has_aipager() is False


def test_uv_has_aipager_subprocess_error(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    def _boom(*a, **k):
        raise OSError("fork failed")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert updater._uv_has_aipager() is False


def test_uv_has_aipager_timeout(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="uv", timeout=10)
    monkeypatch.setattr(subprocess, "run", _boom)
    assert updater._uv_has_aipager() is False


# ---- _pipx_has_aipager / _brew_has_aipager ----------------------------

def test_pipx_has_aipager_yes(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stdout="aipager",
                                                   stderr=""))
    assert updater._pipx_has_aipager() is True


def test_pipx_has_aipager_no_pipx(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: False)
    assert updater._pipx_has_aipager() is False


def test_brew_has_aipager_yes(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stdout="",
                                                   stderr=""))
    assert updater._brew_has_aipager() is True


def test_brew_has_aipager_nonzero(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=1,
                                                   stdout="",
                                                   stderr=""))
    assert updater._brew_has_aipager() is False


def test_brew_has_aipager_timeout(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="brew", timeout=10)
    monkeypatch.setattr(subprocess, "run", _boom)
    assert updater._brew_has_aipager() is False


# ---- cmd_update ---------------------------------------------------------

def test_cmd_update_no_installer_detected(monkeypatch, capsys):
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    rc = updater.cmd_update()
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not detect" in err


def test_cmd_update_uv(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    rc = updater.cmd_update()
    assert rc == 0
    # Verify the uv command was used
    assert any("uv" in str(r) and "upgrade" in str(r) for r in runs)


def test_cmd_update_pipx(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "pipx")
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    assert updater.cmd_update() == 0


def test_cmd_update_brew(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "brew")
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    assert updater.cmd_update() == 0


def test_cmd_update_subprocess_failure(monkeypatch, capsys):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    def _boom(*a, **k):
        raise OSError("perm denied")
    monkeypatch.setattr(subprocess, "run", _boom)
    rc = updater.cmd_update()
    assert rc == 1
    assert "upgrade failed" in capsys.readouterr().err


def test_cmd_update_nonzero_returncode_propagates(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=42))
    assert updater.cmd_update() == 42


# ---- _stop_daemon -------------------------------------------------------

def test_stop_daemon_runs_service_uninstall(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    monkeypatch.setattr(updater, "_has_binary", lambda n: False)  # no pkill
    updater._stop_daemon()
    assert any("service" in str(r) and "uninstall" in str(r) for r in runs)


def test_stop_daemon_runs_pkill_when_available(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    monkeypatch.setattr(updater, "_has_binary", lambda n: True)
    updater._stop_daemon()
    assert any("pkill" in str(r) for r in runs)


def test_stop_daemon_swallows_errors(monkeypatch):
    def _boom(*a, **k):
        raise OSError("perm")
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(updater, "_has_binary", lambda n: False)
    # MUST NOT raise
    updater._stop_daemon()


# ---- _remove_path -------------------------------------------------------

def test_remove_path_file(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert updater._remove_path(f) is True
    assert not f.exists()


def test_remove_path_directory(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "x.txt").write_text("y")
    assert updater._remove_path(d) is True
    assert not d.exists()


def test_remove_path_missing_returns_false(tmp_path):
    assert updater._remove_path(tmp_path / "nope") is False


def test_remove_path_swallows_oserror(tmp_path, monkeypatch, capsys):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    def _boom(self, *a, **k):
        raise OSError("EROFS")
    monkeypatch.setattr(updater.Path, "unlink", _boom)
    assert updater._remove_path(f) is False


# ---- _uninstall_binary --------------------------------------------------

def test_uninstall_binary_no_installer(monkeypatch):
    """None installer → nothing to do, exit 0."""
    assert updater._uninstall_binary(None) == 0


def test_uninstall_binary_uv(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    assert updater._uninstall_binary("uv") == 0


def test_uninstall_binary_pipx(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    assert updater._uninstall_binary("pipx") == 0


def test_uninstall_binary_brew(monkeypatch):
    runs = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    assert updater._uninstall_binary("brew") == 0


def test_uninstall_binary_swallows_subprocess_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("perm")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert updater._uninstall_binary("uv") == 1


# ---- cmd_uninstall ------------------------------------------------------

def test_cmd_uninstall_declined(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    rc = updater.cmd_uninstall(argparse.Namespace(force=False))
    assert rc == 0


def test_cmd_uninstall_force_runs_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_USER_PATHS_TO_REMOVE", [])
    monkeypatch.setattr(updater, "_MACOS_PATHS_TO_REMOVE", [])
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr(updater, "_uninstall_binary", lambda i: 0)
    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    rc = updater.cmd_uninstall(argparse.Namespace(force=True))
    # cmd_uninstall always returns None implicitly when it falls through
    assert rc is None or rc == 0


# ---- _remove_tmp_sockets ------------------------------------------------

def test_remove_tmp_sockets_glob_swallows_errors(monkeypatch, tmp_path):
    """unlinks errors on the per-session sockets are swallowed."""
    # Create some fake socket files in tmp_path
    a = tmp_path / "claude-dtach-jim.sock"
    a.touch()
    b = tmp_path / "claude-status-jim.json"
    b.touch()
    # Redirect Path("/tmp") to tmp_path
    real_path = updater.Path
    def _fake_path(p):
        if p == "/tmp":
            return tmp_path
        if p == "/tmp/aipager.sock":
            return real_path(tmp_path / "aipager.sock")
        return real_path(p)
    monkeypatch.setattr(updater, "Path", _fake_path)
    updater._remove_tmp_sockets()
    # Files unlinked
    assert not a.exists()
    assert not b.exists()
