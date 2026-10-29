"""entrypoints.md ``aipager.bot.job_wait.phrase(tr, agents, shells, kind)``
and spec requirement 2: every surface names what it waits for, plural
forms by key (1 vs 2, never an "s" suffix), one sentence for two counts,
"finishing up" for the zero-count case; and a non-English chat reads the
catalog's words with the same glyph and counts.

Expected translated words come from the shipped catalogs
(``aipager.i18n.core._catalog``, as tests/test_i18n_render_ru.py reads
them), never from the code under test.
"""

from __future__ import annotations

import importlib

import pytest

from aipager import i18n, preferences
from aipager.bot import job_wait
from aipager.i18n import core, plurals
from aipager.i18n.locales import LOCALES

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")

EN = i18n.get("en")


@pytest.mark.parametrize("agents, shells, expected", [
    (1, 0, "waiting for 1 agent"),
    (2, 0, "waiting for 2 agents"),
    (0, 1, "waiting for 1 shell"),
    (0, 2, "waiting for 2 shells"),
    (1, 1, "waiting for 1 agent and 1 shell"),
    (2, 1, "waiting for 2 agents and 1 shell"),
    (1, 2, "waiting for 1 agent and 2 shells"),
    (11, 21, "waiting for 11 agents and 21 shells"),
    (0, 0, "finishing up"),
], ids=["1a", "2a", "1s", "2s", "1a1s", "2a1s", "1a2s", "11a21s", "zero"])
@pytest.mark.parametrize("kind", ["html", "md", "text"])
def test_english_phrase(agents, shells, expected, kind):
    assert job_wait.phrase(EN, agents, shells, kind) == expected


def test_phrase_default_kind_is_usable_without_naming_it():
    assert job_wait.phrase(EN, 2, 0) == "waiting for 2 agents"


def test_phrase_has_no_em_dash_in_any_language():
    for code in LOCALES:
        tr = i18n.get(code)
        for counts in ((1, 0), (2, 1), (0, 0)):
            assert "\u2014" not in job_wait.phrase(tr, *counts, "text"), (code, counts)


# ---- the catalogs carry every key in every language ---------------------------

KEYS = ("jobwait.agents", "jobwait.shells", "jobwait.both", "jobwait.finishing")


@pytest.mark.parametrize("key", KEYS)
def test_every_catalog_translates_the_key(key):
    missing = [code for code in LOCALES if code != "en" and key not in core._catalog(code)]
    assert missing == []


@pytest.mark.parametrize("key", ["jobwait.agents", "jobwait.shells"])
def test_every_catalog_has_every_plural_form_its_language_uses(key):
    """A Plural, not an "s" suffix: e.g. Russian needs one, few and many."""
    gaps = {}
    for code in LOCALES:
        if code == "en":
            continue
        forms = core._catalog(code).get(key)
        needed = {plurals.category(code, n) for n in (0, 1, 2, 3, 5, 11, 21, 22, 25, 100)}
        if not isinstance(forms, dict) or not needed <= set(forms):
            gaps[code] = (needed, forms)
    assert gaps == {}


def _ru(key: str, n: int) -> str:
    forms = core._catalog("ru")[key]
    return forms[plurals.category("ru", n)].replace("{n}", str(n))


@pytest.mark.parametrize("n", [1, 2, 5, 21])
def test_russian_agent_phrase_uses_the_catalog_form_for_n(n):
    assert job_wait.phrase(i18n.get("ru"), n, 0, "text") == _ru("jobwait.agents", n)


@pytest.mark.parametrize("n", [1, 3, 11])
def test_russian_shell_phrase_uses_the_catalog_form_for_n(n):
    assert job_wait.phrase(i18n.get("ru"), 0, n, "text") == _ru("jobwait.shells", n)


def test_russian_finishing_is_the_catalogs():
    assert job_wait.phrase(i18n.get("ru"), 0, 0, "text") == core._catalog("ru")["jobwait.finishing"]


@pytest.mark.parametrize("code", ["ru", "fa", "de"])
def test_translated_phrase_is_not_english(code):
    assert "waiting for" not in job_wait.phrase(i18n.get(code), 2, 1, "text")


# ---- a translated chat on the surfaces ----------------------------------------

def _ru_chat():
    preferences.set_ui_language(kit.CHAT, "ru")
    return i18n.get("ru")


def test_russian_bar_shows_the_translated_phrase_with_the_glyph(chat_bot):
    ru = _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(agents=5))
    first = chat_bot._render_pinned(kit.CHAT, [sess])[0].split("\n")[0]
    assert first == f"🔄 {kit.LABEL} ({job_wait.phrase(ru, 5, 0, 'html')})"


def test_russian_bar_never_shows_english(chat_bot):
    _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(agents=2, job_shells=1))
    assert "waiting for" not in chat_bot._render_pinned(kit.CHAT, [sess])[0]


def test_russian_status_head_carries_glyph_and_translated_phrase(chat_bot):
    ru = _ru_chat()
    kit.register(chat_bot, kit.make_session(agents=2))
    head = chat_bot._render_status_list(kit.CHAT)[0]
    assert f"🔄 {job_wait.phrase(ru, 2, 0, 'html')}" in head


def test_russian_switch_reply_carries_glyph_and_translated_phrase(chat_bot):
    ru = _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(job_shells=3))
    text = chat_bot._render_switch_reply(kit.CHAT, sess)[0]
    assert f"🔄 {job_wait.phrase(ru, 0, 3, 'html')}" in text


def test_russian_finishing_on_the_bar(chat_bot):
    import time
    ru = _ru_chat()
    sess = kit.make_session()
    sess.job_grace_until = time.monotonic() + 60
    kit.register(chat_bot, sess)
    first = chat_bot._render_pinned(kit.CHAT, [sess])[0].split("\n")[0]
    assert first == f"🔄 {kit.LABEL} ({job_wait.phrase(ru, 0, 0, 'html')})"
