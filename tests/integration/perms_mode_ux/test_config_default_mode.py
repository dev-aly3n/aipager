"""Integration tests: SC12, SC13 — default_mode in aipager.yaml.

SC12: dump_scopes with default_mode="auto" → load_default_mode returns "auto".
SC13: load_default_mode from yaml without default_mode key returns "ask".
"""

from __future__ import annotations

import pytest
import yaml


# --------------------------------------------------------------------------- #
# SC12 — dump_scopes writes default_mode: auto                                #
# --------------------------------------------------------------------------- #

def test_sc12_dump_scopes_auto_writes_yaml_key(tmp_path):
    """SC12: When wizard selects Auto, aipager.yaml must contain
    default_mode: auto and load_default_mode must return 'auto'."""
    from aipager.scope import Member, Scope, dump_scopes, load_default_mode

    cfg = tmp_path / "aipager.yaml"
    scopes = [Scope(
        chat_id=123, kind="dm", label="owner DM",
        members=(Member(id=123, label="owner", role="owner"),),
    )]
    dump_scopes(scopes, "tok", cfg, default_mode="auto")

    # Verify raw yaml content
    raw = yaml.safe_load(cfg.read_text())
    assert "default_mode" in raw, "default_mode key must be present in yaml after dump"
    assert raw["default_mode"] == "auto", (
        f"default_mode must be 'auto'; got {raw['default_mode']}"
    )

    # Verify round-trip via load_default_mode
    assert load_default_mode(cfg) == "auto", (
        "load_default_mode must return 'auto' after dump_scopes with auto"
    )


def test_sc12_dump_scopes_ask_writes_yaml_key(tmp_path):
    """SC12: dump_scopes with default_mode='ask' writes ask correctly."""
    from aipager.scope import Member, Scope, dump_scopes, load_default_mode

    cfg = tmp_path / "aipager.yaml"
    scopes = [Scope(
        chat_id=123, kind="dm", label="owner DM",
        members=(Member(id=123, label="owner", role="owner"),),
    )]
    dump_scopes(scopes, "tok", cfg, default_mode="ask")

    assert load_default_mode(cfg) == "ask"


# --------------------------------------------------------------------------- #
# SC13 — load_default_mode returns "ask" when key absent                      #
# --------------------------------------------------------------------------- #

def test_sc13_load_default_mode_absent_key_returns_ask(tmp_path):
    """SC13: Loading config.DEFAULT_MODE from yaml without default_mode key
    must return 'ask' (safe default, no Auto surprise)."""
    from aipager.scope import load_default_mode

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": "tok",
        "scopes": [],
    }))
    result = load_default_mode(cfg)
    assert result == "ask", (
        f"Missing default_mode key must default to 'ask'; got '{result}'"
    )


def test_sc13_load_default_mode_absent_file_returns_ask(tmp_path):
    """SC13: When aipager.yaml does not exist, load_default_mode returns 'ask'."""
    from aipager.scope import load_default_mode

    missing = tmp_path / "nonexistent.yaml"
    result = load_default_mode(missing)
    assert result == "ask", (
        f"Absent yaml file must return 'ask'; got '{result}'"
    )


def test_sc13_config_DEFAULT_MODE_is_string(monkeypatch, tmp_path):
    """SC13: config.DEFAULT_MODE is a string, not None or a boolean."""
    from aipager import config
    # DEFAULT_MODE must be one of "ask" or "auto"
    assert isinstance(config.DEFAULT_MODE, str), (
        f"DEFAULT_MODE must be str; got {type(config.DEFAULT_MODE)}"
    )
    assert config.DEFAULT_MODE in ("ask", "auto"), (
        f"DEFAULT_MODE must be 'ask' or 'auto'; got '{config.DEFAULT_MODE}'"
    )


# --------------------------------------------------------------------------- #
# Equivalence: dump_scopes without default_mode kwarg preserves existing value #
# --------------------------------------------------------------------------- #

def test_dump_scopes_without_kwarg_preserves_auto(tmp_path):
    """If aipager.yaml already has default_mode=auto and dump_scopes is called
    without a default_mode kwarg, the auto value should be preserved."""
    from aipager.scope import Member, Scope, dump_scopes, load_default_mode

    cfg = tmp_path / "aipager.yaml"
    scopes = [Scope(
        chat_id=123, kind="dm", label="owner DM",
        members=(Member(id=123, label="owner", role="owner"),),
    )]

    # First write: set auto
    dump_scopes(scopes, "tok", cfg, default_mode="auto")
    assert load_default_mode(cfg) == "auto"

    # Second write: no kwarg — must preserve
    dump_scopes(scopes, "tok", cfg)
    assert load_default_mode(cfg) == "auto", (
        "dump_scopes without default_mode kwarg must preserve existing value"
    )


# --------------------------------------------------------------------------- #
# Boundary: load_default_mode with invalid value falls back to ask            #
# --------------------------------------------------------------------------- #

def test_load_default_mode_invalid_value_falls_back_to_ask(tmp_path):
    """A garbage default_mode value in yaml must fall back to 'ask', not crash."""
    from aipager.scope import load_default_mode

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml.safe_dump({
        "schema_version": 2,
        "default_mode": "unsafe_garbage",
    }))
    result = load_default_mode(cfg)
    assert result == "ask", (
        f"Invalid default_mode must fall back to 'ask'; got '{result}'"
    )
