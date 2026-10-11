"""Success criteria 9 and 10 through the exported data and functions alone
(entrypoints.md): ``aipager.safety.REASON_SHAPES`` read as plain data
(each writer's sentence is fully matched first by its own code's pattern,
in the listed match order), and ``safety_notice.reason_text`` /
``notice_html`` returning the reason as received in English, as received
for an unknown reason in every language, and never anything but a str.

Written in iteration 2 after the two iteration-1 failures (``reason_text``
returned None; ``REASON_SHAPES`` lacked ``halted`` and ``unchecked``)."""

from __future__ import annotations

import html
import re

import pytest
import qa_p3a_scenarios as S

from aipager import i18n, safety
from aipager.bot import safety_notice

LANGS = ("en", "fa", "ru")
KINDS = ("html", "text")

#: The code each writer's sentence (S.safety_reasons(), in order) is, per
#: design.md section B's table (same order as test_qa_safety_notice).
EXPECTED_CODES = [
    "bash", "bash_search", "tool_not_allowed", "tool_denied", "path_unreadable",
    "path_protected", "path_protected_write", "runs_commands", "outside_write",
    "wide_cwd_write", "outside_search", "wide_cwd_search", "search_folder_unreadable",
    "path_protected", "search_could_reach", "search_reaches", "glob_unreadable",
    "glob_escaped", "glob_not_plain", "glob_climbs", "search_folder_unreadable",
    "pattern_unreadable", "search_anywhere", "search_protected_files"]

WRITERS = S.safety_reasons()
IDS = [f"{i}-{code}" for i, code in enumerate(EXPECTED_CODES)]

HALTED = ["session halted", "session halted after a safety block; resume to continue",
          "session halted\nby a block earlier"]

#: Parsed shapes whose tool or protected path carries HTML metacharacters.
MARKUP_KNOWN = ["Read on protected path /a/<b>&/**",
                "Write write to protected path /srv/<i>x</i>&y/**",
                "Grep search reaches protected path **/a&b<c>.env",
                "Edit<x> with an unreadable path"]

UNKNOWN = ["the moon is <b>full</b> & bright", "error 42 at line 7 (code 0x1F)",
           "line one\nline <two> & three", "Read with an unreadable path.",
           "Session halted by someone"]


def _fail_closed_reason() -> str:
    from aipager.dtach import enforce
    closed = enforce.fail_closed({"session": "claude-x__g100", "tool_name": "Bash"}) or {}
    return closed.get("reason", "")


def _first_shape(reason: str):
    """The code of the first REASON_SHAPES pattern that matches *reason*
    whole (patterns are anchored; design.md: ``re.S`` for paths)."""
    for code, pattern in safety.REASON_SHAPES:
        if re.fullmatch(pattern, reason, re.S):
            return code
    return None


# ---- REASON_SHAPES as plain data ------------------------------------------------------

def test_reason_shapes_is_a_tuple():
    assert isinstance(safety.REASON_SHAPES, tuple)


@pytest.mark.parametrize("k", range(len(safety.REASON_SHAPES)))
def test_each_reason_shape_is_a_code_and_a_pattern(k):
    entry = safety.REASON_SHAPES[k]
    assert (isinstance(entry, tuple) and len(entry) == 2
            and all(isinstance(part, str) for part in entry))


@pytest.mark.parametrize("i", range(len(WRITERS)), ids=IDS)
def test_the_first_shape_matching_a_writer_is_its_own(i):
    assert _first_shape(WRITERS[i][1]) == EXPECTED_CODES[i]


@pytest.mark.parametrize("reason", HALTED)
def test_the_first_shape_matching_a_halted_repeat_is_halted(reason):
    assert _first_shape(reason) == "halted"


def test_the_first_shape_matching_the_fail_closed_reason_is_unchecked():
    assert _first_shape(_fail_closed_reason()) == "unchecked"


@pytest.mark.parametrize("reason", UNKNOWN + ["", " session halted"])
def test_no_shape_matches_a_sentence_aipager_does_not_write(reason):
    assert _first_shape(reason) is None


def test_the_fail_closed_reason_is_not_sticky():
    assert safety_notice.is_sticky(_fail_closed_reason()) is False


