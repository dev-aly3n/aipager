"""The language state aipager keeps on disk (multilingual phase 5).

One file, ``config.LANGUAGE_STATE_FILE`` (``~/.local/state/aipager/
languages.json``), read LATE so the test redirect is one entry. It holds
what the daemon itself learns or does, never a person's choice (that is
``ui_language`` in preferences.json):

- ``launch``: the existing-install marker, ``{"at", "version", "fresh",
  "people"}``. Written ONCE, by the first start that runs this code, and
  never rewritten. A person in ``people`` existed before languages were
  switched on and keeps English until they choose. ``fresh`` is true when
  aipager had never run on this machine before, and ``people`` is then
  empty.
- ``seen``: user id -> the last Telegram language aipager saw from that
  person in their private chat (at most :data:`SEEN_CAP`, oldest first
  out), so a restart does not flip a chat that follows Telegram back to
  English.
- ``offered`` / ``first_contact``: chat id -> when the one-time language
  offer card or first-contact line went out.
- ``descriptions``: Telegram code -> ``{"description", "short"}``, a
  sha256 prefix of the bot description text aipager last set.

Fail-safe: a missing file, or a ``seen``/``offered``/``first_contact``/
``descriptions`` section of the wrong shape, reads as empty. A file that is
there but cannot be read, is oversized, is not a JSON object or holds a
``launch`` marker of the wrong shape is DAMAGED: it is moved aside to
``languages.json.bad-<unix time>`` (never overwritten, so the operator can
recover it) and the new file records ``damaged``. While it does, there is
no marker, so everyone counts as existing (English, exactly as before),
and the one-time offer is never sent (its records are in the old file).
Restoring the old file or deleting languages.json ends it; until then
:func:`warn_if_damaged` logs a warning at every start. Writes are
synchronous,
only when a value changes, atomic and owner-only
(``private_file.write_private``) behind ``_test_guard.check_write``.
Nothing here raises into a caller. Nothing here imports ``aipager.bot``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from aipager._test_guard import check_write
from aipager.private_file import write_private

log = logging.getLogger(__name__)

__all__ = ["SEEN_CAP", "description_hash", "ensure_launch_marker", "first_contact_done",
           "is_damaged", "is_existing_person", "launch_marker", "mark_first_contact_done",
           "mark_offer_done", "note_prior_run", "offer_done", "record_description",
           "reset", "seen", "set_seen", "warn_if_damaged"]

VERSION = 1
#: How many people's last-seen Telegram language are kept.
SEEN_CAP = 2000
#: A file bigger than this is not ours: it reads as empty.
MAX_BYTES = 1_000_000
DESCRIPTION_KINDS = ("description", "short")

#: The loaded document; None means not read yet.
_doc: dict | None = None
#: What :func:`note_prior_run` found in this process; None means it was
#: not called (the marker then errs to "existing").
_prior_run: bool | None = None
#: True when a damaged file could not be moved aside: nothing is written
#: over it in this process.
_no_write: bool = False
#: True once this process logged the damage (when it found the file, or
#: through :func:`warn_if_damaged`): one warning per start.
_damage_logged: bool = False
#: The key a damaged file leaves in the new document.
DAMAGED = "damaged"


def _path() -> Path:
    from aipager import config

    return Path(config.LANGUAGE_STATE_FILE)


def _int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _load() -> dict:
    path = _path()
    if not os.path.lexists(path):
        return {}
    try:
        if path.stat().st_size > MAX_BYTES:
            return _set_aside(path, "oversized")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as e:
        return _set_aside(path, type(e).__name__)
    if not isinstance(document, dict):
        return _set_aside(path, "not_an_object")
    if "launch" in document and _valid_marker(document["launch"]) is None:
        return _set_aside(path, "bad_launch_marker")
    return document


def _set_aside(path: Path, reason: str) -> dict:
    """Move the damaged file *path* aside and return the document that
    replaces it, which records the damage. When it cannot be moved,
    nothing is written over it in this process."""
    global _no_write, _damage_logged
    now = int(time.time())
    target = path.with_name(f"{path.name}.bad-{now}")
    try:
        check_write(target)
        os.replace(path, target)
        moved = target.name
    except Exception:  # noqa: BLE001 - never raises; never writes over it then
        moved = None
        _no_write = True
    log.warning(
        "language state %s could not be used (%s); %s. Everyone stays English and "
        "no language offer is sent until you restore it or delete %s",
        path.name, reason, f"moved aside to {moved}" if moved else "left in place",
        path.name)
    _damage_logged = True
    return {DAMAGED: {"at": now, "reason": reason, "moved_to": moved}}


def _document() -> dict:
    global _doc
    if _doc is None:
        _doc = _load()
        if DAMAGED in _doc and "v" not in _doc:
            _save()  # the damage outlives this process: no fresh marker next start
    return _doc


def is_damaged() -> bool:
    """Whether the state file was damaged (see the module docstring)."""
    return DAMAGED in _document()


def warn_if_damaged() -> bool:
    """Log the damage as a WARNING unless this process already did, so a
    damaged state is reported at every start, not only the one that found
    it. Returns :func:`is_damaged`. Never raises."""
    global _damage_logged
    try:
        if not is_damaged():
            return False
        if not _damage_logged:
            _damage_logged = True
            record = _document().get(DAMAGED)
            record = record if isinstance(record, dict) else {}
            at = _int(record.get("at"))
            log.warning(
                "language state %s is still marked damaged (%s, found %s, %s). Chats "
                "without a chosen language stay English and no language offer is sent "
                "until you restore it or delete %s",
                _path().name, record.get("reason") or "unknown reason",
                time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "at an "
                "unknown time",
                f"moved aside to {record['moved_to']}" if record.get("moved_to")
                else "left in place", _path().name)
        return True
    except Exception:  # noqa: BLE001 - language state must never stop the daemon
        log.debug("could not report the language state damage", exc_info=True)
        return False


def _section(name: str) -> dict:
    """The section *name*, made empty in memory when it has the wrong
    shape (so a write replaces it)."""
    document = _document()
    value = document.get(name)
    if not isinstance(value, dict):
        value = {}
        document[name] = value
    return value


def _save() -> bool:
    document = _document()
    document["v"] = VERSION
    target = _path()
    if _no_write:
        return False
    try:
        check_write(target)
        write_private(target, json.dumps(document, ensure_ascii=False, sort_keys=True))
        return True
    except Exception:  # noqa: BLE001 - language state must never stop the daemon
        log.debug("could not write %s", target.name, exc_info=True)
        return False


def reset() -> None:
    """Forget the in-memory copy (a restart, for tests)."""
    global _doc, _prior_run, _no_write, _damage_logged
    _doc = None
    _prior_run = None
    _no_write = False
    _damage_logged = False


# ---- the existing-install marker --------------------------------------------

def note_prior_run() -> bool:
    """Whether aipager ran on this machine before this process: any of the
    first-install marker, the sessions file, preferences.json or the flood
    state exists. Called by the daemon before it writes any of them, and
    remembered for :func:`ensure_launch_marker`. Never raises: a doubt
    reads as a prior run (English)."""
    global _prior_run
    try:
        from aipager import config, preferences
        from aipager.report import markers
        found = (markers.read_first_start() is not None
                 or any(Path(p).exists() for p in (config.SESSION_STATE_FILE,
                                                   preferences._PREFERENCES_PATH,
                                                   config.FLOOD_STATE_FILE)))
    except Exception:  # noqa: BLE001 - unknown: treat as an existing install
        log.debug("could not tell a fresh install", exc_info=True)
        found = True
    _prior_run = found
    return found


def _valid_marker(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    people = value.get("people")
    if _int(value.get("at")) is None or not isinstance(people, list):
        return None
    if not all(_int(p) is not None for p in people):
        return None
    return value


def launch_marker() -> dict | None:
    """The marker, or None when there is no valid one (or the file was
    damaged)."""
    if is_damaged():
        return None
    return _valid_marker(_document().get("launch"))


def ensure_launch_marker(people=(), *, prior_run: bool | None = None,
                         now=None) -> dict | None:
    """Write the marker unless a valid one exists; return the marker in
    force, or None while the file is damaged (no marker is written then).
    *people* are the user ids that exist now (used only when there was a
    prior run). *prior_run* None takes :func:`note_prior_run`'s answer, or
    True when it was not called."""
    existing = launch_marker()
    if existing is not None:
        return existing
    if is_damaged():
        return None
    if prior_run is None:
        prior_run = True if _prior_run is None else _prior_run
    ids = sorted({p for p in (_int(x) for x in people) if p is not None and p > 0})
    try:
        from aipager import __version__ as version
    except Exception:  # noqa: BLE001 - the version is a record, not a rule
        version = ""
    marker = {
        "at": int(time.time() if now is None else now),
        "version": str(version),
        "fresh": not prior_run,
        "people": ids if prior_run else [],
    }
    _document()["launch"] = marker
    _save()
    return marker


def is_existing_person(user_id) -> bool:
    """Whether *user_id* existed before languages were switched on. True
    for everyone while no marker is written (errs English)."""
    marker = launch_marker()
    if marker is None:
        return True
    return _int(user_id) in marker["people"]


# ---- the last Telegram language seen from each person -----------------------

def seen(user_id) -> str | None:
    uid = _int(user_id)
    if uid is None:
        return None
    value = _section("seen").get(str(uid))
    return value if isinstance(value, str) and value else None


def set_seen(user_id, code: str | None) -> bool:
    """Record (or, with None, forget) *user_id*'s Telegram language.
    Writes only when it changes; returns whether it did."""
    uid = _int(user_id)
    if uid is None:
        return False
    section = _section("seen")
    key = str(uid)
    current = section.get(key)
    if code:
        if current == code:
            return False
        section.pop(key, None)
        section[key] = code
        while len(section) > SEEN_CAP:
            del section[next(iter(section))]
    else:
        if key not in section:
            return False
        del section[key]
    _save()
    return True


# ---- once-only records ------------------------------------------------------

def _done(section: str, chat_id) -> bool:
    cid = _int(chat_id)
    return cid is not None and str(cid) in _section(section)


def _mark(section: str, chat_id, now) -> None:
    cid = _int(chat_id)
    if cid is None or _done(section, cid):
        return
    _section(section)[str(cid)] = int(time.time() if now is None else now)
    _save()


def offer_done(chat_id) -> bool:
    return _done("offered", chat_id)


def mark_offer_done(chat_id, now=None) -> None:
    _mark("offered", chat_id, now)


def first_contact_done(chat_id) -> bool:
    return _done("first_contact", chat_id)


def mark_first_contact_done(chat_id, now=None) -> None:
    _mark("first_contact", chat_id, now)


# ---- what aipager set as the bot description --------------------------------

def description_hash(telegram_code: str, kind: str) -> str | None:
    entry = _section("descriptions").get(telegram_code)
    value = entry.get(kind) if isinstance(entry, dict) else None
    return value if isinstance(value, str) and value else None


def record_description(telegram_code: str, kind: str, digest: str) -> None:
    if kind not in DESCRIPTION_KINDS:
        raise ValueError(kind)  # not a description kind
    if description_hash(telegram_code, kind) == digest:
        return
    section = _section("descriptions")
    entry = section.get(telegram_code)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry[kind] = digest
    section[telegram_code] = entry
    _save()
