"""Tests for aipager.dtach.redraw — PTY size bounce to force TUI redraw."""

from __future__ import annotations

import struct
import subprocess
from unittest.mock import MagicMock


from aipager.dtach import redraw


# ---- find_pty -----------------------------------------------------------

def test_find_pty_returns_dev_pts_path(monkeypatch):
    """Happy path: pgrep returns a dtach PID, child PID, and /proc symlink
    points at /dev/pts/N."""
    def _fake_pgrep(*args, **kwargs):
        result = MagicMock()
        # Two different pgrep invocations are made; return the dtach PID
        # the first time, the child PID the second time.
        call_args = args[0] if args else kwargs.get("args", [])
        if "-f" in call_args:
            result.stdout = "12345\n"
        else:
            result.stdout = "12346\n"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_pgrep)
    monkeypatch.setattr(redraw.os, "readlink", lambda p: "/dev/pts/3")
    assert redraw.find_pty("jim") == "/dev/pts/3"


def test_find_pty_returns_none_when_no_dtach_pid(monkeypatch):
    def _fake_pgrep(*args, **kwargs):
        result = MagicMock()
        result.stdout = "\n"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_pgrep)
    assert redraw.find_pty("jim") is None


def test_find_pty_returns_none_on_pgrep_exception(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pgrep", timeout=5)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert redraw.find_pty("jim") is None


def test_find_pty_returns_none_when_no_child(monkeypatch):
    calls = {"n": 0}

    def _fake_pgrep(*args, **kwargs):
        calls["n"] += 1
        result = MagicMock()
        result.stdout = "12345\n" if calls["n"] == 1 else "\n"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_pgrep)
    assert redraw.find_pty("jim") is None


def test_find_pty_returns_none_when_readlink_not_pts(monkeypatch):
    calls = {"n": 0}

    def _fake_pgrep(*args, **kwargs):
        calls["n"] += 1
        result = MagicMock()
        result.stdout = "12345\n"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_pgrep)
    monkeypatch.setattr(redraw.os, "readlink", lambda p: "/dev/null")
    assert redraw.find_pty("jim") is None


def test_find_pty_returns_none_when_readlink_fails(monkeypatch):
    def _fake_pgrep(*args, **kwargs):
        result = MagicMock()
        result.stdout = "12345\n"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_pgrep)
    monkeypatch.setattr(redraw.os, "readlink",
                        lambda p: (_ for _ in ()).throw(FileNotFoundError()))
    assert redraw.find_pty("jim") is None


# ---- bounce_size --------------------------------------------------------

def test_bounce_size_open_fails_returns_false(monkeypatch):
    def _boom(*a, **k):
        raise OSError("EACCES")

    monkeypatch.setattr(redraw.os, "open", _boom)
    assert redraw.bounce_size("/dev/pts/9") is False


def test_bounce_size_too_small_window_returns_false(monkeypatch):
    monkeypatch.setattr(redraw.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(redraw.os, "close", lambda fd: None)

    def _fake_ioctl(fd, op, buf):
        # rows=1, cols=80, xpix=0, ypix=0
        return struct.pack("HHHH", 1, 80, 0, 0)

    monkeypatch.setattr(redraw.fcntl, "ioctl", _fake_ioctl)
    assert redraw.bounce_size("/dev/pts/9") is False


def test_bounce_size_happy_path_writes_two_ioctls(monkeypatch):
    """Successful bounce — verifies the (rows-1) → (rows) sequence."""
    monkeypatch.setattr(redraw.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(redraw.os, "close", lambda fd: None)
    monkeypatch.setattr(redraw.time, "sleep", lambda s: None)

    writes = []

    def _fake_ioctl(fd, op, buf_or_pack):
        from aipager.dtach.redraw import termios
        if op == termios.TIOCGWINSZ:
            return struct.pack("HHHH", 24, 80, 0, 0)
        if op == termios.TIOCSWINSZ:
            writes.append(struct.unpack("HHHH", buf_or_pack))
            return b""
        return b""

    monkeypatch.setattr(redraw.fcntl, "ioctl", _fake_ioctl)
    assert redraw.bounce_size("/dev/pts/9") is True
    assert writes == [(23, 80, 0, 0), (24, 80, 0, 0)]


def test_bounce_size_swallows_ioctl_failure(monkeypatch):
    monkeypatch.setattr(redraw.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(redraw.os, "close", lambda fd: None)

    def _boom(*a, **k):
        raise OSError("EBADF")

    monkeypatch.setattr(redraw.fcntl, "ioctl", _boom)
    assert redraw.bounce_size("/dev/pts/9") is False


# ---- redraw -------------------------------------------------------------

def test_redraw_returns_false_when_no_pty(monkeypatch):
    monkeypatch.setattr(redraw, "find_pty", lambda name: None)
    assert redraw.redraw("jim") is False


def test_redraw_chains_find_and_bounce(monkeypatch):
    monkeypatch.setattr(redraw, "find_pty", lambda name: "/dev/pts/3")
    monkeypatch.setattr(redraw, "bounce_size", lambda p: True)
    assert redraw.redraw("jim") is True


# ---- main entrypoint ----------------------------------------------------

def test_main_usage_error_when_no_arg(monkeypatch, capsys):
    monkeypatch.setattr(redraw.sys, "argv", ["redraw"])
    assert redraw.main() == 1
    assert "Usage" in capsys.readouterr().err


def test_main_success_returns_zero(monkeypatch):
    monkeypatch.setattr(redraw.sys, "argv", ["redraw", "jim"])
    monkeypatch.setattr(redraw, "redraw", lambda name: True)
    assert redraw.main() == 0


def test_main_failure_returns_one(monkeypatch):
    monkeypatch.setattr(redraw.sys, "argv", ["redraw", "jim"])
    monkeypatch.setattr(redraw, "redraw", lambda name: False)
    assert redraw.main() == 1
