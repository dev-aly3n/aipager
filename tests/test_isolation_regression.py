"""Regression: the autouse ``_isolate_wizard_config`` fixture must
redirect real user config paths so no test corrupts a live install.

Context: a /ship pipeline run in July 2026 clobbered a user's real
``~/.config/aipager/aipager.yaml`` with fixture data (``bot_token="TOK"``,
``chat_id=42``) because tests exercised ``first_run._commit_owner_dm``
without redirecting ``_scope.CONFIG_PATH``. The autouse fixture in
``conftest.py`` prevents this — these tests assert that guarantee.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

import aipager.policy as _policy
import aipager.scope as _scope


_CFG = Path.home() / ".config" / "aipager"
_CLAUDE = Path.home() / ".claude"
_REAL_CONFIG = _CFG / "aipager.yaml"
_REAL_POLICY = _CFG / "policy.yaml"


def test_scope_config_path_is_isolated_from_real_home(tmp_path):
    """``_scope.CONFIG_PATH`` must NOT point at the real user config."""
    assert _scope.CONFIG_PATH != _REAL_CONFIG, (
        f"scope.CONFIG_PATH leaks the real user path: {_scope.CONFIG_PATH}"
    )
    # And it MUST live inside a pytest tmp dir (not /tmp arbitrary).
    assert "pytest-" in str(_scope.CONFIG_PATH), (
        f"scope.CONFIG_PATH should be under a pytest tmp_path, got: "
        f"{_scope.CONFIG_PATH}"
    )


def test_policy_path_is_isolated_from_real_home(tmp_path):
    """``_policy.POLICY_PATH`` must NOT point at the real user policy."""
    assert _policy.POLICY_PATH != _REAL_POLICY, (
        f"policy.POLICY_PATH leaks the real user path: {_policy.POLICY_PATH}"
    )
    assert "pytest-" in str(_policy.POLICY_PATH), (
        f"policy.POLICY_PATH should be under a pytest tmp_path, got: "
        f"{_policy.POLICY_PATH}"
    )


def test_dumping_scope_writes_to_tmp_not_home():
    """Belt-and-braces: actually calling ``scope.dump_scopes`` with
    fixture-shaped values (the ones the bug wrote) must land in tmp,
    not overwrite the real config."""
    from aipager.scope import Member, Scope, dump_scopes, load_scopes

    scopes = [Scope(chat_id=42, kind="dm", label="owner DM",
                    members=(Member(id=42, label="owner", role="owner"),))]
    dump_scopes(scopes, "TOK", _scope.CONFIG_PATH)

    # The write landed in the isolated path — confirm by reading it back.
    loaded, token = load_scopes(_scope.CONFIG_PATH)
    assert token == "TOK"
    assert loaded[0].chat_id == 42

    # And the REAL config on disk (if it exists) is untouched. We can't
    # inspect it directly without racing, so we just re-assert that
    # ``_scope.CONFIG_PATH`` still points at tmp.
    assert _scope.CONFIG_PATH != _REAL_CONFIG


# Every module-level write target ``_isolate_home_paths`` redirects, as
# ``(dotted attribute, real path it must not equal)``. Entries that
# differ only by consumer module are listed separately on purpose: a
# by-value ``from x import CONST`` keeps its own copy, so patching the
# defining module alone would leave the consumer pointing at real home.
_REDIRECTED = [
    ("aipager.claude_bootstrap._SETTINGS", _CLAUDE / "settings.json"),
    ("aipager.claude_bootstrap._CLAUDE_JSON", Path.home() / ".claude.json"),
    ("aipager.team.PENDING_USERS_PATH",
     _CLAUDE / "aipager-pending-users.json"),
    ("aipager.team.TEAM_CONFIG_PATH", _CFG / "team.yaml"),
    ("aipager.policy.POLICY_D_DIR", _CFG / "policy.d"),
    ("aipager.config.SESSION_STATE_FILE", _CLAUDE / "aipager-sessions.json"),
    ("aipager.state.SESSION_STATE_FILE", _CLAUDE / "aipager-sessions.json"),
    ("aipager.status.SESSION_STATE_FILE", _CLAUDE / "aipager-sessions.json"),
    ("aipager.config._KEYBOARD_CONFIG_PATH", _CFG / "keyboard.json"),
    ("aipager.session_store.SESSIONS_ROOT",
     Path.home() / ".local" / "share" / "aipager" / "sessions"),
    ("aipager.service.LINUX_UNIT_PATH",
     Path.home() / ".config" / "systemd" / "user" / "aipager.service"),
    ("aipager.service.MACOS_PLIST_PATH",
     Path.home() / "Library" / "LaunchAgents" / "com.aipager.daemon.plist"),
    ("aipager.service.MACOS_LOG_PATH",
     Path.home() / "Library" / "Logs" / "aipager.log"),
    ("aipager.wizard._constants.CLAUDE_SETTINGS", _CLAUDE / "settings.json"),
    ("aipager.wizard.settings_patch.CLAUDE_SETTINGS",
     _CLAUDE / "settings.json"),
    ("aipager.wizard._constants.CONFIG_DIR", _CFG),
    ("aipager.wizard.daemon_io.CONFIG_DIR", _CFG),
    ("aipager.wizard.draft.CONFIG_DIR", _CFG),
    ("aipager.wizard._constants.CONFIG_ENV", _CFG / "config.env"),
    ("aipager.wizard.daemon_io.CONFIG_ENV", _CFG / "config.env"),
    ("aipager.wizard.CONFIG_ENV", _CFG / "config.env"),
    ("aipager.wizard._constants.TEAM_YAML", _CFG / "team.yaml"),
    ("aipager.wizard.draft.DRAFT_PATH", _CFG / ".wizard-draft.json"),
]


@pytest.mark.parametrize("dotted,real", _REDIRECTED)
def test_home_write_targets_are_isolated(dotted, real):
    module_name, _, attr = dotted.rpartition(".")
    value = getattr(import_module(module_name), attr)
    assert value != real, f"{dotted} leaks the real user path: {value}"
    assert "pytest-" in str(value), (
        f"{dotted} should be under a pytest tmp_path, got: {value}"
    )


def test_uninstall_removal_lists_are_isolated():
    """``cmd_uninstall(force=True)`` rmtrees everything in these lists —
    an unredirected entry would delete a live install."""
    import aipager.updater as _updater

    for name in ("_USER_PATHS_TO_REMOVE", "_MACOS_PATHS_TO_REMOVE"):
        for path in getattr(_updater, name):
            assert "pytest-" in str(path), (
                f"updater.{name} entry escapes tmp_path: {path}"
            )
