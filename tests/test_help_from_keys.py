"""/help and the home screen built from keys (multilingual design 5.4,
phase 3a section D).

``handlers.HELP_TEXT`` stays the English a private chat reads. A group's
Talk line is /help's own key, whose English is pinned to
``group_intake.group_help_talk_line`` (sibling-owned): the same words in
English, translated with the rest of /help elsewhere."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import group_intake, handlers, report_flow
from tests import i18n_ml2

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")


def test_help_text_is_todays_english():
    assert handlers.HELP_TEXT == (
        "<b>How to use aipager</b>\n\n"
        "<b>Start:</b> /new name · /resume\n"
        "<b>Talk:</b> just type (it goes to the ✍️ session) · reply to a message"
        " · /&lt;label&gt; message\n"
        "<b>Control:</b> /stop · /now (send queued) · /clearqueue\n"
        "<b>Manage:</b> /status, then ⋮ (restart, rename, diff, end, delete)\n"
        "<b>Settings:</b> /settings · /mode (Ask, Auto or Full access)\n"
        "<b>Admin:</b> /update (aipager and Claude Code)\n\n"
        "<i>Tip: tapping a command sends it at once. Long-press it (phone) or "
        "press Tab (desktop) to add text first.</i>")


@pytest.mark.parametrize("username,line", [
    ("aipager_bot", "<b>Talk:</b> reply to a session's message, or mention @aipager_bot"
                    " · /&lt;label&gt; message"),
    ("a<b>&c", "<b>Talk:</b> reply to a session's message, or mention @a&lt;b&gt;&amp;c"
               " · /&lt;label&gt; message"),
    ("", "<b>Talk:</b> reply to a session's message, or mention the bot"
         " · /&lt;label&gt; message"),
])
def test_group_talk_line_is_group_intakes(mk_bot, username, line):
    """/help's group Talk line is ``group_intake.group_help_talk_line``
    (one key per sentence), with the English it always had."""
    bot = mk_bot()
    bot._app.bot.username = username
    assert group_intake.group_help_talk_line(bot) == line
    assert group_intake.group_help_talk_line(bot, tr=i18n.get("en")) == line


def _help(mk_bot, run_async, monkeypatch, chat, code, username="aipager_test_bot"):
    monkeypatch.setattr(report_flow, "is_owner", lambda *a, **k: False)
    preferences.set_ui_language(chat, code)
    bot = i18n_ml2.make_bot(mk_bot)
    bot._app.bot.username = username
    update = i18n_ml2.message("/help", chat=chat)
    run_async(bot._handle_help_cmd(update, MagicMock()))
    return update.message.reply_text.await_args.args[0]


def test_a_persian_group_help(mk_bot, run_async, monkeypatch):
    text = _help(mk_bot, run_async, monkeypatch, i18n_ml2.GROUP, "fa")
    talk = text.split("\n")[3]
    assert PERSIAN.search(talk) and "reply to a session" not in talk
    assert "\u2068@aipager_test_bot\u2069" in talk
    assert all(line.startswith("\u200f") for line in text.split("\n") if line)


def test_a_persian_group_help_without_a_username(mk_bot, run_async, monkeypatch):
    text = _help(mk_bot, run_async, monkeypatch, i18n_ml2.GROUP, "fa", username="")
    assert "the bot" not in text and PERSIAN.search(text.split("\n")[3])


def test_a_russian_private_help(mk_bot, run_async, monkeypatch):
    text = _help(mk_bot, run_async, monkeypatch, i18n_ml2.DM, "ru")
    assert CYRILLIC.search(text) and "How to use" not in text
    for command in ("/new", "/resume", "/stop", "/now", "/clearqueue", "/status",
                    "/settings", "/mode", "/update"):
        assert command in text, command


def test_an_english_group_help_is_todays(mk_bot, run_async, monkeypatch):
    text = _help(mk_bot, run_async, monkeypatch, i18n_ml2.GROUP, "en")
    bot = MagicMock()
    bot._app.bot.username = "aipager_test_bot"
    assert text == handlers.HELP_TEXT.replace(
        handlers.i18n.get("en").html(handlers.HELP_TALK_DM),
        group_intake.group_help_talk_line(bot))


def test_the_home_screen_in_persian(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(i18n_ml2.DM, "fa")
    bot = i18n_ml2.make_bot(mk_bot)
    bot._send_keyboard = AsyncMock()
    bot._read_status_file = lambda name: None
    run_async(bot._handle_start_cmd(i18n_ml2.message("/start"), MagicMock()))
    call = bot._app.bot.send_message.await_args
    text = call.args[1]
    assert PERSIAN.search(text) and "Start one with" not in text and "/new" in text
    labels = [b.text for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert all(PERSIAN.search(label) for label in labels)
