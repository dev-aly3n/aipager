"""Black-box integration test for design.md success criterion 10 -- a
reply to an older message sent while the target session is BUSY must
still produce a context block on the turn Claude actually receives once
the queue drains.

The developer's own ``tests/test_notify_queue_reply.py`` already covers
the drain->snapshot leg, but it hand-seeds ``sess.pending_queue`` with a
pre-rendered string via ``sess.queue_prompt(...)`` -- it never proves the
BUSY branch of the real handler actually COMPUTES that string in the
first place. This file closes that gap: it drives a real
``TelegramBotCore._handle_message`` call against a BUSY session (so the
message is genuinely queued, not injected), inspects the queue entry the
real code produced, then drains it and reads the final snapshot -- the
full round trip end to end.
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


def _drive_idle_drain(bot, sess, run_async, monkeypatch):
    """Mirrors tests/test_notify_queue_reply.py's helper exactly (same
    module, same convention) -- transitions the session to idle and lets
    the daemon's queue-drain path pick up the next queued prompt."""
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))


def test_reply_to_older_message_while_busy_queues_a_computed_reply_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """First half: the real BUSY-branch handler call must compute and
    queue a non-empty reply_context -- not merely queue the raw text."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    bot.registry.track_message(1, sess.name, CHAT_ID)   # the older message
    bot.registry.track_message(999, sess.name, CHAT_ID)  # establishes "latest"
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._react = AsyncMock()

    update = mk_update("re your earlier point", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="the busy-time target")
    run_async(bot._handle_message(update, _ctx()))

    assert len(sess.pending_queue) == 1
    text, msg_id, ts, reply_context = sess.pending_queue[0]
    assert text == "re your earlier point"
    assert reply_context != ""
    assert "the busy-time target" in reply_context
    # Part 4: never a file-path clause while queued.
    assert "not retained" in reply_context or "claude-reply-" not in reply_context
    # Part 4's file-clobbering fix: no file written at QUEUE time.
    assert not ps.reply_context_path(sess.name).exists()


def test_queued_reply_context_survives_the_drain_into_the_real_turn(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Second half: drain the queue the real BUSY call populated and read
    back the ACTUAL turn Claude receives (criterion 10's explicit
    warning: asserting on the queue tuple alone proves nothing)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    bot.registry.track_message(1, sess.name, CHAT_ID)
    bot.registry.track_message(999, sess.name, CHAT_ID)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._react = AsyncMock()

    update = mk_update("re your earlier point", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="the busy-time target")
    run_async(bot._handle_message(update, _ctx()))
    queued_reply_context = sess.pending_queue[0][3]
    assert queued_reply_context != ""

    # The busy turn Claude was working on has now finished (a separate
    # code path transitions status before the "idle_prompt" notify event
    # fires and drains the queue -- matching tests/test_notify_queue_reply.py's
    # own convention of an already-IDLE session at drain time).
    sess.status = Status.IDLE
    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] == queued_reply_context
    assert "the busy-time target" in snap["reply_context"]


def test_queued_non_reply_message_while_busy_drains_with_empty_reply_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Negative control: an ordinary (non-reply) message queued while
    busy must drain with an empty reply_context, not an accidentally
    carried-over one."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._react = AsyncMock()

    update = mk_update("just queue this plain text", chat_id=CHAT_ID, message_id=2000)
    run_async(bot._handle_message(update, _ctx()))
    assert sess.pending_queue[0][3] == ""

    sess.status = Status.IDLE
    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] == ""


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
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._react = AsyncMock()

    update = mk_update("about that bit", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="source")
    update.message.quote = MagicMock(text="z" * 1500, is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    reply_context = sess.pending_queue[0][3]
    assert "…(truncated)" in reply_context
    assert not ps.reply_context_path(sess.name).exists()
