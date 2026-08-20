"""`aipager config` in a pipe is a usage mistake, not a crash.

It used to print `Warning: Input is not a terminal (fd=0)`, then raise a
bare `EOFError`, then render that as "aipager hit an unexpected error"
with a link inviting the user to file a bug — against their own shell
redirect. Anyone piping the command, scripting it, or running it in CI
got that.

Two layers are tested here: the guard that fires before a prompt, and
the top-level handler that catches an EOFError from any prompt site that
forgets the guard.
"""
from __future__ import annotations

import io
import sys

import pytest

from aipager import errors

from tests.conftest import REAL_REQUIRE_INTERACTIVE


class _NoTty(io.StringIO):
    def isatty(self) -> bool:
        return False


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


# ---- the guard ----------------------------------------------------------

def test_exits_two_without_a_terminal(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aipager", "config"])
    monkeypatch.setattr(sys, "stdin", _NoTty())
    with pytest.raises(SystemExit) as exc:
        REAL_REQUIRE_INTERACTIVE()
    assert exc.value.code == 2, "scripts need a non-zero code to detect this"
    err = capsys.readouterr().err
    assert "aipager config" in err, f"must name what the user typed: {err!r}"
    assert "terminal" in err.lower()
    assert "issues" not in err.lower(), "still inviting a bug report"
    assert "Traceback" not in err


def test_returns_silently_with_a_terminal(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())
    REAL_REQUIRE_INTERACTIVE()                      # must not raise
    assert capsys.readouterr().err == ""


def test_names_the_subcommand_actually_typed(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aipager", "doctor", "--fix"])
    monkeypatch.setattr(sys, "stdin", _NoTty())
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE()
    assert "aipager doctor" in capsys.readouterr().err


def test_ignores_a_leading_flag_when_naming_the_command(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aipager", "--version"])
    monkeypatch.setattr(sys, "stdin", _NoTty())
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE()
    err = capsys.readouterr().err
    assert "--version" not in err, "a flag is not a command name"
    assert "aipager" in err


def test_names_two_word_subcommands_in_full(capsys, monkeypatch):
    """`aipager service` is not a command anyone can run; naming only the
    first word sends the reader somewhere that does not exist."""
    monkeypatch.setattr(sys, "argv", ["aipager", "service", "install"])
    monkeypatch.setattr(sys, "stdin", _NoTty())
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE()
    assert "aipager service install" in capsys.readouterr().err


def test_an_explicit_command_name_wins(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aipager"])
    monkeypatch.setattr(sys, "stdin", _NoTty())
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE("aipager service install")
    assert "aipager service install" in capsys.readouterr().err


def test_a_stdin_without_isatty_is_not_a_terminal(capsys, monkeypatch):
    """Never guess "interactive" from a stdin substitute we don't
    understand — guessing wrong hangs forever on a read that never
    returns."""
    monkeypatch.setattr(sys, "stdin", object())
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE()


def test_no_stdin_at_all_is_not_a_terminal(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(SystemExit):
        REAL_REQUIRE_INTERACTIVE()


# ---- the catch-all ------------------------------------------------------

def test_eoferror_is_not_reported_as_an_internal_error(capsys, monkeypatch):
    """Belt and braces for a prompt site that forgets the guard."""
    monkeypatch.setattr(sys, "argv", ["aipager", "config"])
    original = sys.excepthook
    try:
        errors.install_excepthook()
        sys.excepthook(EOFError, EOFError(), None)
    finally:
        sys.excepthook = original
    err = capsys.readouterr().err
    assert "unexpected error" not in err.lower()
    assert "issues" not in err.lower()
    assert "terminal" in err.lower()
    assert "aipager config" in err


def test_other_exceptions_still_get_the_bug_report_path(capsys):
    original = sys.excepthook
    try:
        errors.install_excepthook()
        sys.excepthook(ValueError, ValueError("boom"), None)
    finally:
        sys.excepthook = original
    err = capsys.readouterr().err
    assert "unexpected error" in err.lower()
    assert "issues" in err.lower(), "a real defect must still invite a report"
    assert "boom" in err


# ---- is the guard actually WIRED IN? ------------------------------------
#
# Everything above proves require_interactive() behaves correctly when
# called. None of it proves anything calls it. A guard that exists and is
# invoked from nowhere passes its own unit tests perfectly.

def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(errors, "require_interactive",
                        lambda command=None: calls.append(command))
    return calls


def test_the_wizard_checks_before_prompting(monkeypatch):
    from unittest.mock import MagicMock

    from aipager.wizard import display

    calls = _spy(monkeypatch)
    prompt = MagicMock()
    prompt.ask.return_value = "something"
    display._ask(prompt)
    assert calls, "the wizard prompted without checking for a terminal"


def test_uninstall_checks_before_its_confirmation(monkeypatch):
    import argparse

    from aipager import updater

    calls = _spy(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    updater.cmd_uninstall(argparse.Namespace(force=False))
    assert calls, "uninstall prompted without checking for a terminal"


def test_uninstall_with_yes_never_checks(monkeypatch):
    """`-y` is the scripted path — requiring a TTY there would break
    exactly the automation the flag exists for."""
    import argparse

    from aipager import updater

    calls = _spy(monkeypatch)
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_path", lambda p: False)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    updater.cmd_uninstall(argparse.Namespace(force=True))
    assert calls == [], "a headless uninstall -y demanded a terminal"


def test_doctor_fix_checks_before_prompting(monkeypatch):
    """The other two guarded sites, which had no wiring test."""
    from aipager import doctor

    calls = _spy(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    monkeypatch.setattr(doctor, "ensure_daemon_env", lambda: None, raising=False)
    try:
        doctor._fix_daemon_credential()
    except Exception:
        pass
    assert calls, "doctor --fix prompted without checking for a terminal"


def test_service_install_checks_before_its_overwrite_prompt(monkeypatch, tmp_path):
    from aipager import service

    calls = _spy(monkeypatch)
    unit = tmp_path / "aipager.service"
    unit.write_text("[Unit]\nDescription=something else\n")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit)
    monkeypatch.setattr(service, "_systemd_user_available", lambda: (True, "ok"))
    monkeypatch.setattr(service, "_resolve_aipager_bin", lambda: "/usr/bin/aipager")
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    service._install_linux(yes=False)

    assert calls, "service install prompted without checking for a terminal"


def _kill_harness(monkeypatch, tmp_path):
    """Make `_session_kill`'s socket-exists check pass without touching /tmp.

    It does `from pathlib import Path` *inside* the function and builds
    `/tmp/claude-dtach-<name>.sock` directly, so there is no module
    attribute to redirect. Patching `Path.exists` for the one filename
    this test uses is narrower than patching Path itself, and avoids
    creating a real `/tmp/claude-dtach-*.sock` — conftest's
    `_guard_live_sockets` watches that pattern precisely because the
    suite once unlinked a live session's socket.
    """
    import pathlib

    from aipager.cli import session as session_cli

    real_exists = pathlib.Path.exists

    def _exists(self):
        if str(self) == "/tmp/claude-dtach-victim.sock":
            return True
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", _exists)
    return session_cli


def test_session_kill_checks_before_its_confirmation(monkeypatch, tmp_path):
    """The site I missed entirely on the first pass — found in review.

    My grep did match it; I truncated the output with `head -20` and cut
    it off, then treated the list as complete.
    """
    import argparse

    session_cli = _kill_harness(monkeypatch, tmp_path)
    calls = _spy(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    rc = session_cli._session_kill(argparse.Namespace(claude_args=["victim"]))

    assert calls, "session kill prompted without checking for a terminal"
    assert rc == 0, "declining the prompt should be a clean no-op"


def test_session_kill_with_yes_never_checks(monkeypatch, tmp_path):
    """`-y` is the scripted path, same as uninstall."""
    import argparse

    session_cli = _kill_harness(monkeypatch, tmp_path)
    calls = _spy(monkeypatch)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        lambda s: _done(True))

    session_cli._session_kill(argparse.Namespace(claude_args=["victim", "-y"]))

    assert calls == [], "a headless `session kill -y` demanded a terminal"


async def _done(value):
    return value


def test_service_install_with_yes_never_checks(monkeypatch, tmp_path):
    """`service install --yes` is how the daemon gets installed from a
    script. Added after a mutation proved the gap: moving the guard from
    the prompt to command entry broke this headless path and NO test
    noticed, because every other test runs with the guard stubbed out.
    """
    from aipager import service

    calls = _spy(monkeypatch)
    unit = tmp_path / "aipager.service"
    unit.write_text("[Unit]\nDescription=something else\n")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit)
    monkeypatch.setattr(service, "_systemd_user_available", lambda: (True, "ok"))
    monkeypatch.setattr(service, "_resolve_aipager_bin", lambda: "/usr/bin/aipager")
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_check_linger", lambda: None)
    monkeypatch.setattr(service, "_post_install_probe", lambda: None)

    service._install_linux(yes=True)

    assert calls == [], "a scripted `service install --yes` demanded a terminal"


def test_doctor_without_fix_never_checks(monkeypatch):
    """`aipager doctor` is the command users are told to run and paste,
    frequently through a pipe. It must never require a terminal."""
    from aipager import doctor

    calls = _spy(monkeypatch)
    doctor.run_all()
    assert calls == [], "plain `aipager doctor` demanded a terminal"
