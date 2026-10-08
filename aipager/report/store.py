"""The local problem-report store (roadmap 8.112, design section 4).

``~/.local/state/aipager/reports.json`` (``config.REPORTS_FILE``) holds
what a report may carry and nothing more:

- error records keyed by fingerprint: an ``errors[]`` entry exactly as
  the report schema accepts it, plus the typed timestamps the offer
  policy needs (first and last seen, and how many separate occasions, at
  least ``OCCASION_GAP`` seconds apart);
- hourly counters over the fixed :data:`schema.COUNTER_KEYS`;
- an hourly log digest: call site and level counts, never a line of text;
- the last exit (clean, crash, reboot) and the unclean exits of the week.

Bounded: ``MAX_FINGERPRINTS`` records (the least recently seen goes
first), counters for ``KEEP_COUNTER_HOURS``, the digest for
``KEEP_DIGEST_HOURS`` with ``DIGEST_SITES_PER_HOUR`` sites an hour.

The file is UNTRUSTED when read: every record goes back through the
schema and anything else is dropped; a file that is not ours, too big or
not JSON reads as empty and is replaced on the next write. Writes are
atomic and owner-only from the first byte (``private_file.write_private``), behind
``_test_guard.check_write``, and debounced: mutations set a dirty flag,
the session monitor's 2 s tick writes at most every ``MIN_SAVE_INTERVAL``
seconds, and a clean stop or a crash forces the write.

Nothing here raises into a caller and nothing here logs above DEBUG: the
callers are a logging handler and the daemon's start and stop path.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from aipager._test_guard import check_write
from aipager.private_file import write_private
from aipager.report import builder
from aipager.report import fingerprint as fp
from aipager.report import policy as report_policy
from aipager.report import schema as sc

log = logging.getLogger(__name__)

#: Bumping this makes every existing file "not read whole", which turns
#: automatic offers OFF (see load): a new version must migrate ``policy``.
SCHEMA_VERSION = 1
MAX_FINGERPRINTS = 50
KEEP_COUNTER_HOURS = 8 * 24
KEEP_DIGEST_HOURS = 48
DIGEST_SITES_PER_HOUR = 50
UNCLEAN_EXITS_KEPT = 50
UNCLEAN_EXITS_WINDOW = 7 * 86400
OCCASION_GAP = 600
MIN_SAVE_INTERVAL = 30.0
MAX_FILE_BYTES = 1_000_000
STATE_DIR_MODE = 0o700
COUNT_MAX = 10**6
EXIT_KINDS = ("clean", "crash", "reboot")
#: Unix seconds a stored timestamp may hold (2001 to 2096).
TS_MIN, TS_MAX = 1_000_000_000, 4_000_000_000
HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")
#: What a call-site fingerprint may be keyed by.
SITE_KINDS = frozenset({"log_error", "crash", "hook_cap"})
#: At most this many NEW fingerprints an hour from other processes (any
#: local process can write to the daemon's socket): a flood of made-up
#: ones must not push real errors out of the store.
RELAY_NEW_PER_HOUR = 10

_ERROR_ITEM = sc.SCHEMA["errors"][1]
_DIGEST_ROW = sc.SCHEMA["log_digest_24h"][1]
_ENTRY_KEYS = frozenset(_ERROR_ITEM)
_RECORD_KEYS = frozenset({"entry", "first_ts", "last_ts", "occasions", "occasion_ts"})

_lock = threading.RLock()
_errors: OrderedDict[str, dict] = OrderedDict()
_counters: dict[str, dict[str, int]] = {}
_digest: dict[str, dict[tuple[str, str], int]] = {}
_unclean: list[int] = []
_last_exit: str = sc.UNKNOWN
_relayed_new: list[int] = []  # when each new relayed fingerprint arrived
_policy = report_policy.State()
_loaded = False
_dirty = False
_last_write = 0.0


# ---- time --------------------------------------------------------------------

def _now(now) -> int:
    return int(time.time() if now is None else now)


def _hour(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ts))


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _is_ts(value) -> bool:
    return type(value) is int and TS_MIN <= value <= TS_MAX


def _version() -> str | None:
    try:
        from aipager import __version__
    except Exception:  # noqa: BLE001 - a broken install records no version
        return None
    return builder._version_or_none(__version__)


# ---- classification ----------------------------------------------------------

def classify(exc: BaseException, trigger: str | None = None) -> tuple[str, str | None]:
    """``("env", counter)`` for an environment error, ``("bug", None)``
    for an error raised through aipager's own code, ``("anomaly", None)``
    for one with no aipager frame at all.

    Whatever its type, an error that ended the daemon (``crash``) is a bug,
    and one that escaped a task (``task``) is judged by its frames only:
    nothing caught it, so even a network error there is a missing guard.
    """
    if trigger == "crash":
        return "bug", None
    key = fp.env_counter(exc) if trigger != "task" else None
    if key is not None:
        return "env", key
    return ("bug", None) if fp.aipager_frames(exc, limit=1) else ("anomaly", None)


# ---- recording ---------------------------------------------------------------

def record_exception(exc: BaseException, *, where: str, trigger: str,
                     logger: str | None = None, tier: str | None = None,
                     event: str | None = None, tool: str | None = None,
                     now=None) -> str | None:
    """Record *exc* by its fingerprint (an environment error only bumps
    its counter). Returns the fingerprint recorded, else None. *tier*
    overrides the classification. Never raises."""
    try:
        with _lock:
            load()
            if tier is None:
                tier, env_key = classify(exc, trigger)
                if env_key is not None:
                    _count(env_key, 1, _now(now))
                    return None
            entry = builder.error_entry(exc, where=where, trigger=trigger, tier=tier,
                                        logger=logger, event=event, tool=tool)
            return _upsert(entry, _now(now))
    except Exception:  # noqa: BLE001 - a recorder must never raise
        return None


def record_site(kind: str, *, file: str, line: int, fn: str, where: str, trigger: str,
                tier: str, logger: str | None = None, now=None) -> str | None:
    """Record an event that carries no exception (an ERROR log line, an
    unclean exit) by *kind* and its call site. Never raises."""
    try:
        if kind not in SITE_KINDS:
            return None
        location = {"file": file, "line": line, "fn": fn}  # checked with the entry
        with _lock:
            load()
            ts = _now(now)
            entry = {
                "fingerprint": fp.site_fingerprint(kind, file, fn), "tier": tier,
                "where": where, "trigger": trigger, "logger": logger, "type": None,
                "cause_types": [], "errno": None, "tg_class": None, "event": None,
                "tool": None, "count": 1, "first_day": _day(ts), "last_day": _day(ts),
                "versions_seen": [], "frames": [location], "external": []}
            return _upsert(entry, ts)
    except Exception:  # noqa: BLE001 - a recorder must never raise
        return None


def record_relayed(message: dict, now=None) -> str | None:
    """Record an error another aipager process told the daemon about: a
    :func:`relay.read_error_datagram` result (already checked). A hook's
    own crash counts ``hook_error``, a PreToolUse it refused because it
    could not check it counts ``hook_fail_closed``; an environment error
    is only its counter; anything else is a bug: a hook error or a
    refused tool (``fail_closed``), or a command-line crash. Never
    raises."""
    try:
        with _lock:
            load()
            ts = _now(now)
            where = message["where"]
            if where != "cli":
                _count("hook_fail_closed" if message["denied"] else "hook_error", 1, ts)
            if message["env"] is not None:
                _count(message["env"], 1, ts)
                return None
            facts = message["facts"]
            key = fp.fingerprint_of(facts["type"], facts["frames"])
            if key not in _errors:
                _relayed_new[:] = [t for t in _relayed_new if t > ts - 3600]
                if len(_relayed_new) >= RELAY_NEW_PER_HOUR:
                    return None
                _relayed_new.append(ts)
            trigger = ("crash" if where == "cli"
                       else "fail_closed" if message["denied"] else "hook_error")
            entry = {
                "fingerprint": key, "tier": "bug", "where": where,
                "trigger": trigger, "logger": None, "type": facts["type"],
                "cause_types": facts["cause_types"], "errno": facts["errno"],
                "tg_class": None, "event": message["event"], "tool": message["tool"],
                "count": 1, "first_day": _day(ts), "last_day": _day(ts),
                "versions_seen": [], "frames": facts["frames"], "external": facts["external"]}
            return _upsert(entry, ts)
    except Exception:  # noqa: BLE001 - a recorder must never raise
        return None


def _upsert(entry: dict, ts: int) -> str | None:
    """Merge *entry* into its fingerprint's record. The record moves to the
    most recent end; past ``MAX_FINGERPRINTS`` the least recent goes."""
    global _dirty
    key = entry["fingerprint"]
    old = _errors.pop(key, None)
    version = _version()
    if old is None:
        entry["count"], entry["first_day"] = 1, _day(ts)
        seen: list[str] = []
        record = {"first_ts": ts, "occasions": 1, "occasion_ts": ts}
    else:
        prev = old["entry"]
        entry["count"] = min(prev["count"] + 1, COUNT_MAX)
        entry["first_day"] = prev["first_day"]
        if prev["tier"] == "bug":
            entry["tier"] = "bug"
        seen = [v for v in prev["versions_seen"] if v != version]
        record = {"first_ts": old["first_ts"], "occasions": old["occasions"],
                  "occasion_ts": old["occasion_ts"]}
        if ts - old["occasion_ts"] >= OCCASION_GAP:
            record["occasions"] = min(old["occasions"] + 1, COUNT_MAX)
            record["occasion_ts"] = ts
    entry["last_day"] = _day(ts)
    entry["versions_seen"] = (seen + [version])[-5:] if version else seen[-5:]
    clean, problems = sc.validate_part(_ERROR_ITEM, entry)
    if problems or not isinstance(clean, dict) or set(clean) != _ENTRY_KEYS:
        if old is not None:
            _errors[key] = old  # keep what was there; the new one is not storable
        return None
    record["entry"] = clean
    record["last_ts"] = max(ts, record["first_ts"])
    _errors[key] = record
    while len(_errors) > MAX_FINGERPRINTS:
        _errors.popitem(last=False)
    _dirty = True
    return key


def record_counter(key: str, n: int = 1, now=None) -> None:
    """Add *n* to the fixed counter *key* in this hour. Never raises."""
    try:
        if key in sc.COUNTER_KEYS and type(n) is int and n > 0:
            with _lock:
                load()
                _count(key, n, _now(now))
    except Exception:  # noqa: BLE001 - a recorder must never raise
        pass


def _count(key: str, n: int, ts: int) -> None:
    global _dirty
    bucket = _counters.setdefault(_hour(ts), {})
    bucket[key] = min(bucket.get(key, 0) + n, COUNT_MAX)
    _dirty = True


def record_digest(site: str, level: str, now=None) -> None:
    """Count one log line of *level* at *site* (``aipager/x.py:12``) in
    this hour; never its text. Never raises."""
    global _dirty
    try:
        _clean, problems = sc.validate_part(_DIGEST_ROW, {"site": site, "level": level, "n": 1})
        if problems:
            return
        with _lock:
            load()
            bucket = _digest.setdefault(_hour(_now(now)), {})
            key = (site, level)
            if key in bucket or len(bucket) < DIGEST_SITES_PER_HOUR:
                bucket[key] = min(bucket.get(key, 0) + 1, COUNT_MAX)
                _dirty = True
    except Exception:  # noqa: BLE001 - a recorder must never raise
        pass


def note_exit(kind: str, now=None) -> None:
    """How the previous daemon ended: ``clean``, ``crash`` (an unclean
    exit on this boot) or ``reboot`` (it went down with the machine).
    Never raises."""
    global _last_exit, _dirty
    try:
        if kind not in EXIT_KINDS:
            return
        with _lock:
            load()
            _last_exit = kind
            if kind == "crash":
                _unclean.append(_now(now))
                del _unclean[:-UNCLEAN_EXITS_KEPT]
            _dirty = True
    except Exception:  # noqa: BLE001 - a recorder must never raise
        pass


# ---- reading -----------------------------------------------------------------

def records() -> list[dict]:
    """Every stored record (entry plus timestamps), least recent first:
    copies, for the offer policy."""
    with _lock:
        load()
        return [dict(r, entry=dict(r["entry"])) for r in _errors.values()]


def errors(limit: int = 10) -> list[dict]:
    """The most recently seen ``errors[]`` entries, newest first."""
    with _lock:
        load()
        return [dict(r["entry"]) for r in reversed(_errors.values())][:limit]


def counters_24h(now=None) -> dict[str, int]:
    """The counters of the last 24 hourly buckets, summed."""
    since = _hour(_now(now) - 23 * 3600)
    out: dict[str, int] = {}
    with _lock:
        load()
        for hour, bucket in _counters.items():
            if hour >= since:
                for key, n in bucket.items():
                    out[key] = min(out.get(key, 0) + n, COUNT_MAX)
    return out


def digest_24h(now=None) -> list[dict]:
    """The log digest of the last 24 hourly buckets, busiest first."""
    since = _hour(_now(now) - 23 * 3600)
    totals: dict[tuple[str, str], int] = {}
    with _lock:
        load()
        for hour, bucket in _digest.items():
            if hour >= since:
                for key, n in bucket.items():
                    totals[key] = min(totals.get(key, 0) + n, COUNT_MAX)
    rows = [{"site": site, "level": level, "n": n} for (site, level), n in totals.items()]
    return sorted(rows, key=lambda row: -row["n"])


def exit_facts(now=None) -> tuple[int, str]:
    """``(unclean exits in the last 7 days, how the last daemon ended)``."""
    since = _now(now) - UNCLEAN_EXITS_WINDOW
    with _lock:
        load()
        return sum(1 for ts in _unclean if ts >= since), _last_exit


# ---- the file ----------------------------------------------------------------

def _path() -> Path:
    """Read LATE from config (the test redirect is one entry there)."""
    from aipager import config

    return Path(config.REPORTS_FILE)


def reset() -> None:
    """Forget everything in memory; the file is read again on next use."""
    global _loaded, _dirty, _last_write
    with _lock:
        reset_memory()
        _loaded = _dirty = False
        _last_write = 0.0


def reset_memory() -> None:
    """Empty the in-memory tables (a file that could not be read whole
    leaves none of itself behind)."""
    global _last_exit, _policy
    with _lock:
        _errors.clear()
        _counters.clear()
        _digest.clear()
        _unclean.clear()
        _relayed_new.clear()
        _last_exit = sc.UNKNOWN
        _policy = report_policy.State()


def load() -> None:
    """Read the file into memory, once; a bad file reads as empty. A file
    that exists but cannot be read whole (not JSON, another version, too
    big, half written) leaves automatic offers OFF: its policy state is
    unknown, and the defaults would undo two declines or the 3-day gap.
    Only a missing file starts the policy fresh."""
    global _loaded, _policy
    with _lock:
        if _loaded:
            return
        _loaded = True
        try:
            exists = _path().exists()
        except OSError:
            exists = True
        try:
            document = _read()
            if isinstance(document, dict) and document.get("version") == SCHEMA_VERSION:
                _adopt(document)
                return
        except Exception:  # noqa: BLE001 - unreadable reads as empty
            log.debug("the problem report store could not be read", exc_info=True)
            reset_memory()
        if exists:
            _policy = report_policy.State(auto_off=True)


def _read():
    store_path = _path()
    if not store_path.is_file() or store_path.stat().st_size > MAX_FILE_BYTES:
        return None
    # NaN and Infinity parse as floats; every number kept must be an int.
    return json.loads(store_path.read_text(encoding="utf-8"))


def _clean_record(raw) -> dict | None:
    if not isinstance(raw, dict) or set(raw) != _RECORD_KEYS:
        return None
    entry, problems = sc.validate_part(_ERROR_ITEM, raw["entry"])
    if problems or not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        return None
    if not all(_is_ts(raw[k]) for k in ("first_ts", "last_ts", "occasion_ts")):
        return None
    if type(raw["occasions"]) is not int or not 1 <= raw["occasions"] <= COUNT_MAX:
        return None
    return {"entry": entry, "first_ts": raw["first_ts"], "last_ts": raw["last_ts"],
            "occasions": raw["occasions"], "occasion_ts": raw["occasion_ts"]}


def _hourly(raw) -> list[tuple[str, object]]:
    if not isinstance(raw, dict):
        return []
    return [(hour, value) for hour, value in raw.items()
            if isinstance(hour, str) and HOUR_RE.fullmatch(hour)]


def _adopt(document: dict) -> None:
    global _last_exit
    raw_errors = document.get("errors")
    for raw in raw_errors[-MAX_FINGERPRINTS:] if isinstance(raw_errors, list) else []:
        record = _clean_record(raw)
        if record is not None:
            _errors[record["entry"]["fingerprint"]] = record
    for hour, bucket in _hourly(document.get("counters")):
        if isinstance(bucket, dict):
            kept = {k: v for k, v in bucket.items()
                    if k in sc.COUNTER_KEYS and type(v) is int and 0 < v <= COUNT_MAX}
            if kept:
                _counters[hour] = kept
    for hour, rows in _hourly(document.get("digest")):
        kept_rows: dict[tuple[str, str], int] = {}
        for row in rows[:DIGEST_SITES_PER_HOUR] if isinstance(rows, list) else []:
            clean, problems = sc.validate_part(_DIGEST_ROW, row)
            if not problems and isinstance(clean, dict) and len(clean) == 3 and clean["n"] > 0:
                kept_rows[(clean["site"], clean["level"])] = clean["n"]
        if kept_rows:
            _digest[hour] = kept_rows
    exits = document.get("exits")
    if isinstance(exits, dict):
        unclean = exits.get("unclean")
        if isinstance(unclean, list):
            _unclean.extend(ts for ts in unclean[-UNCLEAN_EXITS_KEPT:] if _is_ts(ts))
        if exits.get("last") in EXIT_KINDS:
            _last_exit = exits["last"]
    global _policy
    _policy = _clean_policy(document.get("policy", _NO_POLICY))
    _trim(_now(None))


_NO_POLICY = object()
_POLICY_KEYS = frozenset({"last_offer_ts", "pending", "declines_in_row", "auto_off", "offered"})


def _clean_policy(raw) -> report_policy.State:
    """The offer policy's state from the file. Absent (a store from before
    step 1c, or a fresh one): the defaults. Present but not exactly what
    :func:`_document` writes: automatic offers off, which errs quiet (the
    manual report still works)."""
    if raw is _NO_POLICY:
        return report_policy.State()
    off = report_policy.State(auto_off=True)
    if not isinstance(raw, dict) or set(raw) != _POLICY_KEYS:
        return off
    last, pending, declines = raw["last_offer_ts"], raw["pending"], raw["declines_in_row"]
    offered = raw["offered"]
    if not (last is None or _is_ts(last)) or type(raw["auto_off"]) is not bool:
        return off
    if type(declines) is not int or not 0 <= declines <= 1000:
        return off
    if pending and last is None:
        return off  # an offer awaiting its answer was made at some time
    if (not isinstance(pending, list) or len(pending) > report_policy.MAX_PER_OFFER
            or not all(isinstance(k, str) and fp.FINGERPRINT_RE.fullmatch(k) for k in pending)):
        return off
    if not isinstance(offered, dict) or len(offered) > report_policy.OFFERED_KEPT:
        return off
    for key, value in offered.items():
        if not (fp.FINGERPRINT_RE.fullmatch(key) and isinstance(value, dict)
                and set(value) == {"version", "ts"} and _is_ts(value["ts"])
                and (value["version"] is None or sc.VERSION.check(value["version"])
                     == value["version"] != sc.INVALID)):
            return off
    # A time in the future (a clock that was wrong, or went back) is dated
    # now: trusted as it is, it would hold offers off until that date. The
    # store does this itself, so no caller can forget it.
    now = _now(None)
    last = min(last, now) if last is not None else None
    offered = {k: {"version": v["version"], "ts": min(v["ts"], now)} for k, v in offered.items()}
    return report_policy.State(last_offer_ts=last, pending=tuple(pending),
                               declines_in_row=declines, auto_off=raw["auto_off"],
                               offered=offered)


def policy_state() -> report_policy.State:
    """The offer policy's state (see :mod:`aipager.report.policy`): a copy."""
    with _lock:
        load()
        return replace(_policy, offered={k: dict(v) for k, v in _policy.offered.items()})


