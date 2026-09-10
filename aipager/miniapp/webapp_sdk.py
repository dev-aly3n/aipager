"""Serve Telegram's Mini App SDK (``telegram-web-app.js``) from the Mini
App's own origin whenever we have a copy of it, so the page needs exactly
one reachable host: the one that already delivered it.

The page's single third-party dependency is that script — it is what
turns ``window.Telegram.WebApp.initData`` from nothing into the signed
blob every API call authenticates with. Loaded straight from
``https://telegram.org`` (the way it was until roadmap 8.18) the page
depended on TWO reachable hosts: the tunnel that served the page, and
telegram.org. Where the phone's browser context cannot fetch the second,
the app runs its own ``if (!initData)`` guard and shows "Open this page
from the Telegram app to sign in" — a message that blames the user for a
network condition. Seen on a friend's install: three ``GET /`` 200s over
two days, never a single ``/api/*`` call, daemon and tunnel healthy, the
daemon's own network fetching the script fine.

So the daemon fetches the script itself and serves it at
``/telegram-web-app.js``. Two sources, best first, and **never raising**
(same contract as :mod:`aipager.miniapp.cloudflared_fetch`, whose shape
this mirrors):

1. the copy in memory — fetched from telegram.org by this process;
2. the cache file under the aipager data dir, written after every
   successful fetch and served immediately on later daemon starts.

There is deliberately no third source. aipager does **not** redistribute
Telegram's script: shipping 116 KB of Telegram-authored JavaScript inside
an MIT-licensed wheel (and onward through Homebrew, Snap and the AUR)
would put bytes we have no licence to relicense into every package.

That makes the empty case matter, and it is handled where the page is
rendered rather than here: when :meth:`WebAppSdk.get` has nothing,
``_handle_index`` renders the shell with the ORIGINAL absolute
``https://telegram.org/js/telegram-web-app.js`` src. So the worst case is
exactly the behaviour that shipped before this module existed — never
worse — and the good case needs one host instead of two. The route's 503
is therefore only reachable by something that asks for the script when
the page it came from did not reference it.

To make the good case the common case, :meth:`WebAppSdk.prefetch` runs at
Mini App server start: one background fetch that never blocks startup and
never raises, so the cache is usually warm before any phone loads the
page. After that a copy is refreshed **in the background, at most once
per day**, so a long-running daemon tracks Telegram's changes without a
page load ever waiting on telegram.org. No request ever waits on a fetch:
:meth:`get` returns what is already in hand and, at most, schedules a
refresh for later.

Validation is deliberately loose about *shape* and strict about *size*:
the body must be at least :data:`MIN_BYTES`, under :data:`MAX_BYTES`, and
open like JavaScript rather than like an HTML document (a captive portal,
a CDN block page, an error page). There is **no checksum pin** — Telegram
changes this file whenever they like, and a pin would silently freeze
every install the day they did. The daemon never executes the script; to
it the bytes are data. The phone's browser runs them, exactly as before.

Stdlib only, on purpose — same import-cost reasoning as
:mod:`aipager.miniapp.cloudflared_fetch`.
"""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

SDK_URL = "https://telegram.org/js/telegram-web-app.js"

CACHE_FILENAME = "telegram-web-app.js"

# The real file is ~115 KB. Anything past this is not the SDK, whatever
# the server said — and it bounds what one fetch can hold in memory.
MAX_BYTES = 2 * 1024 * 1024

# ...and anything under this is not the SDK either. On 2026-09-10
# telegram.org served 116,510 bytes (sha256
# 3549138a7934039fe7dfd1291a4ee739bd2b705a614308053a8b08a87d85c451), so
# this floor is under a third of the real file and two orders of
# magnitude above what the bodies it exists to reject look like: a plain
# text error page ("Service Unavailable"), or the first packet of a body
# that stopped arriving. Those are the cases a shape check CANNOT catch,
# because a truncated SDK still opens with ``// WebView``. When the floor
# does trip we serve nothing and the page falls back to telegram.org
# directly, so it can only ever make the page better.
MIN_BYTES = 32 * 1024

# The fetch never happens in a request path (prefetch at startup, then a
# daily background refresh), so this bounds a background task only.
_DOWNLOAD_TIMEOUT_SECONDS = 15.0

# One fetch attempt per day, success or failure. A failed attempt does
# not retry sooner: until it succeeds the page loads the script straight
# from telegram.org, exactly as it did before this module existed.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60.0


