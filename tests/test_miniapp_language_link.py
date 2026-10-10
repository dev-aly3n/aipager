"""The Mini App opens in its viewer's language from the first frame.

Operator live test, 2026-10-10: the page opened in English and turned
Persian a moment later, on every open after a daemon restart (the tunnel's
address changes, and the phone's remembered language is per address). The
fix: the language travels with the link.

- Every Mini App link aipager hands out carries the chat's language as
  ``?lang=<code>`` (none for English: the link is the bare URL, as before).
- ``GET /?lang=<code>`` serves the page already in that language: ``<html
  lang dir>``, the marked strings in the markup and the catalog inlined
  for ``t()`` (``tests/js/miniapp_i18n.js``'s ``first_paint`` scenario
  proves the first frame needs no request). Anything else is the English
  page, byte for byte.
- Changing App language inside the Mini App re-sets the viewer's menu
  button, so its link carries the new language.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager.bot import miniapp_link
from aipager.i18n import locales
from aipager.miniapp import ui_text
from aipager.miniapp.static import index_html, page_language

URL = "https://tunnel.example/"
DM_EN, DM_FA, DM_RU, DM_AR, GROUP = 101, 102, 103, 104, -100
BIDI_RE = re.compile("[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]")
PERSIAN = re.compile("[\u0600-\u06ff]")

ENABLED = [c for c in locales.enabled_codes() if c != "en"]


# ---- the link -------------------------------------------------------------------

@pytest.mark.parametrize("code", ["en", None, ""], ids=["en", "none", "empty"])
def test_an_english_link_is_the_bare_url(code):
    assert miniapp_link.with_lang(URL, code) is URL


@pytest.mark.parametrize("url,want", [
    (URL, URL + "?lang=fa"),
    ("https://t.example/app?x=1", "https://t.example/app?x=1&lang=fa"),
    ("https://t.example/#top", "https://t.example/?lang=fa#top"),
], ids=["bare", "query", "fragment"])
def test_a_language_is_one_query_parameter(url, want):
    assert miniapp_link.with_lang(url, "fa") == want


def test_a_code_with_a_script_is_kept_whole():
    assert miniapp_link.with_lang(URL, "zh-Hans") == URL + "?lang=zh-Hans"


def test_no_url_stays_no_url():
    assert miniapp_link.with_lang("", "fa") == ""
    assert miniapp_link.for_chat("", DM_FA) == ""


# ---- every link the bot hands out ----------------------------------------------------

def _scope(chat_id, label):
    from aipager.scope import Member, Scope
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group", label=label,
                 members=(Member(id=abs(chat_id), label="u", role="user"),))


@pytest.fixture(autouse=True)
def _reachable_url(monkeypatch):
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=True))


@pytest.fixture
def languages():
    """Four private chats, one per language, and a group in Persian."""
    preferences.set_ui_language(DM_FA, "fa")
    preferences.set_ui_language(DM_RU, "ru")
    preferences.set_ui_language(DM_AR, "ar")
    preferences.set_ui_language(GROUP, "fa")


@pytest.fixture
def bot(mk_bot, languages):
    b = mk_bot(scopes=[_scope(DM_EN, "en"), _scope(DM_FA, "fa"), _scope(DM_RU, "ru"),
                       _scope(DM_AR, "ar"), _scope(GROUP, "team")])
    b._app.bot.set_chat_menu_button = AsyncMock()
    b._app.bot.set_my_commands = AsyncMock()
    b._app.bot.delete_my_commands = AsyncMock()
    return b


def _menu_urls(bot) -> dict:
    out = {}
    for call in bot._app.bot.set_chat_menu_button.await_args_list:
        web_app = getattr(call.kwargs["menu_button"], "web_app", None)
        out[call.kwargs["chat_id"]] = web_app.url if web_app is not None else None
    return out


WANT = {DM_EN: URL, DM_FA: URL + "?lang=fa", DM_RU: URL + "?lang=ru",
        DM_AR: URL + "?lang=ar"}


def test_each_private_chats_menu_button_carries_its_language(bot, run_async):
    run_async(bot.publish_miniapp_button(URL))
    assert _menu_urls(bot) == WANT


def test_the_english_menu_button_link_is_the_bare_url(bot, run_async):
    run_async(bot.publish_miniapp_button(URL))
    assert _menu_urls(bot)[DM_EN] == URL


def test_a_group_still_gets_no_menu_button(bot, run_async):
    run_async(bot.publish_miniapp_button(URL))
    assert GROUP not in _menu_urls(bot)


def test_the_default_menu_button_is_still_never_set(bot, run_async):
    run_async(bot.publish_miniapp_button(URL))
    assert all("chat_id" in c.kwargs and c.kwargs["chat_id"] is not None
               for c in bot._app.bot.set_chat_menu_button.await_args_list)


def test_a_language_change_resets_the_button_with_the_new_link(bot, run_async):
    run_async(bot.publish_miniapp_button(URL))
    bot._app.bot.set_chat_menu_button.reset_mock()
    preferences.set_ui_language(DM_EN, "ru")
    run_async(bot._set_menu_button_for(DM_EN))
    assert _menu_urls(bot) == {DM_EN: URL + "?lang=ru"}


def test_a_new_private_scope_gets_its_language_in_the_link(bot, run_async):
    bot._miniapp_url = URL
    old = list(bot.scopes)
    bot.scopes = old + [_scope(105, "new")]
    preferences.set_ui_language(105, "fa")
    run_async(bot._refresh_scope_menus(old))
    assert _menu_urls(bot) == {105: URL + "?lang=fa"}


def _keyboard_app_url(bot, chat_id):
    from telegram import ReplyKeyboardMarkup
    for call in bot._app.bot.send_message.await_args_list:
        markup = call.kwargs.get("reply_markup")
        target = call.kwargs.get("chat_id", call.args[0] if call.args else None)
        if target == chat_id and isinstance(markup, ReplyKeyboardMarkup):
            for row in markup.keyboard:
                for button in row:
                    if getattr(button, "web_app", None) is not None:
                        return button.web_app.url
    return "no button"


@pytest.mark.parametrize("chat_id", [DM_EN, DM_FA, DM_RU, DM_AR],
                         ids=["en", "fa", "ru", "ar"])
def test_the_keyboard_app_button_carries_the_chats_language(bot, run_async, chat_id):
    bot._miniapp_url = URL
    run_async(bot._send_keyboard(level="main", chat_id=chat_id))
    assert _keyboard_app_url(bot, chat_id) == WANT[chat_id]


def test_a_group_keyboard_still_has_no_app_button(bot, run_async):
    bot._miniapp_url = URL
    run_async(bot._send_keyboard(level="main", chat_id=GROUP))
    assert _keyboard_app_url(bot, GROUP) == "no button"


def _private_update(chat_id, chat_type="private"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    return update


@pytest.mark.parametrize("chat_id", [DM_EN, DM_FA, DM_RU, DM_AR],
                         ids=["en", "fa", "ru", "ar"])
def test_the_settings_app_row_carries_the_chats_language(bot, chat_id):
    bot._miniapp_url = URL
    [[button]] = bot._app_button_row(_private_update(chat_id))
    assert button.web_app.url == WANT[chat_id]


def test_the_settings_app_row_is_still_private_only(bot):
    bot._miniapp_url = URL
    assert bot._app_button_row(_private_update(GROUP, "supergroup")) == []


@pytest.mark.parametrize("chat_id", [DM_EN, DM_FA], ids=["en", "fa"])
def test_the_app_command_button_carries_the_chats_language(bot, run_async, mk_update,
                                                           monkeypatch, chat_id):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.miniapp.tunnel.resolve_public_url",
                        AsyncMock(return_value=URL))
    monkeypatch.setattr(type(bot), "_authorize", AsyncMock(return_value=True))
    update = mk_update("/app", user_id=chat_id, chat_id=chat_id)
    update.effective_chat.type = "private"
    update.effective_user.language_code = None
    run_async(bot._handle_app_cmd(update, MagicMock()))
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].web_app.url == WANT[chat_id]


@pytest.mark.parametrize("chat_id", [DM_EN, DM_FA], ids=["en", "fa"])
def test_the_pinned_bar_app_button_carries_the_chats_language(bot, chat_id):
    bot._miniapp_url = URL
    _text, markup = bot._render_pinned(chat_id, [])
    urls = [b.web_app.url for row in markup.inline_keyboard for b in row
            if getattr(b, "web_app", None) is not None]
    assert urls == [WANT[chat_id]]


# ---- the page served for a link ------------------------------------------------------

def test_an_english_link_serves_the_english_page_byte_for_byte():
    english = index_html(sdk_from_self=True)
    assert index_html(sdk_from_self=True, lang="en") == english
    assert index_html(sdk_from_self=True, lang=None) == english


@pytest.mark.parametrize("lang", ["xx", "FA", "fa-IR", "ja", "en-US", "", "fa\"><x",
                                  "../fa", "qps"],
                         ids=["unknown", "upper", "region", "disabled", "en-region", "empty",
                              "markup", "path", "pseudo-off"])
def test_a_lang_nobody_may_pick_is_ignored(lang):
    assert page_language(lang) == "en"
    assert index_html(sdk_from_self=True, lang=lang) == index_html(sdk_from_self=True)


@pytest.mark.parametrize("code,direction", [("fa", "rtl"), ("ar", "rtl"), ("ru", "ltr")],
                         ids=["fa", "ar", "ru"])
def test_the_page_names_its_language_and_direction(code, direction):
    page = index_html(lang=code)
    assert f'<html lang="{code}" dir="{direction}">' in page


def _inlined(page: str) -> dict:
    m = re.search(r"AIPAGER_UI_FIRST=(\{.*?\});\n", page)
    assert m, "no inlined catalog"
    return json.loads(m.group(1))


def test_the_page_inlines_exactly_the_catalog_the_route_serves():
    got = _inlined(index_html(lang="fa"))
    assert got == {"v": ui_text.catalog_version("fa"), "cat": ui_text.catalog("fa")}


def test_the_english_page_inlines_no_catalog():
    assert "AIPAGER_UI_FIRST=" not in index_html()


def test_only_the_links_language_is_inlined():
    page = index_html(lang="fa")
    assert page.count("AIPAGER_UI_FIRST=") == 1
    assert ui_text.catalog("ru")["strings"]["ui.tab.sessions"] not in page


@pytest.mark.parametrize("code", ENABLED)
def test_no_raw_direction_control_reaches_the_page(code):
    assert not BIDI_RE.search(index_html(lang=code))


@pytest.mark.parametrize("code", ENABLED)
def test_the_inlined_catalog_cannot_close_the_script(code):
    m = re.search(r"AIPAGER_UI_FIRST=(\{.*?\});\n", index_html(lang=code))
    assert "</" not in m.group(1)


def test_an_invisible_character_is_escaped_in_the_inlined_catalog():
    from aipager.miniapp.static import _script_safe
    raw = json.dumps({"s": "a\u200cb\u2028c\u2068d</e"}, ensure_ascii=False)
    safe = _script_safe(raw)
    assert safe == '{"s": "a\\u200cb\\u2028c\\u2068d<\\/e"}'
    assert json.loads(safe) == {"s": "a\u200cb\u2028c\u2068d</e"}


def test_an_attribute_is_matched_by_its_whole_name():
    """``placeholder`` is never found inside ``data-i18n-placeholder``,
    whichever comes first in the tag."""
    from aipager.miniapp.static import _localized_body
    cat = ui_text.catalog("fa")
    want = cat["strings"]["ui.report.note_placeholder"]
    body = '<textarea data-i18n-placeholder="ui.report.note_placeholder" placeholder="x">'
    assert _localized_body(body, cat) == (
        f'<textarea data-i18n-placeholder="ui.report.note_placeholder" placeholder="{want}">')


def test_the_persian_markup_is_persian_before_any_script():
    page = index_html(lang="fa")
    fa = ui_text.catalog("fa")["strings"]
    assert f'<span data-i18n="ui.tab.sessions">{fa["ui.tab.sessions"]}</span>' in page
    assert f'aria-label="{fa["ui.shell.sections_aria"]}"' in page
    assert 'data-i18n-num="0 / 500">\u06f0 / \u06f5\u06f0\u06f0<' in page


# Roadmap 8.117's budgets (tests/test_miniapp_page_rules.py), for every
# language a link can name: only its own catalog rides along.
from tests.test_miniapp_page_rules import PAGE_BUDGET_BYTES, SENT_BUDGET_BYTES  # noqa: E402


@pytest.mark.parametrize("code", ENABLED)
@pytest.mark.parametrize("sdk_from_self", [True, False], ids=["self-served", "fallback"])
def test_a_language_page_stays_under_both_byte_budgets(code, sdk_from_self):
    raw = index_html(sdk_from_self=sdk_from_self, lang=code).encode("utf-8")
    assert len(raw) <= PAGE_BUDGET_BYTES, f"{code}: {len(raw)} bytes"
    sent = len(gzip.compress(raw, compresslevel=9, mtime=0))
    assert sent <= SENT_BUDGET_BYTES, f"{code}: {sent} bytes sent"


# ---- the route --------------------------------------------------------------------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture
def server(mk_bot, monkeypatch, languages):
    from aipager.miniapp.server import MiniAppServer
    from aipager.state import SessionRegistry
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    registry = SessionRegistry()
    b = mk_bot(registry, scopes=[_scope(DM_EN, "en"), _scope(DM_FA, "fa")])
    b._app.bot.set_chat_menu_button = AsyncMock()
    b._app.bot.set_my_commands = AsyncMock()
    b._app.bot.delete_my_commands = AsyncMock()
    b._miniapp_url = URL
    srv = MiniAppServer(b, registry, port=8765)
    srv._sdk.get = AsyncMock(return_value=b"// sdk")
    return srv


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _get_pages(srv, queries):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    try:
        out = []
        for q in queries:
            resp = await client.get("/" + q, headers={"Accept-Encoding": "identity"})
            out.append((resp.status, await resp.text()))
        return out
    finally:
        await client.close()


def test_the_route_serves_the_links_language(server):
    [(status, page)] = _run(_get_pages(server, ["?lang=fa"]))
    assert status == 200
    assert page == index_html(sdk_from_self=True, lang="fa")
    assert PERSIAN.search(page.split("<body>", 1)[1].split("<script>", 1)[0])


@pytest.mark.parametrize("query", ["", "?lang=en", "?lang=xx", "?lang=ja", "?lang=",
                                   "?x=fa"],
                         ids=["none", "en", "unknown", "disabled", "empty", "other-param"])
def test_the_route_serves_english_for_anything_else(server, query):
    [(status, page)] = _run(_get_pages(server, [query]))
    assert status == 200
    assert page == index_html(sdk_from_self=True)


def test_the_page_cache_holds_one_page_per_language_whatever_is_asked(server):
    queries = [f"?lang=x{i}" for i in range(40)] + ["?lang=fa", "?lang=fa", ""]
    _run(_get_pages(server, queries))
    assert sorted(server._page_cache) == [(True, "en"), (True, "fa")]


# ---- App language changed in the Mini App ------------------------------------------------

def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def _put_language(srv, user_id, value):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    try:
        resp = await client.put("/api/app-language", json={"value": value},
                                headers={"X-Telegram-Init-Data": _init_data(user_id),
                                         "X-Aipager-Scope": str(user_id)})
        status = resp.status
        if srv._language_tasks:
            await asyncio.gather(*list(srv._language_tasks))
        return status
    finally:
        await client.close()


def test_changing_app_language_resets_the_menu_button_with_the_new_link(server):
    assert _run(_put_language(server, DM_EN, "fa")) == 200
    assert _menu_urls(server.bot) == {DM_EN: URL + "?lang=fa"}


def test_changing_back_to_english_resets_it_to_the_bare_url(server):
    assert _run(_put_language(server, DM_FA, "en")) == 200
    assert _menu_urls(server.bot) == {DM_FA: URL}


def test_the_same_language_again_sets_no_button(server):
    assert _run(_put_language(server, DM_FA, "fa")) == 200
    assert server.bot._app.bot.set_chat_menu_button.await_count == 0


def test_an_app_language_change_posts_nothing_into_the_chat(server):
    assert _run(_put_language(server, DM_EN, "fa")) == 200
    assert server.bot._app.bot.send_message.await_count == 0


# ---- the first frame, driven ------------------------------------------------------------

HARNESS = Path(__file__).parent / "js" / "miniapp_i18n.js"


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available - the page i18n harness is skipped")
    return exe


def _first(node_bin, tmp_path, page, opts, name="page.html"):
    path = tmp_path / name
    path.write_text(page, encoding="utf-8")
    opts_path = tmp_path / (name + ".opts.json")
    opts_path.write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, AIPAGER_I18N_OPTS=str(opts_path))
    proc = subprocess.run([node_bin, str(HARNESS), str(path), "first_paint"],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}"
    lines = [x for x in proc.stdout.splitlines() if x.startswith("RESULT ")]
    return json.loads(lines[-1][len("RESULT "):])


def _ui(code):
    body = ui_text.catalog(code)
    return {"code": code, "rtl": body["rtl"], "v": ui_text.catalog_version(code)}


@pytest.mark.parametrize("code,direction", [("fa", "rtl"), ("ar", "rtl"), ("ru", "ltr")],
                         ids=["fa", "ar", "ru"])
def test_the_first_frame_is_already_in_the_links_language(node_bin, tmp_path, code,
                                                         direction):
    run = _first(node_bin, tmp_path, index_html(lang=code),
                 {"ui": _ui(code), "catalogs": {code: ui_text.catalog(code)}, "storage": {}})
    first = run["first"]
    # The markup as served: the language and direction, before any script.
    assert (first["pre"]["lang"], first["pre"]["dir"]) == (code, direction)
    # What the script then makes of the marked strings is exactly what the
    # server wrote: nothing changes on screen when the script runs.
    assert first["pre"]["statics"] == first["boot"]["statics"]
    # "Loading" follows its own direction from the first frame, as after
    # any switch.
    assert first["pre"]["daemonDir"] == first["boot"]["daemonDir"] == "auto"
    assert (first["boot"]["lang"], first["boot"]["dir"]) == (code, direction)
    # The page asked for no catalog, before or after its first render.
    assert not [c for c in first["boot"]["calls"] + run["calls"] if "/api/i18n/" in c]
    assert (run["lang"], run["dir"]) == (code, direction)
    # The phone remembers it as well (the second signal).
    assert run["storage"]["aipager.lang"] == code


def test_the_first_persian_frame_reads_persian(node_bin, tmp_path):
    fa = ui_text.catalog("fa")
    run = _first(node_bin, tmp_path, index_html(lang="fa"),
                 {"ui": _ui("fa"), "catalogs": {"fa": fa}})
    pre = run["first"]["pre"]["texts"]
    assert fa["strings"]["ui.tab.sessions"] in pre
    assert fa["strings"]["ui.shell.loading"] in pre
    assert "Sessions" not in pre and "Loading…" not in pre
    boot = run["first"]["boot"]["texts"]
    assert fa["strings"]["ui.tab.sessions"] in boot and "Sessions" not in boot
    grid = run["screens"][-1]["texts"]
    assert fa["strings"]["ui.tab.sessions"] in grid


def test_the_page_follows_the_api_when_the_viewer_reads_english_now(node_bin, tmp_path):
    """The link said Persian, the viewer changed to English meanwhile: the
    API names no language, and the page goes English."""
    run = _first(node_bin, tmp_path, index_html(lang="fa"), {"storage": {}})
    assert (run["first"]["pre"]["lang"], run["lang"], run["dir"]) == ("fa", "en", "ltr")
    assert "Sessions" in run["screens"][-1]["texts"]
    assert "aipager.lang" not in run["storage"]


def test_the_page_follows_the_api_to_another_language(node_bin, tmp_path):
    ru = ui_text.catalog("ru")
    run = _first(node_bin, tmp_path, index_html(lang="fa"),
                 {"ui": _ui("ru"), "catalogs": {"ru": ru}})
    assert (run["lang"], run["dir"]) == ("ru", "ltr")
    assert [c for c in run["calls"] if "/api/i18n/" in c] == [
        f"GET /api/i18n/ru.json?v={_ui('ru')['v']}"]
    assert ru["strings"]["ui.tab.sessions"] in run["screens"][-1]["texts"]


def test_the_english_first_frame_is_unchanged(node_bin, tmp_path):
    run = _first(node_bin, tmp_path, index_html(), {})
    first = run["first"]
    assert (first["pre"]["lang"], first["pre"]["dir"]) == ("en", None)
    assert "Sessions" in first["pre"]["texts"] and "Loading…" in first["pre"]["texts"]
    assert not [c for c in run["calls"] if "/api/i18n/" in c]
