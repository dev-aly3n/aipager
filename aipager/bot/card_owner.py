"""Confirm cards belong to whoever asked for them (roadmap 8.91c, D-D).

In a group, an inline tap is invisible to the other members, so a confirm
card anyone could press let bob end the session alice was only looking
at, and the group saw alice's card say "Ended". Now the End, Restart and
Delete confirms, the /mode switch confirm, the session pickers of
/kill, /restart, /delete, /mode, /stop, /rename and /diff, the "Resume
x1 as:" card a Resume tap draws (roadmap 8.94j), and the "Which
session?" question (roadmap 8.94i: its buttons send the asker's message)
record who asked for them (who sent the command, or tapped the button
that drew the card) and answer only that person. Anyone else's tap,
Cancel included, gets a toast naming the card's owner and the command to
send for their own, and nothing happens. The `/new` conflict card
already worked this way (callbacks ``new_*``).

Groups only, in scope or team mode: a private chat has one person, and in
personal mode everyone the chat admits is the operator, so nothing is
recorded there and they behave exactly as before. The record is keyed by
the card's (chat, message id), and lasts until the card is used or
cancelled, or another card is drawn on the same message. It lives on the
session registry and is saved with it (roadmap 8.94h), so a daemon
restart does not open a member's card to everyone; a record for a chat
that is no longer a group scope is dropped (:func:`prune`), and so is
every record of a group Telegram moved to a new id (its messages stay in
the old chat).

NOT here, on purpose: the /new Name card (``new_flow``) and the /rename
name question (``session_parity._rename_pending_map``) keep their own
per-person records in memory only. Both expire 10 minutes after they are
asked, so a restart costs at most one question asked again. A Name card
left over from before a restart is answered as expired; a rename
question's Cancel just says "Rename cancelled." (nothing is pending).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from aipager.bot import group_intake
from aipager.state import MAX_CARD_OWNERS

if TYPE_CHECKING:
    from aipager.bot import TelegramBot

#: The newest records kept (in memory and on disk); an older card beyond
#: this is open to anyone in its chat who may use it, as every card was
#: before (a busy group draws far fewer cards than this in a long while).
MAX_RECORDS = MAX_CARD_OWNERS

#: The taps each kind of card answers only for its owner.
_GUARDED = {
    "end": re.compile(r"endok\d+|kill-cancel"),
    "restart": re.compile(r"restartok\d+|restart-cancel"),
    "delete": re.compile(r"delete-confirm|delete-cancel"),
    "mode": re.compile(r"perms_confirm|perms_cancel|perms_stop_switch|perms_wait"),
    # "Which session?" (8.94i): its talk buttons send the asker's message.
    "ask": re.compile(r"talk|resume"),
    # The "Resume x1 as:" card after a Resume tap (8.94j): its mode
    # buttons and Cancel.
    "resume": re.compile(r"resume-ask|resume-auto|resume-cancel"),
    # Only ever pickers (below); here so every kind has a card form.
    "stop": re.compile(r"pstop\d+"),
    "rename": re.compile(r"rename"),
    "diff": re.compile(r"diff"),
}

#: The pickers' row verbs (session_parity.session_picker), and its Cancel.
_PICKER_ROW = {
    "end": re.compile(r"end"),
    "restart": re.compile(r"restart"),
    "delete": re.compile(r"delete"),
    "mode": re.compile(r"mode_show|modeask\d+|modeauto\d+"),
    "ask": re.compile(r"talk|resume"),
    "resume": re.compile(r"resume-ask|resume-auto|resume-cancel"),
    # /stop, /rename and /diff (8.94j): their rows act at once (a stop of
    # the turn shown, the rename question, the diff).
    "stop": re.compile(r"pstop\d+"),
    "rename": re.compile(r"rename"),
    "diff": re.compile(r"diff"),
}
_PICKER_CANCEL = ("_", "pick:cancel")


def _store(bot: TelegramBot) -> dict:
    """The records, on the session registry (saved with it)."""
    return bot.registry.card_owners


def _personal(bot: TelegramBot) -> bool:
    """Personal mode: everyone the chat admits is the operator."""
    return getattr(bot, "scopes", None) is None and getattr(bot, "team", None) is None


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def claim(bot: TelegramBot, chat_id, msg_id, user_id, *, kind: str,
          command: str, picker: bool = False, who=None) -> None:
    """Record that the card on *msg_id* in *chat_id* (a group) is *user_id*'s.
    ``kind``: ``end``, ``restart``, ``delete``, ``mode``, ``ask``
    ("Which session?", roadmap 8.94i), ``resume`` (the "Resume x1 as:"
    card), or the ``stop`` / ``rename`` / ``diff`` pickers (8.94j).
    ``command``: what another member sends for their own (``/kill x2``);
    empty when there is none. ``picker``: the card is the command's
    session picker. ``who``: the requester's Telegram user, for their
    name when they are not a known member. Nothing is recorded in a
    private chat, or without a real message and user id."""
    if not (group_intake.is_group_chat(chat_id) and _is_id(msg_id) and _is_id(user_id)):
        return
    if _personal(bot):
        return      # personal mode: everyone the chat admits is the operator
    if kind not in _GUARDED:
        raise ValueError(f"unknown card kind {kind!r}")
    store = _store(bot)
    key = (chat_id, msg_id)
    store.pop(key, None)
    store[key] = {
        "user_id": user_id,
        "label": bot._actor_label(user_id, chat_id, who),
        "kind": kind,
        "command": command,
        "picker": picker,
    }
    for old in list(store)[:max(0, len(store) - MAX_RECORDS)]:
        del store[old]
    bot.registry.mark_dirty()


def claim_sent(bot: TelegramBot, chat_id, sent, user_id, **kw) -> None:
    """:func:`claim` for a card just sent (``sent`` is what the send
    returned: a Message, or MUTED/SKIPPED/None when nothing went out)."""
    claim(bot, chat_id, getattr(sent, "message_id", None), user_id, **kw)


def release(bot: TelegramBot, chat_id, msg_id) -> None:
    """The card on *msg_id* was used or cancelled: forget its owner."""
    store = _store(bot)
    if store.pop((chat_id, msg_id), None) is not None:
        bot.registry.mark_dirty()


def prune(bot: TelegramBot) -> None:
    """Drop the records of chats whose cards nobody can own any more: a
    chat that is no longer a group scope (removed, or moved to a new id),
    every record in personal mode, and in legacy team mode those outside
    its one chat. Called at start, after the registry loaded, and after
    every reload of the config."""
    store = _store(bot)
    if not store:
        return
    scopes = getattr(bot, "scopes", None)
    if scopes is not None:
        keep = {s.chat_id for s in scopes if s.kind == "group"}
    elif getattr(bot, "team", None) is not None:
        from aipager import config
        try:
            keep = {int(config.CHAT_ID)}
        except (TypeError, ValueError):
            return      # no chat to tell them by: keep them (fail closed)
    else:
        keep = set()
    gone = [key for key in store if key[0] not in keep]
    for key in gone:
        del store[key]
    if gone:
        bot.registry.mark_dirty()


def refusal(bot: TelegramBot, chat_id, msg_id, user_id,
            session_name: str, action: str) -> str | None:
    """The toast for a tap on someone else's card, or ``None`` when the tap
    may go ahead (a private chat, a card with no owner on record, the
    owner's own tap, or a button the card's record does not cover)."""
    if not group_intake.is_group_chat(chat_id) or _personal(bot):
        return None
    record = _store(bot).get((chat_id, msg_id))
    if record is None or record["user_id"] == user_id:
        return None
    if record["picker"]:
        covered = ((session_name, action) == _PICKER_CANCEL
                   or bool(_PICKER_ROW[record["kind"]].fullmatch(action)))
    else:
        covered = bool(_GUARDED[record["kind"]].fullmatch(action))
    if not covered:
        return None
    if not record["command"]:
        return f"This is {record['label']}'s card."
    return (f"This is {record['label']}'s card. "
            f"Send {record['command']} for your own.")


__all__ = ["MAX_RECORDS", "claim", "claim_sent", "prune", "refusal", "release"]
