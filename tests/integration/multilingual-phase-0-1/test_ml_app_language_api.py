"""The Mini App's App language (entrypoints.md HTTP routes:
``GET /api/preferences`` ``app_language`` and ``PUT /api/app-language``).

Partitions: viewer = group-only member, owner (DM + group), outsider, no
header; body = valid ``en`` / ``auto``, malformed JSON, not an object,
missing ``value``, non-string, disabled code, pseudo code, unknown code.
Error guessing: writes landing in the scope's entry, a chat mirror, an
admin gate that should not exist, unbounded writes.
"""

from __future__ import annotations

import json

import pytest

from aipager import preferences as prefs

DM = 555
GROUP = -1001
OWNER = 555
BOB = 2          # member of the group scope only, not an admin
OUTSIDER = 999999


@pytest.fixture
def http(mini_server, run_async, ml):
    """``http(method, path, user_id=..., **kw) -> (status, body)``."""
    from aiohttp.test_utils import TestClient, TestServer

    def _call(method, path, *, user_id=BOB, headers=None, language_code=None, **kw):
        if headers is None:
            headers = ml.hdr(user_id, language_code=language_code)

        async def _go():
            client = TestClient(TestServer(mini_server._build_app()))
            await client.start_server()
            try:
                resp = await client.request(method, path, headers=headers, **kw)
                text = await resp.text()
                try:
                    body = json.loads(text)
                except ValueError:
                    body = text
                return resp.status, body
            finally:
                await client.close()
        return run_async(_go())
    return _call


@pytest.fixture
def burst(mini_server, run_async, ml):
    """Many PUTs over one client; returns the statuses."""
    from aiohttp.test_utils import TestClient, TestServer

    def _burst(n, value="en", user_id=BOB):
        async def _go():
            client = TestClient(TestServer(mini_server._build_app()))
            await client.start_server()
            out = []
            try:
                for _ in range(n):
                    resp = await client.put("/api/app-language", headers=ml.hdr(user_id),
                                             json={"value": value})
                    out.append((resp.status, await resp.text()))
            finally:
                await client.close()
            return out
        return run_async(_go())
    return _burst


# ---- GET /api/preferences --------------------------------------------------------

def test_preferences_carry_the_app_language(http):
    status, body = http("GET", "/api/preferences")
    assert status == 200
    assert body["app_language"] == {
        "title": "App language",
        "hint": "Only aipager's own text. Claude's answers don't change.",
        "value": "auto",
        "effective": "en",
        "options": [
            {"value": "auto", "label": "Automatic (your Telegram language)"},
            {"value": "en", "label": "English"},
        ],
    }


def test_preferences_show_the_viewers_stored_choice(http):
    prefs.set_ui_language(BOB, "en")
    _status, body = http("GET", "/api/preferences")
    assert body["app_language"]["value"] == "en"


def test_a_scope_language_is_not_the_viewers_choice(http):
    prefs.set_ui_language(GROUP, "en")
    _status, body = http("GET", "/api/preferences")
    assert body["app_language"]["value"] == "auto"


@pytest.mark.parametrize("code", ["fa", "de", "zz", None])
def test_an_unoffered_telegram_language_still_shows_english(http, code):
    _status, body = http("GET", "/api/preferences", language_code=code)
    assert body["app_language"]["effective"] == "en"


def test_ui_language_is_not_a_chat_preference_field(http):
    _status, body = http("GET", "/api/preferences")
    assert "ui_language" not in {g["field"] for g in body["schema"]}


def test_get_preferences_writes_nothing(http, ml):
    before = ml.prefs_bytes()
    http("GET", "/api/preferences", language_code="de")
    assert ml.prefs_bytes() == before


# ---- PUT /api/app-language: success ----------------------------------------------

def test_put_english_answers_with_value_effective_and_confirmation(http):
    status, body = http("PUT", "/api/app-language", json={"value": "en"})
    assert (status, body) == (200, {
        "value": "en", "effective": "en",
        "confirmation": "✅ aipager now speaks English here. Your sessions and "
                        "Claude's answers are unchanged."})


def test_put_writes_the_callers_own_entry(http):
    http("PUT", "/api/app-language", json={"value": "en"})
    assert prefs.get_ui_language(BOB) == "en"


def test_put_never_writes_the_scope_entry(http):
    http("PUT", "/api/app-language", json={"value": "en"})
    assert prefs.get_ui_language(GROUP) == ""


def test_put_is_not_admin_gated(http):
    status, _body = http("PUT", "/api/app-language", user_id=BOB, json={"value": "en"})
    assert status == 200


