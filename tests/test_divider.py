"""The one divider aipager writes in a rich message (aipager/bot/divider.py)
and its plain-text form: a ``---`` paragraph goes, or becomes a text line;
any other ``---`` is Claude's and stays."""

from __future__ import annotations

import pytest

from aipager.bot.divider import DIVIDER, DIVIDER_SEP, PLAIN_DIVIDER, without_dividers


def test_the_divider_is_its_own_paragraph():
    # A blank line on both sides: right under a line, "---" would make that
    # line a heading.
    assert DIVIDER == "---"
    assert DIVIDER_SEP == "\n\n---\n\n"


def test_a_divider_paragraph_is_dropped():
    assert without_dividers("a\n\n---\n\nb") == "a\n\nb"


def test_a_divider_paragraph_becomes_the_plain_line():
    assert without_dividers("a\n\n---\n\nb", PLAIN_DIVIDER) == f"a\n\n{PLAIN_DIVIDER}\n\nb"


def test_every_divider_goes():
    text = f"card{DIVIDER_SEP}status{DIVIDER_SEP}answer{DIVIDER_SEP}block"
    assert without_dividers(text) == "card\n\nstatus\n\nanswer\n\nblock"
    assert without_dividers(text, "~") == "card\n\n~\n\nstatus\n\n~\n\nanswer\n\n~\n\nblock"


def test_a_divider_at_either_end_goes():
    assert without_dividers("---\n\nb") == "b"
    assert without_dividers("a\n\n---") == "a"


@pytest.mark.parametrize("text", [
    "a\n---",            # a heading underline, not a paragraph of its own
    "a\n\n----\n\nb",    # four dashes
    "a\n\n- ---\n\nb",   # a list item
    "a\n\n--- x\n\nb",
    "a\n\n ---\n\nb",
    "```\n---\n```",
    "no divider at all",
], ids=["setext", "four", "list-item", "trailing-text", "indented", "fenced", "none"])
def test_any_other_dashes_stay_as_written(text):
    assert without_dividers(text) == text
    assert without_dividers(text, PLAIN_DIVIDER) == text


def test_the_plain_line_is_the_merged_layouts_old_row():
    assert PLAIN_DIVIDER == "―――――――――――――"
