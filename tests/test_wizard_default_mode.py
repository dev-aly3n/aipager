"""Tests for default_mode wizard step and load_default_mode."""

from __future__ import annotations

import pytest


# ---- load_default_mode -----------------------------------------------------

def test_load_default_mode_missing_file_returns_ask(tmp_path, monkeypatch):
    """When aipager.yaml doesn't exist, default is 'ask'."""
    import aipager.scope as scope_mod
    monkeypatch.setattr(scope_mod, "CONFIG_PATH", tmp_path / "aipager.yaml")
    from aipager.scope import load_default_mode
    assert load_default_mode(tmp_path / "aipager.yaml") == "ask"


def test_load_default_mode_no_key_returns_ask(tmp_path):
    """When aipager.yaml exists but has no default_mode key, returns 'ask'."""
    import yaml
    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": "tok",
        "scopes": [],
    }))
    from aipager.scope import load_default_mode
    assert load_default_mode(cfg) == "ask"


def test_load_default_mode_ask(tmp_path):
    """When default_mode=ask is in the file, returns 'ask'."""
    import yaml
    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": "tok",
        "default_mode": "ask",
        "scopes": [],
    }))
    from aipager.scope import load_default_mode
    assert load_default_mode(cfg) == "ask"


def test_load_default_mode_auto(tmp_path):
    """When default_mode=auto is in the file, returns 'auto'."""
    import yaml
    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": "tok",
        "default_mode": "auto",
        "scopes": [],
    }))
    from aipager.scope import load_default_mode
    assert load_default_mode(cfg) == "auto"


def test_load_default_mode_invalid_value_returns_ask(tmp_path):
    """When default_mode has an unrecognized value, falls back to 'ask'."""
    import yaml
    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": "tok",
        "default_mode": "unsafe",  # invalid
        "scopes": [],
    }))
    from aipager.scope import load_default_mode
    assert load_default_mode(cfg) == "ask"


def test_load_default_mode_corrupt_yaml_returns_ask(tmp_path):
    """When aipager.yaml has invalid YAML, returns 'ask' without crashing."""
    cfg = tmp_path / "aipager.yaml"
    cfg.write_text("{ invalid: yaml: content: }")
    from aipager.scope import load_default_mode
    assert load_default_mode(cfg) == "ask"


# ---- dump_scopes preserves default_mode ------------------------------------

def test_dump_scopes_writes_default_mode_auto(tmp_path):
    """dump_scopes with default_mode='auto' writes the key to yaml."""
    import yaml
    from aipager.scope import Scope, dump_scopes, load_default_mode

    cfg = tmp_path / "aipager.yaml"
    # Write a minimal valid aipager.yaml first
    from aipager.scope import Member
    scopes = [Scope(
        chat_id=123, kind="dm", label="owner DM",
        members=(Member(id=123, label="owner", role="owner"),),
    )]
    dump_scopes(scopes, "tok", cfg, default_mode="auto")

    assert load_default_mode(cfg) == "auto"


def test_dump_scopes_preserves_existing_default_mode(tmp_path):
    """dump_scopes without default_mode kwarg preserves the existing value."""
    import yaml
    from aipager.scope import Member, Scope, dump_scopes, load_default_mode

    cfg = tmp_path / "aipager.yaml"
    scopes = [Scope(
        chat_id=123, kind="dm", label="owner DM",
        members=(Member(id=123, label="owner", role="owner"),),
    )]
    # First write: set to auto
    dump_scopes(scopes, "tok", cfg, default_mode="auto")
    assert load_default_mode(cfg) == "auto"

    # Second write: no default_mode kwarg — should preserve "auto"
    dump_scopes(scopes, "tok", cfg)
    assert load_default_mode(cfg) == "auto"


# ---- _step_default_mode ----------------------------------------------------

def test_step_default_mode_returns_ask(monkeypatch):
    """Selecting Ask returns 'ask'."""
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_ask", lambda prompt: "ask")
    result = first_run._step_default_mode()
    assert result == "ask"


def test_step_default_mode_returns_auto(monkeypatch):
    """Selecting Auto returns 'auto'."""
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_ask", lambda prompt: "auto")
    result = first_run._step_default_mode()
    assert result == "auto"


def test_commit_default_mode_writes_to_yaml(tmp_path, monkeypatch):
    """_commit_default_mode writes default_mode to aipager.yaml."""
    import aipager.scope as scope_mod
    from aipager.scope import Member, Scope, dump_scopes

    cfg = tmp_path / "aipager.yaml"
    monkeypatch.setattr(scope_mod, "CONFIG_PATH", cfg)

    # Write a valid yaml first so read_config can read it
    scopes = [Scope(
        chat_id=42, kind="dm", label="owner DM",
        members=(Member(id=42, label="owner", role="owner"),),
    )]
    dump_scopes(scopes, "TOK", cfg)

    # Redirect scope_io to use the tmp path too
    from aipager.wizard import scope_io as _scope_io
    monkeypatch.setattr(_scope_io, "_scope", scope_mod)

    from aipager.wizard.first_run import _commit_default_mode
    # Override load_default_mode in scope_mod to use our cfg
    # _commit_default_mode calls scope_mod.dump_scopes which internally
    # calls load_default_mode; redirect CONFIG_PATH in scope_mod
    _commit_default_mode("auto")

    assert scope_mod.load_default_mode(cfg) == "auto"