def test_put_never_posts_to_a_chat(http, tg_bot):
    http("PUT", "/api/app-language", json={"value": "en"})
    assert tg_bot._app.bot.send_message.await_count == 0


def test_put_reregisters_no_menu(http, tg_bot):
    http("PUT", "/api/app-language", json={"value": "en"})
    assert tg_bot._app.bot.set_my_commands.await_count == 0


def test_put_auto_clears_the_choice(http):
    prefs.set_ui_language(BOB, "en")
    status, body = http("PUT", "/api/app-language", json={"value": "auto"})
    assert (status, body["value"], body["effective"]) == (200, "auto", "en")


def test_put_auto_removes_the_stored_choice(http):
    prefs.set_ui_language(BOB, "en")
    http("PUT", "/api/app-language", json={"value": "auto"})
    assert prefs.get_ui_language(BOB) == ""


def test_put_auto_with_nothing_stored_writes_nothing(http, ml):
    before = ml.prefs_bytes()
    http("PUT", "/api/app-language", json={"value": "auto"})
    assert ml.prefs_bytes() == before


def test_put_the_same_value_twice_writes_once(http, ml):
    http("PUT", "/api/app-language", json={"value": "en"})
    before = ml.prefs_bytes()
    http("PUT", "/api/app-language", json={"value": "en"})
    assert ml.prefs_bytes() == before


def test_put_the_same_value_twice_still_confirms(http):
    http("PUT", "/api/app-language", json={"value": "en"})
    _status, body = http("PUT", "/api/app-language", json={"value": "en"})
    assert body["confirmation"].startswith("✅ aipager now speaks English here.")


def test_the_choice_reads_back_through_get(http):
    http("PUT", "/api/app-language", json={"value": "en"})
    _status, body = http("GET", "/api/preferences")
    assert body["app_language"]["value"] == "en"


# ---- PUT /api/app-language: errors ------------------------------------------------

@pytest.mark.parametrize("value", ["fa", "ar", "es", "qps", "qps-rtl", "xx", "",
                                   "EN", "english", "en ", 5, None, True, ["en"],
                                   {"v": "en"}])
def test_put_refuses_a_bad_value_with_400(http, value):
    status, body = http("PUT", "/api/app-language", json={"value": value})
    assert (status, body) == (400, {"error": "bad_request"})


@pytest.mark.parametrize("value", ["fa", "qps", "xx", 5])
def test_a_refused_value_writes_nothing(http, ml, value):
    before = ml.prefs_bytes()
    http("PUT", "/api/app-language", json={"value": value})
    assert ml.prefs_bytes() == before


def test_put_refuses_a_missing_value(http):
    status, body = http("PUT", "/api/app-language", json={"lang": "en"})
    assert (status, body) == (400, {"error": "bad_request"})


@pytest.mark.parametrize("raw", [b"{not json", b"", b"\"en\"", b"[\"en\"]", b"null",
                                 b"\xff\xfe"])
def test_put_refuses_a_body_that_is_not_a_json_object(http, raw):
    status, body = http("PUT", "/api/app-language", data=raw,
                        headers=None)
    assert (status, body) == (400, {"error": "bad_request"})


def test_put_without_init_data_is_401(http):
    status, _body = http("PUT", "/api/app-language", headers={}, json={"value": "en"})
    assert status == 401


def test_put_with_a_forged_signature_is_401(http, ml):
    forged = {"X-Telegram-Init-Data": ml.init_data(BOB, token="000000:forged-token-abc")}
    status, _body = http("PUT", "/api/app-language", headers=forged, json={"value": "en"})
    assert status == 401


def test_put_by_a_non_member_is_403(http):
    status, _body = http("PUT", "/api/app-language", user_id=OUTSIDER,
                         json={"value": "en"})
    assert status == 403


def test_a_non_member_writes_nothing(http, ml):
    before = ml.prefs_bytes()
    http("PUT", "/api/app-language", user_id=OUTSIDER, json={"value": "en"})
    assert ml.prefs_bytes() == before


def test_get_on_the_write_route_is_not_allowed(http):
    status, _body = http("GET", "/api/app-language")
    assert status in (404, 405)


def test_the_write_route_is_rate_limited(burst):
    statuses = [s for s, _t in burst(60)]
    assert 429 in statuses


def test_a_rate_limited_write_says_too_many_requests(burst):
    limited = [t for s, t in burst(60) if s == 429]
    assert limited and json.loads(limited[0]) == {"error": "too_many_requests"}
