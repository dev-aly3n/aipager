"""entrypoints.md ``aipager.bot.job_wait.phrase``, as amended
(design-amend-5 A, agents only): ``phrase(tr, agents, kind)``. Every
surface names how many agents a session waits for, plural forms by key (1
vs 2, never an "s" suffix); a non-English chat reads the catalog's words
with the same glyph and count. The shell, both-counts and finishing
sentences are gone from the derived state and from every catalog.

Expected translated words come from the shipped catalogs
(``aipager.i18n.core._catalog``, as tests/test_i18n_render_ru.py reads
them), never from the code under test.
"""

from __future__ import annotations

import importlib
import time

import pytest

from aipager import i18n, preferences
from aipager.bot import job_wait
from aipager.i18n import core, plurals
from aipager.i18n.locales import LOCALES

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")

EN = i18n.get("en")


@pytest.mark.parametrize("agents, expected", [
    (1, "waiting for 1 agent"),
    (2, "waiting for 2 agents"),
    (11, "waiting for 11 agents"),
    (21, "waiting for 21 agents"),
], ids=["1a", "2a", "11a", "21a"])
@pytest.mark.parametrize("kind", ["html", "md", "text"])
def test_english_phrase(agents, expected, kind):
    assert job_wait.phrase(EN, agents, kind) == expected


def test_phrase_default_kind_is_usable_without_naming_it():
    assert job_wait.phrase(EN, 2) == "waiting for 2 agents"


def test_phrase_has_no_em_dash_in_any_language():
    for code in LOCALES:
        tr = i18n.get(code)
        for n in (1, 2, 5):
            assert "—" not in job_wait.phrase(tr, n, "text"), (code, n)


# ---- the catalogs carry the key in every language, and only that one ----------

GONE_KEYS = ("jobwait.shells", "jobwait.both", "jobwait.finishing")


def test_every_catalog_translates_the_agents_key():
    missing = [code for code in LOCALES
               if code != "en" and "jobwait.agents" not in core._catalog(code)]
    assert missing == []


@pytest.mark.parametrize("key", GONE_KEYS)
def test_no_catalog_keeps_a_removed_key(key):
    kept = [code for code in LOCALES if code != "en" and key in core._catalog(code)]
    assert kept == []


def test_every_catalog_has_every_plural_form_its_language_uses():
    """A Plural, not an "s" suffix: e.g. Russian needs one, few and many."""
    gaps = {}
    for code in LOCALES:
        if code == "en":
            continue
        forms = core._catalog(code).get("jobwait.agents")
        needed = {plurals.category(code, n) for n in (0, 1, 2, 3, 5, 11, 21, 22, 25, 100)}
        if not isinstance(forms, dict) or not needed <= set(forms):
            gaps[code] = (needed, forms)
    assert gaps == {}


def _ru(key: str, n: int) -> str:
    forms = core._catalog("ru")[key]
    return forms[plurals.category("ru", n)].replace("{n}", str(n))


@pytest.mark.parametrize("n", [1, 2, 5, 21])
def test_russian_agent_phrase_uses_the_catalog_form_for_n(n):
    assert job_wait.phrase(i18n.get("ru"), n, "text") == _ru("jobwait.agents", n)


@pytest.mark.parametrize("code", ["ru", "fa", "de"])
def test_translated_phrase_is_not_english(code):
    assert "waiting for" not in job_wait.phrase(i18n.get(code), 2, "text")


# ---- a translated chat on the surfaces ----------------------------------------

def _ru_chat():
    preferences.set_ui_language(kit.CHAT, "ru")
    return i18n.get("ru")


def test_russian_bar_shows_the_translated_phrase_with_the_glyph(chat_bot):
    ru = _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(agents=5))
    first = chat_bot._render_pinned(kit.CHAT, [sess])[0].split("\n")[0]
    assert first == f"🔄 {kit.LABEL} ({job_wait.phrase(ru, 5, 'html')})"


def test_russian_bar_never_shows_english(chat_bot):
    _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(agents=2, job_shells=1))
    assert "waiting for" not in chat_bot._render_pinned(kit.CHAT, [sess])[0]


def test_russian_status_head_carries_glyph_and_translated_phrase(chat_bot):
    ru = _ru_chat()
    kit.register(chat_bot, kit.make_session(agents=2))
    head = chat_bot._render_status_list(kit.CHAT)[0]
    assert f"🔄 {job_wait.phrase(ru, 2, 'html')}" in head


def test_russian_switch_reply_carries_glyph_and_translated_phrase(chat_bot):
    ru = _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(agents=3, job_shells=2))
    text = chat_bot._render_switch_reply(kit.CHAT, sess)[0]
    assert f"🔄 {job_wait.phrase(ru, 3, 'html')}" in text


def test_russian_switch_reply_with_only_job_shells_reads_idle(chat_bot):
    _ru_chat()
    sess = kit.register(chat_bot, kit.make_session(job_shells=3))
    text = chat_bot._render_switch_reply(kit.CHAT, sess)[0]
    assert "🔄" not in text and "💤" in text


def test_russian_grace_window_on_the_bar_reads_idle(chat_bot):
    _ru_chat()
    sess = kit.make_session()
    sess.job_grace_until = time.monotonic() + 60
    kit.register(chat_bot, sess)
    first = chat_bot._render_pinned(kit.CHAT, [sess])[0].split("\n")[0]
    assert first.startswith(f"💤 {kit.LABEL} (")
