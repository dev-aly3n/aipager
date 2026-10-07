"""The Telegram Bot API base URL every request goes to.

``https://api.telegram.org`` unless ``AIPAGER_TELEGRAM_API_BASE`` names
another base, which is meant for a local test server. An override must
be an http(s) URL on this machine (``localhost`` or a loopback address)
unless ``AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1``, so a typo can never send
a bot token somewhere real. An invalid override raises
:class:`TelegramApiBaseError` on every call; it never falls back to
Telegram.

Every Bot API URL in aipager is built here: PTB's ``base_url`` /
``base_file_url``, the rich-message client, the start-up preflight,
observer bots, ``aipager doctor`` and the setup wizard. Stdlib-only.

The URLs embed the bot token. Never log them.
"""

from __future__ import annotations

import ipaddress
import os
import urllib.parse

BASE_ENV = "AIPAGER_TELEGRAM_API_BASE"
ALLOW_REMOTE_ENV = "AIPAGER_TELEGRAM_API_ALLOW_REMOTE"
DEFAULT_BASE = "https://api.telegram.org"


class TelegramApiBaseError(ValueError):
    """``AIPAGER_TELEGRAM_API_BASE`` is invalid or not on this machine.
    ``args`` are the lines to show the user."""

    def lines(self) -> list[str]:
        return [str(a) for a in self.args]

    def __str__(self) -> str:
        return " ".join(self.lines())


def _is_loopback(host: str) -> bool:
    host = host.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validated(base: str) -> str:
    invalid = TelegramApiBaseError(f"{BASE_ENV} is not a valid http(s) URL.")
    try:
        parts = urllib.parse.urlsplit(base)
        host = parts.hostname or ""
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        raise invalid from None
    if (
        parts.scheme not in ("http", "https")
        or not host
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
    ):
        raise invalid
    if not _is_loopback(host) and os.environ.get(ALLOW_REMOTE_ENV) != "1":
        raise TelegramApiBaseError(
            f"{BASE_ENV} points at {host}, which is not this machine.",
            f"Set {ALLOW_REMOTE_ENV}=1 to allow it.",
        )
    return base


def api_base() -> str:
    """The Bot API base without a trailing slash. Raises
    :class:`TelegramApiBaseError` for a bad override."""
    raw = os.environ.get(BASE_ENV, "").strip().rstrip("/")
    if not raw:
        return DEFAULT_BASE
    return _validated(raw)


def method_url(token: str, method: str) -> str:
    """``<base>/bot<token>/<method>``. Embeds the token: never log it."""
    return f"{api_base()}/bot{token}/{method}"


def ptb_base_url() -> str:
    """PTB's ``base_url`` (it appends the token)."""
    return f"{api_base()}/bot"


def ptb_base_file_url() -> str:
    """PTB's ``base_file_url`` (it appends the token and file path)."""
    return f"{api_base()}/file/bot"


def check() -> list[str]:
    """The refusal lines for a bad override; an empty list when the base
    is acceptable (unset, loopback, or allowed remote). Never raises."""
    try:
        api_base()
    except TelegramApiBaseError as e:
        return e.lines()
    return []
