"""Plan usage and the token tally (roadmap 8.131).

Two things are kept here and shown by ``/usage`` and the Mini App's Usage
page (``aipager.bot.usage_cmd``):

1. **Plan readings.** Claude Code hands its status line a ``rate_limits``
   object (``five_hour``, ``seven_day``, ``spend_limit``; any subset, and
   none at all for an API key or a gateway without a spend limit).
   ``aipager-statusline`` writes that payload to the session's status file
   before it sends the daemon a ``statusline`` datagram, and the daemon's
   handler passes the parsed windows to :func:`note_plan`. aipager NEVER
   fetches plan usage itself: no Anthropic or claude.ai request, no extra
   use of anyone's credential (a request made with a person's credential
   that Claude Code did not make can get the account banned). A payload
   without ``rate_limits`` changes nothing (absence never erases: the
   first payload of every session has none). Readings are kept per sign-in
   (:func:`login_for`, kind plus source plus config home) and never merged:
   one sign-in's limits must never be shown as another's.

2. **The token tally.** Per local day, per chat scope and per model, the
   input, output, cache read and cache write tokens Claude Code recorded
   in the transcripts of aipager's sessions, subagents included. Read
   incrementally by byte offset on the session monitor's tick
   (:func:`scan`), at most every ``SCAN_INTERVAL`` seconds and
   ``SCAN_BUDGET_BYTES`` a scan. The status line's token totals are not
   used: in Claude Code 2.1.325 they are the last request's, not a running
   sum, so deltas of them undercount.

   Each ``message.id`` is counted once (Claude Code writes one line per
   content block, every line of a message carrying the same final usage,
   verified live with 2.1.325). A later line of a known id that reports a
   larger ``output_tokens`` adds only the difference, so a future
   placeholder count can never be summed twice. That one rule, plus a read
   offset per file, is what keeps /compact and resume (same file, the
   offset continues), /clear (a new file), a forked job or any copy (the
   ids are already seen) and a daemon restart (offsets, counts and seen
   ids are one atomically written document) from ever counting twice.

Days are the daemon's local days (:data:`localtime`, ``time.localtime``,
the rule ``flood.clear_time`` uses for the clock it shows), from each
entry's own ``timestamp``: transcripts are written lazily, so the read
time would misfile a turn that ended before midnight.

The file (``config.USAGE_STATE_FILE``, read LATE) is untrusted when read:
everything is validated back and anything else dropped; a file that is
not ours, too big or not JSON reads as empty. Writes are atomic and
owner-only (``private_file.write_private``), behind
``_test_guard.check_write``, debounced to ``MIN_SAVE_INTERVAL`` on the
2 s tick, and forced at a clean stop.

Imports neither ``aipager.i18n`` nor ``aipager.bot``: text is rendered in
the bot and Mini App layers. Nothing here raises into a caller.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aipager._test_guard import check_write
from aipager.claude_bootstrap import LOGIN_KINDS, SessionLogin
from aipager.private_file import write_private
from aipager.statusline_file import RATE_WINDOWS, Window

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
#: Real transcript reading happens at most this often on the 2 s tick
#: (``force`` skips the wait: /usage and the Mini App ask for it).
SCAN_INTERVAL = 10.0
#: At most this many bytes are read in one scan (a first run's backlog
#: of large transcripts is read over several scans, never at once) ...
SCAN_BUDGET_BYTES = 2 * 1024 * 1024
#: ... and at most this many of one file, so no file starves the others.
CHUNK_BYTES = 512 * 1024
#: A line longer than a chunk is read on up to this size; past it the line
#: is given up and reading resumes on the next line.
MAX_LINE_BYTES = 4 * 1024 * 1024
#: Days kept (today and the 7 before): a reading or a transcript older
#: than this cannot change anything shown.
KEEP_DAYS = 8
#: Days kept at most, whatever the clock did (today, the 7 before and
#: tomorrow, which a clock a little behind a transcript's can see).
MAX_DAYS = KEEP_DAYS + 1
MAX_READINGS = 8
#: Model rows per scope and day, the ``other`` row included.
MAX_MODELS = 16
#: Chat scopes counted per day; a scope past it is not counted that day.
MAX_SCOPES_PER_DAY = 32
MAX_IDS_PER_DAY = 20_000
MAX_CURSORS = 2_000
#: A transcript path longer than this is not followed (real ones are a
#: few hundred characters at most).
MAX_PATH_CHARS = 1024
MIN_SAVE_INTERVAL = 30.0
#: A file above this reads as empty. The caps above keep the largest
#: document they allow well under it (about 22 MB with every text field
#: at its cap in characters JSON escapes to six bytes; about 9 MB with
#: plain ones), so a file aipager wrote always loads again
#: (tests/test_usage_store_bounds.py).
MAX_FILE_BYTES = 32_000_000
STATE_DIR_MODE = 0o700
#: How long a session's sign-in is taken as known (it reads settings
#: files; a status-line datagram arrives after every answer).
LOGIN_CACHE_SECONDS = 300.0
#: A plan reading whose windows did not change is still rewritten when it
#: is this much newer, so "as of" survives a restart roughly right.
PLAN_RESAVE_SECONDS = 60.0
#: Model ids are stored at most this long; past ``MAX_MODELS - 1`` models
#: in one scope and day, the rest are counted under this name.
MODEL_MAX_CHARS = 64
OTHER_MODEL = "other"
UNKNOWN_MODEL = "unknown"
SYNTHETIC_MODEL = "<synthetic>"
#: A plan window this full is "near the limit" (the busy card's note and
#: the Mini App's amber bar), and this full "reached" (roadmap 8.131). One
#: rule for both, judged on the rounded percentage /usage displays
#: (:func:`limit_level`).
NEAR_LIMIT_PERCENT = 90
LIMIT_REACHED_PERCENT = 100
#: A count above this is not a token count.
COUNT_MAX = 10**9
#: A stored sum above this is not ours.
SUM_MAX = 10**15
TS_MIN, TS_MAX = 1_000_000_000, 4_000_000_000

#: The decided local day of a unix time: replaced by tests (never patch
#: ``time`` itself).
localtime = time.localtime

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SCOPE_RE = re.compile(r"^-?\d{1,20}$")
_USAGE_MARK = b'"usage"'
_ASSISTANT_MARK = b'"assistant"'
_COUNT_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                 "cache_creation_input_tokens")

_lock = threading.RLock()
_plan: dict[str, dict] = {}
_plan_marked: dict[str, float] = {}   # key -> the seen_at last marked for a write
_days: dict[str, dict[int, dict[str, list[int]]]] = {}
_seen: dict[str, dict[str, int]] = {}
_seen_day: dict[str, str] = {}      # id -> the day it was first counted on
_cursors: dict[str, dict] = {}
_login_cache: dict[tuple[str, str], tuple[float, SessionLogin]] = {}
_behind = False
_loaded = False
_dirty = False
_last_write = 0.0
_last_scan = 0.0


# ---- data shapes ----------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """The newest plan reading of one sign-in."""

    key: str
    kind: str
    host: str
    session: str
    seen_at: float
    windows: dict[str, Window]
    #: The chat scope of the session it came from: the session's name (and
    #: a gateway's host) is shown only to who may see that chat.
    scope: int = 0


Counts = tuple[int, int, int, int]   # input, output, cache read, cache write
_ZERO: Counts = (0, 0, 0, 0)


@dataclass(frozen=True)
class Snapshot:
    """What /usage and the Mini App show, for the scopes asked for."""

    plan: tuple[Reading, ...]                     # newest first
    today: Counts
    week: Counts                                  # today and the 6 days before
    days: tuple[tuple[str, Counts], ...]          # those 7 days, newest first
    models: tuple[tuple[str, Counts], ...]        # over the 7 days, largest first
    behind: bool                                  # transcripts still being read


# ---- text ------------------------------------------------------------------------

def clean_text(value: str) -> str:
    """*value* with every code point UTF-8 cannot encode (a lone
    surrogate: a broken escape in a transcript, a non-UTF-8 file name)
    replaced by ``?``, so it can be saved, shown and sent."""
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return value.encode("utf-8", "replace").decode("utf-8")


def _plan_key(login: SessionLogin) -> str:
    """:func:`login_key`, encodable and at most 1024 characters (what
    loads again); a longer one keeps a hash of the whole so two sign-ins
    never share a key."""
    key = clean_text(login_key(login))
    if len(key) > 1024:
        digest = hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()
        key = key[:1024 - 33] + "#" + digest
    return key


def plan_key(login: SessionLogin) -> str:
    """The key :func:`note_plan` stores a reading of *login* under (what
    :func:`plan_windows` looks up). ``unknown`` for an unknown sign-in."""
    return _plan_key(login)


def limit_level(pct: Any) -> int:
    """0 below the near-limit step, 1 near it, 2 at or past the limit, for
    a window *pct* percent used (roadmap 8.131). Judged on the rounded
    value /usage displays, so a window shown as "90%" always counts as
    near; a spend limit over 100 counts as reached. Not a finite real
    number (a bool, a string, NaN, infinity): 0."""
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
        return 0
    shown = round(pct)
    if shown >= LIMIT_REACHED_PERCENT:
        return 2
    if shown >= NEAR_LIMIT_PERCENT:
        return 1
    return 0


# ---- time ------------------------------------------------------------------------

def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _date_of(ts: float) -> _dt.date:
    t = localtime(ts)
    return _dt.date(t.tm_year, t.tm_mon, t.tm_mday)


def _day_key(d: _dt.date) -> str:
    return d.isoformat()


def _entry_time(value: Any) -> float | None:
    """Unix seconds of a transcript ``timestamp`` (ISO 8601), or None."""
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


# ---- sign-ins --------------------------------------------------------------------

def login_key(login: SessionLogin) -> str:
    """The store key of *login*: ``unknown`` alone, else
    ``kind|source|config home``."""
    if login.kind not in LOGIN_KINDS or login.kind == "unknown":
        return "unknown"
    return f"{login.kind}|{login.source}|{login.config_home}"


def login_for(sess: Any) -> SessionLogin:
    """The sign-in *sess* runs under (``claude_bootstrap.session_login``
    over the environment every session aipager starts gets), cached per
    session name and folder for ``LOGIN_CACHE_SECONDS``. ``unknown`` for a
    session with no folder (one aipager did not start) or on any error."""
    try:
        name = str(getattr(sess, "name", "") or "")
        cwd = str(getattr(sess, "cwd", "") or "")
        if not cwd:
            return SessionLogin("unknown")
        mono = time.monotonic()
        with _lock:
            hit = _login_cache.get((name, cwd))
            if hit is not None and mono - hit[0] < LOGIN_CACHE_SECONDS:
                return hit[1]
        from aipager import claude_bootstrap, daemon_secrets
        login = claude_bootstrap.session_login(daemon_secrets.build_session_env(), folder=cwd)
        with _lock:
            if len(_login_cache) > 256:
                _login_cache.clear()
            _login_cache[(name, cwd)] = (mono, login)
        return login
    except Exception:  # noqa: BLE001 - an unknown sign-in is still shown
        log.debug("usage: sign-in of a session could not be worked out", exc_info=True)
        return SessionLogin("unknown")


# ---- plan readings ---------------------------------------------------------------

def _windows_doc(windows: dict[str, Window]) -> dict[str, dict]:
    return {name: {"pct": float(w.pct), "resets_at": int(w.resets_at)}
            for name, w in windows.items() if name in RATE_WINDOWS}


def _scope_value(scope: Any) -> int:
    return scope if type(scope) is int and abs(scope) < 10**20 else 0


def note_plan(session_label: str, windows: dict[str, Window], login: SessionLogin, *,
              scope: Any = 0, now: float | None = None) -> bool:
    """Keep *windows* as the newest reading of *login*'s sign-in, seen now
    in session *session_label* of chat scope *scope* (0: none known, which
    only the owner and a personal-mode operator see by name). It wholly
    replaces that sign-in's previous reading (a window Claude Code stopped
    reporting is no longer shown); other sign-ins' readings are untouched.
    Returns whether the stored document changed. Never raises."""
    global _dirty
    try:
        if not windows:
            return False
        ts = _now(now)
        doc = _windows_doc(windows)
        if not doc:
            return False
        key = _plan_key(login)
        kind = login.kind if key != "unknown" else "unknown"
        host = clean_text(str(login.source))[:255] if kind == "gateway" else ""
        with _lock:
            _ensure_loaded()
            old = _plan.pop(key, None)
            entry = {"key": key, "kind": kind, "host": host,
                     "session": clean_text(str(session_label)[:128]), "scope": _scope_value(scope),
                     "seen_at": ts, "windows": doc}
            _plan[key] = entry   # most recently seen last
            changed = (old is None or old["windows"] != doc
                       or old["session"] != entry["session"]
                       or old.get("scope") != entry["scope"]
                       or ts - _plan_marked.get(key, 0.0) >= PLAN_RESAVE_SECONDS)
            while len(_plan) > MAX_READINGS:
                _drop_reading(min(_plan, key=lambda k: _plan[k]["seen_at"]))
            if changed:
                _plan_marked[key] = ts
                _dirty = True
            return changed
    except Exception:  # noqa: BLE001 - a reading must never break the handler
        log.debug("usage: plan reading not kept", exc_info=True)
        return False


def _drop_reading(key: str) -> None:
    _plan.pop(key, None)
    _plan_marked.pop(key, None)


def plan_windows(key: str, *, now: float | None = None) -> dict[str, Window] | None:
    """The windows of the newest reading stored under *key*
    (:func:`plan_key`), as a new dict, or ``None``: for an empty or
    ``unknown`` key (an unknown reading is shared by every session whose
    sign-in is unknown, so it is nobody's in particular), a key with no
    reading, or one older than ``KEEP_DAYS`` (the :func:`snapshot` rule).
    Memory only, never a file read past the first load: the busy card
    asks on every edit (roadmap 8.131). Never raises."""
    try:
        if not isinstance(key, str) or not key or key == "unknown":
            return None
        with _lock:
            _ensure_loaded()
            entry = _plan.get(key)
            if entry is None or entry["seen_at"] < _now(now) - KEEP_DAYS * 86400:
                return None
            return {n: Window(pct=w["pct"], resets_at=w["resets_at"])
                    for n, w in entry["windows"].items()}
    except Exception:  # noqa: BLE001 - no note rather than an error
        log.debug("usage: plan windows not read", exc_info=True)
        return None


# ---- the token tally -------------------------------------------------------------

def _count_value(raw: Any) -> int:
    if type(raw) is not int or raw < 0 or raw >= COUNT_MAX:
        return 0
    return raw


def _dedupe_key(message: dict, timestamp: Any, counts: Counts) -> str:
    """A short hash of the message id; for a line with none, of its
    timestamp and counts (a copy keeps both, so copies still dedupe)."""
    mid = message.get("id")
    if isinstance(mid, str) and mid:
        material = mid
    else:
        material = "t|" + str(timestamp) + "|" + "|".join(str(n) for n in counts)
    return hashlib.blake2b(material.encode("utf-8", "replace"), digest_size=8).hexdigest()


def _model_slot(day: str, scope: int, model: str) -> list[int]:
    """The counts of *model* in *scope* on *day*. Past ``MAX_MODELS - 1``
    models a scope's day counts the rest under ``other``; past
    ``MAX_SCOPES_PER_DAY`` scopes a day's new scope is not counted (a
    throwaway slot), so the file stays within what loads again."""
    scopes = _days.setdefault(day, {})
    bucket = scopes.get(scope)
    if bucket is None:
        if len(scopes) >= MAX_SCOPES_PER_DAY:
            return [0, 0, 0, 0]
        bucket = scopes[scope] = {}
    slot = bucket.get(model)
    if slot is None:
        if len(bucket) >= MAX_MODELS - 1 and model != OTHER_MODEL:
            return _model_slot(day, scope, OTHER_MODEL)
        slot = bucket[model] = [0, 0, 0, 0]
    return slot


def _count_line(line: bytes, scope: int, today: _dt.date) -> bool:
    """Count one transcript line, if it is an assistant message with a
    usage record not counted before. Returns whether anything was added."""
    if _USAGE_MARK not in line or _ASSISTANT_MARK not in line:
        return False
    try:
        entry = json.loads(line)
    except (ValueError, RecursionError):
        return False
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return False
    model = message.get("model")
    if model == SYNTHETIC_MODEL:
        return False
    model = clean_text(model[:MODEL_MAX_CHARS]) if isinstance(model, str) and model.strip() \
        else UNKNOWN_MODEL
    when = _entry_time(entry.get("timestamp"))
    if when is None:
        return False
    day_date = _date_of(when)
    if day_date < today - _dt.timedelta(days=KEEP_DAYS - 1) \
            or day_date > today + _dt.timedelta(days=1):
        return False
    counts: Counts = tuple(_count_value(usage.get(f)) for f in _COUNT_FIELDS)  # type: ignore[assignment]
    key = _dedupe_key(message, entry.get("timestamp"), counts)
    first_day = _seen_day.get(key)
    if first_day is not None:
        # The same message again (another of its lines, a copy, a
        # re-read): only a larger output count adds, and only its
        # difference, on the day it was first counted.
        seen = _seen.get(first_day, {})
        before = seen.get(key, 0)
        if counts[1] <= before:
            return False
        _model_slot(first_day, scope, model)[1] += counts[1] - before
        seen[key] = counts[1]
        return True
    day = _day_key(day_date)
    slot = _model_slot(day, scope, model)
    for i, n in enumerate(counts):
        slot[i] += n
    ids = _seen.setdefault(day, {})
    if len(ids) < MAX_IDS_PER_DAY:
        ids[key] = counts[1]
        _seen_day[key] = day
    return True


def _new_cursor(st: os.stat_result, scope: int, session: str) -> dict:
    return {"ino": int(st.st_ino), "dev": int(st.st_dev), "offset": 0, "skip": False,
            "scope": int(scope), "session": clean_text(str(session)[:128]),
            "grown_at": float(st.st_mtime), "read_at": 0.0}


def _scope_of(sess: Any) -> int:
    scope = getattr(sess, "scope_chat_id", 0)
    return scope if type(scope) is int else 0


def _discover(registry: Any, now: float) -> None:
    """A cursor for every registry session's transcript (and its subagent
    transcripts) written in the last ``KEEP_DAYS`` days."""
    oldest = now - KEEP_DAYS * 86400
    try:
        sessions = list(registry.all_sessions().values())
    except Exception:  # noqa: BLE001 - no registry, no new files
        return
    for sess in sessions:
        path = getattr(sess, "transcript_path", "") or ""
        if not isinstance(path, str) or not path.endswith(".jsonl") or not os.path.isabs(path):
            continue
        scope, label = _scope_of(sess), str(getattr(sess, "label", "") or "")
        candidates = [path]
        subdir = os.path.join(path[:-len(".jsonl")], "subagents")
        try:
            candidates += sorted(os.path.join(subdir, f) for f in os.listdir(subdir)
                                 if f.startswith("agent-") and f.endswith(".jsonl"))
        except (OSError, ValueError):   # ValueError: a path the file system cannot name
            pass
        for candidate in candidates:
            if candidate in _cursors or len(candidate) > MAX_PATH_CHARS:
                continue
            try:
                st = os.stat(candidate)
            except (OSError, ValueError):
                continue
            if st.st_mtime < oldest:
                continue
            _cursors[candidate] = _new_cursor(st, scope, label)


def _read_cursor(path: str, cur: dict, st: os.stat_result, budget: int,
                 today: _dt.date) -> tuple[int, bool]:
    """Read *path* on from its cursor and count what it holds. Returns
    ``(bytes read from disk, whether anything was counted)``. A trailing
    partial line is never consumed."""
    with open(path, "rb") as fh:
        fh.seek(cur["offset"])
        want = max(0, min(CHUNK_BYTES, budget, st.st_size - cur["offset"]))
        raw = fh.read(want)
        read = len(raw)
        cut_by_budget = budget < CHUNK_BYTES and want == budget
        if cur["skip"]:
            i = raw.find(b"\n")
            if i < 0:
                cur["offset"] += len(raw)
                return read, False
            cur["offset"] += i + 1
            raw = raw[i + 1:]
            cur["skip"] = False
        if b"\n" not in raw and cur["offset"] + len(raw) < st.st_size:
            if cut_by_budget:
                return read, False   # the rest of the budget ended mid-line
            # One line longer than a chunk: read on, in this one call.
            fh.seek(cur["offset"])
            raw = fh.read(MAX_LINE_BYTES)
            read += len(raw)
            if b"\n" not in raw:
                if len(raw) >= MAX_LINE_BYTES:
                    # Given up: drop it and resume after its newline.
                    cur["offset"] += len(raw)
                    cur["skip"] = True
                return read, False
    counted = False
    scope = cur["scope"]
    for line in raw.split(b"\n")[:-1]:   # the last piece is never a whole line
        cur["offset"] += len(line) + 1
        if _count_line(line, scope, today):
            counted = True
    return read, counted


def scan(registry: Any, *, now: float | None = None, force: bool = False) -> None:
    """Read what the transcripts of *registry*'s sessions gained since the
    last scan and count it. Does real work at most every ``SCAN_INTERVAL``
    seconds of *now* (the wall clock when not given; a clock set back
    scans at once) unless *force*; reads at most ``SCAN_BUDGET_BYTES``
    (plus one long line), least recently read files first. Never raises."""
    global _last_scan, _behind, _dirty
    try:
        with _lock:
            ts = _now(now)
            if not force and _last_scan and 0 <= ts - _last_scan < SCAN_INTERVAL:
                return
            _last_scan = ts
            _ensure_loaded()
            today = _date_of(ts)
            _discover(registry, ts)
            budget = SCAN_BUDGET_BYTES
            behind = False
            for path in sorted(_cursors, key=lambda p: _cursors[p]["read_at"]):
                cur = _cursors[path]
                try:
                    st = os.stat(path)
                except OSError:
                    del _cursors[path]
                    _dirty = True
                    continue
                if (int(st.st_ino), int(st.st_dev)) != (cur["ino"], cur["dev"]) \
                        or st.st_size < cur["offset"]:
                    # Replaced or truncated: read it again from the start;
                    # the seen ids keep the re-read from counting twice.
                    cur.update(ino=int(st.st_ino), dev=int(st.st_dev), offset=0, skip=False)
                    _dirty = True
                if st.st_size == cur["offset"]:
                    continue
                if budget <= 0:
                    behind = True
                    continue
                before = cur["offset"]
                try:
                    read, _counted = _read_cursor(path, cur, st, budget, today)
                except OSError:
                    continue
                budget -= read
                cur["read_at"] = ts
                if cur["offset"] != before:
                    cur["grown_at"] = max(cur["grown_at"], float(st.st_mtime))
                    _dirty = True
                if cur["offset"] < st.st_size:
                    behind = True
            _behind = behind
            _prune(ts)
    except Exception:  # noqa: BLE001 - the tally must never stop the tick
        log.debug("usage: transcript scan failed", exc_info=True)


def _prune(ts: float) -> None:
    """Drop what can no longer change anything shown."""
    global _dirty
    today = _date_of(ts)
    oldest_day = _day_key(today - _dt.timedelta(days=KEEP_DAYS - 1))
    # Past days go, and so do the furthest from today of more than
    # MAX_DAYS (a clock set back leaves later days behind it).
    for day in [d for d in _days if d < oldest_day] + _furthest_days(_days, today):
        if _days.pop(day, None) is not None:
            _dirty = True
    for day in [d for d in _seen if d < oldest_day] + _furthest_days(_seen, today):
        ids = _seen.pop(day, None)
        if ids is None:
            continue
        for key in ids:
            if _seen_day.get(key) == day:
                del _seen_day[key]
        _dirty = True
    oldest = ts - KEEP_DAYS * 86400
    for key in [k for k, r in _plan.items() if r["seen_at"] < oldest]:
        _drop_reading(key)
        _dirty = True
    for path in [p for p, c in _cursors.items() if c["grown_at"] < oldest]:
        del _cursors[path]
        _dirty = True
    if len(_cursors) > MAX_CURSORS:
        for path in sorted(_cursors, key=lambda p: _cursors[p]["grown_at"])[
                :len(_cursors) - MAX_CURSORS]:
            del _cursors[path]
        _dirty = True


def _furthest_days(days: dict[str, Any], today: _dt.date) -> list[str]:
    """The day keys of *days* past the ``MAX_DAYS`` nearest *today*: the
    ones a scan can still count into (the kept days up to tomorrow) are
    nearest, then the later ones, nearest first. So a clock set back
    keeps the current week and drops the stale days ahead of it."""
    def distance(day: str) -> tuple[bool, int]:
        try:
            delta = (_dt.date.fromisoformat(day) - today).days
        except (TypeError, ValueError):
            return (True, 10**9)
        return (delta > 1, abs(delta))
    return sorted(days, key=lambda d: (distance(d), d))[MAX_DAYS:]


# ---- reading it back -------------------------------------------------------------

def _add(a: Counts, b: list[int] | Counts) -> Counts:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


def snapshot(scopes: frozenset[int] | None, *, now: float | None = None) -> Snapshot:
    """The readings (newest first) and the token sums of the scopes in
    *scopes* (``None``: every scope) for today, the last 7 days, each of
    those days and each model. Never raises."""
    try:
        with _lock:
            _ensure_loaded()
            ts = _now(now)
            today = _date_of(ts)
            plan = tuple(
                Reading(key=r["key"], kind=r["kind"], host=r["host"], session=r["session"],
                        seen_at=r["seen_at"], scope=r.get("scope", 0),
                        windows={n: Window(pct=w["pct"], resets_at=w["resets_at"])
                                 for n, w in r["windows"].items()})
                for r in sorted(_plan.values(), key=lambda r: -r["seen_at"])
                if r["seen_at"] >= ts - KEEP_DAYS * 86400)
            days: list[tuple[str, Counts]] = []
            models: dict[str, Counts] = {}
            for back in range(7):
                day = _day_key(today - _dt.timedelta(days=back))
                total = _ZERO
                for scope, bucket in _days.get(day, {}).items():
                    if scopes is not None and scope not in scopes:
                        continue
                    for model, counts in bucket.items():
                        total = _add(total, counts)
                        models[model] = _add(models.get(model, _ZERO), counts)
                days.append((day, total))
            week = _ZERO
            for _day, counts in days:
                week = _add(week, counts)
            ranked = sorted(models.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
            return Snapshot(plan=plan, today=days[0][1], week=week, days=tuple(days),
                            models=tuple(ranked), behind=_behind)
    except Exception:  # noqa: BLE001 - an empty view rather than an error
        log.debug("usage: snapshot failed", exc_info=True)
        return Snapshot(plan=(), today=_ZERO, week=_ZERO, days=(), models=(), behind=False)


# ---- the file --------------------------------------------------------------------

def _path(path: Path | str | None) -> Path:
    """Read LATE from config (the test redirect is one entry there)."""
    if path is not None:
        return Path(path)
    from aipager import config
    return Path(config.USAGE_STATE_FILE)


def _reject_constant(name: str):
    raise ValueError(name)


def _is_ts(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and TS_MIN <= value <= TS_MAX)


def _clean_reading(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key, kind, host, session = (raw.get(k) for k in ("key", "kind", "host", "session"))
    if not (isinstance(key, str) and 0 < len(key) <= 1024 and kind in LOGIN_KINDS
            and isinstance(host, str) and len(host) <= 255
            and isinstance(session, str) and len(session) <= 128
            and _is_ts(raw.get("seen_at"))):
        return None
    if (key == "unknown") != (kind == "unknown") or (kind != "unknown"
                                                     and not key.startswith(kind + "|")):
        return None
    scope = raw.get("scope", 0)
    if type(scope) is not int or abs(scope) >= 10**20:
        return None
    windows = raw.get("windows")
    if not isinstance(windows, dict):
        return None
    clean: dict[str, dict] = {}
    for name, w in windows.items():
        if name not in RATE_WINDOWS or not isinstance(w, dict) or set(w) != {"pct", "resets_at"}:
            continue
        pct, resets = w["pct"], w["resets_at"]
        if (isinstance(pct, (int, float)) and not isinstance(pct, bool)
                and math.isfinite(pct) and 0 <= pct <= 1000
                and type(resets) is int and TS_MIN <= resets <= TS_MAX):
            clean[name] = {"pct": float(pct), "resets_at": resets}
    if not clean:
        return None
    return {"key": clean_text(key), "kind": kind,
            "host": clean_text(host) if kind == "gateway" else "",
            "session": clean_text(session), "scope": scope, "seen_at": float(raw["seen_at"]),
            "windows": clean}


def _clean_counts(raw: Any) -> list[int] | None:
    if (isinstance(raw, list) and len(raw) == 4
            and all(type(n) is int and 0 <= n <= SUM_MAX for n in raw)):
        return list(raw)
    return None


def _clean_cursor(path: Any, raw: Any) -> dict | None:
    if not (isinstance(path, str) and os.path.isabs(path) and len(path) <= MAX_PATH_CHARS
            and path.endswith(".jsonl") and isinstance(raw, dict)):
        return None
    ints = ("ino", "dev", "offset", "scope")
    if not all(type(raw.get(k)) is int for k in ints) or raw["offset"] < 0 \
            or raw["ino"] < 0 or raw["dev"] < 0:
        return None
    if type(raw.get("skip")) is not bool or not isinstance(raw.get("session"), str):
        return None
    for k in ("grown_at", "read_at"):
        v = raw.get(k)
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                and 0 <= v <= TS_MAX):
            return None
    return {"ino": raw["ino"], "dev": raw["dev"], "offset": raw["offset"],
            "skip": raw["skip"], "scope": raw["scope"],
            "session": clean_text(raw["session"][:128]),
            "grown_at": float(raw["grown_at"]), "read_at": float(raw["read_at"])}


def read(*, path: Path | str | None = None) -> dict:
    """The file's document, validated: ``{"plan": [...], "days": {...},
    "seen": {...}, "cursors": {...}}`` with every bad entry dropped, or
    ``{}`` when the file is missing, too big, not JSON, holds a non-finite
    number or is another version. Never raises."""
    try:
        target = _path(path)
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return {}
        document = json.loads(target.read_text(encoding="utf-8"),
                              parse_constant=_reject_constant)
    except Exception:  # noqa: BLE001 - unreadable reads as empty
        log.debug("usage: state file could not be read", exc_info=True)
        return {}
    if not isinstance(document, dict) or document.get("version") != SCHEMA_VERSION:
        return {}
    try:
        return _clean_document(document)
    except Exception:  # noqa: BLE001 - a document we cannot walk reads as empty
        log.debug("usage: state file not understood", exc_info=True)
        return {}


def _clean_document(document: dict) -> dict:
    plan: list[dict] = []
    raw_plan = document.get("plan")
    for raw in raw_plan[:MAX_READINGS * 4] if isinstance(raw_plan, list) else []:
        reading = _clean_reading(raw)
        if reading is not None and all(r["key"] != reading["key"] for r in plan):
            plan.append(reading)
    plan = sorted(plan, key=lambda r: r["seen_at"])[-MAX_READINGS:]
    days: dict[str, dict[int, dict[str, list[int]]]] = {}
    raw_days = document.get("days")
    for day, scopes in (raw_days.items() if isinstance(raw_days, dict) else ()):
        if not (isinstance(day, str) and _DAY_RE.match(day) and isinstance(scopes, dict)):
            continue
        for scope, models in scopes.items():
            if not (isinstance(scope, str) and _SCOPE_RE.match(scope)
                    and isinstance(models, dict)):
                continue
            kept: dict[str, list[int]] = {}
            named = 0
            for model, counts in models.items():
                clean = _clean_counts(counts)
                if not (clean is not None and isinstance(model, str)
                        and 0 < len(model) <= MODEL_MAX_CHARS):
                    continue
                model = clean_text(model)
                if model in kept:
                    continue
                if model != OTHER_MODEL:
                    # The rows a scan makes: MAX_MODELS - 1 models and other.
                    if named >= MAX_MODELS - 1:
                        continue
                    named += 1
                kept[model] = clean
            if kept:
                days.setdefault(day, {})[int(scope)] = kept
    seen: dict[str, dict[str, int]] = {}
    raw_seen = document.get("seen")
    for day, ids in (raw_seen.items() if isinstance(raw_seen, dict) else ()):
        if not (isinstance(day, str) and _DAY_RE.match(day) and isinstance(ids, dict)):
            continue
        kept_ids = {k: v for k, v in list(ids.items())[:MAX_IDS_PER_DAY]
                    if isinstance(k, str) and _ID_RE.match(k)
                    and type(v) is int and 0 <= v < COUNT_MAX}
        if kept_ids:
            seen[day] = kept_ids
    cursors: dict[str, dict] = {}
    raw_cursors = document.get("cursors")
    for p, raw in (raw_cursors.items() if isinstance(raw_cursors, dict) else ()):
        if len(cursors) >= MAX_CURSORS:
            break
        cursor = _clean_cursor(p, raw)
        if cursor is not None:
            cursors[p] = cursor
    return {"plan": plan, "days": days, "seen": seen, "cursors": cursors}


def _clear_memory() -> None:
    global _behind
    _plan.clear()
    _plan_marked.clear()
    _days.clear()
    _seen.clear()
    _seen_day.clear()
    _cursors.clear()
    _behind = False


def load(*, path: Path | str | None = None) -> bool:
    """Replace what is in memory with the file's document (a bad file
    reads as empty). Returns whether a document was read. Never raises."""
    global _loaded, _dirty
    try:
        with _lock:
            document = read(path=path)
            _clear_memory()
            _loaded = True
            _dirty = False
            if not document:
                return False
            for reading in document["plan"]:
                _plan[reading["key"]] = reading
            _days.update(document["days"])
            for day, ids in document["seen"].items():
                _seen[day] = ids
                for key in ids:
                    prior = _seen_day.get(key)
                    if prior is None or day < prior:
                        _seen_day[key] = day
            _cursors.update(document["cursors"])
            return True
    except Exception:  # noqa: BLE001 - the daemon starts with an empty store
        log.debug("usage: state not loaded", exc_info=True)
        return False


def _ensure_loaded() -> None:
    if not _loaded:
        load()


def _document() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "written_at": time.time(),
        "plan": sorted(_plan.values(), key=lambda r: r["seen_at"]),
        "days": {day: {str(scope): {m: list(c) for m, c in models.items()}
                       for scope, models in scopes.items()}
                 for day, scopes in sorted(_days.items())},
        "seen": {day: dict(ids) for day, ids in sorted(_seen.items())},
        "cursors": {p: dict(c) for p, c in _cursors.items()},
    }


def _serialize(document: dict) -> str:
    """*document* as JSON text UTF-8 can always encode. Not ASCII-escaped:
    a character takes at most six bytes, the bound MAX_FILE_BYTES is
    worked out on. A lone surrogate still left (a transcript path from a
    non-UTF-8 file name, which must stay exact to be opened again) is
    written as its JSON escape ``\\udcXX`` (six bytes), which loads back
    as the same string; one bad value never stops the whole save."""
    text = json.dumps(document, allow_nan=False, ensure_ascii=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # Outside a string json.dumps writes only ASCII, so every
        # surrogate is inside one, where its backslash escape is JSON.
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text


def save_if_dirty(*, path: Path | str | None = None, force: bool = False) -> bool:
    """Write the file when something changed, at most every
    ``MIN_SAVE_INTERVAL`` seconds unless *force*. Returns whether it
    wrote. Never raises."""
    global _dirty, _last_write
    try:
        with _lock:
            if not _dirty:
                return False
            mono = time.monotonic()
            if not force and _last_write and mono - _last_write < MIN_SAVE_INTERVAL:
                return False
            target = _path(path)
            check_write(target)
            write_private(target, _serialize(_document()), dir_mode=STATE_DIR_MODE)
            _dirty = False
            _last_write = mono
            return True
    except Exception:  # noqa: BLE001 - a full disk must not stop the daemon
        log.debug("usage: state file could not be written", exc_info=True)
        return False


def reset_for_tests() -> None:
    """Forget everything in memory; the file is read again on next use."""
    global _loaded, _dirty, _last_write, _last_scan
    with _lock:
        _clear_memory()
        _login_cache.clear()
        _loaded = _dirty = False
        _last_write = _last_scan = 0.0
