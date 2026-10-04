"""Tests for load_default_mode and dump_scopes' round trip of a leftover
``default_mode`` key (the wizard no longer asks for it, roadmap 8.89)."""

from __future__ import annotations



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
