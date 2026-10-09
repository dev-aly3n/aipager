"""Every language surface explains that the setting changes only aipager's
own text, not the Claude sessions (spec.md (d): "a test asserts the line
is present on every such surface"; entrypoints.md exact English).

Surfaces: /language in a DM and in a group, the /settings row's list in a
DM and in a group, the confirmation in a DM and in a group, the renderer,
the offer card, the Mini App hint and the Mini App confirmation.
"""

from __future__ import annotations

import pytest

DM = 555
GROUP = -1001
OWNER = 555
BOB = 2

#: The part of each surface that says Claude is not affected.
NOT_CLAUDE = {
    "dm_list": "Your Claude sessions don't change: Claude answers in the "
               "language you write to it.",
    "group_list": "Claude's sessions don't change: Claude answers in the "
                  "language you write to it.",
    "confirmation": "Your sessions and Claude's answers are unchanged.",
    "offer": "not your Claude sessions: Claude still answers in the language "
             "you write to it.",
    "app_hint": "Claude's answers don't change.",
}


def test_the_dm_command_says_claude_is_unchanged(tg_bot, command):
    text, _kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    assert NOT_CLAUDE["dm_list"] in text


def test_the_group_command_says_claude_is_unchanged(tg_bot, command):
    text, _kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=GROUP, user_id=BOB)
    assert NOT_CLAUDE["group_list"] in text


def test_the_dm_settings_row_says_claude_is_unchanged(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=DM, user_id=OWNER)
    assert NOT_CLAUDE["dm_list"] in ml.edited(q)[0]


def test_the_group_settings_row_says_claude_is_unchanged(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=GROUP, user_id=BOB)
    assert NOT_CLAUDE["group_list"] in ml.edited(q)[0]


def test_the_dm_confirmation_says_claude_is_unchanged(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert NOT_CLAUDE["confirmation"] in ml.edited(q)[0]


def test_the_group_confirmation_says_claude_is_unchanged(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=OWNER)
    assert NOT_CLAUDE["confirmation"] in ml.edited(q)[0]


def test_the_automatic_confirmation_says_claude_is_unchanged(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert NOT_CLAUDE["confirmation"] in ml.edited(q)[0]


@pytest.mark.parametrize("private,key", [(True, "dm_list"), (False, "group_list")])
def test_the_renderer_says_claude_is_unchanged(private, key):
    from aipager import i18n
    from aipager.bot.language_menu import render_language_menu
    text, _kb = render_language_menu(i18n.get("en"), private=private, current="en")
    assert NOT_CLAUDE[key] in text


def test_the_renderer_confirmation_says_claude_is_unchanged():
    from aipager import i18n
    from aipager.bot.language_menu import confirmation
    assert confirmation(i18n.get("en"), "en") == (
        "✅ aipager now speaks English here. " + NOT_CLAUDE["confirmation"])


def test_the_offer_card_says_claude_is_unchanged():
    from aipager import i18n
    from aipager.bot.language_offer import render_offer
    text, _kb = render_offer(i18n.get("en"), "fa")
    assert NOT_CLAUDE["offer"] in text


def test_the_mini_app_hint_says_claude_is_unchanged(mini_server, run_async, ml):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/api/preferences", headers=ml.hdr(BOB))
            return await resp.json()
        finally:
            await client.close()
    body = run_async(_go())
    assert NOT_CLAUDE["app_hint"] in body["app_language"]["hint"]


def test_the_mini_app_confirmation_says_claude_is_unchanged(mini_server, run_async, ml):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            resp = await client.put("/api/app-language", headers=ml.hdr(BOB),
                                    json={"value": "en"})
            return await resp.json()
        finally:
            await client.close()
    body = run_async(_go())
    assert NOT_CLAUDE["confirmation"] in body["confirmation"]


@pytest.mark.parametrize("key", list(NOT_CLAUDE))
def test_no_surface_text_carries_an_em_dash(key):
    assert "\u2014" not in NOT_CLAUDE[key]


def test_the_settings_page_of_the_mini_app_has_the_app_language_host(
        mini_server, run_async):
    """entrypoints.md: Settings shows an "App language" group above "Chat
    preferences" with the hint line under it."""
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            return await (await client.get("/")).text()
        finally:
            await client.close()
    page = run_async(_go())
    assert page.index("settings-app-language") < page.index("Chat preferences")
