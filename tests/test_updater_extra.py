"""Additional updater.py tests covering _has_binary,
cmd_update, _remove_path, _uninstall_binary, cmd_uninstall."""

from __future__ import annotations

import argparse
import subprocess

import pytest

from aipager import install_source, self_update, updater
from aipager.install_source import InstallSource


# ---- _has_binary --------------------------------------------------------

def test_has_binary_present(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda n: "/usr/bin/" + n)
    assert updater._has_binary("uv") is True


def test_has_binary_absent(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda n: None)
    assert updater._has_binary("uv") is False


# ---- cmd_update ---------------------------------------------------------
#
# Detection by PATH probing (`_uv_has_aipager` & co.) is gone: it picked
# whichever installer listed a package containing "aipager" rather than the
# one owning the running interpreter, and failed outright off-PATH.

def _src(kind="uv", upgradable=True):
    return InstallSource(kind=kind, prefix="/venvs/aipager",
                         python="/venvs/aipager/bin/python", origin="index",
                         upgradable=upgradable,
                         reason=None if upgradable else "refused for the test")


def _seam(monkeypatch, result):
    runs: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        runs.append(list(argv))
        if "-I" in argv:
            return self_update.CommandResult(0, "AIPAGER_VERSION=9.9.9", False, None)
        return result
    monkeypatch.setattr(self_update, "_run_command", _run)
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/{n}")
    return runs


def test_cmd_update_missing_installer_binary(monkeypatch, capsys):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _src("uv"))
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: None)
    assert updater.cmd_update() == 1
    assert "could not find `uv`" in capsys.readouterr().err


def test_cmd_update_uv(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _src("uv"))
    runs = _seam(monkeypatch, self_update.CommandResult(0, "", False, None))
    assert updater.cmd_update() == 0
    assert runs[0][:3] == ["/abs/uv", "tool", "upgrade"]


def test_cmd_update_spawn_failure(monkeypatch, capsys):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _src("uv"))
    _seam(monkeypatch, self_update.CommandResult(None, "", False, "OSError: perm denied"))
    assert updater.cmd_update() == 1
    assert "upgrade failed" in capsys.readouterr().err


def test_cmd_update_nonzero_returncode_fails(monkeypatch, capsys):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _src("uv"))
    _seam(monkeypatch, self_update.CommandResult(42, "", False, None))
    assert updater.cmd_update() == 1
    assert "exit 42" in capsys.readouterr().err


# ---- _stop_daemon -------------------------------------------------------

def _isolate_pkill(monkeypatch, available=False):
    """Never let _stop_daemon reach the real `pkill -f "aipager start"`."""
    monkeypatch.setattr(updater, "_has_binary", lambda n: available)


def test_stop_daemon_removes_the_service_unit(monkeypatch, tmp_path):
    """Asserts the EFFECT, not the invocation.

    The previous version of this test mocked ``subprocess.run`` and
    asserted only that an argv containing "service"/"uninstall" had been
    passed to it. That assertion held on every run while the real command
    failed on every run: ``_stop_daemon`` shelled out to
    ``python -m aipager.cli``, and ``aipager.cli`` is a package with no
    ``__main__.py``, so the interpreter refused to execute it. The mock
    meant nothing ever ran, so the test could not see that. Real users
    were left with an enabled Restart=always unit pointing at the binary
    uninstall was about to delete.

    So: check the unit file is actually gone.
    """
    from aipager import service

    unit = tmp_path / "aipager.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit)
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr(service, "_run", lambda cmd, **k: (0, "", ""))
    _isolate_pkill(monkeypatch)

    updater._stop_daemon()

    assert not unit.exists(), (
        "uninstall left the service unit on disk — systemd will keep "
        "restarting a binary that is about to be deleted"
    )


def test_stop_daemon_runs_pkill_when_available(monkeypatch):
    from aipager import service

    monkeypatch.setattr(service, "cmd_service", lambda args: 0)
    runs = []

    def _run(argv, *, timeout, env=None, capture=True):
        runs.append(list(argv))
        return self_update.CommandResult(0, "", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    _isolate_pkill(monkeypatch, available=True)
    updater._stop_daemon()
    assert any("pkill" in r[0] and r[1:] == ["-f", "aipager start"] for r in runs)


def test_stop_daemon_swallows_errors(monkeypatch):
    from aipager import service

    def _boom(*a, **k):
        raise OSError("perm")

    monkeypatch.setattr(service, "cmd_service", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    _isolate_pkill(monkeypatch)
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


@pytest.mark.parametrize("installer,tail", [
    ("uv", ["tool", "uninstall", "aipager"]),
    ("pipx", ["uninstall", "aipager"]),
    ("brew", ["uninstall", "aipager"]),
])
def test_uninstall_binary_goes_through_the_seam(installer, tail, monkeypatch):
    runs = _seam(monkeypatch, self_update.CommandResult(0, "", False, None))
    assert updater._uninstall_binary(installer) == 0
    assert runs == [[f"/abs/{installer}", *tail]]


def test_uninstall_binary_reports_spawn_error(monkeypatch):
    _seam(monkeypatch, self_update.CommandResult(None, "", False, "OSError: perm"))
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
    # Sandbox the resolved control socket as well — see the same
    # redirect in tests/test_updater.py for why _fake_path alone is not
    # enough once SOCKET_PATH lives outside /tmp.
    runtime_sock = tmp_path / "runtime" / "aipager.sock"
    runtime_sock.parent.mkdir(parents=True, exist_ok=True)
    runtime_sock.write_text("")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(runtime_sock))
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
    assert not runtime_sock.exists()
