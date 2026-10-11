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
    assert ar.text("a.b", "{n}", n=2026).endswith("٢٠٢٦")
    assert fa.num(0.25) == "۰٫۲۵"
    assert fa.num(3.0) == "۳"
    assert i18n.get("de").num(1.5) == "1.5"


# ---- right to left --------------------------------------------------------

def test_rtl_marks_never_enter_code():
    fa = i18n.get("fa")
    out = fa.html("a.b", "Run <code>{cmd}</code>\nthen {name}", cmd="ls -la", name="x1")
    assert out == (RLM + "Run " + FSI + "<code>ls -la</code>" + PDI + "\n"
                   + RLM + "then " + FSI + "x1" + PDI)
    # A placeholder that is only part of a code span is not isolated.
    out = fa.html("a.b", "<code>/{label} stop</code>", label="x1")
    assert out == RLM + "<code>/x1 stop</code>"
    # Nothing invisible in an attribute or inside <pre>.
    out = fa.html("a.b", '<a href="{url}">go</a>\n<pre>{a}\n{b}</pre>',
                  url="https://x.y/?a=1", a="one", b="two")
    assert 'href="https://x.y/?a=1"' in out
    pre = out[out.index("<pre>"):out.index("</pre>")]
    assert not any(ch in pre for ch in (RLM, FSI, PDI))
    assert fa.html("a.b", "{x}", x=Safe("<i>s</i>")) == RLM + "<i>s</i>"


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
    assert out == RLM + FSI + "`Read: /a_b*.py`" + PDI + " done in ۱۲"
    inner = out[out.index("`"):out.rindex("`") + 1]
    assert not any(ch in inner for ch in (RLM, FSI, PDI))
    html = fa.html("a.b", "{c} ok", c=Span("<code>a&lt;1</code>"))
    assert html == RLM + FSI + "<code>a&lt;1</code>" + PDI + " ok"
    # Inside a code element the span is left alone.
    assert fa.html("a.b", "<b>{c}</b>", c=Span("x 1")) == RLM + "<b>" + FSI + "x 1" + PDI + "</b>"
    assert fa.html("a.b", "<code>{c}</code>", c=Span("x 1")) == RLM + "<code>x 1</code>"
    # English: the bytes as passed.
    assert en.md("a.b", "{row} done", row=span) == "`Read: /a_b*.py` done"
    assert en.html("a.b", "{c}", c=Span("<i>&</i>")) == "<i>&</i>"


def test_a_command_keeps_its_slash_next_to_the_isolate(monkeypatch):
    from aipager.i18n import Command
    fa, en = i18n.get("fa"), i18n.get("en")
    out = fa.html("a.b", "Send {cmd} to {who}", cmd=Command("/x12"), who=Command("@b<b"))
    assert out == RLM + "Send " + FSI + "/x12" + PDI + " to " + FSI + "@b&lt;b" + PDI
    # Never digit-converted, never split: "/" directly before the name.
    assert "/x12" in out and "۱" not in out
    assert en.html("a.b", "Send {cmd}", cmd=Command("/x1&")) == "Send /x1&amp;"
    assert en.text("a.b", "Send {cmd}", cmd=Command("/x1&")) == "Send /x1&"
    # The one switch for rule 7: commands without isolates.
    monkeypatch.setattr(core, "ISOLATE_COMMANDS", False)
    assert fa.text("a.b", "Send {cmd}", cmd=Command("/x1")) == RLM + "Send /x1"


def test_rtl_mark_follows_a_heading_or_list_marker():
    fa, en = i18n.get("fa"), i18n.get("en")
    out = fa.md("a.b", "# {s} log\n\n- item\n> quote\n## Agents\n1. one\nplain",
                s="x1")
    assert out.split("\n") == [
        "# " + RLM + FSI + "x1" + PDI + " log", RLM, "- " + RLM + "item",
        "> " + RLM + "quote", "## " + RLM + "Agents", "1. " + RLM + "one", RLM + "plain"]
    # Only at a line start, and only where aipager put the mark.
    assert fa.text("a.b", "a - b # c") == RLM + "a - b # c"
    assert fa.text("a.b", "{x}", x="- y") == RLM + FSI + "- y" + PDI
    assert fa.html("a.b", "#tag") == RLM + "#tag"
    assert en.md("a.b", "# x\n- y") == "# x\n- y"


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
