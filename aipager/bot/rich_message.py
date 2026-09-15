"""Raw HTTPS client for Bot API 10.1 Rich Messages.

python-telegram-bot 22.7 supports Bot API 9.5 and does not expose
sendRichMessage / editMessageText (with rich_message). This module sits
beside PTB and makes those calls directly with httpx, reusing the
configured bot token at call time (never captured at import).

Public API
----------
send_rich_message(chat_id, markdown, *, is_rtl, reply_to_message_id, kind)
edit_message_text_rich(chat_id, message_id, markdown, *, is_rtl,
                       reply_markup, kind)
detect_rtl(text)
close_client()
set_rate_limiter(limiter) / get_rate_limiter()

RichMessageFallbackRequired  -- caller should re-send as plain text
RichMessageBlocked           -- bot is blocked; no fallback
RichMessageGone              -- target message no longer exists
RichMessageFloodBanned       -- flood ban / chat muted; no retry, NO fallback

Flood control (roadmap 8.17, extended by 8.21): every POST here acquires
through the SAME ``BudgetRateLimiter`` instance PTB uses (handed over by
``lifecycle._make_builder`` via :func:`set_rate_limiter`), so the rich
path and the PTB path share one per-chat budget instead of two. A 429
whose ``retry_after`` exceeds ``TELEGRAM_MAX_RETRY_AFTER`` is a ban: it
mutes the chat (``bot/flood.py``) and raises
:class:`RichMessageFloodBanned` after exactly one POST — no clamp, no
sleep, no plain-text fallback, each of which was a fresh violation.

A SMALL 429 no longer sleeps here either (8.21). Both response handlers
report it with ``limiter.note_retry_after`` and let the limiter defer the
whole chat; a blocking caller's request is then re-POSTed exactly once
THROUGH the limiter, whose acquire is what waits out the deferral. A
``kind="skip"`` caller — the busy card, the typing indicator, the pinned
dashboard — is abandoned with :class:`FloodSkipped` instead, because a
card that re-enters the window that just rejected it is one more
violation per tick, which is how the 2026-09-11 ban was earned.

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

from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    FloodSkipped,
    rate_limit_args as _rate_limit_args,
)
from aipager.config import TELEGRAM_MAX_RETRY_AFTER

log = logging.getLogger(__name__)

# Rich-message body size ceiling (UTF-8 bytes) imposed by Telegram.
_RICH_LIMIT: int = 32_768

# Constructed lazily by _get_client(); closed by close_client().
_client: httpx.AsyncClient | None = None

# The daemon's one BudgetRateLimiter (bot/flood_budget.py), set by
# lifecycle._make_builder before polling starts. None only in tests and
# means "unpaced" — never leave it None in a running daemon.
_rate_limiter = None

# Kept defined, and kept patchable, but since 8.21 NOTHING on a 429 path
# calls it: the limiter owns every wait now, so a second, module-private
# sleep here would double the deferral and put the retry back into the
# window it was deferred out of. The `no_sleep` fixture asserts the
# absence. A module attribute so tests shorten THIS and never patch
# asyncio.sleep, which is the global module (see CLAUDE.md).
_sleep = asyncio.sleep

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


class RichMessageFloodBanned(FloodMuted):
    """Raised on a 429 whose ``retry_after`` exceeds ``TELEGRAM_MAX_RETRY_AFTER``
    — Telegram has flood-banned the bot in that chat — and on every later
    send or edit to that chat while the resulting mute holds (before any
    HTTP is made). Deliberately NOT a ``RichMessageFallbackRequired``: a
    plain-text fallback is a fresh violation that extends the ban. Callers
    stop; nothing is retried. ``retry_after`` is the seconds left.
    """


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


def set_rate_limiter(limiter) -> None:
    """Hand over the daemon's ``BudgetRateLimiter`` so every POST from this
    module is paced by the same budget as PTB's own calls (R1)."""
    global _rate_limiter
    _rate_limiter = limiter


def get_rate_limiter():
    """The ONE limiter ``lifecycle._make_builder`` installed, or ``None``.

    Read late, never cached by a caller: the busy-card cadence asks this
    for the chat's backoff on every tick, and caching a second reference
    is exactly how two budgets drift apart (roadmap 8.17). ``None`` means
    "unpaced" — the state every test starts in, and never a running
    daemon's.
    """
    return _rate_limiter


