"""``aipager setup --json`` patches and reports the settings file the
daemon's sessions read (design success criterion 2; entrypoints "CLI
commands").

Reuses the agent-setup package's black-box harness (fake Telegram, fake
binaries, no service manager, no sockets, no subprocesses) instead of
copying it: its ``env`` fixture is loaded from that package's conftest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from _cfgdir_kit import has_hooks

_AS_DIR = Path(__file__).resolve().parents[1] / "agent-setup"
if str(_AS_DIR) not in sys.path:
    sys.path.insert(0, str(_AS_DIR))
_spec = importlib.util.spec_from_file_location(
    "_cfgdir_agent_setup_conftest", _AS_DIR / "conftest.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
env = _mod.env  # the fixture

CCD = "CLAUDE_CONFIG_DIR"


def _settings_json(result) -> dict:
    return result.json["settings_json"] or {}


def test_setup_reports_the_daemon_env_config_dirs_file(env, cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    r = env.setup()
    assert Path(_settings_json(r)["path"]) == cc.x / "settings.json"


def test_setup_patches_the_daemon_env_config_dirs_file(env, cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    env.setup()
    assert has_hooks(cc.x / "settings.json")


def test_setup_with_a_config_dir_leaves_the_default_file_absent(env, cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    env.setup()
    assert not cc.home_settings.exists()


def test_setup_reports_the_shell_config_dirs_file(env, cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    r = env.setup()
    assert Path(_settings_json(r)["path"]) == cc.x / "settings.json"


def test_setup_without_a_config_dir_reports_the_default_file(env, cc):
    r = env.setup()
    assert Path(_settings_json(r)["path"]) == cc.home_settings


def test_setup_follows_a_settings_env_move(env, cc):
    from _cfgdir_kit import wjson
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    env.setup()
    assert has_hooks(cc.x / "settings.json")
