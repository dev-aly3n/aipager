"""Interactive `/new` wizard — the whole session-creation state machine.

Reached only when `/new` is sent with **no** arguments (`_handle_new_cmd`
branches there before doing anything else; `/new !name [prompt]` and
`/new name [prompt]` are completely untouched — see `handlers.py`). Three
functions make up the entire contract other modules use:

    start_wizard(bot, update, ctx)          — entry point, sends the
                                               first ("what's the name?")
                                               prompt.
    maybe_handle_text(bot, update, ctx, text) -> bool
                                             — called for every inbound
                                               text message BEFORE any
                                               other routing; returns
                                               True iff it consumed the
                                               text (only true while the
                                               wizard is sitting at one
                                               of its two free-text
                                               steps: `name`,
                                               `opt_model_custom`,
                                               `opt_path_newfolder`).
    handle_callback(bot, update, query, session_name, action) -> bool
                                             — called for every inbound
                                               callback query; returns
                                               True iff `session_name ==
                                               "_"` and `action` starts
                                               with `"nw:"` (this
                                               module's whole callback
                                               namespace).

Pending state lives on a lazily-initialized ``bot._new_wizard_pending:
dict[int, dict]`` instance attribute, keyed by the wizard's chat id — NOT
a module-level dict, so nothing leaks between tests that build a fresh
``TelegramBot`` per test (see design.md's Alternatives). One wizard per
chat; a second ``/new`` (no args) while one is pending replaces it
cleanly. Idle more than ``_WIZARD_TTL_SECONDS`` (checked lazily, no
background timer) is treated as gone.

Every render function below is a pure ``(text, keyboard)`` builder in the
same shape ``settings_menu.py`` already uses; every step transition edits
the wizard's own message in place (`_edit_wizard`, always through
``bot._app.bot.edit_message_text`` rather than ``query.edit_message_text``
so the exact same code path serves both a callback tap and a free-text
reply). Callback-data dispatch is intentionally stateless with respect to
``pending["step"]`` — a callback fully describes its own effect (matching
`settings_menu`'s own `_:set:...` dispatch, which never tracks "current
step" either) — `step` exists purely to route free-text input in
`maybe_handle_text`.

Callback-data contract (sentinel `_`, never the session name or any free
text — see entrypoints.md for the exhaustive table and worst-case byte
counts):

    _:nw:cancel                    Cancel from any step
    _:nw:mode:auto / _:nw:mode:ask Pick permission mode
    _:nw:summary                   Return to summary
    _:nw:opt                       Open Optional menu
    _:nw:opt:model                 Open model submenu
    _:nw:opt:path                  Open path submenu
    _:nw:opt:pref:<section>        Open one preference field's value list
    _:nw:model:<idx>               Pick model by index into MODEL_CHOICES
    _:nw:model:default             Clear model choice
    _:nw:model:custom              Enter custom-model text capture
    _:nw:path:<idx>                Pick directory by index into the
                                    wizard's own fresh path_options snapshot
    _:nw:path:default              Clear path choice
    _:nw:path:new                  Enter new-folder text capture
    _:nw:pref:<section>:<token>    Set/clear a preference field's override
    _:nw:confirm                   Create the session
"""

from __future__ import annotations

import html as html_mod
import logging
import time
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from aipager.bot.settings_menu import settings_schema
from aipager.bot.transport import calling_chat_id
from aipager.config import MODEL_CHOICES
from aipager.miniapp import launch
from aipager.preferences import is_valid_value
from aipager.state import PREFERENCE_OVERRIDE_FIELDS, Status

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

    from aipager.bot.core import TelegramBot

log = logging.getLogger("aipager.bot.new_flow")

# Idle-more-than-this-and-it's-gone. Checked lazily on the next tap or
# text message — no background timer (see module docstring / design.md
# Risks: consistent with the three pre-existing pending dicts).
_WIZARD_TTL_SECONDS = 600

