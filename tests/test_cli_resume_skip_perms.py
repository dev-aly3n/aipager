"""Tests for cli/resume.py skip_perms handling and ! prefix."""

from __future__ import annotations

import argparse

from unittest.mock import patch

import pytest

from aipager.cli import resume as cli_resume


def _make_gone_session(label: str = "dev", skip_perms: bool = False) -> dict:
    return {
        "name": f"claude-{label}",
        "label": label,
        "claude_session_id": "some-uuid-1234",
        "cwd": "/home/user/project",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "I'm done.",
        "skip_perms": skip_perms,
    }


# ---- ! prefix forces Auto mode ----------------------------------------------

def test_bang_prefix_forces_auto_mode(monkeypatch, tmp_path):
    """aipager resume !dev forces skip_perms=True regardless of persisted value."""
    history = [_make_gone_session("dev", skip_perms=False)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"label": label, "skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        # Simulate the socket not existing
        with patch("pathlib.Path.is_socket", return_value=False):
            rc = cli_resume._resume_one("dev", force_auto=True)

    assert rc == 0
    assert launched.get("skip_perms") is True, (
        f"Expected skip_perms=True with ! prefix, got {launched}"
    )


def test_bang_prefix_in_cmd_resume(monkeypatch):
    """_cmd_resume with !dev sets force_auto=True."""
    called = {}

    def _fake_one(label, *, force_auto=False):
        called["label"] = label
        called["force_auto"] = force_auto
        return 0

    monkeypatch.setattr(cli_resume, "_resume_one", _fake_one)
    args = argparse.Namespace(name="!dev")
    cli_resume._cmd_resume(args)
    assert called["label"] == "dev"
    assert called["force_auto"] is True


# ---- persisted skip_perms=True is passed through ----------------------------

def test_persisted_skip_perms_true_passes_through(monkeypatch):
    """When skip_perms=True is persisted, resume without ! still uses Auto."""
    history = [_make_gone_session("dev", skip_perms=True)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"label": label, "skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        with patch("pathlib.Path.is_socket", return_value=False):
            rc = cli_resume._resume_one("dev", force_auto=False)

    assert rc == 0
    assert launched.get("skip_perms") is True, (
        f"Expected persisted skip_perms=True to be used, got {launched}"
    )


def test_persisted_skip_perms_false_uses_ask(monkeypatch):
    """When skip_perms=False is persisted, resume uses Ask mode."""
    history = [_make_gone_session("dev", skip_perms=False)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"label": label, "skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        with patch("pathlib.Path.is_socket", return_value=False):
            rc = cli_resume._resume_one("dev", force_auto=False)

    assert rc == 0
    assert launched.get("skip_perms") is False


# ---- ! prefix override is independent of persisted value -------------------

def test_bang_prefix_overrides_persisted_false(monkeypatch):
    """! prefix forces Auto even when persisted value is False."""
    history = [_make_gone_session("dev", skip_perms=False)]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        with patch("pathlib.Path.is_socket", return_value=False):
            rc = cli_resume._resume_one("dev", force_auto=True)

    assert rc == 0
    assert launched["skip_perms"] is True


# ---- session not found still returns 1 ------------------------------------

def test_resume_one_not_found_returns_1(monkeypatch):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [])
    with patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("nonexistent")
    assert rc == 1
