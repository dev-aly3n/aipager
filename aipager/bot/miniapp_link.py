"""The Mini App link a chat is handed, carrying that chat's language.

The page reads ``?lang=<code>`` to paint its first frame already in the
viewer's language (``miniapp/server.py``'s index route). Without it the
page starts in English and switches a moment later, every time the
tunnel's address changed (the phone's remembered language is per
address). The parameter is display only: the page's language still comes
from the API once it talks to it.

English links are the bare URL, byte for byte as before.
"""

from __future__ import annotations

__all__ = ["for_chat", "with_lang"]


def with_lang(url: str, code: str | None) -> str:
    """*url* with ``lang=<code>`` added, or *url* unchanged when it is
    empty or *code* is English (or nothing)."""
    if not url or not code or code == "en":
        return url
    from urllib.parse import quote
    base, hash_mark, fragment = url.partition("#")
    sep = "&" if "?" in base else "?"
    return base + sep + "lang=" + quote(code, safe="") + hash_mark + fragment


def for_chat(url: str, chat_id) -> str:
    """*url* for *chat_id*: in the chat's resolved language (a private
    chat's is its person's)."""
    if not url:
        return url
    from aipager import i18n
    try:
        code = i18n.for_chat(int(chat_id)).code
    except (TypeError, ValueError):
        return url
    return with_lang(url, code)
