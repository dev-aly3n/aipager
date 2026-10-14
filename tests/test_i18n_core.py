"""The i18n core: keys, the placeholder filler, escaping per output kind,
lookup and fallback, right-to-left marks, native digits and the pseudo
locale."""

from __future__ import annotations

import json
import logging
import math

import pytest

from aipager import i18n
from aipager.i18n import core, locales
from aipager.i18n.core import Msg, Plural, Safe

RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    """Write catalogs under tmp_path and point the core at them."""
    monkeypatch.setattr(core, "LOCALES_DIR", tmp_path)
    core.reset_caches()

    def write(code, entries):
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        (tmp_path / f"{code}.jsonl").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8")
        core.reset_caches()
    yield write
    core.reset_caches()


# ---- Msg and Plural -----------------------------------------------------

def test_msg_refuses_a_malformed_key():
    for bad in ("single", "Bad.key", "a..b", "a.b.", ".a.b", "a-b.c", "a b.c", "", 5):
        with pytest.raises(ValueError):
            Msg(bad, "x")
        with pytest.raises(ValueError):
            Plural(bad, one="x", other="y")
    assert Msg("a.b", "x").key == "a.b"
    assert Msg("set.level_2.title", "x").key == "set.level_2.title"


def test_msg_is_its_english_everywhere():
    m = Msg("kb.back", "« Back", note="a button")
    assert m == "« Back" and hash(m) == hash("« Back")
    assert f"[{m}]" == "[« Back]"
    assert {"« Back": 1}[m] == 1
    assert m in frozenset({"« Back"})
    assert m.note == "a button" and m.eg == {}
    assert type(m + "!") is str


# ---- the filler ---------------------------------------------------------

def test_filler_refuses_format_specs():
    tr = i18n.get("en")
    for bad in ("{0}", "{x.y}", "{x!r}", "{x:>4}", "{x[0]}", "a { b", "a } b", "{ x }"):
        with pytest.raises(ValueError):
            tr.text("a.b", bad, x=1)
    assert tr.text("a.b", "{{x}} is {x}", x=1) == "{x} is 1"


def test_a_missing_or_unused_parameter_is_an_error():
    tr = i18n.get("en")
    with pytest.raises(KeyError):
        tr.text("a.b", "{x} and {y}", x=1)
    with pytest.raises(ValueError):
        tr.text("a.b", "{x}", x=1, y=2)


def test_a_key_needs_its_english_inline():
    tr = i18n.get("en")
    with pytest.raises(TypeError):
        tr.text("a.b")
    with pytest.raises(TypeError):
        tr.text(Msg("a.b", "x"), "y")
    with pytest.raises(ValueError):
        tr.text("Not a key", "x")


# ---- lookup -------------------------------------------------------------

def test_a_missing_key_shows_the_english(catalogs):
    catalogs("de", [{"k": "a.known", "v": "Bekannt {x}", "src": "00000000"}])
    tr = i18n.get("de")
    assert tr.text("a.known", "Known {x}", x=1) == "Bekannt 1"
    assert tr.text("a.unknown", "Unknown {x}", x=1) == "Unknown 1"
    assert tr.text(Msg("a.other", "Other")) == "Other"


def test_english_never_reads_a_catalog(catalogs, monkeypatch):
    catalogs("en", [{"k": "a.b", "v": "WRONG", "src": "00000000"}])
    assert i18n.get("en").text("a.b", "Right") == "Right"


def test_a_malformed_catalog_line_is_skipped(catalogs, caplog):
    catalogs("de", [{"k": "a.b", "v": "Gut", "src": "0"}, {"k": "a.c"}, ["x"]])
    with caplog.at_level(logging.DEBUG, logger="aipager.i18n.core"):
        tr = i18n.get("de")
        assert tr.text("a.b", "Good") == "Gut"
        assert tr.text("a.c", "C") == "C"
    assert "skipped" in caplog.text


# ---- escaping -----------------------------------------------------------

def test_html_escapes_params_once():
    tr = i18n.get("en")
    assert tr.html("a.b", "<b>{x}</b>", x="<i>&") == "<b>&lt;i&gt;&amp;</b>"
    assert tr.html("a.b", "{x}", x=Safe("<i>ok</i>")) == "<i>ok</i>"
    inner = tr.html("a.c", "<i>{y}</i>", y="a&b")
    assert isinstance(inner, Safe)
    assert tr.html("a.b", "<b>{x}</b>", x=inner) == "<b><i>a&amp;b</i></b>"
    assert tr.text("a.b", "{x}", x="<i>&") == "<i>&"
    assert tr.md("a.b", "**{x}**", x="general_purpose [1]") == r"**general\_purpose \[1\]**"


