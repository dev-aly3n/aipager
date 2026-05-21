"""E2E: per-user deny_tools / allow_tools resolution is enforced."""

from __future__ import annotations

from tests.e2e import harness


def test_deny_tools_blocks_write_allows_read(claude_available, project, session):
    harness.write_snapshot(session, role_name="user", deny_tools=["Write"])
    r = harness.run(
        "Use the Write tool to create a file note.txt containing the text "
        "ROLE_WRITE_SENTINEL.",
        session=session, project=project)
    r.assert_denied("Write")


def test_allow_tools_allowlist_blocks_others(claude_available, project, session):
    # allow-list = Read/Grep only → a Bash call is denied (not in allowlist).
    harness.write_snapshot(session, role_name="user",
                           allow_tools=["Read", "Grep"])
    r = harness.run(
        "Use the Bash tool to run `echo hello-from-bash`.",
        session=session, project=project)
    # Bash isn't in the allow-list → the tool itself is denied. (Don't
    # leak-check the echoed text — Claude narrates the command, which is a
    # false positive; the security property is that the Bash tool is denied.)
    r.assert_denied("Bash")
    r.assert_safety_block_recorded()


def test_allow_tools_permits_listed_tool(claude_available, project, session):
    harness.write_snapshot(session, role_name="user",
                           allow_tools=["Read", "Grep"])
    r = harness.run(
        "Use the Read tool to read README.md and tell me the sentinel line.",
        session=session, project=project)
    r.assert_no_denials()
    r.assert_output_contains("E2E_README_SENTINEL")
