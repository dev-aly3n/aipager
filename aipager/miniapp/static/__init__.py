"""The Mini App shell, assembled from sibling ``.py`` string modules.

Stage 1 shipped this as a single ``static.py`` string constant. Stage 2's
UI (grid + drill-down + diff viewer) outgrew a single ~130-line string
readably, so this became a package of plain string modules concatenated
at import time — see design.md Decision 4 for why that's the chosen
split (not real ``.html``/``.js``/``.css`` files, which would need new
wheel package-data config; not one giant string, which stops being
reviewable past this size). Because these are ``.py`` files,
``packages = ["aipager"]`` (pyproject.toml) already ships them — the
exact mechanism that shipped ``static.py`` in stage 1 — so there is no
new packaging risk. The public import path is unchanged:
``from aipager.miniapp.static import INDEX_HTML``.

The page has no secrets and needs none baked in: same as stage 1, it
fetches ``Telegram.WebApp.initData`` client-side and sends it as the
``X-Telegram-Init-Data`` header on every API call. Unauthenticated by
necessity — see design.md's threat model item 2.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata

from aipager.miniapp.static._app import APP_JS
from aipager.miniapp.static._shell import (
    HTML_BODY,
    HTML_TAIL,
    SDK_SRC_SELF,
    SDK_SRC_TELEGRAM,
    html_head,
)
from aipager.miniapp.static._styles import CSS


def _english_catalog_js(first: str = "") -> str:
    """The page's English strings, one JS statement (multilingual phase
    4): the page holds keys only, and this is what ``t()`` reads for an
    English viewer and falls back to for a string another language lacks.
    ASCII only (no control, direction or line-separator character can
    reach the script) and never ``</`` (it cannot close the element).
    *first* (``_first_catalog_js``) declares the link's language in the
    same statement."""
    import json

    from aipager.miniapp.ui_text import english_catalog
    data = json.dumps(english_catalog(), sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))
    # Code, not prose: counted once by the English-literal ratchet.
    return "var AIPAGER_UI_EN=" + data.replace("</", "<\\/") + first + ";\n"


# Everything below the head is identical in both variants, so build it
# once and only vary the head.
_PAGE_TAIL = (
    "<style>\n" + CSS + "</style>\n"
    + HTML_BODY
    + "<script>\n" + _english_catalog_js() + APP_JS + "\n</script>\n"
    + HTML_TAIL
)


def index_html(*, sdk_from_self: bool = True, lang: str | None = None) -> str:
    """The page, with Telegram's SDK loaded from this origin when the
    daemon has a copy of it (``sdk_from_self``) and straight from
    telegram.org when it does not — the pre-8.18 behaviour, which is the
    floor this feature must never fall below. ``server.py`` decides.

    *lang* (the link's ``?lang=``): a language a person may pick, other
    than English, makes the first frame already that language's: ``<html
    lang dir>``, the marked strings in the markup, and its catalog inlined
    for ``t()`` (no request before the first paint). Anything else is the
    English page, byte for byte.
    """
    head = html_head(SDK_SRC_SELF if sdk_from_self else SDK_SRC_TELEGRAM)
    first = _first_language(lang)
    if first is None:
        return head + _PAGE_TAIL
    cat, version = first
    head = head.replace(_html_tag(lang="en"), _html_tag(
        lang=cat["code"], dir="rtl" if cat["rtl"] else "ltr"), 1)
    return head + (
        "<style>\n" + CSS + "</style>\n"
        + _localized_body(HTML_BODY, cat)
        + "<script>\n" + _english_catalog_js(_first_catalog_js(cat, version)) + APP_JS
        + "\n</script>\n"
        + HTML_TAIL
    )


# ---- the first frame in the link's language ----------------------------------

_FSI_REF = "&#x2068;"   # FIRST STRONG ISOLATE, as a character reference
_PDI_REF = "&#x2069;"   # POP DIRECTIONAL ISOLATE

# The static strings that hold a parameter (``STATIC_PARAMS`` in _app.py).
_STATIC_PARAMS = {"ui.empty.text": {"command": "/new"}}

# A start tag and the text right after it (up to the next tag).
_TAG_TEXT_RE = re.compile(r"(<[A-Za-z][^>]*>)([^<]*)")
# An attribute by its whole name (``placeholder`` never matches inside
# ``data-i18n-placeholder``).
_ATTR_RE = r'(?<![\w-]){0}="([^"]*)"'


def _first_language(code) -> tuple[dict, str] | None:
    """``(catalog, version)`` for a ``?lang=`` worth honouring, else None
    (English, a language nobody may pick, anything malformed)."""
    if not isinstance(code, str) or code == "en":
        return None
    # catalog() is None for any code a person may not pick.
    from aipager.miniapp.ui_text import catalog, catalog_version
    cat = catalog(code)
    version = catalog_version(code)
    if cat is None or version is None:
        return None
    return cat, version


def page_language(code) -> str:
    """The language a ``?lang=`` value gives the page's first frame: the
    code itself when it is honoured, else ``"en"`` (the English page)."""
    return code if _first_language(code) is not None else "en"


def _html_tag(**attrs: str) -> str:
    """``<html>`` with *attrs* in order (``lang`` alone is the English
    page's own tag, exactly)."""
    return "<html" + "".join(f' {k}="{html.escape(v, quote=True)}"'
                             for k, v in attrs.items()) + ">"


def _first_catalog_js(cat: dict, version: str) -> str:
    """The link's language, declared after the English catalog in its
    ``var`` statement, which ``t()`` reads at start: the same body
    ``/api/i18n/<code>.json`` serves and its version. Letters as they are
    (``\\uXXXX`` would triple a Persian page's catalog), every invisible
    character escaped, never ``</``."""
    data = json.dumps({"v": version, "cat": cat}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return ",AIPAGER_UI_FIRST=" + _script_safe(data)


def _script_safe(data: str) -> str:
    """JSON text safe inside an inline ``<script>``: every control, format
    (direction marks, joiners), private-use, unassigned and separator
    character other than a plain space is written as an escape, so none
    reaches the page raw, and ``</`` cannot close the element."""
    out = []
    for ch in data:
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7F or (cp > 0x7F and unicodedata.category(ch)[0] in "CZ"):
            units = _surrogates(cp) if cp > 0xFFFF else (cp,)
            out.append("".join("\\u%04x" % u for u in units))
        else:
            out.append(ch)
    return "".join(out).replace("</", "<\\/")


def _surrogates(cp: int) -> tuple[int, int]:
    cp -= 0x10000
    return 0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)


def _num(text: str, cat: dict) -> str:
    """``num()`` of _app.py: the language's digits and signs."""
    digits = cat.get("digits")
    if not isinstance(digits, str) or len(digits) != 10 or digits == "0123456789":
        return text
    out = (text.replace(".", cat.get("decimal") or ".").replace(",", cat.get("group") or ",")
           .replace("%", cat.get("percent") or "%"))
    return out.translate(str.maketrans("0123456789", digits))


def _t(key: str, cat: dict) -> str:
    """``t()`` of _app.py, escaped for HTML: the language's string, else
    its English; a parameter isolated in a right-to-left language."""
    from aipager.miniapp.ui_text import english_catalog
    pattern = cat["strings"].get(key)
    if not isinstance(pattern, str):
        pattern = english_catalog().get(key)
    if not isinstance(pattern, str):
        return ""
    params = _STATIC_PARAMS.get(key, {})

    def one(m: re.Match) -> str:
        if m.group(0) == "{{":
            return "{"
        if m.group(0) == "}}":
            return "}"
        value = html.escape(str(params.get(m.group(1), "")), quote=True)
        return _FSI_REF + value + _PDI_REF if cat["rtl"] and value else value

    out = []
    for part in _PLACEHOLDER_SPLIT_RE.split(pattern):
        m = _PLACEHOLDER_RE.fullmatch(part)
        out.append(one(m) if m else html.escape(part, quote=True))
    return "".join(out)


_PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PLACEHOLDER_SPLIT_RE = re.compile(r"(\{\{|\}\}|\{[A-Za-z_][A-Za-z0-9_]*\})")


def _set_attr(tag: str, name: str, value: str) -> str:
    return re.sub(_ATTR_RE.format(re.escape(name)),
                  lambda _m: name + '="' + value + '"', tag, count=1)


def _localized_body(body: str, cat: dict) -> str:
    """*body* with every marked element as ``applyStatic()`` leaves it in
    *cat*'s language: its text (``data-i18n``, ``data-i18n-num``), its
    aria-label and its placeholder; the "Loading" line follows its own
    direction, as ``applyStatic()`` sets it."""
    def one(m: re.Match) -> str:
        tag, text = m.group(1), m.group(2)
        if "data-i18n" not in tag:
            return m.group(0)
        attrs = {name: re.search(_ATTR_RE.format(name), tag)
                 for name in ("data-i18n", "data-i18n-num", "data-i18n-label",
                              "data-i18n-placeholder")}
        if attrs["data-i18n-label"]:
            tag = _set_attr(tag, "aria-label", _t(attrs["data-i18n-label"].group(1), cat))
        if attrs["data-i18n-placeholder"]:
            tag = _set_attr(tag, "placeholder",
                            _t(attrs["data-i18n-placeholder"].group(1), cat))
        if attrs["data-i18n-num"]:
            text = html.escape(_num(html.unescape(attrs["data-i18n-num"].group(1)), cat),
                               quote=False)
        if attrs["data-i18n"]:
            text = _t(attrs["data-i18n"].group(1), cat)
        if re.search(r'(?<![\w-])id="daemon-line"', tag):
            tag = _set_attr(tag, "dir", "auto")
        return tag + text

    return _TAG_TEXT_RE.sub(one, body)


# Built by the same function the server calls — never re-assembled here —
# so the constant ~100 tests assert against cannot drift away from the
# page users actually get. The variant is named rather than defaulted:
# this is the self-served page, the one the daemon renders once it has
# the script. The fallback page is index_html(sdk_from_self=False).
INDEX_HTML = index_html(sdk_from_self=True)

__all__ = ["INDEX_HTML", "SDK_SRC_SELF", "SDK_SRC_TELEGRAM", "index_html", "page_language"]
