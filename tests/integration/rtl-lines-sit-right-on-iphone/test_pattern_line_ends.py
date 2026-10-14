"""Black-box: in a right-to-left rendering (fa, ar, qps-rtl) every line
of a pattern ends with U+200F as well as starting with one (design.md
success criteria 1-4, entrypoints.md ``Tr.html/text/md/n``).

Patterns are rendered under keys no catalog holds, so every language
shows the English pattern with its own direction rules. Invisible
characters are written as escapes only (tests/test_bidi_guard.py)."""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.i18n import Command, Plural, Safe, Span

RLM, LRM, FSI, PDI = "\u200f", "\u200e", "\u2068", "\u2069"
RTL = ("fa", "ar", "qps-rtl")
KINDS = ("html", "text", "md")
_PAIR = re.compile("[\u200e\u200f]{2}")
_MD_MARKER = re.compile(r"^(#{1,6} |- |\d+\. |> )")


def _render(code, kind, pattern, **params):
    params = {k: v for k, v in params.items() if "{" + k + "}" in pattern}
    return str(getattr(i18n.get(code), kind)("zz.case", pattern, **params))


def _no_pair(text):
    return _PAIR.search(text) is None


# ---- one line, several lines, the text end ---------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_single_line_ends_with_a_mark(code, kind):
    assert _render(code, kind, "x") == RLM + "x" + RLM


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("fa", "ar"))
def test_each_pattern_line_starts_and_ends_with_a_mark(code, kind):
    assert _render(code, kind, "x\ny") == RLM + "x" + RLM + "\n" + RLM + "y" + RLM


@pytest.mark.parametrize("kind", KINDS)
def test_pseudo_rtl_lines_start_and_end_with_a_mark(kind):
    out = _render("qps-rtl", kind, "alpha\nbeta gamma")
    assert all(line.startswith(RLM) and line.endswith(RLM) for line in out.split("\n"))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_blank_line_holds_no_mark(code, kind):
    assert _render(code, kind, "x\n\ny") == RLM + "x" + RLM + "\n\n" + RLM + "y" + RLM


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_pattern_ending_in_a_newline_leaves_the_last_blank_line_bare(code, kind):
    assert _render(code, kind, "line\n").endswith(RLM + "\n")


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_pattern_ending_in_a_newline_has_no_mark_after_it(code):
    assert _render(code, "text", "line\n")[-1] == "\n"


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_plural_html_lines_end_with_a_mark(code):
    agents = Plural("zz.agents", one="{n} agent on {s}", other="{n} agents on {s}\nmore")
    tr = i18n.get(code)
    out = tr.n(agents, 2, s="x1")
    assert out == (RLM + tr.num(2) + " agents on " + FSI + "x1" + PDI + RLM + "\n"
                   + RLM + "more" + RLM)


@pytest.mark.parametrize("kind", KINDS)
def test_plural_in_each_kind_ends_every_line_with_a_mark(kind):
    agents = Plural("zz.agents", one="{n} agent on {s}", other="{n} agents on {s}\nmore")
    out = str(i18n.get("fa").n(agents, 1, kind=kind, s="x1"))
    assert out.startswith(RLM) and out.endswith(RLM)


# ---- parameters ------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("fa", "ar"))
def test_newlines_inside_a_parameter_get_no_marks(code, kind):
    out = _render(code, kind, "{reason}", reason="line one\nline two")
    assert out == RLM + FSI + "line one\nline two" + PDI + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_pattern_that_is_only_a_parameter_ends_with_a_mark(code):
    assert _render(code, "html", "{name}", name="x1") == RLM + FSI + "x1" + PDI + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_safe_piece_alone_on_a_line_ends_with_a_mark(code):
    assert _render(code, "html", "{x}", x=Safe("<i>s</i>")) == RLM + "<i>s</i>" + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_rendered_pieces_at_both_ends_are_not_doubled(code):
    out = _render(code, "text", "{x}: {y}", x=Safe(RLM + "a" + RLM), y=Safe(RLM + "b" + RLM))
    assert out == RLM + "a" + RLM + ": " + RLM + "b" + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_piece_ending_with_lrm_keeps_only_the_last_mark_of_a_run(code):
    out = _render(code, "text", "{x}\n{y}", x=Safe(LRM + "a" + LRM), y=Safe(RLM + "b"))
    assert out == LRM + "a" + RLM + "\n" + RLM + "b" + RLM


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", RTL)
def test_rendered_nested_pieces_never_leave_two_marks_side_by_side(code, kind):
    tr = i18n.get(code)
    piece = Safe(tr.text("zz.piece", "frontend"))
    out = _render(code, kind, "{a} · {b}\n{b}", a=piece, b=piece)
    assert _no_pair(out)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_line_ending_in_a_number_ends_with_a_mark(code):
    tr = i18n.get(code)
    assert _render(code, "html", "count {n}", n=1234) == RLM + "count " + tr.num(1234) + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_line_ending_in_an_emoji_ends_with_a_mark(code):
    out = _render(code, "html", "done \U0001f680")
    assert out == RLM + "done \U0001f680" + RLM


