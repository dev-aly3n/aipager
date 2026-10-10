"""The language list marks a language whose translation is new and not
reviewed yet as "beta", in the viewer's language; English and the other
surfaces (the /settings row, the confirmation) show the bare name (G79)."""

from __future__ import annotations

import dataclasses

import pytest

from aipager import i18n
from aipager.bot import language_menu
from aipager.i18n import locales


@pytest.fixture
def fa_ru(monkeypatch):
    for code in ("fa", "ru"):
        monkeypatch.setitem(locales.LOCALES, code, dataclasses.replace(
            locales.LOCALES[code], enabled=True, quality="beta"))


def _labels(tr, private=True, current="auto"):
    _text, kb = language_menu.render_language_menu(tr, private=private, current=current)
    return [b.text for row in kb.inline_keyboard for b in row]


def test_beta_languages_carry_the_mark(fa_ru):
    labels = _labels(i18n.get("en"))
    beta = ["Español (beta)", "Русский (beta)", "Português (Brasil) (beta)",
            "Français (beta)", "Deutsch (beta)", "العربية (beta)", "Українська (beta)",
            "Italiano (beta)", "Türkçe (beta)", "فارسی (beta)"]
    assert labels == ["Automatic (your Telegram language) ✅", "English", *beta, "« Back"]
    assert _labels(i18n.get("en"), private=False, current="en") == [
        "English ✅", *beta, "« Back"]


def test_english_and_other_surfaces_show_the_bare_name(fa_ru):
    en = i18n.get("en")
    assert "English" in _labels(en) and "English (beta)" not in _labels(en)
    assert language_menu.row_text(en, "ru") == "🌐 Language: Русский"
    assert "Русский here" in language_menu.confirmation(en, "ru")
    assert "(beta)" not in language_menu.confirmation(en, "fa")


def test_the_mark_is_in_the_viewers_language(fa_ru, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    labels = _labels(i18n.get("qps"))
    assert any(label.startswith("⟦Русский") or "Русский" in label and "⟦" in label
               for label in labels)
    assert "Pseudo" in labels and not any("Pseudo (" in x for x in labels)
