"""Black-box: the rules of design.md's "Orchestrator amendment (fix
iteration 1)" as the orchestrator stated them for iteration 2.

1. md and ``Tr.line``: an opening ``_``/``__`` run at a line start (after
   an optional block marker) followed by non-space text takes the start
   mark after it; a lone ``_`` run, ``_ x``, ``___`` runs, ``*`` runs,
   html/text output and the left-to-right twin are unchanged.
2. md: the end mark goes before a closing run holding a ``_``; ``*`` runs
   keep the punctuation-only rule.
3. Noncharacters U+FDD0..U+FDD2 in a parameter value or ``Tr.line`` input
   never become marks; they come out as U+FFFD.
4. md and ``Tr.line``: no mark collapse inside a backtick code span.
5. ``Tr.line`` keeps leading/trailing newlines outside its marks.
6. ``kbwords.custom_action`` matches a label with RLM at its ends, an
   exact match first.
7. ``tools/i18n.py pseudo --rtl`` marks each non-blank line at both ends.

Invisible characters and noncharacters are written as escapes only
(tests/test_bidi_guard.py)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aipager import i18n
from aipager.bot import kbwords
from aipager.i18n import Command, Safe, Span
from tests import bidi_uba

RLM, LRM, FFFD = "\u200f", "\u200e", "\ufffd"
NONCHARS = ("\ufdd0", "\ufdd1", "\ufdd2")
RTL = ("fa", "ar")
_PAIR = re.compile("[\u200e\u200f]{2}")
_ROOT = Path(__file__).resolve().parents[3]


def _md(code, pattern, **params):
    return str(i18n.get(code).md("zz.case", pattern, **params))


# ---- 1. an opening underscore run at a line start ---------------------------------------

@pytest.mark.parametrize("code", RTL)
def test_md_the_start_mark_goes_after_an_opening_underscore(code):
    assert _md(code, "_word: x_") == "_" + RLM + "word: x" + RLM + "_"


@pytest.mark.parametrize("code", RTL)
def test_md_the_start_mark_goes_after_a_list_marker_and_a_double_underscore(code):
    assert _md(code, "- __x__") == "- __" + RLM + "x" + RLM + "__"


@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("marker", ("# ", "## ", "> ", "1. "))
def test_md_the_start_mark_goes_after_any_block_marker_and_an_underscore(code, marker):
    assert _md(code, marker + "_q_") == marker + "_" + RLM + "q" + RLM + "_"


@pytest.mark.parametrize("code", RTL)
def test_md_a_double_underscore_line_of_letters_keeps_both_marks_inside(code):
    assert _md(code, "__bold__") == "__" + RLM + "bold" + RLM + "__"


@pytest.mark.parametrize("code", RTL)
def test_md_every_line_opening_with_an_underscore_takes_the_mark_after_it(code):
    out = _md(code, "_a_\nplain\n- _b_")
    assert out.split("\n") == ["_" + RLM + "a" + RLM + "_", RLM + "plain" + RLM,
                               "- _" + RLM + "b" + RLM + "_"]


@pytest.mark.parametrize("code", RTL)
def test_md_an_underscore_opener_before_a_parameter_takes_the_mark_after_it(code):
    assert _md(code, "_{v} x_", v="frontend").startswith("_" + RLM + "\u2068frontend")


@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("run", ("_", "__"))
def test_md_a_line_that_is_only_an_underscore_run_keeps_the_marks_outside(code, run):
    assert _md(code, run) == RLM + run + RLM


@pytest.mark.parametrize("code", RTL)
def test_md_an_underscore_followed_by_a_space_keeps_the_start_mark_before_it(code):
    assert _md(code, "_ x") == RLM + "_ x" + RLM


@pytest.mark.parametrize("code", RTL)
def test_md_a_triple_underscore_run_keeps_the_start_mark_before_it(code):
    assert _md(code, "___x___").startswith(RLM + "___x")


@pytest.mark.parametrize("code", RTL)
def test_md_a_star_opener_keeps_the_start_mark_before_it(code):
    assert _md(code, "*word: x*") == RLM + "*word: x*" + RLM


@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("kind", ("html", "text"))
def test_html_and_text_keep_the_start_mark_before_an_underscore(code, kind):
    out = str(getattr(i18n.get(code), kind)("zz.case", "_word: x_"))
    assert out.startswith(RLM + "_word")


@pytest.mark.parametrize("code", RTL)
def test_line_puts_the_start_mark_after_an_opening_underscore(code):
    assert i18n.get(code).line("_word: x_") == "_" + RLM + "word: x" + RLM + "_"


@pytest.mark.parametrize("code", RTL)
def test_line_puts_the_start_mark_after_a_list_marker_and_a_double_underscore(code):
    assert i18n.get(code).line("- __x__") == "- __" + RLM + "x" + RLM + "__"


@pytest.mark.parametrize("code", RTL)
def test_line_of_an_underscore_line_keeps_a_safe_a_safe(code):
    assert type(i18n.get(code).line(Safe("_word: x_"))) is Safe


@pytest.mark.parametrize("code", RTL)
def test_line_of_an_underscore_line_is_stable(code):
    tr = i18n.get(code)
    once = tr.line("_word: x_")
    assert tr.line(once) == once


@pytest.mark.parametrize("code", RTL)
def test_line_of_a_lone_underscore_keeps_the_marks_outside(code):
    assert i18n.get(code).line("_") == RLM + "_" + RLM


@pytest.mark.parametrize("code", RTL)
def test_line_of_an_underscore_and_a_space_keeps_the_start_mark_before_it(code):
    assert i18n.get(code).line("_ x").startswith(RLM + "_ x")


@pytest.mark.parametrize("code", RTL)
def test_line_of_a_star_opener_keeps_the_start_mark_before_it(code):
    assert i18n.get(code).line("*word: x*").startswith(RLM + "*word")


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_the_twin_of_an_underscore_line_holds_no_rlm(code):
    out = str(i18n.get(code).directed(False).md("zz.case", "_word: x_\n- __x__"))
    assert RLM not in out


# ---- 2. the end mark before a closing run that holds an underscore ----------------------

@pytest.mark.parametrize("code", RTL)
def test_md_the_end_mark_goes_before_an_underscore_closer_after_a_letter(code):
    assert _md(code, "x _y_") == RLM + "x _y" + RLM + "_"


@pytest.mark.parametrize("code", RTL)
def test_md_the_end_mark_goes_before_a_double_underscore_closer(code):
    assert _md(code, "x __y__") == RLM + "x __y" + RLM + "__"


@pytest.mark.parametrize("code", RTL)
def test_md_the_end_mark_goes_before_an_underscore_closer_after_a_parameter(code):
    assert _md(code, "x _{v}_", v="frontend").endswith("\u2069" + RLM + "_")


@pytest.mark.parametrize("code", RTL)
def test_md_a_star_closer_after_punctuation_follows_the_mark(code):
    assert _md(code, "x *y+*") == RLM + "x *y+" + RLM + "*"


@pytest.mark.parametrize("code", RTL)
def test_md_a_star_closer_after_a_letter_comes_before_the_mark(code):
    assert _md(code, "x *y*") == RLM + "x *y*" + RLM


@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("kind", ("html", "text"))
@pytest.mark.parametrize("pattern", ("x _y_", "x *y+*", "**word:**", "_word: x_"),
                         ids=["underscore", "star-after-plus", "double-star-after-colon",
                              "underscore-opener"])
def test_html_and_text_lines_ending_in_a_closer_like_run_sit_right_on_iphone(code, kind,
                                                                            pattern):
    """design.md step d lets the closer rule touch html/text ("harmless if
    it does"); whichever side of the run the end mark takes there, the
    line must still sit right on iPhone."""
    out = str(getattr(i18n.get(code), kind)("zz.case", pattern))
    assert bidi_uba.sits_right_on_iphone(out)


@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("kind", ("html", "text"))
@pytest.mark.parametrize("pattern", ("x _y_", "x *y+*", "**word:**", "_word: x_"),
                         ids=["underscore", "star-after-plus", "double-star-after-colon",
                              "underscore-opener"])
def test_html_and_text_end_mark_sits_at_the_end_or_before_the_closing_run(code, kind,
                                                                          pattern):
    out = str(getattr(i18n.get(code), kind)("zz.case", pattern))
    assert re.search("\u200f[*_~]*$", out)


# ---- 3. noncharacters in values ----------------------------------------------------------

def _trs():
    out = []
    for code in RTL:
        out += [(code, i18n.get(code)), (code + "-twin", i18n.get(code).directed(False))]
    return out


_TRS = _trs()
_TR_IDS = [name for name, _tr in _TRS]


def _wrap(container, value):
    return {"text": lambda v: v, "safe": Safe, "span": lambda v: Span("`" + v + "`"),
            "command": lambda v: Command("/x" + v)}[container](value)


_CONTAINERS = ("text", "safe", "span", "command")


@pytest.mark.parametrize("kind", ("html", "text", "md"))
@pytest.mark.parametrize("container", _CONTAINERS)
@pytest.mark.parametrize("nc", NONCHARS, ids=["fdd0", "fdd1", "fdd2"])
@pytest.mark.parametrize("tr", [t for _n, t in _TRS], ids=_TR_IDS)
def test_a_noncharacter_in_a_value_comes_out_as_a_replacement_character(tr, nc, container,
                                                                          kind):
    render = getattr(tr, kind)
    got = str(render("zz.case", "a {v}\nb", v=_wrap(container, "p" + nc + "q")))
    want = str(render("zz.case", "a {v}\nb", v=_wrap(container, "p" + FFFD + "q")))
    assert got == want


@pytest.mark.parametrize("kind", ("html", "text", "md"))
@pytest.mark.parametrize("container", _CONTAINERS)
@pytest.mark.parametrize("tr", [t for _n, t in _TRS], ids=_TR_IDS)
def test_noncharacters_at_a_value_edge_add_no_marks(tr, container, kind):
    render = getattr(tr, kind)
    value = "".join(NONCHARS) + "p" + "".join(NONCHARS)
    plain = str(render("zz.case", "{v}\nb", v=_wrap(container, "p")))
    got = str(render("zz.case", "{v}\nb", v=_wrap(container, value)))
    assert (got.count(RLM), got.count(LRM)) == (plain.count(RLM), plain.count(LRM))


@pytest.mark.parametrize("tr", [t for _n, t in _TRS], ids=_TR_IDS)
def test_no_noncharacter_survives_in_a_rendered_value(tr):
    out = str(tr.html("zz.case", "{a} {b}\n{c} {d}", a="\ufdd0", b=Safe("\ufdd1"),
                      c=Command("/x\ufdd2"), d=Span("`\ufdd0`")))
    assert not any(nc in out for nc in NONCHARS)


@pytest.mark.parametrize("nc", NONCHARS, ids=["fdd0", "fdd1", "fdd2"])
@pytest.mark.parametrize("code", RTL)
def test_a_noncharacter_in_line_input_comes_out_as_a_replacement_character(code, nc):
    tr = i18n.get(code)
    assert tr.line("a" + nc + "b") == tr.line("a" + FFFD + "b")


@pytest.mark.parametrize("code", RTL)
def test_noncharacters_at_the_ends_of_line_input_never_become_marks(code):
    out = i18n.get(code).line("".join(NONCHARS) + "a" + "".join(NONCHARS))
    assert out.count(RLM) == 2 and LRM not in out


@pytest.mark.parametrize("code", RTL)
def test_the_twin_line_turns_no_noncharacter_into_a_mark(code):
    out = i18n.get(code).directed(False).line("\ufdd0a\ufdd1b\ufdd2")
    assert RLM not in out and LRM not in out


# ---- 4. no collapse inside a Markdown code span -------------------------------------------

@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_md_keeps_a_mark_pair_inside_a_backtick_span_of_the_pattern(code):
    """qps-rtl transliterates the letters, so only the span's marks are compared."""
    out = _md(code, "see `a" + LRM + RLM + "b` now")
    inner = out[out.index("`") + 1:out.rindex("`")]
    assert inner[1:-1] == LRM + RLM


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_md_keeps_a_mark_pair_inside_a_backtick_span_of_a_safe_value(code):
    out = _md(code, "{v}", v=Safe("`a" + RLM + RLM + "b`"))
    assert "`a" + RLM + RLM + "b`" in out


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_md_still_collapses_a_pair_outside_the_backtick_span(code):
    out = _md(code, "{v}", v=Safe("x" + LRM + RLM + "y `a" + RLM + RLM + "b`"))
    assert "x" + RLM + "y" in out


@pytest.mark.parametrize("code", RTL)
def test_html_still_collapses_a_pair_between_backticks(code):
    out = str(i18n.get(code).html("zz.case", "{v}", v=Safe("`a" + RLM + RLM + "b`")))
    assert _PAIR.search(out) is None


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_line_keeps_a_mark_pair_inside_a_backtick_span(code):
    out = i18n.get(code).line("x `a" + LRM + RLM + "b` y")
    assert out == RLM + "x `a" + LRM + RLM + "b` y" + RLM


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_line_still_collapses_a_pair_outside_a_backtick_span(code):
    out = i18n.get(code).line("x" + RLM + RLM + "y `a" + RLM + RLM + "b`")
    assert out.startswith(RLM + "x" + RLM + "y `")


# ---- 5. Tr.line keeps outer newlines outside its marks -----------------------------------

@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
@pytest.mark.parametrize(("text", "want"), [
    ("x\n", RLM + "x" + RLM + "\n"),
    ("\n\n", "\n\n"),
    ("\nx", "\n" + RLM + "x" + RLM),
    ("\nx\n\n", "\n" + RLM + "x" + RLM + "\n\n"),
    ("\n", "\n"),
], ids=["trailing", "only-newlines", "leading", "both", "one-newline"])
def test_line_keeps_outer_newlines_outside_the_marks(code, text, want):
    assert i18n.get(code).line(text) == want


@pytest.mark.parametrize("code", RTL)
def test_line_of_a_safe_with_a_trailing_newline_stays_a_safe(code):
    assert type(i18n.get(code).line(Safe("x\n"))) is Safe


@pytest.mark.parametrize("code", RTL)
def test_line_with_a_trailing_newline_is_stable(code):
    tr = i18n.get(code)
    once = tr.line("x\n")
    assert tr.line(once) == once


# ---- 6. kbwords.custom_action with marks ----------------------------------------------------

_WRAPS = {"start": lambda s: RLM + s, "end": lambda s: s + RLM,
          "both": lambda s: RLM + s + RLM}


@pytest.fixture
def bot():
    return SimpleNamespace(
        _template_map={"Run tests": "Run the tests"},
        _command_map={"Clear": "/clear"},
        _model_map={"Opus": "/model opus"},
        registry=SimpleNamespace(live_labels=lambda chat_id: set()))


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", ("Run tests", "Clear", "Opus"))
def test_custom_action_matches_a_label_with_a_mark_at_its_ends(bot, label, wrap):
    assert kbwords.custom_action(bot, _WRAPS[wrap](label)) == kbwords.custom_action(bot, label)


@pytest.mark.parametrize("label", ("Run tests", "Clear", "Opus"))
def test_custom_action_recognises_each_bare_label(bot, label):
    assert kbwords.custom_action(bot, label) is not None


@pytest.mark.parametrize("wrap", _WRAPS)
def test_custom_action_prefers_an_exact_template_match(wrap):
    marked = _WRAPS[wrap]("Run tests")
    bot = SimpleNamespace(_template_map={"Run tests": "bare one", marked: "marked one"},
                          _command_map={}, _model_map={},
                          registry=SimpleNamespace(live_labels=lambda chat_id: set()))
    exact = SimpleNamespace(_template_map={marked: "marked one"}, _command_map={},
                            _model_map={},
                            registry=SimpleNamespace(live_labels=lambda chat_id: set()))
    assert kbwords.custom_action(bot, marked) == kbwords.custom_action(exact, marked)


@pytest.mark.parametrize("wrap", _WRAPS)
def test_custom_action_prefers_an_exact_model_match(wrap):
    marked = _WRAPS[wrap]("Opus")
    bot = SimpleNamespace(_template_map={}, _command_map={},
                          _model_map={"Opus": "/model opus", marked: "/model marked"},
                          registry=SimpleNamespace(live_labels=lambda chat_id: set()))
    assert kbwords.custom_action(bot, marked) == ("model", "/model marked")


def test_custom_action_does_not_match_an_unknown_label_with_marks(bot):
    assert kbwords.custom_action(bot, RLM + "Nope" + RLM) is None


# ---- 7. tools/i18n.py pseudo --rtl -------------------------------------------------------------

def _pseudo(tmp_path, text):
    """Runs the tool with HOME and the working directory in a fresh empty
    directory under tmp_path, so it can write nothing outside it."""
    home = tmp_path / "pseudo-home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(_ROOT),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, str(_ROOT / "tools" / "i18n.py"), "pseudo",
                           "--rtl", text], cwd=home, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=True)
    return proc.stdout.removesuffix("\n")


_MARKER = re.compile(r"^(#{1,6} |- |\d+\. |> )")


@pytest.mark.parametrize("text", [
    "alpha", "alpha {name}\n\nbeta <b>x</b>", "one\ntwo\nthree", "- item\n# title\nplain",
], ids=["one-line", "blank-between", "three-lines", "block-markers"])
def test_pseudo_rtl_marks_each_non_blank_line_at_both_ends(tmp_path, text):
    lines = [_MARKER.sub("", line, count=1) for line in _pseudo(tmp_path, text).split("\n")
             if line.strip()]
    assert lines and all(ln.startswith(RLM) and ln.endswith(RLM) for ln in lines)


def test_pseudo_rtl_leaves_a_blank_line_bare(tmp_path):
    assert _pseudo(tmp_path, "alpha\n\nbeta").split("\n")[1] == ""


def test_pseudo_rtl_holds_no_adjacent_marks(tmp_path):
    assert _PAIR.search(_pseudo(tmp_path, "a {x}\nb {y}\n\n- c")) is None


def test_pseudo_rtl_writes_nothing_in_its_home(tmp_path):
    _pseudo(tmp_path, "alpha\nbeta")
    assert list((tmp_path / "pseudo-home").iterdir()) == []
