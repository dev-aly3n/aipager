"""The golden surfaces in every language (multilingual phase 5, design 8.5).

``i18n_surfaces.GOLDEN_SURFACES`` (the render tests' golden surfaces, the
language list and the launch surfaces) rendered in all 14 languages: each
renders, no placeholder or raw key is left, no long dash; Persian and
Arabic read right to left with their own digits on the busy card's clock,
and no other language carries a direction mark; Chinese and Korean button
labels fit their width budgets.
"""

from __future__ import annotations

import re

import pytest

from aipager.i18n import core, locales
from tests import i18n_surfaces, i18n_tool

tool = i18n_tool.load()

CODES = tuple(c for c in locales.enabled_codes() if c != "en")
RLM = "\u200f"
MARKS = ("\u200e", "\u200f", "\u2066", "\u2067", "\u2068", "\u2069", "\u061c")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|`[^`]*`", re.S)
_HOLE = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")
_TAG = re.compile(r"<[^>]+>")
_RTL_LETTER = re.compile("[؀-ۿ]")
_LATIN_LETTER = re.compile("[A-Za-z]")
CLOCK = {"fa": "۴:۱۰", "ar": "٤:١٠"}
KEYS = {r["k"] for r in tool.load_jsonl(tool.SOURCE)}
_KEYISH = re.compile(r"\b[a-z0-9_]+(?:\.[a-z0-9_]+)+\b")


def test_the_golden_surfaces_are_registered_and_cover_the_launch():
    names = {n for n, _f in i18n_surfaces.SURFACES}
    assert set(i18n_surfaces.GOLDEN_SURFACES) <= names
    assert {"language.offer", "language.first_contact", "bot.descriptions",
            "language.list", "card.busy", "prompt.separate", "settings.root"} <= set(
        i18n_surfaces.GOLDEN_SURFACES)
    assert len(CODES) == 14


def _renders(code, mk_bot, run_async, monkeypatch):
    return i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code,
                                    names=set(i18n_surfaces.GOLDEN_SURFACES))


def _first_strong(line: str):
    for ch in _TAG.sub("", line):
        if ch == RLM:
            return "R"
        if _RTL_LETTER.match(ch):
            return "R"
        if _LATIN_LETTER.match(ch):
            return "L"
    return None


@pytest.mark.parametrize("code", CODES)
def test_every_golden_surface_renders_whole(code, mk_bot, run_async, monkeypatch):
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    assert set(renders) == set(i18n_surfaces.GOLDEN_SURFACES)
    rtl = locales.LOCALES[code].rtl
    for name, texts in renders.items():
        assert texts and all(isinstance(t, str) for t in texts), name
        for text in texts:
            outside = _CODE.sub(" ", text)
            assert not _HOLE.search(outside), (name, text)
            assert not [k for k in _KEYISH.findall(outside) if k in KEYS], (name, text)
            assert "—" not in text, (name, text)
            if not rtl:
                assert not any(m in text for m in MARKS), (name, text)


@pytest.mark.parametrize("code", [c for c in CODES if locales.LOCALES[c].rtl])
def test_right_to_left_lines_start_right_to_left(code, mk_bot, run_async, monkeypatch):
    assert code in ("fa", "ar")
    renders = _renders(code, mk_bot, run_async, monkeypatch)
    for name, texts in renders.items():
        for text in texts:
            if name == "menu.commands":
                text = text.split(" ", 1)[1]      # the command word is the menu's own
            for line in text.split("\n"):
                plain = _CODE.sub("", line)
                if _RTL_LETTER.search(plain):
                    assert _first_strong(line) == "R", (name, line)
    status = renders["card.busy"][0].rsplit("\n", 1)[-1]
    assert CLOCK[code] in status and "4:10" not in status


@pytest.mark.parametrize("code", ("zh-Hans", "ko"))
def test_wide_button_labels_fit_their_cells(code):
    cat = core._catalog(code)
    golden = [k for _s, k in tool.load_golden()]
    checked = 0
    for key in golden:
        budget = tool.budget_for(key)
        if not budget or budget["unit"] != "cells":
            continue
        value = cat[key]
        for form in (value.values() if isinstance(value, dict) else [value]):
            filled = _HOLE.sub("x1", form)
            assert tool.cells(filled) <= budget["limit"], (key, form)
            checked += 1
    assert checked > 30


def test_the_pack_keys_exist_in_every_catalog():
    golden = [k for _s, k in tool.load_golden()]
    for code in CODES:
        cat = core._catalog(code)
        assert [k for k in golden if k not in cat] == [], code
