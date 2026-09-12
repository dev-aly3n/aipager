"""Tests for aipager.miniapp.webapp_sdk and the ``GET /telegram-web-app.js``
route that serves Telegram's Mini App SDK from the page's own origin.

No test here reaches telegram.org: ``_download_sync`` is faked in every
test that gets near it (the global conftest guard refuses it otherwise),
and the cache dir is always redirected to ``tmp_path`` via
``AIPAGER_WEBAPP_SDK_CACHE_DIR`` — asserted below, not assumed.
"""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import re
import threading
import time
import urllib.error
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp import webapp_sdk as sdk_mod
from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.static import SDK_SRC_SELF, SDK_SRC_TELEGRAM
from aipager.miniapp.webapp_sdk import WebAppSdk
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

# Captured at import time, before the autouse conftest guard replaces the
# module attribute with its "offline" recorder — the only way to test the
# real function's own error handling.
REAL_DOWNLOAD = sdk_mod._download_sync
# Same, for the scheduler the suite-wide fixture no-ops: these tests are
# the ones that must see it really schedule.
REAL_START_REFRESH = WebAppSdk._start_background_refresh

MIN = sdk_mod.MIN_BYTES
DAY = sdk_mod.REFRESH_INTERVAL_SECONDS


def _js_body(marker: bytes) -> bytes:
    """A plausible SDK body: opens the way the real file does, carries a
    marker so two copies can be told apart, and clears MIN_BYTES."""
    return (
        b"// WebView\n(function () { window.Telegram = { WebApp: {} }; })();\n"
        + marker
        + b"\n// " + b"x" * MIN + b"\n"
    )


FETCHED = _js_body(b"// fetched just now")
CACHED = _js_body(b"// cached earlier")

# A block page big enough that the SIZE guard cannot be what rejects it —
# otherwise the shape guard below would be untested.
HTML_PAGE = (
    b"<!DOCTYPE html>\n<html><head><title>Blocked</title></head><body>\n"
    + b"<p>This request was blocked by the network.</p>\n" * 900
    + b"</body></html>\n"
)

# The two bodies that look like JavaScript and are not the SDK.
TRUNCATED = b"// WebView\n(function () {\n  var eventHandlers = {};\n"
ERROR_TEXT = b"Service Unavailable"


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """Belt and braces with the conftest guard: this file's own redirect,
    so it reads as self-contained and survives a conftest refactor — and
    the one place that puts the real background scheduler back, since the
    suite-wide fixture no-ops it for the tests that do not care."""
    monkeypatch.setenv("AIPAGER_WEBAPP_SDK_CACHE_DIR", str(tmp_path / "sdk-cache"))
    monkeypatch.setattr(
        WebAppSdk, "_start_background_refresh", REAL_START_REFRESH
    )


@pytest.fixture
def run_async():
    """Override the shared ``run_async`` for this file, to CLOSE the loops.

    The shared fixture abandons each event loop it creates. That is free
    for a test that only awaits coroutines, but tests here drive a real
    executor thread — the download runs in the loop's default
    ``ThreadPoolExecutor`` — and an abandoned loop keeps that thread alive
    until the cyclic GC happens to collect the loop. Each live thread
    costs 8 MB of *address space* (its stack) plus a glibc arena, and a
    leak here would not fail THIS file — it would fail an unrelated later
    test whose ``run_in_executor`` can no longer mmap a stack
    ("RuntimeError: can't start new thread", seen in test_voice.py).

    That used to be a near-certainty rather than a risk: the suite ran
    under a hard 1 GiB ``RLIMIT_AS``, because the first test file to call
    ``notify_hook.main()`` clamped the pytest process itself and nothing
    could raise it back, leaving a full run a few MB under the ceiling.
    conftest's autouse ``_never_clamp_the_test_process`` fixture closed
    that hole, so the ceiling is now whatever the OS gives us — but the
    thread hygiene below stays: it is cheap, and it is what keeps this
    file from being the one that puts the next ceiling in reach.

    So: shut the default executor down and close each loop when the test
    ends. Pending tasks are cancelled first, so closing never leaves a
    "Task was destroyed but it is pending" behind.
    """
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run

    for loop in loops:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