async def _post(method: str, payload: dict, *, kind: str = "blocking",
                priority: str = PRIORITY_ESSENTIAL) -> dict:
    """POST *payload* to *method*, return the parsed response body.

    Acquires the shared rate-limit budget first: ``BudgetRateLimiter``'s
    ``process_request`` keys the per-chat bucket on ``data["chat_id"]``
    and runs the callback inside every bucket that applies, exactly as it
    does for a PTB ``send_message``.

    ``kind="skip"`` marks the call skippable (a busy-card edit): if the
    chat's budget is short it is refused with :class:`FloodSkipped` and no
    HTTP happens at all. Blocking callers — answers, replies — are never
    refused, only deferred.

    Raises httpx exceptions on network / timeout failures; returns the raw
    dict (including ok/error_code/description) on any HTTP-level response.
    """
    client = _get_client()
    # Do NOT log the URL — it contains the bot token.
    log.debug("sendRichMessage family: calling %s", method)

    async def _do() -> dict:
        resp = await client.post(_api_url(method), json=payload)
        return resp.json()

    limiter = _rate_limiter
    if limiter is None:
        return await _do()
    # PTB drops a FALSY rate_limit_args before the limiter ever sees it
    # (_extbot.py:335), so never pass {} — None is the blocking marker.
    try:
        return await limiter.process_request(
            callback=_do, args=(), kwargs={}, endpoint=method, data=payload,
            rate_limit_args=_rate_limit_args(kind=kind, priority=priority),
        )
    except RichMessageFloodBanned:
        # Already the right type — it IS a FloodMuted subclass, so it must
        # be re-raised before the arm below would "translate" it into a
        # second, different instance.
        raise
    except FloodMuted as exc:
        # THE BOUNDARY TRANSLATION (8.26 D-2). The gate inside
        # `process_request` raises a bare `FloodMuted`, which is right for
        # the PTB path. On THIS path a bare one is a live hazard: every
        # caller of `_post` wraps it in a broad
        # `except (RichMessageFallbackRequired, Exception)` arm that would
        # fire a plain-text fallback INTO THE BAN — a fresh violation
        # created by the fix. Translating here keeps `flood_budget`
        # ignorant of the rich path (importing `RichMessageFloodBanned`
        # there would invert the dependency and cycle) while every
        # existing discriminator — `notify.py:2168`, `animation.py:1595` —
        # keeps working unchanged. `from None`: the mute is not an error
        # with a cause worth a chained traceback.
        raise RichMessageFloodBanned(exc.retry_after, exc.chat_id) from None


def _kind_kwargs(kind: str, priority: str = PRIORITY_ESSENTIAL) -> dict:
    """The keyword-only extras ``_post`` needs, and NOTHING when there are
    none.

    ``_post`` is the seam the whole test suite doubles, almost always with
    a two-argument stub, and a BLOCKING ESSENTIAL call — which is every
    call this module made before 8.21 — must keep looking exactly like
    ``_post(method, payload)`` to all of them. Only a skippable or
    non-essential call carries an extra keyword; adding them
    unconditionally would break ~a dozen two-argument doubles at once.
    """
    extra: dict = {}
    if kind != "blocking":
        extra["kind"] = kind
    if priority != PRIORITY_ESSENTIAL:
        extra["priority"] = priority
    return extra


def _raise_if_muted(chat_id) -> None:
    """R3: skip the send outright while the chat is flood-muted."""
    if MUTE.is_muted(chat_id):
        raise RichMessageFloodBanned(MUTE.remaining(chat_id), chat_id)


def _retry_after_of(data: dict) -> int:
    params = data.get("parameters") or {}
    return int(params.get("retry_after", 30))


def _ban_if_excessive(method: str, payload: dict, retry_after: int) -> None:
    """R2: a ``retry_after`` past the cap is a flood ban, not a rate limit.

    Mutes the chat for the whole ``retry_after`` and raises
    :class:`RichMessageFloodBanned`; the caller has made exactly one POST
    and makes no more. Small values return and keep the sleep-and-retry.
    """
    if retry_after > TELEGRAM_MAX_RETRY_AFTER:
        chat_id = payload.get("chat_id")
        MUTE.mute(chat_id, retry_after, source=method)
        raise RichMessageFloodBanned(retry_after, chat_id)