def cache_dir() -> Path:
    """Where the fetched script is/will be cached.

    Honours ``AIPAGER_WEBAPP_SDK_CACHE_DIR`` (read fresh on every call,
    never cached at import time) so tests can redirect it with a plain
    env-var patch and never touch ``~/.local/share/aipager/webapp-sdk/``
    — the same root and the same override shape as
    :func:`aipager.miniapp.cloudflared_fetch.cache_dir`. Created
    ``0o700`` with the mode re-applied after ``mkdir`` (which is subject
    to umask), mirroring that function.
    """
    override = os.environ.get("AIPAGER_WEBAPP_SDK_CACHE_DIR")
    if override:
        directory = Path(override)
    else:
        directory = Path.home() / ".local" / "share" / "aipager" / "webapp-sdk"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def looks_like_javascript(data: bytes) -> bool:
    """Pure, no I/O. ``True`` iff ``data`` is between :data:`MIN_BYTES`
    and :data:`MAX_BYTES` long and opens like a script — a comment, an
    identifier, or one of the punctuation characters an expression can
    start with (an IIFE's ``(``, a ``!function`` prelude, a
    ``"use strict"`` directive) — rather than like an HTML document.

    This is the last guard between "telegram.org answered" and "serve
    these bytes as JavaScript to every phone", and it is two independent
    guards on purpose:

    * the **size** bounds catch what a shape check cannot — a truncated
      SDK opens exactly like a whole one;
    * the **shape** check catches what size cannot — a captive portal, a
      CDN block page or a 200-with-an-error-body, all of which begin
      with ``<`` and none of which may ever reach a browser under our
      JavaScript content type.

    It is NOT a JavaScript parser and does not try to be one — see the
    module docstring for why there is no checksum either.
    """
    if not data or len(data) < MIN_BYTES or len(data) > MAX_BYTES:
        return False
    head = data.lstrip()
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:].lstrip()
    if not head:
        return False
    if head.startswith((b"//", b"/*")):
        return True
    first = head[:1]
    if first == b"<":
        return False
    return first.isalpha() or first in (b"_", b"$", b"(", b"!", b'"', b"'", b";")


def _download_sync(url: str) -> bytes | None:
    """Fetch ``url`` synchronously (cert-verified by default — no custom
    SSL context). Always called inside an executor, never on the event
    loop thread. Reads at most ``MAX_BYTES + 1`` so an oversized body is
    rejected by :func:`looks_like_javascript` instead of held in full.

    A body that stops early is discarded, not returned. That needs
    saying, because ``HTTPResponse.read(amt)`` is the one read path that
    does NOT raise on a short body: CPython's ``http.client`` hands back
    whatever arrived and explains why in its own source ("Ideally, we
    would raise IncompleteRead if the content-length wasn't satisfied,
    but it might break compatibility"). Nothing downstream could catch
    that for us — a truncated SDK still opens with ``// WebView`` — and
    the bytes would be adopted, cached, and served to every phone as
    JavaScript for a day, surviving restarts. So:

    * with a ``Content-Length``, require the body to be exactly that long;
    * without one, a truncated *chunked* body raises ``IncompleteRead``
      (caught below), and a truncated *close-delimited* body is
      indistinguishable from a complete one at this layer — which is
      what :data:`MIN_BYTES` is the backstop for.

    Never raises: a network failure, a bad status, a timeout, a short
    read — all collapse to ``None``. ``urllib.error.HTTPError`` is a
    ``URLError`` subclass, so it is covered by the same branch.
    """
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_BYTES + 1)
            declared = resp.getheader("Content-Length")
            if declared is not None and declared.strip().isdigit():
                expected = int(declared.strip())
                # An oversized body is read short deliberately (only
                # MAX_BYTES + 1 of it), so its length cannot match what
                # was declared. Leave that one to the size cap in
                # looks_like_javascript, which names the real problem.
                if expected <= MAX_BYTES and len(data) != expected:
                    log.warning(
                        "webapp sdk: short read for %s (%d of %d bytes) — discarding",
                        url, len(data), expected,
                    )
                    return None
            return data
    except (
        urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException,
    ) as exc:
        # IncompleteRead (a chunked body that stops early) is an
        # HTTPException, not an OSError — without it in this tuple the
        # function would raise, contrary to the promise above.
        log.warning("webapp sdk: fetch failed for %s: %s", url, exc)
        return None


def _read_cache(path: Path) -> tuple[bytes, float] | None:
    """``(body, mtime)`` for a valid cache file, else ``None``. A cache
    that fails validation is treated as absent, not served."""
    try:
        data = path.read_bytes()
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if not looks_like_javascript(data):
        log.warning("webapp sdk: cache file %s is not JavaScript — ignoring it", path)
        return None
    return data, mtime