def _fake_fetch(monkeypatch, result):
    """Replace the download with a stub answering ``result`` (bytes or
    ``None`` for "offline"); returns the list of URLs it was called with."""
    calls: list = []

    def _stub(url):
        calls.append(url)
        return result

    monkeypatch.setattr(sdk_mod, "_download_sync", _stub)
    return calls


def _cache_path() -> Path:
    return sdk_mod.cache_dir() / sdk_mod.CACHE_FILENAME


def _write_cache(body: bytes, *, mtime: float | None = None) -> Path:
    path = _cache_path()
    path.write_bytes(body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class _Clock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


class _FakeResponse:
    """Just enough of ``http.client.HTTPResponse`` for ``_download_sync``:
    a bounded ``read``, a ``getheader``, and the context manager."""

    def __init__(self, body: bytes, *, declared: int | None = None, raises=None):
        self._body = body
        self._declared = declared
        self._raises = raises

    def read(self, amt=None):
        if self._raises is not None:
            raise self._raises
        return self._body if amt is None else self._body[:amt]

    def getheader(self, name, default=None):
        if name.lower() == "content-length" and self._declared is not None:
            return str(self._declared)
        return default

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(monkeypatch, response):
    def _open(url, timeout=None):
        return response

    monkeypatch.setattr(sdk_mod.urllib.request, "urlopen", _open)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(Member(id=555, label="ada", role="developer"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"
    return MiniAppServer(bot, registry, port=8765)


async def _client_for(server: MiniAppServer) -> TestClient:
    client = TestClient(TestServer(server._build_app()))
    await client.start_server()
    return client


# ===== the shell picks its SDK source ======================================

def test_page_loads_the_sdk_from_our_origin_when_the_daemon_has_it(
    server, run_async, monkeypatch,
):
    """The good case: we have the script, so the page asks THIS host for
    it and nothing else. Flip the selector in _handle_index and this
    fails."""
    _write_cache(CACHED)

    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get("/")).text()
        finally:
            await client.close()
        assert f'<script src="{SDK_SRC_SELF}"></script>' in body
        assert SDK_SRC_TELEGRAM not in body
        assert [u for u in re.findall(r'(?:src|href)="(https?://[^"]+)"', body)] == []

    run_async(_run())


def test_page_falls_back_to_telegram_when_the_daemon_has_no_copy(
    server, run_async, monkeypatch,
):
    """The floor this feature must never fall below: with no copy, the
    page is exactly the pre-8.18 page — it loads the script straight from
    telegram.org rather than pointing at a route that would 503."""
    _fake_fetch(monkeypatch, None)

    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get("/")).text()
        finally:
            await client.close()
        assert f'<script src="{SDK_SRC_TELEGRAM}"></script>' in body
        assert f'src="{SDK_SRC_SELF}"' not in body

    run_async(_run())


def test_the_two_page_variants_differ_only_in_that_one_tag(server, run_async):
    """Nothing else about the shell changes with the source — R5."""
    from aipager.miniapp.static import index_html

    ours = index_html(sdk_from_self=True)
    theirs = index_html(sdk_from_self=False)
    assert ours.replace(f'src="{SDK_SRC_SELF}"', "SRC") == theirs.replace(
        f'src="{SDK_SRC_TELEGRAM}"', "SRC"
    )


# ===== the route ============================================================

def test_sdk_route_is_public_and_served_as_javascript(server, run_async):
    """R1: 200, the JS content type, a day of client caching, and NO
    initData header on the request. Put the route behind the auth gate
    and this fails with a 401."""
    _write_cache(CACHED)

    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/telegram-web-app.js")
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "application/javascript; charset=utf-8"
            assert resp.headers["Cache-Control"] == "public, max-age=86400"
            assert await resp.read() == CACHED
        finally:
            await client.close()

    run_async(_run())


def test_sdk_route_leaves_the_api_auth_gate_alone(server, run_async):
    """R5: adding a public asset route must not loosen anything else —
    /api/status without initData is still a 401."""
    _write_cache(CACHED)

    async def _run():
        client = await _client_for(server)
        try:
            assert (await client.get("/telegram-web-app.js")).status == 200
            assert (await client.get("/api/status")).status == 401
        finally:
            await client.close()

    run_async(_run())


def test_route_503s_only_when_neither_memory_nor_disk_has_a_body(
    server, run_async, monkeypatch,
):
    """No copy anywhere: the route answers 503 (never cached by a phone),
    and the page it came from is already pointing at telegram.org, so the
    user sees the pre-8.18 behaviour rather than a broken script tag."""
    _fake_fetch(monkeypatch, None)

    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/telegram-web-app.js")
            assert resp.status == 503
            assert resp.headers["Cache-Control"] == "no-store"
            assert "javascript" not in resp.headers["Content-Type"]
            page = await (await client.get("/")).text()
        finally:
            await client.close()
        assert SDK_SRC_TELEGRAM in page
        assert "if (!initData) {" in page
        assert "Open this page from the Telegram app to sign in." in page

    run_async(_run())


# ===== startup prefetch =====================================================

def test_prefetch_warms_the_body_in_the_background(run_async, monkeypatch):
    calls = _fake_fetch(monkeypatch, FETCHED)
    sdk = WebAppSdk()

    async def _run():
        sdk.prefetch()
        assert sdk._refresh_task is not None
        await sdk._refresh_task
        return await sdk.get()

    assert run_async(_run()) == FETCHED
    assert calls == [sdk_mod.SDK_URL]
    assert _cache_path().read_bytes() == FETCHED


def test_server_start_prefetches_the_sdk(mk_bot, run_async, monkeypatch):
    """The wiring: delete `self._sdk.prefetch()` from start() and this
    fails. Binds an EPHEMERAL loopback port (0), never the daemon's."""
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(Member(id=555, label="ada", role="developer"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"
    srv = MiniAppServer(bot, registry, port=0)
    calls = _fake_fetch(monkeypatch, FETCHED)

    async def _run():
        await srv.start()
        try:
            task = srv._sdk._refresh_task
            assert task is not None, "start() must schedule the prefetch"
            await task
        finally:
            await srv.stop()

    run_async(_run())
    assert calls == [sdk_mod.SDK_URL]


def test_a_failing_prefetch_leaves_the_page_on_the_absolute_url(
    server, run_async, monkeypatch,
):
    _fake_fetch(monkeypatch, None)

    async def _run():
        server._sdk.prefetch()
        if server._sdk._refresh_task is not None:
            await server._sdk._refresh_task
        client = await _client_for(server)
        try:
            return await (await client.get("/")).text()
        finally:
            await client.close()

    page = run_async(_run())
    assert SDK_SRC_TELEGRAM in page
    assert f'src="{SDK_SRC_SELF}"' not in page


# ===== source priority ======================================================

def test_a_successful_fetch_is_served_and_cached(run_async, monkeypatch, tmp_path):
    calls = _fake_fetch(monkeypatch, FETCHED)
    sdk = WebAppSdk()

    async def _run():
        sdk.prefetch()
        await sdk._refresh_task
        return await sdk.get(), await sdk.get()

    first, second = run_async(_run())
    assert first == second == FETCHED
    assert sdk.source == "fetched"
    assert calls == [sdk_mod.SDK_URL], "later requests must be served from memory"
    cache = _cache_path()
    assert cache.read_bytes() == FETCHED
    assert cache.is_relative_to(tmp_path), "the cache must never leave tmp_path"


def test_fetch_failure_with_a_cache_file_serves_the_cache(run_async, monkeypatch):
    _write_cache(CACHED)
    _fake_fetch(monkeypatch, None)
    sdk = WebAppSdk()

    assert run_async(sdk.get()) == CACHED
    assert sdk.source == "cache"
    assert _cache_path().read_bytes() == CACHED, "a failed fetch must not touch the cache"


def test_fetch_failure_without_a_cache_serves_nothing(run_async, monkeypatch):
    """No copy means None — the signal the page uses to load the script
    from telegram.org instead. aipager ships no copy of its own."""
    _fake_fetch(monkeypatch, None)
    sdk = WebAppSdk()

    assert run_async(sdk.get()) is None
    assert sdk.source == ""
    assert not _cache_path().exists()


def test_get_never_waits_on_a_fetch(run_async, monkeypatch):
    """A page load must never block on telegram.org. With a fetch that
    hangs, get() still returns promptly."""
    gate = threading.Event()

    def _slow(url):
        gate.wait(5)
        return FETCHED

    monkeypatch.setattr(sdk_mod, "_download_sync", _slow)
    sdk = WebAppSdk()

    async def _run():
        started = time.monotonic()
        body = await sdk.get()
        elapsed = time.monotonic() - started
        gate.set()
        if sdk._refresh_task is not None:
            await sdk._refresh_task
        return body, elapsed

    body, elapsed = run_async(_run())
    assert body is None
    assert elapsed < 1.0, "get() waited on the download"


def test_a_fetch_that_raises_unexpectedly_never_escapes(run_async, monkeypatch):
    def _boom(url):
        raise RuntimeError("simulated: unexpected failure inside the fetch")

    monkeypatch.setattr(sdk_mod, "_download_sync", _boom)
    sdk = WebAppSdk()

    async def _run():
        body = await sdk.get()
        await sdk._refresh_task
        return body, await sdk.get()

    first, second = run_async(_run())
    assert first is None and second is None


# ===== the validator ========================================================

@pytest.mark.parametrize("body,expected", [
    (b"", False),
    (b"   \n\t ", False),
    (HTML_PAGE, False),
    (b"<html>" + b"<p>blocked</p>" * 4000, False),
    (b"\n  <html" + b"x" * MIN, False),
    (b"\xef\xbb\xbf<html>" + b"x" * MIN, False),
    (b"x" * (sdk_mod.MAX_BYTES + 1), False),
    (ERROR_TEXT, False),
    (TRUNCATED, False),
    (b"// " + b"x" * (MIN - 4), False),
    (b"// " + b"x" * (MIN - 3), True),
    (_js_body(b"// ordinary"), True),
    (b"/* banner */ var a = 1;\n" + b"x" * MIN, True),
    (b"\xef\xbb\xbf// bom then comment\n" + b"x" * MIN, True),
    (b"window.Telegram = {};\n" + b"x" * MIN, True),
    (b"_private = 1;\n" + b"x" * MIN, True),
    (b"$ = 1;\n" + b"x" * MIN, True),
    (b"(function () {})();\n" + b"x" * MIN, True),
    (b"!function(){}();\n" + b"x" * MIN, True),
    (b'"use strict";\n' + b"x" * MIN, True),
    (b"x" * sdk_mod.MAX_BYTES, True),
], ids=[
    "empty", "whitespace", "doctype-html", "html", "leading-ws-html", "bom-html",
    "over-2mb", "plain-text-error", "truncated-sdk-prefix", "one-under-the-floor",
    "exactly-the-floor", "sdk-shaped", "block-comment", "bom-comment", "identifier",
    "underscore", "dollar", "iife", "bang-iife", "use-strict", "exactly-2mb",
])
def test_looks_like_javascript(body, expected):
    """R4 plus the size floor. The shape guards and the size guards are
    independent: drop the `<` check AND the allowlist and the html cases
    fail; drop the floor and `plain-text-error` / `truncated-sdk-prefix`
    fail."""
    assert sdk_mod.looks_like_javascript(body) is expected


def test_a_fetched_html_error_page_is_never_served_or_cached(run_async, monkeypatch):
    """telegram.org (or something in front of it) answers 200 with an HTML
    page. It must not reach a browser under our JS content type and must
    not poison the cache."""
    _fake_fetch(monkeypatch, HTML_PAGE)
    sdk = WebAppSdk()

    async def _run():
        body = await sdk.get()
        await sdk._refresh_task
        return body, await sdk.get()

    first, second = run_async(_run())
    assert first is None and second is None
    assert not _cache_path().exists()


def test_an_invalid_cache_file_is_ignored_not_served(run_async, monkeypatch):
    _write_cache(HTML_PAGE)
    _fake_fetch(monkeypatch, None)
    sdk = WebAppSdk()

    assert run_async(sdk.get()) is None


# ===== the download's own checks ============================================

def test_a_short_read_against_a_declared_length_is_discarded(monkeypatch):
    """The bug this guard exists for: read(amt) returns a truncated body
    WITHOUT raising. Drop the length comparison and this passes the
    truncated bytes on."""
    _fake_urlopen(monkeypatch, _FakeResponse(TRUNCATED, declared=116510))
    assert REAL_DOWNLOAD(sdk_mod.SDK_URL) is None


def test_a_full_length_body_is_accepted(monkeypatch):
    _fake_urlopen(monkeypatch, _FakeResponse(FETCHED, declared=len(FETCHED)))
    assert REAL_DOWNLOAD(sdk_mod.SDK_URL) == FETCHED


def test_an_oversized_body_is_not_mistaken_for_a_short_read(monkeypatch):
    """A body bigger than the cap is read short ON PURPOSE, so the length
    comparison must not fire — the size check rejects it, and says why."""
    huge = b"// WebView\n" + b"x" * (sdk_mod.MAX_BYTES + 100)
    _fake_urlopen(monkeypatch, _FakeResponse(huge, declared=len(huge)))
    got = REAL_DOWNLOAD(sdk_mod.SDK_URL)
    assert got is not None and len(got) == sdk_mod.MAX_BYTES + 1
    assert sdk_mod.looks_like_javascript(got) is False


def test_a_body_with_no_declared_length_is_returned_and_judged_on_size(monkeypatch):
    """Nothing to compare against (chunked, or HTTP/1.0 close-delimited):
    the floor in looks_like_javascript is the backstop."""
    _fake_urlopen(monkeypatch, _FakeResponse(TRUNCATED, declared=None))
    got = REAL_DOWNLOAD(sdk_mod.SDK_URL)
    assert got == TRUNCATED
    assert sdk_mod.looks_like_javascript(got) is False


@pytest.mark.parametrize("declared", ["abc", "", "  ", "116510, 116510", "-5"])
def test_an_unparseable_content_length_is_ignored_not_fatal(monkeypatch, declared):
    """The ``.isdigit()`` guard: a Content-Length we cannot parse must
    make the length comparison stand down, NOT raise. Delete `.isdigit()`
    and ``int()`` raises ValueError, which is not in the except tuple —
    breaking the never-raises promise. The body is then judged on size
    alone, which is what MIN_BYTES is there for."""
    _fake_urlopen(monkeypatch, _FakeResponse(FETCHED, declared=declared))
    assert REAL_DOWNLOAD(sdk_mod.SDK_URL) == FETCHED


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("dns failure"),
    urllib.error.HTTPError(sdk_mod.SDK_URL, 503, "unavailable", {}, None),
    OSError("connection reset"),
    TimeoutError("timed out"),
    http.client.IncompleteRead(b"// WebView\n"),
])
def test_download_never_raises(monkeypatch, exc):
    """The REAL download function (captured at import, before the conftest
    guard swaps it out) turns every failure into ``None`` — including the
    IncompleteRead a truncated chunked body raises."""
    def _raise(url, timeout=None):
        raise exc

    monkeypatch.setattr(sdk_mod.urllib.request, "urlopen", _raise)
    assert REAL_DOWNLOAD(sdk_mod.SDK_URL) is None


def test_a_short_read_leaves_a_good_cache_file_untouched(run_async, monkeypatch, caplog):
    """End to end, and the property that matters most: a partial body
    never reaches the cache, so the copy that was working keeps working.
    One WARNING says what happened."""
    _write_cache(CACHED, mtime=time.time() - DAY - 1)
    _fake_urlopen(monkeypatch, _FakeResponse(TRUNCATED, declared=116510))
    monkeypatch.setattr(sdk_mod, "_download_sync", REAL_DOWNLOAD)
    sdk = WebAppSdk()

    with caplog.at_level(logging.WARNING, logger="aipager.miniapp.webapp_sdk"):
        async def _run():
            first = await sdk.get()
            await sdk._refresh_task
            return first, await sdk.get()

        first, second = run_async(_run())

    assert first == CACHED and second == CACHED
    assert sdk.source == "cache"
    assert _cache_path().read_bytes() == CACHED
    warnings = [r for r in caplog.records if "short read" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]


def test_a_body_under_the_floor_leaves_a_good_cache_file_untouched(
    run_async, monkeypatch,
):
    """Same, for the case with nothing to compare against: no
    Content-Length, a body under the floor."""
    _write_cache(CACHED, mtime=time.time() - DAY - 1)
    _fake_urlopen(monkeypatch, _FakeResponse(ERROR_TEXT, declared=None))
    monkeypatch.setattr(sdk_mod, "_download_sync", REAL_DOWNLOAD)
    sdk = WebAppSdk()

    async def _run():
        first = await sdk.get()
        await sdk._refresh_task
        return first, await sdk.get()

    first, second = run_async(_run())
    assert first == CACHED and second == CACHED
    assert _cache_path().read_bytes() == CACHED


def test_a_full_body_does_replace_the_cache(run_async, monkeypatch):
    """The other half of the same guard: a complete body IS adopted."""
    _write_cache(CACHED, mtime=time.time() - DAY - 1)
    _fake_urlopen(monkeypatch, _FakeResponse(FETCHED, declared=len(FETCHED)))
    monkeypatch.setattr(sdk_mod, "_download_sync", REAL_DOWNLOAD)
    sdk = WebAppSdk()

    async def _run():
        await sdk.get()
        await sdk._refresh_task
        return await sdk.get()

    assert run_async(_run()) == FETCHED
    assert _cache_path().read_bytes() == FETCHED


# ===== background refresh ===================================================

async def _settle(sdk: WebAppSdk) -> None:
    task = sdk._refresh_task
    if task is not None and not task.done():
        await task


def test_background_refresh_runs_at_most_once_per_day(run_async, monkeypatch):
    """A cache older than a day is served immediately and refreshed in the
    background; the next refresh is not before a day has passed, however
    many requests arrive in between. Make _refresh_due() always true and
    this fails."""
    clock = _Clock()
    _write_cache(CACHED, mtime=clock.now - DAY - 1)
    calls = _fake_fetch(monkeypatch, FETCHED)
    sdk = WebAppSdk(clock=clock)

    async def _run():
        served_first = await sdk.get()
        assert sdk._refresh_task is not None
        await _settle(sdk)
        assert len(calls) == 1
        served_after = await sdk.get()
        await _settle(sdk)
        assert len(calls) == 1
        clock.now += DAY - 1
        await sdk.get()
        await _settle(sdk)
        assert len(calls) == 1
        clock.now += 1
        await sdk.get()
        await _settle(sdk)
        assert len(calls) == 2
        await sdk.get()
        await _settle(sdk)
        assert len(calls) == 2
        return served_first, served_after

    served_first, served_after = run_async(_run())
    assert served_first == CACHED
    assert served_after == FETCHED
    assert _cache_path().read_bytes() == FETCHED


def test_a_fresh_cache_is_not_refreshed_at_all(run_async, monkeypatch):
    clock = _Clock()
    _write_cache(CACHED, mtime=clock.now - 3600)
    calls = _fake_fetch(monkeypatch, FETCHED)
    sdk = WebAppSdk(clock=clock)

    async def _run():
        await sdk.get()
        await sdk.get()

    run_async(_run())
    assert calls == []
    assert sdk._refresh_task is None


def test_a_refresh_failure_keeps_serving_the_old_body(run_async, monkeypatch):
    clock = _Clock()
    _write_cache(CACHED, mtime=clock.now - DAY - 1)
    calls = _fake_fetch(monkeypatch, None)
    sdk = WebAppSdk(clock=clock)

    async def _run():
        first = await sdk.get()
        await sdk._refresh_task
        return first, await sdk.get()

    first, second = run_async(_run())
    assert first == CACHED and second == CACHED
    assert sdk.source == "cache"
    assert len(calls) == 1
    assert _cache_path().read_bytes() == CACHED


def test_a_refresh_rejecting_html_keeps_serving_the_old_body(run_async, monkeypatch):
    clock = _Clock()
    _write_cache(CACHED, mtime=clock.now - DAY - 1)
    _fake_fetch(monkeypatch, HTML_PAGE)
    sdk = WebAppSdk(clock=clock)

    async def _run():
        await sdk.get()
        await sdk._refresh_task
        return await sdk.get()

    assert run_async(_run()) == CACHED
    assert _cache_path().read_bytes() == CACHED


def test_close_cancels_an_in_flight_refresh(run_async, monkeypatch):
    clock = _Clock()
    _write_cache(CACHED, mtime=clock.now - DAY - 1)
    gate = threading.Event()

    def _slow(url):
        gate.wait(5)
        return FETCHED

    monkeypatch.setattr(sdk_mod, "_download_sync", _slow)
    sdk = WebAppSdk(clock=clock)

    async def _run():
        await sdk.get()
        task = sdk._refresh_task
        assert task is not None and not task.done()
        sdk.close()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sdk._refresh_task is None
        assert await sdk.get() == CACHED

    run_async(_run())


def test_server_stop_cancels_an_in_flight_refresh(server, run_async, monkeypatch):
    """The WIRING, not just the method: delete `self._sdk.close()` from
    MiniAppServer.stop() and this fails."""
    _write_cache(CACHED, mtime=time.time() - DAY - 1)
    gate = threading.Event()

    def _slow(url):
        gate.wait(5)
        return FETCHED

    monkeypatch.setattr(sdk_mod, "_download_sync", _slow)

    async def _run():
        assert await server._sdk.get() == CACHED
        task = server._sdk._refresh_task
        assert task is not None and not task.done()
        await server.stop()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(_run())


# ===== isolation and packaging =============================================

def test_the_package_ships_no_copy_of_telegrams_script():
    """aipager must not redistribute Telegram's SDK: no copy of it may
    live in the package, in any form."""
    pkg = Path(sdk_mod.__file__).resolve().parent.parent
    strays = [p for p in pkg.rglob("*.js") if "__pycache__" not in p.parts]
    assert strays == [], f"third-party script(s) inside the package: {strays}"


def test_the_suite_never_holds_the_real_download_function():
    """The conftest guard, asserted rather than assumed: remove its
    ``_download_sync`` patch and this fails. (This file restores the real
    function only through ``REAL_DOWNLOAD``, never onto the module.)"""
    assert sdk_mod._download_sync is not REAL_DOWNLOAD


def test_cache_dir_stays_under_tmp_path(tmp_path):
    assert sdk_mod.cache_dir().is_relative_to(tmp_path)
    assert (sdk_mod.cache_dir().stat().st_mode & 0o777) == 0o700


def test_cache_dir_default_is_under_the_aipager_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("AIPAGER_WEBAPP_SDK_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sdk_mod.cache_dir() == tmp_path / ".local" / "share" / "aipager" / "webapp-sdk"


def test_module_is_stdlib_only():
    """Same discipline as cloudflared_fetch: the daemon's cold paths must
    not pay for aiohttp just to reach this module."""
    src = Path(sdk_mod.__file__).read_text(encoding="utf-8")
    assert "import aiohttp" not in src
    assert "from aiohttp" not in src
