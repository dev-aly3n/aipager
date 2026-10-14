"""Black-box over every surface of tests/i18n_surfaces.py: in fa and ar,
each line holding a Persian/Arabic letter sits on the right under the
iPhone rule (tests.bidi_uba.sits_right_on_iphone), outside the surfaces
that follow Claude, the ``/`` menu join and the Mini App; no surface in
en, fa or ar holds two adjacent LRM/RLM; nothing invisible inside code,
commands or between word characters; ``/`` menu descriptions carry the
leading mark only; English equals its golden (design.md success
criteria 4, 5, 7, 9; entrypoints.md "Side effects").

Each language is rendered once per module (rendering is heavy)."""

from __future__ import annotations

import re

import pytest

from tests import bidi_uba, i18n_surfaces

RLM, LRM = "\u200f", "\u200e"
_PAIR = re.compile("[\u200e\u200f]{2}")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|`+[^`\n]*`+", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPLIT_NAME = re.compile("[/@][\u200e\u200f]")
_SPLIT_WORD = re.compile("\\w[\u200e\u200f]\\w")
_CACHE: dict[str, dict[str, list[str]]] = {}


def _renders(code, mk_bot, run_async, monkeypatch):
    if code not in _CACHE:
        names = {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING
        _CACHE[code] = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code,
                                                names=names)
    return _CACHE[code]


def _judged(name):
    return not (name in i18n_surfaces.FOLLOWS_CLAUDE or name == "menu.commands"
                or name.startswith("miniapp."))


def _left_lines(renders):
    """(surface, line) for every judged line with a right-to-left letter
    that does not sit right on iPhone."""
    bad = []
    for name, texts in renders.items():
        if not _judged(name):
            continue
        for text in texts:
            for line in bidi_uba.shown_lines(text):
                if bidi_uba.has_rtl_letter(line) and not bidi_uba.sits_right_on_iphone(line):
                    bad.append((name, line))
    return bad


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_every_right_to_left_line_sits_right_on_iphone(code, mk_bot, run_async, monkeypatch):
    bad = _left_lines(_renders(code, mk_bot, run_async, monkeypatch))
    assert bad == [], f"{len(bad)} lines sit left, e.g. {bad[:10]!r}"


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_the_surfaces_hold_right_to_left_lines_to_judge(code, mk_bot, run_async, monkeypatch):
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    judged = [line for name, texts in renders.items() if _judged(name) for t in texts
              for line in bidi_uba.shown_lines(t) if bidi_uba.has_rtl_letter(line)]
    assert len(judged) > 1000


def _lines_with(renders, surface, needle):
    return [line for text in renders[surface] for line in bidi_uba.shown_lines(text)
            if needle in line]


def test_the_help_title_sits_right_in_persian(mk_bot, run_async, monkeypatch):
    lines = _lines_with(_renders("fa", mk_bot, run_async, monkeypatch), "help.dm",
                        "\u0631\u0627\u0647\u0646\u0645\u0627\u06cc aipager")
    assert lines and all(bidi_uba.sits_right_on_iphone(line) for line in lines)


def test_the_first_contact_language_line_sits_right_in_persian(mk_bot, run_async, monkeypatch):
    lines = _lines_with(_renders("fa", mk_bot, run_async, monkeypatch),
                        "language.first_contact", "/language")
    assert lines and all(bidi_uba.sits_right_on_iphone(line) for line in lines)


def test_the_first_contact_language_line_ends_with_the_command_then_a_mark(
        mk_bot, run_async, monkeypatch):
    lines = _lines_with(_renders("fa", mk_bot, run_async, monkeypatch),
                        "language.first_contact", "/language")
    assert lines and all(re.search("/language\u2069?\u200f$", line) for line in lines)


@pytest.mark.parametrize("code", ("en", "fa", "ar"))
def test_no_surface_holds_two_adjacent_marks(code, mk_bot, run_async, monkeypatch):
    bad = [(name, text) for name, texts in _renders(code, mk_bot, run_async, monkeypatch).items()
           for text in texts if _PAIR.search(text)]
    assert bad == [], f"{len(bad)} texts, e.g. {bad[:5]!r}"


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_no_mark_inside_code(code, mk_bot, run_async, monkeypatch):
    bad = [(name, span) for name, texts in _renders(code, mk_bot, run_async, monkeypatch).items()
           for text in texts for span in _CODE.findall(text) if RLM in span or LRM in span]
    assert bad == [], bad[:10]


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_no_mark_between_a_slash_or_at_and_a_name(code, mk_bot, run_async, monkeypatch):
    bad = [(name, text) for name, texts in _renders(code, mk_bot, run_async, monkeypatch).items()
           for text in texts if _SPLIT_NAME.search(_TAG.sub("", text))]
    assert bad == [], bad[:10]


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_no_mark_between_two_word_characters(code, mk_bot, run_async, monkeypatch):
    bad = [(name, m.group()) for name, texts in
           _renders(code, mk_bot, run_async, monkeypatch).items()
           for text in texts for m in _SPLIT_WORD.finditer(_TAG.sub("", text))]
    assert bad == [], bad[:10]


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_surfaces_that_follow_claude_end_no_line_with_a_mark(code, mk_bot, run_async,
                                                             monkeypatch):
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    bad = [(name, line) for name in i18n_surfaces.FOLLOWS_CLAUDE for text in renders[name]
           for line in _TAG.sub("", text).split("\n") if line.endswith(RLM)]
    assert bad == [], bad[:10]


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_surfaces_that_follow_claude_still_read_left_to_right(code, mk_bot, run_async,
                                                              monkeypatch):
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    for name in sorted(i18n_surfaces.FOLLOWS_CLAUDE):
        for text in renders[name]:
            i18n_surfaces.assert_follows_claude(name, text)


def _descriptions(renders):
    return [text.split(" ", 1)[1] for text in renders["menu.commands"]]


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_menu_descriptions_start_with_a_mark(code, mk_bot, run_async, monkeypatch):
    descs = _descriptions(_renders(code, mk_bot, run_async, monkeypatch))
    assert descs and all(d.startswith(RLM) for d in descs)


@pytest.mark.parametrize("code", ("fa", "ar"))
def test_menu_descriptions_do_not_end_with_a_mark(code, mk_bot, run_async, monkeypatch):
    descs = _descriptions(_renders(code, mk_bot, run_async, monkeypatch))
    assert [d for d in descs if d.endswith(RLM)] == []


def test_english_surfaces_equal_the_golden(mk_bot, run_async, monkeypatch):
    renders = _renders("en", mk_bot, run_async, monkeypatch)
    golden = i18n_surfaces.load_golden()
    assert {n: renders.get(n) for n in golden} == golden


def test_english_surfaces_hold_no_mark(mk_bot, run_async, monkeypatch):
    renders = _renders("en", mk_bot, run_async, monkeypatch)
    bad = [name for name, texts in renders.items() for t in texts if RLM in t or LRM in t]
    assert bad == []
