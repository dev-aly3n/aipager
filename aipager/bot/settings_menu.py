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

SECTIONS = ("layout", "formatting", "length", "level")

_SECTION_TITLES = {
    "layout": "🖼 Message layout",
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

_LENGTH_LABELS = {
    "none": "Don't apply any rule",
    "short": "Short",
    "medium": "Medium",
    "long": "Long",
}
_LENGTH_ORDER = ("none", "short", "medium", "long")

_LEVEL_LABELS = {
    "none": "Don't apply any rule",
    "simple": "Simple words",
    "normal": "Normal",
    "advanced": "Advanced",
}
_LEVEL_ORDER = ("none", "simple", "normal", "advanced")

_SECTION_FIELD = {
    "layout": "layout",
    "formatting": "simple_formatting",
    "length": "answer_length",
    "level": "language_level",
}


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
    if section == "formatting":
        label = _FORMATTING_LABELS[value]
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
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="_:set:close")])
    text = (
        "⚙️ <b>Settings</b>\n\n"
        "Per-chat preferences for how Claude replies here. "
        "Tap a section to change it."
    )
    return text, InlineKeyboardMarkup(rows)


_SECTION_INTRO = {
    "layout": "How a finished turn appears in the chat.",
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

    if section == "layout":
        order, labels = _LAYOUT_ORDER, _LAYOUT_LABELS
    elif section == "formatting":
        order, labels = _FORMATTING_ORDER, _FORMATTING_LABELS
    elif section == "length":
        order, labels = _LENGTH_ORDER, _LENGTH_LABELS
    else:  # "level"
        order, labels = _LEVEL_ORDER, _LEVEL_LABELS

    rows = []
    for value in order:
        marker = " ✅" if value == current else ""
        # `simple_formatting` values are bools; every other field's values
        # already double as their own callback-data tokens.
        token = "on" if value is True else "off" if value is False else value
        rows.append([InlineKeyboardButton(
            f"{labels[value]}{marker}",
            callback_data=f"_:set:{section}:{token}",
        )])
    rows.append([InlineKeyboardButton("« Back", callback_data="_:set:back")])

    text = f"{_SECTION_TITLES[section]}\n\n{_SECTION_INTRO[section]}"
    return text, InlineKeyboardMarkup(rows)
