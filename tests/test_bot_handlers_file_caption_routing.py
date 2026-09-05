"""An upload's caption picks the session the way a text message does.

Observed 2026-09-05: an album captioned ``/aipager_boss check`` went to
whichever session was last active, and Claude Code received the literal
prompt ``/aipager_boss check <paths>`` — read as a slash command,
rejected with "Unknown command", and no hook fired. Text messages have
always split ``/<label> <prompt>`` into a direct send with the prefix
stripped; the lone-file and album paths did not.

These tests pin the caption rule for both paths: a caption whose first
token is ``/<label>`` routes to that session (registry first, then a
live ``claude-<label>`` by discovery) and the prompt is built from what
follows the label — the neutral ``check this:`` wording when nothing
does. A label nothing answers to is refused with the text path's reply
and nothing is injected. A caption that does not START with a slash is
untouched.

No real Telegram, dtach or claude — same fixtures as the album tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aipager.bot import handlers
from aipager.state import Status, TrackedSession
from tests.test_bot_handlers_file_retry_album import (
    CHAT_ID,
    _Downloads,
    _photo_update,
    _wire,
)


def _second_session(bot, label="ann"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    return sess


def _alive_only(monkeypatch, *names):
    """``inject.is_alive`` that answers True for ``names`` only, so a
    label can be made resolvable (or not) by discovery."""
    alive = AsyncMock(side_effect=lambda name: name in names)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", alive)
    return alive


# ===== /<label> alone: route + neutral wording ==========================

def test_lone_photo_captioned_with_label_routes_there_and_strips_prefix(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """``/ann`` on a photo while ``jim`` is the active session: the file
    goes to ann as ``check this: <path>`` — no slash reaches claude — and
    ann becomes the active session. jim is not touched."""
    bot = mk_bot()
    jim, sent = _wire(bot, monkeypatch, tmp_path)
    ann = _second_session(bot)
    downloads = _Downloads()
    update, _photo, _tg = _photo_update(mk_update, downloads, caption="/ann")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_awaited_once()
    assert sent.await_args.args[0] == "claude-ann"
    assert sent.await_args.args[1] == f"check this: {downloads.written[0]}"
    assert bot.registry.last_active_session == "claude-ann"
    assert ann.trigger_msg_id == 999
    assert ann.status == Status.BUSY
    assert jim.status == Status.IDLE and jim.trigger_msg_id is None
    bot._send_busy_and_animate.assert_awaited_once_with(ann)
    bot._react.assert_awaited_once_with(update, "👀")
    update.message.reply_text.assert_not_awaited()


# ===== /<label> <text>: route + caption text kept =======================

def test_album_captioned_with_label_and_text_routes_and_keeps_the_text(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """``/ann look`` riding on the SECOND item of a two-photo album: one
    prompt ``look <p1> <p2>`` into ann."""
    bot = mk_bot()
    _jim, sent = _wire(bot, monkeypatch, tmp_path)
    ann = _second_session(bot)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=101, media_group_id="g1")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=102,
                             caption="/ann look", media_group_id="g1")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g1")].settle_task

    run_async(scenario())

    sent.assert_awaited_once()
    assert sent.await_args.args[0] == "claude-ann"
    assert sent.await_args.args[1] == "look " + " ".join(downloads.written)
    assert len(downloads.written) == 2
    assert ann.trigger_msg_id == 101  # the album's first item, as before
    bot._send_busy_and_animate.assert_awaited_once_with(ann)


# ===== unknown label: refused, nothing injected =========================

def test_upload_captioned_with_unknown_label_is_refused(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """``/nosuch`` answers with the text path's reply; nothing is
    injected anywhere, no session goes BUSY, no card, and the download
    is left on disk."""
    bot = mk_bot()
    jim, sent = _wire(bot, monkeypatch, tmp_path)
    _alive_only(monkeypatch, "claude-jim")
    downloads = _Downloads()
    update, _photo, _tg = _photo_update(mk_update, downloads, caption="/nosuch")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("⚠️ Unknown session: nosuch")
    assert jim.status == Status.IDLE and jim.trigger_msg_id is None
    assert bot.registry.last_active_session == "claude-jim"
    bot._send_busy_and_animate.assert_not_awaited()
    bot._react.assert_not_awaited()
    assert Path(downloads.written[0]).exists()


def test_album_captioned_with_unknown_label_is_refused_once(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    bot = mk_bot()
    _jim, sent = _wire(bot, monkeypatch, tmp_path)
    _alive_only(monkeypatch, "claude-jim")
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1, _, _ = _photo_update(mk_update, downloads, message_id=201,
                             caption="/nosuch which?", media_group_id="g2")
    u2, _, _ = _photo_update(mk_update, downloads, message_id=202, media_group_id="g2")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g2")].settle_task

    run_async(scenario())

    sent.assert_not_awaited()
    u1.message.reply_text.assert_awaited_once_with("⚠️ Unknown session: nosuch")
    u2.message.reply_text.assert_not_awaited()
    bot._send_busy_and_animate.assert_not_awaited()


# ===== no leading slash: today's routing, verbatim caption ==============

def test_caption_with_a_slash_elsewhere_is_plain_text(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """Only the FIRST token routes. ``check /ann`` is a caption, goes to
    the active session verbatim, and ann is not involved."""
    bot = mk_bot()
    jim, sent = _wire(bot, monkeypatch, tmp_path)
    ann = _second_session(bot)
    downloads = _Downloads()
    update, _photo, _tg = _photo_update(mk_update, downloads, caption="check /ann")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_awaited_once()
    assert sent.await_args.args[0] == "claude-jim"
    assert sent.await_args.args[1] == f"check /ann {downloads.written[0]}"
    assert jim.status == Status.BUSY and ann.status == Status.IDLE
    assert bot.registry.last_active_session == "claude-jim"


# ===== label known only by discovery: adopted like a direct send ========

def test_label_alive_but_untracked_is_adopted_and_used(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """``/ann`` with no ``ann`` in the registry but a live ``claude-ann``
    socket: adopted under the typed label and injected, exactly as
    ``/ann hi`` does for text."""
    bot = mk_bot()
    _jim, sent = _wire(bot, monkeypatch, tmp_path)
    _alive_only(monkeypatch, "claude-jim", "claude-ann")
    assert bot.registry.get("claude-ann") is None
    downloads = _Downloads()
    update, _photo, _tg = _photo_update(mk_update, downloads, caption="/ann")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_awaited_once()
    assert sent.await_args.args[0] == "claude-ann"
    assert sent.await_args.args[1] == f"check this: {downloads.written[0]}"
    adopted = bot.registry.get("claude-ann")
    assert adopted is not None and adopted.label == "ann"
    assert adopted.status == Status.BUSY
    assert bot.registry.last_active_session == "claude-ann"
    update.message.reply_text.assert_not_awaited()


# ===== label tracked but its socket is dead: refused like a direct send ==

def test_label_tracked_but_dead_is_refused_with_not_alive(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """``/ann`` where ann is in the registry but ``claude-ann`` no longer
    answers: the direct-send wording, nothing injected, no card."""
    bot = mk_bot()
    jim, sent = _wire(bot, monkeypatch, tmp_path)
    ann = _second_session(bot)
    _alive_only(monkeypatch, "claude-jim")
    downloads = _Downloads()
    update, _photo, _tg = _photo_update(mk_update, downloads, caption="/ann")

    run_async(bot._handle_file(update, MagicMock()))

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("⚠️ [ann] session not alive")
    assert ann.status == Status.IDLE and jim.status == Status.IDLE
    bot._send_busy_and_animate.assert_not_awaited()
    assert bot.registry.last_active_session == "claude-jim"
