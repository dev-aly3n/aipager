"""E2E: origin + bypass govern enforcement.

- Terminal-origin (no marker) is unrestricted.
- `owner` (bypass_safety) is unrestricted even from Telegram.
- `admin` does NOT bypass the safety floor.
"""

from __future__ import annotations

from tests.e2e import harness

_VERSION_TASK = (
    "Use the Bash tool to run `claude --version` and report the version.")


def test_terminal_origin_unrestricted(claude_available, project, session):
    """No `[via Telegram]` marker → terminal origin → the command runs."""
    harness.write_snapshot(session, role_name="user")  # ignored for terminal
    r = harness.run(_VERSION_TASK, session=session, project=project,
                    marker=False)
    r.assert_ran("Bash")              # the command actually executed
    r.assert_output_contains("2.1.")


def test_owner_bypasses_safety(claude_available, project, session):
    """Owner role (bypass_safety) → blocked command runs from Telegram."""
    harness.write_snapshot(session, role_name="owner")
    r = harness.run(_VERSION_TASK, session=session, project=project)
    r.assert_ran("Bash")              # owner: the blocked command actually ran
    r.assert_output_contains("2.1.")


def test_admin_still_bound_by_safety_floor(claude_available, project, session):
    """Admin bypasses deny_tools but NOT the hard-safety floor."""
    harness.write_snapshot(session, role_name="admin")
    r = harness.run(_VERSION_TASK, session=session, project=project)
    r.assert_denied("Bash")
    r.assert_not_leaked("2.1.")
