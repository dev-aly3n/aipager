"""Follow a group Telegram upgraded to a supergroup (roadmap 8.87).

Making a basic group public, enabling topics, passing 200 members and some
admin-rights changes upgrade it to a supergroup, and the upgrade gives it a
NEW chat id. Telegram says so twice: a service message in the old chat
(``migrate_to_chat_id``) and one in the new chat (``migrate_from_chat_id``);
and every call into the old id fails with PTB's ``ChatMigrated`` (a
``TelegramError``, not a ``BadRequest``) whose ``new_chat_id`` is the new id.

Before this module nothing followed it: the scope kept the old id, so the
group's messages were dropped, every send failed, and its sessions were
stranded. :func:`migrate_chat` moves everything aipager keeps for that chat
to the new id, once:

- ``aipager.yaml``: the scope's ``chat_id`` (a surgical edit) plus a
  ``chat_migrations`` record, so a session whose internal name still
  carries the old id (``__g<old>``; names are internal and never change)
  lands in the new chat even after a state loss;
- in memory: the scope, the message chat gate and the command menus, the
  sessions stamped with the old id, the chat's targets, its
  ``preferences.json`` entry, the folders the Mini App created for it;
  what only made sense in the old chat (whose card each message was,
  the pinned bar's message, held answers, flood state, keyboard levels)
  is dropped, and the new chat starts fresh;
- one WARNING in the log and one line in the new chat.

A turn running during the move keeps going and its answer reaches the new
chat, but its busy card (a message in the old chat) is not shown again for
the rest of that turn.

Triggered by the service messages (:func:`handle_migrate_message`) and by
``ChatMigrated`` from any Bot API call (:func:`note_chat_migrated`, called
from the rate limiter that every call passes, the transport helpers and the
pinned bar). It runs only when the old id is a configured GROUP scope and
the new one is not a scope yet: a private chat never migrates, and a second
trigger finds nothing to do.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aipager.scope import _as_chat_id

if TYPE_CHECKING:  # pragma: no cover
    from aipager.bot.core import TelegramBot

log = logging.getLogger(__name__)

#: The one line posted in the new chat. Plain words, no dashes (UI rule).
NOTICE = ("This group was upgraded by Telegram. aipager moved with it, "
          "nothing to do.")

_handler: Callable[[int, int], Awaitable[Any]] | None = None
# Strong references to the migrations scheduled from a sync context, so
# the loop cannot drop one half way (asyncio keeps only weak ones).
_tasks: set = set()


def set_handler(fn: Callable[[int, int], Awaitable[Any]] | None) -> None:
    """Register the running daemon's ``migrate(old, new)`` coroutine
    function, or clear it with ``None``. Set by ``LifecycleMixin.start``."""
    global _handler
    _handler = fn


def note_chat_migrated(old, new) -> None:
    """A Bot API call into *old* failed with ``ChatMigrated`` naming *new*.
    Schedules the migration on the running loop; never raises, never
    waits (the caller is mid-call, often inside the rate limiter)."""
    try:
        handler = _handler
        if handler is None:
            log.warning("Telegram says chat %s is now %s (upgraded to a "
                        "supergroup), and no daemon is running to follow it",
                        old, new)
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(_run_handler(handler, old, new))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:
        log.warning("Could not follow chat %s to %s", old, new, exc_info=True)


async def _run_handler(handler, old, new) -> None:
    try:
        await handler(old, new)
    except Exception:
        log.warning("Could not follow chat %s to %s", old, new, exc_info=True)


# What Telegram answers for a chat the bot cannot reach: deleted, the bot
# removed or banned (``Forbidden``), or a basic group upgraded to a
# supergroup without saying where (``ChatMigrated`` is the reported case).
_UNREACHABLE_TEXTS = ("chat not found", "group chat was upgraded")


def is_unreachable_chat_error(exc) -> bool:
    """Whether *exc* (from a Bot API call into one chat) means the bot
    cannot reach that chat at all, rather than a passing failure."""
    from telegram.error import BadRequest, ChatMigrated, Forbidden
    if isinstance(exc, ChatMigrated):
        return False        # followed, not unreachable
    if isinstance(exc, Forbidden):
        return True
    if isinstance(exc, BadRequest):
        text = str(getattr(exc, "message", "") or exc).lower()
        return any(t in text for t in _UNREACHABLE_TEXTS)
    return False


def warn_unreachable_group(bot, scope, exc) -> None:
    """ONE warning per group scope per daemon run that the bot could not
    reach (roadmap 8.94g): from the start-time lookup, or from setting the
    group's command menu (a group added by a live reload is never looked
    up). It names the scope and what to check. Nothing is sent and
    nothing retries; the group's own traffic (a message in it, a
    session's card) follows an upgrade if Telegram reports one later."""
    warned = bot.__dict__.setdefault("_unreachable_warned", set())
    if scope.chat_id in warned:
        return
    warned.add(scope.chat_id)
    log.warning(
        "Group scope %r (chat %s) could not be reached: %s. The "
        "group was deleted, the bot was removed from it, or Telegram "
        "upgraded it to a supergroup while the daemon was down and did not "
        "say to which id. Check that the bot is still in that group; if it "
        "was upgraded, the old id %s no longer works: set the scope's new "
        "chat id with `aipager config`.",
        scope.label, scope.chat_id, getattr(exc, "message", None) or exc,
        scope.chat_id)


