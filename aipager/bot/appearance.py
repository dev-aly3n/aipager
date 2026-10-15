"""/settings → 🎨 Appearance: how aipager's buttons look in a chat.

Telegram lets a bot colour an inline button in three ways only
(``InlineKeyboardButton.style``: "primary" blue, "success" green, "danger"
red) or leave it as the app draws every button. So each choice here is one
of those, or Normal. Chat-wide; every choice starts on Normal, which sends
no style at all (an app that does not know styles draws them all plain
anyway). Stored by :mod:`aipager.preferences` (``get_appearance``).

Callback-data contract (``_:`` sentinel, ``set:`` is VIEW in tap_gate):
    _:set:ap                   → the Appearance page
    _:set:ap:<field>           → one choice's options
    _:set:ap:<field>:<value>   → set it (admin-gated in a group)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager import i18n, preferences
from aipager.bot import language_menu, settings_menu
from aipager.bot.transport import calling_chat_id, edit_text
from aipager.i18n import Command, Msg

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update

    from aipager.bot.core import TelegramBot

log = logging.getLogger(__name__)

# ---- the words --------------------------------------------------------------------

BUTTON = Msg("set.btn.appearance", "🎨 Appearance",
             note="/settings button: how aipager's buttons look in this chat")
PAGE = Msg("set.ap.text",
           "🎨 <b>Appearance</b>\n\nHow aipager's buttons look in this chat. Telegram "
           "lets a bot colour a button blue, green or red, or leave it plain. Apps that "
           "do not support colours show every button plain.",
           note="Top of /settings → Appearance")

TITLES = {
    "stop": Msg("set.ap.stop.title", "🛑 Stop button",
                note="Appearance choice: the colour of the Stop button"),
    "prompt": Msg("set.ap.prompt.title", "🔐 Allow and Deny buttons",
                  note="Appearance choice: the colours of a permission prompt's buttons"),
    "app": Msg("set.ap.app.title", "📱 App button",
               note="Appearance choice: the colour of the button that opens the Mini App"),
    "settings": Msg("set.ap.settings.title", "⚙️ Settings menu",
                    note="Appearance choice: how /settings marks the current choice"),
}
INTROS = {
    "stop": Msg("set.ap.intro.stop",
                "The Stop button on the busy card and under a question or permission "
                "prompt."),
    "prompt": Msg("set.ap.intro.prompt",
                  "The Allow and Deny buttons under a permission prompt."),
    "app": Msg("set.ap.intro.app",
               "The App button on the pinned message and under /app. The menu button "
               "next to the message box always looks the same."),
    "settings": Msg("set.ap.intro.settings", "How /settings marks the current choice."),
}
LABELS = {
    "normal": Msg("set.ap.opt.normal", "Normal",
                  note="Appearance option: Telegram's own look, no colour"),
    "blue": Msg("set.ap.opt.blue", "Blue", note="Appearance option: a blue button"),
    "green": Msg("set.ap.opt.green", "Green", note="Appearance option: a green button"),
    "red": Msg("set.ap.opt.red", "Red", note="Appearance option: a red button"),
    "coloured": Msg("set.ap.opt.coloured", "Coloured - Allow green, Deny red",
                    note="Appearance option for the permission prompt's buttons"),
    "highlight": Msg("set.ap.opt.highlight", "Current choice in blue",
                     note="Appearance option for /settings: the current choice is a blue "
                          "button as well as marked ✅"),
}
HELP = {
    "normal": Msg("set.ap.help.normal", "Telegram's own look, like every other button."),
    "settings.normal": Msg("set.ap.help.settings_normal", "Only ✅ marks the current choice."),
}

#: What each choice paints, for the Mini App's coloured dots: one colour,
#: two (Allow green, Deny red) or none.
_SWATCHES = {"blue": ["blue"], "green": ["green"], "red": ["red"],
             "coloured": ["green", "red"], "highlight": ["blue"]}
#: The Bot API's button styles.
_STYLE = {"blue": "primary", "green": "success", "red": "danger"}


# ---- what the keyboards use ---------------------------------------------------------

def _look(chat_id) -> preferences.Appearance:
    try:
        return preferences.get_appearance(int(chat_id or 0))
    except (TypeError, ValueError):
        return preferences.Appearance()


def stop_style(chat_id) -> str | None:
    """The style of the Stop button in *chat_id* (None: plain)."""
    return _STYLE.get(_look(chat_id).stop)


def prompt_styles(chat_id) -> tuple[str | None, str | None]:
    """(Allow, Deny) styles of a permission prompt in *chat_id*."""
    if _look(chat_id).prompt == "coloured":
        return "success", "danger"
    return None, None


def app_style(chat_id) -> str | None:
    """The style of the App button in *chat_id*."""
    return _STYLE.get(_look(chat_id).app)


def choice_style(chat_id) -> str | None:
    """The style of the current choice's button in /settings pages."""
    return "primary" if _look(chat_id).settings == "highlight" else None


