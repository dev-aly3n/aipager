"""The reaction lifecycle on the user's own Telegram message.

A message the operator sends moves through at most three of these states
(👀 then 🤷 or 👍/👌), each a reaction the bot sets on it:

- 👀 ``HANDED_OFF`` — aipager gave it to the session (typed into the pty,
  or held behind an open dialog / another sender), but Claude has not
  taken it yet. Claude Code's own queued prompt shows grey at this point.
- 👍 ``TAKEN`` — Claude took it: it started a turn, was absorbed into the
  running turn, or was delivered to a running background agent. Claude
  Code's queued prompt turns white here.
- 🤷 ``NOT_DELIVERED`` — it will never be taken: aipager dropped it
  (``/clearqueue``, ``/stop``, ``/kill``, a held message that could not be
  released), the session ended with it still in Claude's queue, or Claude
  Code refused the input outright. Only where that fate is exactly known:
  Escape in the terminal pulls the queue back into the input box, where
  it may be resubmitted, so it gets no 🤷.
- 👌 ``ACK`` — done: a Claude Code command that ran (a command button on
  an idle session at once; one queued behind a turn when that turn ends;
  one sent as a prompt that no hook ever named), or aipager acknowledging
  its own ``/stop``. Like 👍, it is final.

Every emoji here is in Telegram's allowed bot reaction set
(:data:`TELEGRAM_ALLOWED_REACTIONS`, a verbatim copy of the Bot API's
``ReactionTypeEmoji`` list). One that is not makes ``setMessageReaction``
raise, and every caller swallows that — the reaction silently never
appears (roadmap 8.33: ✅ and 🎙️ did exactly this).

Flood rules (8.26 / 0.7.13): every call is SIGNAL class — exempt from the
per-chat budget by endpoint, never suspended by minimal mode, and never
exempt from the flood mute (the outbound gate refuses it; nothing here
retries it after the lift). A reaction is only ever set on a lifecycle
edge, and the ledger below makes each message's history strictly
monotonic: a state is never set twice and never moves backwards, so a
message costs at most three calls. Setting a reaction replaces the bot's
previous one on that message (bots may set one reaction per message), so
a swap is a single call.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from aipager.bot.flood_budget import (
    PRIORITY_SIGNAL,
    rate_limit_args as _rl_args,
)

log = logging.getLogger(__name__)

HANDED_OFF = "👀"
TAKEN = "👍"
NOT_DELIVERED = "🤷"
ACK = "👌"

#: Telegram Bot API ``ReactionTypeEmoji.emoji`` — the only emoji a bot may
#: react with. Copied verbatim (code points included: ❤ and ✍ carry no
#: variation selector) from https://core.telegram.org/bots/api#reactiontypeemoji
#: on 2026-09-24. Pinned by ``tests/test_reaction_allowed_set.py``.
TELEGRAM_ALLOWED_REACTIONS: frozenset[str] = frozenset((
    "❤", "👍", "👎", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢",
    "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
    "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆", "💔", "🤨", "😐",
    "🍓", "🍾", "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻", "👀",
    "🎃", "🙈", "😇", "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅",
    "🤪", "🗿", "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾",
    "🤷‍♂", "🤷", "🤷‍♀", "😡",
))

#: Lifecycle rank. A reaction is set only when it ranks strictly above the
#: one already on the message, so 👀 → 🤷 → 👍 is the longest possible run
#: and 👍 is final: a message Claude took is never marked undelivered by a
#: later teardown. (No path today names a message in a pick-up after its
#: 🤷, but if one ever does, the positive signal is the true one.)
#: ``ACK`` sits with 👍: it marks a message that is not a prompt, and
#: nothing may overwrite it (R6).
_RANK: dict[str, int] = {
    HANDED_OFF: 1,
    NOT_DELIVERED: 2,
    TAKEN: 3,
    ACK: 3,
}

#: Every emoji aipager reacts with, for the allowed-set pin.
AIPAGER_REACTIONS: frozenset[str] = frozenset(_RANK)

#: Messages remembered. A message older than the last few hundred has long
#: finished its lifecycle; forgetting it only means a (never expected)
#: later edge for it is sent instead of deduplicated.
_LEDGER_CAP = 1024

#: Most 🤷 one teardown (``/stop``, ``/clearqueue``, ``/kill``, a session
#: ending) sets in one go. Reactions skip the per-chat pacing, and a
#: teardown can name 50+ held messages; the newest are the ones the
#: operator is looking at. The rest keep their 👀. A 👍 is never capped:
#: every message Claude took is marked.
BULK_CAP = 10


def is_slash_command(text: str | None) -> bool:
    """A Claude Code slash command (sent raw, without the identity
    marker — see ``session_ops._inject_prompt``)."""
    return str(text or "").lstrip().startswith("/")


def _key(chat_id: Any, msg_id: Any) -> tuple[Any, Any]:
    """One key per message whichever way a caller spells the chat: the
    hand-off reads ``update.effective_chat.id`` (an int), a held message's
    later edge ``resolve_chat_id(sess)`` (which may be a string)."""
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        pass
    try:
        msg_id = int(msg_id)
    except (TypeError, ValueError):
        pass
    return chat_id, msg_id


class ReactionLedger:
    """The last reaction set per ``(chat_id, message_id)``, plus a lock per
    message so two edges for the same message reach Telegram in the order
    they were decided (the 👍 for an idle inject can be decided while the
    👀 call is still in flight)."""

    def __init__(self, cap: int = _LEDGER_CAP) -> None:
        self._cap = cap
        self._state: OrderedDict[tuple[Any, Any], str] = OrderedDict()
        self._locks: dict[tuple[Any, Any], asyncio.Lock] = {}
        # Reactions decided but not yet sent: the settle window below.
        self.pending: dict[tuple[Any, Any], asyncio.Task] = {}

    def current(self, chat_id: Any, msg_id: int) -> str | None:
        return self._state.get(_key(chat_id, msg_id))

    def claim(self, chat_id: Any, msg_id: int, emoji: str) -> bool:
        """Record ``emoji`` as the message's reaction if it moves the
        lifecycle forward; return whether a call should be made.

        Recorded BEFORE the call: a call the mute (or anything else)
        refuses is not retried by a later edge for the same state."""
        key = _key(chat_id, msg_id)
        prev = self._state.get(key)
        if prev is not None and _RANK.get(emoji, 0) <= _RANK.get(prev, 0):
            return False
        self._state[key] = emoji
        self._state.move_to_end(key)
        while len(self._state) > self._cap:
            # The oldest message's lock goes with it. Should a coroutine
            # still hold it (a 1024-message-old edge in flight), the next
            # edge for that message just takes a fresh lock: at worst one
            # unordered pair, never a deadlock.
            old, _ = self._state.popitem(last=False)
            self._locks.pop(old, None)
        return True

    def lock(self, chat_id: Any, msg_id: int) -> asyncio.Lock:
        key = _key(chat_id, msg_id)
        lk = self._locks.get(key)
        if lk is None:
            lk = self._locks[key] = asyncio.Lock()
        return lk


def ledger_of(owner: Any) -> ReactionLedger:
    """The bot's ledger, created on first use."""
    ledger = getattr(owner, "_reaction_ledger", None)
    if not isinstance(ledger, ReactionLedger):
        ledger = ReactionLedger()
        owner._reaction_ledger = ledger
    return ledger


