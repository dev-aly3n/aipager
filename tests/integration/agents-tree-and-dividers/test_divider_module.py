"""``aipager.bot.divider``: the one source of the divider and its plain
stand-in (entrypoints.md "Exported functions"; design.md success
criteria 11 and 12)."""

from __future__ import annotations

import pytest

from aipager.bot.divider import DIVIDER, DIVIDER_SEP, PLAIN_DIVIDER, without_dividers

DASHES = "\u2015" * 13


def test_divider_is_three_hyphens():
    assert DIVIDER == "---"


def test_divider_sep_has_a_blank_line_on_both_sides():
    assert DIVIDER_SEP == "\n\n---\n\n"


def test_plain_divider_is_the_old_dash_row():
    assert PLAIN_DIVIDER == DASHES


# ---- the two examples entrypoints.md gives ----------------------------------------

def test_a_divider_paragraph_is_removed():
    assert without_dividers("a\n\n---\n\nb") == "a\n\nb"


def test_a_divider_paragraph_is_swapped_for_the_plain_row():
    assert without_dividers("a\n\n---\n\nb", PLAIN_DIVIDER) == f"a\n\n{DASHES}\n\nb"


# ---- equivalence classes: what is NOT a divider paragraph stays ----------------------

@pytest.mark.parametrize("text", [
    "a\n---",            # a setext underline, not its own paragraph
    "----",              # four hyphens
    "- ---",             # a list item
    "a\n\n----\n\nb",
    "a\n\n --- \n\nb",   # spaces around: not exactly "---"
    "a\n\n---x\n\nb",
    "a\n\n---\nb",       # the paragraph is "---\nb"
    "",
    "no divider here",
], ids=["setext", "four", "list-item", "four-para", "spaced", "suffixed",
        "glued-below", "empty", "plain"])
def test_text_without_a_divider_paragraph_is_untouched(text):
    assert without_dividers(text) == text


@pytest.mark.parametrize("text", [
    "a\n---", "----", "- ---", "a\n\n --- \n\nb",
], ids=["setext", "four", "list-item", "spaced"])
def test_text_without_a_divider_paragraph_is_untouched_with_a_stand_in(text):
    assert without_dividers(text, PLAIN_DIVIDER) == text


# ---- boundaries: first, last, only, several -----------------------------------------

def test_two_dividers_are_both_removed():
    assert without_dividers("a\n\n---\n\nb\n\n---\n\nc") == "a\n\nb\n\nc"


def test_two_dividers_are_both_swapped():
    assert without_dividers("a\n\n---\n\nb\n\n---\n\nc", PLAIN_DIVIDER) == \
        f"a\n\n{DASHES}\n\nb\n\n{DASHES}\n\nc"


def test_a_leading_divider_is_removed():
    assert without_dividers("---\n\nb") == "b"


def test_a_trailing_divider_is_removed():
    assert without_dividers("a\n\n---") == "a"


def test_a_lone_divider_is_removed():
    assert without_dividers("---") == ""


def test_a_card_shape_loses_its_divider_line():
    card = "Read: /a.py\n\n---\n\n\u2705 **bbx** \u00b7 Done"
    assert "---" not in without_dividers(card).split("\n")


def test_a_card_shape_keeps_one_blank_line_before_its_status():
    card = "Read: /a.py\n\n---\n\n\u2705 **bbx** \u00b7 Done"
    assert without_dividers(card) == "Read: /a.py\n\n\u2705 **bbx** \u00b7 Done"