# Steps that intercept a free-text message. Every other step (mode,
# summary, opt_menu, opt_model, opt_path, opt_pref_field) is
# callback-only, so normal session routing (talking to an active
# session while the wizard sits at one of those steps) is unaffected —
# see design.md Risks.
_TEXT_CAPTURE_STEPS = frozenset({"name", "opt_model_custom", "opt_path_newfolder"})

_EXPIRED_TEXT = "⏱ This session-creation wizard expired. Run /new again."

# Distinguishes "no override recorded for this field" from every legal
# value a preference field can hold (including `False`), mirroring
# preferences.py's own `_MISSING` sentinel technique.
_UNSET = object()


# ---- pending-state plumbing ------------------------------------------

def _pending_store(bot: TelegramBot) -> dict[int, dict]:
    """Lazily-initialized ``bot._new_wizard_pending`` — see module
    docstring for why this is an instance attribute, not a module dict."""
    store = getattr(bot, "_new_wizard_pending", None)
    if store is None:
        store = {}
        bot._new_wizard_pending = store
    return store


def _now() -> float:
    return time.monotonic()


def _is_expired(pending: dict) -> bool:
    return (_now() - pending.get("last_active", 0.0)) > _WIZARD_TTL_SECONDS


def _touch(pending: dict) -> None:
    pending["last_active"] = _now()


# ---- small shared render helpers --------------------------------------

def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel")]])


def _back_cancel_kb(back_action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("« Back", callback_data=back_action),
        InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel"),
    ]])


def _mode_icon_label(pending: dict) -> tuple[str, str]:
    if pending.get("skip_perms"):
        return "🤖", "Auto"
    return "💬", "Ask"


def _short_path(path: str, limit: int = 40) -> str:
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


# ---- step renderers: each returns (text, keyboard) --------------------

def _name_prompt_text(error: str = "") -> str:
    header = "🆕 <b>New session</b>\n\n"
    if error:
        return header + f"⚠️ {html_mod.escape(error)}\n\nWhat should this session be called?"
    return header + "What should this session be called?"


