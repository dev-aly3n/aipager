"""`aipager service install` must find its own binary without PATH (8.38).

Run from a `systemd-run` timer on 2026-09-24 it failed with "aipager not
on PATH": a non-interactive environment has no ~/.local/bin on PATH.
"""
import os

import pytest

from aipager import service


def _exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def no_path(monkeypatch, tmp_path):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    monkeypatch.setattr(service.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(service.sys, "argv", ["python"])
    monkeypatch.setattr(service.sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    return tmp_path


def test_path_wins_when_it_has_aipager(monkeypatch, no_path):
    _exe(no_path / "home" / ".local" / "bin" / "aipager")
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/aipager")
    assert service._resolve_aipager_bin() == "/usr/bin/aipager"


def test_falls_back_to_the_running_script(monkeypatch, no_path):
    running = _exe(no_path / "opt" / "bin" / "aipager")
    _exe(no_path / "home" / ".local" / "bin" / "aipager")
    monkeypatch.setattr(service.sys, "argv", [str(running), "service", "install"])
    assert service._resolve_aipager_bin() == str(running)


def test_a_relative_running_script_is_made_absolute(monkeypatch, no_path):
    _exe(no_path / "bin" / "aipager")
    monkeypatch.chdir(no_path)
    monkeypatch.setattr(service.sys, "argv", ["bin/aipager", "service", "install"])
    assert service._resolve_aipager_bin() == str(no_path / "bin" / "aipager")


def test_argv0_that_is_not_aipager_is_ignored(monkeypatch, no_path):
    _exe(no_path / "opt" / "pytest")
    monkeypatch.setattr(service.sys, "argv", [str(no_path / "opt" / "pytest")])
    with pytest.raises(FileNotFoundError):
        service._resolve_aipager_bin()


def test_falls_back_to_local_bin(no_path):
    local = _exe(no_path / "home" / ".local" / "bin" / "aipager")
    assert service._resolve_aipager_bin() == str(local)


def test_falls_back_to_the_script_beside_the_interpreter(no_path):
    beside = _exe(no_path / "venv" / "bin" / "aipager")
    assert service._resolve_aipager_bin() == str(beside)


def test_a_non_executable_candidate_is_skipped(no_path):
    local = no_path / "home" / ".local" / "bin" / "aipager"
    local.parent.mkdir(parents=True)
    local.write_text("not executable")
    beside = _exe(no_path / "venv" / "bin" / "aipager")
    assert not os.access(local, os.X_OK)
    assert service._resolve_aipager_bin() == str(beside)


def test_nothing_found_still_raises(no_path):
    with pytest.raises(FileNotFoundError):
        service._resolve_aipager_bin()
