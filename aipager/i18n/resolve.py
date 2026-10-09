"""Which language a message uses (section 6 of the multilingual design).

Every resolver returns a :class:`~aipager.i18n.core.Tr` that the caller
passes on explicitly; nothing here is a context variable, so a task
started in one chat can never carry its language into another.

- A chat's own language is its stored ``ui_language`` (an explicit
  choice), else, for a private chat only, the Telegram language aipager
  last saw from that person, else English.
- What only one person sees (a pop-up after a tap, their Mini App) uses
  that person's language: their DM's stored choice, else their Telegram
  language, else the chat's.
- The last-seen Telegram language is kept in memory only (``_seen``) and
  never written: nothing is stored for a chat that did not choose.
"""

from __future__ import annotations

from aipager.i18n import locales
from aipager.i18n.core import Tr, get

__all__ = ["default_code", "for_chat", "for_person", "for_sess", "for_stranger",
           "for_update", "for_viewer"]

#: user id -> the enabled code of the Telegram language last seen in their
#: private chat. Memory only.
_seen: dict[int, str] = {}


def default_code() -> str:
    return "en"


def _int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _stored(chat_id: int | None) -> str | None:
    if chat_id is None:
        return None
    from aipager.preferences import get_ui_language
    code = get_ui_language(chat_id)
    return code if locales.is_selectable(code) else None


def for_chat(chat_id) -> Tr:
    """The language of what everyone in *chat_id* sees."""
    chat_id = _int(chat_id)
    code = _stored(chat_id)
    if code is None and chat_id is not None and chat_id > 0:
        seen = _seen.get(chat_id)
        code = seen if locales.is_selectable(seen) else None
    return get(code or default_code())


def _user(update):
    return (getattr(update, "effective_user", None)
            or getattr(getattr(update, "callback_query", None), "from_user", None))


def _chat_id(update) -> int | None:
    chat = getattr(update, "effective_chat", None)
    cid = _int(getattr(chat, "id", None))
    if cid is None:
        msg = getattr(update, "message", None) or getattr(
            getattr(update, "callback_query", None), "message", None)
        cid = _int(getattr(getattr(msg, "chat", None), "id", None))
    return cid


def _telegram_code(update) -> str | None:
    return locales.normalize(getattr(_user(update), "language_code", None))


def for_update(update) -> Tr:
    """The calling chat's language. In a private chat this also notes the
    person's Telegram language (in memory) for later background
    messages."""
    chat_id = _chat_id(update)
    if chat_id is not None and chat_id > 0:
        uid = _int(getattr(_user(update), "id", None))
        if uid is not None:
            code = _telegram_code(update)
            if code:
                _seen[uid] = code
            else:
                _seen.pop(uid, None)
    return for_chat(chat_id)


def for_person(update) -> Tr:
    """What only the person behind *update* sees: their DM's stored
    choice, else their Telegram language, else the calling chat's."""
    uid = _int(getattr(_user(update), "id", None))
    code = _stored(uid) if uid is not None and uid > 0 else None
    if code is None:
        code = _telegram_code(update)
    if code is None:
        return for_chat(_chat_id(update))
    return get(code)


def for_stranger(update) -> Tr:
    """Someone not on the allow-list: their Telegram language, else
    English."""
    return get(_telegram_code(update) or default_code())


def for_viewer(user_id, language_code) -> Tr:
    """The Mini App: the viewer's DM choice, else the language their
    Telegram app sent in initData, else English."""
    uid = _int(user_id)
    code = _stored(uid) if uid is not None and uid > 0 else None
    return get(code or locales.normalize(language_code) or default_code())


def for_sess(sess) -> Tr:
    """The language of a session's chat (its cards, prompts, answers)."""
    from aipager.bot.transport import resolve_chat_id_int
    return for_chat(resolve_chat_id_int(sess))
