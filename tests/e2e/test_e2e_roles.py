"""E2E: per-member deny_tools / allow_tools resolution is enforced
(``aipager.yaml`` member overrides on top of the role). Posts nothing to
Telegram."""

from __future__ import annotations

from tests.e2e import harness


def test_deny_tools_blocks_write(claude_available, project, session):
    harness.write_snapshot(session, role_name="user", deny_tools=["Write"])
    r = harness.run(
        "Use the Write tool to create a file note.txt containing the text "
        "ROLE_WRITE_SENTINEL.",
        session=session, project=project)
    r.assert_denied("Write")
    r.assert_safety_block_recorded("Write is in this role's deny_tools")
    assert not (project / "note.txt").exists()


def test_allow_tools_allowlist_blocks_others(claude_available, project, session):
    # allow-list = Read/Grep only → a Write (which the user role itself
    # allows inside the project) is denied as not in the allow-list.
    harness.write_snapshot(session, role_name="user",
                           allow_tools=["Read", "Grep"])
    r = harness.run(
        "Use the Write tool to create a file note.txt containing the text "
        "ALLOWLIST_SENTINEL.",
        session=session, project=project)
    r.assert_denied("Write")
    r.assert_safety_block_recorded("Write not in this role's allow_tools")
    assert not (project / "note.txt").exists()


def test_allow_tools_permits_listed_tool(claude_available, project, session):
    harness.write_snapshot(session, role_name="user",
                           allow_tools=["Read", "Grep"])
    r = harness.run(
        "Use the Read tool to read README.md and tell me the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_ran("Read")
    r.assert_output_contains("E2E_README_SENTINEL")
