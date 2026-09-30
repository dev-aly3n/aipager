"""Starting a session from chat: `/new`, in every spelling, ends the same way.

Operator (2026-09-30): "if we send /new x1 or we send /new and then send
x1 so these are two variant but I want same experience for both" and
"default for ask mode auto mode must be automode for all new sessions".

One parser, :func:`create_from_text`, takes ``name [first message...]``
from every entry point:

- ``/new x1`` / ``/new x1 fix the tests`` / ``/new !x1`` (the argument);
- bare ``/new`` (a menu tap, the keyboard word ``new``, the bare word
  ``new``) sends the **Name card**, and the next message from the same
  person is that same argument.

No mode step, no summary, no Confirm: a valid, free name creates the
session at once, with the chat's defaults (``/settings → 🆕 New
sessions``, :func:`resolve_new_session_settings`) or what the Name card
was switched to. Every path ends in the **Ready card**, which says where
the next message goes and offers one-tap Switch to Ask/Auto and Model.
A name already in use gets the Resume / Replace / Cancel card on every
path.

Three functions are the whole contract other modules use, unchanged in
shape from the old wizard:

    start_wizard(bot, update, ctx, error="")  — bare `/new`: the Name card.
    maybe_handle_text(bot, update, ctx, text) -> bool
                                              — every inbound text (and
                                                voice transcript) before
                                                any other routing; True
                                                iff the Name card (or one
                                                of its text steps) took it.
    handle_callback(bot, update, query, session_name, action) -> bool
                                              — True iff the callback is
                                                one of this module's
                                                (`_:nw:...`, `rdy_*` on a
                                                session, `_:set:ns...`).

Pending state lives on ``bot._new_wizard_pending: dict[int, dict]``, keyed
by chat id: one Name card per chat, a second bare ``/new`` replaces it,
idle more than ``_WIZARD_TTL_SECONDS`` is gone (checked lazily). Every
step is authorized against the person acting NOW, and a stranger's text
is never consumed (see :func:`_actor_id`).

Callback data (sentinel ``_``, never free text):

    _:nw:cancel                  Cancel the Name card
    _:nw:mode:auto|ask           Switch the Name card's mode
    _:nw:back                    Back to the Name card from a picker
    _:nw:opt:model|path          Open the model / folder picker
    _:nw:model:<idx>|default|custom
    _:nw:path:<idx>|default|new
    _:sx:<i>:rdy_ask|rdy_auto    Ready card: switch this session's mode
    _:sx:<i>:rdy_model           Ready card: open the model list
    _:sx:<i>:rdy_m<idx>          Ready card: switch to MODEL_CHOICES[idx]
    _:sx:<i>:rdy_back            Ready card: back from the model list
    _:set:ns[:mode|model|cwd[:<token>]]  /settings → 🆕 New sessions
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import time
from typing import TYPE_CHECKING

from telegram import ExternalReplyInfo, InlineKeyboardButton, InlineKeyboardMarkup

from aipager import preferences
from aipager.bot.transport import (
    MUTED,
    calling_chat_id,
    driver_id_from_update,
    edit_message,
    edit_text_at,
    reply_text,
    send_text,
)
from aipager.config import (
    APP_BUTTON,
    BACK_BUTTON,
    COMMANDS_BUTTON,
    MODEL_CHOICES,
    MODELS_BUTTON,
    TEMPLATES_BUTTON,
)
from aipager.miniapp import launch
from aipager.state import Status

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

    from aipager.bot.core import TelegramBot
    from aipager.state import TrackedSession

log = logging.getLogger("aipager.bot.new_flow")

# Idle-more-than-this-and-it's-gone. Checked lazily on the next tap or
# text message, no background timer.
_WIZARD_TTL_SECONDS = 600

# Steps whose free text this module takes. The Name card and its two
# pickers take a NAME (someone who opened the model list and then typed
# a name meant the name); the custom-model and new-folder steps take
# their own value.
_NAME_STEPS = frozenset({"name", "opt_model", "opt_path"})
_TEXT_CAPTURE_STEPS = _NAME_STEPS | {"opt_model_custom", "opt_path_newfolder"}

_EXPIRED_TEXT = "⏱ This new-session card expired. Send /new again."
_CLOSED_TEXT = "✖️ Closed, you moved on. Send /new to start a session."

#: Main-keyboard words that only look at things (or reopen the card):
#: the card keeps waiting through them. `stop` and `kill` act on a
#: session, so they close it.
_LOOKING_WORDS = frozenset({"new", "status"})
_ACTING_WORDS = frozenset({"stop", "kill"})


# ---- pending-state plumbing ------------------------------------------

def _pending_store(bot: TelegramBot) -> dict[int, dict]:
    """Lazily-initialized ``bot._new_wizard_pending`` (an instance
    attribute, not a module dict, so nothing leaks between bots)."""
    store = getattr(bot, "_new_wizard_pending", None)
    if store is None:
        store = {}
        bot._new_wizard_pending = store
    return store


def _now() -> float:
    return time.monotonic()


def _is_expired(pending: dict) -> bool:
    return (_now() - pending.get("last_active", 0.0)) > _WIZARD_TTL_SECONDS


def close_open_card(bot: TelegramBot, chat_id: int | None,
                    user_id: int | None) -> None:
    """The person moved on: they started, switched to or resumed a
    session some other way, or began another text step. Their open Name
    card stops taking their next message as a name, and says so. Without
    this, a message meant for the session they just picked would start a
    new one (review 2026-09-30). Someone else's card in the chat is
    theirs, and stays."""
    if chat_id is None or user_id is None:
        return
    store = _pending_store(bot)
    pending = store.get(chat_id)
    if pending is None or pending.get("user_id") != user_id:
        return
    store.pop(chat_id, None)
    # The pop is what matters; the edit only tells them, so nothing
    # waits on it (held like every other background send).
    from aipager.bot.notify import _BACKGROUND_TASKS  # local: import cycle
    task = asyncio.create_task(_edit_wizard(bot, chat_id, pending, _CLOSED_TEXT))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


#: Commands that only look (or reopen or use the card): the card keeps
#: waiting through them. Every other command acts on a session.
_LOOKING_COMMANDS = frozenset({"new", "status", "start", "help", "whoami",
                               "settings", "app"})

#: Buttons that only look: the card's own, /settings (its New sessions
#: page is this card's own defaults; per-session preferences), and paging
#: through the resume list.
_LOOKING_BUTTONS = ("_:nw:", "_:set:", "_:spref", "_:resume_page:", "_:resume_noop",
                    "_:st:")


def _bot_username(bot: TelegramBot) -> str:
    try:
        return str(bot._app.bot.username or "")
    except Exception:     # not initialized (tests, startup)
        return ""


def _leaves_the_card_open(bot: TelegramBot, update: Update) -> bool:
    """Whether this update is the card's own business or only looking.
    Everything that reaches a session is the person talking to it."""
    query = getattr(update, "callback_query", None)
    msg = getattr(update, "message", None)
    if query is not None and msg is None:
        return (getattr(query, "data", None) or "").startswith(_LOOKING_BUTTONS)
    if msg is None:
        return True       # an edit, a reaction: not talking to a session
    if (getattr(msg, "forward_origin", None) is not None
            or getattr(msg, "via_bot", None) is not None):
        return False      # forwarded, or posted through a bot: for a session, never a name
    text = getattr(msg, "text", None)
    if isinstance(text, str):
        if text.startswith("/"):
            head = text[1:].split(maxsplit=1)[0] if len(text) > 1 else ""
            command, _, target = head.partition("@")
            me = _bot_username(bot)
            if target and me and target.lower() != me.lower():
                return True   # another bot's command: nothing here acts on it
            return command.lower() in _LOOKING_COMMANDS
        return True       # plain text: the card's own rules decide
    if getattr(msg, "photo", None) or getattr(msg, "document", None) is not None:
        return False      # a photo or a file goes to a session
    # A voice note (its transcript meets the same rules as text), and the
    # kinds nothing here acts on (a sticker, a location).
    return True


async def close_if_moved_on(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    """Runs before every other handler (group -1, see lifecycle.start).

    A name starts a session at once, so the card must stop listening the
    moment its person does anything else: a photo, a command such as
    /stop, a button on another message. Closed by default, with the few
    exceptions in :func:`_leaves_the_card_open`, instead of a list of
    exits to keep up to date (three reviews found one missing each time,
    2026-09-30). Never raises, never stops the update."""
    try:
        if not _leaves_the_card_open(bot, update):
            # Only the sender's own card, if they have one open here.
            close_open_card(
                bot, calling_chat_id(update),
                getattr(getattr(update, "effective_user", None), "id", None))
    except Exception:
        log.debug("new_flow: close_if_moved_on failed", exc_info=True)


def _not_for_the_card(bot: TelegramBot, update: Update, pending: dict,
                      text: str) -> str:
    """What the open card does with a text that is not its answer, since
    a name now starts a session at once:

    - ``"close"``: the person is talking to a session (a reply to another
      message, a template, Claude command or model tap, ``stop`` /
      ``kill``). It is routed as usual and the card closes, or it would
      take the NEXT message as a name (review 2026-09-30). Commands,
      files and other buttons never get here: :func:`close_if_moved_on`
      closes the card for them first.
    - ``"keep"``: only looking around (the keyboard's navigation
      buttons, ``status``) or reopening it (``new``). Routed as usual;
      the card keeps waiting.
    - ``""``: the card's answer.
    """
    reply_to = getattr(update.message, "reply_to_message", None)
    if reply_to is not None and getattr(reply_to, "message_id", None) != pending.get("msg_id"):
        return "close"
    if isinstance(getattr(update.message, "external_reply", None), ExternalReplyInfo):
        return "close"    # a quote from another chat: a reply, never a name
    if pending["step"] == "opt_model_custom":
        return ""         # a custom model may well be a model label
    word = text.strip().lower()
    if word in _LOOKING_WORDS or text in (
            TEMPLATES_BUTTON, COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON, APP_BUTTON):
        return "keep"
    if word in _ACTING_WORDS or any(
            text in (getattr(bot, attr, None) or {})
            for attr in ("_template_map", "_command_map", "_model_map")):
        return "close"
    return ""


def _touch(pending: dict) -> None:
    pending["last_active"] = _now()


def _short_path(path: str, limit: int = 40) -> str:
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


def choice_label(model: str) -> str:
    """The MODEL_CHOICES label for a CLI model id ("opus" → "Opus"), the
    id itself for a model typed by hand, "" for none."""
    if not model:
        return ""
    for label, command in MODEL_CHOICES:
        if str(command).split(None, 1)[-1].strip() == model:
            return label
    return model


def _project_dir() -> str:
    from aipager.dtach import inject  # local: avoids an import cycle
    return inject._PROJECT_DIR


# ---- the settings a new session gets ------------------------------------

def resolve_new_session_settings(
    bot: TelegramBot, chat_id: int, user_id: int | None,
) -> dict:
    """What a session started now by ``user_id`` in ``chat_id`` gets when
    nobody chooses: the chat's stored defaults, checked against what is
    true NOW. Auto unless the chat's default says Ask, and never for
    someone who may not use Auto. A stored model no longer offered, or a
    folder no longer allowed, falls back to the built-in default."""
    stored = preferences.get_new_session_defaults(chat_id)
    can_auto = bool(bot._is_admin_user(user_id, chat_id))
    model = model_label = None
    # Only a label still offered: the defaults screen stores a
    # MODEL_CHOICES label, and one withdrawn since must not be passed on
    # as if it were a custom model name.
    if stored.model and stored.model in {lbl for lbl, _cmd in MODEL_CHOICES}:
        resolved, err = launch.validate_model(stored.model, MODEL_CHOICES)
        if not err and resolved:
            model, model_label = resolved, stored.model
    cwd = None
    if stored.cwd and stored.cwd in launch.allowed_roots(bot.registry, chat_id):
        cwd = stored.cwd
    return {
        "skip_perms": can_auto and stored.mode != "ask",
        "can_auto": can_auto,
        "model": model,
        "model_label": model_label,
        "cwd": cwd,
    }


def _settings_line(settings: dict) -> str:
    mode = "🤖 Auto" if settings.get("skip_perms") else "💬 Ask"
    model = settings.get("model_label") or "Default model"
    folder = settings.get("cwd") or _project_dir()
    return (f"{mode} · 🧠 {html_mod.escape(model)} · "
            f"📁 {html_mod.escape(_short_path(folder))}")


# ---- the Name card ------------------------------------------------------

def _render_name_card(pending: dict, *, error: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🆕 <b>New session</b>", ""]
    if error:
        lines += [f"⚠️ {html_mod.escape(error)}", ""]
    lines += [
        "Send a name, and what to do first if you like:",
        "<code>x1</code>",
        "<code>x1 fix the failing tests</code>",
        "",
        _settings_line(pending),
    ]
    row = []
    if pending.get("can_auto"):
        if pending.get("skip_perms"):
            row.append(InlineKeyboardButton("💬 Ask instead",
                                            callback_data="_:nw:mode:ask"))
        else:
            row.append(InlineKeyboardButton("🤖 Auto instead",
                                            callback_data="_:nw:mode:auto"))
    row += [
        InlineKeyboardButton("🧠 Model", callback_data="_:nw:opt:model"),
        InlineKeyboardButton("📁 Folder", callback_data="_:nw:opt:path"),
    ]
    rows = [row, [InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel")]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _back_cancel_row(back_action: str = "_:nw:back") -> list[InlineKeyboardButton]:
    # Every caller passes a literal "_:nw:..." constant, never a session.
    return [InlineKeyboardButton("« Back", callback_data=back_action),
            InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel")]


def _render_opt_model(pending: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = pending.get("model_label")
    rows = []
    for idx, (label, _cmd) in enumerate(MODEL_CHOICES):
        marker = " ✅" if current == label else ""
        rows.append([InlineKeyboardButton(
            f"{label}{marker}", callback_data=f"_:nw:model:{idx}")])
    default_marker = " ✅" if not pending.get("model") else ""
    rows.append([InlineKeyboardButton(
        f"Default model{default_marker}", callback_data="_:nw:model:default")])
    rows.append([InlineKeyboardButton(
        "✏️ Other model...", callback_data="_:nw:model:custom")])
    rows.append(_back_cancel_row())
    return "🧠 <b>Model</b>\n\nPick a model for this session.", InlineKeyboardMarkup(rows)


def _render_opt_path(
    bot: TelegramBot, pending: dict, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    # Fresh every render: the snapshot backing `_:nw:path:<idx>` is taken
    # here, never reused from an earlier render.
    roots = launch.allowed_roots(bot.registry, chat_id)
    pending["path_options"] = roots
    current = pending.get("cwd")
    rows = []
    for idx, root in enumerate(roots):
        marker = " ✅" if current == root else ""
        rows.append([InlineKeyboardButton(
            f"{_short_path(root)}{marker}", callback_data=f"_:nw:path:{idx}")])
    default_marker = " ✅" if not current else ""
    rows.append([InlineKeyboardButton(
        f"Default folder{default_marker}", callback_data="_:nw:path:default")])
    rows.append([InlineKeyboardButton(
        "➕ New folder", callback_data="_:nw:path:new")])
    rows.append(_back_cancel_row())
    return ("📁 <b>Folder</b>\n\nPick where this session works.",
            InlineKeyboardMarkup(rows))


def _model_custom_prompt_text(error: str = "") -> str:
    header = "🧠 <b>Other model</b>\n\n"
    if error:
        return header + f"⚠️ {html_mod.escape(error)}\n\nType the model name."
    return header + "Type the model name (e.g. claude-opus-5)."


def _path_newfolder_prompt_text(error: str = "") -> str:
    header = "📁 <b>New folder</b>\n\n"
    if error:
        return header + f"⚠️ {html_mod.escape(error)}\n\nType the new folder's name."
    return header + "Type the new folder's name."


# ---- message editing ---------------------------------------------------

async def _edit_at(
    bot: TelegramBot, chat_id: int, message_id: int | None, text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    if message_id is None:
        return
    try:
        await edit_text_at(bot._app.bot,
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode="HTML", reply_markup=kb,
        )
    except Exception:
        log.debug("new_flow: failed to edit message %s", message_id, exc_info=True)


async def _edit_wizard(
    bot: TelegramBot, chat_id: int, pending: dict, text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    await _edit_at(bot, chat_id, pending.get("msg_id"), text, kb)


async def _goto_name(
    bot: TelegramBot, chat_id: int, pending: dict, *, error: str = "",
) -> None:
    pending["step"] = "name"
    text, kb = _render_name_card(pending, error=error)
    await _edit_wizard(bot, chat_id, pending, text, kb)


# ---- entry point: bare /new ---------------------------------------------

async def start_wizard(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE | None = None,
    *, error: str = "",
) -> None:
    """Bare ``/new``: send the Name card. Re-checks authorization itself.
    ``error`` is shown on it (a ``/new`` whose argument was not a valid
    name opens the card with the reason, so the next message can just be
    the name)."""
    if not await bot._authorize(update):
        return
    chat_id = calling_chat_id(update)
    if chat_id is None or update.message is None:
        return
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user is not None else None

    async def _reply(text, kb):
        return await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)
    await _open_card(bot, chat_id, user_id, _reply, error=error)


async def open_card_from_tap(bot: TelegramBot, update: Update, query: CallbackQuery) -> None:
    """``🆕 New`` on /status or /start (``_:nw:open``): the same Name card
    as a bare ``/new``, sent as a new message and owned by the person who
    tapped, who must be allowed to prompt here."""
    chat_id = calling_chat_id(update)
    actor = _actor_id(update, query)
    if chat_id is None:
        await bot._safe_answer(query, "Invalid callback")
        return
    if not bot._can_prompt_user(actor, chat_id):
        await bot._safe_answer(query, "You can't start sessions here.", show_alert=True)
        return
    await bot._safe_answer(query)

    async def _send(text, kb):
        return await send_text(bot._app.bot, chat_id, text, parse_mode="HTML",
                               reply_markup=kb)
    await _open_card(bot, chat_id, actor, _send)


async def _open_card(bot: TelegramBot, chat_id: int, user_id: int | None, send,
                     *, error: str = "") -> None:
    """Send the Name card with *send* and make it the chat's open card,
    owned by *user_id* (a second one replaces the first)."""
    store = _pending_store(bot)
    # The same person's rename left waiting for its new name would take
    # the message after this card's name: their newest question wins.
    # Someone else's rename in the group is theirs.
    from aipager.bot import session_parity  # local: avoids an import cycle
    renames = session_parity._rename_pending_map(bot)
    if renames.get(chat_id, {}).get("user_id") == user_id:
        renames.pop(chat_id, None)
    old = store.pop(chat_id, None)
    if old is not None:
        # A second bare `/new` replaces the first: strip the old card's
        # keyboard so no tap can land on it.
        await _edit_at(bot, chat_id, old.get("msg_id"),
                       "↩️ Cancelled - started over.")

    pending = {
        "step": "name",
        "user_id": user_id,
        "msg_id": None,
        "path_options": [],
        "new_folder_parent": None,
        "last_active": _now(),
        **resolve_new_session_settings(bot, chat_id, user_id),
    }
    text, kb = _render_name_card(pending, error=error)
    sent = await send(text, kb)
    if sent is MUTED or getattr(sent, "message_id", None) is None:
        # The chat is flood-muted: the card never went out, so nothing may
        # swallow the next message as its name.
        return
    pending["msg_id"] = sent.message_id
    store[chat_id] = pending


# ---- the one parser ----------------------------------------------------

def _actor_id(update: Update, query: CallbackQuery | None = None) -> int | None:
    """The user acting RIGHT NOW (not whoever opened the card): every step
    is authorized against them, so in a group nobody drives someone else's
    card, and a read_only member cannot tap their way to a session."""
    user = getattr(query, "from_user", None) if query is not None else None
    if user is None:
        user = update.effective_user
    return user.id if user is not None else None


def parse_request(text: str) -> tuple[str, str, bool, str]:
    """``name [first message...]`` → ``(name, first, force_auto, error)``.

    The first word is the name, normalised and checked by
    ``launch.validate_session_name`` (the same rules everywhere); a
    leading ``!`` is the legacy "Auto" shorthand and is stripped. The rest,
    if any, is the first message, newlines flattened (an embedded newline
    would press Enter early)."""
    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return "", "", False, "Session name can't be empty."
    # "x1: fix it", "x1, fix it" and a transcript's "X1." name x1: the
    # punctuation can never be part of a valid name anyway.
    raw = parts[0].rstrip(".,:;!?")
    force_auto = raw.startswith("!")
    clean, err = launch.validate_session_name(raw.lstrip("!"))
    first = parts[1].strip().replace("\n", " - ") if len(parts) > 1 else ""
    return clean, first, force_auto, err


async def create_from_text(
    bot: TelegramBot, update: Update, text: str, *, pending: dict | None = None,
) -> None:
    """Start a session from ``name [first message...]``, the one path
    every `/new` spelling takes. ``pending`` is the Name card's state when
    the text answered it (its message becomes the Ready card), else the
    chat's defaults apply and a new status message becomes it."""
    if update.message is None:
        return
    chat_id = calling_chat_id(update)
    actor = _actor_id(update)
    store = _pending_store(bot)

    name, first, force_auto, err = parse_request(text)
    if err:
        if pending is not None:
            await _goto_name(bot, chat_id, pending, error=err)
        elif chat_id is None:
            # No chat to hold a Name card for (an unscoped legacy update):
            # just say why.
            await reply_text(update.message, f"⚠️ {html_mod.escape(err)}",
                             parse_mode="HTML")
        else:
            await start_wizard(bot, update, None, error=err)
        return

    if pending is None:
        # Started with `/new x1` while their own Name card was open: that
        # card must not take their next message too.
        close_open_card(bot, chat_id, actor)
    settings = pending if pending is not None else resolve_new_session_settings(
        bot, chat_id or 0, actor)
    # Auto is re-checked here, at the moment of creation, against the
    # person creating: a stale card or a `!` from someone who may not use
    # Auto gets Ask, and the Ready card says why.
    may_auto = bool(bot._is_admin_user(actor, chat_id))
    wants_auto = bool(settings.get("skip_perms")) or force_auto
    skip_perms = wants_auto and may_auto
    note = "Auto mode needs an admin, so this one asks." if wants_auto and not may_auto else ""

    existing = bot.registry.find_by_label(name, chat_id, include_gone=True)
    if existing is not None and (
        existing.status != Status.GONE or existing.claude_session_id
    ):
        if pending is not None:
            store.pop(chat_id, None)
        # The Name card itself becomes the conflict card (one message per
        # flow); `/new x1` gets it as the reply.
        await bot._send_new_conflict_prompt(
            update=update, existing=existing, prompt=first,
            skip_perms=skip_perms, model=settings.get("model"),
            cwd=settings.get("cwd"),
            edit_msg_id=pending.get("msg_id") if pending is not None else None)
        return

    launching = (f"🚀 Starting <b>{html_mod.escape(name)}</b> "
                 f"{'🤖 Auto' if skip_perms else '💬 Ask'}…")
    sent = None
    if pending is not None:
        store.pop(chat_id, None)
        msg_id = pending.get("msg_id")
        await _edit_at(bot, chat_id, msg_id, launching)
    else:
        sent = await reply_text(update.message, launching, parse_mode="HTML")
        msg_id = None if sent is MUTED else sent.message_id

    async def _show(body: str, kb: InlineKeyboardMarkup | None = None) -> None:
        if chat_id is None and sent is not None:
            # No chat id to edit by (an unscoped legacy update): edit the
            # message this call sent.
            await edit_message(sent, body, parse_mode="HTML", reply_markup=kb)
        else:
            await _edit_at(bot, chat_id, msg_id, body, kb)

    session_name, err = await bot.create_session(
        name, scope_chat_id=chat_id, skip_perms=skip_perms,
        cwd=settings.get("cwd") or None, driver_user_id=actor,
        model=settings.get("model") or None,
    )
    if not session_name:
        if pending is not None and store.get(chat_id) is None:
            # Retry-able: the Name card comes back with its choices kept,
            # unless a newer card opened while this one was launching.
            pending["msg_id"] = msg_id
            _touch(pending)
            store[chat_id] = pending
            await _goto_name(bot, chat_id, pending, error=err)
        else:
            await _show(f"❌ {html_mod.escape(err)}")
        return

    sess = bot.registry.get_or_create(session_name)
    bot._mark_driver(sess, update)
    if first and sess.queue_prompt(first, update.message.message_id, "",
                                   driver_id_from_update(update)):
        bot.registry.mark_dirty()
    ready_text, ready_kb = render_ready(
        bot, update, sess, model_label=settings.get("model_label"),
        first_message=bool(first), note=note)
    await _show(ready_text, ready_kb)
    log.info("Launched session %s (%s, first message=%s)", name,
             "auto" if skip_perms else "ask", bool(first))