# ---- digits -------------------------------------------------------------

def test_native_digits_touch_numbers_only():
    fa, ar = i18n.get("fa"), i18n.get("ar")
    pattern = "{n} of {total} in {name} at {p}% <code>{c}</code> {flag}"
    out = fa.text("a.b", pattern, n=12, total=1.5, name="x12", p=7, c=34, flag=True)
    assert "۱۲ of ۱٫۵" in out
    assert "۷%" in out
    assert FSI + "x12" + PDI in out
    assert "<code>34</code>" in out
    assert FSI + "True" + PDI in out
    assert ar.text("a.b", "{n}", n=2026).endswith("٢٠٢٦" + RLM)
    assert fa.num(0.25) == "۰٫۲۵"
    assert fa.num(3.0) == "۳"
    assert i18n.get("de").num(1.5) == "1.5"


# ---- right to left --------------------------------------------------------

def test_rtl_marks_never_enter_code():
    fa = i18n.get("fa")
    out = fa.html("a.b", "Run <code>{cmd}</code>\nthen {name}", cmd="ls -la", name="x1")
    assert out == (RLM + "Run " + FSI + "<code>ls -la</code>" + PDI + RLM + "\n"
                   + RLM + "then " + FSI + "x1" + PDI + RLM)
    # A placeholder that is only part of a code span is not isolated.
    out = fa.html("a.b", "<code>/{label} stop</code>", label="x1")
    assert out == RLM + "<code>/x1 stop</code>" + RLM
    # Nothing invisible in an attribute or inside <pre>.
    out = fa.html("a.b", '<a href="{url}">go</a>\n<pre>{a}\n{b}</pre>',
                  url="https://x.y/?a=1", a="one", b="two")
    assert 'href="https://x.y/?a=1"' in out
    pre = out[out.index("<pre>"):out.index("</pre>")]
    assert not any(ch in pre for ch in (RLM, FSI, PDI))
    assert fa.html("a.b", "{x}", x=Safe("<i>s</i>")) == RLM + "<i>s</i>" + RLM


def test_english_renders_byte_identical():
    en = i18n.get("en")
    pattern = "{n} tools <code>{c}</code>\nline {s} {f}"
    out = en.html("a.b", pattern, n=12, c="ls", s="x1", f=2.5)
    assert out == "12 tools <code>ls</code>\nline x1 2.5"
    assert en.text(Msg("a.c", "« Back")) == "« Back"
    assert en.num(1234.5) == "1234.5"
    assert not en.rtl


# ---- plurals --------------------------------------------------------------

AGENTS = Plural("card.agents", one="{n} agent on {s}", other="{n} agents on {s}")


def test_plural_english_forms():
    en = i18n.get("en")
    assert en.n(AGENTS, 1, s="x1") == "1 agent on x1"
    assert en.n(AGENTS, 0, s="x1") == "0 agents on x1"
    assert en.n(AGENTS, 2, kind="text", s="<x>") == "2 agents on <x>"
    with pytest.raises(ValueError):
        en.n(AGENTS, 2, n=3, s="x")


def test_plural_uses_the_language_category(catalogs):
    catalogs("ru", [{"k": "card.agents", "src": "0", "v": {
        "one": "{n} агент {s}", "few": "{n} агента {s}", "many": "{n} агентов {s}",
        "other": "{n} агента {s}"}}])
    ru = i18n.get("ru")
    assert ru.n(AGENTS, 1, s="x") == "1 агент x"
    assert ru.n(AGENTS, 3, s="x") == "3 агента x"
    assert ru.n(AGENTS, 11, s="x") == "11 агентов x"
    # A form the catalog lacks shows the English.
    catalogs("ru", [{"k": "card.agents", "src": "0", "v": {"one": "{n} агент {s}"}}])
    assert i18n.get("ru").n(AGENTS, 5, s="x") == "5 agents on x"


def test_join_uses_the_language_separator():
    assert i18n.get("en").join(["a", "b"]) == "a, b"
    assert i18n.get("ar").join(["a", "b"]) == "a، b"
    assert i18n.get("zh-Hans").join(["a", "b"]) == "a、b"


# ---- the pseudo locale -----------------------------------------------------

