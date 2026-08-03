"""Raw HTTPS client for Bot API 10.1 Rich Messages.

python-telegram-bot 22.7 supports Bot API 9.5 and does not expose
sendRichMessage / sendRichMessageDraft. This module sits beside PTB and
makes those two calls directly with httpx, reusing the configured bot
token at call time (never captured at import).

Public API
----------
send_rich_message(chat_id, markdown, *, is_rtl, reply_to_message_id)
send_rich_message_draft(chat_id, draft_id, markdown, *, is_rtl)
detect_rtl(text)
close_client()

RichMessageFallbackRequired  -- caller should re-send as plain text
RichMessageBlocked           -- bot is blocked; no fallback
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

log = logging.getLogger(__name__)

# Rich-message body size ceiling (UTF-8 bytes) imposed by Telegram.
_RICH_LIMIT: int = 32_768

# Constructed lazily by _get_client(); closed by close_client().
_client: httpx.AsyncClient | None = None

# RTL / LTR letter ranges (Unicode script blocks).
_RTL_RE = re.compile(
    r"[֐-׿؀-ۿݐ-ݿ"
    r"ࢠ-ࣿיִ-﷿ﹰ-﻿]"
)
_LTR_RE = re.compile(r"[A-Za-z]")


# ── Exception types ─────────────────────────────────────────────────────────

class RichMessageFallbackRequired(Exception):
    """Raised when sendRichMessage fails and a plain-text retry is safe."""


class RichMessageBlocked(Exception):
    """Raised on HTTP 403; caller must NOT attempt a plain-text fallback."""


# ── HTTP client ──────────────────────────────────────────────────────────────

def _get_client() -> httpx.AsyncClient:
    global _client
    # Suppress httpx's INFO-level "HTTP Request: POST …/botTOKEN/…" lines,
    # which would otherwise print the bot token in plaintext on every call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_connections=4),
        )
    return _client


async def close_client() -> None:
    """Close the shared httpx client. Idempotent."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _api_url(method: str) -> str:
    """Build the Bot API URL for *method*.

    NEVER log the return value — it embeds the bot token.
    """
    from aipager.config import BOT_TOKEN
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


async def _post(method: str, payload: dict) -> dict:
    """POST *payload* to *method*, return the parsed response body.

    Raises httpx exceptions on network / timeout failures; returns the raw
    dict (including ok/error_code/description) on any HTTP-level response.
    """
    client = _get_client()
    # Do NOT log the URL — it contains the bot token.
    log.debug("sendRichMessage family: calling %s", method)
    resp = await client.post(_api_url(method), json=payload)
    return resp.json()


# ── Public API ───────────────────────────────────────────────────────────────

async def send_rich_message(
    chat_id: int,
    markdown: str,
    *,
    is_rtl: bool = False,
    reply_to_message_id: int | None = None,
) -> dict | None:
    """POST sendRichMessage and return the result dict, or None on ok-but-empty.

    Raises
    ------
    RichMessageBlocked
        HTTP 403 — bot is blocked by the user; caller must not fall back.
    RichMessageFallbackRequired
        Any other failure (400, 404, 5xx, timeout, network error, or a 429
        that fails again after one retry) — caller should re-send as plain
        text with no parse_mode.
    """
    payload: dict = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown, "is_rtl": is_rtl},
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    return await _send_rich_message_once(payload, allow_retry=True)


async def _send_rich_message_once(payload: dict, *, allow_retry: bool) -> dict | None:
    """Inner send with optional 429-retry logic."""
    try:
        data = await _post("sendRichMessage", payload)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        log.warning("sendRichMessage network error: %s", type(exc).__name__)
        raise RichMessageFallbackRequired("network error") from exc
    except Exception as exc:
        log.warning("sendRichMessage unexpected error: %s", exc)
        raise RichMessageFallbackRequired("unexpected error") from exc

    return await _handle_response(data, method="sendRichMessage",
                                  payload=payload, allow_retry=allow_retry)


