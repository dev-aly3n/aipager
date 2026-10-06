"""Pure builders for the Telegram objects the fake Bot API hands out.

No I/O and no state: ``server.FakeBotApi`` numbers the messages and
updates and keeps them. The shapes follow the Bot API closely enough
for python-telegram-bot to parse them and for aipager's group gate to
read them as real Telegram would send them (entities in UTF-16 units,
``bot_command`` at offset 0, a reply carrying the full replied message).
"""

from __future__ import annotations

import re
import time

#: Telegram's "GroupAnonymousBot": the ``from`` of a message an
#: anonymous group admin posts.
GROUP_ANONYMOUS_BOT_ID = 1087968824

_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{3,32})")


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def user(user_id: int, username: str | None = None, first_name: str | None = None,
         *, is_bot: bool = False) -> dict:
    out = {"id": user_id, "is_bot": is_bot,
           "first_name": first_name or (username or f"user{user_id}").capitalize()}
    if username:
        out["username"] = username
    return out


def chat(chat_id: int, chat_type: str, title: str | None = None,
         who: dict | None = None) -> dict:
    out: dict = {"id": chat_id, "type": chat_type}
    if chat_type == "private":
        if who:
            out["first_name"] = who.get("first_name", "")
            if who.get("username"):
                out["username"] = who["username"]
    else:
        out["title"] = title or f"chat {chat_id}"
    return out


def command_entities(text: str) -> list[dict]:
    """A ``bot_command`` entity for a leading ``/command[@bot]`` token."""
    if not text.startswith("/"):
        return []
    token = text.split(maxsplit=1)[0] if text.strip() else text
    token = token.split("\n", 1)[0]
    return [{"type": "bot_command", "offset": 0, "length": utf16_len(token)}]


def mention_entities(text: str) -> list[dict]:
    """A ``mention`` entity for every ``@name``, offsets in UTF-16 units.

    An ``@name`` inside a leading ``/cmd@name`` token is part of the
    command, not a mention."""
    skip_until = 0
    if text.startswith("/"):
        first = text.split(maxsplit=1)[0] if text.strip() else text
        skip_until = len(first)
    out = []
    for m in _MENTION_RE.finditer(text):
        if m.start() < skip_until:
            continue
        out.append({
            "type": "mention",
            "offset": utf16_len(text[:m.start()]),
            "length": utf16_len(m.group(0)),
        })
    return out


def auto_entities(text: str) -> list[dict]:
    return sorted(command_entities(text) + mention_entities(text),
                  key=lambda e: e["offset"])


def text_message(message_id: int, chat_obj: dict, sender: dict, text: str, *,
                 reply_to: dict | None = None, thread_id: int | None = None,
                 entities: list[dict] | None = None, date: int | None = None) -> dict:
    msg: dict = {
        "message_id": message_id,
        "date": int(date if date is not None else time.time()),
        "chat": dict(chat_obj),
        "from": dict(sender),
        "text": text,
    }
    ents = auto_entities(text) if entities is None else entities
    if ents:
        msg["entities"] = ents
    if reply_to is not None:
        msg["reply_to_message"] = strip_nested(reply_to)
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return msg


def strip_nested(msg: dict) -> dict:
    """A message as it appears inside another (no nested reply)."""
    out = dict(msg)
    out.pop("reply_to_message", None)
    return out


def edited(msg: dict, text: str, *, edit_date: int | None = None) -> dict:
    out = dict(msg)
    out["text"] = text
    ents = auto_entities(text)
    if ents:
        out["entities"] = ents
    else:
        out.pop("entities", None)
    out["edit_date"] = int(edit_date if edit_date is not None else time.time())
    return out


def callback_query(callback_id: str, sender: dict, message: dict, data: str) -> dict:
    return {
        "id": callback_id,
        "from": dict(sender),
        "chat_instance": f"ci{message['chat']['id']}",
        "data": data,
        "message": dict(message),
    }


def document_message(message_id: int, chat_obj: dict, sender: dict, *, file_id: str,
                     file_unique_id: str, filename: str, size: int,
                     caption: str | None = None) -> dict:
    msg: dict = {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": dict(chat_obj),
        "from": dict(sender),
        "document": {"file_id": file_id, "file_unique_id": file_unique_id,
                     "file_name": filename, "file_size": size},
    }
    if caption:
        msg["caption"] = caption
        ents = auto_entities(caption)
        if ents:
            msg["caption_entities"] = ents
    return msg


def voice_message(message_id: int, chat_obj: dict, sender: dict, *, file_id: str,
                  file_unique_id: str, size: int, duration: int = 1) -> dict:
    return {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": dict(chat_obj),
        "from": dict(sender),
        "voice": {"file_id": file_id, "file_unique_id": file_unique_id,
                  "duration": duration, "mime_type": "audio/ogg", "file_size": size},
    }


def migration_pair(old_chat: dict, new_chat: dict, sender: dict, *,
                   old_message_id: int, new_message_id: int) -> tuple[dict, dict]:
    """The two service messages Telegram sends when it upgrades a basic
    group to a supergroup: one in the old group (``migrate_to_chat_id``)
    and one in the new supergroup (``migrate_from_chat_id``)."""
    now = int(time.time())
    old_msg = {"message_id": old_message_id, "date": now, "chat": dict(old_chat),
               "from": dict(sender), "migrate_to_chat_id": new_chat["id"]}
    new_msg = {"message_id": new_message_id, "date": now, "chat": dict(new_chat),
               "from": dict(sender), "migrate_from_chat_id": old_chat["id"]}
    return old_msg, new_msg


def sender_chat_message(message_id: int, chat_obj: dict, text: str,
                        sender_chat: dict) -> dict:
    """A message an anonymous admin (or a linked channel) posts: ``from``
    is GroupAnonymousBot and ``sender_chat`` names the chat."""
    msg = text_message(
        message_id, chat_obj,
        user(GROUP_ANONYMOUS_BOT_ID, "GroupAnonymousBot", "Group", is_bot=True),
        text,
    )
    msg["sender_chat"] = dict(sender_chat)
    return msg
