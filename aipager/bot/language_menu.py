"""The Language setting: ``/language``, the first row of ``/settings``
and the group picker (the multilingual design, section 7).

Pure renderers like :mod:`aipager.bot.settings_menu`: each takes the
``Tr`` to render with, so the caller decides whose language the text is
in. Every surface that offers or confirms a language says, in one line,
that it changes only aipager's own text and never the Claude sessions or
Claude's answers (section 7.0): people would otherwise expect their
sessions to switch language too.

Each language is listed in its own name (the endonym, never translated)
and never put in callback data; the code is (``_:set:lang:<code>``).
"Automatic (your Telegram language)" is offered in a private chat only:
a group has one language, set by an admin.

Callback data (``callbacks._dispatch_settings_action``):
    _:set:lang            -> open the list (anyone may look)
    _:set:lang:auto       -> follow Telegram's language (private chat only)
    _:set:lang:<code>     -> set the language (admin-gated in groups)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.i18n import Msg, Tr
from aipager.i18n.locales import LOCALES, enabled_codes, is_beta, is_selectable, lookup

LANG_ROW = Msg("lang.row", "🌐 Language: {language}",
               note="The first row of /settings; {language} is the language's own name",
               eg={"language": "English"})
LIST_PRIVATE = Msg(
    "lang.list.private",
    "🌐 The language aipager uses for its own messages and buttons, here and in the "
    "Mini App. Your Claude sessions don't change: Claude answers in the language you "
    "write to it.",
    note="Above the language list in a private chat. Must say it changes only aipager's "
         "own text, never the Claude sessions or Claude's answers.")
LIST_GROUP = Msg(
    "lang.list.group",
    "🌐 The language aipager uses for this group's cards and buttons. Claude's sessions "
    "don't change: Claude answers in the language you write to it.",
    note="Above the language list in a group. Must say it changes only aipager's own text.")
AUTO = Msg("lang.auto", "Automatic (your Telegram language)",
           note="Language list button: follow the language of the person's Telegram app")
CHANGED = Msg("lang.changed",
              "✅ aipager now speaks {language} here. Your sessions and Claude's answers "
              "are unchanged.",
              note="Confirmation after a language change; {language} is its own name",
              eg={"language": "English"})
BACK = Msg("btn.settings_back", "« Back", note="Back to /settings")
# Pop-ups after a tap on the list: the same words every /settings value
# tap answers with.
INVALID_VALUE = Msg("toast.invalid_value", "Invalid value",
                    note="Pop-up: a settings button carried a value that is not allowed")
INVALID_CALLBACK = Msg("toast.invalid_callback", "Invalid callback",
                       note="Pop-up: a settings button aipager does not know")
ADMIN_ONLY = Msg("toast.settings_admin_only", "Only an admin can change settings in this group.",
                 note="Alert: a member who is not an admin tapped a group setting")

BETA = Msg("lang.beta", "{language} (beta)",
           note="A language in the list whose translation is new and not reviewed yet; "
                "{language} is its own name", eg={"language": "English"})
FIRST_CONTACT = Msg(
    "lang.first_contact",
    "🌐 aipager speaks {language} here (only its own messages; Claude answers in the "
    "language you write). Change with /language",
    note="Once, in a new private chat that follows the person's Telegram language: "
         "which language aipager uses, that Claude is not affected, and how to change it",
    eg={"language": "English"})

AUTO_TOKEN = "auto"


def endonym(code: str) -> str:
    loc = lookup(code)
    return loc.endonym if loc is not None else LOCALES["en"].endonym


def row_text(tr: Tr, code: str) -> str:
    """``🌐 Language: English``: the /settings row for the language in
    force (*code*)."""
    return tr.text(LANG_ROW, language=endonym(code))


def list_text(tr: Tr, *, private: bool) -> str:
    """The 7.0 line above the list (HTML-safe: no parameters)."""
    return tr.html(LIST_PRIVATE if private else LIST_GROUP)


def render_language_menu(tr: Tr, *, private: bool, current: str
                         ) -> tuple[str, InlineKeyboardMarkup]:
    """The language list: the 7.0 line, then (private chats) Automatic,
    one row per selectable language in its own name, and « Back. The
    choice in force (*current*: ``"auto"`` or a code) carries ✅."""
    # A /settings callback like every other section's value,
    # ``_:set:<section>:<token>``: a short code, never a session name.
    section = "lang"
    tokens = ([AUTO_TOKEN] if private else []) + list(enabled_codes())
    rows = []
    for token in tokens:
        if token == AUTO_TOKEN:
            label = tr.text(AUTO)
        elif is_beta(token):
            # Not reviewed yet: said in the list, in the viewer's language.
            label = tr.text(BETA, language=endonym(token))
        else:
            label = endonym(token)
        mark = " ✅" if current == token else ""
        rows.append([InlineKeyboardButton(f"{label}{mark}",
                                          callback_data=f"_:set:{section}:{token}")])
    rows.append([InlineKeyboardButton(tr.text(BACK), callback_data="_:set:back")])
    return list_text(tr, private=private), InlineKeyboardMarkup(rows)


def confirmation(tr: Tr, code: str) -> str:
    """``✅ aipager now speaks English here. ...`` (HTML)."""
    return tr.html(CHANGED, language=endonym(code))


def current_choice(chat_id, *, private: bool, telegram_code: str | None = None) -> str:
    """What the list marks ✅. In a group: the language in force (English
    until an admin chose). In a private chat: the stored code, or
    ``"auto"`` for a stored Automatic; with nothing stored, ``"auto"``
    when the person follows Telegram, else (someone who existed before
    languages were switched on, still on English) ``"auto"`` when
    Automatic would read English and ``"en"`` when it would not, which is
    the truth either way. *telegram_code* is the person's Telegram
    language when the caller has it (the Mini App's initData); else the
    last one aipager saw."""
    from aipager import i18n, preferences
    from aipager.i18n import resolve
    if not private:
        return i18n.for_chat(chat_id).code
    stored = preferences.get_ui_language(chat_id) if chat_id else ""
    if stored == AUTO_TOKEN or (stored and is_selectable(stored)):
        return stored
    if not chat_id or resolve.follows(chat_id):
        return AUTO_TOKEN
    return AUTO_TOKEN if i18n.automatic_code(chat_id, telegram_code) == "en" else "en"


def first_contact_text(tr: Tr) -> str:
    """The 7.1 line (HTML) in *tr*'s language, naming that language: sent
    once, as its own message, to a private chat new since the launch that
    follows its person's Telegram language (``language_launch``)."""
    return tr.html(FIRST_CONTACT, language=endonym(tr.code))
