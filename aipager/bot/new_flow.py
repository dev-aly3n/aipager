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

Pending state lives on ``bot._new_wizard_pending: dict[tuple, dict]``,
keyed ``(chat_id, user_id)``: one Name card per person in a chat (a
private chat has one person, so one card), their second bare ``/new``
replaces only their own, idle more than ``_WIZARD_TTL_SECONDS`` is gone
(checked lazily). Every step is authorized against the person acting
NOW, and a stranger's text is never consumed (see :func:`_actor_id`).
In a group the card names its owner, and another member's reply to it
is told whose it is instead of being routed (roadmap 8.86).

Callback data (sentinel ``_``, never free text):

    _:nw:cancel                  Cancel the Name card
    _:nw:mode:ask|auto|full      Switch the Name card's mode
    _:nw:back                    Back to the Name card from a picker
    _:nw:opt:model|path          Open the model / folder picker
    _:nw:model:<idx>|default|custom
    _:nw:path:<idx>|default|new
    _:sx:<i>:rdy_ask|rdy_auto|rdy_full  Ready card: switch this session's mode
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

from aipager.bot.mode_labels import (
    MODE_ASK_INSTEAD,
    MODE_AUTO_FOR_YOU,
    MODE_AUTO_INSTEAD,
    MODE_FULL_INSTEAD,
    mode_label,
    shown_label,
)
from aipager.bot.mode_switch import (
    MODE_BTN_TO_ASK,
    MODE_BTN_TO_AUTO,
    MODE_BTN_TO_FULL,
    TOAST_MODE_DIALOG_OPEN,
    TOAST_NEEDS_ADMIN_FULL,
)
from aipager import i18n, member_root, perm_modes, preferences
from aipager.bot import group_intake, kbwords, language_menu, settings_menu
from aipager.bot.sibling_text import shown
from aipager.i18n import Command, Msg, Safe, Span
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
    MODEL_CHOICES,
    RETIRED_MODEL_LABELS,
)
from aipager.miniapp import launch
from aipager.state import Status
from aipager.team import attribution_label

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

_EXPIRED_TEXT = Msg("newflow.expired", "⏱ This new-session card expired. Send /new again.",
                    note="HTML. The /new card, left unused too long")
_CLOSED_TEXT = Msg("newflow.closed", "✖️ Closed, you moved on. Send /new to start a session.",
                   note="HTML. The /new card, closed because its person did something else")

# The /new cards (HTML unless said; a card is the chat's, a pop-up the
# tapper's).
NEW_TITLE = Msg("newflow.title", "🆕 <b>New session</b>", note="The /new card's title")
NEW_TITLE_OWNER = Msg("newflow.title_owner", "🆕 <b>New session</b> ({owner})",
                      note="The /new card's title in a group: whose card it is",
                      eg={"owner": "@alice"})
NEW_GROUP_NAME_ASK = Msg("newflow.group_name_ask",
                         "Reply to this message with a name, and what to do first if you "
                         "like:", note="The /new card in a group, above two examples")
NEW_SEND_NAME = Msg("newflow.send_name", "Send a name, and what to do first if you like:",
                    note="The /new card in a private chat, above two examples")
NEW_EXAMPLE_LINE = Msg(
    "newflow.example_line", "<code>{session} fix the failing tests</code>",
    note="The second example under the /new card: a session name, then the first thing to "
         "ask Claude. Tapping it copies the whole line, which the person may send as is; "
         "{session} is the name and stays first. Keep the <code> tags around the whole line",
    eg={"session": "x1"})
NEW_HINT_FOLDER = Msg("newflow.hint_folder", "📁 Pick a project folder with 📁 Folder first.",
                      note="The /new card for someone who must pick a folder first; 📁 Folder "
                           "is the button")
NEW_SETTINGS_LINE = Msg("newflow.settings_line", "{mode} · 🧠 {model} · 📁 {folder}",
                        note="The /new card: the mode, the model and the folder it would use",
                        eg={"mode": "Auto mode", "model": "Opus", "folder": "/work/app"})
NEW_SETTINGS_NO_FOLDER = Msg("newflow.settings_no_folder",
                             "{mode} · 🧠 {model} · 📁 no project folder yet",
                             note="As newflow.settings_line, with no folder picked yet",
                             eg={"mode": "Ask mode", "model": "Opus"})
WARNING_LINE = Msg("route.warning", "⚠️ {text}",
                   note="A warning line; {text} is the warning",
                   eg={"text": "not confirmed - check the session."})
NEW_BTN_MODEL = Msg("newflow.btn.model", "🧠 Model", note="Button: pick a model")
NEW_BTN_FOLDER = Msg("newflow.btn.folder", "📁 Folder", note="Button: pick a folder")
NEW_BTN_CANCEL = Msg("newflow.btn.cancel", "✖️ Cancel", note="Button: close the /new card")
NEW_BTN_OTHER_MODEL = Msg("newflow.btn.other_model", "✏️ Other model...",
                          note="Button: type a model's name")
NEW_MODEL_PAGE = Msg("newflow.model_page", "🧠 <b>Model</b>\n\nPick a model for this session.",
                     note="Above the list of models")
NEW_BTN_NEW_FOLDER = Msg("newflow.btn.new_folder", "➕ New folder",
                         note="Button: make a new folder")
NEW_FOLDER_PAGE = Msg("newflow.folder_page", "📁 <b>Folder</b>\n\nPick where this session works.",
                      note="Above the list of folders")
NEW_OTHER_MODEL_TITLE = Msg("newflow.other_model_title", "🧠 <b>Other model</b>",
                            note="Title of the question for a model's name")
NEW_NEW_FOLDER_TITLE = Msg("newflow.new_folder_title", "📁 <b>New folder</b>",
                           note="Title of the question for a new folder's name")
NEW_PROMPT_MODEL_TYPE_EG = Msg("newflow.prompt_model_type_eg",
                               "Type the model name (e.g. {example}).",
                               note="Private chat: type a model's name; {example} is one",
                               eg={"example": "claude-opus-5"})
NEW_PROMPT_MODEL_REPLY_EG = Msg("newflow.prompt_model_reply_eg",
                                "Reply to this message with the model name (e.g. {example}).",
                                note="Group: reply with a model's name; {example} is one",
                                eg={"example": "claude-opus-5"})
NEW_PROMPT_MODEL_TYPE = Msg("newflow.prompt_model_type", "Type the model name.",
                            note="Private chat, after a wrong model name")
NEW_PROMPT_MODEL_REPLY = Msg("newflow.prompt_model_reply",
                             "Reply to this message with the model name.",
                             note="Group, after a wrong model name")
NEW_PROMPT_FOLDER_TYPE = Msg("newflow.prompt_folder_type", "Type the new folder's name.",
                             note="Private chat: type the new folder's name")
NEW_PROMPT_FOLDER_REPLY = Msg("newflow.prompt_folder_reply",
                              "Reply to this message with the new folder's name.",
                              note="Group: reply with the new folder's name")
NEW_STARTED_OVER = Msg("newflow.started_over", "↩️ Cancelled - started over.",
                       note="An older /new card, replaced by the person's newer one")
TOAST_CANT_START_HERE = Msg("toast.new_cant_start_here", "You can't start sessions here.",
                            note="Pop-up: the person may not start sessions in this chat")
#: Why a /new name was refused (more come from the Mini App's rules, English).
NAME_EMPTY = Msg("newflow.name_empty", "Session name can't be empty.",
                 note="/new with no name")
NOTE_FULL_NEEDS_ADMIN = Msg("newflow.full_needs_admin_note",
                            "Full access mode needs an admin, so this one starts in Auto mode.",
                            note="On the Ready card: someone not an admin asked for Full access "
                                 "mode (or the chat's default is Full access)")
NEW_STARTING = Msg("newflow.starting", "🚀 Starting <b>{session}</b> in {mode}…",
                   note="A session is starting, in that mode; {mode} is the mode's label, "
                        "which already says mode", eg={"session": "x1", "mode": "Auto mode"})
NEW_CREATE_FAILED = Msg("newflow.create_failed", "❌ {error}",
                        note="Starting the session failed; {error} is why (English)",
                        eg={"error": "a session named x1 already exists"})
READY = Msg("newflow.ready", "✅ <b>{session}</b> is ready", note="The Ready card's title",
            eg={"session": "x1"})
READY_SETTINGS = Msg("newflow.ready_settings", "{mode} · 🧠 {model} · 📁 <code>{folder}</code>",
                     note="The Ready card: the session's mode, model and folder",
                     eg={"mode": "Auto mode", "model": "Opus", "folder": "/work/app"})
