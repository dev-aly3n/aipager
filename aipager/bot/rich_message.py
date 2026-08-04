"""Raw HTTPS client for Bot API 10.1 Rich Messages.

python-telegram-bot 22.7 supports Bot API 9.5 and does not expose
sendRichMessage / editMessageText (with rich_message). This module sits
beside PTB and makes those calls directly with httpx, reusing the
configured bot token at call time (never captured at import).

Public API
----------
send_rich_message(chat_id, markdown, *, is_rtl, reply_to_message_id)
edit_message_text_rich(chat_id, message_id, markdown, *, is_rtl, reply_markup)
detect_rtl(text)
close_client()

RichMessageFallbackRequired  -- caller should re-send as plain text
RichMessageBlocked           -- bot is blocked; no fallback
RichMessageGone              -- target message no longer exists

sendRichMessageDraft is deliberately absent. It is the only source of
Telegram's native word-by-word animation, but a draft is a 30-second
ephemeral preview and streaming one locks the send button on Telegram
Android for as long as it runs (bugs.telegram.org/c/62189, closed as
intended behaviour, reproduced on hardware 2026-08-04). A turn lasting
minutes would leave the user unable to reply for its whole duration.
See the CHANGELOG entry before reinstating it.
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


class RichMessageGone(Exception):
    """Raised when the message being edited no longer exists (deleted)."""


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


async def edit_message_text_rich(
    chat_id: int,
    message_id: int,
    markdown: str,
    *,
    is_rtl: bool = False,
    reply_markup: dict | None = None,
) -> dict | None:
    """POST editMessageText with rich_message (Bot API 10.1).

    Returns the Telegram ``result`` dict on success, or ``None`` on any
    non-fatal failure (transient errors, rate-limits, unchanged content).

    Raises
    ------
    RichMessageBlocked
        HTTP 403 — bot is blocked; caller must stop editing.
    RichMessageGone
        The target message no longer exists (deleted by the user or by
        Telegram); caller must clear ``busy_msg_id`` and stop editing.
    """
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"markdown": markdown, "is_rtl": is_rtl},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        data = await _post("editMessageText", payload)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        log.warning("editMessageText network error: %s", type(exc).__name__)
        return None
    except Exception as exc:
        log.warning("editMessageText unexpected error: %s", exc)
        return None
    return await _handle_edit_response(data, payload=payload)


async def _handle_edit_response(data: dict, *, payload: dict) -> dict | None:
    """Interpret the Telegram response for an editMessageText rich call.

    Distinct from ``_handle_response`` because 400 ``message is not modified``
    is a benign no-op here — it must NOT disable streaming or trigger a
    plain-text fallback. All other outcomes follow the error table in design §1.
    """
    if data.get("ok"):
        result = data.get("result")
        if isinstance(result, dict):
            return result
        return None

    error_code: int = data.get("error_code", 0)
    description: str = data.get("description", "")
    desc_lower = description.lower()

    if error_code == 403:
        log.warning("editMessageText blocked (403): %s", description)
        raise RichMessageBlocked(description)

    if error_code == 400:
        if "message is not modified" in desc_lower:
            log.debug("editMessageText: message is not modified (benign)")
            return None
        if ("message to edit not found" in desc_lower
                or "message_id_invalid" in desc_lower):
            log.debug("editMessageText: message gone: %s", description)
            raise RichMessageGone(description)
        log.warning("editMessageText bad request (400): %s", description)
        return None

    if error_code == 429:
        params = data.get("parameters") or {}
        retry_after: int = min(int(params.get("retry_after", 30)), 30)
        log.warning("editMessageText rate-limited (429), sleeping %ds then retrying",
                    retry_after)
        await asyncio.sleep(retry_after)
        try:
            data2 = await _post("editMessageText", payload)
        except Exception as exc:
            log.warning("editMessageText retry error: %s", exc)
            return None
        # Second 429 → give up quietly
        if not data2.get("ok") and data2.get("error_code") == 429:
            log.warning("editMessageText rate-limited again after retry")
            return None
        return await _handle_edit_response(data2, payload=payload)

    if error_code == 404 or "method not found" in desc_lower:
        log.warning("editMessageText not found (404): %s", description)
        return None

    if error_code >= 500:
        log.warning("editMessageText server error (%d): %s", error_code, description)
        return None

    # Any other 4xx
    log.warning("editMessageText error (%d): %s", error_code, description)
    return None


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
