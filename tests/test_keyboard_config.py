"""Tests for the keyboard.json override loader (item 4.1)."""

from __future__ import annotations

import json

from aipager import config


def _reload_config(monkeypatch, kbd_path):
    """Force a re-import of aipager.config with KEYBOARD_CONFIG_PATH
    pointed at our test file. Returns the freshly-imported module."""
    monkeypatch.setattr(config, "_KEYBOARD_CONFIG_PATH", kbd_path)
    # Tell the loader to re-evaluate without touching sys.modules.
    templates, commands, models = config._load_keyboard_overrides()
    return templates, commands, models


def test_defaults_when_no_override(tmp_path, monkeypatch):
    templates, commands, models = _reload_config(
        monkeypatch, tmp_path / "nonexistent.json",
    )
    assert templates == config._DEFAULT_TEMPLATES
    assert commands == config._DEFAULT_COMMANDS
    assert models == config._DEFAULT_MODELS


def test_full_override(tmp_path, monkeypatch):
    path = tmp_path / "keyboard.json"
    path.write_text(json.dumps({
        "templates": [{"label": "Hello", "prompt": "Say hi"}],
        "commands": [{"label": "Compact", "send": "/compact"}],
        "models": [{"label": "Sonnet", "send": "/model sonnet"}],
    }))
    templates, commands, models = _reload_config(monkeypatch, path)
    assert templates == [("Hello", "Say hi")]
    assert commands == [("Compact", "/compact")]
    assert models == [("Sonnet", "/model sonnet")]


def test_partial_override_falls_back_per_section(tmp_path, monkeypatch):
    """Only `templates` is overridden — `commands` / `models` keep defaults."""
    path = tmp_path / "keyboard.json"
    path.write_text(json.dumps({
        "templates": [{"label": "Only me", "prompt": "Yep"}],
    }))
    templates, commands, models = _reload_config(monkeypatch, path)
    assert templates == [("Only me", "Yep")]
    assert commands == config._DEFAULT_COMMANDS
    assert models == config._DEFAULT_MODELS


def test_malformed_json_falls_back_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "keyboard.json"
    path.write_text("{ this is not json")
    templates, commands, models = _reload_config(monkeypatch, path)
    assert templates == config._DEFAULT_TEMPLATES
    assert commands == config._DEFAULT_COMMANDS
    assert models == config._DEFAULT_MODELS


def test_non_object_root_falls_back(tmp_path, monkeypatch):
    path = tmp_path / "keyboard.json"
    path.write_text("[1, 2, 3]")
    templates, commands, models = _reload_config(monkeypatch, path)
    assert templates == config._DEFAULT_TEMPLATES


def test_entries_missing_fields_dropped(tmp_path, monkeypatch):
    path = tmp_path / "keyboard.json"
    path.write_text(json.dumps({
        "templates": [
            {"label": "Good", "prompt": "Yes"},
            {"label": "NoPrompt"},                 # missing payload — dropped
            {"prompt": "Anonymous"},               # missing label — dropped
            {"label": "Good2", "prompt": "Also"},
        ],
    }))
    templates, _, _ = _reload_config(monkeypatch, path)
    assert templates == [("Good", "Yes"), ("Good2", "Also")]


def test_empty_section_after_cleaning_falls_back(tmp_path, monkeypatch):
    """If every entry in a section is malformed, fall back to defaults."""
    path = tmp_path / "keyboard.json"
    path.write_text(json.dumps({
        "templates": [{"label": "no payload"}, {"oops": "wrong"}],
    }))
    templates, _, _ = _reload_config(monkeypatch, path)
    # All entries were dropped → fallback to defaults
    assert templates == config._DEFAULT_TEMPLATES
