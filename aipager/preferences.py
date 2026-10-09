"""Per-scope reply-style + layout preferences (`/settings`).

Storage precedent: ``~/.config/aipager/preferences.json``, one JSON object
keyed by scope chat id (stringified — JSON object keys must be strings;
group chat ids are negative and round-trip via ``int()``/``str()``). This
mirrors the "built-in defaults + optional JSON override" shape ``config.py``
already uses for ``keyboard.json`` (``config._load_keyboard_overrides``),
but NOT its load-once-at-import timing: settings must take effect on the
very next Telegram-driven prompt with no daemon restart, so this module
keeps a mutable in-memory cache that :func:`set_preference` updates
synchronously and persists on every call.

A scope's stored entry holds only the fields the user has explicitly
changed via the menu — an absent field falls back to its built-in
default (``layout`` additionally consults the ``KEEP_FINISHED_CARD`` seed
below). A scope with no entry at all behaves exactly like v0.5.0.

Fail-safe rules (never crash the daemon, never lose an unrelated scope's
settings — see design.md for the full rationale):
- Missing file → empty in-memory store; nothing is written until the
  first :func:`set_preference` call.
- Unparseable file / non-object root → log once, treat as empty for the
  rest of this process's life. The next successful write replaces it.
- A per-scope value that isn't a JSON object → that scope alone is
  treated as empty; every other scope is unaffected.
- A field with an unrecognised value → that field alone is treated as
  absent (default applies); the scope's other fields still load.
- :func:`set_preference` validates field name + value against a fixed
  allow-list *before* touching the cache or disk; invalid input raises
  ``ValueError`` and nothing is written.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from aipager._test_guard import check_write
from aipager.config import KEEP_FINISHED_CARD

log = logging.getLogger(__name__)

_PREFERENCES_PATH = Path.home() / ".config" / "aipager" / "preferences.json"

_VALID_LAYOUT = ("card", "merged", "replace")
_VALID_ANSWER_LENGTH = ("none", "xshort", "short", "medium", "long")
_VALID_LANGUAGE_LEVEL = ("none", "simple", "normal", "advanced")

# One validator per settable field — used both to sanitise a value coming
# off disk (per-field fallback) and to reject a bad `set_preference` call
# before anything is written.
_FIELD_VALIDATORS = {
    "layout": lambda v: v in _VALID_LAYOUT,
    "simple_formatting": lambda v: isinstance(v, bool),
    "diff_preview": lambda v: isinstance(v, bool),
    "card_age_decay": lambda v: isinstance(v, bool),
    "answer_length": lambda v: v in _VALID_ANSWER_LENGTH,
    "language_level": lambda v: v in _VALID_LANGUAGE_LEVEL,
}

# Sentinel distinct from every legal value (including `False`) so a
# missing field can be told apart from an explicitly-stored one.
_MISSING = object()


@dataclass(frozen=True)
class Preferences:
    layout: str
    simple_formatting: bool
    answer_length: str
    language_level: str
    # Post each Write/Edit as its own diff message under the busy card.
    # Off by default: the busy card already lists every edit, and the
    # previews sit outside the background-job model (an agent's edits
    # would land between the busy card and the job's single answer).
    # Defaulted here so every existing keyword construction stays valid.
    diff_preview: bool = False
    # Slow the busy card as a turn gets long (roadmap 8.30): from 2 / 10
    # minutes the card is refreshed at most every 10 / 30 s (never slower
    # than 30 s, however long the turn) and counts in minutes, then hours
    # from an hour. ON by default — it is what keeps a
    # four-hour turn from editing one message thousands of times. Off is
    # 0.7.13's cadence for the whole turn.
    card_age_decay: bool = True


# In-memory cache: None means "not loaded yet" (distinct from a loaded-
# but-empty store, `{}`). Populated lazily on first read or write so a
# process that never touches preferences never even stats the file.
_cache: dict[str, object] | None = None


def _load_raw() -> dict[str, object]:
    """Read the on-disk store, degrading to `{}` on any failure.

    Never raises. The corrupt file itself is left untouched on disk —
    only the next successful :func:`set_preference` overwrites it — so a
    read-only failure never destroys evidence of what went wrong.
    """
    if not _PREFERENCES_PATH.exists():
        return {}
    try:
        data = json.loads(_PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("preferences.json could not be loaded (%s); using defaults", e)
        return {}
    if not isinstance(data, dict):
        log.warning("preferences.json root must be an object; using defaults")
        return {}
    return data


def _ensure_loaded() -> dict[str, object]:
    global _cache
    if _cache is None:
        _cache = _load_raw()
    return _cache


def _save_raw(store: dict[str, object]) -> None:
    """Atomic write: temp file + ``os.replace`` (mirrors
    ``policy_snapshot.write_snapshot``) — a daemon restart mid-write never
    observes a torn file. Best-effort: a write failure is logged, not
    raised, so a read-only filesystem can't crash a prompt handler.
    """
    check_write(_PREFERENCES_PATH)
    try:
        _PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PREFERENCES_PATH.with_suffix(_PREFERENCES_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, _PREFERENCES_PATH)
    except OSError:
        log.warning("could not write %s", _PREFERENCES_PATH, exc_info=True)


def _default_layout() -> str:
    """`KEEP_FINISHED_CARD` seeds the layout default for a scope that has
    never stored one — `"card"` reproduces v0.5.0's default behaviour,
    `"replace"` reproduces the pre-existing `KEEP_FINISHED_CARD=0` knob."""
    return "card" if KEEP_FINISHED_CARD else "replace"


_FIELD_DEFAULTS = {
    "simple_formatting": False,
    "diff_preview": False,
    "card_age_decay": True,
    "answer_length": "none",
    "language_level": "none",
}


def _resolve_field(scope_raw: dict, field: str, default):
    value = scope_raw.get(field, _MISSING)
    if value is _MISSING:
        return default
    if not _FIELD_VALIDATORS[field](value):
        # Unrecognised value for this one field — fall back to default,
        # but don't touch the rest of the scope's fields.
        return default
    return value


def get_preferences(chat_id: int) -> Preferences:
    """Return the fully-resolved SCOPE preferences for ``chat_id``, with
    no session override applied — the scope's own value, ignoring what
    any individual session may have chosen for itself.

    Never raises, never returns ``None`` — a chat_id never seen before,
    or a preferences.json that doesn't exist at all, resolves to the
    same built-in defaults v0.5.0 always used.

    A caller that has a ``TrackedSession`` in hand (the prompt-injection
    path, idle-notification layout, or any future one) must call
    :func:`resolve_preferences` with ``sess.preference_overrides()``
    instead of this function directly — otherwise that session's
    override silently does nothing, because this function has no way to
    see it. This function stays the right (and only) choice for
    genuinely scope-wide reads: rendering ``/settings`` itself and the
    Mini App's scope-wide Settings tab, neither of which has a session.
    """
    store = _ensure_loaded()
    raw = store.get(str(chat_id))
    scope_raw = raw if isinstance(raw, dict) else {}
    return Preferences(
        layout=_resolve_field(scope_raw, "layout", _default_layout()),
        simple_formatting=_resolve_field(
            scope_raw, "simple_formatting", _FIELD_DEFAULTS["simple_formatting"],
        ),
        answer_length=_resolve_field(
            scope_raw, "answer_length", _FIELD_DEFAULTS["answer_length"],
        ),
        language_level=_resolve_field(
            scope_raw, "language_level", _FIELD_DEFAULTS["language_level"],
        ),
        diff_preview=_resolve_field(
            scope_raw, "diff_preview", _FIELD_DEFAULTS["diff_preview"],
        ),
        card_age_decay=_resolve_field(
            scope_raw, "card_age_decay", _FIELD_DEFAULTS["card_age_decay"],
        ),
    )


def is_valid_value(field: str, value: object) -> bool:
    """``True`` iff ``field`` is one of the settable fields and
    ``value`` is one of that field's allowed options. Never raises —
    an unknown field name returns ``False`` rather than ``KeyError``,
    because this is also used to sanity-check untrusted input (a
    session override read back off disk, a Mini App request body)
    where "not a real field" must be a normal, checkable outcome, not
    an exception the caller has to guard against separately.

    The single allow-list both scope writes (:func:`set_preference`)
    and session-override writes validate through — so a value the Mini
    App's per-session route accepts is always one chat's ``/settings``
    would also accept, and vice versa.
    """
    validator = _FIELD_VALIDATORS.get(field)
    return validator is not None and validator(value)


def resolve_preferences(
    scope_chat_id: int,
    session_overrides: Mapping[str, object] | None = None,
) -> Preferences:
    """The one function both chat and the Mini App must call for a
    session's effective preferences: a session override wins field by
    field over the scope's own value.

    Pure — never mutates ``session_overrides`` or the scope cache, and
    always returns a fresh :class:`Preferences` (``dataclasses.replace``
    over ``get_preferences(scope_chat_id)``, never the scope's own
    object mutated in place).

    With ``session_overrides`` ``None`` or ``{}`` this is byte-for-byte
    identical to ``get_preferences(scope_chat_id)`` — a session with no
    overrides inherits the scope exactly as if the per-session layer
    did not exist.

    A key present in ``session_overrides`` wins over the scope value
    only when :func:`is_valid_value` accepts it for that field; an
    absent key, or a value that fails validation, falls back to the
    scope value for that one field alone. This is the same fail-safe
    philosophy :func:`_resolve_field` already applies to a scope's own
    stored fields, now applied on every call rather than only at load
    time — a hand-edited state file, or an option a later release
    removed, degrades to "unset" for just that field instead of
    raising or corrupting the other three.
    """
    scope_prefs = get_preferences(scope_chat_id)
    if not session_overrides:
        return scope_prefs
    changes = {
        field: value
        for field, value in session_overrides.items()
        if is_valid_value(field, value)
    }
    if not changes:
        return scope_prefs
    return replace(scope_prefs, **changes)


def set_preference(chat_id: int, field: str, value: object) -> Preferences:
    """Validate + persist one field for ``chat_id``, returning the scope's
    newly-resolved :class:`Preferences`.

    Raises ``ValueError`` for an unknown field name or a value outside
    that field's allowed set — validated *before* the cache or disk are
    touched, so a rejected call never writes anything.
    """
    if field not in _FIELD_VALIDATORS:
        raise ValueError(f"unknown preference field: {field!r}")
    if not is_valid_value(field, value):
        raise ValueError(f"invalid value for {field!r}: {value!r}")

    store = _ensure_loaded()
    key = str(chat_id)
    existing = store.get(key)
    # A corrupt (non-object) scope entry is replaced outright rather than
    # merged into — see the fail-safe rules in the module docstring.
    scope_raw = dict(existing) if isinstance(existing, dict) else {}
    scope_raw[field] = value
    store[key] = scope_raw
    _save_raw(store)
    return get_preferences(chat_id)


def move_chat(old: int, new: int) -> bool:
    """Move chat *old*'s entry (reply style and new-session defaults) to
    *new* (roadmap 8.87: Telegram upgraded the group to a supergroup and
    gave it a new id). Nothing moves when *old* has no entry, or when
    *new* already has one (it is never overwritten). Returns whether it
    moved. Best-effort write, like every other setter here."""
    store = _ensure_loaded()
    old_key, new_key = str(old), str(new)
    if old_key not in store or new_key in store:
        return False
    store[new_key] = store.pop(old_key)
    _save_raw(store)
    return True


# ---- new-session defaults (/settings → 🆕 New sessions, and /new) -------
#
# What a session started from chat gets when the person starting it does
# not choose: mode, model and folder. Stored in the same per-chat entry as
# the reply style, under their own keys, and deliberately NOT part of
# :class:`Preferences` or ``settings_schema`` (the reply style, shared with
# the Mini App and with per-session overrides). Validated here for shape;
# the model and folder are re-checked by their caller at use time against
# the lists that are true then (MODEL_CHOICES, ``launch.allowed_roots``).

_NEW_SESSION_KEYS = {
    "mode": "new_session_mode",
    "model": "new_session_model",
    "cwd": "new_session_cwd",
}

_NEW_SESSION_VALIDATORS = {
    "mode": lambda v: v in ("auto", "ask"),
    "model": lambda v: isinstance(v, str) and 0 < len(v) <= 100,
    "cwd": lambda v: isinstance(v, str) and v.startswith("/") and len(v) <= 4096,
}


@dataclass(frozen=True)
class NewSessionDefaults:
    """A chat's stored defaults for new sessions. Empty means "not chosen":
    ``mode`` then falls to Auto for a caller who may use it, ``model`` to
    Claude Code's own default, ``cwd`` to the project directory."""
    mode: str = ""
    model: str = ""
    cwd: str = ""


