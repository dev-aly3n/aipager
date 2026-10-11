"""The language list shows every language by its own name only (operator,
2026-10-10: "remove (beta) from language selector"). The review record
(``locales.is_beta``, design 8.5) stays for the translators' tooling, but no
list, row or confirmation shows a beta mark, in any viewer's language."""

from __future__ import annotations

import dataclasses

import pytest

from aipager import i18n
from aipager.bot import language_menu
from aipager.i18n import locales

NAMES = ["Español", "Русский", "Português (Brasil)", "简体中文", "Français", "Deutsch",
         "한국어", "العربية", "Bahasa Indonesia", "Українська", "Italiano", "Türkçe",
         "فارسی", "Tiếng Việt"]


@pytest.fixture
def fa_ru(monkeypatch):
    for code in ("fa", "ru"):
        monkeypatch.setitem(locales.LOCALES, code, dataclasses.replace(
            locales.LOCALES[code], enabled=True, quality="beta"))


def _labels(tr, private=True, current="auto"):
    _text, kb = language_menu.render_language_menu(tr, private=private, current=current)
    return [b.text for row in kb.inline_keyboard for b in row]


def test_every_language_shows_its_own_name_only(fa_ru):
    labels = _labels(i18n.get("en"))
    assert labels == ["Automatic (your Telegram language) ✅", "English", *NAMES, "« Back"]
    assert _labels(i18n.get("en"), private=False, current="en") == [
        "English ✅", *NAMES, "« Back"]


@pytest.mark.parametrize("viewer", ["en", "fa", "ru", "ar", "zh-Hans", "es"])
def test_no_viewer_language_shows_a_mark(fa_ru, viewer):
    labels = _labels(i18n.get(viewer))
    for name in NAMES:
        assert any(label == name or label == f"{name} ✅" for label in labels), (viewer, name)
    assert not any("beta" in label.lower() for label in labels)


def test_the_row_and_the_confirmation_show_the_bare_name(fa_ru):
    en = i18n.get("en")
    assert language_menu.row_text(en, "ru") == "🌐 Language: Русский"
    assert "Русский here" in language_menu.confirmation(en, "ru")
    assert "(beta)" not in language_menu.confirmation(en, "fa")


def test_the_pseudo_list_marks_nothing_but_its_own_text(fa_ru, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    labels = _labels(i18n.get("qps"))
    assert "Русский" in labels and "Pseudo" in labels
    assert not any("Pseudo (" in x or "Русский (" in x for x in labels)


# ---- the review record (phase 5, design 8.5) stays, unshown ----------------------------

def test_every_language_but_english_is_still_recorded_as_unreviewed():
    assert not locales.is_beta("en")
    for code in locales.enabled_codes():
        if code != "en":
            assert locales.is_beta(code), code
            assert locales.LOCALES[code].quality == "beta" and locales.LOCALES[code].reviewed == ""


def test_an_unreviewed_language_still_shows_no_mark(monkeypatch):
    monkeypatch.setitem(locales.LOCALES, "fa", dataclasses.replace(
        locales.LOCALES["fa"], quality="stable", reviewed=""))
    assert locales.is_beta("fa")
    assert "فارسی" in _labels(i18n.get("en"))


def test_a_recorded_review_changes_only_the_record(monkeypatch):
    monkeypatch.setitem(locales.LOCALES, "ru", dataclasses.replace(
        locales.LOCALES["ru"], quality="stable",
        reviewed="native review of the golden strings, a reviewer, 2026-11-02"))
    assert not locales.is_beta("ru")
    assert "Русский" in _labels(i18n.get("en"))
    monkeypatch.setitem(locales.LOCALES, "ru", dataclasses.replace(
        locales.LOCALES["ru"], quality="beta", reviewed="a review"))
    assert locales.is_beta("ru")
    assert "Русский" in _labels(i18n.get("en"))
