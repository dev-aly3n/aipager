"""A person's Telegram language never switches a chat by itself while
``locales.AUTO_DETECT`` is off (phase 2: Persian and Russian can be picked
in /language, not detected). English apps still read as English, and the
one switch turns detection on for phase 5 (G45, G46)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from aipager import i18n, preferences
from aipager.i18n import locales, resolve

GROUP, DM, ALY, BOB = -1001, 555, 555, 777


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


def test_a_persian_telegram_app_does_not_switch_a_chat(fa_ru):
    assert locales.is_selectable("fa") and locales.is_selectable("ru")
    assert locales.normalize("fa-IR") == "fa"
    assert locales.detect("fa-IR") is None and locales.detect("ru") is None
    assert i18n.for_update(_update(DM, ALY, "fa-IR")).code == "en"
    assert i18n.for_chat(DM).code == "en"
    assert resolve._seen == {}
    assert i18n.for_viewer(ALY, "ru").code == "en"
    assert i18n.for_stranger(_update(DM, 9, "fa")).code == "en"
    # A member with no DM choice: the chat's language, not their app's.
    preferences.set_ui_language(GROUP, "ru")
    assert i18n.for_person(_update(GROUP, BOB, "fa")).code == "ru"
    # Picked explicitly, it applies.
    preferences.set_ui_language(DM, "fa")
    assert i18n.for_update(_update(DM, ALY, "en")).code == "fa"
    assert i18n.for_viewer(ALY, "en").code == "fa"


def test_english_apps_still_read_as_english(fa_ru):
    assert locales.detect("en-US") == "en"
    assert i18n.for_update(_update(DM, ALY, "en-GB")).code == "en"
    assert resolve._seen == {ALY: "en"}
    assert locales.detect("qps") is None and locales.detect(None) is None


def test_the_switch_turns_detection_on(fa_ru, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", True)
    assert locales.detect("fa-IR") == "fa"
    assert i18n.for_update(_update(DM, ALY, "fa-IR")).code == "fa"
    assert i18n.for_chat(DM).code == "fa"
    assert i18n.for_viewer(BOB, "ru").code == "ru"
    assert i18n.for_person(_update(GROUP, BOB, "ru")).code == "ru"


def test_a_test_switched_language_is_still_detected(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"de"}))
    assert locales.detect("de-AT") == "de"
    assert i18n.for_update(_update(DM, ALY, "de")).code == "de"
