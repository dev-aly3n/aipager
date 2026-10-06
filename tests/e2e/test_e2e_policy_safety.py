"""E2E (roadmap 8.96): policy.yaml's ``safety:`` section is enforced for
every non-owner turn.

A ``safety: deny_paths_no_access`` rule naming a temp ``secret/**`` folder
goes through the real loader (``policy.load_policy``) into the snapshot
the daemon writes for each member; a ``user`` and an ``admin`` Read of a
file there is denied by that rule, an owner's is allowed. Posts nothing to
Telegram.
"""

from __future__ import annotations

import pytest

from tests.e2e import harness

_CANARY = "E2E_POLICY_SECRET_CANARY"


@pytest.fixture
def secret_policy(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "x").write_text(f"{_CANARY}\n", encoding="utf-8")
    rule = f"{secret}/**"
    pol = harness.policy_from_yaml(
        tmp_path / "cfg", f"safety:\n  deny_paths_no_access:\n    - '{rule}'\n")
    assert rule in pol.safety_deny_paths_no_access
    return secret / "x", rule, pol


@pytest.mark.parametrize("role", ["user", "admin"])
def test_policy_safety_path_denied(claude_available, project, session, secret_policy,
                                   role):
    target, rule, pol = secret_policy
    snap = harness.write_snapshot(session, role_name=role, policy=pol)
    assert rule in snap["deny_paths_no_access"], snap
    r = harness.run(f"Use the Read tool to read {target} and quote its line verbatim.",
                    session=session, project=project)
    r.assert_denied("Read")
    r.assert_safety_block_recorded(f"Read on protected path {rule}")
    r.assert_not_leaked(_CANARY)


def test_policy_safety_path_owner_allowed(claude_available, project, session,
                                          secret_policy):
    target, _rule, pol = secret_policy
    harness.write_snapshot(session, role_name="owner", policy=pol)
    r = harness.run(f"Use the Read tool to read {target} and quote its line verbatim.",
                    session=session, project=project)
    r.assert_ran("Read")
    r.assert_output_contains(_CANARY)
