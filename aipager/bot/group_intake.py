"""Which group messages are for the bot, and how to read them (roadmap 8.84, 8.85).

Telegram hands a bot that is a group admin EVERY message in the group,
whatever its privacy setting, and the pinned status bar needs admin
rights. Without a filter of its own, aipager typed every member's chat
line, photo and voice note into the chat's session. :func:`intake_gate`
runs before every other handler and lets a group message through only
when it is addressed to this bot: a command for it, a reply to one of its
messages, a mention of it, or a tap on a keyboard button aipager put
there (its marked label, see :data:`KEYBOARD_MARKER`). Private chats are
untouched.

The same module strips the bot's own mention from what reaches a session
("@aipagerbot fix the tests" is "fix the tests"; ``@word`` is Claude
Code's file-mention syntax), drops ``@<this bot>`` from a ``/<label>``
token (group clients add it to a command picked from the menu), and
words the "send a message" hints for groups, where a plain message does
not reach a privacy-mode bot at all.
"""

from __future__ import annotations

import html as html_mod
import logging
import time
from typing import TYPE_CHECKING

from telegram.constants import ChatType, MessageEntityType
from telegram.ext import ApplicationHandlerStop

from aipager.config import (
    APP_BUTTON,
    BACK_BUTTON,
    COMMANDS_BUTTON,
    MODELS_BUTTON,
    TEMPLATES_BUTTON,
)

if TYPE_CHECKING:
    from telegram import Message, Update
    from telegram.ext import ContextTypes

    from aipager.bot import TelegramBot

log = logging.getLogger(__name__)

_GROUP_TYPES = frozenset({ChatType.GROUP, ChatType.SUPERGROUP})

#: The main keyboard's own words (keyboards._send_keyboard) and its
#: navigation buttons. Template, command and model labels and the chat's
#: session labels are added per message from the same sources.
_KEYBOARD_WORDS = frozenset({
    "status", "stop", "new",
    TEMPLATES_BUTTON, COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON, APP_BUTTON,
})

#: aipager's own slash commands (the ``CommandHandler`` names
#: ``lifecycle.start`` registers; a test keeps the two equal). After the
#: bot's mention is removed, "@bot /stop" reads "/stop", which no command
#: handler saw: the text router must not type it into a session.
OWN_COMMANDS = frozenset({
    "start", "help", "status", "stop", "now", "kill", "new", "resume",
    "clearqueue", "whoami", "mode", "perms", "settings", "app", "update",
    "restart", "rename", "delete", "diff",
})

#: How long a later item of an album is let through after an item of the
#: same album was (Telegram sends an album as one message per item, the
#: caption, and so the mention, on only one of them).
_ALBUM_ADMIT_SECONDS = 60.0

_unknown_identity_logged = False

#: Roadmap 8.91f. In a group every reply-keyboard label aipager sends
#: starts with this marker, and only the marked form counts as a tap: a
#: member typing "stop" or "Clear" is chatting, not pressing a button.
#: Nobody types a white small square and a space in front of a word, and
#: a keyboard button's text comes back exactly as it was sent. Private
#: chats never see it.
KEYBOARD_MARKER = "\u25ab\ufe0f "
#: The same square without its emoji variation selector, accepted too in
#: case a client drops the selector when it sends the button's text back.
_MARKER_BARE = "\u25ab "


# ---- who this bot is ---------------------------------------------------

def own_username(bot: TelegramBot) -> str:
    """This bot's username without the ``@``, or ``""`` when not known yet
    (the app is not initialized, or a test's mock)."""
    try:
        name = bot._app.bot.username
    except Exception:
        return ""
    return name if isinstance(name, str) else ""


def own_id(bot: TelegramBot) -> int | None:
    try:
        bot_id = bot._app.bot.id
    except Exception:
        return None
    if isinstance(bot_id, bool) or not isinstance(bot_id, int):
        return None
    return bot_id


def is_group_chat(chat_id) -> bool:
    """A group or supergroup: Telegram's chat ids for them are negative,
    a private chat's is the person's (positive) user id."""
    try:
        return int(chat_id) < 0
    except (TypeError, ValueError):
        return False


