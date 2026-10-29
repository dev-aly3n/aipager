"""The first-contact line right after start, against the fake Bot API
(tester-iter1-002).

Right after ``TelegramBot.start()`` the start-up messages use the chat's
outbound budget, and the line, a skippable ORNAMENT send, used to be
dropped by the limiter for a message that came in the first seconds. The
step now waits out ``language_launch.START_GRACE`` from start before it
sends, so the line still follows the reply it belongs to, still at the
lowest priority. 127.0.0.1 only; the description task never runs.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from aipager import i18n, telegram_endpoint
from aipager.bot import bot_descriptions, language_launch, language_menu
from aipager.i18n import persist
from tests.e2e.fake_telegram.server import FakeBotApiThread

TOKEN = "7000000017:AAFakeGraceTail0123456789abcdefghij"
CHAT = 222333444                       # the chat tests/conftest.py pins


@pytest.fixture
def fake(monkeypatch):
    t = FakeBotApiThread(TOKEN, 7000000017, "aipager_grace_bot")
    base = t.start()
    t.api.add_chat(CHAT, "private")
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)

    async def _forever(_seconds):
        await asyncio.Event().wait()

    monkeypatch.setattr(bot_descriptions, "_sleep", _forever)
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 3600.0)
    try:
        yield t.api
    finally:
        t.stop()


def _line(code):
    return re.sub(r"<[^>]+>", "", str(language_menu.first_contact_text(i18n.get(code))))


def test_a_message_right_after_start_still_gets_the_first_contact_line(fake):
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry
    assert persist.note_prior_run() is False             # a fresh install

    async def go():
        bot = TelegramBot(SessionRegistry())
        await asyncio.wait_for(bot.start(), 30)
        try:
            since = fake.calls()[-1].seq if fake.calls() else 0
            sender = fake.user(CHAT, "owner", "Owner")
            sender["language_code"] = "fa"
            await asyncio.to_thread(fake.inject_text, CHAT, sender, "/help")
            await asyncio.to_thread(
                fake.wait_for, lambda c: c.method == "sendMessage" and c.chat_id == CHAT,
                30, since=since)
            try:
                await asyncio.to_thread(
                    fake.wait_for, lambda c: c.method == "sendMessage"
                    and c.chat_id == CHAT and c.text == _line("fa"),
                    language_launch.START_GRACE + 10, since=since)
            except AssertionError:  # timed out: the assertion below says what came
                pass
            await language_launch.drain(bot)
            return fake.calls(method="sendMessage", chat_id=CHAT, since=since)
        finally:
            await asyncio.wait_for(bot.stop(), 30)

    sent = asyncio.run(go())
    texts = [c.text for c in sent]
    assert texts.count(_line("fa")) == 1 and texts[0] != _line("fa"), texts
    assert persist.first_contact_done(CHAT)
