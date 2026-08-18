"""Tests for the live message stack's notify.py surface: the new
`compact_timeout` branch, and the `compacting`/`compact_done` branches'
push_compacting/pop_compacting bookkeeping (design.md "Live Message
Stack").
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


def _sess(label="jim", *, status=Status.BUSY, busy_msg_id=100):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    return s


# ===== compacting: push_compacting bookkeeping ==============================

def test_compacting_with_existing_busy_edits_in_place_no_new_send(mk_bot, run_async):
    """Criterion 2: edits the SAME message — no new send_message call —
    and busy_msg_id continues to return that same id."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_not_called()
    bot._edit_busy_raw.assert_awaited_once()
    edited_id = bot._edit_busy_raw.await_args.args[0]
    assert edited_id == 42
    assert sess.busy_msg_id == 42
    assert sess.stack_top_kind() == "compacting"


def test_compacting_with_no_busy_sends_exactly_one_new_message(mk_bot, run_async):
    """Criterion 3: no live card -> exactly one new message, busy_msg_id
    returns its id, and the stack holds a single compacting entry (no
    phantom busy entry underneath)."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 555
    assert sess.stack_top_kind() == "compacting"
    assert len(sess._live_stack) == 1  # no phantom busy entry underneath


def test_compacting_send_failure_does_not_push_a_stack_entry(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None


# ===== compact_done: pop_compacting bookkeeping ==============================

def test_compact_done_pops_the_compacting_entry_and_reveals_busy(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.push_compacting(42, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()

    # Scoped to notify's own constant. Patching "aipager.bot.notify.
    # asyncio.sleep" instead would reach the SHARED asyncio module and
    # unpace every other module's loops — that is what OOM-killed the box.
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))

    bot._edit_busy_raw.assert_awaited_once()
    edited_id = bot._edit_busy_raw.await_args.args[0]
    assert edited_id == 42  # same physical message throughout
    assert sess.stack_top_kind() == "busy"
    assert sess.busy_msg_id == 42


def test_compact_done_on_solo_compacting_entry_edits_that_same_message_no_second_send(
    mk_bot, run_async, monkeypatch,
):
    """The "nothing to restore" case (design.md Decision 4): compacting
    itself sent the only message. compact_done must edit THAT message,
    never send a second one (entrypoints.md's "exactly one existing
    message" invariant)."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.push_compacting(777, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    # Scoped to notify's own constant. Patching "aipager.bot.notify.
    # asyncio.sleep" instead would reach the SHARED asyncio module and
    # unpace every other module's loops — that is what OOM-killed the box.
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 0, "after_pct": 4}))

    bot._app.bot.send_message.assert_not_called()  # never a second physical message
    bot._edit_busy_raw.assert_awaited_once()
    assert bot._edit_busy_raw.await_args.args[0] == 777
    # Re-established tracking on the resolved message, matching pre-stack
    # behaviour where busy_msg_id stayed set after compact_done resolved it.
    assert sess.busy_msg_id == 777


def test_compact_done_pop_does_not_disturb_merged_reply_routing(mk_bot, run_async, monkeypatch):
    """Criterion 14: registry.track_message routing established when the
    busy card was first sent still resolves correctly after a full
    compacting -> compact_done cycle on the same physical message."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(42, sess.name, 0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()

    # Scoped to notify's own constant. Patching "aipager.bot.notify.
    # asyncio.sleep" instead would reach the SHARED asyncio module and
    # unpace every other module's loops — that is what OOM-killed the box.
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))

    resolved = bot.registry.get_session_by_msg(42, 0)
    assert resolved is not None
    assert resolved.name == sess.name


# ===== compact_timeout ========================================================

def test_compact_timeout_text_never_claims_success(mk_bot, run_async):
    """Criterion 6: text contains neither "Compacted" nor any other
    completion claim, and is textually distinct from compact_done's."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=3218)
    sess.push_compacting(3218, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))

    bot._edit_busy_raw.assert_awaited_once()
    edited_id, text = bot._edit_busy_raw.await_args.args[:2]
    assert edited_id == 3218
    assert "didn't confirm completion" in text
    assert "Compacted" not in text
    assert sess.label in text


def test_compact_timeout_busy_status_with_busy_beneath_resumes_animation(mk_bot, run_async):
    """Criterion 7: sweeper fires on a BUSY session with a busy layer
    beneath -> that layer's animation resumes."""
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=100)
    sess.push_compacting(100, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))

    bot._start_animation.assert_called_once()
    assert sess.busy_msg_id == 100
    assert sess.stack_top_kind() == "busy"


def test_compact_timeout_non_busy_status_clears_the_whole_stack(mk_bot, run_async):
    """Criterion 8: reproduces the exact observed bug state (status:
    idle, compacting card still spinning) -> stack ends up empty, no
    animation resumes."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=3218)
    sess.push_compacting(3218, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))

    bot._start_animation.assert_not_called()
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None


def test_compact_timeout_edit_permanent_failure_clears_stack(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=100)
    sess.push_compacting(100, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=None)  # permanent failure
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))

    bot._start_animation.assert_not_called()
    assert sess.busy_msg_id is None


def test_compact_timeout_on_already_popped_stack_is_a_safe_noop(mk_bot, run_async):
    """A compact_timeout firing after something else already cleared the
    stack (e.g. a race with SessionEnd) must not raise, and must not
    send/edit anything since there's no live message left to target."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    bot._edit_busy_raw = AsyncMock(return_value=True)

    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))

    bot._edit_busy_raw.assert_not_awaited()
    assert sess.busy_msg_id is None


# ===== criterion 18: deadline fires FIRST, real hook arrives SECOND ========

def test_late_hook_after_deadline_fire_still_ends_with_the_honest_compacted_text(
    mk_bot, run_async, monkeypatch,
):
    """The deadline can legitimately fire before a genuinely slow
    compaction's confirming hook arrives (COMPACT_CARD_TIMEOUT_SECONDS
    default 180s is short by design — see config.py). When the real hook
    then arrives, the SAME message is corrected to
    "Compacted: X% -> Y%", and pop_compacting() on the already-popped
    stack is a silent no-op. Neither call raises."""
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=100)
    sess.push_compacting(100, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    # 1. Deadline fires first.
    run_async(bot.notify(sess, "compact_timeout", {"elapsed_seconds": 190.0}))
    first_text = bot._edit_busy_raw.await_args.args[1]
    assert "didn't confirm completion" in first_text
    assert "Compacted" not in first_text
    # BUSY with a busy layer beneath -> resumed, per criterion 7.
    assert sess.busy_msg_id == 100
    assert sess.stack_top_kind() == "busy"

    # 2. The real, hook-confirmed compact_done arrives second — MUST NOT
    #    raise, and corrects the SAME message to the honest delta text.
    # Scoped to notify's own constant. Patching "aipager.bot.notify.
    # asyncio.sleep" instead would reach the SHARED asyncio module and
    # unpace every other module's loops — that is what OOM-killed the box.
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)
    bot._edit_busy_raw.reset_mock()
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))

    bot._edit_busy_raw.assert_awaited_once()
    second_id, second_text = bot._edit_busy_raw.await_args.args[:2]
    assert second_id == 100  # same physical message throughout
    assert "Compacted: 80% → 5%" in second_text
    # pop_compacting() on the already-popped stack was a silent no-op —
    # this compact_done call popped nothing new; the "busy" entry it
    # revealed is the SAME one criterion 7's resume already exposed.
    assert sess.stack_top_kind() == "busy"
    assert sess.busy_msg_id == 100
