"""Telegram-language detection after the launch (phase 5, design section 6).

Detection is on (``locales.AUTO_DETECT``), but only for people new since
the launch marker: a person who existed before (everyone, while no marker
is written) keeps English until they choose, exactly as before. English
apps read as English for everyone; strangers always get their Telegram
language; ``AUTO_DETECT = False`` is the kill switch (G45, G46)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from aipager import i18n, preferences
from aipager.i18n import locales, persist

GROUP, DM, ALICE, BOB = -1001, 555, 555, 777


def _update(chat_id, user_id, language_code=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id, language_code=language_code))


@pytest.fixture
def fa_ru(monkeypatch):
    """Persian and Russian selectable, as phase 2 ships them (a no-op once
    they are enabled in the table)."""
    for code in ("fa", "ru"):
        monkeypatch.setitem(locales.LOCALES, code,
                            dataclasses.replace(locales.LOCALES[code], enabled=True))


@pytest.fixture
def fresh():
    """A launch marker that lists nobody: everyone is new."""
    persist.ensure_launch_marker(people=(), prior_run=False)


def test_detection_is_on():
    assert locales.AUTO_DETECT is True


def test_a_persian_telegram_app_does_not_switch_an_existing_chat(fa_ru):
    # No marker: everyone counts as existing.
    assert locales.normalize("fa-IR") == "fa"
    assert locales.detect("fa-IR", follow=False) is None
    assert i18n.for_update(_update(DM, ALICE, "fa-IR")).code == "en"
    assert i18n.for_chat(DM).code == "en"
    # Remembered all the same (the offer needs it), never stored as a choice.
    assert persist.seen(ALICE) == "fa"
    assert preferences.get_ui_language(DM) == ""
    assert i18n.for_viewer(ALICE, "ru").code == "en"
    # A member with no DM choice: the chat's language, not their app's.
    preferences.set_ui_language(GROUP, "ru")
    assert i18n.for_person(_update(GROUP, BOB, "fa")).code == "ru"
    # Picked explicitly, it applies.
    preferences.set_ui_language(DM, "fa")
    assert i18n.for_update(_update(DM, ALICE, "en")).code == "fa"
    assert i18n.for_viewer(ALICE, "en").code == "fa"


def test_a_listed_person_stays_english(fa_ru):
    persist.ensure_launch_marker(people=[ALICE], prior_run=True)
    assert i18n.for_update(_update(DM, ALICE, "fa-IR")).code == "en"
    assert i18n.for_viewer(ALICE, "fa").code == "en"
    assert i18n.for_person(_update(GROUP, ALICE, "fa")).code == "en"
    # Someone added after the marker follows their app.
    assert i18n.for_update(_update(BOB, BOB, "ru")).code == "ru"


def test_english_apps_still_read_as_english(fa_ru):
    assert locales.detect("en-US") == "en"
    assert i18n.for_update(_update(DM, ALICE, "en-GB")).code == "en"
    assert persist.seen(ALICE) == "en"
    assert locales.detect("qps") is None and locales.detect(None) is None


def test_a_new_person_follows_their_app(fa_ru, fresh):
    assert locales.detect("fa-IR") == "fa"
    assert i18n.for_update(_update(DM, ALICE, "fa-IR")).code == "fa"
    assert i18n.for_chat(DM).code == "fa"
    assert i18n.for_viewer(BOB, "ru").code == "ru"
    assert i18n.for_person(_update(GROUP, BOB, "ru")).code == "ru"


def test_the_kill_switch_brings_back_the_old_behaviour(fa_ru, fresh, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    assert locales.detect("fa-IR") is None
    assert i18n.for_update(_update(DM, ALICE, "fa-IR")).code == "en"
    assert i18n.for_viewer(BOB, "ru").code == "en"
    # Strangers keep their Telegram language either way.
    assert i18n.for_stranger(_update(DM, 9, "fa")).code == "fa"
    # An explicit Automatic is back to the old meaning too: English
    # unless the app is English.
    preferences.set_ui_language(DM, "auto")
    assert i18n.for_chat(DM).code == "en"
    assert i18n.for_viewer(DM, "fa").code == "en"


def test_a_test_switched_language_is_detected_for_one_person(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"de"}))
    monkeypatch.setitem(locales.LOCALES, "de",
                        dataclasses.replace(locales.LOCALES["de"], enabled=False))
    assert locales.detect("de-AT", follow=False) == "de"
    assert i18n.for_person(_update(GROUP, BOB, "de")).code == "de"
