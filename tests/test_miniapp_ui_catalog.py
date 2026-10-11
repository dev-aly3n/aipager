"""The page's text catalog (multilingual phase 4): ``GET /api/i18n/<code>.json``
and the ``ui`` entry that tells the page its viewer reads another
language.

The page is one cached body for everyone, English inline. A viewer whose
private chat chose another language learns it from ``ui`` on the chat
list, Settings and the App language answer (absent for English, whose
bodies are unchanged), then fetches that language's strings, behind the
same gate as every other route.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager.i18n import core, locales
from aipager.miniapp import ui_text
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP, MEMBER, OUTSIDER = -100, 777, 999


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id, language_code=None):
    user = {"id": user_id, "first_name": "Test"}
    if language_code:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, **extra):
    return {"X-Telegram-Init-Data": _init_data(user_id), **extra}


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=GROUP, kind="group", label="team",
                  members=(Member(id=MEMBER, label="bob", role="user"),))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock()
    return MiniAppServer(bot, registry, port=8765)


def _call(srv, method, path, headers, body=None):
    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            kwargs = {"headers": headers, "auto_decompress": False}
            if body is not None:
                kwargs["data"] = json.dumps(body)
            resp = await client.request(method, path, **kwargs)
            return resp.status, dict(resp.headers), await resp.read()
        finally:
            await client.close()
    return asyncio.new_event_loop().run_until_complete(go())


def _json(srv, method, path, user=MEMBER, body=None):
    status, _h, raw = _call(srv, method, path, _hdr(user, **{"Accept-Encoding": "identity"}),
                            body)
    return status, json.loads(raw)


# ---- the route --------------------------------------------------------------------------

def test_the_catalog_needs_init_data(server):
    status, _h, raw = _call(server, "GET", "/api/i18n/fa.json", {})
    assert (status, json.loads(raw)) == (401, {"error": "unauthorized"})


def test_a_stranger_gets_the_fixed_forbidden_body(server):
    assert _json(server, "GET", "/api/i18n/fa.json", OUTSIDER) == (403, {"error": "forbidden"})


@pytest.mark.parametrize("name", ["xx.json", "fa", "fa.js", "ja.json", "pl.json", "qps.json",
                                  "fa.json.json", "FA.json", "fa-.json"])
def test_a_disabled_or_unknown_code_is_not_found(server, name):
    assert _json(server, "GET", "/api/i18n/" + name) == (404, {"error": "not_found"})


def test_a_test_enabled_pseudo_code_is_served_marked(server, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps", "qps-rtl"}))
    status, body = _json(server, "GET", "/api/i18n/qps.json")
    assert status == 200 and set(body["strings"]) == {m.key for m in ui_text.PAGE_STRINGS}
    for key, value in body["strings"].items():
        for form in (value.values() if isinstance(value, dict) else [value]):
            assert form.startswith(core.PSEUDO_OPEN) and form.endswith(core.PSEUDO_CLOSE), key
    status, body = _json(server, "GET", "/api/i18n/qps-rtl.json")
    assert status == 200 and body["rtl"] is True


@pytest.mark.parametrize("code", ["en", "fa", "ru", "es", "pt-BR", "fr", "it"])
def test_every_enabled_code_is_served(server, code):
    status, body = _json(server, "GET", f"/api/i18n/{code}.json")
    assert status == 200
    assert body == ui_text.catalog(code)
    assert set(body) == {"code", "rtl", "digits", "decimal", "group", "percent", "list",
                         "strings"}
    assert set(body["strings"]) <= {m.key for m in ui_text.PAGE_STRINGS}
    if code == "en":
        assert body["strings"] == {}


def test_the_persian_catalog_reads_right_to_left_in_persian_digits():
    body = ui_text.catalog("fa")
    assert body["rtl"] is True
    assert body["digits"] == "۰۱۲۳۴۵۶۷۸۹"
    assert (body["decimal"], body["group"], body["percent"], body["list"]) == ("٫", "٬", "٪", "، ")
    # fa and ru are complete: every page string is there.
    assert set(body["strings"]) == {m.key for m in ui_text.PAGE_STRINGS}
    ru = ui_text.catalog("ru")
    assert (ru["rtl"], ru["digits"], ru["list"]) == (False, "0123456789", ", ")
    assert set(ru["strings"]) == {m.key for m in ui_text.PAGE_STRINGS}
    # Phase 5 run 1: es, pt-BR, fr and it are complete too, left to right.
    for code in ("es", "pt-BR", "fr", "it"):
        body = ui_text.catalog(code)
        assert (body["rtl"], body["digits"], body["list"]) == (False, "0123456789", ", ")
        assert set(body["strings"]) == {m.key for m in ui_text.PAGE_STRINGS}, code


def test_a_missing_key_is_left_out(monkeypatch):
    """A translation the catalog lacks is absent, so the page shows its
    English, never a key and never the English sent as if translated."""
    cat = dict(core._catalog("fa"))
    gone = ui_text.PAGE_STRINGS[0].key
    del cat[gone]
    monkeypatch.setitem(core._CATALOGS, "fa", cat)
    strings = ui_text.catalog("fa")["strings"]
    assert gone not in strings
    assert set(strings) == {m.key for m in ui_text.PAGE_STRINGS} - {gone}
    assert all(strings[k] == cat[k] for k in strings)


def test_a_plural_carries_the_languages_forms(monkeypatch):
    from aipager.i18n import Plural
    plural = Plural("ui.test.things", one="{n} thing", other="{n} things")
    monkeypatch.setattr(ui_text, "PAGE_STRINGS", ui_text.PAGE_STRINGS + (plural,))
    monkeypatch.setitem(core._CATALOGS, "ru", {
        **core._catalog("ru"),
        "ui.test.things": {"one": "{n} штука", "few": "{n} штуки", "many": "{n} штук",
                           "other": "{n} штуки"}})
    assert ui_text.catalog("ru")["strings"]["ui.test.things"] == {
        "one": "{n} штука", "few": "{n} штуки", "many": "{n} штук", "other": "{n} штуки"}
    assert ui_text.english_catalog()["ui.test.things"] == {"one": "{n} thing",
                                                          "other": "{n} things"}


def test_the_version_is_stable_and_names_the_bytes():
    assert ui_text.catalog_version("fa") == ui_text.catalog_version("fa")
    assert len(ui_text.catalog_version("fa")) == 16
    assert ui_text.catalog_version("fa") != ui_text.catalog_version("ru")
    assert ui_text.catalog_version("ja") is None


def test_the_current_version_is_cached_for_a_year(server):
    v = ui_text.catalog_version("fa")
    status, headers, _raw = _call(server, "GET", f"/api/i18n/fa.json?v={v}", _hdr(MEMBER))
    assert status == 200
    assert headers["Cache-Control"] == "private, max-age=31536000, immutable"


@pytest.mark.parametrize("query", ["", "?v=0000000000000000", "?v="])
def test_a_stale_version_is_not_cached(server, query):
    status, headers, _raw = _call(server, "GET", "/api/i18n/fa.json" + query, _hdr(MEMBER))
    assert status == 200 and headers["Cache-Control"] == "no-cache"


def test_gzip_when_the_client_takes_it(server):
    status, headers, raw = _call(server, "GET", "/api/i18n/fa.json",
                                 _hdr(MEMBER, **{"Accept-Encoding": "gzip"}))
    assert status == 200
    assert headers["Content-Encoding"] == "gzip" and headers["Vary"] == "Accept-Encoding"
    assert json.loads(gzip.decompress(raw)) == ui_text.catalog("fa")
    status, headers, raw = _call(server, "GET", "/api/i18n/fa.json",
                                 _hdr(MEMBER, **{"Accept-Encoding": "identity"}))
    assert "Content-Encoding" not in headers and headers["Vary"] == "Accept-Encoding"
    assert json.loads(raw) == ui_text.catalog("fa")


# ---- the ui entry ----------------------------------------------------------------------

def _ui(code):
    return {"code": code, "rtl": code == "fa", "v": ui_text.catalog_version(code)}


def test_english_bodies_carry_no_ui(server):
    for method, path, body in (("GET", "/api/chats", None), ("GET", "/api/preferences", None),
                               ("PUT", "/api/app-language", {"value": "en"})):
        status, data = _json(server, method, path, body=body)
        assert status == 200 and "ui" not in data, path
    status, data = _json(server, "PUT", "/api/app-language", body={"value": "auto"})
    assert status == 200 and "ui" not in data


def test_a_persian_viewer_gets_ui_on_chats_preferences_and_put(server):
    preferences.set_ui_language(MEMBER, "fa")
    assert _json(server, "GET", "/api/chats")[1]["ui"] == _ui("fa")
    assert _json(server, "GET", "/api/preferences")[1]["ui"] == _ui("fa")
    status, data = _json(server, "PUT", "/api/app-language", body={"value": "ru"})
    assert status == 200 and data["ui"] == _ui("ru")
    assert _json(server, "GET", "/api/chats")[1]["ui"] == _ui("ru")
    status, data = _json(server, "PUT", "/api/app-language", body={"value": "en"})
    assert status == 200 and "ui" not in data
    assert "ui" not in _json(server, "GET", "/api/chats")[1]


def test_the_viewers_language_not_the_chats(server):
    """The group is Persian; the viewer chose nothing: English, no ui."""
    preferences.set_ui_language(GROUP, "fa")
    assert "ui" not in _json(server, "GET", "/api/chats")[1]
    preferences.set_ui_language(MEMBER, "ru")
    assert _json(server, "GET", "/api/preferences")[1]["ui"] == _ui("ru")
