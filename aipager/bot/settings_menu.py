"""Render functions for the `/settings` inline-keyboard menu.

Pure functions — no bot/I-O dependency beyond the read inside
``preferences.get_preferences`` — mirroring
``dashboard._render_resume_picker``'s ``(text, keyboard)`` shape but
callable directly from tests without constructing a ``TelegramBot``.

Callback-data contract (``callbacks.py`` parses these; see
``entrypoints.md`` for the full enumerated set):
    _:set                    → re-render the root menu
    _:set:<section>          → open a section's value list
    _:set:back               → return to the root menu
    _:set:close               → remove the keyboard
    _:set:<section>:<value>  → set a value (admin-gated in groups)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.preferences import Preferences, get_preferences

SECTIONS = ("layout", "diffs", "formatting", "length", "level")

_SECTION_TITLES = {
    "layout": "🖼 Message layout",
    "diffs": "📝 Diff previews",
    "formatting": "✏️ Simple formatting",
    "length": "📏 Answer length",
    "level": "🎓 Language level",
}

_LAYOUT_LABELS = {
    "card": "Busy card + result",
    "merged": "Merged into busy message",
    "replace": "Replace with result",
}
_LAYOUT_ORDER = ("card", "merged", "replace")

_FORMATTING_LABELS = {False: "Off — default formatting", True: "On — plain prose only"}
_FORMATTING_ORDER = (False, True)

_DIFFS_LABELS = {False: "Off — busy card only", True: "On — a diff message per edit"}
_DIFFS_ORDER = (False, True)

_LENGTH_LABELS = {
    "none": "Don't apply any rule",
    "xshort": "Extra short",
    "short": "Short",
    "medium": "Medium",
    "long": "Long",
}
_LENGTH_ORDER = ("none", "xshort", "short", "medium", "long")

_LEVEL_LABELS = {
    "none": "Don't apply any rule",
    "simple": "Simple words",
    "normal": "Normal",
    "advanced": "Advanced",
}
_LEVEL_ORDER = ("none", "simple", "normal", "advanced")

_SECTION_FIELD = {
    "layout": "layout",
    "diffs": "diff_preview",
    "formatting": "simple_formatting",
    "length": "answer_length",
    "level": "language_level",
}


# One short line per option, for surfaces with room to explain rather than
# just label. The inline keyboard has no room for these; the Mini App does.
_OPTION_HELP = {
    ("layout", "card"): "Busy card stays, the answer arrives as its own message.",
    ("layout", "merged"): "The answer replaces the busy card's body in place.",
    ("layout", "replace"): "The busy card is replaced by the answer alone.",
    ("diffs", False): "File edits show only as rows on the busy card.",
    ("diffs", True): "Each Write/Edit is also posted as its own diff message under the busy card.",
    ("formatting", False): "Claude formats replies however it likes.",
    ("formatting", True): "Plain prose and dashed lists only — no tables or code blocks.",
    ("length", "none"): "No length guidance at all.",
    ("length", "xshort"): "One or two sentences. No preamble, no summary.",
    ("length", "short"): "A few sentences.",
    ("length", "medium"): "A couple of paragraphs.",
    ("length", "long"): "Full detail when the answer needs it.",
    ("level", "none"): "No vocabulary guidance at all.",
    ("level", "simple"): "Everyday words, short sentences.",
    ("level", "normal"): "Ordinary technical writing.",
    ("level", "advanced"): "Assumes expertise; no hand-holding.",
}

_SECTION_ORDERS = {
    "layout": _LAYOUT_ORDER,
    "diffs": _DIFFS_ORDER,
    "formatting": _FORMATTING_ORDER,
    "length": _LENGTH_ORDER,
    "level": _LEVEL_ORDER,
}
_SECTION_LABELS = {
    "layout": _LAYOUT_LABELS,
    "diffs": _DIFFS_LABELS,
    "formatting": _FORMATTING_LABELS,
    "length": _LENGTH_LABELS,
    "level": _LEVEL_LABELS,
}


def settings_schema() -> list[dict]:
    """The settable sections as plain data: field name, title, and the
    ordered options with their labels and help text.

    Exists so a second surface (the Mini App) renders exactly the sections,
    ordering and wording `/settings` uses, instead of keeping its own copy
    that silently drifts. Derived from the same constants the inline
    keyboard renders from — there is no second list to keep in step.
    """
    out = []
    for section in SECTIONS:
        order = _SECTION_ORDERS[section]
        labels = _SECTION_LABELS[section]
        out.append({
            "section": section,
            "field": _SECTION_FIELD[section],
            "title": _SECTION_TITLES[section],
            "options": [
                {
                    "value": value,
                    "label": labels[value],
                    "help": _OPTION_HELP.get((section, value), ""),
                }
                for value in order
            ],
        })
    return out


def _current_value(prefs: Preferences, section: str):
    return getattr(prefs, _SECTION_FIELD[section])


def _root_button_text(section: str, prefs: Preferences) -> str:
    value = _current_value(prefs, section)
    if section == "layout":
        label = _LAYOUT_LABELS[value]
        # Layout always has a concretely active mode (never "off"), so its
        # root row always carries the marker — the one field where "current
        # value" and "something is actively applied" are the same thing.
        return f"{_SECTION_TITLES[section]}: {label} ✅"
    if section in ("formatting", "diffs"):
        # Boolean sections: the marker means "actively on".
        label = _SECTION_LABELS[section][value]
        marker = " ✅" if value else ""
        return f"{_SECTION_TITLES[section]}: {label}{marker}"
    if section == "length":
        label = _LENGTH_LABELS[value]
        marker = " ✅" if value != "none" else ""
        return f"{_SECTION_TITLES[section]}: {label}{marker}"
    # "level"
    label = _LEVEL_LABELS[value]
    marker = " ✅" if value != "none" else ""
    return f"{_SECTION_TITLES[section]}: {label}{marker}"


def render_settings_root(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Root menu: one button per section (current value inline) + Close."""
    prefs = get_preferences(chat_id)
    rows = [
        [InlineKeyboardButton(_root_button_text(section, prefs),
                              callback_data=f"_:set:{section}")]
        for section in SECTIONS
    ]
    # Per-session overrides live behind their own picker — the Mini App
    # can set these per session and chat could not reach them at all.
    rows.append([InlineKeyboardButton(
        "👤 Per-session preferences", callback_data="_:spref")])
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="_:set:close")])
    text = (
        "⚙️ <b>Settings</b>\n\n"
        "Per-chat preferences for how Claude replies here. "
        "Tap a section to change it."
    )
    return text, InlineKeyboardMarkup(rows)


