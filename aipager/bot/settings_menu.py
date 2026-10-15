"""Render functions for the `/settings` inline-keyboard menu.

Pure functions — no bot/I-O dependency beyond the read inside
``preferences.get_preferences`` — mirroring
``dashboard._render_resume_picker``'s ``(text, keyboard)`` shape but
callable directly from tests without constructing a ``TelegramBot``.

Callback-data contract (``callbacks.py`` parses these; see
``entrypoints.md`` for the full enumerated set):
    _:set                    → re-render the root menu
    _:set:lang[:<code>]      → the Language list (``language_menu``)
    _:set:<section>          → open a section's value list
    _:set:back               → return to the root menu
    _:set:close               → remove the keyboard
    _:set:<section>:<value>  → set a value (admin-gated in groups)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.i18n import Msg
from aipager.preferences import Preferences, get_preferences

SECTIONS = ("layout", "diffs", "cadence", "formatting", "length", "level")

_SECTION_TITLES = {
    "layout": Msg("set.layout.title", "🖼 Message layout",
                  note="/settings section: how a finished turn appears in the chat"),
    "diffs": Msg("set.diffs.title", "📝 Diff previews",
                 note="/settings section: whether each file edit gets its own diff message"),
    "cadence": Msg("set.cadence.title", "⏱ Long-turn card updates",
                   note="/settings section: how often the busy card refreshes in a long turn"),
    "formatting": Msg("set.formatting.title", "✏️ Simple formatting",
                      note="/settings section: plain prose replies, no tables or code blocks"),
    "length": Msg("set.length.title", "📏 Answer length",
                  note="/settings section: how long Claude's replies should be"),
    # "Vocabulary", not "language" (multilingual design): the setting is
    # about Claude's words, not the language aipager speaks. The stored
    # key stays `language_level`.
    "level": Msg("set.level.title", "🎓 Vocabulary level",
                 note="/settings section: how plain Claude's words should be"),
}

_LAYOUT_LABELS = {
    "card": Msg("set.opt.layout.card", "Busy card + result",
                note="Layout option: the busy card stays, the answer is its own message"),
    "merged": Msg("set.opt.layout.merged", "Merged into busy message",
                  note="Layout option: the answer replaces the busy card's body"),
    "replace": Msg("set.opt.layout.replace", "Replace with result",
                   note="Layout option: the busy card is replaced by the answer"),
}
_LAYOUT_ORDER = ("card", "merged", "replace")

_FORMATTING_LABELS = {
    False: Msg("set.opt.formatting.off", "Off - default formatting",
               note="Simple formatting option: off"),
    True: Msg("set.opt.formatting.on", "On - plain prose only",
              note="Simple formatting option: on"),
}
_FORMATTING_ORDER = (False, True)

_DIFFS_LABELS = {
    False: Msg("set.opt.diffs.off", "Off - busy card only",
               note="Diff previews option: edits show only as busy card rows"),
    True: Msg("set.opt.diffs.on", "On - a diff message per edit",
              note="Diff previews option: each edit also gets its own diff message"),
}
_DIFFS_ORDER = (False, True)

# On first: it is the default, and the one that keeps a long turn cheap.
_CADENCE_LABELS = {
    True: Msg("set.opt.cadence.on", "On - slow down as a turn gets long",
              note="Long-turn card updates option: refresh less often as a turn runs on"),
    False: Msg("set.opt.cadence.off", "Off - same pace for the whole turn",
               note="Long-turn card updates option: always the same refresh pace"),
}
_CADENCE_ORDER = (True, False)

_LENGTH_LABELS = {
    "none": Msg("set.opt.length.none", "Don't apply any rule",
                note="Answer length option: no guidance"),
    "xshort": Msg("set.opt.length.xshort", "Extra short", note="Answer length option"),
    "short": Msg("set.opt.length.short", "Short", note="Answer length option"),
    "medium": Msg("set.opt.length.medium", "Medium", note="Answer length option"),
    "long": Msg("set.opt.length.long", "Long", note="Answer length option"),
}
_LENGTH_ORDER = ("none", "xshort", "short", "medium", "long")

_LEVEL_LABELS = {
    "none": Msg("set.opt.level.none", "Don't apply any rule",
                note="Vocabulary level option: no guidance"),
    "simple": Msg("set.opt.level.simple", "Simple words", note="Vocabulary level option"),
    "normal": Msg("set.opt.level.normal", "Normal", note="Vocabulary level option"),
    "advanced": Msg("set.opt.level.advanced", "Advanced", note="Vocabulary level option"),
}
_LEVEL_ORDER = ("none", "simple", "normal", "advanced")

_SECTION_FIELD = {
    "layout": "layout",
    "diffs": "diff_preview",
    "cadence": "card_age_decay",
    "formatting": "simple_formatting",
    "length": "answer_length",
    "level": "language_level",
}


# One short line per option, for surfaces with room to explain rather than
# just label. The inline keyboard has no room for these; the Mini App does.
_OPTION_HELP = {
    ("layout", "card"): Msg("set.help.layout.card",
                            "Busy card stays, the answer arrives as its own message."),
    ("layout", "merged"): Msg("set.help.layout.merged",
                              "The answer replaces the busy card's body in place."),
    ("layout", "replace"): Msg("set.help.layout.replace",
                               "The busy card is replaced by the answer alone."),
    ("diffs", False): Msg("set.help.diffs.off", "File edits show only as rows on the busy card."),
    ("diffs", True): Msg("set.help.diffs.on",
                         "Each Write/Edit is also posted as its own diff message under the "
                         "busy card.",
                         note="Write and Edit are Claude Code's tools: keep them as they are"),
    ("cadence", True): Msg("set.help.cadence.on",
                           "The card refreshes less often as a turn runs on (every 10 s "
                           "after 2 min, every 30 s after 10 min) and its clock then "
                           "moves in 30 s steps, so it never looks frozen. A state change "
                           "still shows at once."),
    ("cadence", False): Msg("set.help.cadence.off",
                            "The card refreshes at the same pace for the whole turn, however "
                            "long."),
    ("formatting", False): Msg("set.help.formatting.off",
                               "Claude formats replies however it likes."),
    ("formatting", True): Msg("set.help.formatting.on",
                              "Plain prose and dashed lists only - no tables or code blocks."),
    ("length", "none"): Msg("set.help.length.none", "No length guidance at all."),
    ("length", "xshort"): Msg("set.help.length.xshort",
                              "One or two sentences. No preamble, no summary."),
    ("length", "short"): Msg("set.help.length.short", "A few sentences."),
    ("length", "medium"): Msg("set.help.length.medium", "A couple of paragraphs."),
    ("length", "long"): Msg("set.help.length.long", "Full detail when the answer needs it."),
    ("level", "none"): Msg("set.help.level.none", "No vocabulary guidance at all."),
    ("level", "simple"): Msg("set.help.level.simple", "Everyday words, short sentences."),
    ("level", "normal"): Msg("set.help.level.normal", "Ordinary technical writing."),
    ("level", "advanced"): Msg("set.help.level.advanced", "Assumes expertise; no hand-holding."),
}

_SECTION_ORDERS = {
    "layout": _LAYOUT_ORDER,
    "diffs": _DIFFS_ORDER,
    "cadence": _CADENCE_ORDER,
    "formatting": _FORMATTING_ORDER,
    "length": _LENGTH_ORDER,
    "level": _LEVEL_ORDER,
}
_SECTION_LABELS = {
    "layout": _LAYOUT_LABELS,
    "diffs": _DIFFS_LABELS,
    "cadence": _CADENCE_LABELS,
    "formatting": _FORMATTING_LABELS,
    "length": _LENGTH_LABELS,
    "level": _LEVEL_LABELS,
}


#: The /settings words outside the tables.
ROW = Msg("set.text.row", "{title}: {value}",
          note="A /settings button: a section's title and its current value",
          eg={"title": "📏 Answer length", "value": "Short"})
ROOT_TEXT = Msg("set.text.root",
                "⚙️ <b>Settings</b>\n\nPer-chat preferences for how Claude replies here. "
                "Tap a section to change it.",
                note="Top of /settings")
NEW_SESSIONS = Msg("set.btn.new_sessions", "🆕 New sessions",
                   note="/settings button: the defaults /new starts a session with")
PER_SESSION = Msg("set.btn.per_session", "👤 Per-session preferences",
                  note="/settings button: one session's own reply style")
PROBLEM_REPORTS = Msg("set.btn.problem_reports", "🐞 Problem reports: {value}",
                      note="/settings button (owner only): whether aipager offers to send "
                           "problem reports; {value} is Ask me, Off or offers off",
                      eg={"value": "offers off"})
CLOSE = Msg("set.btn.close", "✖️ Close", note="Closes the /settings menu")
CHANGED_BY = Msg("set.text.changed_by", "<i>Changed by {person}.</i>",
                 note="Under a setting changed in a group: who changed it; {person} is "
                      "an @username or a name",
                 eg={"person": "@bob"})


def _tr(tr):
    if tr is None:
        from aipager import i18n
        return i18n.get("en")
    return tr


def settings_schema(tr=None) -> list[dict]:
    """The settable sections as plain data: field name, title, and the
    ordered options with their labels and help text, in *tr*'s language
    (English when None, as the Mini App reads it until it is translated).

    Exists so a second surface (the Mini App) renders exactly the sections,
    ordering and wording `/settings` uses, instead of keeping its own copy
    that silently drifts. Derived from the same constants the inline
    keyboard renders from — there is no second list to keep in step.
    """
    tr = _tr(tr)
    out = []
    for section in SECTIONS:
        order = _SECTION_ORDERS[section]
        labels = _SECTION_LABELS[section]
        out.append({
            "section": section,
            "field": _SECTION_FIELD[section],
            "title": tr.text(_SECTION_TITLES[section]),
            "options": [
                {
                    "value": value,
                    "label": tr.text(labels[value]),
                    "help": (tr.text(_OPTION_HELP[(section, value)])
                             if (section, value) in _OPTION_HELP else ""),
                }
                for value in order
            ],
        })
    return out


def _current_value(prefs: Preferences, section: str):
    return getattr(prefs, _SECTION_FIELD[section])


def row_text(tr, title, value) -> str:
    """``"{title}: {value}"`` for a settings button, both already words
    of *tr*'s language (each its own key)."""
    from aipager.i18n import Safe
    return tr.text(ROW, title=Safe(tr.text(title)), value=Safe(tr.text(value)))