def get_new_session_defaults(chat_id: int) -> NewSessionDefaults:
    """Never raises. A stored value of the wrong shape reads as unset."""
    store = _ensure_loaded()
    raw = store.get(str(chat_id))
    scope_raw = raw if isinstance(raw, dict) else {}
    values = {}
    for field, key in _NEW_SESSION_KEYS.items():
        value = scope_raw.get(key, "")
        values[field] = value if _NEW_SESSION_VALIDATORS[field](value) else ""
    return NewSessionDefaults(**values)


def set_new_session_default(chat_id: int, field: str, value: str) -> NewSessionDefaults:
    """Store one default for ``chat_id``; an empty ``value`` clears it
    (back to the built-in default). Raises ``ValueError`` for an unknown
    field or a value of the wrong shape, before anything is written."""
    if field not in _NEW_SESSION_KEYS:
        raise ValueError(f"unknown new-session default: {field!r}")
    if value != "" and not _NEW_SESSION_VALIDATORS[field](value):
        raise ValueError(f"invalid value for {field!r}: {value!r}")
    store = _ensure_loaded()
    key = str(chat_id)
    existing = store.get(key)
    scope_raw = dict(existing) if isinstance(existing, dict) else {}
    if value == "":
        scope_raw.pop(_NEW_SESSION_KEYS[field], None)
    else:
        scope_raw[_NEW_SESSION_KEYS[field]] = value
    store[key] = scope_raw
    _save_raw(store)
    return get_new_session_defaults(chat_id)


