"""The new chat text is in every catalog (design.md success criterion 22;
entrypoints.md "i18n keys"): each of the five keys is in
``tools/i18n/source.jsonl`` with the English inline, and in all 14
``aipager/i18n/locales/<code>.jsonl`` with a non-empty value, ``{time}``
kept where the English has it, and no em dash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LOCALES = ROOT / "aipager" / "i18n" / "locales"
CODES = ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru", "tr", "uk", "vi",
         "zh-Hans"]
KEYS = {
    "perm.rm_clock": "⏱ If nobody answers, Claude Code refuses this by itself at about {time}.",
    "perm.rm_refused": "⏱ Claude Code refused this by itself because nobody answered in time.",
    "ans.verb.refused_by_claude": "Refused by Claude Code",
    "toast.prompt_refused_closed": ("Claude Code already refused this by itself, so the "
                                    "question is no longer open."),
    "toast.rm_refusal_due": ("Claude Code refuses this by itself about now, so answer it in "
                             "the terminal if it is still open."),
}


def rows(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["k"]] = row
    return out


SOURCE = rows(ROOT / "tools" / "i18n" / "source.jsonl")
CATALOGS = {code: rows(LOCALES / f"{code}.jsonl") for code in CODES}


def test_there_are_fourteen_catalogs():
    assert sorted(p.stem for p in LOCALES.glob("*.jsonl")) == sorted(CODES)


@pytest.mark.parametrize("key", KEYS)
def test_source_has_the_english(key):
    assert SOURCE.get(key, {}).get("en") == KEYS[key]


@pytest.mark.parametrize("key", KEYS)
def test_english_has_no_em_dash(key):
    assert "—" not in KEYS[key] and "—" not in SOURCE.get(key, {}).get("en", "—")


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_has_a_value(code, key):
    assert str(CATALOGS[code].get(key, {}).get("v", "")).strip() != ""


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_value_has_no_em_dash(code, key):
    assert "—" not in str(CATALOGS[code].get(key, {}).get("v", ""))


@pytest.mark.parametrize("code", CODES)
def test_clock_value_keeps_the_time_placeholder_once(code):
    assert str(CATALOGS[code].get("perm.rm_clock", {}).get("v", "")).count("{time}") == 1


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", [k for k in KEYS if "{" not in KEYS[k]])
def test_values_without_placeholders_add_none(code, key):
    assert "{" not in str(CATALOGS[code].get(key, {}).get("v", ""))


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_value_is_translated_not_copied(code, key):
    """"Translated by me into all 14 catalogs": no catalog keeps the
    English sentence as is."""
    assert CATALOGS[code].get(key, {}).get("v") != KEYS[key]
