"""Tests for `aipager policy validate` (cli/policy.py)."""

from __future__ import annotations

import argparse

from aipager import policy as policy_mod
from aipager import scope as scope_mod
from aipager.cli.policy import cmd_policy_validate


def test_validate_clean_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(scope_mod, "load_scopes", lambda: None)
    monkeypatch.setattr(policy_mod, "validate_policy_files", lambda **k: [])
    rc = cmd_policy_validate(argparse.Namespace())
    assert rc == 0
    assert "policy OK" in capsys.readouterr().out


def test_validate_problems_returns_one(monkeypatch, capsys):
    monkeypatch.setattr(scope_mod, "load_scopes", lambda: None)
    monkeypatch.setattr(policy_mod, "validate_policy_files",
                        lambda **k: ["role 'ghost' undefined"])
    rc = cmd_policy_validate(argparse.Namespace())
    assert rc == 1
    out = capsys.readouterr().out
    assert "failed" in out and "ghost" in out


def test_validate_reports_bad_scopes_file(monkeypatch, capsys):
    def _boom():
        raise scope_mod.ScopeConfigError("schema_version must be 2")
    monkeypatch.setattr(scope_mod, "load_scopes", _boom)
    rc = cmd_policy_validate(argparse.Namespace())
    assert rc == 1
    assert "schema_version" in capsys.readouterr().out
