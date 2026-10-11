"""Black-box, iteration 3: the two stale taps iteration 2 found in English
(tester-iter2-001 Retry on a gone session, tester-iter2-002 a short-form
``_:sx:<n>:<verb>`` prompt button for a gone session). Iteration 2 only
checked that a Russian tapper sees Cyrillic. Here: the exact catalog text,
the Persian tapper (text and right-to-left start), English bytes for an
English app with no choice, and the group fallback for a Persian app with
no choice (design.md decision 13).

Driven through the real ``_handle_callback``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from tests.i18n_ml2_bb import (
    BOB,
    CAROL,
    DM_B,
    DM_C,
    GROUP,
    RLM,
    callback,
    catalog,
    make_bot,
    strip_marks,
    toast_texts,
)

RETRY = "claude-nobody:retry"
SHORT = "_:sx:99:allow"


@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    return make_bot(mk_bot)


def _tap(bot, run_async, data, *, user=BOB, language_code="fa"):
    update, query = callback(data, chat=GROUP, user=user, language_code=language_code,
                             message_id=42)
    run_async(bot._handle_callback(update, MagicMock()))
    toasts = toast_texts(query)
    assert toasts, f"no toast for {data}"
    return toasts[-1]


def _v(code, key):
    return catalog(code)[key]["v"]


@pytest.fixture
def ru_tapper_in_fa_group():
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(DM_B, "ru")


@pytest.fixture
def fa_tapper_in_ru_group():
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(DM_C, "fa")


# ---- Retry on a gone session ----------------------------------------------------------

def test_retry_on_a_gone_session_reads_the_russian_catalog(bot, run_async,
                                                          ru_tapper_in_fa_group):
    assert _tap(bot, run_async, RETRY) == _v("ru", "toast.session_not_found")


def test_retry_on_a_gone_session_reads_the_persian_catalog(bot, run_async,
                                                          fa_tapper_in_ru_group):
    toast = _tap(bot, run_async, RETRY, user=CAROL, language_code="ru")
    assert strip_marks(toast) == _v("fa", "toast.session_not_found")


def test_retry_on_a_gone_session_starts_right_to_left_in_persian(bot, run_async,
                                                                fa_tapper_in_ru_group):
    toast = _tap(bot, run_async, RETRY, user=CAROL, language_code="ru")
    assert toast.startswith(RLM)


def test_retry_on_a_gone_session_for_an_english_app_is_todays_text(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    assert _tap(bot, run_async, RETRY, language_code="en") == "Session not found"


def test_retry_on_a_gone_session_for_a_persian_app_uses_the_group(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    assert _tap(bot, run_async, RETRY, language_code="fa") == _v(
        "ru", "toast.session_not_found")


# ---- short-form prompt button on a gone session ---------------------------------------

def test_a_short_form_stale_tap_reads_the_russian_catalog(bot, run_async,
                                                         ru_tapper_in_fa_group):
    assert _tap(bot, run_async, SHORT) == _v("ru", "toast.session_unavailable")


def test_a_short_form_stale_tap_reads_the_persian_catalog(bot, run_async,
                                                         fa_tapper_in_ru_group):
    toast = _tap(bot, run_async, SHORT, user=CAROL, language_code="ru")
    assert strip_marks(toast) == _v("fa", "toast.session_unavailable")


def test_a_short_form_stale_tap_starts_right_to_left_in_persian(bot, run_async,
                                                               fa_tapper_in_ru_group):
    toast = _tap(bot, run_async, SHORT, user=CAROL, language_code="ru")
    assert toast.startswith(RLM)


def test_a_short_form_stale_tap_for_an_english_app_is_todays_text(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    toast = _tap(bot, run_async, SHORT, language_code="en")
    assert toast == "That session is no longer available"


def test_a_short_form_stale_tap_for_a_persian_app_uses_the_group(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    assert _tap(bot, run_async, SHORT, language_code="fa") == _v(
        "ru", "toast.session_unavailable")


@pytest.mark.parametrize("verb", ["deny", "opt0", "stop"])
def test_every_short_form_verb_on_a_gone_session_follows_the_tapper(
        bot, run_async, ru_tapper_in_fa_group, verb):
    assert _tap(bot, run_async, f"_:sx:99:{verb}") == _v("ru", "toast.session_unavailable")