# ---- the Ready card ----------------------------------------------------

def render_ready(
    bot: TelegramBot, update: Update | None, sess: TrackedSession, *,
    model_label: str | None = None, first_message: bool = False, note: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    """Where every way of starting a session ends: what it got, and what
    to do next."""
    from aipager.bot import session_parity  # local: avoids an import cycle
    label = html_mod.escape(sess.label)
    mode = "🤖 Auto" if sess.skip_perms else "💬 Ask"
    # The model just picked wins; then, while a switch is not yet
    # confirmed, the one it was switched to; then what the statusline
    # reports; then what it was started with.
    chosen = choice_label(sess.launch_model)
    model = (model_label
             or (chosen if sess.model_switch_pending() else "")
             or sess.model_name or chosen or "Default model")
    folder = sess.cwd or _project_dir()
    lines = [
        f"✅ <b>{label}</b> is ready",
        f"{mode} · 🧠 {html_mod.escape(model)} · 📁 <code>"
        f"{html_mod.escape(_short_path(folder))}</code>",
    ]
    if note:
        lines.append(f"⚠️ {html_mod.escape(note)}")
    lines.append("")
    if first_message:
        lines.append("▶️ Working on your first message.")
    else:
        lines.append(f"✍️ Just send a message, it goes to {label}.")
    lines.append(f"Later: tap {label} on the keyboard, or reply to any {label} message.")

    chat_id = sess.scope_chat_id or (calling_chat_id(update) if update else 0) or 0
    switch = (InlineKeyboardButton("💬 Switch to Ask", callback_data=session_parity.session_cb(
                  bot, chat_id, sess, "rdy_ask"))
              if sess.skip_perms else
              InlineKeyboardButton("🤖 Switch to Auto", callback_data=session_parity.session_cb(
                  bot, chat_id, sess, "rdy_auto")))
    rows = [[switch, InlineKeyboardButton(
        "🧠 Model", callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_model"))]]
    if update is not None:
        rows += bot._app_button_row(update)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _render_ready_models(
    bot: TelegramBot, chat_id: int, sess: TrackedSession,
) -> tuple[str, InlineKeyboardMarkup]:
    from aipager.bot import session_parity  # local: avoids an import cycle
    rows = [[InlineKeyboardButton(
        label, callback_data=session_parity.session_cb(bot, chat_id, sess, f"rdy_m{idx}"))]
        for idx, (label, _cmd) in enumerate(MODEL_CHOICES)]
    rows.append([InlineKeyboardButton(
        "« Back", callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_back"))])
    text = (f"🧠 <b>{html_mod.escape(sess.label)}</b>: pick a model.\n\n"
            "It switches in place, the conversation is kept.")
    return text, InlineKeyboardMarkup(rows)


async def _handle_ready_callback(
    bot: TelegramBot, update: Update, query: CallbackQuery,
    session_name: str, action: str,
) -> None:
    sess = bot.registry.get(session_name)
    chat_id = calling_chat_id(update) or 0
    msg_id = getattr(getattr(query, "message", None), "message_id", None)
    if sess is None or sess.status == Status.GONE:
        await bot._safe_answer(query, "That session has ended.")
        return
    actor = _actor_id(update, query)
    if not bot._can_prompt_user(actor, chat_id):
        await bot._safe_answer(query, "You can't change this session.", show_alert=True)
        return

    async def _rerender(note: str = "") -> None:
        text, kb = render_ready(bot, update, sess, note=note)
        await _edit_at(bot, chat_id, msg_id, text, kb)

    if action in ("rdy_ask", "rdy_auto"):
        target = action == "rdy_auto"
        if target and not bot._is_admin_user(actor, chat_id):
            await bot._safe_answer(query, "Auto mode needs an admin.", show_alert=True)
            return
        if sess.skip_perms == target:
            await _rerender()
            return
        tapped = getattr(getattr(query, "message", None), "message_id", None)
        if (sess.status in (Status.BUSY, Status.INTERACTIVE)
                or not sess.tap_is_for_this_turn(tapped)):
            # A mode switch relaunches the session: never under a running
            # turn from a card that may be minutes old.
            await bot._safe_answer(
                query, f"{sess.label} is working. Switch after it finishes, or use /perms.",
                show_alert=True)
            return
        await bot._safe_answer(query, "Switching...")
        if (sess.status in (Status.BUSY, Status.INTERACTIVE)
                or not sess.tap_is_for_this_turn(tapped)):
            # A turn started while the toast went out (a queued message, a
            # background agent waking it): leave that turn alone.
            await _rerender(note=f"{sess.label} started working, so the mode "
                                 "was not switched.")
            return
        outcome = await bot._perms_switch_core(sess, target)
        if not outcome.ok:
            await _rerender(note="Couldn't switch mode"
                            + (f": {outcome.err}" if getattr(outcome, "err", "") else "."))
            return
        await _rerender()
        return

    if action == "rdy_model":
        text, kb = _render_ready_models(bot, chat_id, sess)
        await _edit_at(bot, chat_id, msg_id, text, kb)
        return

    if action == "rdy_back":
        await _rerender()
        return

    if action.startswith("rdy_m") and action[5:].isdigit():
        idx = int(action[5:])
        if not 0 <= idx < len(MODEL_CHOICES):
            await bot._safe_answer(query, "That model is no longer offered.")
            await _rerender()
            return
        label = MODEL_CHOICES[idx][0]
        resolved, err = launch.validate_model(label, MODEL_CHOICES)
        if err or not resolved:
            await bot._safe_answer(query, "That model is no longer offered.")
            await _rerender()
            return
        outcome = await bot._switch_model_core(sess, resolved, driver_user_id=actor)
        if not outcome.ok:
            await bot._safe_answer(query, outcome.detail or "Couldn't switch the model.",
                                   show_alert=True)
            await _rerender()
            return
        await bot._safe_answer(query, f"Switching to {label}")
        text, kb = render_ready(bot, update, sess, model_label=label)
        await _edit_at(bot, chat_id, msg_id, text, kb)
        return

    await bot._safe_answer(query, "Invalid callback")


# ---- /settings → 🆕 New sessions --------------------------------------

def _defaults_folders_shown(bot: TelegramBot) -> dict[int, list[str]]:
    """``bot._ns_folders_shown``: per chat, the folder list the defaults
    screen last showed. A tap resolves its index against THAT list, never
    one rebuilt at tap time (the allowed folders move as sessions start
    and age out, so an index into a fresh list can name another folder)."""
    shown = getattr(bot, "_ns_folders_shown", None)
    if shown is None:
        shown = {}
        bot._ns_folders_shown = shown
    return shown


def render_new_session_defaults(
    bot: TelegramBot, chat_id: int, *, view: str = "", viewer: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """The chat's defaults for new sessions; ``view`` "model" / "cwd"
    lists that field's choices. ``viewer`` (the person looking) is told
    when Auto does not apply to them."""
    stored = preferences.get_new_session_defaults(chat_id)
    roots = launch.allowed_roots(bot.registry, chat_id)
    if view == "model":
        rows = [[InlineKeyboardButton(
            f"{label}{' ✅' if stored.model == label else ''}",
            callback_data=f"_:set:ns:model:{idx}")]
            for idx, (label, _cmd) in enumerate(MODEL_CHOICES)]
        rows.append([InlineKeyboardButton(
            f"Default model{' ✅' if not stored.model else ''}",
            callback_data="_:set:ns:model:default")])
        rows.append([InlineKeyboardButton("« Back", callback_data="_:set:ns")])
        return ("🧠 <b>Model for new sessions</b>", InlineKeyboardMarkup(rows))
    if view == "cwd":
        _defaults_folders_shown(bot)[chat_id] = list(roots)
        rows = [[InlineKeyboardButton(
            f"{_short_path(root)}{' ✅' if stored.cwd == root else ''}",
            callback_data=f"_:set:ns:cwd:{idx}")]
            for idx, root in enumerate(roots)]
        rows.append([InlineKeyboardButton(
            f"Default folder{' ✅' if not stored.cwd else ''}",
            callback_data="_:set:ns:cwd:default")])
        rows.append([InlineKeyboardButton("« Back", callback_data="_:set:ns")])
        return ("📁 <b>Folder for new sessions</b>", InlineKeyboardMarkup(rows))

    auto = stored.mode != "ask"
    model = stored.model or "Default model"
    folder = stored.cwd if stored.cwd in roots else _project_dir()
    mode = "🤖 Auto" if auto else "💬 Ask"
    if auto and viewer is not None and not bot._is_admin_user(viewer, chat_id):
        mode = "💬 Ask for you (🤖 Auto is for admins)"
    text = (
        "🆕 <b>New sessions</b>\n\n"
        "What /new starts a session with. You can still change it on the "
        "card for one session.\n\n"
        f"Mode: {mode}\n"
        f"Model: {html_mod.escape(model)}\n"
        f"Folder: {html_mod.escape(_short_path(folder))}"
    )
    rows = [
        [InlineKeyboardButton(f"🤖 Auto{' ✅' if auto else ''}",
                              callback_data="_:set:ns:mode:auto"),
         InlineKeyboardButton(f"💬 Ask{' ✅' if not auto else ''}",
                              callback_data="_:set:ns:mode:ask")],
        [InlineKeyboardButton(f"🧠 Model: {model} ›", callback_data="_:set:ns:model")],
        [InlineKeyboardButton(f"📁 Folder: {_short_path(folder, 24)} ›",
                              callback_data="_:set:ns:cwd")],
        [InlineKeyboardButton("« Back", callback_data="_:set:back")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def handle_defaults_callback(
    bot: TelegramBot, update: Update, query: CallbackQuery, parts: list[str],
) -> None:
    """``_:set:ns...`` (``parts`` is what follows ``ns``). Viewing is
    always allowed; a change needs an admin in a group, like every other
    setting."""
    chat_id = calling_chat_id(update)
    scope = chat_id or 0
    viewer = _actor_id(update, query)

    async def _show(view: str = "") -> None:
        text, kb = render_new_session_defaults(bot, scope, view=view, viewer=viewer)
        await _edit_at(bot, scope, getattr(getattr(query, "message", None),
                                           "message_id", None), text, kb)

    if not parts:
        await _show()
        return
    if len(parts) == 1 and parts[0] in ("model", "cwd"):
        await _show(parts[0])
        return
    if len(parts) != 2:
        await bot._safe_answer(query, "Invalid callback")
        return
    if (chat_id is None or chat_id < 0) and not bot._is_admin(update):
        await bot._safe_answer(
            query, "Only an admin can change settings in this group.", show_alert=True)
        return
    field, token = parts
    value = None
    if field == "mode" and token in ("auto", "ask"):
        # Auto is the built-in default: store only a departure from it.
        value = "" if token == "auto" else "ask"
    elif field == "model":
        if token == "default":
            value = ""
        elif token.isdigit() and int(token) < len(MODEL_CHOICES):
            value = MODEL_CHOICES[int(token)][0]
    elif field == "cwd":
        shown = _defaults_folders_shown(bot).get(scope, [])
        if token == "default":
            value = ""
        elif (token.isdigit() and int(token) < len(shown)
              and shown[int(token)] in launch.allowed_roots(bot.registry, scope)):
            value = shown[int(token)]
    if value is None:
        await bot._safe_answer(query, "That choice is no longer available.")
        await _show("cwd" if field == "cwd" else "")
        return
    try:
        preferences.set_new_session_default(scope, field, value)
    except ValueError:
        await bot._safe_answer(query, "Invalid value")
        return
    await _show()


# ---- free-text capture -------------------------------------------------

async def maybe_handle_text(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE | None, text: str,
) -> bool:
    """True iff this text answered the chat's open Name card (a name, or
    one of its text steps), from the person who opened it."""
    chat_id = calling_chat_id(update)
    if chat_id is None:
        return False
    store = _pending_store(bot)
    pending = store.get(chat_id)
    if pending is None or pending["step"] not in _TEXT_CAPTURE_STEPS:
        return False
    if _actor_id(update) != pending["user_id"]:
        # Someone else talking in the same chat: their message is theirs.
        return False
    verdict = _not_for_the_card(bot, update, pending, text)
    if verdict == "close":
        close_open_card(bot, chat_id, pending["user_id"])
    if verdict:
        return False
    if _is_expired(pending):
        store.pop(chat_id, None)
        await _edit_wizard(bot, chat_id, pending, _EXPIRED_TEXT)
        return True
    _touch(pending)
    step = pending["step"]

    if step in _NAME_STEPS:
        await create_from_text(bot, update, text, pending=pending)
        return True

    if step == "opt_model_custom":
        resolved, err = launch.validate_model(text, MODEL_CHOICES)
        if err:
            pending["step"] = "opt_model_custom"
            await _edit_wizard(bot, chat_id, pending, _model_custom_prompt_text(err),
                               InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:model")]))
            return True
        pending["model"] = resolved or None
        pending["model_label"] = choice_label(resolved) or None
        await _goto_name(bot, chat_id, pending)
        return True

    if step == "opt_path_newfolder":
        roots = launch.allowed_roots(bot.registry, chat_id)
        parent = pending.get("new_folder_parent") or (roots[0] if roots else "")
        path, _existed, err = launch.create_directory(parent, text, roots)
        if err:
            await _edit_wizard(bot, chat_id, pending, _path_newfolder_prompt_text(err),
                               InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:path")]))
            return True
        pending["cwd"] = path
        await _goto_name(bot, chat_id, pending)
        return True

    return False  # pragma: no cover - _TEXT_CAPTURE_STEPS is exhaustive above


# ---- callback dispatch -------------------------------------------------

async def handle_callback(
    bot: TelegramBot, update: Update, query: CallbackQuery,
    session_name: str, action: str,
) -> bool:
    """True iff this callback is this module's: the Name card
    (``_:nw:...``), a Ready card button (``rdy_*`` on a session), or
    ``/settings → 🆕 New sessions`` (``_:set:ns...``)."""
    if session_name != "_" and action.startswith("rdy_"):
        await _handle_ready_callback(bot, update, query, session_name, action)
        return True
    if session_name == "_" and (action == "set:ns" or action.startswith("set:ns:")):
        await handle_defaults_callback(bot, update, query, action.split(":")[2:])
        return True
    if session_name != "_" or not action.startswith("nw:"):
        return False
    if action == "nw:open":
        await open_card_from_tap(bot, update, query)
        return True

    chat_id = calling_chat_id(update)
    if chat_id is None:
        await bot._safe_answer(query, "Invalid callback")
        return True

    store = _pending_store(bot)
    pending = store.get(chat_id)
    sub = action.split(":")[1:]  # drop the leading "nw"

    if pending is None:
        await bot._safe_answer(query, "This card expired.")
        msg = getattr(query, "message", None)
        await _edit_at(bot, chat_id, getattr(msg, "message_id", None), _EXPIRED_TEXT)
        return True

    actor = _actor_id(update, query)
    if actor != pending["user_id"]:
        # Before the expiry branch and before _touch(): a stranger's tap
        # must not keep someone else's card alive either.
        await bot._safe_answer(query, "This isn't your new session.", show_alert=True)
        return True

    if _is_expired(pending):
        store.pop(chat_id, None)
        await bot._safe_answer(query, "This card expired.")
        await _edit_wizard(bot, chat_id, pending, _EXPIRED_TEXT)
        return True

    tapped = getattr(getattr(query, "message", None), "message_id", None)
    if isinstance(tapped, int) and pending.get("msg_id") not in (None, tapped):
        # A tap from a card a second `/new` replaced (its keyboard is
        # stripped, but a tap can already be in flight): the chat's open
        # card is another message, whose choices this must not change.
        await bot._safe_answer(query, "That card was replaced. Use the new one.")
        return True

    _touch(pending)

    if sub == ["cancel"]:
        store.pop(chat_id, None)
        await _edit_wizard(bot, chat_id, pending, "↩️ Cancelled.")
        return True

    if sub in (["mode", "auto"], ["mode", "ask"]):
        if sub[1] == "auto" and not bot._is_admin_user(actor, chat_id):
            await bot._safe_answer(query, "Auto mode needs an admin.", show_alert=True)
            return True
        pending["skip_perms"] = sub[1] == "auto"
        await _goto_name(bot, chat_id, pending)
        return True

    if sub == ["back"]:
        await _goto_name(bot, chat_id, pending)
        return True

    if sub == ["opt", "model"]:
        pending["step"] = "opt_model"
        text, kb = _render_opt_model(pending)
        await _edit_wizard(bot, chat_id, pending, text, kb)
        return True

    if sub == ["opt", "path"]:
        pending["step"] = "opt_path"
        text, kb = _render_opt_path(bot, pending, chat_id)
        await _edit_wizard(bot, chat_id, pending, text, kb)
        return True

    if len(sub) == 2 and sub[0] == "model":
        await _handle_model_token(bot, query, chat_id, pending, sub[1])
        return True

    if len(sub) == 2 and sub[0] == "path":
        await _handle_path_token(bot, query, chat_id, pending, sub[1])
        return True

    await bot._safe_answer(query, "Invalid callback")
    return True


async def _handle_model_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    if token == "default":
        pending["model"] = None
        pending["model_label"] = None
        await _goto_name(bot, chat_id, pending)
        return
    if token == "custom":
        pending["step"] = "opt_model_custom"
        await _edit_wizard(bot, chat_id, pending, _model_custom_prompt_text(),
                           InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:model")]))
        return
    if token.isdigit():
        idx = int(token)
        if 0 <= idx < len(MODEL_CHOICES):
            label, _cmd = MODEL_CHOICES[idx]
            resolved, err = launch.validate_model(label, MODEL_CHOICES)
            if err:
                await bot._safe_answer(query, "Invalid model")
                return
            pending["model"] = resolved or None
            pending["model_label"] = label
            await _goto_name(bot, chat_id, pending)
            return
    await bot._safe_answer(query, "That model is no longer offered.")
    pending["step"] = "opt_model"
    text, kb = _render_opt_model(pending)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _handle_path_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    if token == "default":
        pending["cwd"] = None
        await _goto_name(bot, chat_id, pending)
        return
    if token == "new":
        options = pending.get("path_options") or launch.allowed_roots(bot.registry, chat_id)
        parent = pending.get("cwd") or (options[0] if options else "")
        if not parent:
            await bot._safe_answer(
                query,
                "No folder available yet - start a session from a real "
                "project folder first.",
                show_alert=True,
            )
            return
        pending["new_folder_parent"] = parent
        pending["step"] = "opt_path_newfolder"
        await _edit_wizard(bot, chat_id, pending, _path_newfolder_prompt_text(),
                           InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:path")]))
        return
    if token.isdigit():
        idx = int(token)
        options = pending.get("path_options") or []
        if 0 <= idx < len(options):
            pending["cwd"] = options[idx]
            await _goto_name(bot, chat_id, pending)
            return
    await bot._safe_answer(query, "That folder is no longer available.")
    pending["step"] = "opt_path"
    text, kb = _render_opt_path(bot, pending, chat_id)
    await _edit_wizard(bot, chat_id, pending, text, kb)
