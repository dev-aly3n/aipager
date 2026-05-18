"""Tests for aipager.updater (`aipager update` / `aipager uninstall`)."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from aipager import updater


def _ns(**kw):
    return argparse.Namespace(**kw)


# ----- installer detection -----

def test_detect_uv(monkeypatch):
    monkeypatch.setattr(updater, "_uv_has_aipager", lambda: True)
    monkeypatch.setattr(updater, "_pipx_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_brew_has_aipager", lambda: False)
    assert updater._detect_installer() == "uv"


def test_detect_pipx(monkeypatch):
    monkeypatch.setattr(updater, "_uv_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_pipx_has_aipager", lambda: True)
    monkeypatch.setattr(updater, "_brew_has_aipager", lambda: False)
    assert updater._detect_installer() == "pipx"


def test_detect_brew(monkeypatch):
    monkeypatch.setattr(updater, "_uv_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_pipx_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_brew_has_aipager", lambda: True)
    assert updater._detect_installer() == "brew"


def test_detect_none(monkeypatch):
    monkeypatch.setattr(updater, "_uv_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_pipx_has_aipager", lambda: False)
    monkeypatch.setattr(updater, "_brew_has_aipager", lambda: False)
    assert updater._detect_installer() is None


def test_uv_has_aipager_when_binary_missing(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda name: False)
    assert updater._uv_has_aipager() is False


def test_uv_has_aipager_parses_stdout(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda name: True)

    class _R:
        returncode = 0
        stdout = "aipager v0.3.11\nfoo v1.0.0\n"

    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _R())
    assert updater._uv_has_aipager() is True


def test_uv_has_aipager_handles_timeout(monkeypatch):
    monkeypatch.setattr(updater, "_has_binary", lambda name: True)

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="uv", timeout=10)

    monkeypatch.setattr(updater.subprocess, "run", _boom)
    assert updater._uv_has_aipager() is False


# ----- cmd_update -----

def test_update_no_installer(monkeypatch, capsys):
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    rc = updater.cmd_update()
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not detect" in err


def test_update_uv_adds_refresh(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "uv")
    seen: list = []
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    rc = updater.cmd_update()
    assert rc == 0
    assert seen[0] == ["uv", "tool", "upgrade", "aipager", "--refresh"]


def test_update_pipx_command(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "pipx")
    seen: list = []
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    updater.cmd_update()
    assert seen[0] == ["pipx", "upgrade", "aipager"]


def test_update_brew_command(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: "brew")
    seen: list = []
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    updater.cmd_update()
    assert seen[0] == ["brew", "upgrade", "aipager"]


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
    seen: list = []
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    rc = updater.cmd_uninstall(_ns(force=True))
    assert rc == 0
    assert ["uv", "tool", "uninstall", "aipager"] in seen


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


# ----- _remove_tmp_sockets -----

def test_remove_tmp_sockets_handles_missing(monkeypatch):
    # Should not raise even when no matching files exist.
    monkeypatch.setattr(updater, "Path",
                        lambda p: Path(p))
    updater._remove_tmp_sockets()  # smoke: no exception