def test_pseudo_keeps_what_must_stay_verbatim():
    out = core.pseudo("Send /new to Claude <b>{name}</b> &amp; <code>ls -la</code> in Telegram")
    assert out.startswith("⟦") and out.endswith("⟧")
    for kept in ("/new", "Claude", "<b>", "{name}", "</b>", "&amp;", "<code>ls -la</code>",
                 "Telegram"):
        assert kept in out
    assert "Send" not in out and "to" not in out.split()
    assert out.count("~") == math.ceil(len("Send /new to Claude <b>{name}</b> &amp; "
                                        "<code>ls -la</code> in Telegram") * 0.35)


def test_pseudo_locales_render_marked_text():
    qps = i18n.get("qps")
    out = qps.html("a.b", "Hello {name}", name="x1")
    assert out.startswith("⟦") and "x1" in out and "Hello" not in out
    rtl = i18n.get("qps-rtl").text("a.b", "Hi {name}", name="x1")
    assert rtl.startswith(RLM) and FSI + "x1" + PDI in rtl


def test_never_translate_lists_the_names():
    for name in ("Claude Code", "aipager", "Telegram", "Mini App", "Bash",
                 "AskUserQuestion", "opus", "sonnet", "haiku", "opusplan"):
        assert name in core.NEVER_TRANSLATE


def test_the_test_switch_is_only_a_frozenset():
    assert locales._TEST_ENABLED == frozenset()


# ---- Span, Command, block markers, separators (phase 2) ----------------------

def test_a_span_is_isolated_whole():
    from aipager.i18n import Span
    fa, en = i18n.get("fa"), i18n.get("en")
    span = Span("`Read: /a_b*.py`")
    out = fa.md("a.b", "{row} done in {n}", row=span, n=12)
    # Whole: the isolate sits outside the backticks, never inside, and the
    # span is neither escaped nor digit-converted.
    assert out == RLM + FSI + "`Read: /a_b*.py`" + PDI + " done in ۱۲" + RLM
    inner = out[out.index("`"):out.rindex("`") + 1]
    assert not any(ch in inner for ch in (RLM, FSI, PDI))
    html = fa.html("a.b", "{c} ok", c=Span("<code>a&lt;1</code>"))
    assert html == RLM + FSI + "<code>a&lt;1</code>" + PDI + " ok" + RLM
    # Inside a code element the span is left alone.
    assert fa.html("a.b", "<b>{c}</b>", c=Span("x 1")) == (
        RLM + "<b>" + FSI + "x 1" + PDI + "</b>" + RLM)
    assert fa.html("a.b", "<code>{c}</code>", c=Span("x 1")) == RLM + "<code>x 1</code>" + RLM
    # English: the bytes as passed.
    assert en.md("a.b", "{row} done", row=span) == "`Read: /a_b*.py` done"
    assert en.html("a.b", "{c}", c=Span("<i>&</i>")) == "<i>&</i>"


def test_a_command_keeps_its_slash_next_to_the_isolate(monkeypatch):
    from aipager.i18n import Command
    fa, en = i18n.get("fa"), i18n.get("en")
    out = fa.html("a.b", "Send {cmd} to {who}", cmd=Command("/x12"), who=Command("@b<b"))
    assert out == RLM + "Send " + FSI + "/x12" + PDI + " to " + FSI + "@b&lt;b" + PDI + RLM
    # Never digit-converted, never split: "/" directly before the name.
    assert "/x12" in out and "۱" not in out
    assert en.html("a.b", "Send {cmd}", cmd=Command("/x1&")) == "Send /x1&amp;"
    assert en.text("a.b", "Send {cmd}", cmd=Command("/x1&")) == "Send /x1&"
    # The one switch for rule 7: commands without isolates.
    monkeypatch.setattr(core, "ISOLATE_COMMANDS", False)
    assert fa.text("a.b", "Send {cmd}", cmd=Command("/x1")) == RLM + "Send /x1" + RLM


def test_rtl_mark_follows_a_heading_or_list_marker():
    fa, en = i18n.get("fa"), i18n.get("en")
    out = fa.md("a.b", "# {s} log\n\n- item\n> quote\n## Agents\n1. one\nplain",
                s="x1")
    assert out.split("\n") == [
        "# " + RLM + FSI + "x1" + PDI + " log" + RLM, "", "- " + RLM + "item" + RLM,
        "> " + RLM + "quote" + RLM, "## " + RLM + "Agents" + RLM, "1. " + RLM + "one" + RLM,
        RLM + "plain" + RLM]
    # Only at a line start, and only where aipager put the mark.
    assert fa.text("a.b", "a - b # c") == RLM + "a - b # c" + RLM
    assert fa.text("a.b", "{x}", x="- y") == RLM + FSI + "- y" + PDI + RLM
    assert fa.html("a.b", "#tag") == RLM + "#tag" + RLM
    assert en.md("a.b", "# x\n- y") == "# x\n- y"


