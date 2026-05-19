"""Tests for aipager.cli.resume — `aipager resume [<name>]`.

Covers the picker loop, the direct-resume flow, the `_gone_history`
loader, and the `_fmt_ago_cli` helper.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from aipager.cli import resume as cli_resume


# ---- _fmt_ago_cli --------------------------------------------------------

def test_fmt_ago_seconds():
    assert "s ago" in cli_resume._fmt_ago_cli(time.time() - 30)


def test_fmt_ago_minutes():
    assert "m ago" in cli_resume._fmt_ago_cli(time.time() - 600)


def test_fmt_ago_hours():
    assert "h ago" in cli_resume._fmt_ago_cli(time.time() - 7200)


def test_fmt_ago_days():
    assert "d ago" in cli_resume._fmt_ago_cli(time.time() - 200000)


def test_fmt_ago_none_returns_question():
    assert cli_resume._fmt_ago_cli(None) == "?"


def test_fmt_ago_zero_returns_question():
    assert cli_resume._fmt_ago_cli(0) == "?"


def test_fmt_ago_non_numeric_returns_question():
    assert cli_resume._fmt_ago_cli("not a number") == "?"


# ---- _gone_history -------------------------------------------------------

def test_gone_history_excludes_live(monkeypatch):
    monkeypatch.setattr("aipager.status._read_state",
                        lambda: {"sessions": {
                            "claude-jim": {"name": "claude-jim",
                                           "gone_at": 100.0,
                                           "claude_session_id": "X"},
                            "claude-alive": {"name": "claude-alive",
                                              "gone_at": 200.0,
                                              "claude_session_id": "Y"},
                        }})
    monkeypatch.setattr("aipager.status._live_sessions",
                        lambda: {"claude-alive"})  # alive set
    result = cli_resume._gone_history()
    names = [s["name"] for s in result]
    assert names == ["claude-jim"]


def test_gone_history_skips_entries_without_resume_data(monkeypatch):
    """Sessions with no gone_at AND no claude_session_id are skipped."""
    monkeypatch.setattr("aipager.status._read_state",
                        lambda: {"sessions": {
                            "claude-novalue": {"name": "claude-novalue"},
                            "claude-ok": {"name": "claude-ok",
                                          "gone_at": 100.0,
                                          "claude_session_id": "X"},
                        }})
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    result = cli_resume._gone_history()
    assert [s["name"] for s in result] == ["claude-ok"]


def test_gone_history_sorts_newest_first(monkeypatch):
    monkeypatch.setattr("aipager.status._read_state",
                        lambda: {"sessions": {
                            "claude-old": {"name": "claude-old",
                                           "gone_at": 100.0,
                                           "claude_session_id": "X"},
                            "claude-new": {"name": "claude-new",
                                           "gone_at": 999.0,
                                           "claude_session_id": "Y"},
                        }})
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    result = cli_resume._gone_history()
    assert result[0]["name"] == "claude-new"


def test_gone_history_empty_state(monkeypatch):
    monkeypatch.setattr("aipager.status._read_state", lambda: {})
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    assert cli_resume._gone_history() == []


# ---- _resume_one ---------------------------------------------------------

def test_resume_one_with_alive_socket_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        "aipager.cli.resume.Path.is_socket"
        if hasattr(cli_resume, "Path") else "pathlib.Path.is_socket",
        lambda self: True,
    )
    rc = cli_resume._resume_one("jim")
    assert rc == 1
    err = capsys.readouterr().err
    assert "already running" in err


def test_resume_one_with_no_history_errors(monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [])
    rc = cli_resume._resume_one("jim")
    assert rc == 1
    err = capsys.readouterr().err
    assert "no session named" in err.lower()


def test_resume_one_missing_claude_session_id_errors(monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "claude_session_id": "",
        "gone_at": 100.0,
    }])
    rc = cli_resume._resume_one("jim")
    assert rc == 1
    err = capsys.readouterr().err
    assert "no resumable transcript" in err


def test_resume_one_launch_failure_errors(monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "claude_session_id": "UUID-1",
        "cwd": "/x", "gone_at": 100.0,
    }])
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))
    rc = cli_resume._resume_one("jim")
    assert rc == 1
    err = capsys.readouterr().err
    assert "dtach broken" in err


def test_resume_one_happy_path(monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "claude_session_id": "UUID-1",
        "cwd": "/x", "gone_at": 100.0,
        "last_assistant_preview": "what I did",
    }])
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    rc = cli_resume._resume_one("jim")
    assert rc == 0
    out = capsys.readouterr().out
    assert "resumed" in out.lower()
    assert "Attach with:" in out


# ---- _cmd_resume dispatch ------------------------------------------------

def test_cmd_resume_no_arg_calls_picker(monkeypatch):
    called = {}
    monkeypatch.setattr(cli_resume, "_resume_picker_loop",
                        lambda: (called.setdefault("picker", True), 0)[1])
    args = argparse.Namespace(name=None)
    rc = cli_resume._cmd_resume(args)
    assert rc == 0
    assert called["picker"] is True


def test_cmd_resume_with_name_calls_resume_one(monkeypatch):
    called = {}
    monkeypatch.setattr(cli_resume, "_resume_one",
                        lambda label: (called.setdefault("label", label), 0)[1])
    args = argparse.Namespace(name="jim")
    rc = cli_resume._cmd_resume(args)
    assert called["label"] == "jim"


def test_cmd_resume_strips_at_and_slash(monkeypatch):
    received = {}
    monkeypatch.setattr(cli_resume, "_resume_one",
                        lambda label: (received.setdefault("label", label), 0)[1])
    args = argparse.Namespace(name="@JIM")
    cli_resume._cmd_resume(args)
    assert received["label"] == "jim"


# ---- _resume_picker_loop -------------------------------------------------

def test_picker_with_empty_history_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [])
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_quit_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "label": "jim",
        "claude_session_id": "X", "gone_at": 100.0,
    }])
    monkeypatch.setattr("builtins.input", lambda *a: "q")
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_eof_returns_zero(monkeypatch):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "label": "jim",
        "claude_session_id": "X", "gone_at": 100.0,
    }])
    def _raise(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", _raise)
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_selecting_number_invokes_resume_one(monkeypatch):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "label": "jim",
        "claude_session_id": "X", "gone_at": 100.0,
    }])
    called = {}
    monkeypatch.setattr(cli_resume, "_resume_one",
                        lambda label: (called.setdefault("label", label), 0)[1])
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    cli_resume._resume_picker_loop()
    assert called["label"] == "jim"


def test_picker_pagination_next_then_quit(monkeypatch, capsys):
    # 15 entries → 2 pages of 10/5
    sessions = [{
        "name": f"claude-old{i:02d}", "label": f"old{i:02d}",
        "claude_session_id": "X", "gone_at": 1000.0 - i,
    } for i in range(15)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: sessions)
    calls = iter(["n", "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(calls))
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_pagination_prev_then_quit(monkeypatch):
    sessions = [{
        "name": f"claude-old{i:02d}", "label": f"old{i:02d}",
        "claude_session_id": "X", "gone_at": 1000.0 - i,
    } for i in range(15)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: sessions)
    # next → page 1, prev → page 0, quit
    calls = iter(["n", "p", "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(calls))
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_unrecognized_input_keeps_looping(monkeypatch):
    sessions = [{
        "name": "claude-jim", "label": "jim",
        "claude_session_id": "X", "gone_at": 1000.0,
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: sessions)
    calls = iter(["xyzzy", "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(calls))
    rc = cli_resume._resume_picker_loop()
    assert rc == 0


def test_picker_out_of_range_number_keeps_looping(monkeypatch):
    sessions = [{
        "name": "claude-jim", "label": "jim",
        "claude_session_id": "X", "gone_at": 1000.0,
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: sessions)
    # 99 is out of range, then q
    calls = iter(["99", "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(calls))
    rc = cli_resume._resume_picker_loop()
    assert rc == 0
