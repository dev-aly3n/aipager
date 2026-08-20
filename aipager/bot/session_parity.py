"""Chat-side session parity: restart/rename/delete/diff commands, the
per-session "⋮" menu, and per-session preferences.

Stream B of design.md ("Sync Telegram commands with the Mini App"). Owns
this one module in full — see ``research/ship/sync-commands-with-mini-app/
entrypoints.md`` for the binding function-name / callback-data contract a
black-box Tester exercises. Everything not named in that document (every
``_``-prefixed helper below) is an internal implementation detail.

Design constraints this module is written to (see design.md Alternatives
+ Non-negotiable orchestrator rules):

- No module-level mutable state. All pending state (the per-chat rename
  capture, the per-chat session→preferences index) lives on
  lazily-initialised ``TelegramBot`` instance attributes
  (``bot._rename_pending``, ``bot._session_pref_index``), created via a
  ``getattr``/``setattr`` guard rather than a ``core.py`` edit — a module
  dict would leak across pytest tests that build a fresh ``TelegramBot``
  per test; an instance attribute dies with the instance.
- Every session-scoped write (restart, rename, delete, and a per-session
  preference set/clear) is gated by ``bot._can_prompt_user`` at the exact
  moment of the mutating tap/text — not only when the surface offering it
  was drawn. Destructive actions (restart, delete) re-check the gate at
  BOTH the "show confirm" step and the "confirm" step independently,
  since the two taps can be arbitrarily far apart in time.
- A stale ``_:spref:<idx>`` index (a session killed/deleted/renamed away
  between the index being registered and the tap arriving) fails closed
  ("no longer available") rather than silently touching whatever now
  sits at that index.
- Reachable only through the exported functions below — this module is
  never imported by any shared file in this worktree (that wiring is the
  integrator's job, applied after all three streams land; see
  ``implementation-parity.md``).

This commit lands per-session preferences only — the single-renderer
piece design.md's decision #3 asks for
(``render_session_preferences_root`` / ``render_session_preferences_field``)
plus the ``_:spref...`` callback family that reaches it. Restart, rename,
delete, diff, and the ⋮ menu land in their own follow-up commits.
"""

from __future__ import annotations

import html as html_mod
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from aipager.bot import settings_menu
from aipager.bot.transport import calling_chat_id
from aipager.preferences import get_preferences, is_valid_value, resolve_preferences
from aipager.state import PREFERENCE_OVERRIDE_FIELDS, TrackedSession

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

# Schema computed once at import time — settings_menu.settings_schema()
# is a pure function of module-level constants, so there is no reason to
# recompute it on every render. Keyed by section for O(1) lookup.
_SCHEMA = {entry["section"]: entry for entry in settings_menu.settings_schema()}

# Populated by later commits (restart/rename/delete/diff/menu). Empty for
# now, so handle_callback's `{name}:<action>` family always falls through
# (returns False) until those land.
_SESSION_ACTIONS: frozenset[str] = frozenset()


# ---- lazily-initialised TelegramBot instance state -----------------------

def _pref_index_map(bot: "TelegramBot") -> dict[int, list[str]]:
    """``bot._session_pref_index`` — created on first use, never at
    ``TelegramBot.__init__`` (that would need a core.py edit, which this
    stream does not own). See entrypoints.md's per-session-preferences
    section for the exact attribute name — it is part of the contract.
    """
    index = getattr(bot, "_session_pref_index", None)
    if index is None:
        index = {}
        bot._session_pref_index = index
    return index


def _register_pref_index(bot: "TelegramBot", chat_id: int, names) -> list[str]:
    """Overwrite this chat's session→preferences index with a fresh
    snapshot. Called every time a picker or a single session's ⋮ menu
    "Preferences" row is rendered — never reused across renders, so a
    session that disappears between render and tap fails closed rather
    than resolving to whatever now occupies that slot."""
    names = list(names)
    _pref_index_map(bot)[chat_id] = names
    return names


def _resolve_pref_index(
    bot: "TelegramBot", chat_id: int, idx_token: str,
) -> TrackedSession | None:
    """``None`` for a malformed token, an out-of-range index, or a
    session that no longer exists in the registry — the caller treats
    all three identically ("no longer available"), never distinguishing
    them to the user and never writing to a different session than the
    one that was actually shown."""
    try:
        idx = int(idx_token)
    except (TypeError, ValueError):
        return None
    names = _pref_index_map(bot).get(chat_id) or []
    if idx < 0 or idx >= len(names):
        return None
    return bot.registry.get(names[idx])