def _render_mode(
    bot: TelegramBot, pending: dict, chat_id: int, *, note: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    is_admin = bot._is_admin_user(pending["user_id"], chat_id)
    auto_label = "🤖 Auto" if is_admin else "🤖 Auto (requires admin)"
    text = f"🆕 <b>{html_mod.escape(pending['name'])}</b>\n\n"
    if note:
        text += f"⚠️ {html_mod.escape(note)}\n\n"
    text += "Choose a permission mode:"
    rows = [
        [InlineKeyboardButton(auto_label, callback_data="_:nw:mode:auto"),
         InlineKeyboardButton("💬 Ask", callback_data="_:nw:mode:ask")],
        [InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel")],
    ]
    return text, InlineKeyboardMarkup(rows)


def _render_summary(pending: dict, *, error: str = "") -> tuple[str, InlineKeyboardMarkup]:
    icon, label = _mode_icon_label(pending)
    model_line = pending.get("model_label") or "Default"
    path_line = pending.get("cwd") or "Default"
    lines = [
        f"🆕 <b>{html_mod.escape(pending['name'])}</b>",
        f"{icon} {label} mode",
        f"🧠 Model: {html_mod.escape(model_line)}",
        f"📁 Path: {html_mod.escape(path_line)}",
    ]
    prefs = pending.get("prefs") or {}
    if prefs:
        lines.append("")
        lines.append("Preference overrides:")
        for entry in settings_schema():
            field = entry["field"]
            if field not in prefs:
                continue
            value = prefs[field]
            opt_label = next(
                (o["label"] for o in entry["options"] if o["value"] == value),
                str(value),
            )
            lines.append(f"  • {entry['title']}: {html_mod.escape(opt_label)}")
    text = "\n".join(lines)
    if error:
        text = f"❌ {html_mod.escape(error)}\n\n" + text
    text += "\n\nConfirm to create, or Optional to adjust model/path/preferences."
    rows = [
        [InlineKeyboardButton("✅ Confirm", callback_data="_:nw:confirm"),
         InlineKeyboardButton("⚙️ Optional", callback_data="_:nw:opt")],
        [InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel")],
    ]
    return text, InlineKeyboardMarkup(rows)


def _render_opt_menu(pending: dict) -> tuple[str, InlineKeyboardMarkup]:
    prefs = pending.get("prefs") or {}
    rows = [
        [InlineKeyboardButton("🧠 Model", callback_data="_:nw:opt:model")],
        [InlineKeyboardButton("📁 Path", callback_data="_:nw:opt:path")],
    ]
    for entry in settings_schema():
        marker = " ✅" if entry["field"] in prefs else ""
        rows.append([InlineKeyboardButton(
            f"{entry['title']}{marker}",
            callback_data=f"_:nw:opt:pref:{entry['section']}",
        )])
    rows.append([
        InlineKeyboardButton("« Back", callback_data="_:nw:summary"),
        InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel"),
    ])
    text = "⚙️ <b>Optional settings</b>\n\nPick something to change."
    return text, InlineKeyboardMarkup(rows)


def _render_opt_model(pending: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = pending.get("model_label")
    rows = []
    for idx, (label, _cmd) in enumerate(MODEL_CHOICES):
        marker = " ✅" if current == label else ""
        rows.append([InlineKeyboardButton(
            f"{label}{marker}", callback_data=f"_:nw:model:{idx}")])
    default_marker = " ✅" if not pending.get("model") else ""
    rows.append([InlineKeyboardButton(
        f"Use default{default_marker}", callback_data="_:nw:model:default")])
    rows.append([InlineKeyboardButton(
        "✏️ Other model...", callback_data="_:nw:model:custom")])
    rows.append([
        InlineKeyboardButton("« Back", callback_data="_:nw:opt"),
        InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel"),
    ])
    text = "🧠 <b>Model</b>\n\nPick a model for this session."
    return text, InlineKeyboardMarkup(rows)


def _render_opt_path(
    bot: TelegramBot, pending: dict, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    # Fresh every render — never a stale cross-request cache (design.md /
    # entrypoints.md are explicit about this: the snapshot backing
    # `_:nw:path:<idx>` is taken here, not reused from a previous render).
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
        f"Use default{default_marker}", callback_data="_:nw:path:default")])
    rows.append([InlineKeyboardButton(
        "➕ New folder", callback_data="_:nw:path:new")])
    rows.append([
        InlineKeyboardButton("« Back", callback_data="_:nw:opt"),
        InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel"),
    ])
    text = "📁 <b>Working directory</b>\n\nPick a directory for this session."
    return text, InlineKeyboardMarkup(rows)


def _render_opt_pref_field(
    pending: dict, section: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    entry = next(
        (e for e in settings_schema() if e["section"] == section), None)
    if entry is None:
        return None
    field = entry["field"]
    current = pending.get("prefs", {}).get(field, _UNSET)
    rows = []
    for opt in entry["options"]:
        value = opt["value"]
        token = "on" if value is True else "off" if value is False else value
        marker = " ✅" if current is not _UNSET and current == value else ""
        rows.append([InlineKeyboardButton(
            f"{opt['label']}{marker}",
            callback_data=f"_:nw:pref:{section}:{token}",
        )])
    default_marker = " ✅" if current is _UNSET else ""
    rows.append([InlineKeyboardButton(
        f"Use chat default{default_marker}",
        callback_data=f"_:nw:pref:{section}:default",
    )])
    rows.append([
        InlineKeyboardButton("« Back", callback_data="_:nw:opt"),
        InlineKeyboardButton("✖️ Cancel", callback_data="_:nw:cancel"),
    ])
    text = f"{entry['title']}\n\nPick a value, or use this chat's default."
    return text, InlineKeyboardMarkup(rows)


def _model_custom_prompt_text(error: str = "") -> str:
    header = "🧠 <b>Custom model</b>\n\n"
    if error:
        return header + f"⚠️ {html_mod.escape(error)}\n\nType the model name."
    return header + "Type the model name (e.g. claude-opus-5)."


def _path_newfolder_prompt_text(error: str = "") -> str:
    header = "📁 <b>New folder</b>\n\n"
    if error:
        return header + f"⚠️ {html_mod.escape(error)}\n\nType the new folder's name."
    return header + "Type the new folder's name."


# ---- message editing (one code path for both text- and callback-driven
# transitions — see module docstring) -----------------------------------

async def _edit_wizard(
    bot: TelegramBot, chat_id: int, pending: dict, text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot._app.bot.edit_message_text(
            chat_id=chat_id, message_id=pending["msg_id"], text=text,
            parse_mode="HTML", reply_markup=kb,
        )
    except Exception:
        log.debug("new_flow: failed to edit wizard message", exc_info=True)


async def _edit_stale(bot: TelegramBot, chat_id: int, message_id: int | None) -> None:
    """Best-effort: a callback landed on a message with no pending state
    (daemon restart, or the wizard already finished/expired elsewhere)."""
    if message_id is None:
        return
    try:
        await bot._app.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=_EXPIRED_TEXT,
            reply_markup=None,
        )
    except Exception:
        log.debug("new_flow: failed to edit stale wizard message", exc_info=True)


# ---- step transitions ---------------------------------------------------

async def _goto_mode(
    bot: TelegramBot, chat_id: int, pending: dict, *, note: str = "",
) -> None:
    pending["step"] = "mode"
    text, kb = _render_mode(bot, pending, chat_id, note=note)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _goto_summary(
    bot: TelegramBot, chat_id: int, pending: dict, *, error: str = "",
) -> None:
    pending["step"] = "summary"
    text, kb = _render_summary(pending, error=error)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _goto_opt_menu(bot: TelegramBot, chat_id: int, pending: dict) -> None:
    pending["step"] = "opt_menu"
    text, kb = _render_opt_menu(pending)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _goto_opt_model(bot: TelegramBot, chat_id: int, pending: dict) -> None:
    pending["step"] = "opt_model"
    text, kb = _render_opt_model(pending)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _goto_opt_path(bot: TelegramBot, chat_id: int, pending: dict) -> None:
    pending["step"] = "opt_path"
    text, kb = _render_opt_path(bot, pending, chat_id)
    await _edit_wizard(bot, chat_id, pending, text, kb)


async def _goto_opt_pref_field(
    bot: TelegramBot, chat_id: int, pending: dict, section: str,
) -> bool:
    rendered = _render_opt_pref_field(pending, section)
    if rendered is None:
        return False
    pending["step"] = "opt_pref_field"
    pending["pref_section"] = section
    text, kb = rendered
    await _edit_wizard(bot, chat_id, pending, text, kb)
    return True


async def _goto_model_custom(
    bot: TelegramBot, chat_id: int, pending: dict, *, error: str = "",
) -> None:
    pending["step"] = "opt_model_custom"
    text = _model_custom_prompt_text(error)
    await _edit_wizard(bot, chat_id, pending, text, _back_cancel_kb("_:nw:opt:model"))


async def _goto_path_newfolder(
    bot: TelegramBot, chat_id: int, pending: dict, *, error: str = "",
) -> None:
    pending["step"] = "opt_path_newfolder"
    text = _path_newfolder_prompt_text(error)
    await _edit_wizard(bot, chat_id, pending, text, _back_cancel_kb("_:nw:opt:path"))


# ---- entry point ----------------------------------------------------

async def start_wizard(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE,
) -> None:
    """`/new` with no arguments — the wizard's entry point.

    Self-contained: re-checks authorization itself (rather than relying
    solely on `_handle_new_cmd`'s own check) so a black-box caller
    invoking this function directly is still gated correctly.
    """
    if not await bot._authorize(update):
        return
    chat_id = calling_chat_id(update)
    if chat_id is None or update.message is None:
        return
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user is not None else None

    store = _pending_store(bot)
    old = store.pop(chat_id, None)
    if old is not None:
        # A second `/new` (no args) silently replaces the first — strip
        # the old message's keyboard rather than leaving two live
        # wizards a tap could land on.
        try:
            await bot._app.bot.edit_message_text(
                chat_id=chat_id, message_id=old["msg_id"],
                text="↩️ Cancelled — started over.", reply_markup=None,
            )
        except Exception:
            log.debug("new_flow: failed to strip old wizard message", exc_info=True)

    sent = await update.message.reply_text(
        _name_prompt_text(), parse_mode="HTML", reply_markup=_cancel_kb(),
    )
    store[chat_id] = {
        "step": "name",
        "user_id": user_id,
        "msg_id": sent.message_id,
        "name": None,
        "skip_perms": None,
        "model": None,
        "model_label": None,
        "cwd": None,
        "prefs": {},
        "path_options": [],
        "pref_section": None,
        "new_folder_parent": None,
        "last_active": _now(),
    }


# ---- free-text capture -----------------------------------------------

async def maybe_handle_text(
    bot: TelegramBot, update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str,
) -> bool:
    """True iff this text was consumed by the wizard's `name`,
    `opt_model_custom` or `opt_path_newfolder` step. Every other step is
    callback-only and this returns False immediately, leaving normal
    message routing untouched (design.md Risks)."""
    chat_id = calling_chat_id(update)
    if chat_id is None:
        return False
    store = _pending_store(bot)
    pending = store.get(chat_id)
    if pending is None:
        return False
    if pending["step"] not in _TEXT_CAPTURE_STEPS:
        return False

    if _is_expired(pending):
        store.pop(chat_id, None)
        await _edit_wizard(bot, chat_id, pending, _EXPIRED_TEXT)
        return True

    _touch(pending)
    step = pending["step"]

    if step == "name":
        clean, err = launch.validate_session_name(text)
        if not err:
            existing = bot.registry.find_by_label(clean, chat_id, include_gone=True)
            if existing is not None and (
                existing.status != Status.GONE or existing.claude_session_id
            ):
                err = f"'{clean}' is already in use by a live or resumable session."
        if err:
            await _edit_wizard(bot, chat_id, pending, _name_prompt_text(err), _cancel_kb())
            return True
        pending["name"] = clean
        await _goto_mode(bot, chat_id, pending)
        return True

    if step == "opt_model_custom":
        resolved, err = launch.validate_model(text, MODEL_CHOICES)
        if err:
            await _goto_model_custom(bot, chat_id, pending, error=err)
            return True
        pending["model"] = resolved or None
        pending["model_label"] = resolved or None
        await _goto_summary(bot, chat_id, pending)
        return True

    if step == "opt_path_newfolder":
        roots = launch.allowed_roots(bot.registry, chat_id)
        parent = pending.get("new_folder_parent") or (roots[0] if roots else "")
        path, _existed, err = launch.create_directory(parent, text, roots)
        if err:
            await _goto_path_newfolder(bot, chat_id, pending, error=err)
            return True
        pending["cwd"] = path
        await _goto_summary(bot, chat_id, pending)
        return True

    return False  # pragma: no cover — _TEXT_CAPTURE_STEPS is exhaustive above


# ---- callback dispatch -------------------------------------------------

async def handle_callback(
    bot: TelegramBot, update: Update, query: CallbackQuery,
    session_name: str, action: str,
) -> bool:
    """True iff this callback belongs to the wizard (`session_name == "_"`
    and ``action`` starts with ``"nw:"``) — the caller (`_handle_callback`)
    returns immediately when this is True, exactly like the existing
    `_:set:...` dispatch."""
    if session_name != "_" or not action.startswith("nw:"):
        return False

    chat_id = calling_chat_id(update)
    if chat_id is None:
        await bot._safe_answer(query, "Invalid callback")
        return True

    store = _pending_store(bot)
    pending = store.get(chat_id)
    sub = action.split(":")[1:]  # drop the leading "nw"

    if pending is None:
        await bot._safe_answer(query, "This wizard expired.")
        msg = getattr(query, "message", None)
        await _edit_stale(bot, chat_id, getattr(msg, "message_id", None))
        return True

    if _is_expired(pending):
        store.pop(chat_id, None)
        await bot._safe_answer(query, "This wizard expired.")
        await _edit_wizard(bot, chat_id, pending, _EXPIRED_TEXT)
        return True

    _touch(pending)

    if sub == ["cancel"]:
        store.pop(chat_id, None)
        await _edit_wizard(bot, chat_id, pending, "↩️ Cancelled.")
        return True

    if sub == ["mode", "auto"]:
        if not bot._is_admin_user(pending["user_id"], chat_id):
            await bot._safe_answer(query, "Auto mode requires admin.", show_alert=True)
            return True
        pending["skip_perms"] = True
        await _goto_summary(bot, chat_id, pending)
        return True

    if sub == ["mode", "ask"]:
        pending["skip_perms"] = False
        await _goto_summary(bot, chat_id, pending)
        return True

    if sub == ["summary"]:
        await _goto_summary(bot, chat_id, pending)
        return True

    if sub == ["opt"]:
        await _goto_opt_menu(bot, chat_id, pending)
        return True

    if sub == ["opt", "model"]:
        await _goto_opt_model(bot, chat_id, pending)
        return True

    if sub == ["opt", "path"]:
        await _goto_opt_path(bot, chat_id, pending)
        return True

    if len(sub) == 3 and sub[0] == "opt" and sub[1] == "pref":
        ok = await _goto_opt_pref_field(bot, chat_id, pending, sub[2])
        if not ok:
            await bot._safe_answer(query, "Invalid callback")
        return True

    if len(sub) == 2 and sub[0] == "model":
        await _handle_model_token(bot, query, chat_id, pending, sub[1])
        return True

    if len(sub) == 2 and sub[0] == "path":
        await _handle_path_token(bot, query, chat_id, pending, sub[1])
        return True

    if len(sub) == 3 and sub[0] == "pref":
        await _handle_pref_token(bot, query, chat_id, pending, sub[1], sub[2])
        return True

    if sub == ["confirm"]:
        await _confirm(bot, update, query, chat_id, pending, store)
        return True

    await bot._safe_answer(query, "Invalid callback")
    return True


async def _handle_model_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    if token == "default":
        pending["model"] = None
        pending["model_label"] = None
        await _goto_summary(bot, chat_id, pending)
        return
    if token == "custom":
        await _goto_model_custom(bot, chat_id, pending)
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
            await _goto_summary(bot, chat_id, pending)
            return
    await bot._safe_answer(query, "That model is no longer available.")
    await _goto_opt_model(bot, chat_id, pending)


async def _handle_path_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict, token: str,
) -> None:
    if token == "default":
        pending["cwd"] = None
        await _goto_summary(bot, chat_id, pending)
        return
    if token == "new":
        options = pending.get("path_options") or launch.allowed_roots(bot.registry, chat_id)
        parent = pending.get("cwd") or (options[0] if options else "")
        if not parent:
            await bot._safe_answer(
                query,
                "No directory available yet — start a session from a "
                "real project directory first.",
                show_alert=True,
            )
            return
        pending["new_folder_parent"] = parent
        await _goto_path_newfolder(bot, chat_id, pending)
        return
    if token.isdigit():
        idx = int(token)
        options = pending.get("path_options") or []
        if 0 <= idx < len(options):
            pending["cwd"] = options[idx]
            await _goto_summary(bot, chat_id, pending)
            return
    await bot._safe_answer(query, "That option is no longer available.")
    await _goto_opt_path(bot, chat_id, pending)


async def _handle_pref_token(
    bot: TelegramBot, query: CallbackQuery, chat_id: int, pending: dict,
    section: str, token: str,
) -> None:
    entry = next((e for e in settings_schema() if e["section"] == section), None)
    if entry is None:
        await bot._safe_answer(query, "Invalid callback")
        return
    field = entry["field"]
    if token == "default":
        pending.setdefault("prefs", {}).pop(field, None)
        await _goto_summary(bot, chat_id, pending)
        return
    value = True if token == "on" else False if token == "off" else token
    if not is_valid_value(field, value):
        await bot._safe_answer(query, "Invalid value")
        return
    pending.setdefault("prefs", {})[field] = value
    await _goto_summary(bot, chat_id, pending)


# ---- confirm: creates the session --------------------------------------

async def _confirm(
    bot: TelegramBot, update: Update, query: CallbackQuery, chat_id: int,
    pending: dict, store: dict[int, dict],
) -> None:
    if not pending.get("name"):
        await bot._safe_answer(query, "Invalid callback")
        return

    user_id = pending["user_id"]

    # Re-authorize here, not only at /new — the flow spans messages and
    # minutes (design.md hard rule).
    if not bot._can_prompt_user(user_id, chat_id):
        store.pop(chat_id, None)
        await _edit_wizard(
            bot, chat_id, pending,
            "🚫 You're no longer authorized to create sessions.",
        )
        return

    if pending["skip_perms"] and not bot._is_admin_user(user_id, chat_id):
        # Demoted between choosing Auto and confirming — reopen `mode`
        # rather than silently downgrading to Ask.
        await _goto_mode(
            bot, chat_id, pending,
            note="Your permissions changed — pick a mode again.",
        )
        return

    session_name, err = await bot.create_session(
        pending["name"], scope_chat_id=chat_id,
        skip_perms=bool(pending["skip_perms"]),
        cwd=pending.get("cwd") or None,
        driver_user_id=user_id,
        model=pending.get("model") or None,
    )
    if not session_name:
        # Retry-able: stays at summary, pending state kept.
        await _goto_summary(bot, chat_id, pending, error=err)
        return

    sess = bot.registry.get_or_create(session_name)
    bot._mark_driver(sess, update)

    prefs = pending.get("prefs") or {}
    if prefs:
        for field, value in prefs.items():
            attr = PREFERENCE_OVERRIDE_FIELDS.get(field)
            if attr is not None:
                setattr(sess, attr, value)
        bot.registry.mark_dirty()

    store.pop(chat_id, None)

    icon, label = _mode_icon_label(pending)
    from aipager.dtach import inject as _inject  # local import to avoid cycles
    effective_cwd = sess.cwd or _inject._PROJECT_DIR
    model_line = (
        f"\n🧠 {html_mod.escape(sess.model_name)}" if sess.model_name else "")
    cwd_line = f"\n📁 <code>{html_mod.escape(effective_cwd)}</code>"
    perms_nudge = (
        "\n\n💡 Use /perms to switch between Ask and Auto mode."
        if not pending["skip_perms"] else ""
    )
    text = (
        f"✅ <b>{html_mod.escape(pending['name'])}</b> created\n"
        f"{icon} {label} mode"
        f"{model_line}{cwd_line}"
        + perms_nudge
    )

    kb = None
    chat = update.effective_chat
    if chat is not None and getattr(chat, "type", "") == "private" and bot._miniapp_url:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "🧠 Open Mini App", web_app=WebAppInfo(url=bot._miniapp_url))]])

    await _edit_wizard(bot, chat_id, pending, text, kb)
    log.info("Launched session %s via wizard", pending["name"])