# ---- both ends of a right-to-left line (iPhone) ---------------------------------

LRM = "\u200e"
RAH = "\u0631\u0627\u0647"


@pytest.mark.parametrize("code", ["fa", "ar", "qps-rtl"])
def test_every_pattern_line_ends_with_a_mark(code):
    tr = i18n.get(code)
    for out in (tr.text("a.b", "x"), tr.text("a.b", "x\ny"), tr.html("a.b", "<b>x</b> y")):
        for line in out.split("\n"):
            assert line.startswith(RLM) and line.endswith(RLM), ascii(line)
            assert RLM * 2 not in line
    if code != "qps-rtl":
        assert tr.text("a.b", "x\ny") == RLM + "x" + RLM + "\n" + RLM + "y" + RLM
        assert tr.html("a.b", "<b>x</b> y") == RLM + "<b>x</b> y" + RLM


def test_a_line_ending_in_code_a_span_or_a_command():
    from aipager.i18n import Command, Span
    fa = i18n.get("fa")
    assert fa.html("a.b", "run <code>ls</code>") == RLM + "run <code>ls</code>" + RLM
    assert fa.html("a.b", "run <code>{c}</code>", c="ls") == (
        RLM + "run " + FSI + "<code>ls</code>" + PDI + RLM)
    assert fa.md("a.b", "run {s}", s=Span("`ls`")) == RLM + "run " + FSI + "`ls`" + PDI + RLM
    assert fa.text("a.b", "run {c}", c=Command("/language")) == (
        RLM + "run " + FSI + "/language" + PDI + RLM)
    # Nothing inside a code element that spans lines.
    out = fa.html("a.b", "a <code>x\ny</code> b")
    assert out == RLM + "a <code>x\ny</code> b" + RLM


def test_no_mark_beside_a_pre_block():
    fa = i18n.get("fa")
    out = fa.html("a.b", '<a href="{url}">go</a>\n<pre>{a}\n{b}</pre>\nafter',
                  url="u", a="one", b="two")
    assert out == (RLM + '<a href="u">go</a>' + RLM + "\n<pre>one\ntwo</pre>\n"
                   + RLM + "after" + RLM)
    assert fa.html("a.b", "x:\n<pre>log</pre>") == RLM + "x:" + RLM + "\n<pre>log</pre>"
    # A parameter that is the block's whole content is isolated outside
    # its tags; the marks still stay away from the block.
    assert fa.html("a.b", "x:\n<pre>{o}</pre>", o="log") == (
        RLM + "x:" + RLM + "\n" + FSI + "<pre>log</pre>" + PDI)
    # A fence built in code (no pattern holds one): nothing beside it.
    assert fa.line("x\n```\ncode\n```") == RLM + "x\n```\ncode\n```"
    assert fa.line("```\ncode\n```\ny") == "```\ncode\n```\ny" + RLM


def test_a_quote_gets_no_mark_outside_it():
    fa = i18n.get("fa")
    # A quote aipager wrote: its own lines are marked inside it, and no
    # mark stands before its opening tag or after its closing one.
    out = fa.html("a.b", "x\n<blockquote>q1\nq2</blockquote>\ny")
    assert out == (RLM + "x" + RLM + "\n<blockquote>q1" + RLM + "\n" + RLM + "q2" + RLM
                   + "</blockquote>\n" + RLM + "y" + RLM)
    out = fa.html("a.b", "x\n<blockquote expandable>q1\n  <i>q2</i></blockquote>")
    assert out.endswith("\n" + RLM + "  <i>q2" + RLM + "</i></blockquote>")
    # A quote of Claude's text (a parameter on the quote's own line):
    # nothing around it, nothing inside it.
    out = fa.html("a.b", "<i>t</i>\n<blockquote>{p}</blockquote>", p="one\ntwo")
    assert out == RLM + "<i>t</i>" + RLM + "\n<blockquote>" + FSI + "one\ntwo" + PDI + "</blockquote>"
    out = fa.html("a.b", "x\n<blockquote>{p}</blockquote>\ny", p=Safe("a\nb"))
    assert out == RLM + "x" + RLM + "\n<blockquote>a\nb</blockquote>\n" + RLM + "y" + RLM
    # A quote opened on the line itself: no mark goes inside it either.
    assert fa.html("a.b", "x <blockquote>{p}</blockquote>", p="q") == (
        RLM + "x <blockquote>" + FSI + "q" + PDI + "</blockquote>")
    # Never inside code that ends the quote.
    out = fa.html("a.b", "x\n<blockquote>a\nb <code>c</code></blockquote>")
    assert out.endswith("\n" + RLM + "b <code>c</code></blockquote>")


