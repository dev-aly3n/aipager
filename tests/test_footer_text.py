"""Small text under the dividers (operator, 2026-10-15): the busy card's
status line and every line of the agents block under an answer are
footer blocks (aipager/bot/footer.py), whose text is HTML."""

from __future__ import annotations

import time

import pytest

from aipager import i18n
from aipager.bot import agents_line as al
from aipager.bot import footer as ft
from aipager.bot.animation import build_stream_card
from aipager.state import TrackedSession
from tests.footer_text import as_md, status_md

RLM = "\u200f"


# ---- the conversion ------------------------------------------------------------------

@pytest.mark.parametrize("md, html", [
    ("plain", "plain"),
    ("⏳ **x1** · Working", "⏳ <b>x1</b> · Working"),
    (r"**a\*b** \_c\_ \`d\` \[e\] \\f", "<b>a*b</b> _c_ `d` [e] \\f"),
    ("a & b < c > d", "a &amp; b &lt; c &gt; d"),
    ("**one** and **two**", "<b>one</b> and <b>two</b>"),
    ("**open and never closed", "**open and never closed"),
    ("**a** then ** stray", "<b>a</b> then ** stray"),
    ("a lone \\ and * stay", "a lone \\ and * stay"),
    ("x <b>not markup</b>", "x &lt;b&gt;not markup&lt;/b&gt;"),
    (f"{RLM}⏳ **x1** · کار{RLM}", f"{RLM}⏳ <b>x1</b> · کار{RLM}"),
], ids=["plain", "bold", "escapes", "html-escaped", "two-bolds", "unpaired",
        "odd-stars", "lone-marks", "tags-are-text", "rtl-marks-kept"])
def test_markdown_to_footer_html(md, html):
    assert ft.md_to_html(md) == html
    assert ft.footer(md) == f"<footer>{html}</footer>"


@pytest.mark.parametrize("text, plain", [
    ("body\n\n---\n\n<footer>⏳ <b>x1</b> · a &amp; b</footer>", "body\n\n---\n\n⏳ x1 · a & b"),
    ("<footer>⏳ <b>x1</b></footer>", "⏳ x1"),
    ("<footer>Claude's</footer>\n\nlast words", "<footer>Claude's</footer>\n\nlast words"),
    ("a\n\n<footer>x</footer> and more", "a\n\n<footer>x</footer> and more"),
    ("no footer", "no footer"),
], ids=["status-last", "status-alone", "footer-not-last", "not-a-whole-paragraph", "none"])
def test_only_the_last_paragraphs_footer_is_made_plain(text, plain):
    assert ft.without_last_footer(text) == plain


# ---- the card's status line ------------------------------------------------------------

def _sess(label="x1"):
    s = TrackedSession(name=f"claude-{label}", label=label)
    s.busy_started_at = time.monotonic() - 10
    s.tool_history = [("Bash: ls", True)]
    return s


@pytest.mark.parametrize("kind", ["busy", "final", "waiting"])
def test_the_status_line_is_a_footer_on_every_frame(kind):
    sess = _sess()
    if kind == "waiting":
        sess.active_subagents = {"a1": {"type": "Explore",
                                        "started_at": time.monotonic() - 5,
                                        "history_idx": None}}
    card = build_stream_card(sess, "Working", final=kind == "final",
                             waiting=kind == "waiting")
    last = card.rsplit("\n\n", 1)[-1]
    assert last.startswith("<footer>") and last.endswith("</footer>")
    assert card.count("<footer>") == 1
    assert "**" not in last


def test_a_label_with_markdown_and_html_marks_reads_as_written():
    card = build_stream_card(_sess("a*b_<c>&d"), "Working")
    assert "<b>a*b_&lt;c&gt;&amp;d</b>" in card
    assert status_md(card).startswith("⏳ **a*b_<c>&d** ·")


