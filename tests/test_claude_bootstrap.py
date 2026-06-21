"""Tests for aipager.claude_bootstrap — first-run state-file patches."""

from __future__ import annotations

import json
from pathlib import Path

from aipager import claude_bootstrap


def test_writes_bypass_flag_to_empty_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    claude_json = tmp_path / ".claude.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert data["skipDangerousModePermissionPrompt"] is True


def test_preserves_existing_settings_keys(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark", "hooks": {"PreToolUse": []}}))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert data["theme"] == "dark"
    assert data["hooks"] == {"PreToolUse": []}
    assert data["skipDangerousModePermissionPrompt"] is True


def test_trusts_workdir_in_claude_json(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    claude_json = tmp_path / ".claude.json"
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(claude_json.read_text())
    assert data["projects"]["/workspace"]["hasTrustDialogAccepted"] is True


def test_idempotent_on_already_set_flags(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    claude_json = tmp_path / ".claude.json"
    settings.write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
    claude_json.write_text(json.dumps({
        "projects": {"/workspace": {
            "allowedTools": [],
            "mcpContextUris": [],
            "mcpServers": {},
            "hasTrustDialogAccepted": True,
        }}
    }))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    mtime_settings = settings.stat().st_mtime_ns
    mtime_claude = claude_json.stat().st_mtime_ns
    # call twice — neither file should be rewritten
    claude_bootstrap.bootstrap_claude_settings("/workspace")
    claude_bootstrap.bootstrap_claude_settings("/workspace")
    assert settings.stat().st_mtime_ns == mtime_settings
    assert claude_json.stat().st_mtime_ns == mtime_claude


def test_does_not_clobber_other_projects(tmp_path, monkeypatch):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "projects": {
            "/other": {"hasTrustDialogAccepted": False, "myKey": "preserve me"},
        }
    }))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(claude_json.read_text())
    assert data["projects"]["/other"]["myKey"] == "preserve me"
    assert data["projects"]["/other"]["hasTrustDialogAccepted"] is False
    assert data["projects"]["/workspace"]["hasTrustDialogAccepted"] is True


def test_recovers_from_corrupted_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{ not valid json")
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")

    # Should not raise; corrupted file gets overwritten with the fix.
    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert data["skipDangerousModePermissionPrompt"] is True
