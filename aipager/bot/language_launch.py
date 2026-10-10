"""Switching languages on for an install (multilingual phase 5, design 7.4).

- :func:`ensure_marker`: at the first start that runs this code, record
  who already exists (every person aipager knows: DM scopes, scope
  members, the legacy ``CHAT_ID`` and the chats in preferences.json), so
  they stay English until they choose. A fresh install records nobody.
- :func:`after_update` (a group-1 handler: after the reply, never for an
  update the intake gate stopped): in a private chat this install serves,
  note the person's Telegram language and, in a background task, send at
  most one of
  - the first-contact line (7.1), as its own message, once ever, to a chat
    new since the marker that follows a non-English Telegram language;
  - the offer card (7.4), once ever, to a chat that existed before and
    whose person's Telegram language aipager has and is not English;
  then refresh the chat's ``/`` menu and menu button when its language
  moved without a choice (no keyboard message).
- :func:`offer_sweep`: at start, the same offer to existing private chats
  whose Telegram language an earlier run saw. It takes the same per-chat
  in-flight guard as the step after an update, so the two never both send.

Every send is the lowest priority there is (ORNAMENT, skippable), silent,
never tried while the chat is muted or backing off, never in a group. For
:data:`START_GRACE` seconds after start (:func:`note_started`) a step
waits before it sends: the start-up messages use the chat's budget then,
and a skippable send would only be dropped.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aipager.i18n import persist

log = logging.getLogger(__name__)

__all__ = ["after_update", "cancel_tasks", "drain", "ensure_marker", "known_people",
           "note_started", "offer_sweep"]

#: How long the start-time offer sweep waits, so the startup notice and
#: keyboard go first. Slept through :data:`_sleep` (tests replace that
#: seam, never ``asyncio.sleep``).
SWEEP_DELAY = 30.0
#: How long after start a step waits before its (skippable) send, so it is
#: not dropped while the start-up messages use the chat's budget.
START_GRACE = 10.0
_sleep = asyncio.sleep
#: Seam: the clock the grace is measured on.
_clock = time.monotonic


def _positive(value) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def known_people(bot) -> set[int]:
    """Every person this install knows now: each private scope chat (a
    DM's id is its person's), each scope member, a positive ``CHAT_ID``,
    and every private chat with an entry in preferences.json."""
    people: set[int] = set()
    for scope in getattr(bot, "scopes", None) or ():
        cid = _positive(getattr(scope, "chat_id", None))
        if cid is not None:
            people.add(cid)
        for member in getattr(scope, "members", ()) or ():
            mid = _positive(getattr(member, "id", None))
            if mid is not None:
                people.add(mid)
    from aipager import config
    from aipager.bot import lifecycle
    # The legacy single chat, as config has it and as the bot read it.
    for value in (getattr(config, "CHAT_ID", None), getattr(lifecycle, "CHAT_ID", None)):
        cid = _positive(value)
        if cid is not None:
            people.add(cid)
    try:
        from aipager import preferences
        for key in preferences._ensure_loaded():
            pid = _positive(key) if isinstance(key, str) and key.isdigit() else None
            if pid is not None:
                people.add(pid)
    except Exception:  # noqa: BLE001 - a broken preferences file adds nobody
        log.debug("could not read the chats in preferences", exc_info=True)
    return people


def ensure_marker(bot) -> dict | None:
    """Write the launch marker unless one exists. Never raises: without a
    marker everyone counts as existing (English)."""
    try:
        if persist.warn_if_damaged():
            # Logged at every start while it lasts: the offer and first
            # contact stay off for everyone until the operator acts.
            return None
        if persist.launch_marker() is not None:
            return persist.launch_marker()
        marker = persist.ensure_launch_marker(sorted(known_people(bot)))
        if marker is None:
            # The state file was damaged and moved aside: no new marker
            # until the operator restores or deletes it (everyone English).
            return None
        log.info("languages: launch marker written (%s, %d people)",
                 "fresh install" if marker.get("fresh") else "existing install",
                 len(marker.get("people", ())))
        return marker
    except Exception:  # noqa: BLE001 - must not stop the daemon from starting
        log.info("could not write the language launch marker", exc_info=True)
        return None


def note_started(bot) -> None:
    """Polling started: the steps wait out :data:`START_GRACE` from now."""
    bot._language_launch_ready_at = _clock() + START_GRACE


async def _wait_for_grace(bot) -> None:
    ready_at = getattr(bot, "_language_launch_ready_at", None)
    if not isinstance(ready_at, (int, float)) or isinstance(ready_at, bool):
        return
    remaining = ready_at - _clock()
    if remaining > 0:
        await _sleep(remaining)


# ---- after each update ----------------------------------------------------------

def _tasks(bot) -> set:
    tasks = getattr(bot, "_language_launch_tasks", None)
    if tasks is None:
        tasks = set()
        bot._language_launch_tasks = tasks
    return tasks


def _in_flight(bot) -> set:
    chats = getattr(bot, "_language_launch_in_flight", None)
    if chats is None:
        chats = set()
        bot._language_launch_in_flight = chats
    return chats


def _served(bot) -> list[int]:
    try:
        return list(bot._served_private_chats())
    except Exception:  # noqa: BLE001 - no served chat: nothing to do
        log.debug("could not list the served private chats", exc_info=True)
        return []


async def after_update(bot, update, _ctx=None) -> None:
    """Runs after the handlers that answered *update*. Never raises."""
    try:
        from aipager.i18n import resolve
        chat_id = resolve._chat_id(update)
        if chat_id is None or chat_id <= 0 or chat_id not in _served(bot):
            return
        uid = resolve._int(getattr(resolve._user(update), "id", None))
        if uid != chat_id:
            return
        resolve.note_language(update)
        _schedule(bot, chat_id)
    except Exception:  # noqa: BLE001 - after the reply: never breaks it
        log.info("language launch step failed", exc_info=True)


def _schedule(bot, chat_id: int) -> None:
    in_flight = _in_flight(bot)
    if chat_id in in_flight:
        return
    in_flight.add(chat_id)
    task = asyncio.get_running_loop().create_task(_chat_step(bot, chat_id))
    tasks = _tasks(bot)
    tasks.add(task)

    def _done(t, chat_id=chat_id):
        tasks.discard(t)
        in_flight.discard(chat_id)

    task.add_done_callback(_done)


async def _chat_step(bot, chat_id: int) -> None:
    try:
        await _wait_for_grace(bot)
        if not await _first_contact(bot, chat_id):
            await _offer(bot, chat_id)
        await _refresh_if_moved(bot, chat_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - background: log and carry on
        log.info("language launch step failed for a private chat", exc_info=True)


def _real_send(sent) -> bool:
    from aipager.bot.transport import MUTED, SKIPPED
    return sent is not None and sent is not MUTED and sent is not SKIPPED


async def _first_contact(bot, chat_id: int) -> bool:
    """The 7.1 line, when it fits. Whether this chat is one for the line
    (new since the marker), sent or not: such a chat never gets the
    offer."""
    from aipager import i18n, preferences
    if persist.is_existing_person(chat_id):
        return False
    if (preferences.get_ui_language(chat_id) or persist.first_contact_done(chat_id)):
        return True
    tr = i18n.for_chat(chat_id)
    if tr.code == "en":
        return True
    from aipager.bot.report_offer import dm_clear
    if not dm_clear(chat_id):
        return True
    from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
    from aipager.bot.language_menu import first_contact_text
    from aipager.bot.transport import send_text
    try:
        sent = await send_text(
            bot._app.bot, chat_id, first_contact_text(tr), parse_mode="HTML",
            disable_notification=True,
            rate_limit_args=rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT))
    except Exception as e:  # noqa: BLE001 - an ornament never breaks its caller
        log.info("first-contact line not sent (%s)", type(e).__name__)
        return True
    if _real_send(sent):
        persist.mark_first_contact_done(chat_id)
    return True


async def _offer(bot, chat_id: int) -> bool:
    """The 7.4 offer to an existing private chat, once ever. None while
    the language state file is damaged: "once" cannot be kept then."""
    if (persist.is_damaged() or not persist.is_existing_person(chat_id)
            or persist.offer_done(chat_id)):
        return False
    from aipager.bot import language_offer
    if await language_offer.maybe_offer(bot, chat_id, persist.seen(chat_id)):
        persist.mark_offer_done(chat_id)
        return True
    return False


def _registered(bot, chat_id: int) -> str | None:
    if getattr(bot, "scopes", None) is None:
        return getattr(bot, "_registered_lang", None)
    return (getattr(bot, "_registered_scope_lang", None) or {}).get(chat_id)


async def _refresh_if_moved(bot, chat_id: int) -> None:
    """The chat's language moved with its person's Telegram language (no
    choice): its ``/`` menu and menu button follow, with no keyboard
    message (decision D9)."""
    from aipager import i18n
    registered = _registered(bot, chat_id)
    if registered is None or i18n.for_chat(chat_id).code == registered:
        return
    await bot._on_language_changed(chat_id, keyboard=False)


async def offer_sweep(bot) -> None:
    """At start: the offer to each existing private chat this install
    serves, with nothing stored and no offer yet, whose person's Telegram
    language an earlier run saw and is not English. A chat whose step
    after an update is running is left to that step (one in-flight guard
    for both), so the offer never goes out twice. Never raises."""
    try:
        await _sleep(SWEEP_DELAY)
        for chat_id in _served(bot):
            seen = persist.seen(chat_id)
            if not seen or seen == "en":
                continue
            from aipager import preferences
            if preferences.get_ui_language(chat_id):
                continue
            in_flight = _in_flight(bot)
            if chat_id in in_flight:
                continue
            in_flight.add(chat_id)
            try:
                await _offer(bot, chat_id)
            finally:
                in_flight.discard(chat_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - background: log and carry on
        log.info("language offer sweep failed", exc_info=True)


async def drain(bot) -> None:
    """Wait for every background step scheduled so far (tests)."""
    while True:
        pending = [t for t in _tasks(bot) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def cancel_tasks(bot) -> None:
    """Cancel the background steps (the daemon is stopping)."""
    for task in list(_tasks(bot)):
        if not task.done():
            task.cancel()
