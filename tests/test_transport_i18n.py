"""Multilingual phase 3b: the shared transport texts and the tapper's
language for pop-ups (``transport.tapper_tr``)."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences
from aipager.bot import transport

GROUP, MEMBER = -1001, 2


def _query(user=MEMBER, chat=GROUP, language_code=None):
    query = MagicMock()
    query.from_user = MagicMock(id=user, language_code=language_code)
    query.message = MagicMock(chat_id=chat)
    query.message.chat = MagicMock(id=chat)
    return query


def test_tapper_tr_is_the_tappers_own_language():
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(MEMBER, "ru")
    assert transport.tapper_tr(_query()).code == "ru"


def test_tapper_tr_falls_back_to_the_tapped_chat():
    preferences.set_ui_language(GROUP, "fa")
    assert transport.tapper_tr(_query()).code == "fa"
    # A Persian Telegram app alone does not switch (detection is off).
    assert transport.tapper_tr(_query(language_code="fa")).code == "fa"
    preferences.set_ui_language(GROUP, "")
    assert transport.tapper_tr(_query(language_code="fa")).code == "en"


def test_api_error_english_by_default():
    """Without a tr the friendly message is today's English, for every
    pattern (notify's caller passes none yet)."""
    assert transport._detect_api_error("API Error: 402 credit balance too low") == (
        "Anthropic subscription / credit balance issue. Check your dashboard at "
        "https://console.anthropic.com", None)
    assert transport._detect_api_error("API Error: 429 rate_limit_error retry after 30") == (
        "Rate limit hit. Wait 30s before retrying.", 30)
    assert transport._detect_api_error("API Error: 418") == ("API error occurred.", None)
    assert transport._detect_api_error("all good") is None


def test_api_error_in_the_given_language():
    from aipager import i18n
    msg, retry = transport._detect_api_error("API Error: 429 rate_limit_error retry after 30",
                                             tr=i18n.get("ru"))
    assert (msg, retry) == ("Достигнут лимит запросов. Подожди 30 с перед повтором.", 30)
    msg, _ = transport._detect_api_error("API Error: 529 overloaded", tr=i18n.get("fa"))
    assert msg.startswith("\u200fسرورهای Anthropic")


def test_the_constants_keep_their_english():
    assert transport.NEEDS_ADMIN_REPLY == "That command needs an admin."
    assert transport.RESUME_FULL_NEEDS_ADMIN == "Full access mode needs an admin."
    assert transport.RETRY_OTHERS_COMMAND_REPLY == "Only its sender can retry that command."


def _too_long_then_ok():
    from unittest.mock import AsyncMock

    from telegram.error import BadRequest
    tg = MagicMock()
    tg.send_message = AsyncMock(side_effect=[BadRequest("Message is too long"),
                                             MagicMock(message_id=1)])
    return tg


def test_the_truncation_suffix_follows_the_chat(run_async):
    preferences.set_ui_language(GROUP, "ru")
    tg = _too_long_then_ok()
    run_async(transport._send_with_retry(tg, chat_id=GROUP, text="a" * 5000))
    sent = tg.send_message.await_args.args[1]
    assert sent.endswith("\n\n…[обрезано]") and len(sent) == transport.TELEGRAM_MAX_TEXT_LEN


def test_a_numeric_string_chat_id_gets_the_chats_suffix():
    """Chat ids arrive as strings too (config.CHAT_ID is "-100123")."""
    preferences.set_ui_language(-100123, "ru")
    assert transport._trunc_suffix("-100123") == "\n\n…[обрезано]"
    assert transport._trunc_suffix(-100123) == "\n\n…[обрезано]"


def test_the_truncation_suffix_in_english(run_async):
    tg = _too_long_then_ok()
    run_async(transport._send_with_retry(tg, chat_id=str(GROUP), text="a" * 5000))
    assert tg.send_message.await_args.args[1].endswith(transport._TRUNC_SUFFIX)
    assert transport._trunc_suffix("not a number") == transport._TRUNC_SUFFIX
    assert transport._trunc_suffix(None) == transport._TRUNC_SUFFIX