# ---- reason_text always returns a str ------------------------------------------------

ALL_REASONS = ([r for _, r in WRITERS] + HALTED + UNKNOWN + MARKUP_KNOWN
               + [_fail_closed_reason(), ""])
STR_CASES = [(lang, kind, k) for lang in LANGS for kind in KINDS
             for k in range(len(ALL_REASONS))]


@pytest.mark.parametrize("lang,kind,k", STR_CASES)
def test_reason_text_always_returns_a_str(lang, kind, k):
    assert isinstance(safety_notice.reason_text(i18n.get(lang), ALL_REASONS[k], kind=kind), str)


# ---- English: byte for byte as received -----------------------------------------------

@pytest.mark.parametrize("reason", MARKUP_KNOWN)
def test_markup_shapes_still_parse(reason):
    """Precondition for the two tests below: these ARE recognised reasons."""
    assert safety_notice.parse_reason(reason) is not None


@pytest.mark.parametrize("reason", MARKUP_KNOWN)
def test_english_html_of_a_parsed_reason_with_markup_is_it_escaped(reason):
    assert safety_notice.reason_text(i18n.get("en"), reason, kind="html") == html.escape(reason)


@pytest.mark.parametrize("reason", MARKUP_KNOWN)
def test_english_text_of_a_parsed_reason_with_markup_is_it_raw(reason):
    assert safety_notice.reason_text(i18n.get("en"), reason, kind="text") == reason


@pytest.mark.parametrize("reason", MARKUP_KNOWN)
def test_html_is_the_default_kind(reason):
    tr = i18n.get("en")
    assert safety_notice.reason_text(tr, reason) == safety_notice.reason_text(tr, reason,
                                                                               kind="html")


@pytest.mark.parametrize("reason", HALTED + [_fail_closed_reason()])
def test_english_text_of_an_enforce_reason_is_it_raw(reason):
    assert safety_notice.reason_text(i18n.get("en"), reason, kind="text") == reason


@pytest.mark.parametrize("reason", MARKUP_KNOWN)
def test_english_notice_of_a_markup_reason_is_todays_frame(reason):
    assert safety_notice.notice_html(i18n.get("en"), "x1", reason) == (
        f"🛑 <b>x1</b> · Blocked by safety policy: {html.escape(reason)} (stopped)")


# ---- Persian and Russian: kind="text" is not escaped ------------------------------------

@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_translated_text_kind_keeps_the_raw_path(lang):
    shown = safety_notice.reason_text(i18n.get(lang), MARKUP_KNOWN[0], kind="text")
    assert "/a/<b>&/**" in shown


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_translated_text_kind_has_no_html_entity(lang):
    shown = safety_notice.reason_text(i18n.get(lang), MARKUP_KNOWN[0], kind="text")
    assert "&amp;" not in shown and "&lt;" not in shown


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_translated_html_kind_escapes_a_markup_tool(lang):
    shown = safety_notice.reason_text(i18n.get(lang), MARKUP_KNOWN[3], kind="html")
    assert "Edit&lt;x&gt;" in shown


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_translated_html_kind_leaves_no_raw_markup_tool(lang):
    shown = safety_notice.reason_text(i18n.get(lang), MARKUP_KNOWN[3], kind="html")
    assert "<x>" not in shown


# ---- unknown reasons through notice_html -----------------------------------------------

NOTICE_UNKNOWN = [(lang, r) for lang in LANGS for r in UNKNOWN]


@pytest.mark.parametrize("lang,reason", NOTICE_UNKNOWN)
def test_a_notice_carries_an_unknown_reason_escaped_and_whole(lang, reason):
    shown = safety_notice.notice_html(i18n.get(lang), "x1", reason)
    assert html.escape(reason) in shown


@pytest.mark.parametrize("reason", UNKNOWN)
def test_an_english_notice_of_an_unknown_reason_is_todays_frame(reason):
    assert safety_notice.notice_html(i18n.get("en"), "x1", reason) == (
        f"🛑 <b>x1</b> · Blocked by safety policy: {html.escape(reason)} (stopped)")