# ---- small pure helpers ---------------------------------------------------

def _token_for_value(value) -> str:
    """Mirrors settings_menu.render_settings_section's inline
    ``bool → "on"/"off"`` mapping — every other field's values already
    double as their own callback-data token."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value)


def _value_for_token(section: str, token: str):
    """Inverse of :func:`_token_for_value`, scoped to one section's
    option list. ``None`` for a token that doesn't match any option —
    the caller treats that as "invalid value", same contract as
    ``is_valid_value`` returning ``False``."""
    entry = _SCHEMA.get(section)
    if entry is None:
        return None
    for option in entry["options"]:
        if _token_for_value(option["value"]) == token:
            return option["value"]
    return None


async def _edit(query, text: str, kb: InlineKeyboardMarkup | None) -> None:
    """Edit the tapped message in place. Swallows "message not modified"
    / "message to edit not found" the same way every other callback
    branch in this codebase already does — a toast already told the user
    what happened; a failed edit must never surface as a crash."""
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


# ---- per-session preferences ----------------------------------------

def _render_session_pref_picker(
    bot: "TelegramBot", chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """``_:spref`` — every session in scope, freshly indexed on each
    render (entrypoints.md: "populated fresh every time the picker ...
    is rendered")."""
    sessions = sorted(
        (s for s in bot.registry.all_sessions(chat_id).values() if s.label),
        key=lambda s: s.label.lower(),
    )
    _register_pref_index(bot, chat_id, [s.name for s in sessions])
    rows = [
        [InlineKeyboardButton(sess.label, callback_data=f"_:spref:{i}")]
        for i, sess in enumerate(sessions)
    ]
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="_:spref:close")])
    if sessions:
        text = "👤 <b>Per-session preferences</b>\n\nPick a session to override its reply style."
    else:
        text = "👤 <b>Per-session preferences</b>\n\nNo sessions yet."
    return text, InlineKeyboardMarkup(rows)


