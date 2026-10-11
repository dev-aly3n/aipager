"""Which language a message uses (section 6 of the multilingual design).

Every resolver returns a :class:`~aipager.i18n.core.Tr` that the caller
passes on explicitly; nothing here is a context variable, so a task
started in one chat can never carry its language into another.

The one rule (phase 5):

- A chat's own language is its stored ``ui_language`` (an explicit
  choice). Else, for a private chat whose person follows Telegram, the
  Telegram language aipager last saw from that person. Else English. A
  group is its stored choice, else English.
- A person follows Telegram, while ``locales.AUTO_DETECT`` is on, when
  they chose "Automatic" (stored ``"auto"``) or are new since the launch
  marker (:func:`aipager.i18n.persist.is_existing_person`). With it off
  (the kill switch) nobody does. A person who existed before languages
  were switched on keeps English until they choose; an English app reads
  English for everyone.
- What only one person sees (a pop-up after a tap, their Mini App) uses
  that person's language: their DM's stored choice, else their Telegram
  language when they follow it, else the chat's (the Mini App: English).
- Someone not on the allow-list gets their DM's stored choice, else their
  Telegram language, else English.

The last-seen Telegram language is persisted (``persist.seen``), so a
restart does not flip a chat that follows Telegram back to English. It is
never a choice: nothing is stored in preferences for a chat that did not
choose.
"""

from __future__ import annotations

from aipager.i18n import locales, persist
from aipager.i18n.core import Tr, get

__all__ = ["automatic_code", "default_code", "follows", "for_chat", "for_person", "for_sess",
           "for_stranger", "for_update", "for_viewer", "note_language"]

_AUTO = "auto"


def default_code() -> str:
    return "en"


def _int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _raw(chat_id: int | None) -> str:
    if chat_id is None:
        return ""
    from aipager.preferences import get_ui_language
    return get_ui_language(chat_id)


def _stored(chat_id: int | None) -> str | None:
    code = _raw(chat_id)
    return code if locales.is_selectable(code) else None


def follows(user_id) -> bool:
    """Whether *user_id* (a person, i.e. their private chat) follows their
    Telegram language when they have not picked one."""
    uid = _int(user_id)
    if uid is None or uid <= 0 or not locales.AUTO_DETECT:
        # The kill switch: nobody follows (strangers are not "people").
        return False
    if _raw(uid) == _AUTO:
        return True
    return not persist.is_existing_person(uid)


def for_chat(chat_id) -> Tr:
    """The language of what everyone in *chat_id* sees."""
    chat_id = _int(chat_id)
    code = _stored(chat_id)
    if code is None and chat_id is not None and chat_id > 0 and follows(chat_id):
        seen = persist.seen(chat_id)
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


def _language_code(user) -> str | None:
    value = getattr(user, "language_code", None)
    return value if isinstance(value, str) and value.strip() else None


def note_language(update) -> bool:
    """Record the Telegram language of the person behind *update* when it
    comes from their private chat: a language aipager has is remembered,
    one it does not have (ja, zh-hant) forgets the last one, and an update
    without a language changes nothing. Returns whether it changed."""
    chat_id = _chat_id(update)
    if chat_id is None or chat_id <= 0:
        return False
    user = _user(update)
    uid = _int(getattr(user, "id", None))
    language_code = _language_code(user)
    if uid is None or language_code is None:
        return False
    return persist.set_seen(uid, locales.normalize(language_code))


def for_update(update) -> Tr:
    """The calling chat's language. In a private chat this also notes the
    person's Telegram language for later background messages."""
    note_language(update)
    return for_chat(_chat_id(update))


def for_person(update) -> Tr:
    """What only the person behind *update* sees: their DM's stored
    choice, else their Telegram language when they follow it, else the
    calling chat's."""
    user = _user(update)
    uid = _int(getattr(user, "id", None))
    code = _stored(uid) if uid is not None and uid > 0 else None
    if code is None:
        code = locales.detect(getattr(user, "language_code", None), follow=follows(uid))
    if code is None:
        return for_chat(_chat_id(update))
    return get(code)


def for_stranger(user_or_update) -> Tr:
    """Someone not on the allow-list (a Telegram user, or an update from
    them): their DM's stored choice, else their Telegram language, else
    English. Strangers always get their own language: they have no chat
    with aipager that could already be English."""
    obj = user_or_update
    user = obj if _int(getattr(obj, "id", None)) is not None else _user(obj)
    uid = _int(getattr(user, "id", None))
    code = _stored(uid) if uid is not None and uid > 0 else None
    if code is None:
        code = locales.normalize(getattr(user, "language_code", None))
    return get(code or default_code())


def for_viewer(user_id, language_code) -> Tr:
    """The Mini App: the viewer's DM choice, else the language their
    Telegram app sent in initData when they follow it, else English."""
    uid = _int(user_id)
    code = _stored(uid) if uid is not None and uid > 0 else None
    if code is None:
        code = locales.detect(language_code, follow=follows(uid))
    return get(code or default_code())


def automatic_code(user_id, language_code=None) -> str:
    """The code "Automatic" would give *user_id*: their Telegram language
    (*language_code* when the caller has it, else the last one seen), or
    English."""
    if isinstance(language_code, str) and language_code.strip():
        return locales.normalize(language_code) or default_code()
    seen = persist.seen(user_id)
    return seen if locales.is_selectable(seen) else default_code()


def for_sess(sess) -> Tr:
    """The language of a session's chat (its cards, prompts, answers)."""
    from aipager.bot.transport import resolve_chat_id_int
    return for_chat(resolve_chat_id_int(sess))