async def _handle_response(
    data: dict,
    *,
    method: str,
    payload: dict,
    allow_retry: bool,
) -> dict | None:
    """Interpret the Telegram response dict and raise/return appropriately."""
    if data.get("ok"):
        result = data.get("result")
        if isinstance(result, dict):
            return result
        # ok=true but result is missing or wrong type
        log.warning("%s returned ok=true but result=%r — treating as sent", method, result)
        return None

    error_code: int = data.get("error_code", 0)
    description: str = data.get("description", "")

    if error_code == 403:
        log.warning("%s blocked (403): %s", method, description)
        raise RichMessageBlocked(description)

    if error_code == 429:
        params = data.get("parameters") or {}
        retry_after: int = min(int(params.get("retry_after", 30)), 30)
        if allow_retry:
            log.warning("%s rate-limited (429), sleeping %ds then retrying",
                        method, retry_after)
            await asyncio.sleep(retry_after)
            try:
                data2 = await _post(method, payload)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                log.warning("%s network error on retry: %s", method, type(exc).__name__)
                raise RichMessageFallbackRequired("network error on retry") from exc
            except Exception as exc:
                log.warning("%s unexpected error on retry: %s", method, exc)
                raise RichMessageFallbackRequired("unexpected error on retry") from exc
            # allow_retry=False so a second 429 immediately falls back
            return await _handle_response(data2, method=method,
                                          payload=payload, allow_retry=False)
        # Second 429 → fall back
        log.warning("%s rate-limited again after retry", method)
        raise RichMessageFallbackRequired(f"429 after retry: {description}")

    if error_code == 404 or "method not found" in description.lower():
        log.warning("%s not found / method not found (404) — Telegram may have "
                    "rolled back Rich Messages: %s", method, description)
        raise RichMessageFallbackRequired(f"404: {description}")

    if error_code >= 500:
        log.warning("%s server error (%d): %s", method, error_code, description)
        raise RichMessageFallbackRequired(f"{error_code}: {description}")

    # 400 or any unrecognised 4xx
    log.warning("%s bad request (%d): %s", method, error_code, description)
    raise RichMessageFallbackRequired(f"{error_code}: {description}")


async def send_rich_message_draft(
    chat_id: int,
    draft_id: int,
    markdown: str,
    *,
    is_rtl: bool = False,
) -> bool:
    """POST sendRichMessageDraft. Returns True on ok, False on every failure.

    Never raises — a draft is cosmetic and must not affect the turn.
    On 403, logs at WARNING once; all other failures log at DEBUG.
    """
    payload = {
        "chat_id": chat_id,
        "draft_id": draft_id,
        "rich_message": {"markdown": markdown, "is_rtl": is_rtl},
    }
    try:
        data = await _post("sendRichMessageDraft", payload)
    except Exception as exc:
        log.debug("sendRichMessageDraft failed: %s", exc)
        return False

    if data.get("ok"):
        return True

    error_code = data.get("error_code", 0)
    description = data.get("description", "")
    if error_code == 403:
        log.warning("sendRichMessageDraft blocked (403): %s", description)
    else:
        log.debug("sendRichMessageDraft error %d: %s", error_code, description)
    return False


# ── RTL detection ────────────────────────────────────────────────────────────

def detect_rtl(text: str) -> bool:
    """Return True when *text* is predominantly RTL (e.g. Persian, Arabic).

    Samples the first 2000 characters. Compares RTL-script letter count
    to Latin letter count — a ratio against total length under-counts RTL
    when the text is dense with spaces, digits, punctuation and markdown.
    Empty string → False.
    """
    if not text:
        return False
    sample = text[:2000]
    rtl = len(_RTL_RE.findall(sample))
    ltr = len(_LTR_RE.findall(sample))
    return rtl > 0 and rtl > ltr
