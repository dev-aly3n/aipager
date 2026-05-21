"""E2E: benign Telegram-driven work is NOT over-blocked (no false positives)."""

from __future__ import annotations

from tests.e2e import harness


def test_benign_read_allowed(claude_available, project, session):
    harness.write_snapshot(session, role_name="user")
    r = harness.run(
        "Use the Read tool to read README.md and quote the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_output_contains("E2E_README_SENTINEL")


def test_benign_multistep_allowed(claude_available, project, session):
    """Multiple allowed tool calls in one turn must all go through —
    sticky only triggers AFTER a real block."""
    harness.write_snapshot(session, role_name="user")
    r = harness.run(
        "Use Bash to run `ls`, then use the Read tool to read README.md "
        "and quote the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_output_contains("E2E_README_SENTINEL")
