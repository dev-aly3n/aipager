"""`aipager resume`: the saved permission mode, old files' ``skip_perms``,
and the ``!`` prefix (Full access)."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from aipager.cli import resume as cli_resume


def _make_gone_session(label: str = "dev", **mode) -> dict:
    return {
        "name": f"claude-{label}",
        "label": label,
        "claude_session_id": "some-uuid-1234",
        "cwd": "/home/user/project",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "I'm done.",
        **mode,
    }


def _resume(monkeypatch, record, *, force_full=False):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [record])
    launched = {}

    async def mock_launch(label, *, mode, resume_id=None, cwd=None, **kw):
        launched.update({"label": label, "mode": mode, "resume_id": resume_id})
        return True, ""

    printed = []
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
            patch("pathlib.Path.is_socket", return_value=False), \
            patch("aipager.ui.ok", side_effect=printed.append):
        rc = cli_resume._resume_one("dev", force_full=force_full)
    assert rc == 0
    return launched, printed


@pytest.mark.parametrize("record, want", [
    ({"perm_mode": "ask"}, "ask"),
    ({"perm_mode": "auto"}, "auto"),
    ({"perm_mode": "full"}, "full"),
    ({"skip_perms": True}, "full"),
    ({"skip_perms": False}, "ask"),
    ({}, "ask"),
    ({"perm_mode": "auto", "skip_perms": False}, "auto"),
    ({"perm_mode": "bogus", "skip_perms": True}, "full"),
], ids=["ask", "auto", "full", "legacy-true", "legacy-false", "missing", "new-key-wins",
        "invalid-new-key-falls-back"])
def test_the_saved_mode_is_what_resume_launches(monkeypatch, record, want):
    launched, printed = _resume(monkeypatch, _make_gone_session(**record))
    assert launched["mode"] == want
    flag = {"ask": "--permission-mode default", "auto": "--permission-mode auto",
            "full": "--dangerously-skip-permissions"}[want]
    name = {"ask": "Ask", "auto": "Auto", "full": "Full access"}[want]
    assert f"mode={name} ({flag})" in printed[0]


@pytest.mark.parametrize("record", [{"perm_mode": "ask"}, {"perm_mode": "auto"}, {}],
                         ids=["ask", "auto", "missing"])
def test_bang_prefix_forces_full_access(monkeypatch, record):
    launched, _printed = _resume(monkeypatch, _make_gone_session(**record), force_full=True)
    assert launched["mode"] == "full"


def test_bang_prefix_in_cmd_resume(monkeypatch):
    """_cmd_resume with !dev sets force_full=True."""
    called = {}

    def _fake_one(label, *, force_full=False):
        called["label"] = label
        called["force_full"] = force_full
        return 0

    monkeypatch.setattr(cli_resume, "_resume_one", _fake_one)
    cli_resume._cmd_resume(argparse.Namespace(name="!dev"))
    assert called == {"label": "dev", "force_full": True}
    cli_resume._cmd_resume(argparse.Namespace(name="dev"))
    assert called == {"label": "dev", "force_full": False}


def test_resume_one_not_found_returns_1(monkeypatch):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [])
    with patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("nonexistent")
    assert rc == 1
