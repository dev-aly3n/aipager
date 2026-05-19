"""Tests for aipager.cli.__init__ — the argparse dispatcher and thin
``_cmd_*`` delegators.

We don't test the daemon-boot path here (that's in test_cli_daemon.py /
needs subprocess scaffolding); just the wiring of subcommands and the
help / version branches.
"""

from __future__ import annotations

import argparse
import sys


from aipager import cli


def _run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    try:
        cli.main()
    except SystemExit as e:
        return e.code
    return 0


# ---- thin dispatcher wrappers --------------------------------------------

def test_cmd_version_prints_version(monkeypatch, capsys):
    rc = cli._cmd_version(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip()  # some version string


def test_cmd_config_delegates_to_wizard(monkeypatch):
    called = {"n": 0}
    def _fake_run():
        called["n"] += 1
        return 42
    monkeypatch.setattr("aipager.wizard.run", _fake_run)
    rc = cli._cmd_config(argparse.Namespace())
    assert rc == 42
    assert called["n"] == 1


def test_cmd_doctor_delegates(monkeypatch):
    called = {"n": 0}
    def _fake_doctor(args):
        called["n"] += 1
        return 7
    monkeypatch.setattr("aipager.doctor.cmd_doctor", _fake_doctor)
    rc = cli._cmd_doctor(argparse.Namespace())
    assert rc == 7
    assert called["n"] == 1


def test_cmd_status_delegates(monkeypatch):
    monkeypatch.setattr("aipager.status.cmd_status", lambda args: 3)
    assert cli._cmd_status(argparse.Namespace()) == 3


def test_cmd_logs_delegates(monkeypatch):
    captured = {}
    def _fake(*, follow, lines):
        captured["follow"] = follow
        captured["lines"] = lines
        return 5
    monkeypatch.setattr("aipager.service.cmd_logs", _fake)
    rc = cli._cmd_logs(argparse.Namespace(follow=True, lines=50))
    assert rc == 5
    assert captured["follow"] is True
    assert captured["lines"] == 50


def test_cmd_update_delegates(monkeypatch):
    monkeypatch.setattr("aipager.updater.cmd_update", lambda args: 9)
    assert cli._cmd_update(argparse.Namespace()) == 9


def test_cmd_uninstall_delegates(monkeypatch):
    monkeypatch.setattr("aipager.updater.cmd_uninstall", lambda args: 11)
    assert cli._cmd_uninstall(argparse.Namespace()) == 11


def test_cmd_service_delegates(monkeypatch):
    monkeypatch.setattr("aipager.service.cmd_service", lambda args: 13)
    assert cli._cmd_service(argparse.Namespace()) == 13


# ---- main() argparse routing --------------------------------------------

def test_main_with_no_command_prints_help_and_exits(monkeypatch, capsys):
    rc = _run_main(["aipager"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "aipager" in out
    assert "subcommand" in out.lower() or "usage" in out.lower()


def test_main_version_flag_exits(monkeypatch, capsys):
    """`aipager --version` triggers argparse's built-in --version action."""
    rc = _run_main(["aipager", "--version"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "aipager" in out


def test_main_help_subcommand_no_topic_prints_root(monkeypatch, capsys):
    rc = _run_main(["aipager", "help"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "subcommand" in out.lower() or "usage" in out.lower()


def test_main_help_with_known_topic_prints_subhelp(monkeypatch, capsys):
    rc = _run_main(["aipager", "help", "status"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    # the `status` subparser had --json flag — should appear in help text
    assert "json" in out.lower() or "status" in out


def test_main_help_with_unknown_topic_errors_with_code_2(monkeypatch, capsys):
    rc = _run_main(["aipager", "help", "totally-bogus"], monkeypatch)
    assert rc == 2


def test_main_service_without_subcommand_prints_help(monkeypatch, capsys):
    rc = _run_main(["aipager", "service"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "install" in out  # service install subcommand should appear


def test_main_version_subcommand(monkeypatch, capsys):
    rc = _run_main(["aipager", "version"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip()  # printed something


def test_main_doctor_dispatches(monkeypatch, capsys):
    called = []
    def _fake(args):
        called.append(args)
        return 0
    monkeypatch.setattr("aipager.doctor.cmd_doctor", _fake)
    rc = _run_main(["aipager", "doctor"], monkeypatch)
    assert rc == 0
    assert len(called) == 1


def test_main_status_with_json_flag(monkeypatch, capsys):
    captured = {}
    def _fake(args):
        captured["as_json"] = args.as_json
        return 0
    monkeypatch.setattr("aipager.status.cmd_status", _fake)
    rc = _run_main(["aipager", "status", "--json"], monkeypatch)
    assert rc == 0
    assert captured["as_json"] is True


def test_main_logs_with_flags(monkeypatch):
    captured = {}
    def _fake(*, follow, lines):
        captured["follow"] = follow
        captured["lines"] = lines
        return 0
    monkeypatch.setattr("aipager.service.cmd_logs", _fake)
    rc = _run_main(["aipager", "logs", "-f", "-n", "42"], monkeypatch)
    assert rc == 0
    assert captured["follow"] is True
    assert captured["lines"] == 42


def test_main_resume_no_arg_calls_picker(monkeypatch):
    monkeypatch.setattr("aipager.cli.resume._resume_picker_loop", lambda: 0)
    rc = _run_main(["aipager", "resume"], monkeypatch)
    assert rc == 0


def test_main_resume_with_name(monkeypatch):
    captured = {}
    def _fake_one(label):
        captured["label"] = label
        return 0
    monkeypatch.setattr("aipager.cli.resume._resume_one", _fake_one)
    rc = _run_main(["aipager", "resume", "jim"], monkeypatch)
    assert rc == 0
    assert captured["label"] == "jim"


def test_main_uninstall_with_yes_flag(monkeypatch):
    captured = {}
    def _fake(args):
        captured["force"] = args.force
        return 0
    monkeypatch.setattr("aipager.updater.cmd_uninstall", _fake)
    rc = _run_main(["aipager", "uninstall", "-y"], monkeypatch)
    assert rc == 0
    assert captured["force"] is True


def test_main_session_ls(monkeypatch):
    monkeypatch.setattr("aipager.status._gather_sessions",
                        lambda: ([], set()))
    rc = _run_main(["aipager", "session", "ls"], monkeypatch)
    assert rc == 0
