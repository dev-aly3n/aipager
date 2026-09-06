"""A failed dtach launch reaches chat as a fixed, path-free phrase
(roadmap 8.7).

``launch_session`` used to return ``dtach failed: <raw stderr>`` and
every caller rendered that into a Telegram message — dtach's messages
carry its own binary path, the socket path (session name, ``/tmp``) and,
for an exec failure, the command path with the operator's home
directory. The raw text now stays in the daemon log at WARNING; chat
gets one of a handful of phrases that say what to do.

No real dtach: the subprocess is a mock that exits non-zero with a
recorded stderr, the same seam the other launch tests use.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import inject as dtach_inject

DTACH = "/home/someone/.local/share/pipx/venvs/aipager/bin/dtach"
SOCK = "/tmp/claude-dtach-claude-secretproj.sock"
CMD = "/home/someone/.local/bin/claude"
NAME = "secretproj"

SAMPLES = [
    (f"{DTACH}: {SOCK}: Address already in use",
     "a session socket with this name already exists — kill it or pick another name"),
    (f"{DTACH}: could not execute {CMD}: No such file or directory",
     "dtach could not start the shell (bash missing or not executable?)"),
    (f"{DTACH}: Could not find a pty.",
     "no pseudo-terminal available on this machine"),
    (f"{DTACH}: {SOCK}: File name too long",
     "the session name makes the socket path too long — pick a shorter name"),
    (f"{DTACH}: {SOCK}: Permission denied",
     "no permission to create the session socket"),
    (f"{DTACH}: {SOCK}: No such file or directory",
     "the socket directory does not exist"),
]


def _make_proc(returncode: int, stderr: bytes):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


def _launch_failing(monkeypatch, tmp_path, run_async, *, rc: int, stderr: str):
    async def _fake_exec(*args, **kwargs):
        return _make_proc(rc, stderr.encode())
    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(dtach_inject.Path, "is_socket", lambda self: False)
    return run_async(dtach_inject.launch_session(NAME, cwd=str(tmp_path)))


@pytest.mark.parametrize("stderr,phrase", SAMPLES, ids=[s[1][:24] for s in SAMPLES])
def test_each_known_dtach_failure_maps_to_a_path_free_phrase(
    stderr, phrase, monkeypatch, tmp_path, run_async,
):
    ok, err = _launch_failing(monkeypatch, tmp_path, run_async, rc=1, stderr=stderr)

    assert ok is False
    assert err == f"dtach failed: {phrase}"
    for leak in (DTACH, SOCK, CMD, "/home/", NAME, "/tmp"):
        assert leak not in err, leak


@pytest.mark.parametrize("stderr", ["something odd happened", ""])
def test_unknown_or_empty_stderr_gives_the_generic_phrase_with_the_status(
    stderr, monkeypatch, tmp_path, run_async,
):
    ok, err = _launch_failing(monkeypatch, tmp_path, run_async, rc=2, stderr=stderr)

    assert ok is False
    assert err == "dtach failed: dtach exited with status 2 (see `aipager logs`)"


def test_raw_stderr_is_logged_once_at_warning(monkeypatch, tmp_path, run_async, caplog):
    raw = f"{DTACH}: {SOCK}: Address already in use\nsecond line that is not shown"
    with caplog.at_level(logging.WARNING, logger="aipager.dtach.inject"):
        _launch_failing(monkeypatch, tmp_path, run_async, rc=1, stderr=raw)

    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "dtach launch failed" in r.getMessage()]
    assert len(warnings) == 1
    assert "rc=1" in warnings[0]
    assert SOCK in warnings[0] and "Address already in use" in warnings[0]
    assert "second line" not in warnings[0]


def test_logged_stderr_is_capped(monkeypatch, tmp_path, run_async, caplog):
    raw = "x" * 1000
    with caplog.at_level(logging.WARNING, logger="aipager.dtach.inject"):
        _launch_failing(monkeypatch, tmp_path, run_async, rc=1, stderr=raw)
    msg = next(r.getMessage() for r in caplog.records if "dtach launch failed" in r.getMessage())
    assert msg.count("x") <= 300


# ===== the helper itself =================================================

def test_helper_order_shell_failure_wins_over_missing_file():
    text = f"{DTACH}: could not execute {CMD}: No such file or directory"
    assert dtach_inject._describe_dtach_failure(1, text) == (
        "dtach could not start the shell (bash missing or not executable?)")


def test_helper_is_case_insensitive():
    assert dtach_inject._describe_dtach_failure(1, "X: ADDRESS ALREADY IN USE") == (
        "a session socket with this name already exists — kill it or pick another name")


def test_helper_generic_includes_the_status():
    assert dtach_inject._describe_dtach_failure(7, "") == (
        "dtach exited with status 7 (see `aipager logs`)")


def test_not_installed_and_timeout_branches_are_unchanged(monkeypatch, tmp_path, run_async):
    async def _missing(*args, **kwargs):
        raise FileNotFoundError("dtach")
    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _missing)
    monkeypatch.setattr(dtach_inject.Path, "is_socket", lambda self: False)
    ok, err = run_async(dtach_inject.launch_session(NAME, cwd=str(tmp_path)))
    assert (ok, err) == (False, "dtach not installed")


def test_invalid_utf8_in_stderr_does_not_crash_the_launch(monkeypatch, tmp_path, run_async):
    async def _fake_exec(*args, **kwargs):
        return _make_proc(1, b"\xff\xfe: Address already in use")
    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(dtach_inject.Path, "is_socket", lambda self: False)
    ok, err = run_async(dtach_inject.launch_session(NAME, cwd=str(tmp_path)))
    assert ok is False
    assert err.endswith("kill it or pick another name")


def test_helper_order_shell_failure_wins_over_permission_denied():
    text = f"{DTACH}: could not execute {CMD}: Permission denied"
    assert dtach_inject._describe_dtach_failure(1, text) == (
        "dtach could not start the shell (bash missing or not executable?)")