_SECTION_INTRO = {
    "layout": "How a finished turn appears in the chat.",
    "diffs": (
        "Whether each file edit is posted as a separate diff message under "
        "the busy card. Off keeps one busy card and one answer — the card "
        "still lists every edit, and the Mini App has a diff viewer."
    ),
    "formatting": (
        "When ON, replies use plain prose and simple dashed lists only — "
        "no tables, code blocks, headings, or bold/italics."
    ),
    "length": "How long Claude's replies should be.",
    "level": "Vocabulary level for Claude's replies.",
}


def render_settings_section(
    chat_id: int, section: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """A section's value list + Back button, or ``None`` for an unknown
    section (the caller treats this the same as "Invalid callback")."""
    if section not in SECTIONS:
        return None
    prefs = get_preferences(chat_id)
    current = _current_value(prefs, section)

    # One table for every section (the same one `settings_schema` reads),
    # so a section added there cannot be forgotten here.
    order, labels = _SECTION_ORDERS[section], _SECTION_LABELS[section]

    rows = []
    for value in order:
        marker = " ✅" if value == current else ""
        # Boolean fields' (simple_formatting, diff_preview) values are
        # bools; every other field's values already double as their own
        # callback-data tokens.
        token = "on" if value is True else "off" if value is False else value
        rows.append([InlineKeyboardButton(
            f"{labels[value]}{marker}",
            callback_data=f"_:set:{section}:{token}",
        )])
    rows.append([InlineKeyboardButton("« Back", callback_data="_:set:back")])

    text = f"{_SECTION_TITLES[section]}\n\n{_SECTION_INTRO[section]}"
    return text, InlineKeyboardMarkup(rows)
