"""Pure-function helpers shared across the bot package.

These helpers don't reach into ``TelegramBot`` state — they take all
inputs as arguments and return values. Keeping them in their own
module means the larger ``bot`` mixins can be unit-tested without
constructing a full bot instance.

Sections:

- Telegram-side primitives: ``_send_with_retry``, ``_safe_truncate``,
  ``_md_safe_boundaries``.
- "Bot blocked by user" detection: ``_log_blocked_once``,
  ``_is_bot_blocked``.
- Write/Edit diff rendering: ``_diff_view_enabled``, ``_truncate_diff``,
  ``_build_diff_block``.
- Claude API error pattern matching: ``_ERROR_PATTERNS``,
  ``_RETRY_AFTER_RE``, ``_extract_retry_after``, ``_detect_api_error``.
- Constants: ``TELEGRAM_MAX_*``, ``_TRUNC_SUFFIX``, ``ACTION_VERBS``,
  ``_PERSONAL_MODE_SENTINEL``.

The ``TruncationFailed`` exception type lives here too — it's part of
the send pipeline contract.
"""

from __future__ import annotations

import asyncio
import difflib
import html as html_mod
import logging
import os
import re
import time

from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager.config import TELEGRAM_MAX_RETRY_AFTER
from aipager.team import Role, User as TeamUser

log = logging.getLogger("aipager.bot.transport")


def calling_chat_id(source) -> int | None:
    """Chat id of an inbound Update or CallbackQuery (the calling scope).

    Used to scope label→session lookups so a `/jim` in one scope can't
    resolve another scope's `jim`. Returns None when it can't be
    determined (callers then fall back to label-only matching).
    """
    chat = getattr(getattr(source, "effective_chat", None), "id", None)
    if chat is not None:
        return chat
    msg = getattr(source, "message", None)
    chat = getattr(getattr(msg, "chat", None), "id", None)
    return chat


def resolve_chat_id(sess):
    """Destination chat for a session's outbound notifications (Phase B).

    Returns the session's ``scope_chat_id`` (an int) when set, else the
    global ``config.CHAT_ID`` (a str). The str fallback preserves the
    pre-multi-scope behavior exactly for single-scope installs and for
    sessions not yet stamped (``scope_chat_id == 0``). Read at call time
    so the value tracks runtime config.
    """
    from aipager import config
    return sess.scope_chat_id or config.CHAT_ID


# Sentinel returned by ``_authorize_callback`` in personal mode so
# callers can distinguish "auth passed in personal mode" from "auth
# passed in team mode and here is the TeamUser." Not exported.
_PERSONAL_MODE_SENTINEL = TeamUser(id=0, label="me", role=Role.ADMIN)

# Telegram's documented document upload ceiling is 50 MB; stay below it.
TELEGRAM_MAX_DOC_BYTES = 40 * 1024 * 1024
# Telegram bot API can DOWNLOAD files up to 20 MB via `getFile`. Anything
# above that fails before the file reaches us — better to check `file_size`
# from the update payload and warn the user up front than to attempt and
# fail with a vague "Failed to download file".
TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024
# Single-message text limit Telegram enforces.
TELEGRAM_MAX_TEXT_LEN = 4000
_TRUNC_SUFFIX = "\n\n…[truncated]"

# Throttle for "bot was blocked" log spam — daemon would otherwise emit
# one line per send attempt while the user has the bot blocked.
_LAST_BLOCKED_LOG_TS: float = 0.0
_BLOCKED_LOG_INTERVAL = 60.0


def _log_blocked_once(e: Exception) -> None:
    """Log a friendly explanation when the user has blocked the bot.
    Suppresses subsequent identical events for one minute."""
    global _LAST_BLOCKED_LOG_TS
    now = time.monotonic()
    if now - _LAST_BLOCKED_LOG_TS < _BLOCKED_LOG_INTERVAL:
        return
    _LAST_BLOCKED_LOG_TS = now
    log.error(
        "Telegram refuses to deliver: %s\n"
        "  → The Telegram user has blocked or deleted the bot.\n"
        "  → Open the bot in Telegram and tap Start to unblock, then\n"
        "    new notifications will resume.",
        e,
    )


def _is_bot_blocked(e: Exception) -> bool:
    """Best-effort detection of 'user blocked the bot' across PTB versions."""
    if isinstance(e, Forbidden):
        return True
    msg = str(e).lower()
    return "bot was blocked" in msg or "blocked by the user" in msg


# Item 4.4 — Write/Edit diff preview.
#
# When claude calls a Write or Edit tool, we render the change as a
# unified diff and send it as a Telegram reply threaded under the busy
# message. This gives users on-the-go review without needing to ssh in.
#
# Trade-off: every Write/Edit is one message. The body is capped to
# `_DIFF_MAX_LINES` / `_DIFF_MAX_CHARS` to keep the chat readable. Users
# who find it too noisy can set ``AIPAGER_DIFF_VIEW=0`` to disable.

_DIFF_MAX_LINES = 30
_DIFF_MAX_CHARS = 2000


