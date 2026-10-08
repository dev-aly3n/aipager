"""Roadmap 8.117: the Mini App page goes out gzipped when the client takes it.

The page is one ~216 KB response (its script and styles inline). A client
that takes gzip (every Telegram in-app browser) now gets it compressed,
about a quarter of the bytes; any other client gets the exact same page,
uncompressed, as before. Each variant is built and compressed once.
"""

from __future__ import annotations

import gzip

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer, accepts_gzip
from aipager.miniapp.static import index_html
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                  members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"
    srv = MiniAppServer(bot, registry, port=8765)
    srv._sdk.get = _sdk_returns(b"// the SDK")
    return srv


def _sdk_returns(body):
    async def _get():
        return body
    return _get


async def _fetch(srv, headers: dict | None = None, *, skip_encoding=False):
    client = TestClient(TestServer(srv._build_app()), auto_decompress=False)
    await client.start_server()
    try:
        resp = await client.get(
            "/", headers=headers or {},
            skip_auto_headers=["Accept-Encoding"] if skip_encoding else None)
        return resp.status, dict(resp.headers), await resp.read()
    finally:
        await client.close()


PAGE = index_html(sdk_from_self=True).encode("utf-8")


@pytest.mark.parametrize("accept", ["gzip", "gzip, deflate, br", "br;q=1, gzip;q=0.5",
                                    "*", "x-gzip"])
def test_a_client_that_takes_gzip_gets_the_page_compressed(server, run_async, accept):
    status, headers, body = run_async(_fetch(server, {"Accept-Encoding": accept}))
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Vary"] == "Accept-Encoding"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert gzip.decompress(body) == PAGE
    # Exactly what tests/test_miniapp_page_rules.py pins the size of.
    assert body == gzip.compress(PAGE, compresslevel=9, mtime=0)


@pytest.mark.parametrize("accept", ["identity", "gzip;q=0", "deflate, br", "*;q=0",
                                    "gzip;q=zero", None])
def test_any_other_client_gets_the_same_page_as_before(server, run_async, accept):
    status, headers, body = run_async(_fetch(
        server, {"Accept-Encoding": accept} if accept else None,
        skip_encoding=accept is None))
    assert status == 200
    assert "Content-Encoding" not in headers
    assert headers["Vary"] == "Accept-Encoding"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body == PAGE


def test_each_variant_is_compressed_for_its_own_sdk_source(server, run_async):
    gz = {"Accept-Encoding": "gzip"}
    _s, _h, ours = run_async(_fetch(server, gz))
    server._sdk.get = _sdk_returns(None)                 # no copy of the SDK any more
    _s, _h, theirs = run_async(_fetch(server, gz))
    assert gzip.decompress(ours) == PAGE
    assert gzip.decompress(theirs) == index_html(sdk_from_self=False).encode("utf-8")


def test_the_page_is_built_once_per_variant(server, run_async, monkeypatch):
    built = []
    real = index_html

    def _counting(*, sdk_from_self=True):
        built.append(sdk_from_self)
        return real(sdk_from_self=sdk_from_self)

    monkeypatch.setattr("aipager.miniapp.static.index_html", _counting)
    for accept in ("gzip", "identity", "gzip"):
        run_async(_fetch(server, {"Accept-Encoding": accept}))
    assert built == [True]


@pytest.mark.parametrize("header, takes", [
    ("", False), ("gzip", True), ("GZIP", True), (" gzip ; q=0.001", True),
    ("gzip;q=0.0", False), ("*;q=0.5", True), ("identity, *;q=0", False),
    ("*;q=1, gzip;q=0", False), ("deflate", False), ("gzip;q=", False),
])
def test_the_accept_encoding_header_is_read_as_written(header, takes):
    assert accepts_gzip(header) is takes