def test_blank_lines_hold_no_mark():
    fa = i18n.get("fa")
    assert fa.md("a.b", "# t\n\n- i") == "# " + RLM + "t" + RLM + "\n\n- " + RLM + "i" + RLM
    assert fa.text("a.b", "\nx\n") == "\n" + RLM + "x" + RLM + "\n"
    assert fa.text("a.b", "x\n  \ny") == RLM + "x" + RLM + "\n  \n" + RLM + "y" + RLM


def test_a_newline_inside_a_parameter_gets_no_mark():
    fa = i18n.get("fa")
    assert fa.text("a.b", "{reason}", reason="line one\nline two") == (
        RLM + FSI + "line one\nline two" + PDI + RLM)
    assert fa.html("a.b", "x {s}", s=Safe("a\nb")) == RLM + "x a\nb" + RLM


def test_nested_pieces_leave_no_two_marks_side_by_side():
    fa = i18n.get("fa")
    a, b = fa.text("a.c", RAH), fa.text("a.d", "y")
    out = fa.text("a.b", "{a}: {b}", a=Safe(a), b=Safe(b))
    assert out == RLM + RAH + RLM + ": " + RLM + "y" + RLM
    # The last mark of a run is the one kept (Telegram keeps it too).
    assert fa.text("a.b", "{x}", x=Safe("abc" + LRM)) == RLM + "abc" + RLM
    assert fa.text("a.b", "{x}", x=Safe(LRM + "abc")) == LRM + "abc" + RLM
    # Never inside code, even where the code holds a run.
    code = Safe("<code>a" + RLM + RLM + "b</code>")
    assert fa.html("a.b", "x {c}", c=code) == RLM + "x <code>a" + RLM + RLM + "b</code>" + RLM


def test_an_emphasis_closer_after_punctuation_keeps_its_place():
    fa = i18n.get("fa")
    # CommonMark: "*(x)*" followed by a mark would no longer close.
    assert fa.md("a.b", "*(x)*") == RLM + "*(x)" + RLM + "*"
    assert fa.md("a.b", "x **(y)**") == RLM + "x **(y)" + RLM + "**"
    # After a letter the closer is fine as it is; an escaped star is text.
    assert fa.md("a.b", "**x**") == RLM + "**x**" + RLM
    assert fa.md("a.b", "x.\\*") == RLM + "x.\\*" + RLM
    assert fa.md("a.b", "x.\\\\*") == RLM + "x.\\\\" + RLM + "*"
    # A symbol counts as punctuation (CommonMark's punctuation is P and S).
    assert fa.md("a.b", "x *y+*") == RLM + "x *y+" + RLM + "*"


def test_an_underscore_closer_keeps_its_place_after_a_letter():
    """A ``_`` run followed by anything but a space or punctuation never
    closes (CommonMark), and U+200F is neither: the end mark goes before
    it even after a letter, where a ``*`` run keeps it after."""
    fa = i18n.get("fa")
    assert fa.md("a.b", "x _y_") == RLM + "x _y" + RLM + "_"
    assert fa.md("a.b", "x __y__") == RLM + "x __y" + RLM + "__"
    assert fa.md("a.b", "x *y*") == RLM + "x *y*" + RLM
    assert fa.md("a.b", "x \\_y\\_") == RLM + "x \\_y\\_" + RLM
    # A run that is the whole line is text, not a closer.
    assert fa.md("a.b", "_") == RLM + "_" + RLM
    assert fa.md("a.b", "__") == RLM + "__" + RLM