# ── Public API ───────────────────────────────────────────────────────────────

async def send_rich_message(
    chat_id: int,
    markdown: str,
    *,
    is_rtl: bool = False,
    reply_to_message_id: int | None = None,
    kind: str = "blocking",
    priority: str = PRIORITY_ESSENTIAL,
) -> dict | None:
    """POST sendRichMessage and return the result dict, or None on ok-but-empty.

    ``priority`` declares the call's class to the limiter (8.26 R3).
    Defaults to ESSENTIAL — an answer is never dropped — so no existing
    caller changes. Only the busy card passes ``PRIORITY_ORNAMENT``.

    Raises
    ------
    RichMessageBlocked
        HTTP 403 — bot is blocked by the user; caller must not fall back.
    RichMessageFloodBanned
        The chat is flood-muted (raised before any HTTP), or this very
        send got a 429 with ``retry_after`` over the cap and muted it.
        Caller must NOT fall back to plain text.
    RichMessageFallbackRequired
        Any other failure (400, 404, 5xx, timeout, network error, or a 429
        that fails again after one retry) — caller should re-send as plain
        text with no parse_mode.
    FloodSkipped
        ``kind="skip"`` only: the chat's budget was short, so nothing was
        sent. Transient and healthy — the next trigger simply tries again.
    """
    _raise_if_muted(chat_id)
    payload: dict = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown, "is_rtl": is_rtl},
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    return await _send_rich_message_once(payload, allow_retry=True, kind=kind,
                                        priority=priority)


async def _send_rich_message_once(payload: dict, *, allow_retry: bool,
                                  kind: str = "blocking",
                                  priority: str = PRIORITY_ESSENTIAL,
                                  ) -> dict | None:
    """Inner send with optional 429-retry logic."""
    try:
        data = await _post("sendRichMessage", payload,
                           **_kind_kwargs(kind, priority))
    except FloodSkipped:
        # A refused skip is not a failure to fall back from: it means the
        # chat's budget was short and nothing was attempted. Swallowing it
        # into RichMessageFallbackRequired would degrade a card to a
        # plain-text edit — an extra call into the chat that is short.
        raise
    except RichMessageFloodBanned:
        # 8.26 D-2: the gate under `_post` refused this for a muted chat.
        # MUST sit above the broad arm below, which would otherwise
        # re-classify a ban as RichMessageFallbackRequired and send the
        # answer as plain text INTO the ban. Delete this arm and the
        # fallback-into-the-ban trap is back.
        raise
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        log.warning("sendRichMessage network error: %s", type(exc).__name__)
        raise RichMessageFallbackRequired("network error") from exc
    except Exception as exc:
        log.warning("sendRichMessage unexpected error: %s", exc)
        raise RichMessageFallbackRequired("unexpected error") from exc

    return await _handle_response(data, method="sendRichMessage",
                                  payload=payload, allow_retry=allow_retry,
                                  kind=kind, priority=priority)