def _root_button_text(section: str, prefs: Preferences, tr=None) -> str:
    tr = _tr(tr)
    value = _current_value(prefs, section)
    title = _SECTION_TITLES[section]
    if section == "layout":
        label = _LAYOUT_LABELS[value]
        # Layout always has a concretely active mode (never "off"), so its
        # root row always carries the marker — the one field where "current
        # value" and "something is actively applied" are the same thing.
        return f"{row_text(tr, title, label)} ✅"
    if section == "cadence":
        # The one boolean that defaults ON (8.30): its row is marked when
        # it differs from THAT default — switched off — like every other
        # row's marker, which says "customized", not "on".
        label = _CADENCE_LABELS[value]
        marker = " ✅" if value is False else ""
        return f"{row_text(tr, title, label)}{marker}"
    if section in ("formatting", "diffs"):
        # Boolean sections: the marker means "actively on".
        label = _SECTION_LABELS[section][value]
        marker = " ✅" if value else ""
        return f"{row_text(tr, title, label)}{marker}"
    if section == "length":
        label = _LENGTH_LABELS[value]
        marker = " ✅" if value != "none" else ""
        return f"{row_text(tr, title, label)}{marker}"
    # "level"
    label = _LEVEL_LABELS[value]
    marker = " ✅" if value != "none" else ""
    return f"{row_text(tr, title, label)}{marker}"


