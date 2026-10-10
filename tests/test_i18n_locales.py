"""The language table and which codes a person may pick."""

from __future__ import annotations

import dataclasses

from aipager import i18n
from aipager.i18n import locales

ALL = ("en", "es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk",
       "it", "tr", "fa", "vi")


def test_the_fifteen_languages_in_order_with_their_own_names():
    assert tuple(locales.LOCALES) == ALL
    assert [loc.endonym for loc in locales.LOCALES.values()] == [
        "English", "Español", "Русский", "Português (Brasil)", "简体中文",
        "Français", "Deutsch", "한국어", "العربية", "Bahasa Indonesia",
        "Українська", "Italiano", "Türkçe", "فارسی", "Tiếng Việt"]
    rtl = {c for c, loc in locales.LOCALES.items() if loc.rtl}
    assert rtl == {"ar", "fa"}
    assert locales.LOCALES["fa"].digits == "arabext"
    assert locales.LOCALES["ar"].digits == "arab"


def test_only_english_and_the_beta_languages_are_offered():
    """Phase 2: Persian and Russian join English, both marked beta; phase
    5 run 1 adds Spanish, Portuguese (Brazil), French and Italian, run 2
    German, Arabic, Ukrainian and Turkish, run 3 Chinese (Simplified),
    Korean, Indonesian and Vietnamese, beta too (G80)."""
    beta = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it", "tr",
            "fa", "vi")
    assert locales.enabled_codes() == ("en",) + beta
    assert [c for c, loc in locales.LOCALES.items() if loc.enabled] == ["en", *beta]
    assert locales.LOCALES["en"].quality == "stable"
    assert [locales.LOCALES[c].quality for c in beta] == ["beta"] * len(beta)
    assert locales.AUTO_DETECT is True    # phase 5: on, for people new since the marker


def test_normalize_ignores_disabled_languages(monkeypatch):
    # Japanese and Polish stand for a language aipager does not offer (no
    # catalog, after phase 5 run 3 too); German, switched off here, for a
    # known one that is off.
    monkeypatch.setitem(locales.LOCALES, "de", dataclasses.replace(
        locales.LOCALES["de"], enabled=False))
    assert locales.normalize("en") == "en"
    assert locales.normalize("en-US") == "en"
    assert locales.normalize("EN_gb") == "en"
    for code in ("ja", "pl", "ja-JP", "pl-PL", "de", "de-AT"):
        assert locales.normalize(code) is None, code
    assert locales.normalize("es-MX") == "es"
    assert locales.normalize("pt-br") == "pt-BR"
    assert locales.normalize("pt") == "pt-BR"
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"zh-Hans", "id"}))
    assert locales.normalize("zh") == "zh-Hans"
    assert locales.normalize("zh-cn") == "zh-Hans"
    assert locales.normalize("zh-Hans-CN") == "zh-Hans"
    assert locales.normalize("in") == "id"
    assert locales.normalize("ja") is None


def test_traditional_chinese_never_becomes_simplified(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"zh-Hans"}))
    for code in ("zh-hant", "zh-TW", "zh-hk", "zh-Hant-TW", "zh-mo"):
        assert locales.normalize(code) is None, code


def test_normalize_survives_junk():
    for junk in (None, "", "  ", 5, object(), "x" * 300, "-", "--"):
        assert locales.normalize(junk) is None


def test_pseudo_codes_are_never_offered():
    for code in ("qps", "qps-rtl"):
        assert code not in locales.enabled_codes()
        assert not locales.is_selectable(code)
        assert locales.normalize(code) is None
        assert code not in locales.LOCALES
        assert i18n.get(code).code == code
    assert i18n.get("qps-rtl").rtl


def test_a_test_may_enable_a_pseudo_code(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    assert locales.enabled_codes() == ("en", "es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko",
                                       "ar", "id", "uk", "it", "tr", "fa", "vi", "qps")
    assert locales.is_selectable("qps")
    assert locales.normalize("qps") is None


def test_get_never_raises_and_caches():
    assert i18n.get("xx").code == "en"
    assert i18n.get(None).code == "en"
    assert i18n.get("en") is i18n.get("en")


def test_the_catalog_files_ship_beside_the_package():
    """The 14 catalogs are the first data files in the package; a missing
    one would only degrade to English, so pin that they exist."""
    from aipager.i18n.core import LOCALES_DIR
    for code in ALL[1:]:
        assert (LOCALES_DIR / f"{code}.jsonl").is_file(), code
    assert not (LOCALES_DIR / "en.jsonl").exists()
