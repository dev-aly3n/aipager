"""Every right-to-left line aipager sends sits on the right on iPhone too.

Telegram for iPhone right-aligns a line only when the glyph run at its
LEFT edge reads right to left, which in a right-to-left line is its
logical END; a Persian or Arabic line that starts with U+200F but ends
with an English word, a number or a command sits on the left. So every
such line also ends right to left: judged here, for fa and ar, on every
registered surface (tests/i18n_surfaces.py) with a real implementation
of the bidi algorithm (tests/bidi_uba.py). Also: no two direction marks
side by side anywhere (Telegram's server turns all but the last of such
a run into U+200C), no mark inside code or a command, reply-keyboard
labels still read as the same buttons, and the / menu descriptions are
marked at their start only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import bidi_uba, i18n_surfaces

RLM, LRM, FSI, PDI = "\u200f", "\u200e", "\u2068", "\u2069"
MARKS = (RLM, LRM)
CODES = ("fa", "ar")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|`+[^`]*`+", re.S)
_PAIR = re.compile("[" + LRM + RLM + "]{2}")
_RENDERS: dict[str, dict[str, list[str]]] = {}

#: Surfaces the iPhone judgement skips: those that carry Claude's text
#: (left to right by design), the / menu (a description is one field of
#: the list, not a message line: see test_menu_descriptions_mark_their_start_only)
#: and the Mini App (laid out by the browser from its <html dir>).
_NOT_JUDGED = i18n_surfaces.FOLLOWS_CLAUDE | {"menu.commands"}


def _names() -> set[str]:
    return {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING


def _renders(code, mk_bot, run_async, monkeypatch) -> dict[str, list[str]]:
    """Every surface in *code*, rendered once per test process."""
    if code not in _RENDERS:
        _RENDERS[code] = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code,
                                                  names=_names())
    return _RENDERS[code]


def _judged(renders):
    for name, texts in renders.items():
        if name in _NOT_JUDGED or name.startswith("miniapp."):
            continue
        for text in texts:
            yield name, text


@pytest.mark.parametrize("code", CODES)
def test_every_right_to_left_line_ends_right_to_left(code, mk_bot, run_async, monkeypatch):
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    judged = left = 0
    bad = []
    for name, text in _judged(renders):
        for line in bidi_uba.shown_lines(text):
            if not bidi_uba.has_rtl_letter(line):
                continue
            judged += 1
            if not bidi_uba.sits_right_on_iphone(line):
                left += 1
                bad.append((name, ascii(line)))
    assert not bad, f"{left} of {judged} lines sit left on iPhone: {bad[:20]}"
    assert judged > 2000


@pytest.mark.parametrize("code", CODES)
def test_claudes_option_rows_get_no_mark(code, mk_bot, run_async, monkeypatch):
    """A question's rows that are Claude's own words (its option labels)
    carry no mark; only a row whose label aipager wrote does."""
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    rows = [line for text in renders["prompt.keyboards"] for line in text.split("\n")
            if line.startswith("  ")]
    claudes = [row for row in rows if "Postgres" in row or "SQLite" in row]
    assert claudes
    for row in claudes:
        assert RLM not in row, ascii(row)
    assert any(row.endswith(" - x" + RLM) for row in rows)


@pytest.mark.parametrize("code", CODES)
def test_the_lines_the_operator_saw_sit_right(code, mk_bot, run_async, monkeypatch):
    """The lines that sat left before: /help's title ends in "aipager",
    the first-contact line in "/language"."""
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    for name, end in (("help.dm", "aipager"), ("language.first_contact", "/language")):
        lines = [line for text in renders[name] for line in bidi_uba.shown_lines(text)
                 if line.rstrip(RLM + PDI + " .").endswith(end)
                 and bidi_uba.has_rtl_letter(line)]
        assert lines, (name, end)
        for line in lines:
            assert line.endswith(RLM), (name, ascii(line))
            assert bidi_uba.sits_right_on_iphone(line), (name, ascii(line))


def _english_golden() -> dict[str, list[str]]:
    return i18n_surfaces.load_golden()


@pytest.mark.parametrize("code", CODES + ("en",))
def test_no_two_marks_side_by_side(code, mk_bot, run_async, monkeypatch):
    renders = (_english_golden() if code == "en"
               else _renders(code, mk_bot, run_async, monkeypatch))
    for name, texts in renders.items():
        for text in texts:
            assert not _PAIR.search(text), (name, ascii(text))


def _texts(renders):
    for name, texts in renders.items():
        for text in texts:
            if name == "menu.commands":
                text = text.split(" ", 1)[1]
            yield name, text


@pytest.mark.parametrize("code", CODES)
def test_no_mark_inside_code_or_commands(code, mk_bot, run_async, monkeypatch):
    marks = RLM + LRM + FSI + PDI
    for name, text in _texts(_renders(code, mk_bot, run_async, monkeypatch)):
        for span in _CODE.findall(text):
            assert not any(m in span for m in marks), (name, ascii(span))
        assert not re.search("(?<![\\w./-])[/@][" + marks + "]", text), (name, ascii(text))
        assert not re.search("[A-Za-z0-9_][" + marks + "][A-Za-z0-9_]", text), (
            name, ascii(text))


@pytest.mark.parametrize("code", CODES)
def test_keyboard_labels_read_as_their_english(code, mk_bot, run_async, monkeypatch):
    """A reply-keyboard label, tapped with its marks or with a client
    that dropped them, is the same button as its English label."""
    from aipager.bot import kbwords
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    english = _english_golden()
    bot = mk_bot()
    checked = 0
    for name in ("keyboard.main", "keyboard.main_group", "keyboard.templates",
                 "keyboard.commands", "keyboard.models"):
        labels, en_labels = renders[name][1:], english[name][1:]
        assert len(labels) == len(en_labels), name
        for label, en in zip(labels, en_labels):
            bare = label.replace(RLM, "").replace(LRM, "")
            for tapped in (label, bare):
                assert kbwords.classify(bot, tapped) == kbwords.classify(bot, en), (
                    name, ascii(tapped))
                assert kbwords.nav_action(tapped) == kbwords.nav_action(en), ascii(tapped)
                assert kbwords.bare_word(tapped) == kbwords.bare_word(en), ascii(tapped)
            checked += 1
    assert checked > 20


@pytest.mark.parametrize("code", CODES)
def test_menu_descriptions_mark_their_start_only(code, mk_bot, run_async, monkeypatch):
    """A / menu description sets its direction with U+200F at its start;
    it is a field of the / list, not a message line, so it carries no
    end mark (its 40 characters stay its own)."""
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    descriptions = [text.split(" ", 1)[1] for text in renders["menu.commands"]]
    assert len(descriptions) > 5
    for description in descriptions:
        assert description.startswith(RLM) and not description.startswith(RLM * 2), (
            ascii(description))
        assert not description.endswith(RLM), ascii(description)


def test_no_line_is_built_from_the_start_mark_alone():
    """A line built in code takes ``Tr.line`` (both ends), a nested piece
    ``Tr.bare``, a / menu field ``Tr.lead``: no module but the core uses
    ``.mark``, which marks one end only."""
    root = Path(__file__).resolve().parent.parent / "aipager"
    found = []
    for path in sorted(root.rglob("*.py")):
        if path == root / "i18n" / "core.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.mark\b", line):
                found.append(f"{path.relative_to(root)}:{n}")
    assert found == []
