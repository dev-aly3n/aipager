"""Tests for the voice + file handlers in aipager.bot.handlers.

These are the heavy untested code paths in handlers.py:
- ``_handle_voice``: voice message → transcribe → inject as prompt
- ``_dispatch_voice_transcript``: routing for the transcribed text
- ``_handle_file``: photo/document upload + size guards
- ``_install_voice_extra``: subprocess pip install with progress
- ``_restart_daemon``: systemctl restart fallback chain
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from aipager.state import Status, TrackedSession


# ===== _handle_voice ====================================================

def test_handle_voice_no_voice_attribute_is_noop(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = None
    run_async(bot._handle_voice(update, MagicMock()))
    # No reply, no transcribe
    update.message.reply_text.assert_not_awaited()


def test_handle_voice_unavailable_offers_install_button(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=1000)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: False)
    run_async(bot._handle_voice(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "voice extra" in text
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "__voice__:install" in cb
    assert "__voice__:cancel" in cb


def test_handle_voice_oversized_rejects(mk_bot, mk_update, run_async, monkeypatch):
    from aipager.bot.transport import TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES + 1)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    run_async(bot._handle_voice(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "capped" in text or "MB" in text


def test_handle_voice_download_failure_reports(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=1000)
    update.message.voice.get_file = AsyncMock(side_effect=RuntimeError("io"))
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    run_async(bot._handle_voice(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to download" in text


def test_handle_voice_transcription_unavailable_shows_friendly(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=1000)
    fake_file = MagicMock()
    fake_file.download_to_drive = AsyncMock()
    update.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack_msg = MagicMock()
    ack_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack_msg)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    from aipager.bot import voice as voice_mod
    monkeypatch.setattr(voice_mod, "transcribe",
                        AsyncMock(side_effect=voice_mod.VoiceUnavailable("nope")))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    run_async(bot._handle_voice(update, MagicMock()))
    ack_msg.edit_text.assert_awaited()
    text = ack_msg.edit_text.await_args.args[0]
    assert "nope" in text


def test_handle_voice_empty_transcript_warns(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=1000)
    fake_file = MagicMock()
    fake_file.download_to_drive = AsyncMock()
    update.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack_msg = MagicMock()
    ack_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack_msg)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    monkeypatch.setattr("aipager.bot.voice.transcribe",
                        AsyncMock(return_value=""))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    run_async(bot._handle_voice(update, MagicMock()))
    text = ack_msg.edit_text.await_args.args[0]
    assert "Couldn't make out" in text


def test_handle_voice_happy_path_dispatches(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    update = mk_update("")
    update.message.voice = MagicMock(file_size=1000)
    fake_file = MagicMock()
    fake_file.download_to_drive = AsyncMock()
    update.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack_msg = MagicMock()
    ack_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack_msg)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    monkeypatch.setattr("aipager.bot.voice.transcribe",
                        AsyncMock(return_value="hello world"))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._dispatch_voice_transcript = AsyncMock()
    run_async(bot._handle_voice(update, MagicMock()))
    # Showed transcript and dispatched
    edit_text = ack_msg.edit_text.await_args.args[0]
    assert "hello world" in edit_text
    bot._dispatch_voice_transcript.assert_awaited_once()


# ===== _dispatch_voice_transcript =======================================

def test_dispatch_voice_no_session_warns(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("")
    run_async(bot._dispatch_voice_transcript(update, "hi"))
    text = update.message.reply_text.await_args.args[0]
    assert "no active session" in text


def test_dispatch_voice_dead_session_warns(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("")
    run_async(bot._dispatch_voice_transcript(update, "hi"))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_dispatch_voice_queued_when_busy(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._react = AsyncMock()
    update = mk_update("")
    run_async(bot._dispatch_voice_transcript(update, "hello"))
    assert any(t == "hello" for t, *_ in sess.pending_queue)


def test_dispatch_voice_injects_when_idle(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("")
    run_async(bot._dispatch_voice_transcript(update, "hello"))
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


def test_dispatch_voice_send_failure_reports(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update = mk_update("")
    run_async(bot._dispatch_voice_transcript(update, "hello"))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to inject" in text


# ===== _handle_file =====================================================

def test_handle_file_oversized_document_rejects(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    update = mk_update("")
    update.message.document = MagicMock()
    update.message.document.file_size = 50 * 1024 * 1024  # 50 MB
    update.message.document.file_name = "big.bin"
    update.message.photo = None
    run_async(bot._handle_file(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "too large" in text or "MB" in text


def test_handle_file_oversized_photo_rejects(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("")
    update.message.document = None
    big_photo = MagicMock()
    big_photo.file_size = 50 * 1024 * 1024
    update.message.photo = [big_photo]
    run_async(bot._handle_file(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "too large" in text or "MB" in text


def test_handle_file_no_file_attribute_returns(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("")
    update.message.document = None
    update.message.photo = []  # empty list
    # Reach the early return path
    run_async(bot._handle_file(update, MagicMock()))


def test_handle_file_photo_happy_path(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("")
    update.message.document = None
    photo = MagicMock()
    photo.file_size = 10000
    photo.get_file = AsyncMock(return_value=MagicMock(
        download_to_drive=AsyncMock(),
    ))
    update.message.photo = [photo]
    update.message.caption = None
    run_async(bot._handle_file(update, MagicMock()))
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


def test_handle_file_document_with_caption(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    sent_capture = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent_capture)
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("")
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_name = "report.txt"
    doc.get_file = AsyncMock(return_value=MagicMock(
        download_to_drive=AsyncMock(),
    ))
    update.message.document = doc
    update.message.caption = "Please summarize"
    run_async(bot._handle_file(update, MagicMock()))
    # The injected text should include the caption + file path
    prompt = sent_capture.await_args.args[1]
    assert "Please summarize" in prompt
    assert "report.txt" in prompt


def test_handle_file_download_failure_warns(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    update = mk_update("")
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_name = "x.bin"
    doc.get_file = AsyncMock(side_effect=RuntimeError("io"))
    update.message.document = doc
    update.message.caption = None
    run_async(bot._handle_file(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to download" in text


def test_handle_file_no_active_session_warns(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    update = mk_update("")
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_name = "x.bin"
    doc.get_file = AsyncMock(return_value=MagicMock(
        download_to_drive=AsyncMock(),
    ))
    update.message.document = doc
    update.message.caption = None
    run_async(bot._handle_file(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "don't know which session" in text


def test_handle_file_queues_when_busy(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._react = AsyncMock()
    update = mk_update("")
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_name = "x.bin"
    doc.get_file = AsyncMock(return_value=MagicMock(
        download_to_drive=AsyncMock(),
    ))
    update.message.document = doc
    update.message.caption = "look at this"
    run_async(bot._handle_file(update, MagicMock()))
    assert any("look at this" in t for t, *_ in sess.pending_queue)


# ===== _install_voice_extra =============================================

def test_install_voice_extra_success(mk_bot, run_async, monkeypatch):
    """Install succeeds → success message with restart button."""
    bot = mk_bot()
    query = MagicMock()
    bot._safe_edit_callback = AsyncMock()

    class _FakeStdout:
        async def read(self):
            return b"Successfully installed faster-whisper\n"

    async def _fake_create(*a, **k):
        proc = MagicMock()
        proc.returncode = 0
        proc.wait = AsyncMock(return_value=0)
        proc.stdout = _FakeStdout()
        return proc

    monkeypatch.setattr("aipager.bot.handlers.asyncio.create_subprocess_exec",
                        _fake_create)
    monkeypatch.setattr("aipager.updater._detect_installer", lambda: "pip")
    monkeypatch.setattr("aipager.updater.install_extra_cmd",
                        lambda inst, extra: ["pip", "install", "x"])
    run_async(bot._install_voice_extra(query))
    bot._safe_edit_callback.assert_awaited()
    # Final call shows success
    calls = [c.args[1] for c in bot._safe_edit_callback.await_args_list]
    assert any("Installed" in t for t in calls)


def test_install_voice_extra_failure(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    query = MagicMock()
    bot._safe_edit_callback = AsyncMock()

    class _FakeStdout:
        async def read(self):
            return b"pip: command not found\n"

    async def _fake_create(*a, **k):
        proc = MagicMock()
        proc.returncode = 127
        proc.wait = AsyncMock(return_value=127)
        proc.stdout = _FakeStdout()
        return proc

    monkeypatch.setattr("aipager.bot.handlers.asyncio.create_subprocess_exec",
                        _fake_create)
    monkeypatch.setattr("aipager.updater._detect_installer", lambda: "pip")
    monkeypatch.setattr("aipager.updater.install_extra_cmd",
                        lambda inst, extra: ["pip", "install", "x"])
    run_async(bot._install_voice_extra(query))
    calls = [c.args[1] for c in bot._safe_edit_callback.await_args_list]
    assert any("failed" in t.lower() for t in calls)


def test_install_voice_extra_no_recipe(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    query = MagicMock()
    bot._safe_edit_callback = AsyncMock()
    monkeypatch.setattr("aipager.updater._detect_installer", lambda: "pip")
    monkeypatch.setattr("aipager.updater.install_extra_cmd",
                        lambda inst, extra: None)
    run_async(bot._install_voice_extra(query))
    text = bot._safe_edit_callback.await_args.args[1]
    assert "no installer recipe" in text


def test_install_voice_extra_subprocess_spawn_failure(mk_bot, run_async, monkeypatch):
    """Regression: the except clause used to reference asyncio.SubprocessError
    which doesn't exist, causing an AttributeError before the handler ran."""
    bot = mk_bot()
    query = MagicMock()
    bot._safe_edit_callback = AsyncMock()

    async def _boom(*a, **k):
        raise OSError("ENOENT")

    monkeypatch.setattr("aipager.bot.handlers.asyncio.create_subprocess_exec",
                        _boom)
    monkeypatch.setattr("aipager.updater._detect_installer", lambda: "pip")
    monkeypatch.setattr("aipager.updater.install_extra_cmd",
                        lambda inst, extra: ["pip", "install", "x"])
    run_async(bot._install_voice_extra(query))
    calls = [c.args[1] for c in bot._safe_edit_callback.await_args_list]
    assert any("Couldn't start" in t for t in calls)


def test_install_voice_extra_subprocess_error_swallowed(mk_bot, run_async, monkeypatch):
    """`subprocess.SubprocessError` (the real one) is also caught."""
    bot = mk_bot()
    query = MagicMock()
    bot._safe_edit_callback = AsyncMock()

    import subprocess
    async def _boom(*a, **k):
        raise subprocess.SubprocessError("bad")

    monkeypatch.setattr("aipager.bot.handlers.asyncio.create_subprocess_exec",
                        _boom)
    monkeypatch.setattr("aipager.updater._detect_installer", lambda: "pip")
    monkeypatch.setattr("aipager.updater.install_extra_cmd",
                        lambda inst, extra: ["pip", "install", "x"])
    run_async(bot._install_voice_extra(query))
    calls = [c.args[1] for c in bot._safe_edit_callback.await_args_list]
    assert any("Couldn't start" in t for t in calls)
