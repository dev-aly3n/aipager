"""Black-box: the beta label, the review record and the golden strings (phase 5
items 1 and 5; design.md section 8, D14; entrypoints.md ``locales.is_beta``,
``Locale.reviewed``, ``render_language_menu``, ``tools/i18n/golden.txt`` and
``tools/i18n.py review-pack --golden`` / ``todo`` / ``check``).

Success criteria: every non-English language shows "(beta)" (Persian
included); a language marked stable without ``reviewed`` still shows
"(beta)"; ``golden.txt`` has 120-180 valid keys in the fixed sections;
``review-pack --golden`` emits only those keys, with English; ``todo --lang
X`` is empty for all 14; ``check`` exits 0.

The CLI runs as a subprocess on a throwaway HOME.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot import language_menu
from aipager.i18n import locales

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "tools" / "i18n" / "golden.txt"
SECTIONS = ("first_contact", "busy_card", "permission", "settings", "language", "errors",
            "keyboard")
LAUNCHED = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it", "tr",
            "fa", "vi")


def cli(tmp_path, *args):
    home = tmp_path / "cli-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AIPAGER_", "CLAUDE_TG_", "CLAUDE_CODE_"))}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(REPO / "tools" / "i18n.py"), *args],
                          cwd=str(REPO), env=env, capture_output=True, text=True,
                          timeout=300)


def golden_rows():
    rows, section = [], None
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            name = line[1:].strip()
            if name in SECTIONS:
                section = name
            continue
        rows.append((section, line))
    return rows


def source_keys():
    src = REPO / "tools" / "i18n" / "source.jsonl"
    return {json.loads(x)["k"] for x in src.read_text(encoding="utf-8").splitlines()
            if x.strip()}


# ---- beta ------------------------------------------------------------------------------

@pytest.mark.parametrize("code", LAUNCHED)
def test_every_launched_language_is_beta(code):
    assert locales.is_beta(code) is True


def test_english_is_not_beta():
    assert locales.is_beta("en") is False


@pytest.mark.parametrize("code", LAUNCHED)
def test_no_language_has_a_review_recorded(code):
    assert locales.LOCALES[code].reviewed == ""


def test_persian_stays_beta():
    assert locales.LOCALES["fa"].quality == "beta"


def _set_locale(monkeypatch, code, **fields):
    monkeypatch.setitem(locales.LOCALES, code,
                        dataclasses.replace(locales.LOCALES[code], **fields))


def test_stable_without_a_review_is_still_beta(monkeypatch):
    _set_locale(monkeypatch, "fa", quality="stable", reviewed="")
    assert locales.is_beta("fa") is True


def test_stable_with_a_review_is_not_beta(monkeypatch):
    _set_locale(monkeypatch, "fa", quality="stable",
                reviewed="native review of the golden strings, A. Reviewer, 2026-11-02")
    assert locales.is_beta("fa") is False


def test_a_review_without_stable_is_still_beta(monkeypatch):
    _set_locale(monkeypatch, "ru", quality="beta", reviewed="native review, 2026-11-02")
    assert locales.is_beta("ru") is True


def _menu_labels(current="en", private=True):
    out = language_menu.render_language_menu(i18n.get("en"), private=private, current=current)
    markup = out[1] if isinstance(out, tuple) else out
    return [b.text for row in markup.inline_keyboard for b in row]


@pytest.mark.parametrize("code", LAUNCHED)
def test_the_picker_labels_every_launched_language_beta(code):
    (endonym,) = [t for t in _menu_labels()
                  if t.lstrip("✅ ").startswith(locales.LOCALES[code].endonym)]
    assert "(beta)" in endonym


def test_the_picker_does_not_label_english_beta():
    assert not any("English" in t and "(beta)" in t for t in _menu_labels())


def test_the_picker_keeps_beta_for_stable_without_a_review(monkeypatch):
    _set_locale(monkeypatch, "fa", quality="stable", reviewed="")
    (fa,) = [t for t in _menu_labels() if locales.LOCALES["fa"].endonym in t]
    assert "(beta)" in fa


def test_the_picker_drops_beta_after_a_recorded_review(monkeypatch):
    _set_locale(monkeypatch, "fa", quality="stable", reviewed="native review, 2026-11-02")
    (fa,) = [t for t in _menu_labels() if locales.LOCALES["fa"].endonym in t]
    assert "(beta)" not in fa


def test_the_private_picker_offers_automatic():
    assert any("Automatic" in t for t in _menu_labels())


def test_the_group_picker_does_not_offer_automatic():
    assert not any("Automatic" in t for t in _menu_labels(private=False))


# ---- golden.txt ----------------------------------------------------------------------------

def test_the_golden_list_has_120_to_180_keys():
    assert 120 <= len(golden_rows()) <= 180


def test_every_golden_key_is_under_a_known_section():
    assert all(section in SECTIONS for section, _k in golden_rows())


def test_every_section_has_keys():
    assert {section for section, _k in golden_rows()} == set(SECTIONS)


def test_no_golden_key_repeats():
    keys = [k for _s, k in golden_rows()]
    assert len(keys) == len(set(keys))


def test_every_golden_key_is_a_real_key():
    assert {k for _s, k in golden_rows()} - source_keys() == set()


@pytest.mark.parametrize("key", ["lang.first_contact", "lang.offer.text", "botdesc.short",
                                 "botdesc.description"])
def test_the_launch_texts_are_golden(key):
    assert key in {k for _s, k in golden_rows()}


def test_the_mode_words_are_golden():
    keys = {k for _s, k in golden_rows()}
    assert {"mode.ask", "mode.auto"} <= keys


# ---- review-pack --golden ------------------------------------------------------------------

@pytest.fixture(scope="module")
def fa_pack(tmp_path_factory):
    res = cli(tmp_path_factory.mktemp("pack"), "review-pack", "--lang", "fa", "--golden")
    assert res.returncode == 0, res.stderr[-2000:]
    return json.loads(res.stdout)["fa"]


def test_the_golden_pack_has_exactly_the_golden_keys_in_order(fa_pack):
    assert [e["k"] for e in fa_pack] == [k for _s, k in golden_rows()]


def test_the_golden_pack_names_each_keys_section(fa_pack):
    assert [e["section"] for e in fa_pack] == [s for s, _k in golden_rows()]


def test_the_golden_pack_carries_the_english(fa_pack):
    assert all(("en" in e) or ("one" in e and "other" in e) for e in fa_pack)


def test_the_golden_pack_english_is_the_source_text(fa_pack):
    (row,) = [e for e in fa_pack if e["k"] == "botdesc.short"]
    assert row["en"] == "Control Claude Code sessions from Telegram."


def test_the_golden_pack_carries_the_translation(fa_pack):
    from tests.i18n_ml2_bb import catalog
    (row,) = [e for e in fa_pack if e["k"] == "botdesc.short"]
    assert row["v"] == catalog("fa")["botdesc.short"]["v"]


def _short_msg():
    from aipager.bot import bot_descriptions
    return bot_descriptions.SHORT


def test_the_golden_pack_carries_notes(fa_pack):
    assert all("note" in e for e in fa_pack)


@pytest.mark.parametrize("code", ["ja", "pl", "xx", "en"])
def test_the_golden_pack_refuses_an_untranslated_language(tmp_path, code):
    assert cli(tmp_path, "review-pack", "--lang", code, "--golden").returncode == 2


def test_the_golden_pack_without_a_language_covers_all_14(tmp_path):
    res = cli(tmp_path, "review-pack", "--golden")
    assert sorted(json.loads(res.stdout)) == sorted(LAUNCHED)


def test_the_golden_pack_with_changed_is_a_subset(tmp_path):
    res = cli(tmp_path, "review-pack", "--lang", "fa", "--golden", "--changed")
    keys = [e["k"] for e in json.loads(res.stdout).get("fa", [])]
    assert set(keys) <= {k for _s, k in golden_rows()}


# ---- todo and check (item 1) -----------------------------------------------------------------

@pytest.mark.parametrize("code", LAUNCHED)
def test_todo_is_empty(tmp_path, code):
    res = cli(tmp_path, "todo", "--lang", code)
    assert (res.returncode, json.loads(res.stdout)["entries"]) == (0, [])


def test_check_passes(tmp_path):
    res = cli(tmp_path, "check")
    assert res.returncode == 0, (res.stdout + res.stderr)[-3000:]


def test_extract_is_current(tmp_path):
    res = cli(tmp_path, "extract", "--check")
    assert res.returncode == 0, (res.stdout + res.stderr)[-3000:]


@pytest.mark.parametrize("code", LAUNCHED)
@pytest.mark.parametrize("key", ["botdesc.description", "botdesc.short"])
def test_the_description_keys_are_translated(code, key):
    from aipager.i18n import core
    assert core.has_translation(code, key) is True


def test_has_translation_is_false_for_an_unknown_key():
    from aipager.i18n import core
    assert core.has_translation("fa", "no.such.key") is False


@pytest.mark.parametrize("code", LAUNCHED)
def test_the_short_description_fits_telegrams_120(code):
    assert len(i18n.get(code).text(_short_msg())) <= 120


@pytest.mark.parametrize("code", LAUNCHED + ("en",))
def test_the_description_fits_telegrams_512(code):
    from aipager.bot import bot_descriptions
    assert len(i18n.get(code).text(bot_descriptions.DESCRIPTION)) <= 512
