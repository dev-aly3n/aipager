"""Black-box tests for `aipager miniapp enable/disable/status`, driven
through the real console-script entrypoint (`aipager.cli.main`, invoked
via `sys.argv` exactly as a user would type it) rather than the private
`_cmd_miniapp_enable` / `_cmd_miniapp_disable` / `_cmd_miniapp_status`
functions that entrypoints.md lists as internal (NOT exported).

tests/test_miniapp_cli.py (developer, white-box) already exercises the
private functions directly and reads config.env off disk to check the
result. This file's value-add: (1) drives the *documented* CLI syntax
end-to-end so a bug in argv wiring (e.g. `--port` not reaching the
underlying command) would be caught, and (2) covers the printed restart
reminder, which entrypoints.md promises ("Prints a restart reminder")
but no existing test asserts on stdout content for.
"""

from __future__ import annotations

import sys

import pytest

from aipager.cli import main


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["aipager", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


def test_enable_exits_zero_and_prints_restart_reminder(
    monkeypatch, capsys, _isolate_home_paths,
):
    code = _run_cli(monkeypatch, ["miniapp", "enable"])
    out = capsys.readouterr().out.lower()
    assert code == 0
    assert "restart" in out


def test_disable_exits_zero_and_prints_restart_reminder(
    monkeypatch, capsys, _isolate_home_paths,
):
    _run_cli(monkeypatch, ["miniapp", "enable"])
    capsys.readouterr()
    code = _run_cli(monkeypatch, ["miniapp", "disable"])
    out = capsys.readouterr().out.lower()
    assert code == 0
    assert "restart" in out


def test_status_exits_zero_before_any_enable(monkeypatch, capsys, _isolate_home_paths):
    """entrypoints.md: status 'does not require the daemon to be
    running' -- and, by extension, does not require the feature to have
    ever been enabled."""
    code = _run_cli(monkeypatch, ["miniapp", "status"])
    assert code == 0


def test_default_port_visible_via_status_after_bare_enable(
    monkeypatch, capsys, _isolate_home_paths,
):
    """entrypoints.md: '--port defaults to 8765 if omitted on first
    enable.'"""
    _run_cli(monkeypatch, ["miniapp", "enable"])
    capsys.readouterr()
    _run_cli(monkeypatch, ["miniapp", "status"])
    out = capsys.readouterr().out
    assert "8765" in out


def test_explicit_port_and_url_visible_via_status(
    monkeypatch, capsys, _isolate_home_paths,
):
    _run_cli(
        monkeypatch,
        ["miniapp", "enable", "--port", "9999", "--url", "https://example.ts.net/"],
    )
    capsys.readouterr()
    _run_cli(monkeypatch, ["miniapp", "status"])
    out = capsys.readouterr().out
    assert "9999" in out
    assert "https://example.ts.net/" in out


def test_enable_without_port_reuses_previously_configured_port(
    monkeypatch, capsys, _isolate_home_paths,
):
    """entrypoints.md: '--port defaults to 8765 ... on first enable'
    implies re-enabling without --port keeps whatever was configured, not
    a silent reset to the default. Driven end-to-end through argv so a
    bug in how the parser's `type=int, default=None` interacts with the
    underlying enable command would surface here."""
    _run_cli(monkeypatch, ["miniapp", "enable", "--port", "5555"])
    capsys.readouterr()
    _run_cli(monkeypatch, ["miniapp", "disable"])
    capsys.readouterr()
    _run_cli(monkeypatch, ["miniapp", "enable"])
    out = capsys.readouterr().out
    assert "5555" in out


def test_enable_rejects_non_https_url(monkeypatch, capsys, _isolate_home_paths):
    code = _run_cli(monkeypatch, ["miniapp", "enable", "--url", "http://insecure.example/"])
    assert code != 0


def test_rejected_enable_does_not_block_a_subsequent_valid_enable(
    monkeypatch, capsys, _isolate_home_paths,
):
    """A failed `enable --url http://...` must not corrupt on-disk state
    such that a later, valid `enable` call also fails."""
    code1 = _run_cli(monkeypatch, ["miniapp", "enable", "--url", "http://insecure.example/"])
    assert code1 != 0
    capsys.readouterr()
    code2 = _run_cli(monkeypatch, ["miniapp", "enable", "--port", "8765"])
    assert code2 == 0


def test_bare_miniapp_with_no_subcommand_does_not_crash(
    monkeypatch, capsys, _isolate_home_paths,
):
    """`aipager miniapp` with no enable/disable/status must print help,
    not raise -- error-guessing for a likely operator typo."""
    code = _run_cli(monkeypatch, ["miniapp"])
    assert code == 0
