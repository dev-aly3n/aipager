"""A left-to-right language's own lines inside a right-to-left rich message
(operator, 2026-10-17: an English chat, a Persian answer, the agents block
under it read "agents working 8 ⏳" and ".Results ... here" on Telegram
Desktop).

A rich message has one direction (``is_rtl``), and Telegram Desktop and
Web lay out every block of a right-to-left one right to left. So
``Tr.directed(True)`` on a left-to-right language gives the "in-RTL twin":
every line starts (after a Markdown block marker) and ends with U+200E,
every text parameter is isolated in U+2068...U+2069, with every rule the
right-to-left marks already keep. Removing those characters gives the
English bytes back; with no direction or left to right nothing changes.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot import agents_line, animation, held_message
from aipager.bot.notify import finished_line
from aipager.i18n import Command, Safe, Span, locales

RLM, LRM, FSI, RLI, PDI = "\u200f", "\u200e", "\u2068", "\u2067", "\u2069"
LTR_LANGS = sorted(c for c, loc in locales.LOCALES.items() if not loc.rtl)
INVISIBLE = re.compile("[\u200e\u200f\u2066-\u2069]")
ADJACENT = re.compile("[\u200e\u200f]{2,}")
FA_WORDS = "\u0631\u0633\u062a\u0648\u0631\u0627\u0646 \u0645\u0627\u0647\u06cc"


def twin(code: str = "en"):
    return i18n.get(code).directed(True)


def strip(text) -> str:
    return str(text).replace(LRM, "").replace(FSI, "").replace(PDI, "")


# ---- the twin ---------------------------------------------------------------------

def test_the_twin_is_cached_and_shows_itself():
    t = twin()
    assert t is twin() and repr(t) == "<Tr en-in-rtl>"
    assert repr(i18n.get("en")) == "<Tr en>"


def test_a_right_to_left_language_ignores_rich():
    fa = i18n.get("fa")
    assert fa.directed(True, rich=False) is fa
    assert fa.directed(False, rich=False) is fa.directed(False)
    assert not fa.ltr_in_rtl and not fa.directed(False).ltr_in_rtl


@pytest.mark.parametrize("kind", ["md", "html", "text"])
def test_a_line_is_marked_at_both_ends(kind):
    out = str(getattr(twin(), kind)("a.b", "Results from the agents will follow here."))
    assert out == LRM + "Results from the agents will follow here." + LRM


def test_every_line_of_a_pattern_is_marked_and_a_blank_line_is_not():
    out = twin().md("a.b", "one\n\ntwo")
    assert out == LRM + "one" + LRM + "\n\n" + LRM + "two" + LRM


@pytest.mark.parametrize("marker", ["# ", "## ", "- ", "* ", "> ", "1. "],
                         ids=["h1", "h2", "dash", "star", "quote", "ordered"])
def test_the_start_mark_goes_after_a_block_marker(marker):
    out = twin().md("a.b", marker + "item")
    assert out == marker + LRM + "item" + LRM


def test_parameters_are_isolated_and_scrubbed():
    out = twin().md("a.b", "Searching the web for {q}", q=FA_WORDS + "\ufdd1")
    assert out == (LRM + "Searching the web for " + FSI + FA_WORDS + "\ufffd" + PDI + LRM)


def test_a_span_and_a_command_are_isolated_whole():
    out = twin().md("a.b", "Run {c} or send {cmd}", c=Span("`ls -l`"), cmd=Command("/x1"))
    assert FSI + "`ls -l`" + PDI in out and FSI + "/x1" + PDI in out
    assert "`ls -l`" in out and "/x1" in out


def test_nothing_invisible_inside_code_or_a_tag():
    out = twin().html("a.b", '<a href="{u}">go</a> <code>{c}</code> now',
                      u="https://example.com/x", c="ls -l")
    assert 'href="https://example.com/x"' in out
    assert "<code>ls -l</code>" in out
    assert out == (LRM + '<a href="https://example.com/x">go</a> ' + FSI
                   + "<code>ls -l</code>" + PDI + " now" + LRM)


def test_no_mark_beside_a_pre_block():
    out = twin().html("a.b", "x:\n<pre>{o}</pre>", o="log")
    assert out == LRM + "x:" + LRM + "\n" + FSI + "<pre>log</pre>" + PDI


@pytest.mark.parametrize("pattern,want", [
    ("*(x)*", LRM + "*(x)" + LRM + "*"),
    ("x **(y)**", LRM + "x **(y)" + LRM + "**"),
    ("_x_", "_" + LRM + "x" + LRM + "_"),
    ("- __x__", "- __" + LRM + "x" + LRM + "__"),
], ids=["star-paren", "bold-paren", "underscore", "list-dunder"])
def test_an_emphasis_run_stays_whole(pattern, want):
    assert twin().md("a.b", pattern) == want


@pytest.mark.parametrize("pattern,params", [
    ("{lead} · Finished", {"lead": Span("\u200e\U0001f4ac **x1**\u200e")}),
    ("*(x)*", {}), ("_x_", {}), ("x:\n<pre>{o}</pre>", {"o": "log"}),
    ("- item {a}\n\n# head {b}", {"a": FA_WORDS, "b": "x"}),
    ('<a href="{u}">go</a>\n<pre>{a}\n{b}</pre>\nafter', {"u": "u", "a": "one", "b": "two"}),
    ("Searching the web for {q}", {"q": FA_WORDS + "."}),
], ids=["span-lead", "star", "underscore", "pre", "blocks", "pre-lines", "persian"])
@pytest.mark.parametrize("kind", ["md", "html"])
def test_the_twin_mirrors_the_right_to_left_rules(pattern, params, kind):
    # A Persian chat's rendering of an untranslated key uses the same
    # pattern: the twin is exactly it with U+200E for U+200F.
    fa = i18n.get("fa")
    assert getattr(twin(), kind)("a.b", pattern, **params) == \
        getattr(fa, kind)("a.b", pattern, **params).replace(RLM, LRM)


def test_two_marks_never_touch_when_a_rendered_piece_is_nested():
    t = twin()
    head = agents_line._header(agents_line.BlockView(
        agents=(agents_line.AgentRow("general-purpose", False),),
        shells_finished=1, shells_finished_seconds=7985, shells_stopped=1,
        shells_stopped_seconds=250), t)
    assert head.startswith(LRM) and head.endswith(LRM) and not ADJACENT.search(head)
    line = finished_line(t, "\u200e\U0001f4ac **x1**\u200e", "md", seconds=250, added=3)
    assert not ADJACENT.search(line)


def test_no_rlm_and_no_digits_change():
    out = twin().n(agents_line.HEAD_WORKING, 8, kind="md")
    assert RLM not in out and "8" in out


# ---- line, bare, isolate, mark, lead ---------------------------------------------

def test_line_marks_both_ends_outside_a_code_span():
    t = twin()
    assert t.line("\u2705 `SendMessage`", kind="md") == LRM + "\u2705 `SendMessage`" + LRM
    assert t.line("`a` and `b`", kind="md") == LRM + "`a` and `b`" + LRM


def test_line_does_not_double_a_pieces_own_mark():
    t = twin()
    assert t.line(LRM + "x" + LRM, kind="md") == LRM + "x" + LRM
    assert isinstance(t.line(Safe("x")), Safe)
    assert t.line("\n\nx\n") == "\n\n" + LRM + "x" + LRM + "\n"
    assert t.line("   ") == "   "


def test_line_keeps_a_block_marker_first():
    assert twin().line("- x", kind="md") == "- " + LRM + "x" + LRM


def test_bare_strips_the_twins_marks_only():
    t = twin()
    assert t.bare(LRM + "x" + LRM) == "x"
    assert t.bare(RLM + "x" + RLM) == RLM + "x" + RLM
    assert isinstance(t.bare(Safe(LRM + "x")), Safe)


def test_isolate_wraps_only_where_parameters_are_isolated():
    assert twin().isolate("safar") == FSI + "safar" + PDI
    assert twin().isolate("a\ufdd2") == FSI + "a\ufffd" + PDI
    assert isinstance(twin().isolate(Safe("x")), Safe)
    assert i18n.get("fa").isolate("x") == FSI + "x" + PDI
    assert i18n.get("fa").directed(False).isolate("x") == FSI + "x" + PDI
    assert i18n.get("en").isolate("safar") == "safar"
    assert i18n.get("de").isolate("safar") == "safar"


def test_mark_and_lead_stay_rlm_only():
    t = twin()
    assert t.mark == "" and t.lead("x") == "x"


def test_the_base_line_and_bare_do_not_change():
    en = i18n.get("en")
    assert en.line("\u2705 `x`", kind="md") == "\u2705 `x`"
    assert en.bare(LRM + "x") == LRM + "x"


# ---- every left-to-right language ---------------------------------------------

def _samples(t):
    return [
        t.n(agents_line.HEAD_WORKING, 8, kind="md"),
        t.n(agents_line.MORE, 2, kind="md"),
        t.md(agents_line.PROMISE),
        t.md(agents_line.ROW_WORKING, agent="general-purpose",
             activity="Searching the web for " + FA_WORDS + "\u2026"),
        t.md(agents_line.ITEM_WORKING, agent="general-purpose", activity=FA_WORDS + "."),
        held_message.late_marker_text(t, 180),
        t.n(animation.FOLD, 5, kind="md"),
        finished_line(t, "\U0001f4ac **x1**", "md", seconds=250, added=10, removed=5),
        t.md(animation.CARD_STATUS, mark=Safe("\u23f3"), session="x1",
             verb=Safe(animation._verb(t, "Working"))),
    ]


@pytest.mark.parametrize("code", LTR_LANGS)
def test_every_left_to_right_language_strips_back_to_its_own_bytes(code):
    base = i18n.get(code)
    for marked, plain in zip(_samples(twin(code)), _samples(base)):
        marked = str(marked)
        assert strip(marked) == str(plain)
        assert RLM not in marked and not ADJACENT.search(marked)
        for line in marked.split("\n"):
            if line.strip():
                assert LRM in line
            else:
                assert not INVISIBLE.search(line)


# ---- the agents block reserve ------------------------------------------------

def test_the_reserve_has_its_own_key_for_the_twin():
    agents_line._RESERVE.clear()
    t_alone = agents_line.reserve_bytes(twin())
    agents_line._RESERVE.clear()
    base = agents_line.reserve_bytes(i18n.get("en"))
    t = agents_line.reserve_bytes(twin())
    assert t == t_alone > base
    view = agents_line.BlockView(
        agents=tuple(agents_line.AgentRow(f"general_purpose{i}", False, tool="Read",
                                          tool_arg=FA_WORDS * 8) for i in range(9)),
        shells_running=3)
    from aipager.bot.footer import footer_block
    block = footer_block(agents_line.render_block(view, twin()))
    assert len(("\n\n---\n\n" + block).encode("utf-8")) <= t


# ---- the agents block -------------------------------------------------------------

def test_every_block_line_is_marked_after_its_list_marker():
    view = agents_line.BlockView(agents=(
        agents_line.AgentRow("general-purpose", False, tool="WebSearch", tool_arg=FA_WORDS),
        agents_line.AgentRow("general-purpose", False, prose=FA_WORDS + ".", parent=0),
        agents_line.AgentRow("Explore", True, seconds=30, parent=0),
    ) + tuple(agents_line.AgentRow("general-purpose", False) for _ in range(8)))
    block = agents_line.render_block(view, twin())
    for line in block.split("\n"):
        if not line.strip():
            continue
        body = line[2:] if line.startswith("- ") else line
        assert body.startswith(LRM) and body.endswith(LRM), line
    assert not ADJACENT.search(block) and RLM not in block
    assert strip(block) == agents_line.render_block(view, i18n.get("en"))


# ---- the busy card -----------------------------------------------------------------

def _card_session():
    import time

    from aipager.state import SessionRegistry, Status
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x1")
    s.label = "safar"
    s.status = Status.BUSY
    s.busy_started_at = time.monotonic() - 125
    s.tool_history = [("SendMessage", True), ("Read: /a", "failed"),
                      ("\U0001f916 general-purpose \u00b7 43 tool calls \u00b7 17:38", True),
                      ("\U0001f916 general-purpose", False), ("Bash: sleep 9", False)]
    s.active_subagents = {"a": {"type": "general-purpose", "activity": "Bash: Dump listing",
                                "started_at": time.monotonic() - 2310, "history_idx": 3,
                                "tools": ["Read: /a", "Read: /b"]}}
    s.finished_subagents = [{"history_idx": 2, "tools": ["Read: /x"]}]
    s.stream_commentary = [(0, FA_WORDS + ".")]
    return s


def test_every_code_built_card_row_is_marked_outside_its_code_span():
    s = _card_session()
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    rows = [line for line in card.split("\n")
            if any(m in line[:3] for m in ("\u2705", "\u274c", "\u23f3", "\u23f9"))]
    assert len(rows) >= 7
    for line in rows:
        assert line.startswith(LRM) and line.endswith(LRM), line
        for span in re.findall(r"`[^`]*`", line):
            assert not INVISIBLE.search(span), line
    # Claude's prose quote is never marked.
    assert "> " + FA_WORDS + "." in card.split("\n")
    assert not ADJACENT.search(card) and RLM not in card


def test_the_card_strips_back_to_the_english_card():
    s = _card_session()
    marked, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    plain, _ = animation.build_stream_card_ex(s, "Working", rtl=False)
    assert strip(marked) == plain and not INVISIBLE.search(plain)


def test_card_tr_not_rich_is_the_base():
    s = _card_session()
    s.turn_prompt = FA_WORDS
    assert animation.card_tr(s).ltr_in_rtl
    assert animation.card_tr(s, rich=False) is i18n.get("en")
