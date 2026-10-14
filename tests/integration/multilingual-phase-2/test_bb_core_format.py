"""Black-box: the i18n core's Persian and Russian formatting
(entrypoints.md "Exported functions": ``Span``, ``Command``, ``Tr.num``,
RTL rendering; ``bidi_guard.strip_own_marks``).

Partitions: language = en / ru / fa; parameter kind = plain text, Command,
Span, pre-formatted number string; line start = plain / Markdown block
marker. Boundaries: zero, a money string with group and decimal
separators, a percentage. Error guessing: markup in a text parameter, a
mark landing between ``/`` and a session name or inside a code element.
"""

from __future__ import annotations

import pytest

from aipager import bidi_guard, i18n
from aipager.bot import animation
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    FSI,
    PDI,
    RLM,
    catalog,
)

TALK = i18n.Msg("cmd.talk", "Talk to {session}")


def _fa_talk():
    return catalog("fa")["cmd.talk"]["v"]


# ---- Tr.num ---------------------------------------------------------------------

def test_num_converts_money_to_persian_digits_and_separators():
    assert i18n.get("fa").num("$1,234.50") == "$۱٬۲۳۴٫۵۰"


def test_num_converts_a_percentage_to_persian():
    assert i18n.get("fa").num("45%") == "۴۵٪"


def test_num_converts_zero_to_persian():
    assert i18n.get("fa").num("0") == "۰"


def test_num_leaves_english_money_alone():
    assert i18n.get("en").num("$1,234.50") == "$1,234.50"


def test_num_leaves_a_russian_percentage_alone():
    assert i18n.get("ru").num("45%") == "45%"


def test_num_leaves_russian_money_alone():
    assert i18n.get("ru").num("$1,234.50") == "$1,234.50"


# ---- RTL marks and isolates -----------------------------------------------------

def test_a_persian_line_starts_with_the_rlm():
    assert i18n.get("fa").html(TALK, session="x1").startswith(RLM)


def test_a_persian_text_parameter_is_isolated_whole():
    out = i18n.get("fa").html(TALK, session="x1")
    assert out == RLM + _fa_talk().replace("{session}", FSI + "x1" + PDI) + RLM


def test_a_session_name_keeps_latin_digits_in_persian():
    assert FSI + "x12" + PDI in i18n.get("fa").html(TALK, session="x12")


def test_a_command_keeps_its_slash_next_to_the_name():
    out = i18n.get("fa").html(TALK, session=i18n.Command("/x12"))
    assert "/x12" in out


def test_a_command_is_isolated_as_one_unit():
    out = i18n.get("fa").html(TALK, session=i18n.Command("/x12"))
    assert FSI + "/x12" + PDI in out


def test_a_command_is_escaped_like_text():
    out = i18n.get("en").html(TALK, session=i18n.Command("@a<b"))
    assert out == "Talk to @a&lt;b"


def test_a_span_is_never_escaped_or_digit_converted():
    span = "<code>a &lt; 12</code>"
    out = i18n.get("fa").html(TALK, session=i18n.Span(span))
    assert span in out


def test_a_span_outside_a_tag_is_isolated_whole():
    span = "<code>x1</code>"
    out = i18n.get("fa").html(TALK, session=i18n.Span(span))
    assert FSI + span + PDI in out


def test_a_text_parameter_is_escaped_in_persian_html():
    out = i18n.get("fa").html(TALK, session="<b>&")
    assert "&lt;b&gt;&amp;" in out


@pytest.mark.parametrize("code", ["en", "ru"])
def test_left_to_right_languages_get_no_marks(code):
    out = i18n.get(code).html(TALK, session=i18n.Command("/x1"))
    assert not BIDI_CONTROLS.search(out)


def test_english_is_the_source_text_exactly():
    assert i18n.get("en").html(TALK, session="x1") == "Talk to x1"


# ---- Markdown block markers (the full log) -------------------------------------

def _log(code):
    return animation.build_full_log(
        "x12", [("Read: /tmp/a1.py", True)], "", "the answer", tr=i18n.get(code))


def test_the_persian_log_title_keeps_its_heading_marker_first():
    first = _log("fa").split("\n", 1)[0]
    assert first.startswith("# " + RLM)


def test_every_persian_log_heading_has_the_mark_after_the_marker():
    heads = [ln for ln in _log("fa").splitlines() if ln.startswith("#")]
    assert heads and all(RLM in ln.split(" ", 1)[1][:1] for ln in heads)


def test_the_persian_log_keeps_the_session_name_latin():
    assert "x12" in _log("fa").split("\n", 1)[0]


def test_the_persian_log_tool_row_is_untouched():
    assert "`Read: /tmp/a1.py`" in _log("fa")


def test_the_russian_log_has_no_marks():
    assert not BIDI_CONTROLS.search(_log("ru"))


def test_the_english_log_title_is_unchanged():
    assert _log("en").split("\n", 1)[0] == "# x12 - full log"


# ---- strip_own_marks -------------------------------------------------------------

def test_strip_own_marks_removes_the_three_aipager_marks():
    assert bidi_guard.strip_own_marks(RLM + "a" + FSI + "b" + PDI) == "ab"


@pytest.mark.parametrize("foreign", ["\u202e", "\u202a", "\u202b", "\u202d",
                                     "\u2066", "\u2067", "\u200e", "\u061c"])
def test_strip_own_marks_keeps_every_other_control(foreign):
    assert bidi_guard.strip_own_marks("a" + foreign + "b") == "a" + foreign + "b"


@pytest.mark.parametrize("value", [None, 5, b"\xe2\x80\x8f"])
def test_strip_own_marks_returns_non_strings_as_they_are(value):
    assert bidi_guard.strip_own_marks(value) is value


def test_strip_own_marks_on_empty_text():
    assert bidi_guard.strip_own_marks("") == ""
