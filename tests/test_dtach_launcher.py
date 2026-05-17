"""Tests for aipager.dtach_launcher — name validation, stale socket probe."""

from __future__ import annotations

import socket as _socket
import subprocess

import pytest

from aipager import dtach_launcher


# ----- _validate_name -----

@pytest.mark.parametrize("name", [
    "dev", "feature-x", "feature_42", "ABC", "x",
    "a" * 50,
])
def test_validate_name_accepts(name):
    assert dtach_launcher._validate_name(name) is None


@pytest.mark.parametrize("name,reason_substr", [
    ("", "empty"),
    ("a b", "1-50 chars"),
    ("a/b", "1-50 chars"),
    ("a.b", "1-50 chars"),
    ("a" * 51, "1-50 chars"),
    ("../escape", "1-50 chars"),
])
def test_validate_name_rejects(name, reason_substr):
    err = dtach_launcher._validate_name(name)
    assert err is not None
    assert reason_substr in err


# ----- _socket_alive -----

def test_socket_alive_no_file(tmp_path):
    sock = tmp_path / "missing.sock"
    assert dtach_launcher._socket_alive(str(sock)) is False


def test_socket_alive_stale_file(tmp_path):
    # Plain file at the path — connect() raises ENOTSOCK or similar.
    f = tmp_path / "stale.sock"
    f.write_text("")
    assert dtach_launcher._socket_alive(str(f)) is False


def test_socket_alive_listening(tmp_path):
    sock = tmp_path / "live.sock"
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    server.bind(str(sock))
    server.listen(1)
    try:
        assert dtach_launcher._socket_alive(str(sock)) is True
    finally:
        server.close()


def test_socket_alive_dgram_socket_not_stream(tmp_path):
    """If something bound SOCK_DGRAM there (wrong protocol), _socket_alive
    should treat it as not-alive — dtach uses SOCK_STREAM."""
    sock = tmp_path / "wrong.sock"
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
    server.bind(str(sock))
    try:
        assert dtach_launcher._socket_alive(str(sock)) is False
    finally:
        server.close()


# ----- launch error paths (no actual subprocess) -----

def test_launch_invalid_name_returns_2(capsys):
    rc = dtach_launcher.launch("with spaces")
    assert rc == 2
    err = capsys.readouterr().err
    assert "session name" in err
    assert "with spaces" in err


def test_launch_dtach_missing_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: None)
    rc = dtach_launcher.launch("dev")
    assert rc == 1
    err = capsys.readouterr().err
    assert "dtach not installed" in err
    assert "uv tool install" in err


def test_launch_dtach_broken_binary(monkeypatch, capsys):
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(dtach_launcher, "_dtach_works",
                        lambda p: (False, "exec format error"))
    rc = dtach_launcher.launch("dev")
    assert rc == 1
    err = capsys.readouterr().err
    assert "fails to run" in err
    assert "exec format error" in err


def test_launch_spawn_failure_relays_stderr(monkeypatch, capsys):
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(dtach_launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(dtach_launcher, "_socket_alive", lambda s: False)

    class _Fake:
        returncode = 1
        stderr = "dtach: claude: No such file or directory\n"
        stdout = ""

    monkeypatch.setattr(dtach_launcher.subprocess, "run",
                        lambda *a, **k: _Fake())
    rc = dtach_launcher.launch("dev")
    assert rc == 1
    err = capsys.readouterr().err
    assert "dtach failed to start" in err
    assert "No such file or directory" in err


def test_launch_socket_never_appears(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(dtach_launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(dtach_launcher, "_socket_alive", lambda s: False)
    monkeypatch.setattr(dtach_launcher, "_claude_version_diag",
                        lambda: "exit 1: ENOENT")
    monkeypatch.setattr(dtach_launcher.time, "sleep", lambda *_: None)

    class _Ok:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(dtach_launcher.subprocess, "run",
                        lambda *a, **k: _Ok())
    rc = dtach_launcher.launch("dev")
    assert rc == 1
    err = capsys.readouterr().err
    assert "never appeared" in err
    assert "ENOENT" in err


# ----- _dtach_works -----

def test_dtach_works_handles_missing_binary(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(dtach_launcher.subprocess, "run", _boom)
    ok, why = dtach_launcher._dtach_works("/nope")
    assert ok is False
    assert "missing" in why


def test_dtach_works_handles_timeout(monkeypatch):
    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=2)
    monkeypatch.setattr(dtach_launcher.subprocess, "run", _hang)
    ok, why = dtach_launcher._dtach_works("/nope")
    assert ok is False
    assert "hung" in why


def test_dtach_works_recognizes_usage_output(monkeypatch):
    class _R:
        stdout = ""
        stderr = "dtach - emulates the detach feature of screen\nusage: dtach -A ..."
    monkeypatch.setattr(dtach_launcher.subprocess, "run", lambda *a, **k: _R())
    ok, why = dtach_launcher._dtach_works("/usr/bin/dtach")
    assert ok is True
    assert why == ""
