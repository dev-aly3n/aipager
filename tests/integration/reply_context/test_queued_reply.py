"""Black-box integration test for design.md success criterion 10 -- a
reply to an older message sent while the target session is BUSY must
still produce a context block on the turn Claude actually receives.

Queue handoff (a later design.md) removed the BUSY-queues-in-aipager
behaviour this file originally exercised: a reply sent while BUSY now
injects immediately, exactly like every other status, and its
reply_context is carried on the per-message note `_inject_prompt`
writes rather than on a `pending_queue` tuple. This file was rewritten
to drive the same real `TelegramBotCore._handle_message` call against a
BUSY session and inspect the note the real code produced, closing the
same gap the original docstring described (proving the BUSY-time call
actually COMPUTES the string, not merely that some other layer's
default is empty) against the current behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

from .conftest import latest_note_reply_context

CHAT_ID = -1001
BOT_ID = 87654321


@pytest.fixture(autouse=True)
def _isolate_snapshot_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _reply_target(*, message_id, text="(old text)"):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = None
    m.from_user = None
    return m


def _wire_happy_dtach(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def test_reply_to_older_message_while_busy_queues_a_computed_reply_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """A reply sent while BUSY injects immediately (no aipager-side
    queueing) and its note must carry a non-empty, correctly-computed
    reply_context — not merely the raw text."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    bot.registry.track_message(1, sess.name, CHAT_ID)   # the older message
    bot.registry.track_message(999, sess.name, CHAT_ID)  # establishes "latest"
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re your earlier point", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="the busy-time target")
    run_async(bot._handle_message(update, _ctx()))

    assert sess.pending_queue == [], "BUSY no longer queues — it injects immediately"
    assert sess.status == Status.BUSY  # transition() is a no-op on an already-BUSY session

    reply_context = latest_note_reply_context(sess.name)
    assert reply_context is not None
    assert reply_context != ""
    assert "the busy-time target" in reply_context
    # Part 4: never a file-path clause while still BUSY (allow_file is
    # keyed on Status.BUSY, unchanged by queue handoff).
    assert "not retained" in reply_context or "claude-reply-" not in reply_context
    # Part 4's file-clobbering fix: no file written while BUSY.
    assert not ps.reply_context_path(sess.name).exists()


def test_queued_reply_context_survives_the_drain_into_the_real_turn(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """The note produced by a BUSY-time reply carries the SAME
    reply_context an idle-time reply would — proving the computation is
    identical regardless of status, now that both paths inject through
    the same `_inject_prompt` seam."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    bot.registry.track_message(1, sess.name, CHAT_ID)
    bot.registry.track_message(999, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("re your earlier point", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="the busy-time target")
    run_async(bot._handle_message(update, _ctx()))

    reply_context = latest_note_reply_context(sess.name)
    assert reply_context is not None
    assert reply_context != ""
    assert "the busy-time target" in reply_context


def test_queued_non_reply_message_while_busy_drains_with_empty_reply_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Negative control: an ordinary (non-reply) message sent while busy
    must carry an empty reply_context, not an accidentally carried-over
    one."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("just send this plain text", chat_id=CHAT_ID, message_id=2000)
    run_async(bot._handle_message(update, _ctx()))

    assert sess.pending_queue == []
    reply_context = latest_note_reply_context(sess.name)
    assert reply_context == ""


def test_oversized_highlight_while_busy_never_writes_a_fallback_file(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Part 4's file-clobbering fix, specifically for the highlighted-
    fragment oversized-fallback path (not just the whole-message path
    already covered above)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    bot.registry.track_message(1, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("about that bit", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="source")
    update.message.quote = MagicMock(text="z" * 1500, is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    reply_context = latest_note_reply_context(sess.name)
    assert reply_context is not None
    assert "…(truncated)" in reply_context
    assert not ps.reply_context_path(sess.name).exists()
