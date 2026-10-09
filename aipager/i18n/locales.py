"""The languages aipager knows, and which of them a person may pick.

``LOCALES`` holds the 15 languages of the multilingual design, each with
its own name (the endonym, never translated), its direction and its
digits. Only English is enabled: the others exist so the machinery can
be built and checked before any translation ships.

``PSEUDO`` holds two fake languages for tests and tooling (``qps`` and
its right-to-left twin ``qps-rtl``). They are reachable through
:func:`aipager.i18n.get` but never listed, never returned by
:func:`normalize` and never selectable, unless a test adds a code to
``_TEST_ENABLED`` (there is no env var or setting for it).

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LOCALES", "Locale", "enabled_codes", "is_selectable", "normalize"]


@dataclass(frozen=True)
class Locale:
    code: str
    endonym: str
    english_name: str
    rtl: bool = False
    #: CLDR numbering system: "latn" (0-9), "arabext" (fa), "arab" (ar).
    digits: str = "latn"
    decimal: str = "."
    enabled: bool = False
    #: "stable" once reviewed, "beta" before, "" while not translated.
    quality: str = ""
    #: The code Telegram's /-menu and bot descriptions take (two letters).
    telegram_code: str = ""


LOCALES: dict[str, Locale] = {loc.code: loc for loc in (
    Locale("en", "English", "English", enabled=True, quality="stable",
           telegram_code="en"),
    Locale("es", "Español", "Spanish", telegram_code="es"),
    Locale("ru", "Русский", "Russian", telegram_code="ru"),
    Locale("pt-BR", "Português (Brasil)", "Portuguese (Brazil)", telegram_code="pt"),
    Locale("zh-Hans", "简体中文", "Chinese (Simplified)", telegram_code="zh"),
    Locale("fr", "Français", "French", telegram_code="fr"),
    Locale("de", "Deutsch", "German", telegram_code="de"),
    Locale("ko", "한국어", "Korean", telegram_code="ko"),
    Locale("ar", "العربية", "Arabic", rtl=True, digits="arab", decimal="٫",
           telegram_code="ar"),
    Locale("id", "Bahasa Indonesia", "Indonesian", telegram_code="id"),
    Locale("uk", "Українська", "Ukrainian", telegram_code="uk"),
    Locale("it", "Italiano", "Italian", telegram_code="it"),
    Locale("tr", "Türkçe", "Turkish", telegram_code="tr"),
    Locale("fa", "فارسی", "Persian", rtl=True, digits="arabext", decimal="٫",
           telegram_code="fa"),
    Locale("vi", "Tiếng Việt", "Vietnamese", telegram_code="vi"),
)}

PSEUDO: dict[str, Locale] = {loc.code: loc for loc in (
    Locale("qps", "Pseudo", "Pseudo"),
    Locale("qps-rtl", "Pseudo RTL", "Pseudo RTL", rtl=True),
)}

#: Codes a test has switched on. Tests monkeypatch this; nothing else
#: writes it.
_TEST_ENABLED: frozenset[str] = frozenset()

# Telegram's language_code spellings that are not our codes.
_ALIASES = {
    "in": "id",
    "pt": "pt-BR",
    "pt-br": "pt-BR",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans": "zh-Hans",
}

# Traditional Chinese must not fall back to Simplified through "zh": it
# gets English, like every other language we do not have.
_NOT_OURS = frozenset({"zh-hant", "zh-tw", "zh-hk", "zh-mo"})

_BY_LOWER = {code.lower(): code for code in LOCALES}


def lookup(code: str) -> Locale | None:
    """The Locale for one of our codes (pseudo codes included), or None."""
    return LOCALES.get(code) or PSEUDO.get(code)


def enabled_codes() -> tuple[str, ...]:
    """The codes a person may pick, in ``LOCALES`` order, then any pseudo
    code a test switched on."""
    on = [c for c, loc in LOCALES.items() if loc.enabled or c in _TEST_ENABLED]
    on += [c for c in PSEUDO if c in _TEST_ENABLED]
    return tuple(on)


def is_selectable(code: object) -> bool:
    return isinstance(code, str) and code in enabled_codes()


def _ours(tag: str) -> str | None:
    code = _ALIASES.get(tag) or _BY_LOWER.get(tag)
    if code and code in LOCALES and (LOCALES[code].enabled or code in _TEST_ENABLED):
        return code
    return None


def normalize(language_code: object) -> str | None:
    """An enabled code for a Telegram ``language_code`` (``"pt-br"``,
    ``"zh-hans"``, ``"en-US"``, ``"in"``), or None when we do not have
    that language (or it is not enabled). Never a pseudo code."""
    if not isinstance(language_code, str) or not language_code.strip():
        return None
    tag = language_code.strip().lower().replace("_", "-")
    parts = tag.split("-")
    candidates = [tag]
    if len(parts) > 2:
        candidates.append("-".join(parts[:2]))
    if len(parts) > 1:
        candidates.append(parts[0])
    for cand in candidates:
        if cand in _NOT_OURS:
            return None
        code = _ours(cand)
        if code:
            return code
    return None
