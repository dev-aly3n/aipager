"""Black-box scenarios of multilingual phase 3b: /update in a Persian chat,
a problem report in a Russian chat, a Persian group with a Russian member,
and a Persian stranger. Every Telegram call is a recorded fake; the
installer, the network and Sentry are faked at their seams."""

from __future__ import annotations

import asyncio
import json
import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences, self_update
from aipager import team as team_mod
from aipager.bot import report_flow, update_flow
from tests import i18n_ml2
from tests.i18n_ml2 import BOB, DM, GROUP, OWNER, callback, message
from tests.i18n_surfaces import update_status
from tests.report_ui_harness import FakeNet, attachment, drain_sends, make_bot, pin_version
from tests.report_ui_harness import OWNER as REPORT_OWNER
from tests.report_ui_harness import tap, text_update

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")


@pytest.fixture
def bot(mk_bot):
    return i18n_ml2.make_bot(mk_bot)


def _fa(text):
    return bool(PERSIAN.search(text)) and not CYRILLIC.search(text)


def _ru(text):
    return bool(CYRILLIC.search(text)) and not PERSIAN.search(text)


def test_update_in_a_persian_chat(bot, run_async, monkeypatch):
    """/update, Check for updates, the Update button, and the line the
    new daemon posts after the restart: all Persian."""
    preferences.set_ui_language(DM, "fa")
    update = message("/update")
    run_async(update_flow.handle_update_cmd(bot, update, MagicMock()))
    call = update.message.reply_text.await_args
    assert _fa(call.args[0])
    button = call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert _fa(button.text) and button.callback_data == "_:up:chk"

    async def _check():
        bot.updates._checked = (time.monotonic(), update_status())
        return update_status()
    bot.updates.check = _check
    spawned = []
    bot.updates._spawn = spawned.append
    update, query = callback("_:up:chk")
    query.message.edit_text = AsyncMock()
    run_async(bot._handle_callback(update, MagicMock()))
    assert _fa(query.answer.await_args.args[0])
    run_async(spawned[0])
    text = query.message.edit_text.await_args.args[0]
    assert _fa(text) and "1.0.1" in text and "←" in text and "→" not in text
    go = query.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert go.callback_data == "_:up:go:both" and _fa(go.text)

    started = []

    async def _start(kind, **kw):
        started.append((kind, kw["chat_id"]))
        return update_flow.StartResult(True, None, None)
    bot.updates.start = _start
    update, query = callback(go.callback_data)
    run_async(bot._handle_callback(update, MagicMock()))
    assert started == [("both", DM)]

    marker = {"from": "1.0.1", "to": "1.0.2", "chat_id": DM, "scheduled_at": time.time(),
              "sessions": [{"name": "claude-x9", "label": "x9"}]}
    monkeypatch.setattr(self_update, "MARKER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(self_update, "read_and_clear_marker", lambda: dict(marker))
    monkeypatch.setattr(self_update, "running_version", lambda: "1.0.2")
    bot._app.bot.send_message.reset_mock()
    run_async(update_flow.deliver_update_marker(bot, bot.registry))
    line = bot._app.bot.send_message.await_args.args[1]
    assert _fa(line) and "x9" in line and "۰" in line


def test_a_problem_report_in_a_russian_chat(mk_bot, run_async, monkeypatch):
    """Report a problem, Add a note, the note, Send: the card, its buttons,
    its statuses and the outcome read Russian; the report itself is the
    fixed English JSON that is sent byte for byte."""
    pin_version(monkeypatch)
    net = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", net.transport)
    preferences.set_ui_language(REPORT_OWNER, "ru")
    bot, tg = make_bot(mk_bot)

    def press(data, **kw):
        update, query = tap(data, **kw)

        async def _go():
            await bot._handle_callback(update, MagicMock())
            await drain_sends(bot)
        run_async(_go())
        return query

    press("_:rp:open")
    card = tg.sent[0]
    assert _ru(card.text) and "Это всё, что содержит отчёт." in card.text
    labels = [b.text for row in card.kw["reply_markup"].inline_keyboard for b in row]
    assert labels == ["📤 Отправить", "✏️ Добавить заметку", "✖️ Отмена"]
    press("_:rp:note", message_id=card.message_id)
    assert "не больше 500 символов" in tg.last_edit_of(card.message_id)["text"]

    update = text_update("it froze")

    async def _note():
        from aipager.bot import new_flow
        await new_flow.close_if_moved_on(bot, update)
        await bot._handle_message(update, MagicMock())
        await asyncio.sleep(0)
    run_async(_note())
    assert "<b>Заметка добавлена.</b>" in tg.last_edit_of(card.message_id)["text"]
    press("_:rp:send", message_id=card.message_id)
    final = tg.last_edit_of(card.message_id)["text"]
    assert "Отправлено. Номер " in final
    sent = json.loads(attachment(net.posts[0].content))
    assert sent["note"] == "it froze"


def test_a_persian_group_with_a_russian_member(bot, run_async):
    """The group reads Persian; a member who may not update gets a Russian
    alert; their own refusal never changes the group's message."""
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    update, query = callback("_:up:chk", chat=GROUP, user=BOB)
    run_async(bot._handle_callback(update, MagicMock()))
    assert query.answer.await_args.args[0] == "🚫 Обновлять aipager может только админ."
    query.edit_message_text.assert_not_awaited()
    update = message("/update", chat=GROUP, user=OWNER)
    run_async(update_flow.handle_update_cmd(bot, update, MagicMock()))
    assert _fa(update.message.reply_text.await_args.args[0])


def test_a_persian_stranger_is_refused_in_persian(bot, run_async):
    team_mod.reset_unauthorized_seen()
    try:
        update = message("hello", chat=777, user=777, language_code="fa")
        update.message.sender_chat = None
        update.message.from_user = update.effective_user
        update.effective_message = update.message
        assert run_async(bot._authorize(update)) is False
        text = update.message.reply_text.await_args.args[0]
        assert _fa(text) and "777" in text and "`aipager config`" in text
    finally:
        team_mod.reset_unauthorized_seen()
