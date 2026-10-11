"""The languages aipager knows, and which of them a person may pick.

``LOCALES`` holds the 15 languages of the multilingual design, each with
its own name (the endonym, never translated), its direction and its
digits. English is enabled; the other 14 (Russian, Persian, Spanish,
Portuguese (Brazil), French, Italian, German, Turkish, Ukrainian, Arabic,
Chinese (Simplified), Korean, Indonesian and Vietnamese) are enabled as
beta until a native speaker has reviewed them: :func:`is_beta` until a
review is recorded in ``Locale.reviewed``. A person's Telegram language is
followed by :func:`detect` (on since phase 5, :data:`AUTO_DETECT`).

``PSEUDO`` holds two fake languages for tests and tooling (``qps`` and
its right-to-left twin ``qps-rtl``). They are reachable through
:func:`aipager.i18n.get` but never listed, never returned by
:func:`normalize` and never selectable, unless a test adds a code to
``_TEST_ENABLED`` (there is no env var or setting for it).

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AUTO_DETECT", "LOCALES", "Locale", "detect", "enabled_codes", "is_beta",
           "is_selectable", "normalize"]


@dataclass(frozen=True)
class Locale:
    code: str
    endonym: str
    english_name: str
    rtl: bool = False
    #: CLDR numbering system: "latn" (0-9), "arabext" (fa), "arab" (ar).
    digits: str = "latn"
    decimal: str = "."
    #: The thousands separator and percent sign ``Tr.num`` writes into a
    #: number already formatted as a string (native digits only).
    group: str = ","
    percent: str = "%"
    enabled: bool = False
    #: "stable" once reviewed, "beta" before, "" while not translated.
    quality: str = ""
    #: The code Telegram's /-menu and bot descriptions take (two letters).
    telegram_code: str = ""
    #: The native review that made the language stable, as a free-text
    #: record ("native review of the golden strings, <name>, 2026-11-02");
    #: empty until one is done. A language shows "(beta)" until it has
    #: both ``quality="stable"`` and this record (:func:`is_beta`).
    reviewed: str = ""


LOCALES: dict[str, Locale] = {loc.code: loc for loc in (
    Locale("en", "English", "English", enabled=True, quality="stable",
           telegram_code="en"),
    Locale("es", "Español", "Spanish", enabled=True, quality="beta", telegram_code="es"),
    # Phase 2: offered as beta (translated, not reviewed yet).
    Locale("ru", "Русский", "Russian", enabled=True, quality="beta", telegram_code="ru"),
    Locale("pt-BR", "Português (Brasil)", "Portuguese (Brazil)", enabled=True, quality="beta",
           telegram_code="pt"),
    Locale("zh-Hans", "简体中文", "Chinese (Simplified)", enabled=True, quality="beta",
           telegram_code="zh"),
    Locale("fr", "Français", "French", enabled=True, quality="beta", telegram_code="fr"),
    Locale("de", "Deutsch", "German", enabled=True, quality="beta", telegram_code="de"),
    Locale("ko", "한국어", "Korean", enabled=True, quality="beta", telegram_code="ko"),
    # CLDR's Arabic percent sign is U+066A followed by the Arabic letter
    # mark (U+061C, written as an escape: no raw direction control in
    # source), which keeps the sign on the number's side in a sentence.
    Locale("ar", "العربية", "Arabic", rtl=True, digits="arab", decimal="٫",
           group="٬", percent="٪\u061c", enabled=True, quality="beta", telegram_code="ar"),
    Locale("id", "Bahasa Indonesia", "Indonesian", enabled=True, quality="beta",
           telegram_code="id"),
    Locale("uk", "Українська", "Ukrainian", enabled=True, quality="beta", telegram_code="uk"),
    Locale("it", "Italiano", "Italian", enabled=True, quality="beta", telegram_code="it"),
    Locale("tr", "Türkçe", "Turkish", enabled=True, quality="beta", telegram_code="tr"),
    Locale("fa", "فارسی", "Persian", rtl=True, digits="arabext", decimal="٫",
           group="٬", percent="٪", enabled=True, quality="beta", telegram_code="fa"),
    Locale("vi", "Tiếng Việt", "Vietnamese", enabled=True, quality="beta", telegram_code="vi"),
)}

PSEUDO: dict[str, Locale] = {loc.code: loc for loc in (
    Locale("qps", "Pseudo", "Pseudo"),
    Locale("qps-rtl", "Pseudo RTL", "Pseudo RTL", rtl=True),
)}

#: Codes a test has switched on. Tests monkeypatch this; nothing else
#: writes it.
_TEST_ENABLED: frozenset[str] = frozenset()

#: Whether aipager follows a person's Telegram language when they did not
#: choose one (phase 5): on, for people new since the launch marker
#: (``aipager.i18n.persist``) and for those who picked "Automatic"; people
#: who existed before stay English until they choose. The kill switch:
#: False brings back the pre-launch behaviour for everyone but strangers
#: (who always get their Telegram language).
AUTO_DETECT: bool = True

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


def is_beta(code: object) -> bool:
    """Whether *code* is listed with "(beta)": every language but English
    until a native review is recorded (``quality == "stable"`` and a
    ``reviewed`` record). Persian stays beta until the operator's review."""
    if code == "en" or (isinstance(code, str) and code in PSEUDO):
        return False
    loc = LOCALES.get(code) if isinstance(code, str) else None
    if loc is None:
        return True
    return not (loc.quality == "stable" and loc.reviewed.strip())


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


def detect(language_code: object, *, follow: bool = True) -> str | None:
    """The language to follow for a Telegram ``language_code`` when the
    chat did not choose: English for an English app; any enabled language
    when *follow* is true (the person follows Telegram) and
    :data:`AUTO_DETECT` is on, or when a test switched that code on
    through ``_TEST_ENABLED``; else None, which reads as English."""
    code = normalize(language_code)
    if code is None:
        return None
    if code == "en" or code in _TEST_ENABLED or (follow and AUTO_DETECT):
        return code
    return None