READY_CHANGED = Msg("newflow.ready_changed", "🔁 {changed}",
                    note="The Ready card: what a tap changed and who", eg={"changed": "x"})
READY_WORKING_FIRST = Msg("newflow.working_first", "▶️ Working on your first message.",
                          note="The Ready card: the first message went with /new")
READY_JUST_SEND = Msg("newflow.just_send", "✍️ Just send a message, it goes to {session}.",
                      note="The Ready card in a private chat", eg={"session": "x1"})
READY_LATER = Msg("newflow.ready_later",
                  "Later: tap {session} on the keyboard, or reply to any {session} message.",
                  note="The Ready card: how to talk to the session later",
                  eg={"session": "x1"})
READY_MODELS = Msg("newflow.ready_models",
                   "🧠 <b>{session}</b>: pick a model.\n\nIt switches in place, the "
                   "conversation is kept.", note="Above the Ready card's model list",
                   eg={"session": "x1"})
TOAST_SESSION_ENDED = Msg("toast.session_ended", "That session has ended.", note="Pop-up")
TOAST_CANT_CHANGE = Msg("toast.new_cant_change", "You can't change this session.",
                        note="Pop-up: the person may not change this session")
TOAST_WORKING_USE_MODE = Msg("toast.new_working_use_mode",
                             "{session} is working. Switch after it finishes, or use /mode.",
                             note="Pop-up: a mode switch would restart a working session",
                             eg={"session": "x1"})
TOAST_SWITCHING = Msg("toast.new_switching", "Switching...", note="Pop-up: a switch began")
NOTE_MODE_NOT_SWITCHED = Msg("newflow.note_mode_not_switched",
                             "{session} started working, so the mode was not switched.",
                             note="On the Ready card", eg={"session": "x1"})
NOTE_MODE_FAILED = Msg("newflow.note_mode_failed", "Couldn't switch mode.",
                       note="On the Ready card")
NOTE_MODE_FAILED_ERR = Msg("newflow.note_mode_failed_err", "Couldn't switch mode: {error}",
                           note="On the Ready card; {error} is why (English)",
                           eg={"error": "dtach not installed"})
CHANGED_MODE = Msg("newflow.changed_mode", "Switched to {mode} by {person}.",
                   note="On the Ready card in a group: who switched the mode",
                   eg={"mode": "Ask mode", "person": "@bob"})
CHANGED_MODEL = Msg("newflow.changed_model", "Model switched to {model} by {person}.",
                    note="On the Ready card in a group: who switched the model",
                    eg={"model": "Opus", "person": "@bob"})
TOAST_MODEL_GONE = Msg("toast.new_model_gone", "That model is no longer offered.",
                       note="Pop-up: the tapped model was taken off the list")
TOAST_SWITCH_FAILED = Msg("toast.new_switch_failed", "Couldn't switch the model.",
                          note="Pop-up: /model could not be sent")
TOAST_SWITCHING_TO = Msg("toast.new_switching_to", "Switching to {model}",
                         note="Pop-up: the model switch was sent", eg={"model": "Opus"})
OTHERS_CARD_NAMED = Msg("newflow.others_card_named",
                        "This card is {owner}'s. Send /new for your own.",
                        note="A reply to someone else's /new card in a group",
                        eg={"owner": "@alice"})
OTHERS_CARD_SOMEONE = Msg("newflow.others_card_someone",
                          "This card is someone else's. Send /new for your own.",
                          note="As newflow.others_card_named, the owner unknown")
TOAST_NOT_YOURS = Msg("toast.new_not_yours", "This isn't your new session.",
                      note="Pop-up: a tap on someone else's /new card")
TOAST_CARD_EXPIRED = Msg("toast.new_card_expired", "This card expired.",
                         note="Pop-up: the /new card was left unused too long")
TOAST_CARD_REPLACED = Msg("toast.new_card_replaced", "That card was replaced. Use the new one.",
                          note="Pop-up: a tap on an older /new card")
CANCELLED = Msg("sess.cancelled", "↩️ Cancelled.", note="A card cancelled")
TOAST_INVALID_MODEL = Msg("toast.new_invalid_model", "Invalid model",
                          note="Pop-up: the tapped model cannot be used")
TOAST_NO_FOLDER_YET = Msg("toast.new_no_folder_yet",
                          "No folder available yet - start a session from a real project "
                          "folder first.", note="Pop-up: no folder to make a new one in")
TOAST_FOLDER_GONE = Msg("toast.new_folder_gone", "That folder is no longer available.",
                        note="Pop-up: the tapped folder is not on the list any more")



# ---- pending-state plumbing ------------------------------------------

def _pending_store(bot: TelegramBot) -> dict[tuple, dict]:
    """Lazily-initialized ``bot._new_wizard_pending`` (an instance
    attribute, not a module dict, so nothing leaks between bots), keyed
    ``(chat_id, user_id)`` (roadmap 8.86)."""
    store = getattr(bot, "_new_wizard_pending", None)
    if store is None:
        store = {}
        bot._new_wizard_pending = store
    return store


def _now() -> float:
    return time.monotonic()


def _is_expired(pending: dict) -> bool:
    return (_now() - pending.get("last_active", 0.0)) > _WIZARD_TTL_SECONDS


