"""design.md success criterion 1 (settings half): setup's settings.json
is byte-identical to what the wizard's ``_step_settings`` produces from
the same input file, and an existing file is backed up."""

from __future__ import annotations

import json

import pytest

INPUTS = {
    "absent": None,
    "empty-object": "{}\n",
    "user-content": json.dumps({
        "model": "opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command",
                                       "command": "/usr/bin/true"}]}]},
    }, indent=2) + "\n",
    "foreign-statusline": json.dumps({
        "statusLine": {"type": "command", "command": "/usr/bin/mine"},
    }) + "\n",
}


def _reset(env, content):
    if env.settings_path.exists():
        env.settings_path.unlink()
    for b in env.settings_path.parent.glob("settings.json.bak*"):
        b.unlink()
    if content is not None:
        env.settings_path.parent.mkdir(parents=True, exist_ok=True)
        env.settings_path.write_text(content)


def _wizard_bytes(env, content):
    from aipager.wizard import settings_patch
    _reset(env, content)
    settings_patch._step_settings("[4/5]")
    return env.settings_path.read_bytes()


@pytest.mark.parametrize("name", list(INPUTS))
def test_settings_equal_the_wizard_step(env, name):
    content = INPUTS[name]
    _reset(env, content)
    assert env.setup().code == 0
    via_setup = env.settings_path.read_bytes()
    assert via_setup == _wizard_bytes(env, content)


@pytest.mark.parametrize("name", ["user-content", "foreign-statusline"])
def test_existing_settings_are_backed_up(env, name):
    _reset(env, INPUTS[name])
    env.setup()
    backups = list(env.settings_path.parent.glob("settings.json.bak.*"))
    assert [b.read_text() for b in backups] == [INPUTS[name]]


def test_backup_is_reported_in_json(env):
    _reset(env, INPUTS["user-content"])
    d = env.setup().json
    assert d["settings_json"]["backup"] and "settings.json.bak." in d[
        "settings_json"]["backup"]


def test_existing_settings_status_patched(env):
    _reset(env, INPUTS["user-content"])
    assert env.setup().json["settings_json"]["status"] == "patched"


def test_absent_settings_status_created(env):
    assert env.setup().json["settings_json"]["status"] == "created"


def test_absent_settings_no_backup(env):
    assert env.setup().json["settings_json"]["backup"] is None


def test_settings_path_reported_absolute(env):
    assert env.setup().json["settings_json"]["path"] == str(
        env.settings_path)


def test_user_keys_survive(env):
    _reset(env, INPUTS["user-content"])
    env.setup()
    doc = json.loads(env.settings_path.read_text())
    assert doc.get("permissions") == {"allow": ["Bash(ls:*)"]}


def test_already_patched_settings_unchanged_on_fresh_yaml(env):
    """settings.json already aipager-patched, aipager.yaml absent: only the
    yaml changes."""
    content = _wizard_bytes(env, None).decode()
    _reset(env, content)
    r = env.setup()
    assert (r.json["changed"], env.settings_path.read_text()) == (
        ["bot_token", "owner_dm"], content)