def render_settings_root(
    chat_id: int, *, problem_reports: str | None = None,
    language: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Root menu: the Language row, one button per section (current value
    inline) + Close.

    ``problem_reports`` is the owner's "Problem reports" value as the row
    shows it (``report_flow.settings_row_state``); None leaves the row
    out (a group, someone else, or no owner).

    ``language`` is the code of the language in force for the caller
    (``i18n.for_update``); None reads the chat's own (``for_chat``). The
    Language row comes first: it opens the list, ``_:set:lang``."""
    from aipager import i18n
    from aipager.bot import language_menu
    tr = i18n.get(language) if language else i18n.for_chat(chat_id)
    prefs = get_preferences(chat_id)
    rows = [[InlineKeyboardButton(language_menu.row_text(tr, tr.code),
                                  callback_data="_:set:lang")]]
    rows += [
        [InlineKeyboardButton(_root_button_text(section, prefs, tr),
                              callback_data=f"_:set:{section}")]
        for section in SECTIONS
    ]
    # How aipager's buttons look here (appearance.py): chat-wide, kept out
    # of SECTIONS on purpose (not a reply style, no per-session override).
    from aipager.bot import appearance
    rows.append([InlineKeyboardButton(
        tr.text(appearance.BUTTON), callback_data="_:set:ap")])
    # What /new starts a session with (mode, model, folder): the chat's
    # own defaults, kept out of SECTIONS on purpose (new_flow.py).
    rows.append([InlineKeyboardButton(
        tr.text(NEW_SESSIONS), callback_data="_:set:ns")])
    # Per-session overrides live behind their own picker — the Mini App
    # can set these per session and chat could not reach them at all.
    rows.append([InlineKeyboardButton(
        tr.text(PER_SESSION), callback_data="_:spref")])
    # The owner's install-wide switch (roadmap 8.112), owner DM only: kept
    # out of SECTIONS so it never reaches settings_schema() or a group.
    if problem_reports is not None:
        from aipager.i18n import Msg as _Msg
        from aipager.i18n import Safe
        value = (tr.text(problem_reports) if isinstance(problem_reports, _Msg)
                 else problem_reports)
        rows.append([InlineKeyboardButton(
            tr.text(PROBLEM_REPORTS, value=Safe(value)), callback_data="_:rp:set")])
    rows.append([InlineKeyboardButton(tr.text(CLOSE), callback_data="_:set:close")])
    return tr.html(ROOT_TEXT), InlineKeyboardMarkup(rows)


_SECTION_INTRO = {
    "layout": Msg("set.text.intro.layout", "How a finished turn appears in the chat."),
    "diffs": Msg(
        "set.text.intro.diffs",
        "Whether each file edit is posted as a separate diff message under "
        "the busy card. Off keeps one busy card and one answer - the card "
        "still lists every edit, and the Mini App has a diff viewer."),
    "cadence": Msg(
        "set.text.intro.cadence",
        "How often a busy card refreshes during a LONG turn. On, it slows "
        "down as the turn gets older and its clock moves in 30 s steps, "
        "which keeps a long-running chat well inside Telegram's limits. "
        "Off keeps the first-minutes pace for the whole turn."),
    "formatting": Msg(
        "set.text.intro.formatting",
        "When ON, replies use plain prose and simple dashed lists only - "
        "no tables, code blocks, headings, or bold/italics."),
    "length": Msg("set.text.intro.length", "How long Claude's replies should be."),
    "level": Msg("set.text.intro.level", "Vocabulary level for Claude's replies."),
}


def render_settings_section(
    chat_id: int, section: str, *, language: str | None = None,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """A section's value list + Back button, or ``None`` for an unknown
    section (the caller treats this the same as "Invalid callback").
    ``language`` as for :func:`render_settings_root`."""
    if section not in SECTIONS:
        return None
    from aipager import i18n
    from aipager.bot import language_menu
    tr = i18n.get(language) if language else i18n.for_chat(chat_id)
    prefs = get_preferences(chat_id)
    current = _current_value(prefs, section)

    # One table for every section (the same one `settings_schema` reads),
    # so a section added there cannot be forgotten here.
    order, labels = _SECTION_ORDERS[section], _SECTION_LABELS[section]

    from aipager.bot import appearance
    marked = appearance.choice_style(chat_id)
    rows = []
    for value in order:
        marker = " ✅" if value == current else ""
        # Boolean fields' (simple_formatting, diff_preview,
        # card_age_decay) values are bools; every other field's values already double as their own
        # callback-data tokens.
        token = "on" if value is True else "off" if value is False else value
        rows.append([InlineKeyboardButton(
            f"{tr.text(labels[value])}{marker}",
            callback_data=f"_:set:{section}:{token}",
            style=marked if value == current else None,
        )])
    rows.append([InlineKeyboardButton(tr.text(language_menu.BACK),
                                      callback_data="_:set:back")])

    # The section's title over its intro: two messages of their own.
    text = f"{tr.html(_SECTION_TITLES[section])}\n\n{tr.html(_SECTION_INTRO[section])}"
    return text, InlineKeyboardMarkup(rows)
