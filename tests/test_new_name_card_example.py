"""The /new Name card's second example (a name, then what to ask first)
reads in the chat's language, the session name ``x1`` kept as it is and
first, and nothing invisible inside the copyable ``<code>`` line
(multilingual phase 3a, design: ``newflow.example_line``)."""

from __future__ import annotations

import re

import pytest

from tests import i18n_surfaces_p3a as p3a
from tests.i18n_surfaces import Env

SCRIPT = {"fa": re.compile("[؀-ۿ]"), "ru": re.compile("[Ѐ-ӿ]")}
MARKS = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
RLM = "\N{RIGHT-TO-LEFT MARK}"
CODE_LINE = re.compile(r"<code>(x1 [^<]*)</code>")


def _card(mk_bot, run_async, monkeypatch, code):
    env = Env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._new_world(env)
    p3a._open_name_card(env, bot, tg, chat=p3a.DM)
    return tg.out[0]


def test_english_is_todays_line(mk_bot, run_async, monkeypatch):
    card = _card(mk_bot, run_async, monkeypatch, "en")
    assert "\n<code>x1</code>\n<code>x1 fix the failing tests</code>\n" in card


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_example_reads_in_the_chats_language(mk_bot, run_async, monkeypatch, code):
    card = _card(mk_bot, run_async, monkeypatch, code)
    assert "fix the failing tests" not in card
    found = CODE_LINE.findall(card)
    assert len(found) == 1, card
    inside = found[0]
    assert SCRIPT[code].search(inside)
    assert not MARKS.search(inside)
    assert "<code>x1</code>" in card


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_settings_line_has_one_mark_at_most(mk_bot, run_async, monkeypatch, code):
    """The Name card's settings line (mode, model, folder) starts with a
    single right-to-left mark in Persian, not one per nested piece."""
    card = _card(mk_bot, run_async, monkeypatch, code)
    (line,) = [line for line in card.split("\n") if "🧠" in line]
    assert RLM * 2 not in card
    if code == "fa":
        assert line.startswith(RLM) and line.count(RLM) == 1, ascii(line)
    else:
        assert RLM not in card


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_ready_cards_settings_line_has_one_mark_at_most(mk_bot, run_async, monkeypatch,
                                                            code):
    env = Env(mk_bot, run_async, monkeypatch, code)
    _bot, tg, _sess = p3a._ready_session(env)
    ready = [text for text in tg.out if "🧠" in text]
    assert ready
    for card in ready:
        (line,) = [line for line in card.split("\n") if "🧠" in line]
        assert RLM * 2 not in card
        assert line.count(RLM) == (1 if code == "fa" else 0), ascii(line)
