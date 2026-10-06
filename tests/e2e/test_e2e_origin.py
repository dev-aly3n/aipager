"""E2E: origin + bypass govern enforcement.

- Terminal-origin (no marker) is unrestricted, even in a session whose
  last Telegram turn was a restricted member's.
- `owner` (bypass_safety) is unrestricted even from Telegram.
- `admin` does NOT bypass the safety floor.

Posts nothing to Telegram.
"""

from __future__ import annotations

from tests.e2e import harness

_VERSION_TASK = (
    "Use the Bash tool to run `claude --version` and report the version.")


def test_terminal_origin_unrestricted(claude_available, project, session):
    """A restricted member drove the last turn; then the operator types a
    prompt in the terminal (no marker, no note): the command runs."""
    harness.write_snapshot(session, role_name="user")
    snap = harness.write_terminal_prompt(session, _VERSION_TASK)
    assert snap.get("turn_origin") == "terminal", snap
    r = harness.run(_VERSION_TASK, session=session, project=project,
                    marker=False)
    r.assert_ran("Bash")              # the command actually executed
    r.assert_output_contains("2.1.")


def test_owner_bypasses_safety(claude_available, project, session):
    """Owner role (bypass_safety) → blocked command runs from Telegram."""
    snap = harness.write_snapshot(session, role_name="owner")
    assert snap["bypass_safety"] is True
    r = harness.run(_VERSION_TASK, session=session, project=project)
    r.assert_ran("Bash")              # owner: the blocked command actually ran
    r.assert_output_contains("2.1.")


def test_admin_still_bound_by_safety_floor(claude_available, project, session):
    """Admin bypasses deny_tools but NOT the hard-safety floor."""
    snap = harness.write_snapshot(session, role_name="admin")
    assert snap["bypass_safety"] is False and "Bash" not in snap["deny_tools"]
    r = harness.run(_VERSION_TASK, session=session, project=project)
    r.assert_denied("Bash")
    r.assert_safety_block_recorded("Bash command blocked by safety policy")
    r.assert_not_leaked("2.1.")
