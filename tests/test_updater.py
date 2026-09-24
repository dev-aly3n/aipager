"""Tests for aipager.updater (`aipager update` / `aipager uninstall`)."""

from __future__ import annotations

import argparse
import os
import sys

from aipager import install_source, self_update, updater
from aipager.install_source import InstallSource


def _ns(**kw):
    return argparse.Namespace(**kw)


# ----- installer detection -----
#
# Detection is by the RUNNING interpreter now (aipager.install_source; see
# tests/test_install_source.py for the matrix). `_detect_installer` is the
# compat wrapper everything else still calls.

def _source(kind="pipx", *, upgradable=True, origin="index", detail=None):
    return InstallSource(kind=kind, prefix="/venvs/aipager",
                         python="/venvs/aipager/bin/python", origin=origin,
                         origin_detail=detail, upgradable=upgradable,
                         reason=None if upgradable else "refused for the test")


def test_detect_installer_follows_the_running_interpreter(monkeypatch):
    for kind in ("uv", "pipx", "brew", "pip"):
        monkeypatch.setattr(install_source, "detect_install_source",
                            lambda k=kind: _source(k))
        assert updater._detect_installer() == kind


def test_detect_installer_none_for_refused_install(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda: _source("editable", upgradable=False))
    assert updater._detect_installer() is None


def test_detect_installer_never_shells_out(monkeypatch):
    """The old probes ran `uv tool list` / `pipx list` / `brew list`."""
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _source("uv"))
    monkeypatch.setattr(updater, "_has_binary", lambda n: (_ for _ in ()).throw(
        AssertionError("detection must not probe PATH")))
    assert updater._detect_installer() == "uv"


# ----- cmd_update -----

def _fake_seam(monkeypatch, *, upgrade_rc=0, probe_version="0.7.14",
               running="0.7.13", timed_out=False):
    calls: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        calls.append((list(argv), timeout, capture))
        if "-I" in argv:  # the fresh-interpreter version probe
            return self_update.CommandResult(
                0, f"AIPAGER_VERSION={probe_version}", False, None)
        if argv[1:3] == ["--user", "show"]:
            return self_update.CommandResult(0, "KillMode=process", False, None)
        if timed_out:
            return self_update.CommandResult(None, "", True, "timed out after 600s")
        return self_update.CommandResult(upgrade_rc, "", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    monkeypatch.setattr(self_update, "running_version", lambda: running)
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
    return calls


def test_update_refused_install(monkeypatch, capsys):
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda: _source("editable", upgradable=False))
    rc = updater.cmd_update()
    assert rc == 1
    assert "can't update this install" in capsys.readouterr().err


def test_update_uv_adds_refresh(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _source("uv"))
    calls = _fake_seam(monkeypatch)
    assert updater.cmd_update() == 0
    assert calls[0][0] == ["/abs/bin/uv", "tool", "upgrade", "aipager", "--refresh"]
    assert calls[0][1] == self_update.UPGRADE_TIMEOUT_SECONDS
    assert calls[0][2] is False  # live installer output in the terminal


def test_update_pipx_command(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _source("pipx"))
    calls = _fake_seam(monkeypatch)
    updater.cmd_update()
    assert calls[0][0] == ["/abs/bin/pipx", "upgrade", "aipager"]


def test_update_brew_command(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _source("brew"))
    calls = _fake_seam(monkeypatch)
    updater.cmd_update()
    assert calls[0][0] == ["/abs/bin/brew", "upgrade", "aipager"]


def test_update_pip_venv_uses_its_own_interpreter(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _source("pip"))
    calls = _fake_seam(monkeypatch)
    updater.cmd_update()
    assert calls[0][0] == ["/venvs/aipager/bin/python", "-m", "pip", "install",
                           "--upgrade", "aipager"]


# ----- cmd_uninstall -----

def test_uninstall_cancelled(monkeypatch, capsys):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    rc = updater.cmd_uninstall(_ns(force=False))
    assert rc == 0


def test_uninstall_force_removes_paths(monkeypatch, tmp_path):
    # Redirect target paths into tmp_path so we don't touch the real home dir.
    user_paths = [
        tmp_path / "aipager-config-dir",
        tmp_path / "claude-state.json",
    ]
    for p in user_paths:
        if "dir" in p.name:
            p.mkdir()
            (p / "sentinel").write_text("x")
        else:
            p.write_text("{}")
    monkeypatch.setattr(updater, "_USER_PATHS_TO_REMOVE", user_paths)
    monkeypatch.setattr(updater, "_MACOS_PATHS_TO_REMOVE", [])
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)

    rc = updater.cmd_uninstall(_ns(force=True))
    assert rc == 0
    for p in user_paths:
        assert not p.exists()


