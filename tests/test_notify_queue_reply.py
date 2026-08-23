"""Tests for design.md Part 4 — the queued-reply gap.

A reply to an old message, sent while the target session is BUSY,
must still carry its reply-pointer context once the queue drains —
not just in the ``pending_queue`` tuple, but in the actual turn Claude
receives (the policy snapshot the ``UserPromptSubmit`` hook reads).
Criterion 10 explicitly warns that asserting on the queue tuple alone
proves nothing; every test here reads back
``policy_snapshot.read_snapshot(...)["reply_context"]`` after a drain.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession


def _sess(label="jim", status=Status.IDLE, *, busy_msg_id=None, scope_chat_id=0):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    s.scope_chat_id = scope_chat_id
    return s


@pytest.fixture(autouse=True)
def _mock_send_rich_message(monkeypatch):
    """Same isolation test_bot_notify_idle.py's module fixture applies —
    only the PTB send_message path (header) fires."""
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(return_value={}),
    )


def _isolate_snapshot(monkeypatch, tmp_path):
    # Both paths — see test_bot_handlers_reply_context.py for why.
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")


def _latest_note_reply_context(session_name: str) -> str | None:
    """The reply_context carried by the drain's own note (queue handoff,
    design.md) — the drain still calls ``_inject_prompt``, which now
    writes a per-message note instead of the canonical snapshot
    directly, so this replaces ``ps.read_snapshot(name)["reply_context"]``
    as "what this drained turn resolved reply_context to"."""
    notes = ps.list_outstanding_notes(session_name)
    if not notes:
        return None
    return notes[-1].get("reply_context", "")


def _drive_idle_drain(bot, sess, run_async, monkeypatch):
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))


def test_queued_reply_context_reaches_the_drained_turns_snapshot(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """Criterion 10 — assert on the actual drained turn's snapshot, not
    on the queue tuple."""
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    sess = _sess()
    sess.queue_prompt(
        "queued reply text", 100,
        "The user's message is a reply to an earlier message in this "
        "session (sent 21:40, by you)...",
    )
    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    ctx = _latest_note_reply_context(sess.name)
    assert ctx is not None
    assert ctx == (
        "The user's message is a reply to an earlier message in this "
        "session (sent 21:40, by you)..."
    )


def test_queued_non_reply_prompt_drains_with_empty_reply_context(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    sess = _sess()
    sess.queue_prompt("just a normal queued message", 100)  # reply_context="" default
    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    ctx = _latest_note_reply_context(sess.name)
    assert ctx is not None
    assert ctx == ""


def test_queued_trigger_gets_tracked_at_drain_time(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """Part 4: queued messages are never tracked at queue time — the
    drain must track the queued trigger so a reply to it is routable
    immediately, not only once the next bot message re-tracks by
    coincidence."""
    bot = mk_bot()
    _isolate_snapshot(monkeypatch, tmp_path)
    sess = _sess(scope_chat_id=4242)
    bot.registry._sessions[sess.name] = sess
    sess.queue_prompt("queued text", 777)
    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    resolved = bot.registry.get_session_by_msg(777, 4242)
    assert resolved is not None
    assert resolved.name == sess.name


def test_queued_trigger_none_does_not_crash_and_is_not_tracked(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """msg_id is None for a queued prompt with no originating Telegram
    message (the Mini App's Compact route) — the drain must skip
    track_message rather than pass None as a msg_id."""
    bot = mk_bot()
    _isolate_snapshot(monkeypatch, tmp_path)
    sess = _sess(scope_chat_id=4242)
    bot.registry._sessions[sess.name] = sess
    sess.queue_prompt("/compact", None)
    _drive_idle_drain(bot, sess, run_async, monkeypatch)  # must not raise
    assert sess.pending_queue == []


def test_queue_drain_widens_a_pre_part4_3_tuple_entry_without_crashing(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """A queue entry that predates this feature (loaded from an older
    state file as a 3-tuple, upgraded to a 5-tuple with reply_context=""
    and driver_user_id=None by state.load()) must drain cleanly.

    See test_queue_drain_attribution.py for the driver_user_id half of
    this coverage (review-2#rev-iter2-001) — this test only re-confirms
    the pre-existing reply_context widening still drains without error
    now that the tuple has grown a 5th slot.
    """
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    sess = _sess()
    sess.pending_queue.append(
        ("legacy queued text", 100, time.time(), "", None)
    )
    _drive_idle_drain(bot, sess, run_async, monkeypatch)
    ctx = _latest_note_reply_context(sess.name)
    assert ctx is not None
    assert ctx == ""
