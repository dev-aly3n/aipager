"""Send one problem report to Sentry (roadmap 8.112, design section 5).

No Sentry SDK: it would collect frame locals and source lines by
default, the opposite of the report's allow-list. One hand-built
envelope goes out in one HTTPS POST, only after the user confirmed it:

- the envelope header: a random event id and the send time, nothing else;
- the event: release, environment, a fixed set of tags taken from the
  report's enums and versions (plus ``report_fp``, the checked fingerprint
  the user may quote), and the primary error as Sentry's
  exception (its type and its aipager code locations, never a message);
  Sentry groups events by our fingerprint, not its own guess;
- the attachment ``report.json``: exactly the bytes of
  :func:`builder.render_preview`, what the user saw. (Sentry trims a
  context object at 8 kB, so the whole report cannot ride in the event.)

The report is validated again first and refused unless it comes back
unchanged, so only an allow-listed report can be sent. At most
:data:`SENDS_PER_DAY` sends leave a machine a day, the key file's kill
switch is obeyed, and nothing is retried.
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from aipager.private_file import write_private
from aipager.report import builder, endpoint
from aipager.report import fingerprint as fp
from aipager.report import schema as sc

SEND_TIMEOUT = 10.0
#: A report with 10 errors of 8 frames is about 12 kB; far bigger is a bug.
MAX_PREVIEW_BYTES = 256 * 1024
#: Send attempts a machine makes per UTC day, manual and automatic together
#: (design section 6).
SENDS_PER_DAY = 5
#: The Sentry grouping key of a report that carries no error.
NO_ERROR_FINGERPRINT = "aipager-report-without-error"
#: ... and of one whose error has no usable fingerprint (kept apart from those).
UNKEYED_FINGERPRINT = "aipager-report-unkeyed-error"
ATTACHMENT_NAME = "report.json"
STATE_DIR_MODE = 0o700

SENT = "sent"
RATE_LIMITED = "rate_limited"   # Sentry said 429: the key's or the month's limit
REJECTED = "rejected"           # any other 4xx: the key revoked, a malformed envelope
OFFLINE = "offline"             # no answer, a timeout, a 5xx, the count not recordable
DISABLED = "disabled"           # the key file's kill switch
TOO_OLD = "too_old"             # the key file's min_version is above this release
DAILY_CAP = "daily_cap"
INVALID = "invalid"             # not an allow-listed report: refused, never sent
OUTCOMES = (SENT, RATE_LIMITED, REJECTED, OFFLINE, DISABLED, TOO_OLD, DAILY_CAP, INVALID)


@dataclass(frozen=True)
class SendResult:
    outcome: str
    #: What the user may quote: the primary error's fingerprint, else the
    #: event id (32 hex). Only for a sent report.
    reference: str | None = None


# ---- the report ----------------------------------------------------------------

def _complete(schema, value) -> bool:
    """Every field the event is built from is present (only ``note`` is
    optional)."""
    if not isinstance(schema, dict):
        return True
    if not isinstance(value, dict):
        return False
    return all(key in value and _complete(sub, value[key])
               for key, sub in schema.items() if key != "note")


def checked_preview(report) -> bytes | None:
    """The bytes the user previewed, if *report* is exactly an
    allow-listed report (validation leaves it unchanged); else None."""
    try:
        clean, _problems = sc.validate(report)
        # A leaf comes back as the very value or as "<invalid>", a dict
        # without unknown keys, a list cut and as a list: equal means the
        # report is already exactly the allow-list's.
        if clean != report:
            return None
        if not _complete(sc.SCHEMA, report):
            return None
        preview = builder.render_preview(report).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None
    if len(preview) > MAX_PREVIEW_BYTES:
        return None
    return preview


def _python_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def is_developer_install(report: dict) -> bool:
    """Design D4: an install from a local folder or a checkout, or an
    editable one, is a developer's."""
    facts = report["aipager"]
    return facts["origin"] in ("local", "vcs") or facts["install"] == "editable"


# A report is sent as validation left it, so any leaf may be the
# "<invalid>" mark. The report's own sections are there (checked_preview's
# _complete), but an errors[] item may be that mark instead of a dict, and a
# dict item holds only the keys it was given: the helpers below read an
# item's fields with a default and check their type before use.