# How long 👀 waits before it is sent. An idle prompt gets 👀 at hand-off
# and 👍 a fraction of a second later when Claude takes it; sent at once,
# the user saw 👀 blink into 👍 (2026-09-25). Only 👀 waits: every other
# reaction goes out at once and cancels a 👀 still waiting, so an idle
# prompt shows 👍 straight away (with its busy card) and a queued one
# shows 👀 after the pause, then 👍 the moment Claude picks it up. An
# earlier version delayed every reaction, which put 👍 a second behind
# the busy card (2026-09-26). 0 sends 👀 at once (tests pin the lifecycle
# that way unless they test the pause).
REACTION_SETTLE_SECONDS = 1.0


async def _send(owner: Any, chat_id: Any, msg_id: int, emoji: str) -> None:
    try:
        await owner._app.bot.set_message_reaction(
            chat_id, msg_id, emoji,
            # SIGNAL (8.26 R3): budget-exempt by endpoint, never
            # suspended by minimal mode, never exempt from the mute.
            rate_limit_args=_rl_args(priority=PRIORITY_SIGNAL),
        )
    except Exception:
        log.debug("reaction %s on %s/%s failed", emoji, chat_id, msg_id,
                  exc_info=True)


async def _send_handed_off_later(owner: Any, ledger: "ReactionLedger",
                                 key: tuple, chat_id: Any, msg_id: int) -> None:
    """Wait out the pause, then send 👀 unless the message has moved on
    (a later reaction was sent at once and cancelled this, or claimed a
    higher state)."""
    try:
        await asyncio.sleep(REACTION_SETTLE_SECONDS)
    finally:
        if ledger.pending.get(key) is asyncio.current_task():
            ledger.pending.pop(key, None)
    if ledger.current(chat_id, msg_id) == HANDED_OFF:
        async with ledger.lock(chat_id, msg_id):
            await _send(owner, chat_id, msg_id, HANDED_OFF)