def _others_cards(store: dict, chat_id: int, user_id: int | None) -> list[dict]:
    """The open Name cards of everyone else in *chat_id*."""
    return [p for (chat, owner), p in store.items()
            if chat == chat_id and owner != user_id]


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
    pending = store.pop((chat_id, user_id), None)
    if pending is None:
        return
    # The pop is what matters; the edit only tells them, so nothing
    # waits on it (held like every other background send).
    from aipager.bot.notify import _BACKGROUND_TASKS  # local: import cycle
    task = asyncio.create_task(_edit_wizard(bot, chat_id, pending,
                                            i18n.for_chat(chat_id).html(_CLOSED_TEXT)))
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
        # Judged as the router reads it: "@bot /x1 fix it" is "/x1 fix it".
        stripped, mentioned = group_intake.text_without_own_mentions(bot, msg)
        if mentioned:
            text = stripped.strip()
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
    2026-09-30). Never raises, never stops the update.

    The problem report note capture (roadmap 8.112) rides on this same
    handler (PTB runs only the first matching handler of a group, so a
    second group -1 handler would never run): ``report_flow.on_update``
    goes first, on its own, and closes that capture on ANY other action,
    whatever ``_leaves_the_card_open`` says about the Name card."""
    try:
        from aipager.bot import report_flow  # local: import cycle
        report_flow.on_update(bot, update)
    except Exception:
        log.debug("new_flow: report note pre-check failed", exc_info=True)
    try:
        if not _leaves_the_card_open(bot, update):
            # Only the sender's own card and rename question, if they have
            # one open here (roadmap 8.86: the same rule closes both).
            chat_id = calling_chat_id(update)
            user_id = getattr(getattr(update, "effective_user", None), "id", None)
            close_open_card(bot, chat_id, user_id)
            if not _answers_the_rename(bot, update, chat_id, user_id):
                from aipager.bot import session_parity  # local: import cycle
                session_parity.close_rename(bot, chat_id, user_id)
    except Exception:
        log.debug("new_flow: close_if_moved_on failed", exc_info=True)


def _answers_the_rename(bot: TelegramBot, update: Update, chat_id, user_id) -> bool:
    """A tap on the Cancel of the person's OWN rename question: its
    handler closes it (and says so), so closing it here first would race
    that edit. A Cancel on someone else's question is another action."""
    query = getattr(update, "callback_query", None)
    data = getattr(query, "data", None) if query is not None else None
    if not (isinstance(data, str) and data.endswith(":rename-cancel")):
        return False
    from aipager.bot import session_parity  # local: import cycle
    own = session_parity._rename_pending_map(bot).get((chat_id, user_id))
    tapped = getattr(getattr(query, "message", None), "message_id", None)
    # No rename of theirs: nothing to close either way.
    return own is not None and own.get("msg_id") == tapped


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
    # Main-keyboard words that only look at things (new, status, the
    # navigation buttons) keep the card waiting; stop, kill and a template,
    # command or model button act on a session, so they close it.
    kind = kbwords.classify(bot, text)
    if kind == "looking":
        return "keep"
    if kind == "acting":
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
    true NOW. Auto unless the chat's default says otherwise, and never
    Full access for someone who may not use it (Auto instead, and
    ``full_refused`` says so). A stored model no longer offered (other
    than a retired pinned row, which keeps its model), or a folder no
    longer allowed, falls back to the built-in default."""
    stored = preferences.get_new_session_defaults(chat_id)
    can_full = bool(bot._may_use_full_access(user_id, chat_id))
    # Confined to the session's folder (roadmap 8.79): a stored default
    # that is the home folder, `/` or above the home folder is not theirs.
    confined = bool(bot._is_confined_user(user_id, chat_id))
    # A role without can_manage: only its members' root folder and the
    # folders inside it (None for anyone else).
    root = member_root.restricted_root(bot, user_id, chat_id)
    model = model_label = None
    # Only a label still offered, or a retired pinned row: the defaults
    # screen stores a MODEL_CHOICES label, and one withdrawn since must
    # not be passed on as if it were a custom model name.
    if stored.model and stored.model in {lbl for lbl, _cmd in MODEL_CHOICES}:
        resolved, err = launch.validate_model(stored.model, MODEL_CHOICES)
        if not err and resolved:
            model, model_label = resolved, stored.model
    elif stored.model in RETIRED_MODEL_LABELS:
        # Taken off the list since the chat chose it: still that model.
        model, model_label = RETIRED_MODEL_LABELS[stored.model], stored.model
    cwd = None
    if stored.cwd and stored.cwd in launch.allowed_roots(
            bot.registry, chat_id, confined=confined, root=root):
        cwd = stored.cwd
    if cwd is None and root is not None and root != member_root.NOWHERE:
        cwd = root
    return {
        "mode": perm_modes.narrowest_allowed(stored.mode, can_full),
        "full_refused": stored.mode == perm_modes.FULL and not can_full,
        "can_full": can_full,
        "model": model,
        "model_label": model_label,
        "cwd": cwd,
        "confined": confined,
        "root": root,
    }


def _needs_folder(pending: dict) -> bool:
    """This card's person may not start in the folder it would use now:
    no folder picked, and the default is the home folder or above it."""
    return bool(pending.get("confined") and not pending.get("cwd")
                and launch.default_folder_is_wide())


def _note_folder_choices(bot: TelegramBot, chat_id: int, pending: dict) -> None:
    """Record whether a confined person has any launch folder to pick, so
    the Name card can say what to do when none is left (roadmap 8.79)."""
    root = pending.get("root")
    pending["has_folders"] = bool(launch.allowed_roots(
        bot.registry, chat_id, confined=bool(pending.get("confined")), root=root))
    # Somewhere New folder can create one (the home folder counts).
    pending["has_parents"] = bool(launch.allowed_roots(bot.registry, chat_id, root=root))


def _settings_line(settings: dict, tr=None) -> str:
    """What a session started from this card would get (HTML), in *tr*'s
    language (English when None). A model's name is shown as it is."""
    tr = tr or i18n.get("en")
    # Each piece without its own right-to-left marks: the line has them
    # already, at its ends.
    mode = Safe(tr.bare(tr.html(mode_label(settings.get("mode") or perm_modes.AUTO))))
    model = (settings.get("model_label")
             or Safe(tr.bare(tr.html(NS_DEFAULT_MODEL))))
    if _needs_folder(settings):
        return tr.html(NEW_SETTINGS_NO_FOLDER, mode=mode, model=model)
    folder = settings.get("cwd") or _project_dir()
    return tr.html(NEW_SETTINGS_LINE, mode=mode, model=model, folder=_short_path(folder))


# ---- the Name card ------------------------------------------------------

#: The Name card's ask in a group, where a plain message does not reach a
#: bot in privacy mode but a reply to the card does (roadmap 8.84).
_GROUP_NAME_ASK = NEW_GROUP_NAME_ASK


def _owner_label(bot: TelegramBot, chat_id: int, user_id: int | None) -> str:
    """``@alice``: the card owner as the attribution helper names a
    member (their label in this chat's scope, else the allow-list); ""
    for nobody known (personal mode), so the card names no one."""
    member = None
    try:
        if getattr(bot, "scopes", None) is not None:
            member = bot._member_in_scope(bot._scope_for(chat_id), user_id)
        elif getattr(bot, "team", None) is not None:
            member = bot.team.get(user_id)
    except Exception:
        log.debug("new_flow: owner lookup failed", exc_info=True)
    return attribution_label(member) if member is not None else ""


def _error_text(tr, error) -> str:
    """A refusal shown on a card, as plain text: a keyed one (ours, or the
    Mini App's rules with their parameters) in *tr*'s language, anything
    else (a launch error) as it was said."""
    return str(shown(tr, error))