# ---- keyboard labels in groups (roadmap 8.91f) ---------------------------

def mark_label(label: str, chat_id) -> str:
    """*label* as the keyboard shows it in *chat_id*: marked in a group,
    unchanged in a private chat."""
    return KEYBOARD_MARKER + label if is_group_chat(chat_id) else label


def keyboard_tap(text: str) -> tuple[str, bool]:
    """``(label, True)`` when *text* is a marked keyboard label (the
    marker removed), else ``(text, False)`` unchanged."""
    for marker in (KEYBOARD_MARKER, _MARKER_BARE):
        if text.startswith(marker):
            return text[len(marker):], True
    return text, False


# ---- reading a message ---------------------------------------------------

def drop_own_suffix(token: str, username: str) -> str:
    """``x1@thisbot`` -> ``x1``. Only this bot's own name is dropped, in
    any case; another bot's suffix stays (the gate already stopped such a
    command in a group)."""
    if username and token.lower().endswith("@" + username.lower()):
        return token[: -(len(username) + 1)]
    return token


def _own_mention_spans(bot: TelegramBot, entities: dict) -> list[tuple[int, int]]:
    """``(offset, length)`` in UTF-16 code units of every entity that
    mentions this bot: a ``@username`` mention, or a text mention of its
    id. Other people's mentions are never included."""
    me = own_username(bot).lower()
    me_id = own_id(bot)
    spans = []
    for entity, text in entities.items():
        etype = getattr(entity, "type", None)
        if etype == MessageEntityType.MENTION:
            if me and isinstance(text, str) and text.lower() == "@" + me:
                spans.append((entity.offset, entity.length))
        elif etype == MessageEntityType.TEXT_MENTION:
            user = getattr(entity, "user", None)
            if me_id is not None and getattr(user, "id", None) == me_id:
                spans.append((entity.offset, entity.length))
    return spans


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Cut UTF-16 ``spans`` out of ``text`` (Telegram counts entity offsets
    in UTF-16 code units, so an emoji before a mention moves it by two).
    A comma or colon right after the mention ("@bot, fix it") and one space
    after that go with it, so "fix @bot the tests" reads "fix the tests"."""
    raw = text.encode("utf-16-le")
    for offset, length in sorted(spans, reverse=True):
        start, end = offset * 2, (offset + length) * 2
        if raw[end:end + 2] in (b",\x00", b":\x00"):
            end += 2
        if raw[end:end + 2] == b" \x00":
            end += 2
        raw = raw[:start] + raw[end:]
    return raw.decode("utf-16-le")


def text_without_own_mentions(bot: TelegramBot, message: Message | None,
                              *, caption: bool = False) -> tuple[str, bool]:
    """The message's text (or caption) with this bot's own mentions removed,
    and whether one was. Not stripped of whitespace: callers do that."""
    raw = getattr(message, "caption" if caption else "text", None)
    if not isinstance(raw, str):
        return "", False
    try:
        parse = message.parse_caption_entities if caption else message.parse_entities
        entities = parse([MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION])
    except Exception:
        return raw, False
    if not isinstance(entities, dict) or not entities:
        return raw, False
    spans = _own_mention_spans(bot, entities)
    if not spans:
        return raw, False
    return _remove_spans(raw, spans), True


# ---- wording ---------------------------------------------------------------

def talk_hint(bot: TelegramBot, chat_id, label: str, *, dm: str) -> str:
    """The "how do I talk to <label>" line. ``dm`` is returned unchanged in
    a private chat. In a group a plain message never reaches a bot in
    privacy mode, so the line says to reply or mention. ``label`` is used
    as given (callers pass it HTML-escaped where the text is HTML)."""
    if not is_group_chat(chat_id):
        return dm
    return f"Reply to a message from {label} (or mention {_mention(bot)}) to talk to it."


def group_help_talk_line(bot: TelegramBot) -> str:
    """/help's Talk line in a group (HTML)."""
    return (f"<b>Talk:</b> reply to a session's message, or mention {_mention(bot)}"
            " · /&lt;label&gt; message")


