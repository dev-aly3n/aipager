"""Tests for aipager.dtach.launcher — name validation, stale socket probe."""

from __future__ import annotations

import socket as _socket
import subprocess

import pytest

from aipager.dtach import launcher as dtach_launcher


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


@pytest.mark.parametrize("name", ["ls", "list", "kill"])
def test_validate_name_rejects_reserved_verbs(name):
    err = dtach_launcher._validate_name(name)
    assert err is not None
    assert "reserved" in err


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


# ----- files Claude sends: the switch and the note ---------------------------

def _spawn_argv(monkeypatch, *, claude_bin="/opt/claude", version="2.1.302",
                claude_args=None):
    from aipager import claude_resolve
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(dtach_launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(dtach_launcher, "_socket_alive", lambda s: False)
    resolved = ResolvedClaude(chosen=ClaudeInstall(path="/opt/claude",
                                                   realpath="/opt/claude",
                                                   version=version))
    monkeypatch.setattr(claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: resolved)
    seen = {}

    class _Fake:
        returncode = 1
        stderr = "stop here\n"
        stdout = ""

    def _run(argv, *a, **k):
        seen["argv"] = argv
        return _Fake()
    monkeypatch.setattr(dtach_launcher.subprocess, "run", _run)
    assert dtach_launcher.launch("dev", claude_args, claude_bin=claude_bin) == 1
    return seen["argv"]


def test_terminal_session_sets_attachments_by_path(monkeypatch):
    argv = _spawn_argv(monkeypatch)
    assert "CLAUDE_CODE_ATTACHMENTS_BY_PATH=1" in argv
    env_at = argv.index("env")
    assert env_at < argv.index("CLAUDE_CODE_ATTACHMENTS_BY_PATH=1") < argv.index("/opt/claude")


def test_terminal_prompt_carries_the_telegram_note(monkeypatch):
    from aipager.claude_launch import TELEGRAM_NOTE
    argv = _spawn_argv(monkeypatch)
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert prompt.startswith('Your session name is "dev". ')
    assert prompt.endswith("\n\n" + TELEGRAM_NOTE)


def test_terminal_prompt_has_the_no_send_note_for_an_old_claude(monkeypatch):
    from aipager.claude_launch import TELEGRAM_NOTE, TELEGRAM_NOTE_NO_SEND
    argv = _spawn_argv(monkeypatch, version="2.1.300")
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert TELEGRAM_NOTE not in prompt
    assert prompt.endswith("\n\n" + TELEGRAM_NOTE_NO_SEND)
    # Another binary than the one resolved: its version is unknown.
    argv = _spawn_argv(monkeypatch, claude_bin="/elsewhere/claude", version="2.1.300")
    assert TELEGRAM_NOTE in argv[argv.index("--append-system-prompt") + 1]


# ----- the recorded system prompt is not replayed (8.125) --------------------

def _claude_argv(argv):
    return argv[argv.index("/opt/claude") + 1:]


@pytest.mark.parametrize("claude_args", [None, ["--resume", "abc"]], ids=["fresh", "resume"])
def test_terminal_session_turns_the_prompt_snapshot_off(monkeypatch, claude_args):
    monkeypatch.setattr(dtach_launcher, "_prepare_resume_args", lambda a: (a, 0))
    args = _claude_argv(_spawn_argv(monkeypatch, version="2.1.310", claude_args=claude_args))
    at = args.index("--system-prompt-snapshot")
    assert args[at + 1] == "off"
    assert args.count("--system-prompt-snapshot") == 1
    if claude_args:
        assert args[args.index("--resume") + 1] == "abc"


def test_terminal_session_never_passes_the_flag_to_a_claude_that_may_refuse_it(monkeypatch):
    assert "--system-prompt-snapshot" not in _claude_argv(
        _spawn_argv(monkeypatch, version="2.1.258"))
    # Another binary than the one resolved: its version is unknown.
    argv = _spawn_argv(monkeypatch, claude_bin="/elsewhere/claude", version="2.1.310")
    assert "--system-prompt-snapshot" not in argv


def test_terminal_session_flag_words_after_the_double_dash_are_not_the_users_choice(
        monkeypatch):
    """After ``--`` an argument is the prompt, not a flag: aipager's own
    "off" stays."""
    args = _claude_argv(_spawn_argv(monkeypatch, version="2.1.310",
                                    claude_args=["--", "explain --system-prompt-snapshot"]))
    at = args.index("--system-prompt-snapshot")
    assert args[at + 1] == "off"
    args = _claude_argv(_spawn_argv(monkeypatch, version="2.1.310",
                                    claude_args=["--", "--system-prompt-snapshot"]))
    assert args[args.index("--system-prompt-snapshot") + 1] == "off"


@pytest.mark.parametrize("own", [["--system-prompt-snapshot", "on"],
                                 ["--system-prompt-snapshot=on"]], ids=["spaced", "equals"])
def test_terminal_session_keeps_the_users_own_snapshot_choice(monkeypatch, own):
    args = _claude_argv(_spawn_argv(monkeypatch, version="2.1.310", claude_args=own))
    assert [a for a in args if a.startswith("--system-prompt-snapshot")] == own[:1]
    assert "off" not in args
