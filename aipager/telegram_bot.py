"""Backwards-compat shim — the real code now lives under :mod:`aipager.bot`.

Existing imports of the form ``from aipager import telegram_bot as tb``
and ``tb.<name>`` keep working because we re-export everything the bot
package exposes (the façade class plus the pure helpers from
``aipager.bot.transport``). New code should import from the canonical
paths directly:

    from aipager.bot import TelegramBot
    from aipager.bot.transport import _send_with_retry, ACTION_VERBS
    from aipager.dtach import inject

This file will be removed once test imports have been migrated; new
code should not be added here.
"""

from aipager.bot import TelegramBot  # noqa: F401
from aipager.bot.transport import (  # noqa: F401
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _diff_view_enabled,
    _ERROR_PATTERNS,
    _extract_retry_after,
    _is_bot_blocked,
    _LAST_BLOCKED_LOG_TS,
    _log_blocked_once,
    _MAX_TRUNCATIONS,
    _md_safe_boundaries,
    _PERSONAL_MODE_SENTINEL,
    _RETRY_AFTER_RE,
    _safe_truncate,
    _send_with_retry,
    _TRUNC_SUFFIX,
    _truncate_diff,
)
from aipager.dtach import inject  # noqa: F401