def save_policy(state: report_policy.State) -> None:
    """Keep the offer policy's new *state* and write it NOW: a hard kill
    before the next debounced save would forget an offer, and a daemon in
    a crash loop would then offer the same bug again within the gap.
    Offers are rare, so the write costs nothing."""
    global _policy, _dirty
    with _lock:
        load()
        if state == _policy:
            return  # unchanged: a routine tick must not rewrite the file
        _policy = state
        _dirty = True
        save_if_dirty(force=True)


def _trim(ts: int) -> None:
    for table, hours in ((_counters, KEEP_COUNTER_HOURS), (_digest, KEEP_DIGEST_HOURS)):
        oldest = _hour(ts - (hours - 1) * 3600)
        for hour in [h for h in table if h < oldest]:
            del table[hour]
    _unclean[:] = [t for t in _unclean if t >= ts - UNCLEAN_EXITS_WINDOW]


def _document() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "errors": list(_errors.values()),
        "counters": {hour: dict(bucket) for hour, bucket in sorted(_counters.items())},
        "digest": {hour: [{"site": site, "level": level, "n": n}
                          for (site, level), n in bucket.items()]
                   for hour, bucket in sorted(_digest.items())},
        "exits": {"unclean": list(_unclean), "last": _last_exit},
        "policy": {"last_offer_ts": _policy.last_offer_ts, "pending": list(_policy.pending),
                   "declines_in_row": _policy.declines_in_row, "auto_off": _policy.auto_off,
                   "offered": {k: dict(v) for k, v in _policy.offered.items()}},
    }


def save_if_dirty(*, force: bool = False) -> bool:
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
            _trim(_now(None))
            target = _path()
            check_write(target)
            # Owner-only from the first byte; a folder made here is owner-only.
            write_private(target, json.dumps(_document(), allow_nan=False),
                          dir_mode=STATE_DIR_MODE)
            _dirty = False
            _last_write = mono
            return True
    except Exception:  # noqa: BLE001 - a full disk must not stop the daemon
        log.debug("the problem report store could not be written", exc_info=True)
        return False