# ---- problem reports (/settings in the owner's DM, roadmap 8.112) ------
#
# "Problem reports: Ask me / Off": whether aipager may OFFER to send a
# report on its own. One value per install (the owner's), kept under its
# own top-level key, not in any chat's entry: it is not a reply style, so
# it stays out of :class:`Preferences`, ``settings_schema()`` (every chat's
# Mini App settings) and per-session overrides. Nothing here reads a key
# as a chat id except :func:`get_preferences` and friends, which look up
# ``str(chat_id)`` only.

PROBLEM_REPORTS_VALUES = ("ask", "off")
_INSTALL_KEY = "_install"
_PROBLEM_REPORTS_KEY = "problem_reports"


def get_problem_reports() -> str:
    """``"ask"`` (the default) or ``"off"``. A stored value that is
    neither reads as ``"off"``: a value we did not write errs quiet.
    Never raises."""
    try:
        store = _ensure_loaded()
        entry = store.get(_INSTALL_KEY)
        if not isinstance(entry, dict) or _PROBLEM_REPORTS_KEY not in entry:
            return "ask"
        value = entry[_PROBLEM_REPORTS_KEY]
        return value if value in PROBLEM_REPORTS_VALUES else "off"
    except Exception:  # noqa: BLE001 - a broken file must not stop a tick
        log.debug("could not read the problem reports preference", exc_info=True)
        return "off"


