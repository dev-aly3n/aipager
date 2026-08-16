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
from aipager.miniapp.static._shell import HTML_BODY, HTML_HEAD, HTML_TAIL
from aipager.miniapp.static._styles import CSS

INDEX_HTML = (
    HTML_HEAD
    + "<style>\n" + CSS + "</style>\n"
    + HTML_BODY
    + "<script>\n" + APP_JS + "\n</script>\n"
    + HTML_TAIL
)

__all__ = ["INDEX_HTML"]
