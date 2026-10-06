"""E2E (roadmap 8.74): a slash command sent from Telegram is held to its
sender's rules; the same command typed in the terminal is not.

A slash command is typed raw (no ``[via Telegram]`` marker), and Claude
Code records it as a ``<command-name>/x</command-name>`` prompt with the
expanded text in a separate meta entry. The hook calls such a prompt
Telegram when the scope-mode snapshot's ``note_bodies`` (the Telegram
messages its ``UserPromptSubmit`` consumed) name ``/x``. These tests run a
real project command (``.claude/commands/e2ecmd.md``) through
``claude -p "/e2ecmd"`` and check that record assumption against the real
Claude Code: when no ``<command-name>/e2ecmd`` record is written, they
SKIP with what was observed instead of passing on a guess.

Posts nothing to Telegram.
"""

from __future__ import annotations

import pytest

from tests.e2e import harness

COMMAND = "/e2ecmd"
# Only running the command shows this (Claude cannot work out a sha256).
_RAN = "E2E_CMD_RAN_a2a9179a"
_COMMAND_BODY = (
    "This is a sandbox test of a project command. Use the Bash tool to run "
    "exactly this command once and report its output verbatim:\n"
    "echo E2E_CMD_RAN_$(printf %s aipager-e2e-cmd-7 | sha256sum | cut -c1-8)\n"
)


@pytest.fixture
def cmd_project(tmp_path):
    return harness.make_project(tmp_path, commands={"e2ecmd": _COMMAND_BODY})


def _require_command_record(r: harness.ClaudeRun) -> None:
    prompts = r.user_prompt_texts()
    record = f"<command-name>{COMMAND}</command-name>"
    if not any(record in p for p in prompts):
        seen = [p[:160] for p in prompts[:3]]
        pytest.skip(f"claude -p {COMMAND!r} wrote no {record} prompt record "
                    f"(8.74's assumption not observable this way); prompts seen: "
                    f"{seen!r}; result: {r.result[:160]!r}")


def test_telegram_slash_command_held_to_sender_rules(claude_available, cmd_project,
                                                     session):
    """The command came from a restricted member in a group (its note's
    body is ``/e2ecmd``): the Bash call the command makes is denied."""
    snap = harness.write_snapshot(session, role_name="user", body=COMMAND)
    assert snap["scope_mode"] is True and COMMAND in snap["note_bodies"], snap
    r = harness.run(COMMAND, session=session, project=cmd_project, marker=False)
    _require_command_record(r)
    r.assert_denied("Bash")
    r.assert_safety_block_recorded("Bash is in this role's deny_tools")
    r.assert_not_leaked(_RAN)


def test_terminal_slash_command_unrestricted(claude_available, cmd_project, session):
    """The same command typed in the terminal after a restricted member's
    Telegram turn: no Telegram body names it, so it runs."""
    harness.write_snapshot(session, role_name="user")       # an earlier Telegram turn
    snap = harness.write_terminal_prompt(session, COMMAND)  # then the terminal types it
    assert snap["scope_mode"] is True and COMMAND not in snap["note_bodies"], snap
    r = harness.run(COMMAND, session=session, project=cmd_project, marker=False)
    _require_command_record(r)
    r.assert_ran("Bash")
    r.assert_output_contains(_RAN)
