"""Black-box: "Automatic" is its own stored value (design.md section 4, D7, D10;
entrypoints.md ``preferences.set_ui_language``, ``language_menu.current_choice``,
callback ``_:set:lang:auto``, ``PUT /api/app-language``).

Partitions: chat = private / group; value = "auto" / a code / ""; person =
existing / new; Telegram language = en / fa / none.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import language_menu
from aipager.i18n import persist
from tests.i18n_ml2_bb import BOT_TOKEN, callback, init_header

OWNER, NEWBIE, GROUP = 555, 4321, -1001


def _prefs_doc():
    from pathlib import Path
    return json.loads(Path(prefs._PREFERENCES_PATH).read_text(encoding="utf-8"))


# ---- preferences ---------------------------------------------------------------------

def test_the_automatic_constant():
    assert prefs.UI_LANGUAGE_AUTO == "auto"


def test_automatic_is_stored_for_a_private_chat():
    prefs.set_ui_language(OWNER, "auto")
    assert prefs.get_ui_language(OWNER) == "auto"


def test_automatic_is_written_to_preferences_json():
    prefs.set_ui_language(OWNER, "auto")
    assert _prefs_doc()[str(OWNER)]["ui_language"] == "auto"


def test_automatic_survives_a_restart(p5):
    prefs.set_ui_language(OWNER, "auto")
    p5.restart()
    assert prefs.get_ui_language(OWNER) == "auto"


@pytest.mark.parametrize("chat", [GROUP, -100123456789])
def test_automatic_is_refused_for_a_group(chat):
    with pytest.raises(ValueError):
        prefs.set_ui_language(chat, "auto")


def test_a_refused_group_automatic_stores_nothing():
    prefs.set_ui_language(GROUP, "de")
    with pytest.raises(ValueError):
        prefs.set_ui_language(GROUP, "auto")
    assert prefs.get_ui_language(GROUP) == "de"


def test_a_code_replaces_automatic():
    prefs.set_ui_language(OWNER, "auto")
    prefs.set_ui_language(OWNER, "ru")
    assert prefs.get_ui_language(OWNER) == "ru"


# ---- the callback ----------------------------------------------------------------------

def _tap(bot, run_async, data, chat, user, language_code="en"):
    update, query = callback(data, chat=chat, user=user, language_code=language_code)
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def test_the_automatic_button_stores_automatic(bot, run_async):
    _tap(bot, run_async, "_:set:lang:auto", OWNER, OWNER)
    assert prefs.get_ui_language(OWNER) == "auto"


def test_the_automatic_button_is_refused_in_a_group(bot, run_async):
    _tap(bot, run_async, "_:set:lang:auto", GROUP, OWNER)
    assert prefs.get_ui_language(GROUP) != "auto"


def test_the_automatic_button_in_a_group_says_invalid_value(bot, run_async):
    from tests.i18n_ml2_bb import toast_texts
    q = _tap(bot, run_async, "_:set:lang:auto", GROUP, OWNER)
    assert any("Invalid value" in t for t in toast_texts(q))


def test_the_automatic_button_in_a_group_keeps_its_language(bot, run_async):
    prefs.set_ui_language(GROUP, "fa")
    _tap(bot, run_async, "_:set:lang:auto", GROUP, OWNER)
    assert prefs.get_ui_language(GROUP) == "fa"


def test_an_existing_owner_choosing_automatic_then_reads_their_app(bot, run_async, p5):
    from aipager import i18n
    from tests.i18n_ml2_bb import message
    p5.marker(OWNER)
    _tap(bot, run_async, "_:set:lang:auto", OWNER, OWNER)
    assert i18n.for_update(message("hi", chat=OWNER, user=OWNER,
                                   language_code="fa")).code == "fa"


# ---- which choice is marked ----------------------------------------------------------------

def test_a_stored_code_is_marked(p5):
    p5.marker(OWNER)
    prefs.set_ui_language(OWNER, "ru")
    assert language_menu.current_choice(OWNER, private=True, telegram_code="fa") == "ru"


def test_stored_automatic_is_marked(p5):
    p5.marker(OWNER)
    prefs.set_ui_language(OWNER, "auto")
    assert language_menu.current_choice(OWNER, private=True, telegram_code="en") == "auto"


@pytest.mark.parametrize("tag", ["fa", "en", None])
def test_a_new_person_with_nothing_stored_is_on_automatic(p5, tag):
    p5.marker(OWNER)
    assert language_menu.current_choice(NEWBIE, private=True, telegram_code=tag) == "auto"


@pytest.mark.parametrize("tag", ["en", None])
def test_an_existing_english_person_is_on_automatic(p5, tag):
    """D10: Automatic would read English, so the mark stays where it was."""
    p5.marker(OWNER)
    assert language_menu.current_choice(OWNER, private=True, telegram_code=tag) == "auto"


def test_an_existing_persian_person_is_on_english(p5):
    """D10: the legacy chat reads English, which Automatic would not."""
    p5.marker(OWNER)
    assert language_menu.current_choice(OWNER, private=True, telegram_code="fa") == "en"


def test_an_existing_person_seen_in_persian_is_on_english(p5):
    p5.marker(OWNER)
    persist.set_seen(OWNER, "fa")
    assert language_menu.current_choice(OWNER, private=True) == "en"


def test_a_group_with_nothing_stored_is_on_english(p5):
    p5.marker(OWNER)
    assert language_menu.current_choice(GROUP, private=False) == "en"


# ---- the Mini App ----------------------------------------------------------------------

@pytest.fixture
def http(bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    from aiohttp.test_utils import TestClient, TestServer

    from aipager.miniapp.server import MiniAppServer
    server = MiniAppServer(bot, bot.registry, port=8765)

    def _call(method, path, *, user_id=OWNER, language_code=None, **kw):
        async def _go():
            client = TestClient(TestServer(server._build_app()))
            await client.start_server()
            try:
                resp = await client.request(
                    method, path, headers=init_header(user_id, language_code=language_code),
                    **kw)
                return resp.status, await resp.json()
            finally:
                await client.close()
        return run_async(_go())
    return _call


def test_the_mini_app_stores_automatic(http):
    http("PUT", "/api/app-language", json={"value": "auto"})
    assert prefs.get_ui_language(OWNER) == "auto"


def test_the_mini_app_answers_automatic(http):
    status, body = http("PUT", "/api/app-language", json={"value": "auto"})
    assert (status, body["value"]) == (200, "auto")


def test_the_mini_app_automatic_follows_the_viewers_app(http):
    _s, body = http("PUT", "/api/app-language", language_code="fa", json={"value": "auto"})
    assert body["effective"] == "fa"


def test_the_mini_app_marks_an_existing_persian_owner_on_english(http, p5):
    p5.marker(OWNER)
    _s, body = http("GET", "/api/preferences", language_code="fa")
    assert body["app_language"]["value"] == "en"


def test_the_mini_app_shows_english_in_force_for_an_existing_persian_owner(http, p5):
    p5.marker(OWNER)
    _s, body = http("GET", "/api/preferences", language_code="fa")
    assert body["app_language"]["effective"] == "en"


def test_the_mini_app_marks_a_new_persian_person_on_automatic(http, p5):
    p5.marker(OWNER)
    _s, body = http("GET", "/api/preferences", user_id=2, language_code="fa")
    assert body["app_language"]["value"] == "auto"


def test_the_mini_app_puts_a_new_persian_person_in_persian(http, p5):
    p5.marker(OWNER)
    _s, body = http("GET", "/api/preferences", user_id=2, language_code="fa")
    assert body["app_language"]["effective"] == "fa"


@pytest.mark.parametrize("value", ["", "xx", "ja", "qps"])
def test_the_mini_app_refuses_a_bad_value(http, value):
    status, _b = http("PUT", "/api/app-language", json={"value": value})
    assert status == 400
