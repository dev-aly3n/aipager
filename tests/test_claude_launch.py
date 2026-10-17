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