def test_a_right_to_left_status_line_keeps_its_marks_inside_the_footer():
    sess = _sess()
    sess.scope_chat_id = None
    card = build_stream_card(sess, "Working", tr=i18n.get("fa"))
    last = card.rsplit("\n\n", 1)[-1]
    assert last.startswith("<footer>" + RLM) and last.endswith(RLM + "</footer>")


# ---- the agents block ------------------------------------------------------------------
# Rendered in Markdown (its own tests), drawn as footers under the answer.

def _view(*rows, **kw):
    return al.BlockView(agents=tuple(rows), **kw)


def test_every_line_of_the_block_is_a_footer():
    view = _view(al.AgentRow("pipeline-runner", False, prose="Writing *final*.md & notes"),
                 al.AgentRow("ship-tester", False, tool="Read", tool_arg="state.py", parent=0),
                 shells_running=1)
    block = ft.footer_block(al.render_block(view))
    paragraphs = block.split("\n\n")
    assert [p.startswith("- ") for p in paragraphs] == [False, False, True, False]
    for p in paragraphs:
        as_md(p[2:] if p.startswith("- ") else p)          # each one footer
    assert "Writing *final*.md &amp; notes" in paragraphs[1]
    assert as_md(paragraphs[-1]) == "Results from the agents will follow here."


def test_a_finished_block_is_one_footer():
    view = _view(al.AgentRow("Explore", True, seconds=42), agents_done_seconds=360)
    assert ft.footer_block(al.render_block(view)) == "<footer>✅ 1 agent done (6:00)</footer>"


@pytest.mark.parametrize("code", ["en", "fa", "ru", "de"])
@pytest.mark.parametrize("wide", ["&", "\U0001f600"], ids=["amp", "emoji"])
def test_the_reserve_holds_the_widest_block_as_drawn(code, wide):
    """HTML escapes "&" to five bytes: a block full of them, at the row cap
    and every label and activity at its longest, fits the reserve."""
    tr = i18n.get(code)
    rows = [al.AgentRow(wide * 80, False, prose=wide * 200)]
    rows += [al.AgentRow(wide * 80, False, prose=wide * 200, parent=0) for _ in range(3)]
    rows += [al.AgentRow(wide * 80, False, tool="Read", tool_arg=wide * 200)
             for _ in range(4)]
    view = _view(*rows, shells_running=999)
    block = ft.footer_block(al.render_block(view, tr))
    drawn = len(("\n\n---\n\n" + block).encode("utf-8"))
    assert drawn <= al.reserve_bytes(tr)


@pytest.mark.parametrize("code", ["en", "fa", "de"])
@pytest.mark.parametrize("wide", ["&", "\U0001f600"], ids=["amp", "emoji"])
def test_the_row_reserve_holds_the_widest_row_as_drawn(code, wide):
    """A row with every field at its longest and its starter's name before
    its own, drawn as a footer: "&" (five bytes as HTML) and a four-byte
    character alike."""
    tr = i18n.get(code)
    rows = [al.AgentRow(wide * 80, False, prose=wide * 200),
            al.AgentRow(wide * 80, False, description=wide * 200),
            al.AgentRow(wide * 80, False, tool="Read", tool_arg=wide * 200),
            al.AgentRow(wide * 80, False, waiting=True)]
    for row in rows:
        drawn = ft.footer(str(al._row(row, tr, wide * 80)))
        assert len(drawn.encode("utf-8")) <= al._worst_rows(tr), (code, row)


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_the_header_reserve_holds_the_widest_header_as_drawn(code):
    tr = i18n.get(code)
    clock = 999 * 3600 + 59 * 60 + 59
    view = _view(*(al.AgentRow("a", False) for _ in range(999)),
                 shells_finished=999, shells_finished_seconds=clock,
                 shells_stopped=999, shells_stopped_seconds=clock)
    drawn = ft.footer(al._header(view, tr))
    assert len(drawn.encode("utf-8")) <= al._worst_header(tr)


def test_no_block_draws_nothing():
    from aipager.bot import notify
    assert notify._drawn_block("") == ""
    assert notify._drawn_block("⏳ 1 agent working") == "<footer>⏳ 1 agent working</footer>"
