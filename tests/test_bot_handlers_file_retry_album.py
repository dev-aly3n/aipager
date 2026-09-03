"""``_handle_file`` reliability: bounded download retries and album
(media group) coalescing.

Observed 2026-09-03: two photos sent as one Telegram message with a
caption arrived as two Updates sharing a ``media_group_id``; one
download hit a ``TimedOut`` and was reported as a failure, the other
went out alone. These tests pin the two fixes:

- a transient network error is retried (``TimedOut``/``NetworkError``,
  never ``BadRequest`` — which SUBCLASSES ``NetworkError`` in PTB 22),
  and the eventual error names the file;
- every item of an album is collected and injected as ONE prompt after
  the settle timer, with a single note for any item that failed.

No real Telegram, dtach or claude: downloads are ``AsyncMock``s that
write into ``tmp_path`` so the collision suffix is exercised for real.
Backoff is driven through the module-level tuple, never by patching
``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest, NetworkError, TimedOut

from aipager.bot import handlers
from aipager.state import Status, TrackedSession

CHAT_ID = -1001


def _wire(bot, monkeypatch, tmp_path):
    """Idle session + mocked dtach + instant backoff. Returns (sess, sent)
    where ``sent`` is the ``send_text_and_enter`` mock — its second
    positional arg is the injected prompt."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr(handlers, "FILE_DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(handlers, "_DOWNLOAD_BACKOFF_SECONDS", (0, 0, 0))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    return sess, sent


class _Downloads:
    """A ``download_to_drive`` stand-in that really writes the file (so
    ``_unique_save_path`` sees the collision) and records every path in
    arrival order. ``write`` is a bound ``async def`` on purpose:
    ``AsyncMock`` only awaits a side_effect it recognises as a coroutine
    function, and an object with an async ``__call__`` is not one."""

    def __init__(self):
        self.written: list[str] = []

    async def write(self, custom_path, **_kw):
        Path(custom_path).write_bytes(b"jpeg")
        self.written.append(custom_path)


def _photo_update(mk_update, downloads, *, message_id=999, caption=None,
                  media_group_id=None, get_file_effects=None):
    """A photo message. ``get_file_effects`` is a side_effect list for
    ``get_file``; by default it resolves to a file whose download writes
    via ``downloads``."""
    update = mk_update("", message_id=message_id, chat_id=CHAT_ID)
    update.message.document = None
    photo = MagicMock()
    photo.file_size = 10_000
    tg_file = MagicMock(download_to_drive=AsyncMock(side_effect=downloads.write))
    photo.get_file = AsyncMock(
        side_effect=get_file_effects if get_file_effects is not None else None,
        return_value=tg_file,
    )
    update.message.photo = [photo]
    update.message.caption = caption
    update.message.media_group_id = media_group_id
    return update, photo, tg_file


def _doc_update(mk_update, downloads, *, file_name, message_id=999,
                caption=None, media_group_id=None, get_file_effects=None):
    update = mk_update("", message_id=message_id, chat_id=CHAT_ID)
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1_000
    doc.file_name = file_name
    tg_file = MagicMock(download_to_drive=AsyncMock(side_effect=downloads.write))
    doc.get_file = AsyncMock(
        side_effect=get_file_effects if get_file_effects is not None else None,
        return_value=tg_file,
    )
    update.message.document = doc
    update.message.caption = caption
    update.message.media_group_id = media_group_id
    return update, doc, tg_file


# ===== Retry ============================================================