def _mention(bot: TelegramBot) -> str:
    me = own_username(bot)
    return f"@{html_mod.escape(me)}" if me else "the bot"


# ---- anonymous senders (roadmap 8.91e) -------------------------------------

#: The one reply a message from an anonymous admin, a channel, or a linked
#: channel's forward gets, once per chat per daemon run.
POST_AS_YOURSELF_TEXT = "Post as yourself to use the bot."


def is_anonymous_sender(msg) -> bool:
    """A message that does not say which person wrote it: it carries a
    ``sender_chat`` (an anonymous group admin, a post as a channel or as the
    group, a linked channel's automatic forward), or its ``from`` is one of
    Telegram's shared sender ids (``team.SHARED_SENDER_IDS``). Only a real
    int chat id counts as a ``sender_chat``."""
    from aipager.team import is_shared_sender_id
    sender_chat_id = getattr(getattr(msg, "sender_chat", None), "id", None)
    if isinstance(sender_chat_id, int) and not isinstance(sender_chat_id, bool):
        return True
    return is_shared_sender_id(getattr(getattr(msg, "from_user", None), "id", None))


def _has_members(bot: TelegramBot) -> bool:
    """Scope or legacy team mode: the bot knows who each person is. In
    personal mode everyone the chat admits is the operator, so an
    anonymous admin there is treated as before (8.91e)."""
    return (getattr(bot, "scopes", None) is not None
            or getattr(bot, "team", None) is not None)


def _is_configured_chat(bot: TelegramBot, chat_id) -> bool:
    """A chat this install serves: a scope's chat, or (legacy team mode)
    the configured ``CHAT_ID``."""
    if getattr(bot, "scopes", None) is not None:
        return bot._scope_for(chat_id) is not None
    from aipager import config
    try:
        return int(config.CHAT_ID) == chat_id
    except (TypeError, ValueError):
        return False


async def _answer_anonymous_sender(bot: TelegramBot, msg) -> None:
    """Tell an anonymous sender, once per chat per daemon run and only in a
    configured chat, to post as themselves. Nothing else happens: no
    pending user is recorded, nothing is routed."""
    chat_id = getattr(msg, "chat_id", None)
    if not _is_configured_chat(bot, chat_id):
        return
    told = getattr(bot, "_anonymous_senders_told", None)
    if told is None:
        told = set()
        bot._anonymous_senders_told = told
    if chat_id in told:
        return
    told.add(chat_id)
    log.info("group intake: an anonymous or channel message in chat %s; "
             "asked to post as yourself (once per chat)", chat_id)
    from aipager.bot.transport import reply_text
    await reply_text(msg, POST_AS_YOURSELF_TEXT)


# ---- the gate --------------------------------------------------------------

def _album_store(bot: TelegramBot) -> dict:
    store = getattr(bot, "_admitted_albums", None)
    if store is None:
        store = {}
        bot._admitted_albums = store
    return store


def _is_prompt_kind(msg) -> bool:
    """The kinds of message aipager's prompt handlers take (text, a file
    or photo with or without a caption, a voice note). A service message
    (member joined, chat migrated, pinned) and everything else pass."""
    return bool(getattr(msg, "text", None) or getattr(msg, "caption", None)
                or getattr(msg, "voice", None) or getattr(msg, "photo", None)
                or getattr(msg, "document", None))


def _log_unknown_identity_once() -> None:
    global _unknown_identity_logged
    if not _unknown_identity_logged:
        _unknown_identity_logged = True
        log.warning("group intake: the bot's own username or id is unknown; in groups "
                    "only commands and replies to a bot are taken until it is")