def set_problem_reports(value: str) -> str:
    """Store ``"ask"`` or ``"off"``; anything else raises ``ValueError``
    before anything is written. Returns the stored value."""
    if not isinstance(value, str) or value not in PROBLEM_REPORTS_VALUES:
        raise ValueError(f"invalid problem reports value: {value!r}")
    store = _ensure_loaded()
    existing = store.get(_INSTALL_KEY)
    entry = dict(existing) if isinstance(existing, dict) else {}
    entry[_PROBLEM_REPORTS_KEY] = value
    store[_INSTALL_KEY] = entry
    _save_raw(store)
    return get_problem_reports()


_STYLE_LEAD_IN = (
    "Apply this reply guidance to your next answer; "
    "do not mention or quote it:"
)

# Always injected, independent of /settings ("status-line-at-card-bottom";
# reworded by roadmap 8.42): aipager delivers a background job's interim
# answer the moment the turn ends, and the agents' results arrive later as
# a message of their own — so saying they will follow is true, and the
# model should not hold anything back for a combined reply.
_DELIVERY_LINE = (
    "If you start background agents, your reply is delivered now; results "
    "from those agents arrive later as a separate message."
)

_FORMATTING_LINE = (
    "Reply in plain prose and simple dashed lists only. No tables (use a "
    "list instead), no code blocks, no headings, no bold/italics."
)

_LENGTH_LINES = {
    "xshort": "Answer in one or two sentences. No preamble, no summary.",
    "short": "Keep the answer short — a few sentences.",
    "medium": "Keep the answer to a medium length — a short paragraph or two.",
    "long": "Give a long, thorough answer.",
}

_LEVEL_LINES = {
    "simple": "Use simple, everyday words.",
    "normal": "Use plain, professional language — no unnecessary jargon.",
    "advanced": (
        "Use precise technical language; do not simplify vocabulary or "
        "explain basics."
    ),
}


def style_text(prefs: Preferences) -> str:
    """Pure. The `UserPromptSubmit`-hook instruction block for ``prefs``.

    Fixed bullet order: formatting, length, level — matching design.md's
    "Exact injected text" section verbatim — followed by the unconditional
    delivery rule (:data:`_DELIVERY_LINE`), which is why this never
    returns ``""`` any more: every Telegram-originated prompt carries it,
    since the model cannot know up front whether it will background an
    agent ("status-line-at-card-bottom").
    """
    bullets: list[str] = []
    if prefs.simple_formatting:
        bullets.append(_FORMATTING_LINE)
    length_line = _LENGTH_LINES.get(prefs.answer_length)
    if length_line:
        bullets.append(length_line)
    level_line = _LEVEL_LINES.get(prefs.language_level)
    if level_line:
        bullets.append(level_line)
    # The delivery rule is unconditional: it is not a /settings option but
    # a fact about how aipager delivers this session's messages, and the
    # model cannot know in advance whether it will background an agent.
    bullets.append(_DELIVERY_LINE)
    return _STYLE_LEAD_IN + "\n" + "\n".join(f"- {b}" for b in bullets)