# ---- code, Span, Command -----------------------------------------------------------------------

@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_code_parameter_at_a_line_end_is_isolated_then_marked(code):
    out = _render(code, "html", "Run <code>{cmd}</code>\nthen {name}", cmd="ls -la", name="x1")
    assert out == (RLM + "Run " + FSI + "<code>ls -la</code>" + PDI + RLM + "\n"
                   + RLM + "then " + FSI + "x1" + PDI + RLM)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_code_span_at_the_line_end_gets_the_mark_after_its_closing_tag(code):
    out = _render(code, "html", "tail <code>x {c}</code>", c="ls")
    assert out.endswith("</code>" + RLM)


@pytest.mark.parametrize("code", RTL)
def test_no_mark_inside_a_code_span(code):
    out = _render(code, "html", "a <code>x {c}\ny</code> b", c="ls")
    inner = out[out.index("<code>"):out.index("</code>")]
    assert not any(ch in inner for ch in (RLM, LRM, FSI, PDI))


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_command_at_the_line_end_is_followed_by_the_mark(code):
    out = _render(code, "html", "Send {cmd} to {who}", cmd=Command("/x12"), who=Command("@alice"))
    assert out == RLM + "Send " + FSI + "/x12" + PDI + " to " + FSI + "@alice" + PDI + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_span_at_the_line_end_is_followed_by_the_mark(code):
    out = _render(code, "md", "done: {row}", row=Span("`Read: /a_b*.py`"))
    assert out == RLM + "done: " + FSI + "`Read: /a_b*.py`" + PDI + RLM


@pytest.mark.parametrize("code", RTL)
def test_no_mark_inside_a_span(code):
    out = _render(code, "md", "{row}\nx", row=Span("`a\nb`"))
    inner = out[out.index("`"):out.rindex("`") + 1]
    assert not any(ch in inner for ch in (RLM, LRM))


@pytest.mark.parametrize("code", RTL)
def test_no_mark_inside_an_attribute(code):
    out = _render(code, "html", '<a href="{url}">go</a>', url="https://x.y/?a=1")
    assert 'href="https://x.y/?a=1"' in out


# ---- <pre> and <blockquote> ---------------------------------------------------------------------

@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_pre_block_gets_no_mark_beside_it(code):
    out = _render(code, "html", '<a href="{url}">go</a>\n<pre>{a}\n{b}</pre>\nafter',
                  url="u", a="one", b="two")
    assert out == (RLM + '<a href="u">go</a>' + RLM + "\n<pre>one\ntwo</pre>\n"
                   + RLM + "after" + RLM)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_pre_only_pattern_has_no_marks(code):
    out = _render(code, "html", "<pre>{a}</pre>", a="one")
    assert RLM not in out and LRM not in out


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_written_pre_only_pattern_is_unchanged(code):
    assert _render(code, "html", "<pre>a {v}</pre>", v="1") == "<pre>a 1</pre>"


@pytest.mark.parametrize("code", RTL)
def test_no_mark_inside_pre(code):
    out = _render(code, "html", "x\n<pre>a\nb {v}\nc</pre>", v="1")
    inner = out[out.index("<pre>"):out.index("</pre>")]
    assert not any(ch in inner for ch in (RLM, LRM, FSI, PDI))


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_written_quote_marks_its_lines_and_its_end_sits_inside_the_tag(code):
    out = _render(code, "html", "x\n<blockquote>q1\nq2</blockquote>\ny")
    assert out == (RLM + "x" + RLM + "\n<blockquote>q1" + RLM + "\n" + RLM + "q2" + RLM
                   + "</blockquote>\n" + RLM + "y" + RLM)


@pytest.mark.parametrize("code", RTL)
def test_nothing_follows_a_closing_blockquote_before_the_line_break(code):
    out = _render(code, "html", "x\n<blockquote>q1\nq2</blockquote>\ny")
    assert "</blockquote>\n" in out


