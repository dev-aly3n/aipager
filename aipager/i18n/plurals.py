"""Integer CLDR plural rules for aipager's 15 languages.

Hand-written so the daemon needs no Babel at runtime. Only whole numbers
are ever counted in aipager's text (tool calls, agents, messages), so
only the integer half of each CLDR rule is implemented. CI checks these
rules against Babel's CLDR data (tests/test_i18n_plurals.py), and a
frozen table pins them where Babel is not installed.

Pure: no I/O.
"""

from __future__ import annotations

__all__ = ["categories", "category"]


def _one(n: int) -> str:
    return "one" if n == 1 else "other"


def _zero_one(n: int) -> str:
    return "one" if n in (0, 1) else "other"


def _millions(n: int) -> bool:
    # CLDR's "many" for es/fr/it/pt: e = 0 and i != 0 and i % 1000000 = 0.
    return n != 0 and n % 1_000_000 == 0


def _romance_one(n: int) -> str:
    if n == 1:
        return "one"
    return "many" if _millions(n) else "other"


def _romance_zero_one(n: int) -> str:
    if n in (0, 1):
        return "one"
    return "many" if _millions(n) else "other"


def _east_slavic(n: int) -> str:
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return "one"
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return "few"
    return "many"


def _arabic(n: int) -> str:
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    mod100 = n % 100
    if 3 <= mod100 <= 10:
        return "few"
    if 11 <= mod100 <= 99:
        return "many"
    return "other"


def _other(n: int) -> str:
    return "other"


_ONE_OTHER = ("one", "other")
_ROMANCE = ("one", "many", "other")
_SLAVIC = ("one", "few", "many", "other")

#: code -> (rule, categories in CLDR order)
_RULES = {
    "en": (_one, _ONE_OTHER),
    "de": (_one, _ONE_OTHER),
    "tr": (_one, _ONE_OTHER),
    "fa": (_zero_one, _ONE_OTHER),
    "es": (_romance_one, _ROMANCE),
    "it": (_romance_one, _ROMANCE),
    "fr": (_romance_zero_one, _ROMANCE),
    "pt-BR": (_romance_zero_one, _ROMANCE),
    "ru": (_east_slavic, _SLAVIC),
    "uk": (_east_slavic, _SLAVIC),
    "ar": (_arabic, ("zero", "one", "two", "few", "many", "other")),
    "zh-Hans": (_other, ("other",)),
    "ko": (_other, ("other",)),
    "id": (_other, ("other",)),
    "vi": (_other, ("other",)),
    # The pseudo locales follow English.
    "qps": (_one, _ONE_OTHER),
    "qps-rtl": (_one, _ONE_OTHER),
}


def category(code: str, n: int) -> str:
    """The CLDR plural category of the whole number *n* in *code*.
    A negative count is measured by its size; an unknown code follows
    English."""
    rule = _RULES.get(code, _RULES["en"])[0]
    return rule(abs(int(n)))


def categories(code: str) -> tuple[str, ...]:
    """Every category *code* uses, in CLDR order (what a catalog entry
    for a plural key must carry)."""
    return _RULES.get(code, _RULES["en"])[1]
