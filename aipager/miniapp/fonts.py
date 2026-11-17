"""The Mini App's bundled fonts, served from the daemon at ``/fonts/<name>``.

Persian, Arabic and Vietnamese read best in a font made for them (the
project site uses the same three); the system font is whatever a phone
falls back to. The files sit in ``static/fonts/`` beside their licenses
(see the README there). The page loads them from this origin only: no
font ever comes from another host.

The route answers only the names in ``FONT_FILES``: a fixed list, never a
path built from the request, so nothing outside that folder can be asked
for. Like ``GET /`` and the SDK script it has no auth gate: a stylesheet
fetches fonts without the page's initData header, and the bytes are
public. Every name carries its font's version, so a file never changes
under its name and may be cached for a year.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "static" / "fonts"

FONT_FILES = frozenset({
    "vazirmatn-33.003-arabic.woff2",
    "noto-sans-arabic-2.012-arabic.woff2",
    "be-vietnam-pro-1.002-400-latin.woff2",
    "be-vietnam-pro-1.002-400-vietnamese.woff2",
    "be-vietnam-pro-1.002-500-latin.woff2",
    "be-vietnam-pro-1.002-500-vietnamese.woff2",
    "be-vietnam-pro-1.002-600-latin.woff2",
    "be-vietnam-pro-1.002-600-vietnamese.woff2",
    "be-vietnam-pro-1.002-700-latin.woff2",
    "be-vietnam-pro-1.002-700-vietnamese.woff2",
})

FONT_CONTENT_TYPE = "font/woff2"
FONT_CACHE_CONTROL = "public,max-age=31536000,immutable"


def font_bytes(name: str) -> bytes | None:
    """The file *name* names, or None for a name not in ``FONT_FILES``
    (or a file missing from a broken install). The name is checked
    before the cache, so the cache holds at most the listed files."""
    if name not in FONT_FILES:
        return None
    return _read(name)


@lru_cache(maxsize=len(FONT_FILES))
def _read(name: str) -> bytes | None:
    try:
        return (FONTS_DIR / name).read_bytes()
    except OSError:
        return None