def _addressed_to_bot(bot: TelegramBot, msg, *, taps: bool = True) -> bool:
    me = own_username(bot)
    me_id = own_id(bot)
    if not me or me_id is None:
        _log_unknown_identity_once()
    body = msg.text if isinstance(getattr(msg, "text", None), str) else None
    if body is None and isinstance(getattr(msg, "caption", None), str):
        body = msg.caption

    # A command for this bot (or for every bot): `/x1`, `/x1@thisbot`.
    if body is not None and body.startswith("/"):
        head = body.split(maxsplit=1)[0] if body.strip() else "/"
        _command, at, target = head.partition("@")
        if not at:
            return True
        if not me:
            return True       # unknown name: a command may be ours
        if target.lower() == me.lower():
            return True
        # Another bot's command: not ours, whatever else it carries.
        return False

    # A reply to one of this bot's messages.
    reply_to = getattr(msg, "reply_to_message", None)
    sender = getattr(reply_to, "from_user", None) if reply_to is not None else None
    if sender is not None:
        if me_id is not None:
            if getattr(sender, "id", None) == me_id:
                return True
        elif getattr(sender, "is_bot", False) is True:
            return True       # unknown id: a reply to a bot may be to us

    # A mention of this bot, in the text or the caption.
    for caption in (False, True):
        _text, found = text_without_own_mentions(bot, msg, caption=caption)
        if found:
            return True

    # A tap on a keyboard button aipager put on this group's keyboards:
    # only the marked form (8.91f). The same word typed is chatter.
    if taps and isinstance(getattr(msg, "text", None), str):
        label, marked = keyboard_tap(msg.text)
        if marked and label in _keyboard_labels(bot, msg.chat_id):
            return True
    return False


def explicitly_addressed(bot: TelegramBot, msg) -> bool:
    """A group message addressed to this bot by a command, a reply to one
    of its messages, or a mention of it: anything the gate admits except a
    keyboard tap. Its words are read as before, including keyboard words
    ("@bot stop" stops)."""
    return _addressed_to_bot(bot, msg, taps=False)


def _keyboard_labels(bot: TelegramBot, chat_id) -> set[str]:
    labels = set(_KEYBOARD_WORDS)
    for attr in ("_template_map", "_command_map", "_model_map"):
        labels.update(getattr(bot, attr, None) or {})
    try:
        labels.update(bot.registry.live_labels(chat_id))
    except Exception:
        log.debug("group intake: live labels unavailable", exc_info=True)
    return labels


async def intake_gate(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    """Runs before every other handler (group -2, see lifecycle.start).

    - An edited message is ignored everywhere: nothing acts on an edit,
      and the message handlers assume ``update.message``.
    - In a group or supergroup, a new message of a kind the prompt
      handlers take passes only when it is addressed to this bot (see
      :func:`_addressed_to_bot`); anything else stops here, with no
      reaction and no reply.
    - Private chats, callback queries and service messages pass.
    - Scope and team mode: an admitted group message with no person behind it (an anonymous
      admin, a channel post: :func:`is_anonymous_sender`) is answered
      "post as yourself" once per chat per run, and stops here.

    Raises ``ApplicationHandlerStop`` to stop an update; never anything
    else (a failure while deciding stops a group message, never lets it
    through)."""
    if getattr(update, "edited_message", None) is not None:
        raise ApplicationHandlerStop
    msg = getattr(update, "message", None)
    if msg is None:
        return
    chat = getattr(msg, "chat", None)
    if getattr(chat, "type", None) not in _GROUP_TYPES:
        return
    try:
        if not _is_prompt_kind(msg):
            return
        albums = _album_store(bot)
        now = time.monotonic()
        for key, seen in list(albums.items()):
            if now - seen > _ALBUM_ADMIT_SECONDS:
                albums.pop(key, None)
        album_key = ((msg.chat_id, msg.media_group_id)
                     if getattr(msg, "media_group_id", None) else None)
        admitted = _addressed_to_bot(bot, msg)
        if admitted and album_key is not None:
            albums[album_key] = now
        admitted = admitted or (album_key is not None and album_key in albums)
        if admitted and _has_members(bot) and is_anonymous_sender(msg):
            # Roadmap 8.91e: an anonymous admin or a channel post has no
            # person to authorize. One "post as yourself", then silence.
            await _answer_anonymous_sender(bot, msg)
            raise ApplicationHandlerStop
        if admitted:
            return
    except ApplicationHandlerStop:
        raise
    except Exception:
        log.warning("group intake: could not judge a group message; ignoring it",
                    exc_info=True)
    raise ApplicationHandlerStop
