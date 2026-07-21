"""Regression: the autouse ``_isolate_wizard_config`` fixture must
redirect real user config paths so no test corrupts a live install.

Context: a /ship pipeline run in July 2026 clobbered a user's real
``~/.config/aipager/aipager.yaml`` with fixture data (``bot_token="TOK"``,
``chat_id=42``) because tests exercised ``first_run._commit_owner_dm``
without redirecting ``_scope.CONFIG_PATH``. The autouse fixture in
``conftest.py`` prevents this — these tests assert that guarantee.
"""

from __future__ import annotations

from pathlib import Path

import aipager.policy as _policy
import aipager.scope as _scope


_REAL_CONFIG = Path.home() / ".config" / "aipager" / "aipager.yaml"
_REAL_POLICY = Path.home() / ".config" / "aipager" / "policy.yaml"


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