def test_an_underscore_opener_keeps_its_place_at_a_line_start():
    """By the same rule a ``_`` run right after U+200F never opens: in
    Markdown the start mark goes after an opening ``_``/``__`` run, like
    after a block marker (the full log's italic memory note)."""
    fa = i18n.get("fa")
    assert fa.md("a.b", "_word: x_") == "_" + RLM + "word: x" + RLM + "_"
    assert fa.md("a.b", "- __x__") == "- __" + RLM + "x" + RLM + "__"
    assert fa.md("a.b", "a\n_b_") == RLM + "a" + RLM + "\n_" + RLM + "b" + RLM + "_"
    assert fa.line("_x · 4:10_", kind="md") == "_" + RLM + "x · 4:10" + RLM + "_"
    # Not an opener followed by text: a space, a longer run, or no text.
    assert fa.md("a.b", "_ x") == RLM + "_ x" + RLM
    assert fa.md("a.b", "___x___") == RLM + "___x" + RLM + "___"
    assert fa.md("a.b", "_") == RLM + "_" + RLM
    # A star after the mark opens fine.
    assert fa.md("a.b", "*x*") == RLM + "*x*" + RLM
    # The left-to-right twin is untouched.
    twin = fa.directed(False)
    assert twin.md("a.b", "_x_") == "_x_"
    from aipager.bot.animation import LOG_MEMORY
    for code in ("fa", "ar"):
        note = i18n.get(code).n(LOG_MEMORY, 500, kind="md")
        assert note.startswith("_" + RLM) and note.endswith("." + RLM + "_"), ascii(note)


def test_an_underscore_in_a_name_is_not_emphasis():
    """A ``_`` run is moved across only when a run of the same length
    pairs with it on the line; ``_private_var`` and ``my_var_`` are names."""
    fa = i18n.get("fa")
    assert fa.md("a.b", "_private_var") == RLM + "_private_var" + RLM
    assert fa.md("a.b", "x my_var_") == RLM + "x my_var_" + RLM
    # The opener pairs only with a closer of its own length.
    assert fa.md("a.b", "_x__") == RLM + "_x__" + RLM
    assert fa.md("a.b", "__x_") == RLM + "__x_" + RLM
    # A closer only with an opener before it, of its own length.
    assert fa.md("a.b", "x_ y_") == RLM + "x_ y_" + RLM
    assert fa.md("a.b", "x __y_") == RLM + "x __y_" + RLM
    # A run with a space on both sides neither opens nor closes.
    assert fa.md("a.b", "x _ y_") == RLM + "x _ y_" + RLM
    # An escaped opener opens nothing.
    assert fa.md("a.b", "\\_x_") == RLM + "\\_x_" + RLM
    # Paired, the marks go inside as before.
    assert fa.md("a.b", "x _my_var_") == RLM + "x _my_var" + RLM + "_"
    assert fa.md("a.b", "(_x_)") == RLM + "(_x_)" + RLM


@pytest.mark.parametrize("kind", ["html", "text", None])
def test_emphasis_rules_are_markdown_only(kind):
    """In HTML and plain text ``*`` and ``_`` are characters: no mark
    goes into a name or a path (/status ending in ``ls __pycache__``)."""
    fa = i18n.get("fa")
    for text in ("x foo_", "Reading __init__", "__init__.py changed", "_x_", "x *(y)*",
                 "\u23f3 x \u00b7 ls __pycache__", "**"):
        assert fa.line(text, kind=kind) == RLM + text + RLM, (kind, text)
    if kind is not None:
        render = fa.html if kind == "html" else fa.text
        assert render("a.b", "x foo_") == RLM + "x foo_" + RLM
        assert render("a.b", "_x_") == RLM + "_x_" + RLM
        assert render("a.b", "x *(y)*") == RLM + "x *(y)*" + RLM


def test_marks_in_a_markdown_code_span_stay_as_they_are():
    """Markdown output (and a line built in code, which may be Markdown)
    keeps a run of marks inside a backtick span; in HTML a backtick is
    only text."""
    fa = i18n.get("fa")
    span = "`echo a" + LRM + RLM + "b`"
    assert fa.md("a.b", "x {s}", s=Safe(span)) == RLM + "x " + span + RLM
    assert fa.md("a.b", "x {s} y", s=Safe("``a" + LRM + RLM + "b``")) == (
        RLM + "x ``a" + LRM + RLM + "b`` y" + RLM)
    assert fa.line("x " + span) == RLM + "x " + span + RLM
    assert fa.line("x " + span, kind="md") == RLM + "x " + span + RLM
    assert fa.line("x " + span, kind="html") == RLM + "x `echo a" + RLM + "b`" + RLM
    both = "<code>a" + LRM + RLM + "b</code> " + span
    assert fa.line("x " + both) == RLM + "x " + both + RLM
    assert fa.html("a.b", "x {s}", s=Safe(span)) == RLM + "x `echo a" + RLM + "b`" + RLM
    # An escaped backtick opens nothing, and runs of different lengths
    # make no span: the marks between them are collapsed.
    for left, right in (("\\`", "`"), ("``", "`"), ("`", "``")):
        piece = Safe(left + "a" + LRM + RLM + "b" + right)
        assert fa.md("a.b", "x {s}", s=piece) == RLM + "x " + left + "a" + RLM + "b" + right + RLM


