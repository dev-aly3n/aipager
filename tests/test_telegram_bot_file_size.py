"""Tests for item 3.4 — file-too-big upfront warning in `_handle_file`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import telegram_bot as tb


def _mk_doc_update(file_size: int):
    update = MagicMock()
    msg = MagicMock()
    msg.document.file_size = file_size
    msg.document.file_name = "big.bin"
    msg.photo = None
    msg.reply_text = AsyncMock()
    update.message = msg
    return update


def _mk_photo_update(file_size: int):
    update = MagicMock()
    msg = MagicMock()
    msg.document = None
    photo = MagicMock()
    photo.file_size = file_size
    msg.photo = [photo]
    msg.reply_text = AsyncMock()
    update.message = msg
    return update


def test_document_over_limit_rejected_with_friendly_message(mk_bot, run_async):
    bot = mk_bot()
    over = tb.TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES + 1024 * 1024  # 1 MB over
    update = _mk_doc_update(file_size=over)
    run_async(bot._handle_file(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "20 MB" in text  # the limit
    assert "MB" in text  # the file size
    # Crucially: we did NOT attempt to download
    update.message.document.get_file.assert_not_called()


def test_photo_over_limit_rejected(mk_bot, run_async):
    bot = mk_bot()
    over = tb.TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES + 1
    update = _mk_photo_update(file_size=over)
    run_async(bot._handle_file(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()


def test_file_under_limit_proceeds_to_download(monkeypatch, tmp_path, mk_bot, run_async):
    """A file under the cap should NOT short-circuit; the existing
    download path runs (and we let it fail in the mock, which is fine
    for this test — we just want to assert the upfront check passed)."""
    bot = mk_bot()
    update = _mk_doc_update(file_size=1024)  # 1 KB
    # The download path will eventually call msg.document.get_file()
    # which we set up as a MagicMock; let it raise so we don't actually
    # touch the filesystem. The point is we GOT THERE — past the cap.
    update.message.document.get_file = AsyncMock(side_effect=RuntimeError("download stubbed"))
    run_async(bot._handle_file(update, MagicMock()))
    # reply_text was called with the generic download failure (which
    # is the existing behavior; the upfront check is what we're
    # verifying did NOT fire).
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to download file" in text
    assert "20 MB" not in text  # not the size-cap message


def test_no_file_size_attribute_skips_check(mk_bot, run_async):
    """If Telegram doesn't include file_size for some reason, we let
    the download attempt run (rather than block on an unknown size)."""
    bot = mk_bot()
    update = MagicMock()
    msg = MagicMock()
    msg.document.file_size = None  # unknown
    msg.document.file_name = "unknown.bin"
    msg.document.get_file = AsyncMock(side_effect=RuntimeError("stubbed"))
    msg.photo = None
    msg.reply_text = AsyncMock()
    update.message = msg
    run_async(bot._handle_file(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "20 MB" not in text  # didn't hit the cap path
