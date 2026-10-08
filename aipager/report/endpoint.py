"""Where a problem report goes (roadmap 8.112, design section 5).

The Sentry key (a DSN, public by design: it only lets one submit events)
lives in ``report-endpoint.json`` in the aipager repository, so it can be
rotated or switched off without a release. It is fetched only when the
user has confirmed a send (never at startup, never on a timer), checked
strictly, and its decision kept for :data:`CACHE_SECONDS`. When GitHub
cannot be reached, or the file is not one aipager understands, the key
compiled into this release is used instead; a well-formed file that says
"off" is obeyed and never overridden by that fallback.

The DSN check admits only a Sentry ingest host, so an edited file cannot
point reports anywhere but Sentry.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

import httpx

from aipager import _test_guard

ENDPOINT_URL = "https://raw.githubusercontent.com/dev-aly3n/aipager/main/report-endpoint.json"
#: The same key as the repository file (a test pins the two equal).
FALLBACK_DSN = ("https://1718006caf2b98a09c45304f632720ed"
                "@o4512218007339008.ingest.us.sentry.io/4512218016448512")
FETCH_TIMEOUT = 5.0
#: The file is about 200 bytes; anything much bigger is not ours.
MAX_BODY = 4096
CACHE_SECONDS = 86400
DOCUMENT_VERSION = 1
DOCUMENT_KEYS = frozenset({"v", "dsn", "enabled", "min_version"})
#: https, a 32-hex public key, a Sentry ingest host (``o<org>.ingest.sentry.io``
#: or ``o<org>.ingest.<region>.sentry.io``), a numeric project. Nothing else:
#: no secret key, no port, no path, no query.
DSN_RE = re.compile(
    r"https://([0-9a-f]{32})@(o[0-9]{1,20}\.ingest(?:\.[a-z]{2})?\.sentry\.io)/([0-9]{1,20})")
#: The leading release segments of a version (``0.8.0rc1`` -> ``0.8.0``).
RELEASE_RE = re.compile(r"^[0-9]{1,6}(?:\.[0-9]{1,6})*")
MIN_VERSION_RE = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}")

OK = "ok"
DISABLED = "disabled"
TOO_OLD = "too_old"


@dataclass(frozen=True)
class Dsn:
    public_key: str
    host: str
    project: str

    @property
    def envelope_url(self) -> str:
        return f"https://{self.host}/api/{self.project}/envelope/"


@dataclass(frozen=True)
class Endpoint:
    """``status`` is :data:`OK` (send to ``dsn``), :data:`DISABLED` (the
    kill switch) or :data:`TOO_OLD` (this release may no longer send);
    ``source`` is ``"file"`` or ``"fallback"``."""
    status: str
    dsn: Dsn | None
    source: str


def parse_dsn(text) -> Dsn | None:
    if not isinstance(text, str):
        return None
    m = DSN_RE.fullmatch(text)  # fullmatch: "$" would let a trailing newline through
    if m is None:
        return None
    return Dsn(public_key=m.group(1), host=m.group(2), project=m.group(3))


def release_tuple(version) -> tuple[int, ...] | None:
    """The numeric release segments of *version* (``0.7.20.dev3+g1`` is
    ``(0, 7, 20)``), or None when it has none or is the unknown build."""
    if not isinstance(version, str) or version.endswith("+unknown"):
        return None
    m = RELEASE_RE.match(version)
    if m is None:
        return None
    parts = tuple(int(p) for p in m.group(0).split(".")[:4])
    return parts + (0,) * (4 - len(parts))


def _below(version, minimum: str) -> bool:
    ours = release_tuple(version)
    floor = release_tuple(minimum)
    return ours is None or floor is None or ours < floor


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError
        out[key] = value
    return out


def read_document(body: bytes) -> dict | None:
    """The key file, if it is exactly the shape aipager understands."""
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        return None
    try:
        # Every field is type-checked below, so NaN or Infinity can never pass.
        doc = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or set(doc) != DOCUMENT_KEYS:
        return None
    if type(doc["v"]) is not int or doc["v"] != DOCUMENT_VERSION:
        return None
    if type(doc["enabled"]) is not bool or not isinstance(doc["dsn"], str):
        return None
    if not isinstance(doc["min_version"], str) or not MIN_VERSION_RE.fullmatch(doc["min_version"]):
        return None
    return doc


def decide(doc: dict, version) -> Endpoint | None:
    """What a well-formed key file means for this release; None when its
    key is not a Sentry one (the file is then ignored, as if unreachable)."""
    if not doc["enabled"] or doc["dsn"] == "":
        return Endpoint(DISABLED, None, "file")
    dsn = parse_dsn(doc["dsn"])
    if dsn is None:
        return None
    if _below(version, doc["min_version"]):
        return Endpoint(TOO_OLD, None, "file")
    return Endpoint(OK, dsn, "file")


def fallback() -> Endpoint:
    return Endpoint(OK, parse_dsn(FALLBACK_DSN), "fallback")


#: What building a client can raise: a proxy or certificate setting in the
#: environment httpx cannot use (an unknown proxy scheme, socks without
#: socksio, a missing certificate file). Caught by class, never read.
CLIENT_ERRORS = (ValueError, ImportError, OSError, httpx.HTTPError)


def client(transport: httpx.BaseTransport | None, version: str, timeout: float,
           what: str) -> httpx.Client | None:
    """The one way a problem report reaches the network: no redirect is
    ever followed (it would carry the request, the report included, to
    another host), and the only identity sent is aipager's version. None
    when httpx cannot build a client here. Under pytest a client without
    an injected transport is refused (``_test_guard.check_network``). An
    injected transport is shared by every client built on it and closed
    with the first (fine for a test's ``httpx.MockTransport``; in use none
    is injected, so each client has its own)."""
    if transport is None:
        _test_guard.check_network(what)
    try:
        return httpx.Client(transport=transport, timeout=timeout, follow_redirects=False,
                            headers={"User-Agent": f"aipager/{version}"})
    except CLIENT_ERRORS:
        return None


_lock = threading.Lock()
_cached: Endpoint | None = None
_cached_at: float | None = None


def reset_cache() -> None:
    global _cached, _cached_at
    with _lock:
        _cached, _cached_at = None, None


def _fetch(http: httpx.Client) -> bytes | None:
    try:
        # Uncompressed, so the cap below counts the bytes as they arrive.
        with http.stream("GET", ENDPOINT_URL, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                return None
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BODY:
                    return None
            return body
    except (httpx.HTTPError, httpx.StreamError, OSError):
        return None


def resolve(version, transport: httpx.BaseTransport | None, now: float) -> Endpoint:
    """The endpoint to send to now. A well-formed file's decision is kept
    for :data:`CACHE_SECONDS`; a failed fetch is not kept (the next send
    tries GitHub again) and falls back to the compiled key."""
    global _cached, _cached_at
    with _lock:
        if _cached is not None and _cached_at is not None and 0 <= now - _cached_at < CACHE_SECONDS:
            return _cached
    http = client(transport, version, FETCH_TIMEOUT, "problem report key file")
    if http is None:
        return fallback()
    with http:
        doc = read_document(_fetch(http))
    decision = decide(doc, version) if doc is not None else None
    if decision is None:
        return fallback()
    with _lock:
        _cached, _cached_at = decision, now
    return decision