def render_session_preferences_root(
    sess: TrackedSession, chat_id: int, *, cb_prefix: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Root view: one row per settable field with its current EFFECTIVE
    value and an override marker, mirroring
    ``settings_menu.render_settings_root``'s shape but scoped to one
    session. Called from exactly two sites — ``/settings → Per-session``
    and a session's ⋮ menu "Preferences" row — with a different
    ``cb_prefix`` per call (design.md decision #3): the two paths can
    never show different fields, labels, or current-value markers
    because they're the same function."""
    overrides = sess.preference_overrides()
    effective = resolve_preferences(sess.scope_chat_id or 0, overrides)
    rows = []
    for entry in settings_menu.settings_schema():
        section, field = entry["section"], entry["field"]
        value = getattr(effective, field)
        label = next(
            (o["label"] for o in entry["options"] if o["value"] == value), str(value),
        )
        # A plain star, not the pencil emoji settings_menu's own
        # "formatting" section title already carries (_SECTION_TITLES's
        # "✏️ Simple formatting") — reusing that glyph as an override
        # marker would make every row under that one section look
        # overridden even when it isn't.
        marker = " ⭐" if field in overrides else ""
        rows.append([InlineKeyboardButton(
            f"{entry['title']}: {label}{marker}",
            callback_data=f"{cb_prefix}:{section}",
        )])
    rows.append([InlineKeyboardButton("« Back", callback_data="_:spref")])
    text = (
        f"👤 <b>Per-session preferences — [{html_mod.escape(sess.label)}]</b>\n\n"
        "Overrides this session's reply style. Unset fields fall back to "
        "this chat's /settings. ⭐ marks a field this session has overridden."
    )
    return text, InlineKeyboardMarkup(rows)


def render_session_preferences_field(
    sess: TrackedSession, chat_id: int, section: str, *, cb_prefix: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """A field's value list + "Use chat default" + Back, or ``None`` for
    an unrecognised ``section`` — same contract as
    ``settings_menu.render_settings_section``."""
    entry = _SCHEMA.get(section)
    if entry is None:
        return None
    field = entry["field"]
    overrides = sess.preference_overrides()
    overridden = field in overrides
    override_value = overrides.get(field)
    scope_value = getattr(get_preferences(sess.scope_chat_id or 0), field)

    rows = []
    for option in entry["options"]:
        value = option["value"]
        token = _token_for_value(value)
        marker = " ✅" if overridden and value == override_value else ""
        default_tag = " (chat default)" if value == scope_value else ""
        rows.append([InlineKeyboardButton(
            f"{option['label']}{default_tag}{marker}",
            callback_data=f"{cb_prefix}:{section}:{token}",
        )])
    rows.append([InlineKeyboardButton(
        "↩️ Use chat default" + ("" if overridden else " ✅"),
        callback_data=f"{cb_prefix}:{section}:default",
    )])
    rows.append([InlineKeyboardButton("« Back", callback_data=cb_prefix)])

    text = (
        f"{entry['title']} — for [<b>{html_mod.escape(sess.label)}</b>]\n\n"
        f"{entry['title']} this session uses, overriding this chat's own "
        "/settings just for it."
    )
    return text, InlineKeyboardMarkup(rows)


async def _handle_spref_callback(
    bot: "TelegramBot", query, chat_id: int, action: str,
) -> bool:
    """Dispatch for every ``_:spref...`` callback. ``action`` is the raw
    string after the leading ``_:`` sentinel — see the parts breakdown
    in the docstring of :func:`handle_callback`."""
    rest = action[len("spref"):]  # "" | ":<idx>" | ":<idx>:<section>" | ":<idx>:<section>:<token>"
    parts = [p for p in rest.split(":") if p != ""]

    if not parts:
        text, kb = _render_session_pref_picker(bot, chat_id)
        await _edit(query, text, kb)
        return True

    if len(parts) == 1:
        if parts[0] == "close":
            await _edit(query, "Closed.", None)
            return True
        sess = _resolve_pref_index(bot, chat_id, parts[0])
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        text, kb = render_session_preferences_root(
            sess, chat_id, cb_prefix=f"_:spref:{parts[0]}",
        )
        await _edit(query, text, kb)
        return True

    if len(parts) == 2:
        idx_token, section = parts
        sess = _resolve_pref_index(bot, chat_id, idx_token)
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        rendered = render_session_preferences_field(
            sess, chat_id, section, cb_prefix=f"_:spref:{idx_token}",
        )
        if rendered is None:
            await bot._safe_answer(query, "Invalid callback")
            return True
        text, kb = rendered
        await _edit(query, text, kb)
        return True

    if len(parts) == 3:
        idx_token, section, token = parts
        sess = _resolve_pref_index(bot, chat_id, idx_token)
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        user_id = query.from_user.id if getattr(query, "from_user", None) else None
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't change this session's preferences.")
            return True
        entry = _SCHEMA.get(section)
        if entry is None:
            await bot._safe_answer(query, "Invalid callback")
            return True
        field = entry["field"]
        attr = PREFERENCE_OVERRIDE_FIELDS[field]
        if token == "default":
            setattr(sess, attr, None)
            bot.registry.mark_dirty()
        else:
            value = _value_for_token(section, token)
            if value is None or not is_valid_value(field, value):
                await bot._safe_answer(query, "Invalid value")
                return True
            setattr(sess, attr, value)
            bot.registry.mark_dirty()
        rendered = render_session_preferences_field(
            sess, chat_id, section, cb_prefix=f"_:spref:{idx_token}",
        )
        if rendered is not None:
            text, kb = rendered
            await _edit(query, text, kb)
        return True

    await bot._safe_answer(query, "Invalid callback")
    return True


# ---- text capture (rename) --------------------------------------------

async def maybe_handle_text(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str,
) -> bool:
    """Always ``False`` until the rename commit lands — placeholder so
    the shared-file integration snippet (which calls this unconditionally
    from ``_handle_message``) has a stable target from the first commit
    onward."""
    return False


# ---- callback dispatch -------------------------------------------------

async def handle_callback(
    bot: "TelegramBot", update: Update, query, session_name: str, action: str,
) -> bool:
    """``True`` iff this callback was handled here — the caller
    (``callbacks.py``'s ``_handle_callback``) returns immediately in
    that case; ``False`` lets it fall through to its own existing
    branches (kill/stop/settings/etc.) or new_flow's wizard.

    Two disjoint families:
    - ``_:spref...`` (session_name == "_") — per-session preferences,
      dispatched to :func:`_handle_spref_callback`.
    - ``{name}:<action>`` for every action in :data:`_SESSION_ACTIONS`
      (empty until the restart/rename/delete/diff/menu commits land).
    """
    chat_id = calling_chat_id(update)

    if session_name == "_" and action.startswith("spref"):
        return await _handle_spref_callback(bot, query, chat_id, action)

    if action not in _SESSION_ACTIONS:
        return False

    return False


__all__ = [
    "handle_callback",
    "maybe_handle_text",
    "render_session_preferences_field",
    "render_session_preferences_root",
]