# ---- the schema (the Mini App) ---------------------------------------------------------

def appearance_schema(tr=None) -> list[dict]:
    """The choices as plain data in *tr*'s language, the same shape as
    ``settings_menu.settings_schema`` plus each option's ``swatch``: the
    colours it paints, for the Mini App's dots."""
    tr = tr or i18n.get("en")
    out = []
    for field, values in preferences.APPEARANCE_FIELDS.items():
        out.append({
            "field": field,
            "title": tr.text(TITLES[field]),
            "options": [{
                "value": value,
                "label": tr.text(LABELS[value]),
                "help": tr.text(_help(field, value)) if _help(field, value) else "",
                "swatch": list(_SWATCHES.get(value, [])),
            } for value in values],
        })
    return out


def _help(field: str, value: str) -> Msg | None:
    if value != "normal":
        return None
    return HELP["settings.normal"] if field == "settings" else HELP["normal"]


def values(chat_id: int) -> dict:
    look = _look(chat_id)
    return {field: getattr(look, field) for field in preferences.APPEARANCE_FIELDS}


# ---- the /settings pages ----------------------------------------------------------------

def render_page(chat_id: int, *, language: str | None = None):
    """The Appearance page: one row per choice with its current value."""
    tr = i18n.get(language) if language else i18n.for_chat(chat_id)
    look = _look(chat_id)
    rows = [[InlineKeyboardButton(
        settings_menu.row_text(tr, TITLES[field], LABELS[getattr(look, field)])
        + (" ✅" if getattr(look, field) != "normal" else ""),
        callback_data=f"_:set:ap:{field}")] for field in preferences.APPEARANCE_FIELDS]
    rows.append([InlineKeyboardButton(tr.text(language_menu.BACK),
                                      callback_data="_:set:back")])
    return tr.html(PAGE), InlineKeyboardMarkup(rows)


def render_field(chat_id: int, field: str, *, language: str | None = None):
    """One choice's options (the current one marked), or None for an
    unknown field."""
    allowed = preferences.APPEARANCE_FIELDS.get(field)
    if allowed is None:
        return None
    tr = i18n.get(language) if language else i18n.for_chat(chat_id)
    current = getattr(_look(chat_id), field)
    marked = choice_style(chat_id)
    rows = []
    for value in allowed:
        here = value == current
        rows.append([InlineKeyboardButton(
            tr.text(LABELS[value]) + (" ✅" if here else ""),
            callback_data=f"_:set:ap:{field}:{value}",
            style=marked if here else None)])
    rows.append([InlineKeyboardButton(tr.text(language_menu.BACK),
                                      callback_data="_:set:ap")])
    text = f"{tr.html(TITLES[field])}\n\n{tr.html(INTROS[field])}"
    return text, InlineKeyboardMarkup(rows)


async def handle_callback(bot: TelegramBot, update: Update, query: CallbackQuery,
                          session_name: str, action: str) -> bool:
    """True iff this callback is ``_:set:ap...``. Viewing is open; a change
    needs an admin in a group (or a chat that cannot be told), like every
    other setting."""
    if session_name != "_" or not (action == "set:ap" or action.startswith("set:ap:")):
        return False
    parts = action.split(":")[2:]
    chat_id = calling_chat_id(update)
    scope = chat_id or 0
    shared = i18n.for_update(update)
    person = i18n.for_person(update)

    async def _show(rendered, by: str = "") -> None:
        text, kb = rendered
        if by:
            text += "\n\n" + shared.html(settings_menu.CHANGED_BY, person=Command(by))
        try:
            await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            log.debug("appearance: page edit failed", exc_info=True)

    if not parts:
        await _show(render_page(scope, language=shared.code))
        return True
    field = parts[0]
    if field not in preferences.APPEARANCE_FIELDS or len(parts) > 2:
        await bot._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
        return True
    if len(parts) == 1:
        await _show(render_field(scope, field, language=shared.code))
        return True
    if (chat_id is None or chat_id < 0) and not bot._is_admin(update):
        await bot._safe_answer(query, person.text(language_menu.ADMIN_ONLY), show_alert=True)
        return True
    try:
        preferences.set_appearance(scope, field, parts[1])
    except ValueError:
        await bot._safe_answer(query, person.text(language_menu.INVALID_VALUE))
        return True
    tapper = getattr(query, "from_user", None)
    by = bot._actor_label(getattr(tapper, "id", None), chat_id, tapper)
    await _show(render_field(scope, field, language=shared.code), by=by or "")
    return True


__all__ = ["BUTTON", "appearance_schema", "app_style", "choice_style", "handle_callback",
           "prompt_styles", "render_field", "render_page", "stop_style", "values"]
