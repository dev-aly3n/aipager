"""Black-box integration tests for design.md success criteria 2, 4, and 5
(the "pointer" content, delivered end to end), plus wiring checks that the
same pointer mechanism reaches the two other reply-bearing handlers
entrypoints.md documents (``_dispatch_voice_transcript``, ``_handle_file``),
not only ``_handle_message``.

All assertions read back through the documented response-side surfaces:
``policy_snapshot.read_snapshot``/``reply_context_path``. No internal
``_build_reply_context``/``_resolve_reply_target`` call is made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

CHAT_ID = -1001
BOT_ID = 87654321


@pytest.fixture(autouse=True)
def _isolate_snapshot_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


def _idle_sess(name="claude-jim", label="jim", **kw):
    s = TrackedSession(name=name, label=label, status=Status.IDLE)
    s.scope_chat_id = kw.pop("scope_chat_id", CHAT_ID)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _wire_happy_dtach(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def _reply_target(*, message_id, text="(old text)", caption=None, from_user=None):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = caption
    m.from_user = from_user
    return m


def _track_older_then_latest(bot, sess, older_id=1, latest_id=999):
    """Registers ``sess`` and tracks TWO messages in chronological order so
    ``sess.last_msg_id`` ends up ``latest_id`` -- ``track_message`` itself
    sets ``last_msg_id`` to whatever id it is last called with (it is the
    same call the bot uses when it sends a message), so tracking only the
    older id and then separately assigning ``sess.last_msg_id`` by hand
    would be silently clobbered back to the older id. Tracking both, in
    order, mirrors how this actually happens in production: message 1 was
    sent, then message 999 was sent later, and 1 remains resolvable via
    the message map even though it is no longer "the latest"."""
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(older_id, sess.name, CHAT_ID)
    bot.registry.track_message(latest_id, sess.name, CHAT_ID)
    assert sess.last_msg_id == latest_id  # sanity: setup actually landed
    return sess


# ===== Criterion 2: reply to an OLDER message ===============================

def test_reply_to_older_message_produces_locator_capped_excerpt_and_file(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    full_text = "a" * 150  # > 80-char excerpt cap
    sess = _track_older_then_latest(bot, _idle_sess())  # reply target (1) is NOT the latest
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re your earlier point", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text=full_text)
    run_async(bot._handle_message(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    ctx = snap["reply_context"]
    assert ctx != ""
    # Excerpt capped at 80 chars -- the 81st char of the run must not appear.
    assert ("a" * 81) not in ctx
    assert ("a" * 80) in ctx

    p = ps.reply_context_path(sess.name)
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"
    # The file may carry a locator header ahead of the text (the dev's own
    # policy_snapshot unit tests confirm this via substring checks) -- what
    # criterion 2 promises is that the FULL text is present, not that the
    # file is byte-for-byte only the text.
    assert full_text in p.read_text()


def test_reply_to_older_message_full_text_file_capped_at_4000_chars(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    long_text = "b" * 6000
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re your earlier point", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text=long_text)
    run_async(bot._handle_message(update, _ctx()))

    content = ps.reply_context_path(sess.name).read_text()
    # The 4000-char cap applies to the replied-to TEXT itself (a locator
    # header may be prepended on top, per design.md's ``full_text=text
    # [:4000]``) -- assert the cap on the text, not on total file size.
    assert ("b" * 4000) in content
    assert ("b" * 4001) not in content


# ===== Criterion 4: oversized highlight truncated, fallback file written ===

def test_oversized_highlight_truncated_with_marker_and_fallback_file(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    long_fragment = "z" * 1500
    update = mk_update("about that bit", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="source msg")
    update.message.quote = MagicMock(text=long_fragment, is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    ctx = snap["reply_context"]
    assert "…(truncated)" in ctx
    assert ("z" * 1001) not in ctx  # never leaks past the cap inline

    # Not queued (session was IDLE) -> the oversized-fragment fallback file
    # IS written.
    assert ps.reply_context_path(sess.name).exists()


def test_small_highlight_never_writes_a_fallback_file(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("about that", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="source msg")
    update.message.quote = MagicMock(text="a short fragment", is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    assert not ps.reply_context_path(sess.name).exists()


# ===== Criterion 5: server-added quote (is_manual=False) wording ===========

def test_quote_is_manual_false_never_says_highlighted(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re that", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="source msg")
    update.message.quote = MagicMock(text="a fragment", is_manual=False)
    run_async(bot._handle_message(update, _ctx()))

    ctx = ps.read_snapshot(sess.name)["reply_context"]
    assert ctx != ""
    assert "highlighted" not in ctx
    assert "highlighting" not in ctx


def test_quote_is_manual_true_says_highlighting(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Positive control for the test above: a genuine manual selection
    DOES use "highlighting" wording, proving the assertion above is
    discriminating on ``is_manual`` and not merely on wording that's
    absent everywhere."""
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re that", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="source msg")
    update.message.quote = MagicMock(text="a fragment", is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    ctx = ps.read_snapshot(sess.name)["reply_context"]
    assert "highlighting" in ctx


# ===== Wiring: the same pointer mechanism reaches the OTHER two ============
# ===== reply-bearing handlers (entrypoints.md) ==============================

def test_dispatch_voice_transcript_carries_reply_context_too(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """``_dispatch_voice_transcript`` shares the reply-shape contract per
    entrypoints.md. This proves the wiring reaches it too, not only the
    text handler (a plausible place for an incomplete rollout to miss)."""
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="the old voice target")
    run_async(bot._dispatch_voice_transcript(update, "a transcribed voice reply"))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] != ""
    assert "the old voice target" in snap["reply_context"]


def test_handle_file_carries_reply_context_too(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """Same for ``_handle_file`` (photo/document uploads)."""
    bot = mk_bot()
    sess = _track_older_then_latest(bot, _idle_sess())
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()

    update = mk_update("", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=1, text="the old file target")
    update.message.photo = []
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_name = "notes.txt"
    doc.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))
    update.message.document = doc
    update.message.caption = "see attached"
    run_async(bot._handle_file(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] != ""
    assert "the old file target" in snap["reply_context"]
