"""The tree in fa and ar (design.md success criterion 13; entrypoints.md
"fa (right to left) block"): every list line is "- " then the start
mark, no line is indented, no two marks touch, every line with a
Persian/Arabic letter sits right on iPhone, and the divider between the
answer and the block is bare."""

from __future__ import annotations

import time

import pytest

from aipager import i18n, preferences
from aipager.bot.agents_line import AgentRow, BlockView, render_block
from aipager.bot.divider import DIVIDER_SEP
from tests import answer_status_bb as bb
from tests import bidi_uba

RLM, LRM = "\u200f", "\u200e"
MARKS = (RLM, LRM)
RTL = ["fa", "ar"]


def _views():
    return {
        "example": BlockView(agents=(
            AgentRow("pipeline-runner", False, prose="Writing final.md for the pipeline"),
            AgentRow("r2-runner", False, waiting=True),
            AgentRow("ship-tester", False, parent=0, tool="Read", tool_arg="state.py"),
            AgentRow("ship-developer", False, parent=0, description="Editing state.py"),
            AgentRow("Explore", True, 42.0, parent=1),
            AgentRow("ship-reviewer", False, parent=3, waiting=True))),
        "deep": BlockView(agents=(
            AgentRow("pipeline-runner", False),
            AgentRow("a1", False, parent=0),
            AgentRow("a2", False, parent=1, tool="Bash", tool_arg="pytest -q"),
            AgentRow("a3", True, 61.0, parent=2))),
        "capped": BlockView(agents=(AgentRow("pipeline-runner", False),)
                            + tuple(AgentRow(f"h{i}", False, parent=0) for i in range(9)),
                            shells_running=2),
        "persian-prose": BlockView(agents=(
            AgentRow("pipeline-runner", False,
                     prose="\u062f\u0631 \u062d\u0627\u0644 \u0646\u0648\u0634\u062a\u0646"),
            AgentRow("ship-tester", False, parent=0,
                     prose="\u0628\u0631\u0631\u0633\u06cc \u0641\u0627\u06cc\u0644"))),
    }


VIEWS = sorted(_views())


def _render(view, code, rtl):
    return render_block(_views()[view], i18n.get(code).directed(rtl))


def _items(block):
    return [x for x in block.split("\n") if x.startswith("- ")]


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_the_tree_has_list_items(view, code):
    assert _items(_render(view, code, True))


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_every_list_line_is_marker_then_rlm(view, code):
    assert all(x[2] == RLM for x in _items(_render(view, code, True)))


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_a_left_to_right_drawing_puts_its_start_mark_after_the_marker(view, code):
    block = _render(view, code, False)
    start = block[0]
    assert (start in MARKS, all(x[2] == start for x in _items(block))) == (True, True)


@pytest.mark.parametrize("rtl", [True, False], ids=["rtl", "ltr"])
@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_no_line_is_indented(view, code, rtl):
    block = _render(view, code, rtl)
    assert [x for x in block.split("\n") if x[:1] in (" ", "\t")] == []


@pytest.mark.parametrize("rtl", [True, False], ids=["rtl", "ltr"])
@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_no_two_marks_touch(view, code, rtl):
    block = _render(view, code, rtl)
    assert not any(a in MARKS and b in MARKS for a, b in zip(block, block[1:]))


@pytest.mark.parametrize("rtl", [True, False], ids=["rtl", "ltr"])
@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_no_mark_before_the_list_marker(view, code, rtl):
    block = _render(view, code, rtl)
    assert [x for x in block.split("\n") if x[:1] in MARKS and x[1:3] == "- "] == []


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_every_right_to_left_line_sits_right_on_iphone(view, code):
    block = _render(view, code, True)
    bad = [line for line in bidi_uba.shown_lines(block)
           if bidi_uba.has_rtl_letter(line) and not bidi_uba.sits_right_on_iphone(line)]
    assert bad == []


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("code", RTL)
def test_every_list_line_without_its_marker_sits_right_on_iphone(view, code):
    block = _render(view, code, True)
    bad = [x for x in _items(block)
           if bidi_uba.has_rtl_letter(bidi_uba.shown_text(x[2:]))
           and not bidi_uba.sits_right_on_iphone(bidi_uba.shown_text(x[2:]))]
    assert bad == []


@pytest.mark.parametrize("code", RTL)
def test_the_waiting_row_is_translated(code):
    block = _render("example", code, True)
    assert "waiting for its own agents" not in block


@pytest.mark.parametrize("code", RTL)
def test_the_depth_three_hint_reads_left_to_right(code):
    """Two agent names joined by the glyph, in logical order."""
    block = _render("example", code, True)
    plain = block.replace("\u2068", "").replace("\u2069", "")
    assert "ship-developer \u203a ship-reviewer" in plain


# ---- end to end: a right-to-left answer with agents ----------------------------------------

PERSIAN_ANSWER = "\u0627\u06cc\u0646 \u067e\u0627\u0633\u062e \u0627\u0633\u062a."
ARABIC_ANSWER = "\u0647\u0630\u0627 \u0647\u0648 \u0627\u0644\u062c\u0648\u0627\u0628."
ANSWERS = {"fa": PERSIAN_ANSWER, "ar": ARABIC_ANSWER}


def _sent(bot, sess, wire, run_async, code):
    preferences.set_ui_language(bb.CHAT, code)
    now = time.monotonic()
    sess.bg_agent_started("a505c081ca3926ed5", "pipeline-runner", now)
    sess.bg_agent_started("ac1d54c85d78f59ea", "ship-tester", now)
    sess.agent_launched_child("a505c081ca3926ed5", "ac1d54c85d78f59ea", now)
    bb.finish(run_async, bot, sess, ANSWERS[code])
    return bb.answer_text(wire, ANSWERS[code])


@pytest.mark.parametrize("code", RTL)
def test_the_divider_line_is_bare(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    assert "---" in text.split("\n")


@pytest.mark.parametrize("code", RTL)
def test_the_divider_is_its_own_paragraph(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    assert DIVIDER_SEP in text


@pytest.mark.parametrize("code", RTL)
def test_no_line_carries_a_mark_and_the_divider(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    assert [x for x in text.split("\n") if "---" in x and x != "---"] == []


@pytest.mark.parametrize("code", RTL)
def test_the_sent_message_has_no_two_marks_touching(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    assert not any(a in MARKS and b in MARKS for a, b in zip(text, text[1:]))


@pytest.mark.parametrize("code", RTL)
def test_the_sent_helper_line_is_marker_then_rlm(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    items = _items(text)
    assert len(items) == 1 and items[0][2] == RLM


@pytest.mark.parametrize("code", RTL)
def test_every_sent_right_to_left_line_sits_right(bot, sess, wire, run_async, code):
    text = _sent(bot, sess, wire, run_async, code)
    bad = [line for line in bidi_uba.shown_lines(text)
           if bidi_uba.has_rtl_letter(line) and not bidi_uba.sits_right_on_iphone(line)]
    assert bad == []
