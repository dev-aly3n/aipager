"""Tests for aipager.claude_bootstrap — first-run state-file patches."""

from __future__ import annotations

import json

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
    settings.write_text(json.dumps({"theme": "dark", "myCustomKey": 42}))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    # No aipager-hook available — keeps this test focused on the bypass flag
    monkeypatch.setattr(claude_bootstrap.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(claude_bootstrap.sys, "executable", "/nonexistent/python")

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert data["theme"] == "dark"
    assert data["myCustomKey"] == 42
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
    # No aipager-hook available — keep the idempotency test focused
    monkeypatch.setattr(claude_bootstrap.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(claude_bootstrap.sys, "executable", "/nonexistent/python")
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


def test_wires_aipager_hook_when_helper_resolves(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    hook_bin = tmp_path / "aipager-hook"
    hook_bin.write_text("#!/bin/sh\nexit 0\n")
    hook_bin.chmod(0o755)
    sl_bin = tmp_path / "aipager-statusline"
    sl_bin.write_text("#!/bin/sh\nexit 0\n")
    sl_bin.chmod(0o755)
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: str(tmp_path / cmd) if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert "hooks" in data
    # Every event the wizard wires must be present
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"):
        entries = data["hooks"][event]
        assert isinstance(entries, list) and entries, f"no entries for {event}"
        cmds = [
            h.get("command", "")
            for block in entries
            for h in block.get("hooks", [])
        ]
        assert any(c.endswith("/aipager-hook") for c in cmds), \
            f"aipager-hook missing from {event}"
    # PreToolUse should be tagged with matcher *
    assert data["hooks"]["PreToolUse"][0].get("matcher") == "*"
    # statusLine wired
    assert data["statusLine"]["command"].endswith("/aipager-statusline")


def test_hooks_idempotent_no_duplicate(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")
    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    for event in claude_bootstrap._HOOK_EVENTS:
        entries = data["hooks"][event]
        cmds = [
            h.get("command", "")
            for block in entries
            for h in block.get("hooks", [])
        ]
        aipager_count = sum(1 for c in cmds if c.endswith("/aipager-hook"))
        assert aipager_count == 1, f"{event} has {aipager_count} aipager-hook entries"


def test_skips_hook_wiring_when_helper_missing(tmp_path, monkeypatch):
    """If aipager-hook isn't on PATH (broken install), don't write a
    bare command that claude can't execute."""
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(claude_bootstrap.sys, "executable", "/nonexistent/python")

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    # Bypass flag still set — that doesn't depend on aipager-hook resolution
    assert data["skipDangerousModePermissionPrompt"] is True
    # But hooks absent — we don't want a bare "aipager-hook" claude can't run
    assert "hooks" not in data
    assert "statusLine" not in data


# ---- _has_hook_cmd: bare-name + wrapper detection ---------------------

from aipager.claude_bootstrap import _has_hook_cmd  # noqa: E402


def _wrap(cmd):
    return [{"hooks": [{"type": "command", "command": cmd}]}]


def test_has_hook_cmd_bare_name():
    assert _has_hook_cmd(_wrap("aipager-hook"), "aipager-hook") is True


def test_has_hook_cmd_absolute_path():
    assert _has_hook_cmd(
        _wrap("/home/x/.local/bin/aipager-hook"), "aipager-hook",
    ) is True


def test_has_hook_cmd_wrapper_capped():
    """Wrapper script named aipager-hook-capped.sh (ulimit wrapper
    pattern) must be detected — this is the bug the fix addresses."""
    assert _has_hook_cmd(
        _wrap("/home/x/.claude/scripts/aipager-hook-capped.sh"),
        "aipager-hook",
    ) is True


def test_has_hook_cmd_wrapper_bare_prefix():
    """Wrapper without a path — basename-prefix match still fires."""
    assert _has_hook_cmd(
        _wrap("aipager-hook-debug"), "aipager-hook",
    ) is True


def test_has_hook_cmd_unrelated_command():
    """Regression guard: unrelated commands must NOT match."""
    assert _has_hook_cmd(_wrap("/bin/echo hello"), "aipager-hook") is False


def test_has_hook_cmd_missing_command_key():
    """Hook block without a command key — no crash, return False."""
    entries = [{"hooks": [{"type": "command"}]}]
    assert _has_hook_cmd(entries, "aipager-hook") is False


def test_has_hook_cmd_empty_command():
    assert _has_hook_cmd(_wrap(""), "aipager-hook") is False


def test_has_hook_cmd_empty_bare_name_returns_false():
    """Defensive: an empty bare_name must never match anything."""
    assert _has_hook_cmd(_wrap("/some/aipager-hook"), "") is False

