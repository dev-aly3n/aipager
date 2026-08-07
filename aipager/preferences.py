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
from dataclasses import dataclass
from pathlib import Path

from aipager.config import KEEP_FINISHED_CARD

log = logging.getLogger(__name__)

_PREFERENCES_PATH = Path.home() / ".config" / "aipager" / "preferences.json"

_VALID_LAYOUT = ("card", "merged", "replace")
_VALID_ANSWER_LENGTH = ("none", "short", "medium", "long")
_VALID_LANGUAGE_LEVEL = ("none", "simple", "normal", "advanced")

# One validator per settable field — used both to sanitise a value coming
# off disk (per-field fallback) and to reject a bad `set_preference` call
# before anything is written.
_FIELD_VALIDATORS = {
    "layout": lambda v: v in _VALID_LAYOUT,
    "simple_formatting": lambda v: isinstance(v, bool),
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
    """Return the fully-resolved preferences for ``chat_id``.

    Never raises, never returns ``None`` — a chat_id never seen before,
    or a preferences.json that doesn't exist at all, resolves to the
    same built-in defaults v0.5.0 always used.
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
    )


def set_preference(chat_id: int, field: str, value: object) -> Preferences:
    """Validate + persist one field for ``chat_id``, returning the scope's
    newly-resolved :class:`Preferences`.

    Raises ``ValueError`` for an unknown field name or a value outside
    that field's allowed set — validated *before* the cache or disk are
    touched, so a rejected call never writes anything.
    """
    validator = _FIELD_VALIDATORS.get(field)
    if validator is None:
        raise ValueError(f"unknown preference field: {field!r}")
    if not validator(value):
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


_STYLE_LEAD_IN = (
    "Apply this reply-style guidance to your next answer; "
    "do not mention or quote it:"
)

_FORMATTING_LINE = (
    "Reply in plain prose and simple dashed lists only. No tables (use a "
    "list instead), no code blocks, no headings, no bold/italics."
)

_LENGTH_LINES = {
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
    """Pure. The `UserPromptSubmit`-hook instruction block for ``prefs``,
    or ``""`` when every style option is at its default (formatting off,
    length/level both "none") — the common case for any scope that has
    never touched `/settings`, and the hook's cue to print nothing at all.

    Fixed bullet order: formatting, length, level — matching design.md's
    "Exact injected text" section verbatim.
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
    if not bullets:
        return ""
    return _STYLE_LEAD_IN + "\n" + "\n".join(f"- {b}" for b in bullets)