@pytest.mark.parametrize("code", RTL)
def test_a_quote_of_a_parameter_gets_no_mark_inside(code):
    out = _render(code, "html", "<blockquote>{q}</blockquote>\nend", q="a\nb")
    inner = out[out.index("<blockquote>"):out.index("</blockquote>")]
    assert RLM not in inner and LRM not in inner


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_a_quote_opening_the_text_gets_no_start_mark(code):
    out = _render(code, "html", "<blockquote>{q}</blockquote>\nend", q="a\nb")
    assert out.startswith("<blockquote>")


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_the_line_after_a_quote_of_a_parameter_is_marked(code):
    out = _render(code, "html", "<blockquote>{q}</blockquote>\nend", q="a\nb")
    assert out.endswith("</blockquote>\n" + RLM + "end" + RLM)


@pytest.mark.parametrize("code", RTL)
def test_a_pre_arriving_in_a_safe_parameter_gets_no_mark_beside_it(code):
    out = _render(code, "html", "head\n{block}\ntail", block=Safe("<pre>a\nb</pre>"))
    assert "\n<pre>a\nb</pre>\n" in out


# ---- Markdown ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_block_markers_keep_the_marks_inside_the_line(code):
    out = _render(code, "md", "# t\n\n- i")
    assert out == "# " + RLM + "t" + RLM + "\n\n- " + RLM + "i" + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_every_block_marker_kind(code):
    out = _render(code, "md", "# {s} log\n\n- item\n> quote\n## Agents\n1. one\nplain", s="x1")
    assert out.split("\n") == [
        "# " + RLM + FSI + "x1" + PDI + " log" + RLM, "", "- " + RLM + "item" + RLM,
        "> " + RLM + "quote" + RLM, "## " + RLM + "Agents" + RLM, "1. " + RLM + "one" + RLM,
        RLM + "plain" + RLM]


@pytest.mark.parametrize("code", ("fa", "ar"))
@pytest.mark.parametrize("closer", ("**", "_", "~", "__", "*"))
def test_md_an_emphasis_closer_after_punctuation_stays_after_the_mark(code, closer):
    out = _render(code, "md", closer + "word:" + closer)
    assert out == RLM + closer + "word:" + RLM + closer


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_an_emphasis_closer_after_a_bracket_stays_after_the_mark(code):
    assert _render(code, "md", "~(x)~") == RLM + "~(x)" + RLM + "~"


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_an_emphasis_closer_after_a_letter_is_followed_by_the_mark(code):
    assert _render(code, "md", "**bold**") == RLM + "**bold**" + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_an_escaped_star_is_not_a_closer(code):
    assert _render(code, "md", "price\\*") == RLM + "price\\*" + RLM


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_md_a_closer_after_punctuation_on_each_line(code):
    out = _render(code, "md", "*a.*\n*b!*")
    assert out == RLM + "*a." + RLM + "*\n" + RLM + "*b!" + RLM + "*"


# ---- the generic line rule over many patterns ------------------------------------------------------

_PATTERNS = (
    "x", "x\ny", "x\n\ny", "a b c\nd e\n\n\nf",
    "Run <code>{c}</code>\nthen {name}", "<b>{name}</b> · {path}",
    "{name}: {path}\n{name}", "head\n- one\n- two",
)


def _line_ok(line: str) -> bool:
    if not line.strip():
        return RLM not in line and LRM not in line
    body = _MD_MARKER.sub("", line, count=1)
    return body.startswith(RLM) and body.endswith(RLM)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("pattern", _PATTERNS, ids=[f"p{i}" for i in range(len(_PATTERNS))])
def test_every_pattern_line_is_marked_at_both_ends(code, kind, pattern):
    out = _render(code, kind, pattern, c="ls", name="frontend", path="/home/alice")
    assert all(_line_ok(line) for line in out.split("\n"))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("pattern", _PATTERNS, ids=[f"p{i}" for i in range(len(_PATTERNS))])
def test_no_pattern_renders_two_adjacent_marks(code, kind, pattern):
    out = _render(code, kind, pattern, c="ls", name="frontend", path="/home/alice")
    assert _no_pair(out)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", RTL)
def test_a_parameter_carrying_marks_at_its_ends_leaves_no_pair(code, kind):
    out = _render(code, kind, "{v}\n{v} x {v}", v=Safe(RLM + "v" + RLM))
    assert _no_pair(out)
