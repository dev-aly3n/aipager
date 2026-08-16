"""Tests for `aipager miniapp enable/disable/status` — round-trips
against a tmp_path-redirected aipager.yaml (via the autouse
_isolate_home_paths / _isolate_wizard_config fixtures).

These settings used to live in config.env; they moved into the
`miniapp:` block of aipager.yaml because `migrate.retire_v1()` renames
config.env away on every daemon start, which silently disabled the Mini
App after one restart. The CLI surface is unchanged — same subcommands,
same flags — so these tests kept their original intent and only changed
where they read the result from.
"""

import argparse

import yaml

from aipager import scope
from aipager.miniapp.cli import (
    _cmd_miniapp_disable,
    _cmd_miniapp_enable,
    _cmd_miniapp_status,
    _current_miniapp_config,
)


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _seed_config(*, token="abc:123", chat_id=42):
    """dump_miniapp edits an existing document — it never invents a config
    file, so a token/scopes must already be present, as they are on any
    real install where `aipager config` has run."""
    scope.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    scope.CONFIG_PATH.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": token,
        "scopes": [{
            "kind": "dm",
            "chat_id": chat_id,
            "label": "me",
            "members": [{"id": chat_id, "label": "me", "role": "owner"}],
        }],
    }), encoding="utf-8")


def test_enable_writes_defaults(_isolate_home_paths):
    _seed_config()
    rc = _cmd_miniapp_enable(_ns(port=None, url=None))
    assert rc == 0
    cfg = _current_miniapp_config()
    assert cfg["enabled"] is True
    assert cfg["port"] == 8765
    assert cfg["public_url"] == ""


def test_enable_with_port_and_url(_isolate_home_paths):
    _seed_config()
    rc = _cmd_miniapp_enable(_ns(port=9999, url="https://example.ts.net/"))
    assert rc == 0
    cfg = _current_miniapp_config()
    assert cfg["enabled"] is True
    assert cfg["port"] == 9999
    assert cfg["public_url"] == "https://example.ts.net/"


def test_enable_rejects_non_https_url(_isolate_home_paths):
    _seed_config()
    rc = _cmd_miniapp_enable(_ns(port=None, url="http://insecure.example/"))
    assert rc != 0
    # Nothing should have been written on a rejected URL.
    assert _current_miniapp_config()["enabled"] is False


def test_enable_preserves_other_config_keys(_isolate_home_paths):
    """The whole point of the move: writing the Mini App block must not
    disturb the bot token or the scopes sharing the file."""
    _seed_config(token="abc:123", chat_id=42)

    rc = _cmd_miniapp_enable(_ns(port=8765, url=None))
    assert rc == 0

    loaded = scope.load_scopes(scope.CONFIG_PATH)
    assert loaded is not None
    scopes, token = loaded
    assert token == "abc:123"
    assert [s.chat_id for s in scopes] == [42]
    assert _current_miniapp_config()["enabled"] is True


def test_enable_reuses_existing_port_when_omitted(_isolate_home_paths):
    _seed_config()
    _cmd_miniapp_enable(_ns(port=5555, url=None))
    _cmd_miniapp_disable(_ns())
    # Re-enable without --port: should keep 5555, not reset to 8765.
    rc = _cmd_miniapp_enable(_ns(port=None, url=None))
    assert rc == 0
    cfg = _current_miniapp_config()
    assert cfg["port"] == 5555
    assert cfg["enabled"] is True


def test_disable_sets_enabled_false(_isolate_home_paths):
    _seed_config()
    _cmd_miniapp_enable(_ns(port=None, url=None))
    rc = _cmd_miniapp_disable(_ns())
    assert rc == 0
    assert _current_miniapp_config()["enabled"] is False


def test_status_reflects_disk_state(_isolate_home_paths, monkeypatch):
    import aipager.miniapp.tunnel as tunnel_mod

    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
    _seed_config()
    _cmd_miniapp_enable(_ns(port=7777, url="https://tunnel.example/"))
    cfg = _current_miniapp_config()
    assert cfg["enabled"] is True
    assert cfg["port"] == 7777
    assert cfg["public_url"] == "https://tunnel.example/"

    rc = _cmd_miniapp_status(_ns())
    assert rc == 0


def test_status_before_any_enable_shows_defaults(_isolate_home_paths):
    cfg = _current_miniapp_config()
    assert cfg["enabled"] is False
    assert cfg["port"] == 8765
    assert cfg["public_url"] == ""


def test_status_never_requires_daemon_running(_isolate_home_paths, monkeypatch):
    """status must work purely from files on disk — no daemon socket probe."""
    import aipager.miniapp.tunnel as tunnel_mod

    monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
    rc = _cmd_miniapp_status(_ns())
    assert rc == 0
