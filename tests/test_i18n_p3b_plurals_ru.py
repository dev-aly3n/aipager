"""Multilingual phase 3b: every new counted message picks the Russian form
CLDR says for 1, 2, 5, 11, 12, 21, 22 and 25 (one, few, many), with the
right noun in the catalog, and shows Persian digits in Persian."""

from __future__ import annotations

import pytest

from aipager import i18n
from aipager.bot import dashboard, live_reload, report_flow, report_offer, update_flow
from aipager.i18n import core, plurals

COUNTS = (1, 2, 5, 11, 12, 21, 22, 25)
PLURALS = [
    update_flow._SN_OTHERS, update_flow._GATE_BUSY, update_flow._GATE_BUSY_HELD,
    update_flow._MARKER_DONE,
    report_flow._NOTE_PROMPT, report_flow._NOTE_CUT, report_flow.OUTCOME_MSGS["daily_cap"],
    report_offer._STOPPED, report_offer._MEMCAP, report_offer._INTERNAL, report_offer._ALSO,
    live_reload._DROPPED, live_reload._DROPPED_ANON,
    dashboard.DIFF_MORE, dashboard.AGO_SECONDS, dashboard.AGO_MINUTES, dashboard.AGO_HOURS,
    dashboard.AGO_DAYS, dashboard.RESUME_NO_TRANSCRIPT, dashboard.RESUME_MORE_NO_TRANSCRIPT,
    dashboard.RESUME_TITLE, dashboard.DASH_AGENTS, dashboard.DASH_AGENTS_COUNT,
]


def _params(plural) -> dict:
    out = {}
    for form in (plural.one, plural.other):
        for name in core.placeholders(form):
            if name != "n":
                out[name] = str(plural.eg.get(name, "x"))
    return out


def _form(code, plural, n, params) -> str:
    text = core._catalog(code)[plural.key][plurals.category(code, n)]
    text = text.replace("{n}", str(n))
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


@pytest.mark.parametrize("plural", PLURALS, ids=lambda p: p.key)
def test_russian_forms(plural):
    ru = i18n.get("ru")
    params = _params(plural)
    for n in COUNTS:
        assert ru.n(plural, n, kind="text", **params) == _form("ru", plural, n, params), (
            plural.key, n)


@pytest.mark.parametrize("plural", PLURALS, ids=lambda p: p.key)
def test_persian_counts_use_persian_digits(plural):
    fa = i18n.get("fa")
    params = {k: "x" for k in _params(plural)}
    for n in (2, 25):
        text = fa.n(plural, n, kind="text", **params)
        assert str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) in text, text
        assert str(n) not in text, text


@pytest.mark.parametrize("key, words", [
    ("grp.dropped", {"one": "сообщение", "few": "сообщения", "many": "сообщений"}),
    ("upd.marker.done", {"one": "сессия", "few": "сессии", "many": "сессий"}),
    ("diff.more_lines", {"one": "строка", "few": "строки", "many": "строк"}),
    ("rep.offer.also_other", {"one": "ошибка.", "few": "ошибки.", "many": "ошибок."}),
    ("resume.no_transcript", {"one": "сессия", "few": "сессии", "many": "сессий"}),
])
def test_the_russian_nouns_agree(key, words):
    forms = core._catalog("ru")[key]
    for category, word in words.items():
        assert f"{{n}} {word}" in forms[category], (key, category, forms[category])