def primary_error(report: dict) -> dict | None:
    """The error the event is about: the first errors[] item that is one."""
    return next((e for e in report["errors"] if isinstance(e, dict)), None)


def _fingerprint(error: dict | None) -> str | None:
    value = error.get("fingerprint") if error is not None else None
    return value if isinstance(value, str) and fp.FINGERPRINT_RE.fullmatch(value) else None


def _exception(error: dict) -> dict:
    type_name = error.get("type")
    if isinstance(type_name, str) and type_name != sc.INVALID:
        module, _, name = type_name.rpartition(".")
    else:
        # No exception (an ERROR log line, a crash, a hook memory-cap hit):
        # named by its trigger, an enum.
        trigger = error.get("trigger")
        module, name = "", trigger if isinstance(trigger, str) else sc.INVALID
    value = {"type": name or sc.INVALID}  # never empty ("builtins." would be)
    if module:
        value["module"] = module
    # The report keeps the innermost frame first; Sentry wants it last.
    frames = [{"filename": f["file"], "function": f["fn"], "lineno": f["line"],
               "in_app": True} for f in reversed(error.get("frames") or []) if isinstance(f, dict)]
    if frames:
        value["stacktrace"] = {"frames": frames}
    return value


def event(report: dict, event_id: str, now: float) -> dict:
    """The Sentry event: only enums, versions, code locations and the
    fingerprint, all from the validated report."""
    version = report["aipager"]["version"]
    out = {
        "event_id": event_id,
        "timestamp": int(now),
        "platform": "python",
        "level": "error",
        "release": f"aipager@{version}",
        "environment": "dev" if is_developer_install(report) else "production",
        "tags": {
            "aipager": version,
            "install": report["aipager"]["install"],
            "os": report["os"]["system"],
            "python": _python_minor(report["python"]["version"]),
            "claude_code": report["claude_code"]["version"] or sc.UNKNOWN,
            "trigger": report["trigger"],
        },
    }
    primary = primary_error(report)
    if primary is not None:
        key = _fingerprint(primary)
        out.update({"fingerprint": [key or UNKEYED_FINGERPRINT],
                    "exception": {"values": [_exception(primary)]}})
        if key is not None:
            out["tags"]["report_fp"] = key  # searchable: the fingerprint field is not
    else:
        trigger = report["trigger"]  # the enum "auto" or "manual": a fixed title
        out.update({"fingerprint": [NO_ERROR_FINGERPRINT],
                    "message": {"formatted": f"aipager problem report ({trigger})"}})
    return out


