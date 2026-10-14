"""Black-box: ``Tr.line``, ``Tr.bare``, ``Tr.lead`` and ``Tr.mark``
(entrypoints.md), and the helpers that nest rendered pieces
(``settings_menu.row_text``, ``sibling_text.shown``,
``report_offer.notice_text``)."""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot import report_offer, settings_menu, sibling_text
from aipager.i18n import Msg, Safe

RLM, LRM = "\u200f", "\u200e"
RTL = ("fa", "ar", "qps-rtl")
_PAIR = re.compile("[\u200e\u200f]{2}")


def _ltr_trs():
    return [i18n.get("en"), i18n.get("ru"), i18n.get("fa").directed(False),
            i18n.get("ar").directed(False)]


_LTR_IDS = ["en", "ru", "fa-twin", "ar-twin"]


# ---- Tr.line ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", RTL)
def test_line_marks_both_ends(code):
    assert i18n.get(code).line("a") == RLM + "a" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_collapses_the_example_from_the_entrypoints(code):
    assert i18n.get(code).line(RLM + "a" + RLM + RLM + "b" + RLM) == RLM + "a" + RLM + "b" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_does_not_double_existing_end_marks(code):
    assert i18n.get(code).line(RLM + RLM + "a" + RLM + RLM) == RLM + "a" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_keeps_a_safe_a_safe(code):
    assert type(i18n.get(code).line(Safe("<b>a</b>"))) is Safe


@pytest.mark.parametrize("code", RTL)
def test_line_of_a_safe_marks_both_ends(code):
    assert i18n.get(code).line(Safe("<b>a</b>")) == RLM + "<b>a</b>" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_keeps_a_str_a_str(code):
    assert type(i18n.get(code).line("a")) is str


@pytest.mark.parametrize("tr", _ltr_trs(), ids=_LTR_IDS)
def test_line_is_a_no_op_outside_a_right_to_left_rendering(tr):
    text = RLM + "a · b" + LRM
    assert tr.line(text) == text


@pytest.mark.parametrize("tr", _ltr_trs(), ids=_LTR_IDS)
def test_line_keeps_the_type_outside_a_right_to_left_rendering(tr):
    assert type(tr.line(Safe("x"))) is Safe


@pytest.mark.parametrize("code", RTL)
def test_line_of_empty_text_is_unchanged(code):
    assert i18n.get(code).line("") == ""


@pytest.mark.parametrize("code", RTL)
def test_line_of_blank_text_is_unchanged(code):
    assert i18n.get(code).line("   ") == "   "


@pytest.mark.parametrize("code", RTL)
def test_line_puts_the_start_mark_after_a_list_marker(code):
    assert i18n.get(code).line("- item") == "- " + RLM + "item" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_marks_only_the_outer_ends_of_multi_line_text(code):
    assert i18n.get(code).line("a\nb") == RLM + "a\nb" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_gives_no_start_mark_to_a_pre_opener(code):
    assert i18n.get(code).line("<pre>x</pre>") == "<pre>x</pre>"


@pytest.mark.parametrize("code", RTL)
def test_line_gives_no_marks_beside_a_blockquote(code):
    assert i18n.get(code).line("<blockquote>q</blockquote>") == "<blockquote>q</blockquote>"


@pytest.mark.parametrize("code", RTL)
def test_line_ending_in_a_closing_pre_gets_no_end_mark(code):
    assert i18n.get(code).line("a\n<pre>x</pre>") == RLM + "a\n<pre>x</pre>"


@pytest.mark.parametrize("code", RTL)
def test_line_collapses_an_inner_mixed_run_to_its_last_mark(code):
    assert i18n.get(code).line("a" + RLM + LRM + "b") == RLM + "a" + LRM + "b" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_never_leaves_a_pair_beside_a_trailing_lrm(code):
    assert _PAIR.search(i18n.get(code).line("a" + LRM)) is None


@pytest.mark.parametrize("code", RTL)
def test_line_of_a_rendered_line_is_stable(code):
    tr = i18n.get(code)
    once = tr.line(tr.text("zz.case", "{a} · {b}", a="frontend", b="12"))
    assert tr.line(once) == once


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_line_of_a_code_built_line_ending_in_a_clock_ends_right_to_left(code):
    tr = i18n.get(code)
    built = tr.text("zz.case", "working") + " · " + tr.clock(3990)
    assert tr.line(built).endswith(tr.clock(3990) + RLM)


# ---- Tr.bare -----------------------------------------------------------------------------