def ids_from_message(msg) -> tuple[int, int] | None:
    """``(old, new)`` from a migration service message, in either chat:
    the old chat's carries ``migrate_to_chat_id``, the new chat's
    ``migrate_from_chat_id``. ``None`` for any other message."""
    if msg is None:
        return None
    chat_id = _as_chat_id(getattr(msg, "chat_id", None))
    to_id = _as_chat_id(getattr(msg, "migrate_to_chat_id", None))
    from_id = _as_chat_id(getattr(msg, "migrate_from_chat_id", None))
    if to_id is not None:
        return chat_id, to_id
    if from_id is not None:
        return from_id, chat_id
    return None


async def handle_migrate_message(bot: TelegramBot, update, ctx=None) -> None:
    """``MessageHandler(filters.StatusUpdate.MIGRATE)``. A service message
    has no meaningful sender, so it is never authorized as a person's
    message: what makes it safe is that only Telegram sends it, and that
    :func:`migrate_chat` moves only a configured group scope."""
    try:
        ids = ids_from_message(getattr(update, "effective_message", None))
        if ids is not None:
            await bot._migrate_chat(*ids)
    except Exception:
        log.warning("Could not follow a group's upgrade to a supergroup",
                    exc_info=True)


def _scope_for(scopes, chat_id):
    for s in scopes or ():
        if s.chat_id == chat_id:
            return s
    return None


async def migrate_chat(bot: TelegramBot, old, new) -> bool:
    """Move everything aipager keeps for the group *old* to the supergroup
    *new* (see the module docstring). Returns whether it moved anything.

    Idempotent: everything that decides is read and changed before the
    first ``await``, so two triggers racing (both service messages and a
    failed send arrive within a second) move it once."""
    old_id, new_id = _as_chat_id(old), _as_chat_id(new)
    scopes = bot.scopes
    # Personal or legacy mode (no scopes) has no scope to move.
    scope = _scope_for(scopes, old_id)
    if scope is None:
        log.debug("Chat %s moved to %s: not a scope (or already followed)",
                  old_id, new_id)
        return False
    if scope.kind != "group" or (new_id or 0) >= 0:
        # Only a group is ever upgraded; a private chat never moves.
        log.warning("Ignoring a move of chat %s (%s) to %s: only a group "
                    "is upgraded to a supergroup", old_id, scope.kind, new_id)
        return False
    if _scope_for(scopes, new_id) is not None:
        # Once per pair: every later call into the old id reports it again.
        warned = bot.__dict__.setdefault("_migration_refused", set())
        if (old_id, new_id) in warned:
            return False
        warned.add((old_id, new_id))
        log.warning(
            "Telegram upgraded group %s (%s) to %s, which is already a scope "
            "in aipager.yaml; not moving it. Remove one of the two with "
            "`aipager config`.", old_id, scope.label, new_id)
        return False

    _write_config(old_id, new_id)
    old_scopes = list(scopes)
    bot.scopes = [dataclasses.replace(s, chat_id=new_id)
                  if s.chat_id == old_id else s for s in scopes]
    _follow_config_chat_id(old_id, new_id)
    _refresh_gate(bot)
    moved = bot.registry.migrate_chat(old_id, new_id)
    _move_preferences(old_id, new_id)
    _move_created_folders(old_id, new_id)
    _drop_old_chat_state(bot, old_id)
    try:
        bot.registry.save()
    except Exception:
        log.warning("Could not save the session registry after the move",
                    exc_info=True)
    log.warning(
        "Telegram upgraded group %s (%s) to a supergroup with the new id "
        "%s. aipager moved the scope, %d session(s) and the chat's settings "
        "there.", old_id, scope.label, new_id, len(moved))

    try:
        await bot._refresh_scope_menus(old_scopes)
    except Exception:
        log.warning("Could not refresh the command menus after the move",
                    exc_info=True)
    await _post_notice(bot, new_id)
    return True


