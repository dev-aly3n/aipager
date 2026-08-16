"""Tests for `aipager miniapp enable/disable/status` — round-trips
against a tmp_path-redirected config.env (via the autouse
_isolate_home_paths fixture)."""

import argparse

from aipager.miniapp.cli import (
    _cmd_miniapp_disable,
    _cmd_miniapp_enable,
    _cmd_miniapp_status,
    _current_miniapp_config,
    _parse_env_lines,
    _read_config_env_lines,
)


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def test_enable_writes_defaults(_isolate_home_paths):
    from aipager.wizard._constants import CONFIG_ENV

    rc = _cmd_miniapp_enable(_ns(port=None, url=None))
    assert rc == 0
    assert CONFIG_ENV.exists()
    values = _parse_env_lines(_read_config_env_lines())
    assert values["MINIAPP_ENABLED"] == "1"
    assert values["MINIAPP_PORT"] == "8765"
    assert "MINIAPP_PUBLIC_URL" not in values


def test_enable_with_port_and_url(_isolate_home_paths):
    rc = _cmd_miniapp_enable(_ns(port=9999, url="https://example.ts.net/"))
    assert rc == 0
    values = _parse_env_lines(_read_config_env_lines())
    assert values["MINIAPP_ENABLED"] == "1"
    assert values["MINIAPP_PORT"] == "9999"
    assert values["MINIAPP_PUBLIC_URL"] == "https://example.ts.net/"


def test_enable_rejects_non_https_url(_isolate_home_paths):
    rc = _cmd_miniapp_enable(_ns(port=None, url="http://insecure.example/"))
    assert rc != 0
    values = _parse_env_lines(_read_config_env_lines())
    # Nothing should have been written on a rejected URL.
    assert "MINIAPP_ENABLED" not in values


def test_enable_preserves_other_config_keys(_isolate_home_paths):
    from aipager.wizard._constants import CONFIG_DIR, CONFIG_ENV

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text(
        "CLAUDE_TG_BOT_TOKEN=abc:123\nCLAUDE_TG_CHAT_ID=42\n"
    )

    rc = _cmd_miniapp_enable(_ns(port=8765, url=None))
    assert rc == 0

    values = _parse_env_lines(_read_config_env_lines())
    assert values["CLAUDE_TG_BOT_TOKEN"] == "abc:123"
    assert values["CLAUDE_TG_CHAT_ID"] == "42"
    assert values["MINIAPP_ENABLED"] == "1"


def test_enable_reuses_existing_port_when_omitted(_isolate_home_paths):
    _cmd_miniapp_enable(_ns(port=5555, url=None))
    _cmd_miniapp_disable(_ns())
    # Re-enable without --port: should keep 5555, not reset to 8765.
    rc = _cmd_miniapp_enable(_ns(port=None, url=None))
    assert rc == 0
    values = _parse_env_lines(_read_config_env_lines())
    assert values["MINIAPP_PORT"] == "5555"
    assert values["MINIAPP_ENABLED"] == "1"


def test_disable_sets_enabled_zero(_isolate_home_paths):
    _cmd_miniapp_enable(_ns(port=None, url=None))
    rc = _cmd_miniapp_disable(_ns())
    assert rc == 0
    values = _parse_env_lines(_read_config_env_lines())
    assert values["MINIAPP_ENABLED"] == "0"


def test_status_reflects_disk_state(_isolate_home_paths, monkeypatch):
    import aipager.miniapp.tunnel as tunnel_mod

    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
    _cmd_miniapp_enable(_ns(port=7777, url="https://tunnel.example/"))
    cfg = _current_miniapp_config()
    assert cfg["MINIAPP_ENABLED"] == "1"
    assert cfg["MINIAPP_PORT"] == "7777"
    assert cfg["MINIAPP_PUBLIC_URL"] == "https://tunnel.example/"

    rc = _cmd_miniapp_status(_ns())
    assert rc == 0


def test_status_before_any_enable_shows_defaults(_isolate_home_paths):
    cfg = _current_miniapp_config()
    assert cfg["MINIAPP_ENABLED"] == "0"
    assert cfg["MINIAPP_PORT"] == "8765"
    assert cfg["MINIAPP_PUBLIC_URL"] == ""


def test_status_never_requires_daemon_running(_isolate_home_paths, monkeypatch):
    """status must work purely from files on disk — no daemon socket probe."""
    import aipager.miniapp.tunnel as tunnel_mod

    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
    rc = _cmd_miniapp_status(_ns())
    assert rc == 0