def _render_name_card(pending: dict, *, error: str = "",
                      chat_id=None, tr=None) -> tuple[str, InlineKeyboardMarkup]:
    """The Name card: the chat's (in a group it names its owner, and is
    still in the group's language)."""
    tr = tr or i18n.for_chat(chat_id)
    if group_intake.is_group_chat(chat_id) and pending.get("owner_label"):
        # Whose card it is: in a group several can be open (roadmap 8.86).
        header = tr.html(NEW_TITLE_OWNER, owner=Command(pending["owner_label"]))
    else:
        header = tr.html(NEW_TITLE)
    lines = [header, ""]
    if error:
        lines += [tr.html(WARNING_LINE, text=_error_text(tr, error)), ""]
    lines += [
        tr.html(NEW_GROUP_NAME_ASK if group_intake.is_group_chat(chat_id)
                else NEW_SEND_NAME),
        "<code>x1</code>",
        tr.html(NEW_EXAMPLE_LINE, session="x1"),
        "",
        _settings_line(pending, tr),
    ]
    if _needs_folder(pending):
        if pending.get("has_folders"):
            lines += ["", tr.html(NEW_HINT_FOLDER)]
        else:
            # The Mini App's own wording, in the chat's language.
            hint = (launch.NO_PROJECT_FOLDER if pending.get("has_parents")
                    else launch.NO_FOLDER_AT_ALL)
            lines += ["", f"📁 {html_mod.escape(shown(tr, hint))}"]
    # The modes this card does not start in, each on the mode row: Ask and
    # Auto for everyone, Full access only for an admin.
    current = pending.get("mode") or perm_modes.AUTO
    mode_row = []
    if current != perm_modes.ASK:
        mode_row.append(InlineKeyboardButton(tr.text(MODE_ASK_INSTEAD),
                                             callback_data="_:nw:mode:ask"))
    if current != perm_modes.AUTO:
        mode_row.append(InlineKeyboardButton(tr.text(MODE_AUTO_INSTEAD),
                                             callback_data="_:nw:mode:auto"))
    if current != perm_modes.FULL and pending.get("can_full"):
        mode_row.append(InlineKeyboardButton(tr.text(MODE_FULL_INSTEAD),
                                             callback_data="_:nw:mode:full"))
    row = [
        InlineKeyboardButton(tr.text(NEW_BTN_MODEL), callback_data="_:nw:opt:model"),
        InlineKeyboardButton(tr.text(NEW_BTN_FOLDER), callback_data="_:nw:opt:path"),
    ]
    rows = ([mode_row] if mode_row else []) + [
        row, [InlineKeyboardButton(tr.text(NEW_BTN_CANCEL), callback_data="_:nw:cancel")]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _back_cancel_row(back_action: str = "_:nw:back", tr=None) -> list[InlineKeyboardButton]:
    # Every caller passes a literal "_:nw:..." constant, never a session.
    tr = tr or i18n.get("en")
    return [InlineKeyboardButton(tr.text(language_menu.BACK), callback_data=back_action),
            InlineKeyboardButton(tr.text(NEW_BTN_CANCEL), callback_data="_:nw:cancel")]


def _render_opt_model(pending: dict, tr=None) -> tuple[str, InlineKeyboardMarkup]:
    tr = tr or i18n.get("en")
    current = pending.get("model_label")
    rows = []
    for idx, (label, _cmd) in enumerate(MODEL_CHOICES):
        marker = " ✅" if current == label else ""
        rows.append([InlineKeyboardButton(
            f"{label}{marker}", callback_data=f"_:nw:model:{idx}")])
    default_marker = " ✅" if not pending.get("model") else ""
    rows.append([InlineKeyboardButton(
        f"{tr.text(NS_DEFAULT_MODEL)}{default_marker}", callback_data="_:nw:model:default")])
    rows.append([InlineKeyboardButton(
        tr.text(NEW_BTN_OTHER_MODEL), callback_data="_:nw:model:custom")])
    rows.append(_back_cancel_row(tr=tr))
    return tr.html(NEW_MODEL_PAGE), InlineKeyboardMarkup(rows)


def _render_opt_path(
    bot: TelegramBot, pending: dict, chat_id: int, tr=None,
) -> tuple[str, InlineKeyboardMarkup]:
    # Fresh every render: the snapshot backing `_:nw:path:<idx>` is taken
    # here, never reused from an earlier render.
    tr = tr or i18n.for_chat(chat_id)
    confined = bool(pending.get("confined"))
    root = pending.get("root")
    roots = launch.allowed_roots(bot.registry, chat_id, confined=confined, root=root)
    pending["path_options"] = roots
    current = pending.get("cwd")
    rows = []
    for idx, folder in enumerate(roots):
        marker = " ✅" if current == folder else ""
        rows.append([InlineKeyboardButton(
            f"{_short_path(folder)}{marker}", callback_data=f"_:nw:path:{idx}")])
    if root is None and not (confined and launch.default_folder_is_wide()):
        # The daemon's own folder; for a confined person only when it is
        # not the home folder or above it (roadmap 8.79), and never for
        # someone held to a members' root folder.
        default_marker = " ✅" if not current else ""
        rows.append([InlineKeyboardButton(
            f"{tr.text(NS_DEFAULT_FOLDER)}{default_marker}", callback_data="_:nw:path:default")])
    rows.append([InlineKeyboardButton(
        tr.text(NEW_BTN_NEW_FOLDER), callback_data="_:nw:path:new")])
    rows.append(_back_cancel_row(tr=tr))
    return (tr.html(NEW_FOLDER_PAGE), InlineKeyboardMarkup(rows))


def _model_custom_prompt_text(error: str = "", chat_id=None, tr=None) -> str:
    """The custom model's question: "Type …" in a private chat; in a group,
    where a plain message does not reach a bot in privacy mode, "Reply to
    this message with …" (roadmap 8.84). One whole sentence each."""
    tr = tr or i18n.for_chat(chat_id)
    group = group_intake.is_group_chat(chat_id)
    header = tr.html(NEW_OTHER_MODEL_TITLE) + "\n\n"
    if error:
        ask = NEW_PROMPT_MODEL_REPLY if group else NEW_PROMPT_MODEL_TYPE
        return (header + tr.html(WARNING_LINE, text=_error_text(tr, error)) + "\n\n"
                + tr.html(ask))
    return header + tr.html(NEW_PROMPT_MODEL_REPLY_EG if group else NEW_PROMPT_MODEL_TYPE_EG,
                            example=Span("claude-opus-5"))


def _path_newfolder_prompt_text(error: str = "", chat_id=None, tr=None) -> str:
    """The new folder's question (as :func:`_model_custom_prompt_text`)."""
    tr = tr or i18n.for_chat(chat_id)
    ask = (NEW_PROMPT_FOLDER_REPLY if group_intake.is_group_chat(chat_id)
           else NEW_PROMPT_FOLDER_TYPE)
    header = tr.html(NEW_NEW_FOLDER_TITLE) + "\n\n"
    if error:
        return (header + tr.html(WARNING_LINE, text=_error_text(tr, error)) + "\n\n"
                + tr.html(ask))
    return header + tr.html(ask)


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
    _note_folder_choices(bot, chat_id, pending)
    text, kb = _render_name_card(pending, error=error, chat_id=chat_id)
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
    person = i18n.for_person(update)
    if chat_id is None:
        await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
        return
    if not bot._can_prompt_user(actor, chat_id):
        await bot._safe_answer(query, person.text(TOAST_CANT_START_HERE), show_alert=True)
        return
    await bot._safe_answer(query)

    async def _send(text, kb):
        return await send_text(bot._app.bot, chat_id, text, parse_mode="HTML",
                               reply_markup=kb)
    await _open_card(bot, chat_id, actor, _send)


async def _open_card(bot: TelegramBot, chat_id: int, user_id: int | None, send,
                     *, error: str = "") -> None:
    """Send the Name card with *send* and make it *user_id*'s open card
    in this chat (their second one replaces their first; anyone else's
    stays open, roadmap 8.86)."""
    store = _pending_store(bot)
    old = store.pop((chat_id, user_id), None)
    if old is not None:
        # Their second bare `/new` replaces their first: strip the old
        # card's keyboard so no tap can land on it.
        await _edit_at(bot, chat_id, old.get("msg_id"),
                       i18n.for_chat(chat_id).html(NEW_STARTED_OVER))

    pending = {
        "step": "name",
        "user_id": user_id,
        "msg_id": None,
        "path_options": [],
        "new_folder_parent": None,
        "last_active": _now(),
        **resolve_new_session_settings(bot, chat_id, user_id),
    }
    if group_intake.is_group_chat(chat_id):
        pending["owner_label"] = _owner_label(bot, chat_id, user_id)
    _note_folder_choices(bot, chat_id, pending)
    text, kb = _render_name_card(pending, error=error, chat_id=chat_id)
    sent = await send(text, kb)
    if sent is MUTED or getattr(sent, "message_id", None) is None:
        # The chat is flood-muted: the card never went out, so nothing may
        # swallow the next message as its name.
        return
    pending["msg_id"] = sent.message_id
    store[(chat_id, user_id)] = pending
    # The same person's rename left waiting for its new name would take
    # the message after this card's name: their newest question wins,
    # and says it closed. Someone else's rename in the group is theirs.
    from aipager.bot import session_parity  # local: avoids an import cycle
    session_parity.close_rename(bot, chat_id, user_id)


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
    """``name [first message...]`` → ``(name, first, force_full, error)``.

    The first word is the name, normalised and checked by
    ``launch.validate_session_name`` (the same rules everywhere); a
    leading ``!`` is the legacy "Auto" shorthand and is stripped. The rest,
    if any, is the first message, newlines flattened (an embedded newline
    would press Enter early)."""
    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return "", "", False, NAME_EMPTY
    # "x1: fix it", "x1, fix it" and a transcript's "X1." name x1: the
    # punctuation can never be part of a valid name anyway.
    raw = parts[0].rstrip(".,:;!?")
    force_full = raw.startswith("!")
    clean, err = launch.validate_session_name(raw.lstrip("!"))
    first = parts[1].strip().replace("\n", " - ") if len(parts) > 1 else ""
    return clean, first, force_full, err


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
    # The card this text answered is its owner's (always the actor:
    # maybe_handle_text only hands over their own).
    key = (chat_id, pending["user_id"] if pending is not None else actor)

    name, first, force_full, err = parse_request(text)
    if err:
        if pending is not None:
            await _goto_name(bot, chat_id, pending, error=err)
        elif chat_id is None:
            # No chat to hold a Name card for (an unscoped legacy update):
            # just say why.
            tr = i18n.for_update(update)
            await reply_text(update.message,
                             tr.html(WARNING_LINE, text=_error_text(tr, err)),
                             parse_mode="HTML")
        else:
            await start_wizard(bot, update, None, error=err)
        return

    if pending is None:
        # Started with `/new x1` while their own Name card or rename was
        # open: neither may take their next message too.
        close_open_card(bot, chat_id, actor)
        from aipager.bot import session_parity  # local: import cycle
        session_parity.close_rename(bot, chat_id, actor)
    settings = pending if pending is not None else resolve_new_session_settings(
        bot, chat_id or 0, actor)
    # Full access is re-checked here, at the moment of creation, against
    # the person creating: a stale card, a chat default of Full access or
    # a `!` from someone who may not use it gets Auto, and the Ready card
    # says why.
    may_full = bool(bot._may_use_full_access(actor, chat_id))
    wanted = perm_modes.FULL if force_full else (settings.get("mode") or perm_modes.AUTO)
    wants_full = wanted == perm_modes.FULL or bool(settings.get("full_refused"))
    mode = perm_modes.narrowest_allowed(wanted, may_full)
    # The cards are the chat's.
    tr = i18n.for_chat(chat_id) if chat_id is not None else i18n.for_update(update)
    note = tr.text(NOTE_FULL_NEEDS_ADMIN) if wants_full and not may_full else ""

    if first and await bot._refuse_admin_command(update, first):
        # A slash command its sender may not send (roadmap 8.74): the
        # session starts (or the conflict card asks about it), the command
        # is never queued.
        first = ""

    existing = bot.registry.find_by_label(name, chat_id, include_gone=True)
    if existing is not None and (
        existing.status != Status.GONE or existing.claude_session_id
    ):
        if pending is not None:
            store.pop(key, None)
        # The Name card itself becomes the conflict card (one message per
        # flow); `/new x1` gets it as the reply.
        await bot._send_new_conflict_prompt(
            update=update, existing=existing, prompt=first,
            mode=mode, model=settings.get("model"),
            cwd=settings.get("cwd"),
            edit_msg_id=pending.get("msg_id") if pending is not None else None)
        return

    launching = tr.html(NEW_STARTING, session=name, mode=Safe(tr.html(mode_label(mode))))
    sent = None
    if pending is not None:
        store.pop(key, None)
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
        name, scope_chat_id=chat_id, mode=mode,
        cwd=settings.get("cwd") or None, driver_user_id=actor,
        model=settings.get("model") or None,
    )
    if not session_name:
        if pending is not None and store.get(key) is None:
            # Retry-able: the Name card comes back with its choices kept,
            # unless a newer card opened while this one was launching.
            pending["msg_id"] = msg_id
            _touch(pending)
            store[key] = pending
            await _goto_name(bot, chat_id, pending, error=err)
        else:
            await _show(tr.html(NEW_CREATE_FAILED, error=_error_text(tr, err)))
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
    log.info("Launched session %s (%s, first message=%s)", name, mode, bool(first))