async def _handle_response(
    data: dict,
    *,
    method: str,
    payload: dict,
    allow_retry: bool,
    kind: str = "blocking",
    priority: str = PRIORITY_ESSENTIAL,
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
        raw_retry_after = _retry_after_of(data)
        # A retry_after past the cap is a ban: mute + raise, first and
        # unchanged (R6). Only a SMALL one reaches the limiter below.
        _ban_if_excessive(method, payload, raw_retry_after)
        # 8.21: no private clamp, no private sleep. The limiter bars the
        # whole chat for exactly the time Telegram asked and doubles its
        # card cadence; the re-POST below waits that out in its OWN
        # acquire, so the wait happens once, in one place.
        limiter = get_rate_limiter()
        if limiter is not None:
            limiter.note_retry_after(payload.get("chat_id"), raw_retry_after)
        if kind == "skip":
            raise FloodSkipped(payload.get("chat_id"), method)
        if allow_retry:
            try:
                data2 = await _post(method, payload,
                                    **_kind_kwargs(kind, priority))
            except RichMessageFloodBanned:
                # 8.26 D-2: the retry met a mute (this very call may have
                # armed it). Above the broad arm, for the same reason as
                # in `_send_rich_message_once`.
                raise
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                log.warning("%s network error on retry: %s", method, type(exc).__name__)
                raise RichMessageFallbackRequired("network error on retry") from exc
            except Exception as exc:
                log.warning("%s unexpected error on retry: %s", method, exc)
                raise RichMessageFallbackRequired("unexpected error on retry") from exc
            # allow_retry=False so a second 429 immediately falls back
            return await _handle_response(data2, method=method,
                                          payload=payload, allow_retry=False,
                                          kind=kind, priority=priority)
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
    kind: str = "blocking",
    priority: str = PRIORITY_ESSENTIAL,
) -> dict | None:
    """POST editMessageText with rich_message (Bot API 10.1).

    ``priority`` declares the call's class to the limiter (8.26 R3). The
    busy card passes ``PRIORITY_ORNAMENT``; everything else is ESSENTIAL
    by default, so a forgotten classification is never dropped.

    Returns the Telegram ``result`` dict on success, or ``None`` on any
    non-fatal failure (transient errors, rate-limits, unchanged content).

    Raises
    ------
    RichMessageBlocked
        HTTP 403 — bot is blocked; caller must stop editing.
    RichMessageGone
        The target message no longer exists (deleted by the user or by
        Telegram); caller must clear ``busy_msg_id`` and stop editing.
    RichMessageFloodBanned
        The chat is flood-muted (raised before any HTTP), or this edit got
        a 429 with ``retry_after`` over the cap and muted it; caller must
        stop editing and must not degrade to a plain-text edit.
    FloodSkipped
        ``kind="skip"`` only: the chat's budget was short, so no edit was
        attempted. Transient — the caller returns False and tries again on
        its next tick, leaving every card stamp untouched.
    """
    _raise_if_muted(chat_id)
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"markdown": markdown, "is_rtl": is_rtl},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        data = await _post("editMessageText", payload,
                           **_kind_kwargs(kind, priority))
    except FloodSkipped:
        # Not a failure: nothing was attempted, and the chat is healthy.
        # Swallowing it into `return None` would make the animator read it
        # as a transient error and log at card cadence.
        raise
    except RichMessageFloodBanned:
        # 8.26 D-2: the gate refused this edit for a muted chat. MUST sit
        # above the broad arm below, which returns None — the animator
        # reads None as "message gone", drops `busy_msg_id` and loses the
        # card, and the ban is silently downgraded to a transient error.
        raise
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        log.warning("editMessageText network error: %s", type(exc).__name__)
        return None
    except Exception as exc:
        log.warning("editMessageText unexpected error: %s", exc)
        return None
    return await _handle_edit_response(data, payload=payload, kind=kind,
                                      priority=priority)


async def _handle_edit_response(data: dict, *, payload: dict,
                                kind: str = "blocking",
                                priority: str = PRIORITY_ESSENTIAL,
                                allow_retry: bool = True) -> dict | None:
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
        raw_retry_after = _retry_after_of(data)
        _ban_if_excessive("editMessageText", payload, raw_retry_after)
        # 8.21: THIS is the busy-card path, i.e. the 2026-09-11 incident
        # itself — a fix that converts only `_handle_response` fixes
        # nothing. Same rule as there: report it, never sleep privately.
        limiter = get_rate_limiter()
        if limiter is not None:
            limiter.note_retry_after(payload.get("chat_id"), raw_retry_after)
        if kind == "skip":
            raise FloodSkipped(payload.get("chat_id"), "editMessageText")
        if not allow_retry:
            log.warning("editMessageText rate-limited again after retry")
            return None
        try:
            data2 = await _post("editMessageText", payload,
                                **_kind_kwargs(kind, priority))
        except FloodSkipped:
            raise
        except RichMessageFloodBanned:
            # 8.26 D-2: above the broad arm, which returns None — the
            # animator reads None as "message gone" and drops the card.
            raise
        except Exception as exc:
            log.warning("editMessageText retry error: %s", exc)
            return None
        # Second 429 → give up quietly (unless it is a ban — then mute)
        if not data2.get("ok") and data2.get("error_code") == 429:
            _ban_if_excessive("editMessageText", payload, _retry_after_of(data2))
            log.warning("editMessageText rate-limited again after retry")
            return None
        return await _handle_edit_response(data2, payload=payload, kind=kind,
                                           priority=priority, allow_retry=False)

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
