"""What every launched session starts with (aipager/claude_launch.py)."""

from __future__ import annotations

import pytest

from aipager import claude_launch as cl

NAME_PROMPT = ('Your session name is "x1". When users address you by this name, '
               'respond naturally -- it is your name in this session.')


def test_the_note_is_plain_english():
    assert "—" not in cl.SEND_FILES_NOTE
    assert "SendUserFile" in cl.SEND_FILES_NOTE and "ToolSearch" in cl.SEND_FILES_NOTE
    assert cl.ATTACHMENTS_ENV == "CLAUDE_CODE_ATTACHMENTS_BY_PATH"


@pytest.mark.parametrize("version", ["2.1.301", "2.1.302", "2.2.0", "3.0.0", None, "",
                                     "unknown"])
def test_note_follows_the_name(version):
    assert cl.base_system_prompt("x1", version) == f"{NAME_PROMPT}\n\n{cl.SEND_FILES_NOTE}"


@pytest.mark.parametrize("version", ["2.1.300", "2.1.235", "1.9.999"])
def test_note_omitted_for_claude_before_2_1_301(version):
    assert cl.base_system_prompt("x1", version) == NAME_PROMPT


# ---- the recorded system prompt is not replayed (8.125) ---------------------

@pytest.mark.parametrize("version", ["2.1.259", "2.1.267", "2.1.310", "2.2.0", "3.0.0",
                                     "2.1.310 (Claude Code)"])
def test_snapshot_off_for_a_claude_that_takes_the_flag(version):
    assert cl.snapshot_off_args(version) == ("--system-prompt-snapshot", "off")


@pytest.mark.parametrize("version", ["2.1.258", "2.1.235", "1.9.999", None, "", "unknown"])
def test_no_snapshot_flag_before_2_1_259_or_when_unknown(version):
    """An older Claude Code refuses an unknown flag and never starts."""
    assert cl.snapshot_off_args(version) == ()
