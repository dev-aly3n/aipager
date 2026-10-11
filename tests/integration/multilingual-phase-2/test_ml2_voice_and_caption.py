"""In a Persian chat, a voice note and an upload whose caption starts with
/x1 route as they always did (multilingual phase 2, section 7.5): nothing
in routing reads the chat's language."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import preferences
from aipager.state import Status
from tests.i18n_ml2 import DM, add_session, message


def test_a_voice_note_routes_in_a_persian_chat(ml2_bot, run_async, monkeypatch, tmp_path):
    preferences.set_ui_language(DM, "fa")
    update = message("", chat=DM)
    update.message.voice = MagicMock(file_size=1000)
    fake_file = MagicMock(download_to_drive=AsyncMock())
    update.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack = MagicMock(edit_text=AsyncMock())
    update.message.reply_text = AsyncMock(return_value=ack)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    monkeypatch.setattr("aipager.bot.voice.transcribe", AsyncMock(return_value="سلام دنیا"))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    ml2_bot._dispatch_voice_transcript = AsyncMock()
    run_async(ml2_bot._handle_voice(update, MagicMock()))
    assert ml2_bot._dispatch_voice_transcript.await_args.args[1] == "سلام دنیا"


def test_a_caption_starting_with_the_session_routes_there(ml2_bot, run_async, monkeypatch,
                                                         tmp_path):
    preferences.set_ui_language(DM, "fa")
    x1 = add_session(ml2_bot, label="x1")
    other = add_session(ml2_bot, label="x2")
    ml2_bot.registry.last_active_session = other.name
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    ml2_bot._send_busy_and_animate = AsyncMock()
    ml2_bot._react = AsyncMock()
    update = message("", chat=DM)
    update.message.text = None
    update.message.photo = []
    doc = MagicMock(file_size=1000, file_name="report.txt")
    doc.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))
    update.message.document = doc
    update.message.caption = "/x1 خلاصه کن"
    run_async(ml2_bot._handle_file(update, MagicMock()))
    name, prompt = sent.await_args.args[:2]
    assert name == x1.name and "خلاصه کن" in prompt and "report.txt" in prompt
    assert x1.status == Status.BUSY