# ---- the Ready card ----------------------------------------------------

def render_ready(
    bot: TelegramBot, update: Update | None, sess: TrackedSession, *,
    model_label: str | None = None, first_message: bool = False, note: str = "",
    changed: str = "", tr=None, default_saved: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Where every way of starting a session ends: what it got, and what
    to do next, in *tr*'s language (the chat's when None). ``changed``
    (plain text): what a tap on the card just changed and who did it,
    shown in a group (roadmap 8.91c); ``note`` (plain text) a warning;
    ``default_saved``: a model switch from the card that Claude Code also
    made the default for new sessions."""
    from aipager.bot import session_parity  # local: avoids an import cycle
    chat_id = sess.scope_chat_id or (calling_chat_id(update) if update else 0) or 0
    tr = tr or i18n.for_chat(chat_id)
    # Without their own right-to-left marks (the line has them).
    mode = Safe(tr.bare(shown_label(tr, sess)))
    # The model just picked wins; then, while a switch is not yet
    # confirmed, the one it was switched to; then what the statusline
    # reports; then what it was started with. A model's name as it is.
    chosen = choice_label(sess.launch_model)
    model = (model_label
             or (chosen if sess.model_switch_pending() else "")
             or sess.model_name or chosen
             or Safe(tr.bare(tr.html(NS_DEFAULT_MODEL))))
    folder = sess.cwd or _project_dir()
    lines = [
        tr.html(READY, session=sess.label),
        tr.html(READY_SETTINGS, mode=mode, model=model, folder=_short_path(folder)),
    ]
    if changed:
        lines.append(tr.html(READY_CHANGED, changed=changed))
    if default_saved:
        from aipager.bot.session_ops import MODEL_DEFAULT_SAVED  # local: import cycle
        lines.append(tr.html(MODEL_DEFAULT_SAVED))
    if note:
        lines.append(tr.html(WARNING_LINE, text=note))
    lines.append("")
    if first_message:
        lines.append(tr.html(READY_WORKING_FIRST))
    else:
        lines.append(group_intake.talk_hint(
            bot, chat_id, html_mod.escape(sess.label),
            dm=tr.html(READY_JUST_SEND, session=sess.label), tr=tr, raw_label=sess.label))
    lines.append(tr.html(READY_LATER, session=sess.label))
    # A switch to each other mode; Full access only when the person who
    # started the session may use it (the tap re-checks the tapper).
    current = sess.current_mode()
    may_full = bool(bot._may_use_full_access(sess.created_by_user_id, chat_id))
    switches = []
    if current != perm_modes.ASK:
        switches.append(InlineKeyboardButton(
            tr.text(MODE_BTN_TO_ASK),
            callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_ask")))
    if current != perm_modes.AUTO:
        switches.append(InlineKeyboardButton(
            tr.text(MODE_BTN_TO_AUTO),
            callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_auto")))
    if current != perm_modes.FULL and may_full:
        switches.append(InlineKeyboardButton(
            tr.text(MODE_BTN_TO_FULL),
            callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_full")))
    rows = [switches, [InlineKeyboardButton(
        tr.text(NEW_BTN_MODEL),
        callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_model"))]]
    if update is not None:
        rows += bot._app_button_row(update)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _render_ready_models(
    bot: TelegramBot, chat_id: int, sess: TrackedSession, tr=None,
) -> tuple[str, InlineKeyboardMarkup]:
    from aipager.bot import session_parity  # local: avoids an import cycle
    tr = tr or i18n.for_chat(chat_id)
    rows = [[InlineKeyboardButton(
        label, callback_data=session_parity.session_cb(bot, chat_id, sess, f"rdy_m{idx}"))]
        for idx, (label, _cmd) in enumerate(MODEL_CHOICES)]
    rows.append([InlineKeyboardButton(
        tr.text(language_menu.BACK),
        callback_data=session_parity.session_cb(bot, chat_id, sess, "rdy_back"))])
    return tr.html(READY_MODELS, session=sess.label), InlineKeyboardMarkup(rows)


#: The Ready card follow-ups in flight (kept so they are not collected).
_READY_FOLLOW_TASKS: set = set()
#: How many Ready cards' tap counts are kept (a follow-up lasts ~16 s).
_READY_TAPS_KEPT = 256


def _count_ready_tap(bot: TelegramBot, chat_id: int, msg_id) -> None:
    """One more tap on the Ready card *msg_id*: a follow-up edit started
    before it no longer knows what the card shows."""
    taps = getattr(bot, "_ready_card_taps", None)
    if taps is None:
        taps = bot._ready_card_taps = {}
    taps[(chat_id, msg_id)] = taps.get((chat_id, msg_id), 0) + 1
    while len(taps) > _READY_TAPS_KEPT:   # the oldest cards first
        taps.pop(next(iter(taps)))


def _ready_taps(bot: TelegramBot, chat_id: int, msg_id) -> int:
    return (getattr(bot, "_ready_card_taps", None) or {}).get((chat_id, msg_id), 0)


async def _say_default_saved(
    bot: TelegramBot, update: Update, chat_id: int, msg_id, sess: TrackedSession,
    previous: str, changed: str, taps: int, actor: int | None = None,
) -> None:
    """After a model switch from the Ready card: once the session reports it,
    the card says when Claude Code also made it the default for new
    sessions, as the chat's /model reply does. Left alone when the card was
    tapped again meanwhile (it may show the model list or another change),
    or the session ended. A member's switch puts the old default back and
    says nothing (``session_ops.settle_default``). Never raises."""
    from aipager.bot.session_ops import await_model_change, settle_default
    try:
        new_model = await await_model_change(sess, previous)
        # Also for a switch the statusline did not confirm: a member's must
        # not stay the owner's default (session_ops.settle_default).
        saved = await settle_default(bot, sess, actor, chat_id, confirmed=bool(new_model))
        if not new_model or not saved:
            return
        if _ready_taps(bot, chat_id, msg_id) != taps or sess.status is Status.GONE:
            return
        text, kb = render_ready(bot, update, sess, changed=changed, default_saved=True)
        await _edit_at(bot, chat_id, msg_id, text, kb)
    except Exception:
        log.debug("[%s] the Ready card's default note failed", sess.label, exc_info=True)


async def _handle_ready_callback(
    bot: TelegramBot, update: Update, query: CallbackQuery,
    session_name: str, action: str,
) -> None:
    sess = bot.registry.get(session_name)
    chat_id = calling_chat_id(update) or 0
    msg_id = getattr(getattr(query, "message", None), "message_id", None)
    # The card is the chat's; a pop-up is the tapper's.
    tr = i18n.for_chat(chat_id)
    person = i18n.for_person(update)
    if sess is None or sess.status == Status.GONE:
        await bot._safe_answer(query, person.text(TOAST_SESSION_ENDED))
        return
    actor = _actor_id(update, query)
    if not bot._can_prompt_user(actor, chat_id):
        await bot._safe_answer(query, person.text(TOAST_CANT_CHANGE), show_alert=True)
        return
    _count_ready_tap(bot, chat_id, msg_id)

    async def _rerender(note: str = "", changed: str = "") -> None:
        text, kb = render_ready(bot, update, sess, note=note, changed=changed)
        await _edit_at(bot, chat_id, msg_id, text, kb)

    # Who tapped, named on what the tap changed in a group (8.91c).
    by = bot._actor_label(actor, chat_id, getattr(query, "from_user", None))

    if action in ("rdy_ask", "rdy_auto", "rdy_full"):
        target = action.removeprefix("rdy_")
        if target == perm_modes.FULL and not bot._may_use_full_access(actor, chat_id):
            await bot._safe_answer(query, person.text(TOAST_NEEDS_ADMIN_FULL), show_alert=True)
            return
        if bot._already_in_mode(sess, target):
            await _rerender()
            return
        tapped = getattr(getattr(query, "message", None), "message_id", None)
        # Any mode switch (the in-place Ask <-> Auto one included): a role
        # without can_manage only for a session inside its members' root
        # folder.
        refusal = member_root.relaunch_refusal(bot, actor, chat_id, sess.cwd)
        if refusal:
            await bot._safe_answer(query, shown(person, refusal), show_alert=True)
            return
        if not sess.mode_switch_restarts(target):
            # Ask <-> Auto: Shift+Tab in the terminal, no restart, so a
            # running turn is fine; an open prompt would read the keys.
            if sess.dialog_is_open():
                await bot._safe_answer(
                    query, person.text(TOAST_MODE_DIALOG_OPEN, session=sess.label),
                    show_alert=True)
                return
            await bot._safe_answer(query, person.text(TOAST_SWITCHING))
            outcome = await bot._mode_switch_core(sess, target)
            if outcome.reason == "sent":
                await _rerender()
                from aipager.bot import mode_switch  # local: import cycle
                mode_switch.attach_reply(sess, chat_id, msg_id, "ready", by)
            elif outcome.reason == "dialog_open":
                await _rerender(note=tr.text(TOAST_MODE_DIALOG_OPEN, session=sess.label))
            elif outcome.reason == "parked":
                from aipager.bot.session_ops import BG_TOAST_REFUSED_PARKED  # import cycle
                await _rerender(note=tr.text(BG_TOAST_REFUSED_PARKED, session=sess.label))
            elif outcome.ok:
                await _rerender()
            else:
                err = getattr(outcome, "err", "")
                await _rerender(note=(tr.text(NOTE_MODE_FAILED_ERR, error=err) if err
                                      else tr.text(NOTE_MODE_FAILED)))
            return
        if (sess.status in (Status.BUSY, Status.INTERACTIVE)
                or not sess.tap_is_for_this_turn(tapped)):
            # Into or out of Full access relaunches the session: never
            # under a running turn from a card that may be minutes old.
            await bot._safe_answer(
                query, person.text(TOAST_WORKING_USE_MODE, session=sess.label),
                show_alert=True)
            return
        await bot._safe_answer(query, person.text(TOAST_SWITCHING))
        if (sess.status in (Status.BUSY, Status.INTERACTIVE)
                or not sess.tap_is_for_this_turn(tapped)):
            # A turn started while the toast went out (a queued message, a
            # background agent waking it): leave that turn alone.
            await _rerender(note=tr.text(NOTE_MODE_NOT_SWITCHED, session=sess.label))
            return
        outcome = await bot._mode_switch_core(sess, target)
        if getattr(outcome, "reason", "") == "bg_busy":
            # A busy Claude Code background job in the way of the restart:
            # the card becomes the question, with its buttons.
            from aipager.bg_text import BG_BUSY_QUESTION
            from aipager.bot import session_parity  # import cycle
            await _edit_at(bot, chat_id, msg_id,
                           tr.html(BG_BUSY_QUESTION, session=sess.label),
                           session_parity.bg_busy_keyboard(bot, chat_id, sess, tr,
                                                           mode=target))
            return
        if not outcome.ok:
            from aipager.bg_text import bg_outcome_text
            err = getattr(outcome, "err", "")
            bg = bg_outcome_text(tr, outcome, sess.label)
            await _rerender(note=bg or (tr.text(NOTE_MODE_FAILED_ERR, error=err) if err
                                        else tr.text(NOTE_MODE_FAILED)))
            return
        await _rerender(changed=(tr.text(CHANGED_MODE, mode=Safe(tr.text(mode_label(target))),
                                         person=Command(by))
                                 if by else ""))
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
            await bot._safe_answer(query, person.text(TOAST_MODEL_GONE))
            await _rerender()
            return
        label = MODEL_CHOICES[idx][0]
        resolved, err = launch.validate_model(label, MODEL_CHOICES)
        if err or not resolved:
            await bot._safe_answer(query, person.text(TOAST_MODEL_GONE))
            await _rerender()
            return
        outcome = await bot._switch_model_core(sess, resolved, driver_user_id=actor)
        if getattr(outcome, "reason", "") == "parked":
            from aipager.bot.session_ops import BG_TOAST_REFUSED_PARKED  # import cycle
            await bot._safe_answer(
                query, person.text(BG_TOAST_REFUSED_PARKED, session=sess.label),
                show_alert=True)
            await _rerender()
            return
        if not outcome.ok:
            # A refusal by its code, in the tapper's language (the Mini App
            # keeps the English detail); any other detail as said.
            from aipager.bot.session_ops import model_refusal_text  # local: import cycle
            await bot._safe_answer(
                query, (model_refusal_text(person, outcome.reason, outcome.detail)
                        if outcome.detail else person.text(TOAST_SWITCH_FAILED)),
                show_alert=True)
            await _rerender()
            return
        await bot._safe_answer(query, person.text(TOAST_SWITCHING_TO, model=label))
        changed = tr.text(CHANGED_MODEL, model=label, person=Command(by)) if by else ""
        text, kb = render_ready(bot, update, sess, model_label=label, changed=changed)
        await _edit_at(bot, chat_id, msg_id, text, kb)
        # Started even with no statusline yet: a member's switch must not
        # stay the owner's default whether or not it is confirmed.
        task = asyncio.create_task(_say_default_saved(
            bot, update, chat_id, msg_id, sess, outcome.previous_model, changed,
            _ready_taps(bot, chat_id, msg_id), actor))
        _READY_FOLLOW_TASKS.add(task)
        task.add_done_callback(_READY_FOLLOW_TASKS.discard)
        return

    await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))


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


NS_TITLE = Msg(
    "set.text.ns_root",
    "🆕 <b>New sessions</b>\n\nWhat /new starts a session with. You can still change it "
    "on the card for one session.\n\nPermissions: {mode}\nModel: {model}\nFolder: {folder}",
    note="/settings > New sessions: the defaults /new uses. {mode} is the Ask, Auto or Full "
         "access mode label (it already says mode) or modecard.auto_for_you, {model} a model name "
         "or Default model, {folder} a path",
    eg={"mode": "Auto mode", "model": "Opus 5.5", "folder": "~/work/app"})
NS_MODEL_TITLE = Msg("set.text.ns_model_title", "🧠 <b>Model for new sessions</b>",
                     note="Above the model list of /settings > New sessions")
NS_FOLDER_TITLE = Msg("set.text.ns_folder_title", "📁 <b>Folder for new sessions</b>",
                      note="Above the folder list of /settings > New sessions")
NS_OLDER_MODEL = Msg("set.opt.ns_older_model", "{model} (older model)",
                     note="A model no longer on the list but still chosen; {model} is "
                          "its name",
                     eg={"model": "Opus 4.7"})
NS_DEFAULT_MODEL = Msg("set.opt.ns_default_model", "Default model",
                       note="The model Claude Code picks itself")
NS_DEFAULT_FOLDER = Msg("set.opt.ns_default_folder", "Default folder",
                        note="The folder aipager starts sessions in when none is chosen")
NS_MODEL_BUTTON = Msg("set.btn.ns_model", "🧠 Model: {model} ›",
                      note="Opens the model list; {model} is the current choice",
                      eg={"model": "Default model"})
NS_FOLDER_BUTTON = Msg("set.btn.ns_folder", "📁 Folder: {folder} ›",
                       note="Opens the folder list; {folder} is a shortened path",
                       eg={"folder": "~/work/app"})
CHOICE_GONE = Msg("toast.choice_gone", "That choice is no longer available.",
                  note="Pop-up: the tapped model or folder is not on the list any more")


def render_new_session_defaults(
    bot: TelegramBot, chat_id: int, *, view: str = "", viewer: int | None = None,
    tr=None,
) -> tuple[str, InlineKeyboardMarkup]:
    """The chat's defaults for new sessions; ``view`` "model" / "cwd"
    lists that field's choices. ``viewer`` (the person looking) is told
    when Full access does not apply to them. In *tr*'s language (the chat's when
    None); model names and paths are shown as they are."""
    if tr is None:
        tr = i18n.for_chat(chat_id)
    back = tr.text(language_menu.BACK)
    stored = preferences.get_new_session_defaults(chat_id)
    # The chat's 🎨 Appearance highlight, for the ✅ button only.
    from aipager.bot import appearance
    on = appearance.choice_style(chat_id)

    def _hl(current: bool) -> str | None:
        return on if current else None
    # A viewer held to a members' root folder sees only it and the folders
    # inside it.
    root = (member_root.restricted_root(bot, viewer, chat_id)
            if viewer is not None else None)
    roots = launch.allowed_roots(
        bot.registry, chat_id,
        confined=viewer is not None and bool(bot._is_confined_user(viewer, chat_id)),
        root=root)
    if view == "model":
        rows = [[InlineKeyboardButton(
            f"{label}{' ✅' if stored.model == label else ''}",
            callback_data=f"_:set:ns:model:{idx}", style=_hl(stored.model == label))]
            for idx, (label, _cmd) in enumerate(MODEL_CHOICES)]
        if (stored.model in RETIRED_MODEL_LABELS
                and stored.model not in {lbl for lbl, _cmd in MODEL_CHOICES}):
            # Taken off the list since the chat chose it, and still what
            # /new starts with: shown as the choice, a tap only re-shows
            # the list (pick another row to change it).
            rows.append([InlineKeyboardButton(
                f"{tr.text(NS_OLDER_MODEL, model=stored.model)} ✅",
                callback_data="_:set:ns:model", style=on)])
        rows.append([InlineKeyboardButton(
            f"{tr.text(NS_DEFAULT_MODEL)}{' ✅' if not stored.model else ''}",
            callback_data="_:set:ns:model:default", style=_hl(not stored.model))])
        rows.append([InlineKeyboardButton(back, callback_data="_:set:ns")])
        return (tr.html(NS_MODEL_TITLE), InlineKeyboardMarkup(rows))
    if view == "cwd":
        _defaults_folders_shown(bot)[chat_id] = list(roots)
        rows = [[InlineKeyboardButton(
            f"{_short_path(folder)}{' ✅' if stored.cwd == folder else ''}",
            callback_data=f"_:set:ns:cwd:{idx}", style=_hl(stored.cwd == folder))]
            for idx, folder in enumerate(roots)]
        rows.append([InlineKeyboardButton(
            f"{tr.text(NS_DEFAULT_FOLDER)}{' ✅' if not stored.cwd else ''}",
            callback_data="_:set:ns:cwd:default", style=_hl(not stored.cwd))])
        rows.append([InlineKeyboardButton(back, callback_data="_:set:ns")])
        return (tr.html(NS_FOLDER_TITLE), InlineKeyboardMarkup(rows))

    # A model's name as written; "Default model" is words.
    model_html = (stored.model if stored.model
                  else Safe(tr.html(NS_DEFAULT_MODEL)))
    model_text = (stored.model if stored.model
                  else Safe(tr.text(NS_DEFAULT_MODEL)))
    folder = stored.cwd if stored.cwd in roots else (
        roots[0] if root is not None and roots else _project_dir())
    mode = mode_label(stored.mode)
    if (stored.mode == perm_modes.FULL and viewer is not None
            and not bot._may_use_full_access(viewer, chat_id)):
        mode = MODE_AUTO_FOR_YOU
    text = tr.html(NS_TITLE, mode=Safe(tr.html(mode)), model=model_html,
                   folder=_short_path(folder))
    # One mode per row (a label is too wide for a third of a row); Auto,
    # the default, first.
    def _mark(choice: str) -> str:
        return f"{tr.text(mode_label(choice))}{' ✅' if stored.mode == choice else ''}"

    # Full access is offered to whoever may use it (_may_use_full_access).
    # A private chat's screen is its one person's, so someone who may not
    # is not shown the row (unless it is what the chat already stores); a
    # group's screen is shared by its members, so the row stays and a tap
    # by someone who may not is refused (handle_defaults_callback).
    offer_full = (stored.mode == perm_modes.FULL or viewer is None
                  or not isinstance(chat_id, int) or chat_id < 0
                  or bool(bot._may_use_full_access(viewer, chat_id)))
    rows = [
        [InlineKeyboardButton(_mark(perm_modes.AUTO), callback_data="_:set:ns:mode:auto",
                              style=_hl(stored.mode == perm_modes.AUTO))],
        [InlineKeyboardButton(_mark(perm_modes.ASK), callback_data="_:set:ns:mode:ask",
                              style=_hl(stored.mode == perm_modes.ASK))],
    ]
    if offer_full:
        rows.append([InlineKeyboardButton(_mark(perm_modes.FULL),
                                          callback_data="_:set:ns:mode:full",
                                          style=_hl(stored.mode == perm_modes.FULL))])
    rows += [
        [InlineKeyboardButton(tr.text(NS_MODEL_BUTTON, model=model_text),
                              callback_data="_:set:ns:model")],
        [InlineKeyboardButton(tr.text(NS_FOLDER_BUTTON, folder=_short_path(folder, 24)),
                              callback_data="_:set:ns:cwd")],
        [InlineKeyboardButton(back, callback_data="_:set:back")],
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

    # The page is the chat's; a pop-up is the tapper's.
    tr = i18n.for_chat(scope)
    person = i18n.for_person(update)

    async def _show(view: str = "", by: str = "") -> None:
        text, kb = render_new_session_defaults(bot, scope, view=view, viewer=viewer, tr=tr)
        if by:
            text += "\n\n" + tr.html(settings_menu.CHANGED_BY, person=Command(by))
        await _edit_at(bot, scope, getattr(getattr(query, "message", None),
                                           "message_id", None), text, kb)

    if not parts:
        await _show()
        return
    if len(parts) == 1 and parts[0] in ("model", "cwd"):
        await _show(parts[0])
        return
    if len(parts) != 2:
        await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
        return
    if (chat_id is None or chat_id < 0) and not bot._is_admin(update):
        await bot._safe_answer(
            query, person.text(language_menu.ADMIN_ONLY), show_alert=True)
        return
    field, token = parts
    value = None
    if field == "mode" and token == perm_modes.FULL and not bot._may_use_full_access(viewer, chat_id):
        # Only an admin may make Full access what /new starts with (it
        # would only give them Auto anyway, but it says Full access).
        await bot._safe_answer(query, person.text(TOAST_NEEDS_ADMIN_FULL), show_alert=True)
        return
    if field == "mode" and token in perm_modes.MODES:
        value = token
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
              and shown[int(token)] in launch.allowed_roots(
                  bot.registry, scope,
                  confined=bool(bot._is_confined_user(viewer, scope)),
                  root=member_root.restricted_root(bot, viewer, scope))):
            value = shown[int(token)]
    if value is None:
        await bot._safe_answer(query, person.text(CHOICE_GONE))
        await _show("cwd" if field == "cwd" else "")
        return
    try:
        preferences.set_new_session_default(scope, field, value)
    except ValueError:
        await bot._safe_answer(query, person.text(language_menu.INVALID_VALUE))
        return
    # In a group, who changed it (roadmap 8.91c); a DM is as before.
    await _show(by=bot._actor_label(viewer, chat_id, getattr(query, "from_user", None)))


# ---- free-text capture -------------------------------------------------

async def _refuse_reply_to_others_card(
    bot: TelegramBot, update: Update, store: dict, chat_id: int, actor: int | None,
) -> bool:
    """In a group, a reply to another member's open Name card is not
    theirs to answer and not a prompt either: say whose card it is
    (roadmap 8.86). True iff it was that. It is not the answer to the
    replier's own card or rename either, so those close (moved on)."""
    if not group_intake.is_group_chat(chat_id):
        return False
    reply_to = getattr(update.message, "reply_to_message", None)
    replied = getattr(reply_to, "message_id", None) if reply_to is not None else None
    if not isinstance(replied, int):
        return False
    for card in _others_cards(store, chat_id, actor):
        if card.get("msg_id") == replied:
            owner = card.get("owner_label") or _owner_label(bot, chat_id, card.get("user_id"))
            close_open_card(bot, chat_id, actor)
            from aipager.bot import session_parity  # local: import cycle
            session_parity.close_rename(bot, chat_id, actor)
            tr = i18n.for_update(update)
            await reply_text(update.message,
                             tr.text(OTHERS_CARD_NAMED, owner=Command(owner)) if owner
                             else tr.text(OTHERS_CARD_SOMEONE))
            return True
    return False


