"""E2E: benign Telegram-driven work is NOT over-blocked (no false
positives) for the built-in ``user`` role: reading and searching inside
the session's project. Posts nothing to Telegram."""

from __future__ import annotations

from tests.e2e import harness


def test_benign_read_allowed(claude_available, project, session):
    harness.write_snapshot(session, role_name="user")
    r = harness.run(
        "Use the Read tool to read README.md and quote the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_ran("Read")
    r.assert_output_contains("E2E_README_SENTINEL")


def test_benign_multistep_allowed(claude_available, project, session):
    """Several allowed tool calls in one turn all go through — sticky
    only triggers AFTER a real block. (Bash is not among them: the
    built-in ``user`` role has no shell.)"""
    harness.write_snapshot(session, role_name="user")
    r = harness.run(
        "Use the Grep tool to search this folder for the text E2E_README, "
        "then use the Read tool to read README.md and quote the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_ran("Grep")
    r.assert_ran("Read")
    r.assert_output_contains("E2E_README_SENTINEL")


def test_benign_write_in_project_allowed(claude_available, project, session):
    """A ``user`` turn may write inside its own project (roadmap 8.50
    confines writes to the project and scratchpad, it does not forbid
    them)."""
    harness.write_snapshot(session, role_name="user")
    r = harness.run(
        "Use the Write tool to create the file note.txt in the current folder "
        "containing exactly: E2E_WRITE_OK",
        session=session, project=project)
    r.assert_ran("Write")
    assert (project / "note.txt").read_text().strip() == "E2E_WRITE_OK"
