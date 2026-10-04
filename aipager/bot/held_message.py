"""The message a "Which session?" question was asked about (roadmap 8.94i).

In a group, a member with no session of their own to send to is asked
"Which session?" (delivery 11), or "x1 has ended. Which session?" when
theirs has ended (delivery 12a), and nothing is sent. The question keeps
that message here, keyed by the card's (chat, message id), so a tap on
one of its session buttons also sends it there: through the same path a
message routed there would take (the sender's identity and rights, the
reply context, every gate). Only the person who sent it can send it this
way, and only for :data:`MAX_AGE` seconds; after that the tap only sets
their target and they send it again.

Kept in memory only, like the cards' owners: a daemon restart forgets
it, and the tap then only sets the target, as it did before.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from aipager.bot import TelegramBot
    from aipager.state import TrackedSession

#: Ten minutes: after that the message is stale and is not sent.
MAX_AGE = 600.0

#: The newest questions kept; an older one's tap only sets the target.
MAX_RECORDS = 256

#: Too old to send: what the card says instead.
TOO_OLD = "That message is too old to send; send it again."

Resend = Callable[["TrackedSession"], Awaitable[bool]]


@dataclass
class Held:
    user_id: int
    at: float
    resend: Resend
    #: Taken once: a second tap on the same card (a quick double tap)
    #: finds it used and changes nothing.
    used: bool = False


def _store(bot: TelegramBot) -> OrderedDict:
    store = getattr(bot, "_held_messages", None)
    if store is None:
        store = OrderedDict()
        bot._held_messages = store
    return store


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def hold(bot: TelegramBot, chat_id, msg_id, user_id, resend: Resend) -> None:
    """The card on *msg_id* asked *user_id* which session their message
    is for; ``resend(sess)`` sends it to the one they pick and returns
    True when it went there. Nothing is kept without a real message and
    user id."""
    if not (_is_id(msg_id) and _is_id(user_id)):
        return
    store = _store(bot)
    key = (chat_id, msg_id)
    store.pop(key, None)
    store[key] = Held(user_id=user_id, at=time.monotonic(), resend=resend)
    while len(store) > MAX_RECORDS:
        store.popitem(last=False)


def take(bot: TelegramBot, chat_id, msg_id, user_id) -> Held | None:
    """The message held on the card *msg_id* when *user_id* is the person
    who sent it, marked used (a record already used comes back with
    ``used`` set: the caller does nothing more); ``None`` otherwise
    (nothing held there, or someone else's tap: theirs only sets their
    own target). The record stays, used, until the card is cancelled or
    the store's cap pushes it out."""
    store = getattr(bot, "_held_messages", None)
    if not store:
        return None
    held = store.get((chat_id, msg_id))
    if held is None or held.user_id != user_id:
        return None
    if held.used:
        return held
    taken = Held(user_id=held.user_id, at=held.at, resend=held.resend)
    held.used = True
    return taken


def is_fresh(held: Held) -> bool:
    """Young enough to be sent (:data:`MAX_AGE`)."""
    return time.monotonic() - held.at <= MAX_AGE


def drop(bot: TelegramBot, chat_id, msg_id) -> None:
    """The card was cancelled: forget its message."""
    store = getattr(bot, "_held_messages", None)
    if store:
        store.pop((chat_id, msg_id), None)


__all__ = ["MAX_AGE", "MAX_RECORDS", "TOO_OLD", "Held", "drop", "hold", "is_fresh", "take"]
