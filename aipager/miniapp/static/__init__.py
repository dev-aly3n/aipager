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

from aipager.miniapp.static._app import APP_JS
from aipager.miniapp.static._shell import (
    HTML_BODY,
    HTML_TAIL,
    SDK_SRC_SELF,
    SDK_SRC_TELEGRAM,
    html_head,
)
from aipager.miniapp.static._styles import CSS

# Everything below the head is identical in both variants, so build it
# once and only vary the head.
_PAGE_TAIL = (
    "<style>\n" + CSS + "</style>\n"
    + HTML_BODY
    + "<script>\n" + APP_JS + "\n</script>\n"
    + HTML_TAIL
)


def index_html(*, sdk_from_self: bool = True) -> str:
    """The page, with Telegram's SDK loaded from this origin when the
    daemon has a copy of it (``sdk_from_self``) and straight from
    telegram.org when it does not — the pre-8.18 behaviour, which is the
    floor this feature must never fall below. ``server.py`` decides.
    """
    return html_head(SDK_SRC_SELF if sdk_from_self else SDK_SRC_TELEGRAM) + _PAGE_TAIL


# Built by the same function the server calls — never re-assembled here —
# so the constant ~100 tests assert against cannot drift away from the
# page users actually get. The variant is named rather than defaulted:
# this is the self-served page, the one the daemon renders once it has
# the script. The fallback page is index_html(sdk_from_self=False).
INDEX_HTML = index_html(sdk_from_self=True)

__all__ = ["INDEX_HTML", "SDK_SRC_SELF", "SDK_SRC_TELEGRAM", "index_html"]
