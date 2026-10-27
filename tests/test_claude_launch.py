"""What every launched session starts with (aipager/claude_launch.py)."""

from __future__ import annotations

import pytest

from aipager import claude_launch as cl

NAME_PROMPT = ('Your session name is "x1". When users address you by this name, '
               'respond naturally -- it is your name in this session.')


#: The Telegram note as the operator approved it (2026-10-27), typed here
#: apart from the module so a change to either one fails.
NOTE_WITH_SEND = (
    "Your answers are shown in Telegram as rich text. Markdown renders (headings, "
    "lists, links, code blocks). When a table helps, use a Markdown table, or an "
    "HTML <table> for a caption or merged cells: Telegram draws it natively and it "
    "scrolls sideways. Never draw tables or diagrams with box characters or spaces "
    "in a code block; Mermaid is not drawn. Images in Markdown are not shown: send "
    "images and files (screenshots, PDFs, anything) with the SendUserFile tool; if "
    "it is not in your tool list, load it with ToolSearch, and never say you cannot "
    "send files. A table over 20 columns or a few hundred rows goes as a CSV file "
    "instead. Side details can fold in <details><summary>\u2026</summary>\u2026</details>."
)
SEND_SENTENCE = (
    "Images in Markdown are not shown: send images and files (screenshots, PDFs, "
    "anything) with the SendUserFile tool; if it is not in your tool list, load it "
    "with ToolSearch, and never say you cannot send files. "
)
CSV_SENTENCE = "A table over 20 columns or a few hundred rows goes as a CSV file instead."
SPLIT_SENTENCE = "A table over 20 columns or a few hundred rows: split it."
NOTE_NO_SEND = NOTE_WITH_SEND.replace(SEND_SENTENCE, "").replace(CSV_SENTENCE,
                                                                 SPLIT_SENTENCE)


def test_the_notes_are_the_approved_text():
    assert cl.TELEGRAM_NOTE == NOTE_WITH_SEND
    assert cl.TELEGRAM_NOTE_NO_SEND == NOTE_NO_SEND
    assert NOTE_NO_SEND != NOTE_WITH_SEND and SPLIT_SENTENCE in NOTE_NO_SEND
    assert "SendUserFile" not in NOTE_NO_SEND and "CSV" not in NOTE_NO_SEND


@pytest.mark.parametrize("note", ["TELEGRAM_NOTE", "TELEGRAM_NOTE_NO_SEND"])
def test_the_note_is_plain_english(note):
    text = getattr(cl, note)
    assert "\u2014" not in text and "\u2013" not in text
    assert "..." not in text and text.count("\u2026") == 2
    assert cl.ATTACHMENTS_ENV == "CLAUDE_CODE_ATTACHMENTS_BY_PATH"


@pytest.mark.parametrize("version", ["2.1.301", "2.1.302", "2.2.0", "3.0.0", None, "",
                                     "unknown"])
def test_note_follows_the_name(version):
    assert cl.telegram_note(version) == NOTE_WITH_SEND
    assert cl.base_system_prompt("x1", version) == f"{NAME_PROMPT}\n\n{NOTE_WITH_SEND}"


@pytest.mark.parametrize("version", ["2.1.300", "2.1.235", "1.9.999"])
def test_note_without_the_send_sentence_for_claude_before_2_1_301(version):
    assert cl.telegram_note(version) == NOTE_NO_SEND
    assert cl.base_system_prompt("x1", version) == f"{NAME_PROMPT}\n\n{NOTE_NO_SEND}"


# ---- the recorded system prompt is not replayed (8.125) ---------------------

@pytest.mark.parametrize("version", ["2.1.259", "2.1.267", "2.1.310", "2.2.0", "3.0.0",
                                     "2.1.310 (Claude Code)"])
def test_snapshot_off_for_a_claude_that_takes_the_flag(version):
    assert cl.snapshot_off_args(version) == ("--system-prompt-snapshot", "off")


@pytest.mark.parametrize("version", ["2.1.258", "2.1.235", "1.9.999", None, "", "unknown"])
def test_no_snapshot_flag_before_2_1_259_or_when_unknown(version):
    """An older Claude Code refuses an unknown flag and never starts."""
    assert cl.snapshot_off_args(version) == ()
