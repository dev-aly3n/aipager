"""The new pop-up ``toast.prompt_opening`` (entrypoints.md "CLI
commands"): present in the source catalogue and in all 14 locales, one
translated sentence each, within Telegram's 200-character pop-up limit,
and no em dash (project rule for user text)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
KEY = "toast.prompt_opening"
ENGLISH = "Claude Code is still opening this prompt in the terminal, so tap it again in a moment."
CODES = ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru", "tr", "uk", "vi",
         "zh-Hans"]


def rows(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row.get("k")] = row
    return out


def source_row():
    return rows(ROOT / "tools" / "i18n" / "source.jsonl").get(KEY)


def locale_value(code):
    row = rows(ROOT / "aipager" / "i18n" / "locales" / f"{code}.jsonl").get(KEY)
    return (row or {}).get("v")


def test_source_has_the_english():
    assert (source_row() or {}).get("en") == ENGLISH


def test_source_kind_is_a_toast():
    assert (source_row() or {}).get("kind") == "toast"


@pytest.mark.parametrize("code", CODES)
def test_locale_has_a_value(code):
    assert locale_value(code)


@pytest.mark.parametrize("code", CODES)
def test_locale_value_is_translated(code):
    assert locale_value(code) != ENGLISH


@pytest.mark.parametrize("code", CODES)
def test_locale_value_fits_a_popup(code):
    assert len(locale_value(code) or "x" * 201) <= 200


@pytest.mark.parametrize("code", CODES)
def test_locale_value_has_no_em_dash(code):
    assert "—" not in (locale_value(code) or "")


def test_english_fits_a_popup():
    assert len(ENGLISH) <= 200


def test_english_commands_doc_quotes_the_toast():
    text = " ".join((ROOT / "docs" / "commands.md").read_text(encoding="utf-8").split())
    assert ENGLISH in text


@pytest.mark.parametrize("code", CODES)
def test_translated_commands_doc_quotes_its_toast(code):
    path = ROOT / "docs" / "i18n" / code / "commands.md"
    text = " ".join(path.read_text(encoding="utf-8").split())
    assert " ".join((locale_value(code) or "\0").split()) in text
