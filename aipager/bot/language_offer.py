"""The upgrade offer card (the multilingual design, section 7.4).

When the other languages ship, every chat that already exists stays
English, and the owner of each private chat whose Telegram language
aipager speaks gets one offer, in that language: "aipager now speaks
Persian ... [فارسی] [Keep English]". Like every surface that offers a
language, it says that only aipager's own text changes, never the Claude
sessions (section 7.0).

Wired by :mod:`aipager.bot.language_launch` (phase 5): once per private
chat that existed before the launch, recorded when it is sent, so it never
comes again whatever the answer. It is the lowest priority send there is
(ORNAMENT, skippable) and is never tried while the chat is muted or backing
off, like the problem report offer.

Callback data: ``_:set:lang_offer:<code>`` and ``_:set:lang_offer:keep``,
in the /settings namespace (a chat-level setting, never a session; the
callback-data budget guard's shape for it), routed by
``callbacks._dispatch_settings_action``.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.i18n import Msg, Tr, get
from aipager.i18n.locales import is_selectable, normalize

log = logging.getLogger(__name__)

OFFER_TEXT = Msg(
    "lang.offer.text",
    "🌐 aipager now speaks {language}. This changes only aipager's own messages and "
    "buttons here and in the Mini App, not your Claude sessions: Claude still answers "
    "in the language you write to it.",
    note="The one-time offer to an existing user; {language} is the language's own name. "
         "Must say it changes only aipager's own text, never the Claude sessions.",
    eg={"language": "فارسی"})
KEEP = Msg("lang.offer.keep", "Keep English", note="Offer button: stay in English")

#: The callback section (``_:set:lang_offer:<token>``).
SECTION = "lang_offer"
KEEP_TOKEN = "keep"


def render_offer(tr: Tr, code: str) -> tuple[str, InlineKeyboardMarkup]:
    """The card's text (HTML) and its two buttons: the language in its own
    name, and Keep English."""
    from aipager.bot.language_menu import endonym
    text = tr.html(OFFER_TEXT, language=endonym(code))
    section = SECTION
    row = []
    for token, label in ((code, endonym(code)), (KEEP_TOKEN, tr.text(KEEP))):
        row.append(InlineKeyboardButton(label, callback_data=f"_:set:{section}:{token}"))
    return text, InlineKeyboardMarkup([row])


async def maybe_offer(bot, chat_id, language_code) -> bool:
    """Send the offer to private chat *chat_id* if it fits: nothing stored
    for that chat, a Telegram language we speak that is not English, and
    the chat not muted or backing off. Whether it went out."""
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id <= 0:
        return False
    from aipager import preferences
    if preferences.get_ui_language(chat_id):
        return False
    code = normalize(language_code)
    if not code or code == "en":
        return False
    from aipager.bot.report_offer import dm_clear
    if not dm_clear(chat_id):
        return False
    from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
    from aipager.bot.transport import MUTED, SKIPPED, send_text
    text, kb = render_offer(get(code), code)
    try:
        sent = await send_text(
            bot._app.bot, chat_id, text, parse_mode="HTML", reply_markup=kb,
            disable_notification=True,
            rate_limit_args=rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT))
    except Exception as e:  # noqa: BLE001 - an offer never breaks its caller
        log.info("language offer not sent (%s)", type(e).__name__)
        return False
    return sent is not None and sent is not MUTED and sent is not SKIPPED


async def handle_callback(bot, update, query, token: str) -> None:
    """``_:set:lang_offer:<code>`` sets the private chat's language to
    *code*; ``..:keep`` to English. The card becomes the confirmation.
    An explicit choice: when the chat's language changed, its ``/`` menu,
    reply keyboard and menu button are sent again, as after a choice in
    ``/language``."""
    from aipager import i18n, preferences
    from aipager.bot.language_menu import INVALID_VALUE, confirmation
    from aipager.bot.transport import calling_chat_id, edit_text
    chat_id = calling_chat_id(update)
    code = "en" if token == KEEP_TOKEN else token
    if not isinstance(chat_id, int) or chat_id <= 0 or not is_selectable(code):
        await bot._safe_answer(query, i18n.for_person(update).text(INVALID_VALUE))
        return
    before = i18n.for_chat(chat_id).code
    preferences.set_ui_language(chat_id, code)
    await bot._safe_answer(query)
    tr = get(code)
    try:
        await edit_text(query, confirmation(tr, code), parse_mode="HTML", reply_markup=None)
    except Exception:
        log.debug("language offer edit failed", exc_info=True)
    if i18n.for_chat(chat_id).code != before:
        await bot._on_language_changed(chat_id)


__all__ = ["KEEP", "OFFER_TEXT", "SECTION", "handle_callback", "maybe_offer",
           "render_offer"]
