"""Tests for `aipager session ls` and `aipager session kill` dispatch."""

from __future__ import annotations

import argparse
import asyncio
import json

from aipager import cli


def _ns(name, claude_args=None):
    return argparse.Namespace(name=name, claude_args=claude_args or [])


# ----- ls dispatch -----

def test_session_ls_routes_through_cmd_session(monkeypatch, capsys):
    captured = {}

    def _fake_gather():
        captured["called"] = True
        return ([], set())

    monkeypatch.setattr("aipager.status._gather_sessions", _fake_gather)
    rc = cli._cmd_session(_ns("ls"))
    assert rc == 0
    assert captured.get("called") is True


def test_session_list_alias(monkeypatch):
    captured = {}
    monkeypatch.setattr("aipager.status._gather_sessions",
                        lambda: (captured.setdefault("called", True) and [], set()))
    rc = cli._cmd_session(_ns("list"))
    assert rc == 0


def test_session_ls_filters_gone_by_default(monkeypatch, capsys):
    monkeypatch.setattr("aipager.status._gather_sessions", lambda: ([
        {"name": "claude-jim", "label": "jim", "status": "IDLE",
         "model": "", "context_pct": None, "cost_usd": None, "queue_depth": 0},
        {"name": "claude-zzz", "label": "zzz", "status": "GONE",
         "model": "", "context_pct": None, "cost_usd": None, "queue_depth": 0},
    ], set()))
    rc = cli._cmd_session(_ns("ls", []))
    assert rc == 0
    out = capsys.readouterr().out
    assert "jim" in out
    assert "zzz" not in out


def test_session_ls_all_includes_gone(monkeypatch, capsys):
    monkeypatch.setattr("aipager.status._gather_sessions", lambda: ([
        {"name": "claude-jim", "label": "jim", "status": "IDLE",
         "model": "", "context_pct": None, "cost_usd": None, "queue_depth": 0},
        {"name": "claude-zzz", "label": "zzz", "status": "GONE",
         "model": "", "context_pct": None, "cost_usd": None, "queue_depth": 0},
    ], set()))
    rc = cli._cmd_session(_ns("ls", ["--all"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "jim" in out
    assert "zzz" in out


def test_session_ls_json(monkeypatch, capsys):
    monkeypatch.setattr("aipager.status._gather_sessions", lambda: ([
        {"name": "claude-jim", "label": "jim", "status": "IDLE",
         "model": "Opus", "context_pct": 5, "cost_usd": 0.10, "queue_depth": 0},
    ], set()))
    rc = cli._cmd_session(_ns("ls", ["--json"]))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions"][0]["label"] == "jim"


# ----- kill dispatch -----

def test_session_kill_requires_target(monkeypatch, capsys):
    rc = cli._cmd_session(_ns("kill", []))
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_session_kill_unknown_session(monkeypatch, tmp_path, capsys):
    # No socket file → friendly error
    monkeypatch.setattr(cli, "asyncio", asyncio)
    # Force the socket-check path to "not found"
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    rc = cli._cmd_session(_ns("kill", ["nonexistent-xyz"]))
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_session_kill_with_force_skips_confirm(monkeypatch, tmp_path, capsys):
    # Socket exists → proceeds without input()
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    called: list = []

    async def _fake_kill(session):
        called.append(session)
        return True

    monkeypatch.setattr("aipager.dtach_inject.kill_session", _fake_kill)
    # No input() should be called when -y is present — sentinel
    monkeypatch.setattr("builtins.input",
                        lambda *_: pytest_fail_called_input())

    rc = cli._cmd_session(_ns("kill", ["jim", "-y"]))
    assert rc == 0
    assert called == ["claude-jim"]
    assert "killed" in capsys.readouterr().out


def test_session_kill_accepts_claude_prefix(monkeypatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    seen = []

    async def _fake_kill(session):
        seen.append(session)
        return True

    monkeypatch.setattr("aipager.dtach_inject.kill_session", _fake_kill)
    rc = cli._cmd_session(_ns("kill", ["claude-jim", "-y"]))
    assert rc == 0
    assert seen == ["claude-jim"]


def test_session_kill_user_cancels(monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    rc = cli._cmd_session(_ns("kill", ["jim"]))
    assert rc == 0  # cancelled cleanly


def pytest_fail_called_input():
    raise AssertionError("input() should not be called when -y is passed")
