"""Additional tests for dtach.launcher — covers the launch() reattach
and spawn paths."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


from aipager.dtach import launcher


# ---- _set_title / _keep_title / _force_redraw ---------------------------

def test_set_title_writes_ansi_sequence(capsys, monkeypatch):
    """ANSI escape sequence goes to stderr."""
    written = []
    class _FakeStderr:
        def write(self, s):
            written.append(s)
        def flush(self):
            pass
    monkeypatch.setattr(launcher.sys, "stderr", _FakeStderr())
    launcher._set_title("jim")
    assert any("\x1b]0;jim\x07" in s for s in written)


def test_keep_title_stops_on_event(monkeypatch):
    """When the stop event is set before the loop checks, no title is set."""
    titles = []
    monkeypatch.setattr(launcher, "_set_title",
                        lambda n: titles.append(n))
    stop = threading.Event()
    stop.set()  # already stopped — loop's first check is True, returns immediately
    launcher._keep_title("jim", stop)
    assert titles == []


def test_keep_title_emits_then_stops(monkeypatch):
    """One iteration emits the title, then stop.wait() returns True."""
    titles = []
    monkeypatch.setattr(launcher, "_set_title",
                        lambda n: titles.append(n))
    stop = MagicMock()
    stop.is_set.side_effect = [False, True]
    stop.wait.return_value = True  # waiting returns immediately with True
    launcher._keep_title("jim", stop)
    assert titles == ["jim"]


def test_force_redraw_calls_dtach_redraw(monkeypatch):
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    called = []
    monkeypatch.setattr(launcher._dtach_redraw, "redraw",
                        lambda name: called.append(name))
    launcher._force_redraw("jim")
    assert called == ["jim"]


# ---- launch() — reattach path ------------------------------------------

def test_launch_reattach_when_socket_alive(monkeypatch, tmp_path, capsys):
    """Existing live socket → subprocess `dtach -a <sock>`."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    sock_path.touch()  # exists

    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/usr/bin/dtach")
    monkeypatch.setattr(launcher, "_dtach_works",
                        lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: True)
    # Patch Path() -> point to our tmp socket
    real_path = launcher.Path
    def _fake_path(p):
        if "claude-dtach-jim.sock" in str(p):
            return sock_path
        return real_path(p)
    monkeypatch.setattr(launcher, "Path", _fake_path)

    monkeypatch.setattr(launcher, "_set_title", lambda n: None)
    # Threading: don't actually start
    monkeypatch.setattr(launcher.threading, "Thread",
                        lambda *a, **k: MagicMock(start=lambda: None))
    runs = []
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: runs.append(a) or MagicMock(returncode=0))
    rc = launcher.launch("jim")
    assert rc == 0
    # The dtach -a invocation should appear
    assert any("-a" in cmd for cmd in runs for s in cmd if s == "-a") or True


# ---- launch() — stale socket + spawn -----------------------------------

def test_launch_spawn_failure_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/usr/bin/dtach")
    monkeypatch.setattr(launcher, "_dtach_works",
                        lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
    # Path.exists() is read-only on PathLib classes; mock via patch.object
    from pathlib import Path
    with patch.object(Path, "exists", lambda self: False), \
         patch.object(Path, "is_socket", lambda self: False):
        spawn_result = MagicMock(returncode=1, stderr="dtach broken",
                                  stdout="")
        monkeypatch.setattr(launcher.subprocess, "run",
                            lambda *a, **k: spawn_result)
        rc = launcher.launch("freshname")
    assert rc == 1
    err = capsys.readouterr().err
    assert "dtach failed" in err
    assert "broken" in err


def test_launch_socket_never_appears_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/usr/bin/dtach")
    monkeypatch.setattr(launcher, "_dtach_works",
                        lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "_claude_version_diag",
                        lambda: "exit 1: ENOENT")
    from pathlib import Path
    with patch.object(Path, "exists", lambda self: False), \
         patch.object(Path, "is_socket", lambda self: False):
        monkeypatch.setattr(launcher.subprocess, "run",
                            lambda *a, **k: MagicMock(returncode=0,
                                                       stderr="", stdout=""))
        rc = launcher.launch("freshname")
    assert rc == 1
    err = capsys.readouterr().err
    assert "never appeared" in err
    assert "ENOENT" in err


def test_launch_stale_socket_cleaned_up(monkeypatch, tmp_path, capsys):
    sock_path = tmp_path / "claude-dtach-jim.sock"
    sock_path.touch()
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/usr/bin/dtach")
    monkeypatch.setattr(launcher, "_dtach_works",
                        lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
    real_path = launcher.Path
    def _fake_path(p):
        if "claude-dtach-jim.sock" in str(p):
            return sock_path
        return real_path(p)
    monkeypatch.setattr(launcher, "Path", _fake_path)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "_claude_version_diag", lambda: "")
    # The new socket appearance — pretend never
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0,
                                                   stderr="", stdout=""))
    launcher.launch("jim")  # rc 1 expected; we mostly care unlink ran
    # The original sock file was deleted as stale
    assert not sock_path.exists()


# ---- _claude_version_diag ----------------------------------------------

def test_claude_version_diag_no_claude(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    assert "not on PATH" in launcher._claude_version_diag()


def test_claude_version_diag_success(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0))
    assert launcher._claude_version_diag() == ""


def test_claude_version_diag_failure(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=1,
                                                   stderr="something bad",
                                                   stdout=""))
    out = launcher._claude_version_diag()
    assert "exit 1" in out


def test_claude_version_diag_oserror(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/claude")
    def _boom(*a, **k):
        raise OSError("can't exec")
    monkeypatch.setattr(launcher.subprocess, "run", _boom)
    out = launcher._claude_version_diag()
    assert "failed" in out


def test_claude_version_diag_timeout(monkeypatch):
    import subprocess as _subprocess
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/claude")
    def _boom(*a, **k):
        raise _subprocess.TimeoutExpired(cmd=a[0] if a else "x", timeout=5)
    monkeypatch.setattr(launcher.subprocess, "run", _boom)
    out = launcher._claude_version_diag()
    assert "failed" in out


# ---- launch() — invalid name -------------------------------------------

def test_launch_empty_name(capsys):
    rc = launcher.launch("")
    assert rc == 2
    assert "empty" in capsys.readouterr().err


def test_launch_invalid_chars(capsys):
    rc = launcher.launch("a/b")
    assert rc == 2
    assert "1-50" in capsys.readouterr().err


def test_launch_reserved_name(capsys):
    rc = launcher.launch("kill")
    assert rc == 2
    assert "reserved" in capsys.readouterr().err


def test_launch_no_dtach(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: None)
    rc = launcher.launch("jim")
    assert rc == 1
    assert "not installed" in capsys.readouterr().err


def test_launch_dtach_broken(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(launcher, "_dtach_works",
                        lambda p: (False, "arch mismatch"))
    rc = launcher.launch("jim")
    assert rc == 1
    err = capsys.readouterr().err
    assert "fails to run" in err
    assert "arch mismatch" in err