@pytest.mark.parametrize("code", RTL)
def test_bare_strips_both_end_runs(code):
    assert i18n.get(code).bare(RLM + RLM + "a" + RLM + "b" + RLM + RLM) == "a" + RLM + "b"


@pytest.mark.parametrize("code", RTL)
def test_bare_of_a_rendered_line_has_no_end_marks(code):
    tr = i18n.get(code)
    out = tr.bare(tr.text("zz.case", "hello {x}", x="frontend"))
    assert not out.startswith(RLM) and not out.endswith(RLM)


@pytest.mark.parametrize("code", RTL)
def test_bare_keeps_a_safe_a_safe(code):
    assert type(i18n.get(code).bare(Safe(RLM + "a" + RLM))) is Safe


@pytest.mark.parametrize("tr", _ltr_trs(), ids=_LTR_IDS)
def test_bare_is_a_no_op_outside_a_right_to_left_rendering(tr):
    assert tr.bare(RLM + "a" + RLM) == RLM + "a" + RLM


# ---- Tr.lead and Tr.mark --------------------------------------------------------------------

@pytest.mark.parametrize("code", RTL)
def test_lead_marks_the_start_only(code):
    assert i18n.get(code).lead("a") == RLM + "a"


@pytest.mark.parametrize("code", RTL)
def test_lead_of_a_rendered_field_drops_its_end_mark(code):
    tr = i18n.get(code)
    assert not tr.lead(tr.text("zz.case", "describe {x}", x="frontend")).endswith(RLM)


@pytest.mark.parametrize("code", RTL)
def test_lead_keeps_a_safe_a_safe(code):
    assert type(i18n.get(code).lead(Safe("a"))) is Safe


@pytest.mark.parametrize("tr", _ltr_trs(), ids=_LTR_IDS)
def test_lead_is_a_no_op_outside_a_right_to_left_rendering(tr):
    assert tr.lead("a") == "a"


@pytest.mark.parametrize("code", RTL)
def test_mark_is_rlm_in_a_right_to_left_rendering(code):
    assert i18n.get(code).mark == RLM


@pytest.mark.parametrize("tr", _ltr_trs(), ids=_LTR_IDS)
def test_mark_is_empty_outside_a_right_to_left_rendering(tr):
    assert tr.mark == ""


# ---- helpers that nest pieces ----------------------------------------------------------------

_TITLE = Msg("zz.title", "Title")
_VALUE = Msg("zz.value", "Value")


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_row_text_starts_and_ends_with_a_mark(code):
    out = settings_menu.row_text(i18n.get(code), _TITLE, _VALUE)
    assert out.startswith(RLM) and out.endswith(RLM)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_row_text_holds_no_adjacent_marks(code):
    assert _PAIR.search(settings_menu.row_text(i18n.get(code), _TITLE, _VALUE)) is None


def test_row_text_in_english_holds_no_mark():
    out = settings_menu.row_text(i18n.get("en"), _TITLE, _VALUE)
    assert RLM not in out and LRM not in out


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_shown_as_html_has_no_mark_at_either_end(code):
    out = str(sibling_text.shown(i18n.get(code), Msg("zz.shown", "a piece"), kind="html"))
    assert not out.startswith(RLM) and not out.endswith(RLM)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_shown_as_html_is_a_safe(code):
    out = sibling_text.shown(i18n.get(code), Msg("zz.shown", "a piece"), kind="html")
    assert isinstance(out, Safe)


_ENTRIES = (
    [{"trigger": "crash", "count": 1}],
    [{"trigger": "crash", "count": 3}],
    [{"trigger": "hook_cap", "count": 2}],
    [{"type": "aipager.error", "count": 1}],
    [{"type": "aipager.error", "count": 1}, {"type": "x.warning", "count": 2}],
)


@pytest.mark.parametrize("code", ("fa", "ar"))
@pytest.mark.parametrize("entries", _ENTRIES, ids=[f"e{i}" for i in range(len(_ENTRIES))])
def test_report_notice_holds_no_adjacent_marks(code, entries):
    assert _PAIR.search(report_offer.notice_text(entries, i18n.get(code))) is None


@pytest.mark.parametrize("code", ("fa", "ar"))
@pytest.mark.parametrize("entries", _ENTRIES, ids=[f"e{i}" for i in range(len(_ENTRIES))])
def test_report_notice_lines_end_with_a_mark(code, entries):
    out = report_offer.notice_text(entries, i18n.get(code))
    assert all(line.endswith(RLM) for line in out.split("\n") if line.strip())
