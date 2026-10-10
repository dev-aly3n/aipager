"""Success criterion 12: an answer held through a Telegram rate limit opens
with the late marker in the chat's language, on the rich and the plain
path; in English the marker equals ``held.late_marker`` for every input,
around every rounding edge and for odd inputs."""

from __future__ import annotations

import re

import pytest
import qa_p3a_scenarios as S

from aipager import i18n
from aipager.bot import held, held_message

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}

SECONDS = [0, 0.0, 1, 29, 29.9, 30, 30.1, 59, 60, 89, 89.9, 90, 90.1, 91, 149, 150, 151,
           3599, 3600, 7200, 86400, 10 ** 7, -1, -60, -0.5, 1e-9]
ODD = [None, "abc", "120", "", float("nan"), float("inf"), float("-inf"), [], object()]


@pytest.mark.parametrize("seconds", SECONDS, ids=[repr(s) for s in SECONDS])
def test_english_equals_held_late_marker(seconds):
    assert held_message.late_marker_text(i18n.get("en"), seconds) == held.late_marker(seconds)


def _held(seconds):
    try:
        return ("ok", held.late_marker(seconds))
    except Exception as exc:                     # noqa: BLE001 - mirror whatever held does
        return ("raises", type(exc).__name__)


def _ours(seconds):
    try:
        return ("ok", held_message.late_marker_text(i18n.get("en"), seconds))
    except Exception as exc:                     # noqa: BLE001
        return ("raises", type(exc).__name__)


@pytest.mark.parametrize("seconds", ODD, ids=[repr(s)[:20] for s in ODD])
def test_english_odd_input_behaves_like_held(seconds):
    expected = _held(seconds)
    got = _ours(seconds)
    if expected[0] == "raises":
        assert got[0] == "raises"
    else:
        assert got == expected


def test_held_late_marker_constant_is_unchanged_english():
    assert held.LATE_MARKER.startswith("⏳ delivered late")


# ---- Persian and Russian ---------------------------------------------------------------

def ru_category(n):
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


BOUNDARY = (1, 2, 5, 11, 12, 21, 22, 25)


def _tmpl(text, n):
    return re.sub(rf"(?<![\w.]){n}(?![\d.])", "{n}", text, count=1)


@pytest.mark.parametrize("n", BOUNDARY)
def test_russian_minutes_follow_the_cldr_category(n):
    tr = i18n.get("ru")
    rep = {"one": 1, "few": 2, "many": 5}[ru_category(n)]
    assert _tmpl(held_message.late_marker_text(tr, n * 60), n) == _tmpl(
        held_message.late_marker_text(tr, rep * 60), rep)


@pytest.mark.parametrize("a,b", [(1, 5), (2, 5), (1, 2)])
def test_russian_categories_differ(a, b):
    tr = i18n.get("ru")
    assert _tmpl(held_message.late_marker_text(tr, a * 60), a) != _tmpl(
        held_message.late_marker_text(tr, b * 60), b)


@pytest.mark.parametrize("n", BOUNDARY)
def test_persian_minutes_in_persian_digits(n):
    shown = held_message.late_marker_text(i18n.get("fa"), n * 60)
    assert str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) in shown


MARKUP = set("*_`[]<>&\\")


@pytest.mark.parametrize("lang,n", [(lang, n) for lang in ("en", "fa", "ru")
                                    for n in BOUNDARY + (0, 120)])
def test_the_marker_holds_no_markdown_or_html_metacharacter(lang, n):
    shown = held_message.late_marker_text(i18n.get(lang), n * 60)
    assert not (set(shown) & MARKUP)


@pytest.mark.parametrize("lang,n", [(lang, n) for lang in ("fa", "ru") for n in BOUNDARY])
def test_the_marker_has_no_em_dash(lang, n):
    assert "\u2014" not in held_message.late_marker_text(i18n.get(lang), n * 60)


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_marker_still_opens_with_the_hourglass(lang):
    shown = held_message.late_marker_text(i18n.get(lang), 300)
    assert shown.lstrip("\u200f").startswith("⏳")


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_marker_names_telegram_verbatim(lang):
    assert "Telegram" in held_message.late_marker_text(i18n.get(lang), 300)


def test_too_old_is_todays_english():
    assert held_message.TOO_OLD == "That message is too old to send; send it again."


# ---- through flush_held_answers ------------------------------------------------------

@pytest.fixture
def held_out(scenario_out):
    return lambda lang: scenario_out("held_answers", lang)


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
@pytest.mark.parametrize("k", range(len(S.HELD_SECONDS)))
def test_a_rich_late_answer_opens_with_the_chats_marker(held_out, lang, k):
    out = [t for t in held_out(lang) if t.startswith("[rich] ")]
    marker = held_message.late_marker_text(i18n.get(lang), S.HELD_SECONDS[k])
    assert out[k] == f"[rich] {marker}\n\nThe answer."


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("k", range(len(S.HELD_SECONDS)))
def test_a_rich_late_answer_marker_is_in_the_chats_script(held_out, lang, k):
    out = [t for t in held_out(lang) if t.startswith("[rich] ")]
    assert SCRIPT[lang].search(out[k].split("\n", 1)[0])


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("k,seconds", [(0, 60), (1, 660)])
def test_a_plain_late_answer_opens_with_the_chats_marker(held_out, lang, k, seconds):
    out = [t for t in held_out(lang) if not t.startswith("[rich] ")]
    marker = held_message.late_marker_text(i18n.get(lang), seconds)
    assert out[k].startswith(marker + "\n")

