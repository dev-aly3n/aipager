"""Check 15: aipager's hand-written plural rules equal CLDR's.

Babel (a dev-only dependency) carries CLDR's rules; when it is installed
every category of every count 0..10000 (plus a few millions) must match,
and so must the set of categories each language uses. Babel is not in
every local venv, so a frozen table recorded from Babel 2.10.3 always
runs and fails a broken rule without it.
"""

from __future__ import annotations

import pytest

from aipager.i18n import plurals
from aipager.i18n.locales import LOCALES

_BABEL_NAMES = {"pt-BR": "pt_BR", "zh-Hans": "zh_Hans"}

_COUNTS = (0, 1, 2, 3, 4, 5, 10, 11, 12, 14, 21, 22, 25, 100, 101, 102, 103,
           111, 1_000_000, 2_000_000, 1_000_000_000)

# Recorded from Babel 2.10.3 (CLDR) for the counts above.
_FROZEN = {
    "en": "other one other other other other other other other other other other other "
          "other other other other other other other other",
    "es": "other one other other other other other other other other other other other "
          "other other other other other many many many",
    "ru": "many one few few few many many many many many one few many many one few few "
          "many many many many",
    "pt-BR": "one one other other other other other other other other other other other "
             "other other other other other many many many",
    "zh-Hans": " ".join(["other"] * len(_COUNTS)),
    "fr": "one one other other other other other other other other other other other "
          "other other other other other many many many",
    "de": "other one other other other other other other other other other other other "
          "other other other other other other other other",
    "ko": " ".join(["other"] * len(_COUNTS)),
    "ar": "zero one two few few few few many many many many many many other other other "
          "few many other other other",
    "id": " ".join(["other"] * len(_COUNTS)),
    "uk": "many one few few few many many many many many one few many many one few few "
          "many many many many",
    "it": "other one other other other other other other other other other other other "
          "other other other other other many many many",
    "tr": "other one other other other other other other other other other other other "
          "other other other other other other other other",
    "fa": "one one other other other other other other other other other other other "
          "other other other other other other other other",
    "vi": " ".join(["other"] * len(_COUNTS)),
}

_FROZEN_SETS = {
    "en": {"one"}, "de": {"one"}, "tr": {"one"}, "fa": {"one"},
    "es": {"one", "many"}, "it": {"one", "many"}, "fr": {"one", "many"},
    "pt-BR": {"one", "many"},
    "ru": {"one", "few", "many"}, "uk": {"one", "few", "many"},
    "ar": {"zero", "one", "two", "few", "many"},
    "zh-Hans": set(), "ko": set(), "id": set(), "vi": set(),
}


def test_every_language_has_a_frozen_row():
    assert set(_FROZEN) == set(LOCALES) == set(_FROZEN_SETS)


@pytest.mark.parametrize("code", sorted(_FROZEN))
def test_plural_rules_match_the_frozen_cldr_table(code):
    got = " ".join(plurals.category(code, n) for n in _COUNTS)
    assert got == _FROZEN[code]
    assert set(plurals.categories(code)) - {"other"} == _FROZEN_SETS[code]
    assert "other" in plurals.categories(code)


@pytest.mark.parametrize("code", sorted(_FROZEN))
def test_plural_rules_match_babel(code):
    babel = pytest.importorskip("babel")
    loc = babel.Locale.parse(_BABEL_NAMES.get(code, code))
    rule = loc.plural_form
    counts = list(range(0, 10_001)) + [10**6, 2 * 10**6, 10**9]
    wrong = [n for n in counts if plurals.category(code, n) != rule(n)]
    assert not wrong, f"{code}: differs from CLDR at {wrong[:10]}"
    assert set(plurals.categories(code)) - {"other"} == set(rule.tags) - {"other"}


def test_pseudo_codes_and_unknown_codes_follow_english():
    for code in ("qps", "qps-rtl", "xx"):
        assert [plurals.category(code, n) for n in (0, 1, 2)] == ["other", "one", "other"]
        assert plurals.categories(code) == ("one", "other")