async def maybe_handle_text(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE | None, text: str,
) -> bool:
    """True iff this text answered the sender's own open Name card (a
    name, or one of its text steps), or, in a group, replied to someone
    else's (told whose it is, routed nowhere)."""
    chat_id = calling_chat_id(update)
    if chat_id is None:
        return False
    store = _pending_store(bot)
    actor = _actor_id(update)
    if await _refuse_reply_to_others_card(bot, update, store, chat_id, actor):
        return True
    pending = store.get((chat_id, actor))
    if pending is None or pending["step"] not in _TEXT_CAPTURE_STEPS:
        return False
    verdict = _not_for_the_card(bot, update, pending, text)
    if verdict == "close":
        close_open_card(bot, chat_id, actor)
    if verdict:
        return False
    if _is_expired(pending):
        store.pop((chat_id, actor), None)
        await _edit_wizard(bot, chat_id, pending, i18n.for_chat(chat_id).html(_EXPIRED_TEXT))
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
            tr = i18n.for_chat(chat_id)
            await _edit_wizard(bot, chat_id, pending,
                               _model_custom_prompt_text(err, chat_id, tr),
                               InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:model", tr)]))
            return True
        pending["model"] = resolved or None
        pending["model_label"] = choice_label(resolved) or None
        await _goto_name(bot, chat_id, pending)
        return True

    if step == "opt_path_newfolder":
        # Every folder this chat works in may be the parent, the home
        # folder included; a confined person's result must still be a
        # project folder (roadmap 8.79).
        roots = launch.allowed_roots(bot.registry, chat_id, root=pending.get("root"))
        parent = pending.get("new_folder_parent") or (roots[0] if roots else "")
        path, existed, err = launch.create_directory(
            parent, text, roots, confined=bool(pending.get("confined")))
        if err:
            tr = i18n.for_chat(chat_id)
            await _edit_wizard(bot, chat_id, pending,
                               _path_newfolder_prompt_text(err, chat_id, tr),
                               InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:path", tr)]))
            return True
        if not existed:
            launch.remember_created(chat_id, path)
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
    person = i18n.for_person(update)
    if chat_id is None:
        await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
        return True
    # The card is the chat's (in a group too: only pop-ups are the tapper's).
    tr = i18n.for_chat(chat_id)

    store = _pending_store(bot)
    actor = _actor_id(update, query)
    pending = store.get((chat_id, actor))
    sub = action.split(":")[1:]  # drop the leading "nw"
    tapped = getattr(getattr(query, "message", None), "message_id", None)

    others = _others_cards(store, chat_id, actor)
    if isinstance(tapped, int):
        theirs = any(p.get("msg_id") == tapped for p in others)
    else:         # no message to tell by: anyone else's card counts
        theirs = pending is None and bool(others)
    if theirs:
        # Someone else's card (roadmap 8.86: one per person). Before the
        # expiry branch and before _touch(): a stranger's tap must not
        # keep someone else's card alive either.
        await bot._safe_answer(query, person.text(TOAST_NOT_YOURS), show_alert=True)
        return True

    if pending is None:
        await bot._safe_answer(query, person.text(TOAST_CARD_EXPIRED))
        await _edit_at(bot, chat_id, tapped, tr.html(_EXPIRED_TEXT))
        return True

    if _is_expired(pending):
        store.pop((chat_id, actor), None)
        await bot._safe_answer(query, person.text(TOAST_CARD_EXPIRED))
        await _edit_wizard(bot, chat_id, pending, tr.html(_EXPIRED_TEXT))
        return True

    if isinstance(tapped, int) and pending.get("msg_id") not in (None, tapped):
        # A tap from a card a second `/new` replaced (its keyboard is
        # stripped, but a tap can already be in flight): the chat's open
        # card is another message, whose choices this must not change.
        await bot._safe_answer(query, person.text(TOAST_CARD_REPLACED))
        return True

    _touch(pending)

    if sub == ["cancel"]:
        store.pop((chat_id, actor), None)
        await _edit_wizard(bot, chat_id, pending, tr.html(CANCELLED))
        return True

    if len(sub) == 2 and sub[0] == "mode" and sub[1] in perm_modes.MODES:
        if sub[1] == perm_modes.FULL and not bot._may_use_full_access(actor, chat_id):
            await bot._safe_answer(query, person.text(TOAST_NEEDS_ADMIN_FULL), show_alert=True)
            return True
        pending["mode"] = sub[1]
        # A choice made on the card: nothing was refused any more.
        pending["full_refused"] = False
        await _goto_name(bot, chat_id, pending)
        return True

    if sub == ["back"]:
        await _goto_name(bot, chat_id, pending)
        return True

    if sub == ["opt", "model"]:
        pending["step"] = "opt_model"
        text, kb = _render_opt_model(pending, tr)
        await _edit_wizard(bot, chat_id, pending, text, kb)
        return True

    if sub == ["opt", "path"]:
        pending["step"] = "opt_path"
        text, kb = _render_opt_path(bot, pending, chat_id, tr)
        await _edit_wizard(bot, chat_id, pending, text, kb)
        return True

    if len(sub) == 2 and sub[0] == "model":
        await _handle_model_token(bot, query, chat_id, pending, sub[1])
        return True

    if len(sub) == 2 and sub[0] == "path":
        await _handle_path_token(bot, query, chat_id, pending, sub[1])
        return True

    await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
    return True


def _tapper(query):
    """What only the tapper sees: their language."""
    from types import SimpleNamespace
    return i18n.for_person(SimpleNamespace(callback_query=query))


async def _handle_model_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    if token == "default":
        pending["model"] = None
        pending["model_label"] = None
        await _goto_name(bot, chat_id, pending)
        return
    tr = i18n.for_chat(chat_id)
    if token == "custom":
        pending["step"] = "opt_model_custom"
        await _edit_wizard(bot, chat_id, pending,
                           _model_custom_prompt_text(chat_id=chat_id, tr=tr),
                           InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:model", tr)]))
        return
    if token.isdigit():
        idx = int(token)
        if 0 <= idx < len(MODEL_CHOICES):
            label, _cmd = MODEL_CHOICES[idx]
            resolved, err = launch.validate_model(label, MODEL_CHOICES)
            if err:
                await bot._safe_answer(query, _tapper(query).text(TOAST_INVALID_MODEL))
                return
            pending["model"] = resolved or None
            pending["model_label"] = label
            await _goto_name(bot, chat_id, pending)
            return
    await bot._safe_answer(query, _tapper(query).text(TOAST_MODEL_GONE))
    pending["step"] = "opt_model"
    text, kb = _render_opt_model(pending, tr)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _handle_path_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    root = pending.get("root")
    if token == "default" and root is not None:
        # Held to a members' root folder: "default" is the root itself.
        if root == member_root.NOWHERE:
            await bot._safe_answer(query, _tapper(query).text(member_root.NO_ROOT),
                                   show_alert=True)
            return
        pending["cwd"] = root
        await _goto_name(bot, chat_id, pending)
        return
    if token == "default":
        if pending.get("confined") and launch.default_folder_is_wide():
            await bot._safe_answer(query, _tapper(query).text(launch.WIDE_FOLDER_REFUSAL),
                                   show_alert=True)
            return
        pending["cwd"] = None
        await _goto_name(bot, chat_id, pending)
        return
    if token == "new":
        options = pending.get("path_options") or launch.allowed_roots(
            bot.registry, chat_id, confined=bool(pending.get("confined")), root=root)
        # A confined person with no project folder yet creates one in a
        # folder they may not start in themselves (the home folder).
        parents = options or launch.allowed_roots(bot.registry, chat_id, root=root)
        parent = pending.get("cwd") or (parents[0] if parents else "")
        if not parent:
            await bot._safe_answer(query, _tapper(query).text(TOAST_NO_FOLDER_YET),
                                   show_alert=True)
            return
        pending["new_folder_parent"] = parent
        pending["step"] = "opt_path_newfolder"
        tr = i18n.for_chat(chat_id)
        await _edit_wizard(bot, chat_id, pending,
                           _path_newfolder_prompt_text(chat_id=chat_id, tr=tr),
                           InlineKeyboardMarkup([_back_cancel_row("_:nw:opt:path", tr)]))
        return
    if token.isdigit():
        idx = int(token)
        options = pending.get("path_options") or []
        if 0 <= idx < len(options):
            pending["cwd"] = options[idx]
            await _goto_name(bot, chat_id, pending)
            return
    await bot._safe_answer(query, _tapper(query).text(TOAST_FOLDER_GONE))
    pending["step"] = "opt_path"
    text, kb = _render_opt_path(bot, pending, chat_id)
    await _edit_wizard(bot, chat_id, pending, text, kb)