def test_timed_out_twice_then_success_injects_once(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path, caplog,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, photo, tg_file = _photo_update(mk_update, downloads)
    photo.get_file.side_effect = [TimedOut("slow"), TimedOut("slow"), tg_file]

    with caplog.at_level(logging.INFO, logger="aipager.bot.handlers"):
        run_async(bot._handle_file(update, MagicMock()))

    assert photo.get_file.await_count == 3
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == f"Describe this image: {downloads.written[0]}"
    update.message.reply_text.assert_not_awaited()
    retries = [r.getMessage() for r in caplog.records
               if r.levelno == logging.INFO and "retrying" in r.getMessage()]
    assert len(retries) == 2
    assert "attempt 1/3" in retries[0] and "photo.jpg" in retries[0]
    assert "attempt 2/3" in retries[1]


def test_network_error_on_every_attempt_replies_once_naming_file(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, doc, tg_file = _doc_update(mk_update, downloads, file_name="report.pdf")
    # get_file succeeds; the byte transfer is what keeps dropping.
    tg_file.download_to_drive = AsyncMock(side_effect=NetworkError("reset"))

    run_async(bot._handle_file(update, MagicMock()))

    assert tg_file.download_to_drive.await_count == 3
    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "❌ Failed to download file" in text
    assert "report.pdf" in text


def test_bad_request_is_not_retried(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    """``BadRequest`` subclasses ``NetworkError`` in PTB 22 — it must still
    be treated as permanent: one attempt, then the error reply."""
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, photo, _tg_file = _photo_update(
        mk_update, downloads, get_file_effects=BadRequest("wrong file_id"))

    run_async(bot._handle_file(update, MagicMock()))

    assert photo.get_file.await_count == 1
    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "❌ Failed to download file" in text and "photo.jpg" in text


def test_download_calls_carry_a_long_read_timeout(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """A big file on a slow link should not need a retry at all — both
    halves of the download get the dedicated read timeout, not the 20s
    Application default."""
    bot = mk_bot()
    _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, photo, tg_file = _photo_update(mk_update, downloads)

    run_async(bot._handle_file(update, MagicMock()))

    assert photo.get_file.await_args.kwargs["read_timeout"] == handlers._DOWNLOAD_READ_TIMEOUT
    assert tg_file.download_to_drive.await_args.kwargs["read_timeout"] == handlers._DOWNLOAD_READ_TIMEOUT
    assert handlers._DOWNLOAD_READ_TIMEOUT > 20


# ===== Single file: unchanged shape =====================================

def test_single_photo_injects_immediately_with_existing_shape(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """No ``media_group_id`` → one download, one prompt, right away: no
    album entry, no settle timer, the same wording and side effects as
    before this change."""
    bot = mk_bot()
    sess, sent = _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, _photo, _tg_file = _photo_update(mk_update, downloads, caption=None)

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_awaited_once()
    assert sent.await_args.args[1] == f"Describe this image: {downloads.written[0]}"
    assert Path(downloads.written[0]).parent == tmp_path
    assert Path(downloads.written[0]).name.endswith("_photo.jpg")
    assert bot._albums == {}
    assert sess.status == Status.BUSY
    assert sess.trigger_msg_id == 999
    bot._react.assert_awaited_once_with(update, "👀")
    bot._send_busy_and_animate.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


def test_single_document_with_caption_keeps_caption_then_path(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    update, _doc, _tg_file = _doc_update(
        mk_update, downloads, file_name="notes.txt", caption="summarize")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_awaited_once()
    assert sent.await_args.args[1] == f"summarize {downloads.written[0]}"
    assert Path(downloads.written[0]).name.endswith("_notes.txt")


# ===== Albums ===========================================================

def test_album_of_three_becomes_one_prompt_after_settle(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    sess, sent = _wire(bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    ctx = MagicMock()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=101,
                             caption="compare these", media_group_id="g1")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=102, media_group_id="g1")
    u3, _, _ = _photo_update(mk_update, downloads, message_id=103, media_group_id="g1")

    async def scenario():
        for u in (u1, u2, u3):
            await bot._handle_file(u, ctx)
        # Every item is downloaded but nothing has gone out: the group is
        # parked with its settle timer armed.
        assert len(downloads.written) == 3
        sent.assert_not_awaited()
        album = bot._albums[(CHAT_ID, "g1")]
        assert album.pending == 0
        task = album.settle_task
        assert task is not None and not task.done()
        await task  # the settle timer fires
        return album

    album = run_async(scenario())

    sent.assert_awaited_once()
    prompt = sent.await_args.args[1]
    assert prompt == "compare these " + " ".join(downloads.written)
    # Three distinct files even though the names are second-resolution.
    assert len(set(downloads.written)) == 3
    assert all(Path(p).exists() for p in downloads.written)
    assert bot._albums == {}
    assert album.settle_task.done()
    # Reply-context / origin / reaction all hang off the FIRST item.
    assert sess.trigger_msg_id == 101
    bot._react.assert_awaited_once_with(u1, "👀")
    bot._send_busy_and_animate.assert_awaited_once()
    for u in (u1, u2, u3):
        u.message.reply_text.assert_not_awaited()


def test_album_caption_on_a_later_item_still_leads_the_prompt(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """The caption rides on whichever item Telegram attaches it to."""
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=201, media_group_id="g2")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=202,
                             caption="which is sharper?", media_group_id="g2")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g2")].settle_task

    run_async(scenario())

    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "which is sharper? " + " ".join(downloads.written)


def test_album_without_caption_uses_plural_default_wording(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=301, media_group_id="g3")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=302, media_group_id="g3")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g3")].settle_task

    run_async(scenario())

    assert sent.await_args.args[1] == "Describe these images: " + " ".join(downloads.written)


def test_album_with_one_failing_item_injects_the_rest_and_notes_it_once(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=401,
                             caption="look", media_group_id="g4")
    # The middle item times out on every attempt (backoff is 0 here, but
    # each asyncio.sleep still yields to the loop — the parked settle
    # timer from item 1 must not fire in those gaps).
    u2, p2, _ = _photo_update(mk_update, downloads, message_id=402, media_group_id="g4",
                              get_file_effects=TimedOut("slow"))
    u3, _, _ = _photo_update(mk_update, downloads, message_id=403, media_group_id="g4")

    async def scenario():
        for u in (u1, u2, u3):
            await bot._handle_file(u, MagicMock())
        sent.assert_not_awaited()
        await bot._albums[(CHAT_ID, "g4")].settle_task

    run_async(scenario())

    assert p2.get_file.await_count == 3
    sent.assert_awaited_once()
    assert len(downloads.written) == 2
    assert sent.await_args.args[1] == "look " + " ".join(downloads.written)
    # Exactly one failure note, on the album, naming the item.
    u1.message.reply_text.assert_awaited_once()
    text = u1.message.reply_text.await_args.args[0]
    assert "❌ Failed to download file" in text and "photo.jpg" in text
    u2.message.reply_text.assert_not_awaited()
    u3.message.reply_text.assert_not_awaited()
    assert bot._albums == {}


def test_album_where_every_item_fails_replies_once_without_injecting(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _sess, sent = _wire(bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _doc_update(mk_update, downloads, file_name="a.pdf", message_id=501,
                           media_group_id="g5", get_file_effects=NetworkError("x"))
    u2, _, _ = _doc_update(mk_update, downloads, file_name="b.pdf", message_id=502,
                           media_group_id="g5", get_file_effects=NetworkError("x"))

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g5")].settle_task

    run_async(scenario())

    sent.assert_not_awaited()
    u1.message.reply_text.assert_awaited_once()
    text = u1.message.reply_text.await_args.args[0]
    assert "a.pdf" in text and "b.pdf" in text
    u2.message.reply_text.assert_not_awaited()


def test_stale_album_is_dropped_on_the_next_arrival(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """A group that never settled (a lost Update, a dead timer) is bounded:
    the next album item sweeps anything idle for over the max age."""
    bot = mk_bot()
    _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    stale = handlers._PendingAlbum(
        key=(CHAT_ID, "old"), update=MagicMock(), ctx=MagicMock(),
        touched=time.monotonic() - handlers._ALBUM_MAX_AGE_SECONDS - 1,
    )
    bot._albums[stale.key] = stale
    u1, _, _ = _photo_update(mk_update, downloads, message_id=601, media_group_id="g6")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        assert (CHAT_ID, "old") not in bot._albums
        assert (CHAT_ID, "g6") in bot._albums
        task = bot._albums[(CHAT_ID, "g6")].settle_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run_async(scenario())


def test_new_album_item_cancels_the_armed_settle_timer(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _wire(bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=701, media_group_id="g7")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=702, media_group_id="g7")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        first = bot._albums[(CHAT_ID, "g7")].settle_task
        await bot._handle_file(u2, MagicMock())
        second = bot._albums[(CHAT_ID, "g7")].settle_task
        assert first is not second
        assert first.cancelled() or first.cancelling()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        assert first.cancelled()

    run_async(scenario())
