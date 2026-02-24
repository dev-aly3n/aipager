"""Telegram Bot API client with proxy fallback."""

import json
import logging

import requests

from aipager.config import BOT_TOKEN, CHAT_ID, PROXY, TELEGRAM_API

log = logging.getLogger(__name__)


def _api_url(method: str) -> str:
    return f"{TELEGRAM_API}/{method}"


def _call(method: str, data: dict, timeout: int = 5) -> dict | None:
    """Call Telegram API — try direct first, fallback to proxy."""
    if not BOT_TOKEN:
        log.warning("CLAUDE_TG_BOT_TOKEN not set, skipping")
        return None

    url = _api_url(method)

    # Try direct (use full timeout — needed for long-poll)
    try:
        r = requests.post(url, json=data, timeout=timeout)
        result = r.json()
        if result.get("ok"):
            return result
        # Got a real API response (not a network error) — don't retry via proxy
        log.warning("API error: %s", result.get("description", result))
        return None
    except requests.RequestException as e:
        log.debug("Direct connection failed: %s", e)

    # Fallback to proxy (only on network errors)
    try:
        r = requests.post(url, json=data, timeout=timeout, proxies={
            "https": PROXY,
            "http": PROXY,
        })
        result = r.json()
        if result.get("ok"):
            return result
        log.warning("API error (via proxy): %s", result.get("description", result))
    except requests.RequestException as e:
        log.warning("Proxy connection failed: %s", e)

    return None


def send_message(text: str, reply_markup: dict | None = None,
                 chat_id: str = CHAT_ID) -> int | None:
    """Send a message, optionally with inline keyboard. Returns message_id."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    result = _call("sendMessage", data)
    if result:
        return result["result"]["message_id"]
    return None


def edit_message_reply_markup(message_id: int, reply_markup: dict | None = None,
                              chat_id: str = CHAT_ID) -> bool:
    """Remove or change inline keyboard on an existing message."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    else:
        data["reply_markup"] = {"inline_keyboard": []}

    result = _call("editMessageReplyMarkup", data)
    return result is not None


def edit_message_text(message_id: int, text: str,
                      reply_markup: dict | None = None,
                      chat_id: str = CHAT_ID) -> bool:
    """Edit the text of an existing message."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    result = _call("editMessageText", data)
    return result is not None


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a callback query (removes loading spinner on button)."""
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text

    result = _call("answerCallbackQuery", data)
    return result is not None


def get_updates(offset: int | None = None, timeout: int = 30) -> list[dict]:
    """Long-poll for updates. Returns list of Update objects."""
    data = {
        "timeout": timeout,
        "allowed_updates": ["callback_query", "message"],
    }
    if offset is not None:
        data["offset"] = offset

    result = _call("getUpdates", data, timeout=timeout + 5)
    if result:
        return result.get("result", [])
    return []