def _write_config(old_id: int, new_id: int) -> None:
    from aipager import scope as scope_mod
    try:
        scope_mod.migrate_scope_chat_id(old_id, new_id)
    except Exception as e:
        log.warning(
            "Could not rewrite aipager.yaml for group %s -> %s (%s); the "
            "move holds until the daemon restarts. Set that scope's chat_id "
            "to %s with `aipager config`.", old_id, new_id, e, new_id)


def _follow_config_chat_id(old_id: int, new_id: int) -> None:
    """``config.CHAT_ID`` is the home chat, which is the group only on an
    install with no private chat scope."""
    from aipager import config
    if str(getattr(config, "CHAT_ID", "")) == str(old_id):
        config.CHAT_ID = str(new_id)


def _refresh_gate(bot) -> None:
    from aipager.bot import live_reload
    gate = getattr(bot, "_message_chat_gate", None)
    if gate is None:
        return
    try:
        live_reload.refresh_chat_gate(gate, bot.scopes)
    except Exception:
        log.warning("Could not update the message chat gate after the move",
                    exc_info=True)


def _move_preferences(old_id: int, new_id: int) -> None:
    from aipager import preferences
    try:
        preferences.move_chat(old_id, new_id)
    except Exception:
        log.warning("Could not move the chat's preferences", exc_info=True)


def _move_created_folders(old_id: int, new_id: int) -> None:
    """The Mini App's folders created for the group stay launch folders
    in the new chat (in memory, :mod:`aipager.miniapp.launch`)."""
    try:
        from aipager.miniapp import launch
        launch.move_created(old_id, new_id)
    except Exception:
        log.warning("Could not move the chat's created folders",
                    exc_info=True)


def _drop_old_chat_state(bot, old_id: int) -> None:
    """What only made sense in the old chat: held answers (they reply to
    its messages), its flood state, the pinned bar, keyboard levels and
    owed keyboards, re-sent prompt surfaces, one-shot notices."""
    from aipager.bot.flood import MUTE, _key
    from aipager.bot.held import HELD
    from aipager.bot.rich_message import get_rate_limiter

    try:
        HELD.forget_chat(old_id)
        MUTE.forget(old_id)
        limiter = get_rate_limiter()
        if limiter is not None and hasattr(limiter, "forget_chat"):
            limiter.forget_chat(old_id)
    except Exception:
        log.warning("Could not drop the old chat's flood state", exc_info=True)
    key = _key(old_id)
    st = getattr(bot, "_pinned", {}).pop(old_id, None)
    if st is not None and st.trailing is not None and not st.trailing.done():
        st.trailing.cancel()
    levels = getattr(bot, "_keyboard_levels", {})
    for k in list(levels):
        if k == key or (isinstance(k, tuple) and k and k[0] == key):
            del levels[k]
    getattr(bot, "_keyboard_owed", {}).pop(key, None)
    resent = getattr(bot, "_resent_prompts", {})
    for k in [k for k in resent if k[0] == old_id]:
        del resent[k]
    told = getattr(bot, "_read_only_told", set())
    for k in [k for k in told if isinstance(k, tuple) and k and k[0] == old_id]:
        told.discard(k)


async def _post_notice(bot, new_id: int) -> None:
    if getattr(bot, "_app", None) is None:
        return
    from aipager.bot.transport import send_text
    try:
        await send_text(bot._app.bot, new_id, NOTICE)
    except Exception:
        log.info("Could not post the upgrade notice in chat %s", new_id,
                 exc_info=True)