async def set_reaction(owner: Any, chat_id: Any, msg_id: int | None,
                       emoji: str) -> bool:
    """Set ``emoji`` on the user's message if it moves the lifecycle
    forward. Returns whether it will be sent (👀 after a short pause,
    unless a later reaction replaces it first; anything else at once).

    ``owner`` is the ``TelegramBot`` (its ``_app.bot`` sends; the ledger
    lives on it). Never raises: a reaction is best-effort."""
    if not msg_id or chat_id is None:
        # ``/new <prompt>`` and the picker queue their prompt under
        # message id 0: there is no message to react to.
        return False
    if emoji not in TELEGRAM_ALLOWED_REACTIONS or emoji not in _RANK:
        # Telegram would refuse it; the old ✅/🎙️ failed exactly like this,
        # invisibly. Say so instead.
        log.warning("refusing to set reaction %r: not an allowed lifecycle "
                    "emoji", emoji)
        return False
    ledger = ledger_of(owner)
    key = _key(chat_id, msg_id)
    if emoji == HANDED_OFF and REACTION_SETTLE_SECONDS > 0:
        if not ledger.claim(chat_id, msg_id, emoji):
            return False
        if key not in ledger.pending:
            ledger.pending[key] = asyncio.get_running_loop().create_task(
                _send_handed_off_later(owner, ledger, key, chat_id, msg_id))
        return True
    async with ledger.lock(chat_id, msg_id):
        if not ledger.claim(chat_id, msg_id, emoji):
            return False
        waiting = ledger.pending.pop(key, None)
        if waiting is not None:
            waiting.cancel()          # its 👀 is overtaken: never send it
        await _send(owner, chat_id, msg_id, emoji)
        return True


def _msg_order(entry: dict) -> int:
    try:
        return int(entry.get("msg_id"))
    except (TypeError, ValueError):
        return 0


async def mark_all(owner: Any, entries: list[dict], emoji: str,
                   default_chat_id: Any = None, *,
                   cap: int | None = None) -> None:
    """``set_reaction`` for every ``{msg_id, chat_id}`` entry (a note, its
    wire shape, or a queued target); ``chat_id`` falls back to
    ``default_chat_id``. Entries without a message id are skipped. With
    ``cap``, only the newest ``cap`` (by message id — Telegram numbers a
    chat's messages in order) are marked."""
    # An entry without a message id sorts first (see _msg_order), so the
    # cap never spends a slot on it, and set_reaction skips it.
    if cap is not None and len(entries) > cap:
        log.info("reaction %s: marking the newest %d of %d messages", emoji,
                 cap, len(entries))
        entries = sorted(entries, key=_msg_order)[-cap:]
    for entry in entries:
        msg_id = entry.get("msg_id")
        await set_reaction(owner, entry.get("chat_id") or default_chat_id,
                           msg_id, emoji)


def held_entries(pending_queue: list) -> list[dict]:
    """``pending_queue`` tuples ``(text, msg_id, …)`` as ``{msg_id}``
    entries for :func:`mark_all` (a held message's chat is the
    session's)."""
    out = []
    for item in pending_queue:
        try:
            msg_id = item[1]
        except (TypeError, IndexError):
            continue
        if msg_id:
            out.append({"msg_id": msg_id})
    return out