def _diff_view_enabled() -> bool:
    return os.environ.get("AIPAGER_DIFF_VIEW", "1") not in ("0", "false", "no", "")


def _truncate_diff(lines: list[str]) -> tuple[str, int]:
    """Truncate a list of diff lines to the per-message limits.

    Returns (body_text, dropped_line_count). The body never exceeds
    `_DIFF_MAX_CHARS` and includes a `…[N more lines]` marker when
    truncation happens.
    """
    total = len(lines)
    if total <= _DIFF_MAX_LINES:
        body = "\n".join(lines)
        if len(body) <= _DIFF_MAX_CHARS:
            return body, 0
    keep = lines[:_DIFF_MAX_LINES]
    body = "\n".join(keep)
    if len(body) > _DIFF_MAX_CHARS:
        body = body[:_DIFF_MAX_CHARS]
    dropped = max(0, total - len(keep))
    return body, dropped


def _build_diff_block(
    tool_name: str, tool_input: dict,
) -> tuple[str, str] | None:
    """Return (header, diff_body) for Write or Edit; None if input is malformed.

    For Write: treat as a brand-new file (empty original → all new lines).
    For Edit: unified diff between ``old_string`` and ``new_string``.
    """
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path:
        return None
    if tool_name == "Write":
        new = tool_input.get("content") or ""
        if not new:
            return None
        diff_lines = list(difflib.unified_diff(
            [], new.splitlines(),
            fromfile="/dev/null", tofile=file_path, lineterm="",
        ))
        header = f"📝 <b>Write</b> · <code>{html_mod.escape(file_path)}</code>"
        return header, "\n".join(diff_lines)
    if tool_name == "Edit":
        old = tool_input.get("old_string") or ""
        new = tool_input.get("new_string") or ""
        if not old and not new:
            return None
        diff_lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=file_path, tofile=file_path, lineterm="",
        ))
        header = f"📝 <b>Edit</b> · <code>{html_mod.escape(file_path)}</code>"
        return header, "\n".join(diff_lines)
    return None


class TruncationFailed(Exception):
    """Raised by ``_send_with_retry`` when text remains "too long" after the
    truncation cap. Caller in the IDLE path can catch this and fall back
    to sending the response as a document attachment.
    """


# Maximum number of times ``_send_with_retry`` will truncate-and-resend
# before giving up. HTML escaping can occasionally expand text on
# truncation, so without a cap a pathological input could loop forever.
# Public so callers (notify pipeline) can reference the same limit in
# their fallback messaging.
_MAX_TRUNCATIONS = 2


