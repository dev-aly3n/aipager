"""A language switch refreshes what the chat shows in its language:
exactly one `/` menu registration, ONE main keyboard (single-scope mode
used to send two: the menu refresh sent one itself) and one menu button,
each in the new language (G63, G64)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager.i18n import locales
from tests.test_language_setting import tap

CHAT = 222333444  # the pinned single chat (_pin_single_chat_config)
MARK = "⟦"


@pytest.fixture
def single(mk_bot, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    bot = mk_bot()
    bot._miniapp_url = "https://example.invalid/app"
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.set_chat_menu_button = AsyncMock()
    bot._send_keyboard = AsyncMock()
    return bot


def test_a_switch_sends_one_keyboard(single, run_async):
    tap(single, run_async, "_:set:lang:qps", chat=CHAT, user=CHAT)
    assert single._send_keyboard.await_count == 1
    assert single._send_keyboard.await_args.kwargs == {"level": "main", "chat_id": CHAT}
    assert single._app.bot.set_my_commands.await_count == 1
    assert single._app.bot.set_chat_menu_button.await_count == 1
    # Nothing again when the language does not change.
    tap(single, run_async, "_:set:lang:qps", chat=CHAT, user=CHAT)
    assert single._send_keyboard.await_count == 1
    assert single._app.bot.set_my_commands.await_count == 1


def test_menu_and_button_follow_the_language(single, run_async):
    tap(single, run_async, "_:set:lang:qps", chat=CHAT, user=CHAT)
    commands = single._app.bot.set_my_commands.await_args.args[0]
    assert commands and all(c.description.startswith(MARK) for c in commands)
    assert [c.command for c in commands][:2] == ["new", "status"]
    button = single._app.bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert button.text == "📱 App"     # never translated (operator, 2026-10-10)
    # And back to English: today's words.
    tap(single, run_async, "_:set:lang:en", chat=CHAT, user=CHAT)
    commands = single._app.bot.set_my_commands.await_args.args[0]
    assert commands[0].description == "Start a session"
    button = single._app.bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert button.text == "📱 App"
    assert single._send_keyboard.await_count == 2


def test_a_session_command_reads_talk_to_in_the_language(single, run_async):
    from aipager.state import Status, TrackedSession
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    single.registry._sessions[sess.name] = sess
    tap(single, run_async, "_:set:lang:qps", chat=CHAT, user=CHAT)
    commands = single._app.bot.set_my_commands.await_args.args[0]
    talk = next(c for c in commands if c.command == "x1")
    assert talk.description.startswith(MARK) and "x1" in talk.description