def _line(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sent_at(now: float) -> str:
    stamp = _dt.datetime.fromtimestamp(int(now), _dt.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(report: dict, preview: bytes, event_id: str, now: float) -> bytes:
    """Sentry's envelope: a header line, then each item's header line and
    payload; ``length`` is the payload's size in bytes."""
    payload = _line(event(report, event_id, now))
    return b"\n".join([
        _line({"event_id": event_id, "sent_at": _sent_at(now)}),
        _line({"type": "event", "length": len(payload), "content_type": "application/json"}),
        payload,
        _line({"type": "attachment", "length": len(preview), "filename": ATTACHMENT_NAME,
               "content_type": "application/json", "attachment_type": "event.attachment"}),
        preview,
    ]) + b"\n"


def _headers(dsn: endpoint.Dsn, version: str) -> dict:
    """The two headers a send adds to the client's own (``endpoint.client``:
    the user agent ``aipager/<version>`` and httpx's transfer headers), from
    the checked key (``DSN_RE``) and the validated version."""
    return {"Content-Type": "application/x-sentry-envelope",
            "X-Sentry-Auth": (f"Sentry sentry_version=7, sentry_key={dsn.public_key}, "
                              f"sentry_client=aipager/{version}")}


# ---- the daily count -----------------------------------------------------------

#: Within one process the count is read and written under this lock. The
#: daemon and an ``aipager report`` in a terminal are two processes: two
#: sends confirmed in the same instant may both pass the check (one send
#: over the cap) or lose one count; neither can send without a person's
#: confirmation, and a lost write fails closed (nothing is sent).
_lock = threading.Lock()
_COUNT_KEYS = frozenset({"day", "count"})
#: The count file is about 40 bytes; anything bigger is not ours.
MAX_COUNT_BYTES = 1024


def _sends_path() -> Path:
    from aipager import config

    return Path(config.REPORT_SENDS_FILE)


def _day(now: float) -> str:
    return _dt.datetime.fromtimestamp(int(now), _dt.timezone.utc).date().isoformat()


def _write_count(today: str, count: int) -> bool:
    try:
        write_private(_sends_path(), json.dumps({"day": today, "count": count}),
                      dir_mode=STATE_DIR_MODE)
        return True
    except OSError:
        return False


def sends_today(today: str) -> int:
    """Sends already made on the UTC day *today*. A file that is not one
    aipager wrote counts as the day's cap (fail closed) and is rewritten
    as exactly that, so the next day starts clean; an unreadable one counts
    as the cap too; a missing file is no sends."""
    sends_path = _sends_path()
    try:
        if not sends_path.exists():
            return 0
        # Never a folder, a pipe or a device, never more than a small file.
        if not sends_path.is_file() or sends_path.stat().st_size > MAX_COUNT_BYTES:
            return SENDS_PER_DAY
        raw = sends_path.read_bytes()
    except FileNotFoundError:
        return 0
    except OSError:
        return SENDS_PER_DAY
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        doc = None
    if (not isinstance(doc, dict) or set(doc) != _COUNT_KEYS
            or sc.DAY.check(doc["day"]) == sc.INVALID
            or type(doc["count"]) is not int or doc["count"] < 0):
        _write_count(today, SENDS_PER_DAY)
        return SENDS_PER_DAY
    if doc["day"] != today:
        return 0
    return min(doc["count"], SENDS_PER_DAY)


def _take_send(today: str) -> str | None:
    """Count one send attempt; the outcome that stops it, or None."""
    with _lock:
        count = sends_today(today)
        if count >= SENDS_PER_DAY:
            return DAILY_CAP
        if not _write_count(today, count + 1):
            return OFFLINE  # an uncountable send could break the cap
    return None


# ---- sending ---------------------------------------------------------------------

def send(report, transport: httpx.BaseTransport | None = None,
         now: float | None = None) -> SendResult:
    """Send *report*, once. Never raises for a network or disk problem
    (under pytest, an un-injected transport raises
    :class:`aipager._test_guard.LiveNetworkError`). Blocking: the daemon
    runs it in a thread."""
    now = time.time() if now is None else now
    preview = checked_preview(report)
    if preview is None:
        return SendResult(INVALID)
    # Built first: nothing after a send is counted can fail but the network.
    event_id = uuid.uuid4().hex
    body = envelope(report, preview, event_id, now)
    today = _day(now)
    with _lock:
        if sends_today(today) >= SENDS_PER_DAY:
            return SendResult(DAILY_CAP)
    version = report["aipager"]["version"]
    # Built before anything is counted: a client httpx cannot build here
    # (a proxy or certificate setting it cannot use) sends nothing.
    http = endpoint.client(transport, version, SEND_TIMEOUT, "problem report")
    if http is None:
        return SendResult(OFFLINE)
    with http:
        target = endpoint.resolve(version, transport, now)
        if target.status == endpoint.DISABLED:
            return SendResult(DISABLED)
        if target.status != endpoint.OK or target.dsn is None:
            return SendResult(TOO_OLD)
        stopped = _take_send(today)
        if stopped is not None:
            return SendResult(stopped)
        try:
            response = http.post(target.dsn.envelope_url, content=body,
                                 headers=_headers(target.dsn, version))
        except (httpx.HTTPError, httpx.StreamError, OSError):
            return SendResult(OFFLINE)
    status = response.status_code
    if status == 200:
        # The bug's fingerprint (Sentry: tag report_fp), else the whole event
        # id (Sentry finds an event only by its full id).
        return SendResult(SENT, _fingerprint(primary_error(report)) or event_id)
    if status == 429:
        return SendResult(RATE_LIMITED)
    if 400 <= status < 500:
        return SendResult(REJECTED)
    return SendResult(OFFLINE)