async def _send_with_retry(bot, *, chat_id, text: str, parse_mode: str | None = None,
                           reply_to_message_id: int | None = None,
                           reply_markup=None, max_retries: int = 2):
    """Send a Telegram message with backoff for RetryAfter and graceful
    handling of "message is too long"."""
    last_err: Exception | None = None
    truncations = 0
    for _attempt in range(max_retries + 1):
        try:
            return await bot.send_message(
                chat_id, text, parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None) or 1
            if wait > TELEGRAM_MAX_RETRY_AFTER:
                # Telegram wants us to back off longer than we're willing to
                # block the daemon for. Log, notify the user via a reaction
                # (reactions are a separate rate-limit bucket from
                # sendMessage, so they still get through), and re-raise so
                # the caller can update state.
                log.warning(
                    "Telegram flood control — retry_after=%ss exceeds "
                    "cap=%ss, giving up on this message",
                    wait, TELEGRAM_MAX_RETRY_AFTER,
                )
                if reply_to_message_id:
                    try:
                        await bot.set_message_reaction(
                            chat_id, reply_to_message_id, "🚨",
                        )
                    except Exception:
                        log.debug("Failed to set flood-control reaction",
                                  exc_info=True)
                raise
            log.warning("Telegram flood control — retrying in %ss", wait)
            await asyncio.sleep(wait)
            last_err = e
            continue
        except BadRequest as e:
            if "too long" in str(e).lower():
                truncations += 1
                if truncations > _MAX_TRUNCATIONS:
                    log.warning(
                        "Telegram still rejects message as too long after %d "
                        "truncation attempts; caller should fall back to a "
                        "document send", _MAX_TRUNCATIONS,
                    )
                    raise TruncationFailed() from e
                # On each retry truncate more aggressively in case Telegram's
                # "too long" was about something other than raw char count
                # (e.g. HTML entity expansion). Halve the budget each time
                # but never below a sensible floor.
                new_limit = max(TELEGRAM_MAX_TEXT_LEN // (2 ** (truncations - 1)), 500)
                text = text[: new_limit - len(_TRUNC_SUFFIX)] + _TRUNC_SUFFIX
                last_err = e
                continue
            raise
        except Forbidden as e:
            _log_blocked_once(e)
            raise
    if last_err:
        raise last_err


def _md_safe_boundaries(md: str) -> list[int]:
    """Find character positions in markdown that are safe to cut at.

    Returns positions of paragraph breaks (\\n\\n) that are NOT inside
    fenced code blocks. Cutting at these positions guarantees both halves
    are valid markdown that can be independently converted to HTML.
    """
    boundaries = []
    in_fence = False
    pos = 0
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        pos += len(line) + 1  # +1 for the \n
        # Check if next char starts a paragraph break and we're outside a fence
        if not in_fence and pos < len(md) and md[pos - 1:pos + 1] == "\n\n":
            boundaries.append(pos)
    return boundaries


def _safe_truncate(text: str, limit: int, is_html: bool) -> str:
    """Truncate text to limit, ensuring HTML tags aren't split mid-tag."""
    if not is_html or len(text) <= limit:
        return text[:limit] + "…"
    # Cut at limit, then back up to avoid splitting an HTML tag
    cut = text[:limit]
    last_lt = cut.rfind("<")
    last_gt = cut.rfind(">")
    if last_lt > last_gt:
        cut = cut[:last_lt]
    # Use a stack to track nesting order, then close in reverse
    stack: list[str] = []
    for m in re.finditer(r"<(/?)(b|i|code|pre|a)\b[^>]*>", cut):
        is_close, tag = m.group(1), m.group(2)
        if is_close:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    # Close remaining open tags in reverse (innermost first)
    for tag in reversed(stack):
        cut += f"</{tag}>"
    return cut + "…"


ACTION_VERBS = {
    "allow": "Allowed",
    "deny": "Denied",
    "continue": "Continued",
    "allow_always": "Allowed always",
}

# ── API error detection ──

# Each entry: (matcher regex, friendly message, kind).
# ``kind`` is used by ``_detect_api_error`` to know whether to also try
# extracting a retry-after hint — currently only rate-limit errors carry
# one in practice.
_ERROR_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(
        r"API Error:\s*402|payment.?required|credit.?balance.?too.?low"
        r"|insufficient.?credit|monthly.?limit|usage.?limit.?reached"
        r"|subscription.?(expired|inactive|required)",
        re.I,
     ),
     "Anthropic subscription / credit balance issue. "
     "Check your dashboard at https://console.anthropic.com",
     "subscription"),
    (re.compile(r"API Error:\s*500|api_error|internal server error", re.I),
     "Anthropic's servers hit an internal error. Usually resolves in seconds.",
     "server"),
    (re.compile(r"API Error:\s*529|overloaded_error|overloaded", re.I),
     "Anthropic's servers are overloaded. Try again in a moment.",
     "overload"),
    # NOTE: do NOT add a bare ``rate.?limit`` alternation — claude often
    # discusses third-party APIs hitting rate limits in its prose (e.g.
    # "Waiting on the NearBlocks rate-limit"), which would trigger this
    # warning falsely. Anchor on tokens that only appear in real
    # Anthropic errors: the structured ``rate_limit_error`` token, a
    # ``429`` / ``HTTP 429`` status, or the verbatim canonical body.
    (re.compile(
        r"API Error:\s*429"
        r"|HTTP 429"
        r"|rate_limit_error"
        r"|This request would exceed your account's rate limit",
        re.I,
     ),
     "Rate limit hit. Wait a moment before retrying.",
     "rate_limit"),
    (re.compile(r"connection.?(error|reset|refused|timeout)|ECONNR|network.?error", re.I),
     "Lost connection to Anthropic. Check network and retry.",
     "network"),
    (re.compile(r"API Error:\s*\d{3}", re.I),
     "API error occurred.",
     "api"),
]

# Capture an explicit retry-after hint from the error body in any of the
# common shapes Anthropic / proxies use. Returns the integer seconds in
# group(1) regardless of which alternation matched.
_RETRY_AFTER_RE = re.compile(
    r"retry[\s-]*after[^\d]{0,20}(\d{1,5})"
    r"|wait[^\d]{0,20}(\d{1,5})\s*sec"
    r"|(\d{1,5})\s*second\s+(?:cool|wait)",
    re.I,
)


def _extract_retry_after(text: str) -> int | None:
    m = _RETRY_AFTER_RE.search(text or "")
    if not m:
        return None
    for grp in m.groups():
        if grp is not None:
            try:
                return int(grp)
            except ValueError:
                continue
    return None


def _detect_api_error(text: str) -> tuple[str, int | None] | None:
    """Check if text contains a known API error pattern.

    Returns ``(friendly_message, retry_after_seconds_or_None)`` if a
    pattern matches; for rate-limit errors we additionally try to pull
    a "retry-after" hint out of the error body and substitute it into
    the message ("Wait 60s before retrying" instead of "Wait a
    moment"). Otherwise returns ``None``.
    """
    if not text:
        return None
    for pattern, friendly_msg, kind in _ERROR_PATTERNS:
        if pattern.search(text):
            retry_after = (
                _extract_retry_after(text) if kind == "rate_limit" else None
            )
            if retry_after is not None:
                friendly_msg = (
                    f"Rate limit hit. Wait {retry_after}s before retrying."
                )
            return friendly_msg, retry_after
    return None