#: The noncharacters the marks are built from (U+FDD0 the twin's line
#: slot, U+FDD1 a line start, U+FDD2 a line end) and what a value holding
#: one shows instead.
SENTINELS = "\ufdd0\ufdd1\ufdd2"
FFFD = "\ufffd"


@pytest.mark.parametrize("code", ["fa", "fa-ltr"])
def test_a_value_never_adds_a_mark(code):
    tr = i18n.get("fa").directed(code == "fa")
    out = tr.text("a.b", "x {t} y", t="e" + SENTINELS + "\nm")
    assert FSI + "e" + FFFD * 3 + "\nm" + PDI in out
    out = tr.html("a.b", "x <code>{c}</code>", c=Safe("a" + SENTINELS + "b"))
    assert "<code>a" + FFFD * 3 + "b</code>" in out
    from aipager.i18n import Command
    from aipager.i18n.core import Span
    out = tr.md("a.b", "x {s} {c}", s=Span("`a" + SENTINELS + "`"), c=Command("/a" + SENTINELS))
    assert "`a" + FFFD * 3 + "`" in out and "/a" + FFFD * 3 in out
    for out in (tr.text("a.b", "x {t} y", t="e" + SENTINELS + "\nm"),
                tr.html("a.b", "x <code>{c}</code>", c=Safe("a" + SENTINELS + "b"))):
        assert not set(SENTINELS) & set(out)
        assert out.count(RLM) == (2 if code == "fa" else 0)


def test_line_never_turns_a_placeholder_into_a_mark():
    fa = i18n.get("fa")
    assert fa.line("\ufdd2x\ufdd1y\ufdd0") == RLM + FFFD + "x" + FFFD + "y" + FFFD + RLM


def test_line_keeps_outer_line_breaks_outside_its_marks():
    fa = i18n.get("fa")
    assert fa.line("x\n") == RLM + "x" + RLM + "\n"
    assert fa.line("\n\nx\ny\n") == "\n\n" + RLM + "x\ny" + RLM + "\n"
    assert fa.line(RLM + "x" + RLM + "\n") == RLM + "x" + RLM + "\n"
    assert fa.line("\n\n") == "\n\n"
    assert isinstance(fa.line(Safe("x\n")), Safe)


def test_a_plural_ends_with_a_mark():
    fa = i18n.get("fa")
    things = Plural("a.things", one="{n} thing on {s}", other="{n} things on {s}")
    assert fa.n(things, 2, s="x1") == RLM + "۲ things on " + FSI + "x1" + PDI + RLM


def test_line_marks_a_line_built_in_code():
    fa = i18n.get("fa")
    assert fa.line("x · 4:10") == RLM + "x · 4:10" + RLM
    assert fa.line(RLM + "a" + RLM + RLM + "b" + RLM) == RLM + "a" + RLM + "b" + RLM
    assert fa.line("- item") == "- " + RLM + "item" + RLM
    # A piece's own start mark goes, so the block marker stays first.
    assert fa.line(RLM + "- item") == "- " + RLM + "item" + RLM
    # A last line that is only an emphasis run is left whole.
    assert fa.line("x\n**") == RLM + "x\n**" + RLM
    assert fa.line("x\ny") == RLM + "x\ny" + RLM
    assert fa.line("<pre>x</pre>") == "<pre>x</pre>"
    assert fa.line("x\n<pre>y</pre>") == RLM + "x\n<pre>y</pre>"
    assert fa.line("") == "" and fa.line("  ") == "  "
    once = fa.line(fa.html("a.b", "- {s} y", s="x1"))
    assert fa.line(once) == once
    assert isinstance(fa.line(Safe("x")), Safe)
    assert type(fa.line("x")) is str