def test_uninstall_calls_binary_uninstall(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_USER_PATHS_TO_REMOVE", [])
    monkeypatch.setattr(updater, "_MACOS_PATHS_TO_REMOVE", [])
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
    seen: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        seen.append(list(argv))
        return self_update.CommandResult(0, "", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    rc = updater.cmd_uninstall(_ns(force=True))
    assert rc == 0
    assert ["/abs/bin/uv", "tool", "uninstall", "aipager"] in seen


# ----- _remove_path -----

def test_remove_path_file(tmp_path):
    f = tmp_path / "x"
    f.write_text("hello")
    assert updater._remove_path(f) is True
    assert not f.exists()


def test_remove_path_directory(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "inside").write_text("y")
    assert updater._remove_path(d) is True
    assert not d.exists()


def test_remove_path_missing_returns_false(tmp_path):
    assert updater._remove_path(tmp_path / "nope") is False


# ----- install_extra_cmd (5.3 follow-up) -----

def test_install_extra_cmd_uv(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
    assert updater.install_extra_cmd("uv", "voice") == [
        "/abs/bin/uv", "tool", "install", "--reinstall", "aipager[voice]",
    ]


def test_install_extra_cmd_pipx(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
    assert updater.install_extra_cmd("pipx", "voice") == [
        "/abs/bin/pipx", "install", "--force", "aipager[voice]",
    ]


def test_install_extra_cmd_brew_uses_pip_fallback():
    """Homebrew formulas don't expose pip extras, so we install
    faster-whisper directly into the daemon's Python interpreter
    instead of returning None."""
    assert updater.install_extra_cmd("brew", "voice") == [
        sys.executable, "-m", "pip", "install", "--upgrade",
        "faster-whisper>=1.0",
    ]


def test_install_extra_cmd_unknown_installer_uses_pip_fallback():
    """Editable / pip-user / unknown installs have no installer to go
    through — pip into the running interpreter is the universal fallback."""
    expected = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        "faster-whisper>=1.0",
    ]
    assert updater.install_extra_cmd(None, "voice") == expected
    assert updater.install_extra_cmd("editable", "voice") == expected


def test_install_extra_cmd_unknown_extra_returns_none():
    """Genuinely unsupported extras (no entry in _EXTRA_PACKAGES, and
    not handled by uv/pipx's generic extra syntax) return None so the
    caller can surface a clear error."""
    assert updater.install_extra_cmd(None, "telepathy") is None
    assert updater.install_extra_cmd("brew", "telepathy") is None


# ----- _remove_tmp_sockets -----

def test_remove_tmp_sockets_handles_missing(monkeypatch, tmp_path):
    # Should not raise even when no matching files exist.
    real_path = updater.Path

    # _remove_tmp_sockets also unlinks the *resolved* config.SOCKET_PATH,
    # which on any host with $XDG_RUNTIME_DIR set is NOT under /tmp and so
    # falls straight through _fake_path's `return real_path(p)` to the
    # live daemon's real control socket. Redirect it into tmp_path too.
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

    # Assert containment BEFORE the call, not after: _remove_tmp_sockets
    # unlinks the live daemon's hook socket and every running session's
    # dtach socket, so a sandbox that leaks would already have destroyed
    # them by the time a post-hoc assertion could notice.
    assert _fake_path("/tmp") == tmp_path
    assert _fake_path("/tmp/aipager.sock").parent == tmp_path

    # Positive proof, for any leak the two assertions above cannot see: a
    # decoy at a real /tmp path matching one of the globs, which must
    # survive. A status file rather than a socket on purpose — a stray
    # /tmp/claude-dtach-*.sock would look like a live session to a daemon
    # running on this machine.
    decoy = real_path(f"/tmp/claude-status-updater-sandbox-{os.getpid()}.json")
    decoy.write_text("{}")
    try:
        monkeypatch.setattr(updater, "Path", _fake_path)
        updater._remove_tmp_sockets()  # smoke: no exception, tmp_path is empty
        assert decoy.exists(), "sandbox leaked — the real /tmp was reached"
        # Positive proof the SOCKET_PATH branch actually ran (and ran
        # inside the sandbox): a test that merely redirected it would
        # still pass if the branch were deleted outright.
        assert not runtime_sock.exists(), "config.SOCKET_PATH was not unlinked"
    finally:
        decoy.unlink(missing_ok=True)
