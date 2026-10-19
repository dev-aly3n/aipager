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

import pytest

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


# ---- a relative config home: refused, nothing written (review-1 rev-iter1-003) -------

_RELATIVE = ["", "  ", "relcfg"]
_RELATIVE_IDS = ["empty", "whitespace", "relative"]


def _rel_target(cc, value):
    return (cc.work / value / "settings.json") if value else (cc.work / "settings.json")


@pytest.mark.parametrize("value", _RELATIVE, ids=_RELATIVE_IDS)
def test_setup_refuses_a_relative_config_dir(env, cc, monkeypatch, value):
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, value)
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "settings_invalid")


@pytest.mark.parametrize("value", _RELATIVE, ids=_RELATIVE_IDS)
def test_setup_writes_no_settings_into_a_relative_config_dir(env, cc, monkeypatch, value):
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, value)
    env.setup()
    assert not _rel_target(cc, value).exists()


def test_setup_with_a_relative_config_dir_writes_no_default_file(env, cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "relcfg")
    env.setup()
    assert not cc.home_settings.exists()


def test_setup_with_a_relative_config_dir_writes_nothing(env, cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "relcfg")
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_setup_refuses_a_relative_daemon_env_value(env, cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR="relcfg")
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "settings_invalid")


def test_setup_refusal_message_is_relative_homes_problem(env, cc, monkeypatch):
    from aipager.wizard import settings_patch
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "relcfg")
    problem, _fix = settings_patch.relative_home()
    assert env.setup().json["message"] == problem


def test_setup_refusal_fix_is_relative_homes_fix(env, cc, monkeypatch):
    from aipager.wizard import settings_patch
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "relcfg")
    _problem, fix = settings_patch.relative_home()
    assert env.setup().json["fix"] == fix


def test_setup_leaves_a_projects_own_settings_file_alone(env, cc, monkeypatch):
    raw = '{"project": "own file"}'
    (cc.work / "settings.json").write_text(raw)
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "")
    env.setup()
    assert (cc.work / "settings.json").read_text() == raw