def test_bare_and_lead():
    fa = i18n.get("fa")
    piece = fa.text("a.b", "x")
    assert fa.bare(piece) == "x"
    assert fa.bare(RLM + RLM + "a" + RLM + "b" + RLM) == "a" + RLM + "b"
    assert fa.lead(piece) == RLM + "x"
    assert isinstance(fa.bare(Safe(piece)), Safe) and isinstance(fa.lead(Safe(piece)), Safe)
    assert type(fa.bare(piece)) is str


@pytest.mark.parametrize("code", ["en", "de", "fa-ltr", "ar-ltr"])
def test_line_bare_and_lead_leave_other_directions_alone(code):
    tr = (i18n.get(code[:2]).directed(False) if code.endswith("-ltr") else i18n.get(code))
    for text in ("x · 4:10", RLM + "x" + RLM, Safe("- y")):
        assert tr.line(text) is text
        assert tr.bare(text) is text
        assert tr.lead(text) is text
    assert tr.mark == ""


def test_the_left_to_right_twin_and_english_are_unchanged():
    """The bytes the previous release rendered (recorded from it)."""
    from aipager.i18n import Command
    fa, ar, en = i18n.get("fa"), i18n.get("ar"), i18n.get("en")
    got = []
    for tr in (fa.directed(False), ar.directed(False), en):
        got += [
            tr.html("a.b", RAH + " {s}\n# x\n\n" + RAH + " 12 <code>{c}</code>", s="x1", c="ls"),
            tr.md("a.b", "# {s} " + RAH + "\n- item " + RAH + " · 3", s="x1"),
            tr.text("a.b", "{a} \u00b7 " + RAH + " {b}", a=Safe(RLM + RAH + RLM),
                    b=Command("/x1")),
            tr.html("a.b", "x\n<blockquote>" + RAH + "\nq2</blockquote>\n<pre>{p}</pre>",
                    p="a\nb"),
        ]
    assert got == [
    '\u200e\u2067\u0631\u0627\u0647\u2069 \u2068x1\u2069\n# x\n\n\u200e\u2067\u0631\u0627\u0647\u2069 12 \u2068<code>ls</code>\u2069',
    '# \u200e\u2068x1\u2069 \u2067\u0631\u0627\u0647\u2069\n- item \u2067\u0631\u0627\u0647\u2069 \xb7 3',
    '\u200e\u200f\u2067\u0631\u0627\u0647\u2069\u200f \xb7 \u2067\u0631\u0627\u0647\u2069 \u2068/x1\u2069',
    'x\n\u200e<blockquote>\u2067\u0631\u0627\u0647\u2069\nq2</blockquote>\n\u200e\u2068<pre>a\nb</pre>\u2069',
    '\u200e\u2067\u0631\u0627\u0647\u2069 \u2068x1\u2069\n# x\n\n\u200e\u2067\u0631\u0627\u0647\u2069 12 \u2068<code>ls</code>\u2069',
    '# \u200e\u2068x1\u2069 \u2067\u0631\u0627\u0647\u2069\n- item \u2067\u0631\u0627\u0647\u2069 \xb7 3',
    '\u200e\u200f\u2067\u0631\u0627\u0647\u2069\u200f \xb7 \u2067\u0631\u0627\u0647\u2069 \u2068/x1\u2069',
    'x\n\u200e<blockquote>\u2067\u0631\u0627\u0647\u2069\nq2</blockquote>\n\u200e\u2068<pre>a\nb</pre>\u2069',
    '\u0631\u0627\u0647 x1\n# x\n\n\u0631\u0627\u0647 12 <code>ls</code>',
    '# x1 \u0631\u0627\u0647\n- item \u0631\u0627\u0647 \xb7 3',
    '\u200f\u0631\u0627\u0647\u200f \xb7 \u0631\u0627\u0647 /x1',
    'x\n<blockquote>\u0631\u0627\u0647\nq2</blockquote>\n<pre>a\nb</pre>',
    ]


def test_num_converts_separators_in_fa_only():
    fa, ar, en, ru = (i18n.get(c) for c in ("fa", "ar", "en", "ru"))
    assert fa.num("$1,234.50") == "$۱٬۲۳۴٫۵۰"
    assert fa.num("45%") == "۴۵٪"
    assert ar.num("45%") == "٤٥٪\u061c"
    assert en.num("$1,234.50") == "$1,234.50"
    assert ru.num("45%") == "45%"
    assert en.num("45%") == "45%"
    assert fa.clock(250) == "۴:۱۰"


def test_the_line_mark_is_rtl_only():
    assert i18n.get("fa").mark == RLM
    assert i18n.get("en").mark == "" and i18n.get("ru").mark == ""