def _atomic_write(dest: Path, data: bytes) -> bool:
    """Write ``data`` to a temp file beside ``dest`` and ``replace()`` it
    into place, so a concurrent reader never sees a half-written file.
    Never raises; returns ``False`` and best-effort cleans up on
    failure."""
    tmp = dest.with_name(f"{dest.name}.download-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except OSError as exc:
        log.warning("webapp sdk: could not write cache file %s: %s", dest, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class WebAppSdk:
    """One per :class:`~aipager.miniapp.server.MiniAppServer`. Holds the
    in-memory copy and the refresh bookkeeping; :meth:`get` is the only
    entry point the routes need, :meth:`prefetch` the only one startup
    needs.

    ``clock`` is wall-clock seconds (``time.time`` by default) — wall
    rather than monotonic because it is compared against the cache
    file's mtime, which is how "when was this copy fetched" survives a
    daemon restart. Injectable so tests drive a day forward without
    waiting one.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._body: bytes | None = None
        # "fetched" | "cache" | "" — for logs and tests only.
        self._source = ""
        # Wall-clock time of the last fetch attempt (or of the cache
        # file's write, when the body was loaded from it). ``None`` means
        # no attempt yet in this process and no cache on disk.
        self._last_attempt: float | None = None
        self._cache_checked = False
        self._refresh_task: asyncio.Task | None = None

    @property
    def source(self) -> str:
        return self._source

    async def get(self) -> bytes | None:
        """The script body if we have one, else ``None`` — never a fetch
        the caller has to wait for, and never an exception.

        ``None`` is not a failure to handle here: it is the signal that
        ``_handle_index`` should render the page pointing at telegram.org
        instead, which is what it did before this module existed.
        """
        try:
            if not self._cache_checked:
                self._load_cache()
            if self._refresh_due():
                self._start_background_refresh()
        except Exception:
            # Same reasoning as cloudflared_fetch.ensure_cloudflared: the
            # never-raises promise has to cover every step, not just the
            # ones already wrapped inside.
            log.warning("webapp sdk: unexpected failure", exc_info=True)
        return self._body

    def prefetch(self) -> None:
        """Warm the copy at Mini App server start.

        Called from a running loop, returns immediately, and schedules at
        most the one background fetch :meth:`get` would have scheduled on
        the first page load — so the common case is that the cache is
        already warm when a phone arrives, and the page never has to fall
        back to telegram.org. Never raises, never blocks startup: a
        daemon whose first fetch fails simply serves the pre-8.18 page.
        """
        try:
            if not self._cache_checked:
                self._load_cache()
            if self._refresh_due():
                self._start_background_refresh()
        except Exception:
            log.warning("webapp sdk: prefetch could not start", exc_info=True)

    def close(self) -> None:
        """Cancel an in-flight background refresh. Called from the
        server's ``stop()``; harmless when nothing is running."""
        task, self._refresh_task = self._refresh_task, None
        if task is not None and not task.done():
            task.cancel()

    # -- internals -------------------------------------------------------

    def _load_cache(self) -> None:
        self._cache_checked = True
        try:
            path = cache_dir() / CACHE_FILENAME
        except OSError as exc:
            log.warning("webapp sdk: cache dir unavailable: %s", exc)
            return
        found = _read_cache(path)
        if found is None:
            return
        self._body, mtime = found
        self._source = "cache"
        # The cached copy's write time is the last successful fetch, by
        # this or an earlier daemon process — so a restart does not
        # refetch a copy that is hours old.
        self._last_attempt = mtime

    def _refresh_due(self) -> bool:
        if self._last_attempt is None:
            return True
        return self._clock() - self._last_attempt >= REFRESH_INTERVAL_SECONDS

    def _start_background_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        # Stamp the attempt now, not inside the task, so the next request
        # arriving before the task's first await does not start a second.
        self._last_attempt = self._clock()
        self._refresh_task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> bool:
        """One fetch attempt. On success the body, the source and the
        cache file are all replaced; on any failure NOTHING changes —
        whatever was being served keeps being served, and an existing
        good cache file is left exactly as it was. Never raises."""
        self._last_attempt = self._clock()
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, _download_sync, SDK_URL)
            if data is None:
                return False
            if not looks_like_javascript(data):
                log.warning(
                    "webapp sdk: rejected fetched body (%d bytes) — not the SDK",
                    len(data),
                )
                return False
            # Only past BOTH checks does anything get replaced: the cache
            # write below is the one that must never see a partial body.
            self._body = data
            self._source = "fetched"
            self._write_cache(data)
            log.info("webapp sdk: fetched %d bytes from %s", len(data), SDK_URL)
            return True
        except Exception:
            log.warning("webapp sdk: refresh failed unexpectedly", exc_info=True)
            return False

    def _write_cache(self, data: bytes) -> None:
        try:
            path = cache_dir() / CACHE_FILENAME
        except OSError as exc:
            log.warning("webapp sdk: cache dir unavailable: %s", exc)
            return
        _atomic_write(path, data)


__all__ = [
    "CACHE_FILENAME",
    "MAX_BYTES",
    "MIN_BYTES",
    "REFRESH_INTERVAL_SECONDS",
    "SDK_URL",
    "WebAppSdk",
    "cache_dir",
    "looks_like_javascript",
]
