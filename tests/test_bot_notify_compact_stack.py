"""Tests for the live message stack's notify.py surface: the new
`compact_timeout` branch, and the `compacting`/`compact_done` branches'
push_compacting/pop_compacting bookkeeping (design.md "Live Message
Stack").
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def test_compacting_with_no_busy_records_busy_card_trigger(mk_bot, run_async):
    """review-1 rev-iter1-002 (design.md "turn anchor follows
    consumption"): this branch bypasses send_busy, so it must record
    busy_card_trigger itself — otherwise this genuinely live,
    correctly-anchored card reads as "never seeded" against the very
    next re-anchor decision, and a real absorption after this point
    would silently never move it."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.trigger_msg_id = 9
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_card_trigger == 9


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


def test_compact_done_on_solo_compacting_entry_records_busy_card_trigger(
    mk_bot, run_async, monkeypatch,
):
    """review-1 rev-iter1-002: the "nothing to restore" re-establish
    branch must also seed busy_card_trigger for the message it just
    re-adopted — otherwise a later re-anchor decision against this
    genuinely live card reads a stale/None busy_card_trigger and
    silently never fires."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.trigger_msg_id = 9
    sess.push_compacting(777, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()

    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 0, "after_pct": 4}))

    assert sess.busy_msg_id == 777
    assert sess.busy_card_trigger == 9


def test_compact_done_with_nothing_live_sends_fresh_and_records_busy_card_trigger(
    mk_bot, run_async, monkeypatch,
):
    """review-1 rev-iter1-002: the fully-empty-stack fresh-send branch
    (a duplicate/late compact_done fire after everything else already
    cleared) must also seed busy_card_trigger for the message it just
    sent."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.trigger_msg_id = 9
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=321))
    bot._start_animation = MagicMock()

    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 0, "after_pct": 4}))

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 321
    assert sess.busy_card_trigger == 9


# ===== compact_done between turns: the message is not a card ================

def _idle_compaction(bot, run_async, monkeypatch, sess):
    """Run a compaction Claude Code started between turns: compacting
    sends its own message (555), compact_done resolves it."""
    sess.trigger_msg_id = 9
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    bot._app.bot.delete_message = AsyncMock()
    bot._animate_compact = AsyncMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_msg_id == 555
    run_async(bot.notify(sess, "compact_done", {"before_pct": 20, "after_pct": 3}))


def test_idle_compaction_leaves_compacted_as_the_message_last_word(
    mk_bot, run_async, monkeypatch,
):
    """A compaction between turns ends on its "Compacted" text, and
    nothing tracks the message as a busy card: the orphan sweeper must
    never find it, or it paints the last finished turn's card into it
    (that turn's done card a second time)."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    _idle_compaction(bot, run_async, monkeypatch, sess)

    edited_id, text = bot._edit_busy_raw.await_args.args[:2]
    assert edited_id == 555
    assert "Compacted: 20% → 3%" in text
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None
    bot._start_animation.assert_not_called()
    assert sess.last_token_pct == 3
    assert not sess.card_orphaned(time.monotonic() + 3600)
    run_async(bot._settle_orphan_card(sess))
    bot._edit_busy_rich.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()


def test_idle_compaction_with_no_message_sends_one_it_does_not_track(
    mk_bot, run_async, monkeypatch,
):
    """compacting could not send: compact_done still shows the result,
    as a message nothing tracks."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=321))
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 20, "after_pct": 3}))

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id is None
    bot._start_animation.assert_not_called()
    assert not sess.card_orphaned(time.monotonic() + 3600)


def test_turn_started_during_idle_compact_done_keeps_its_own_card(
    mk_bot, run_async, monkeypatch,
):
    """A turn that opens its card while compact_done edits the message
    keeps that card: compact_done neither takes the slot back for the
    compaction message nor touches the new card's animation."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    sess.trigger_msg_id = 9
    sess.push_compacting(555, time.monotonic(), deadline_seconds=180.0)
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    async def _edit(msg_id, text, **kwargs):
        sess.status = Status.BUSY
        sess.trigger_msg_id = 10
        sess.busy_msg_id = 777  # the new turn's card, as send_busy claims it
        return True

    bot._edit_busy_raw = AsyncMock(side_effect=_edit)
    run_async(bot.notify(sess, "compact_done", {"before_pct": 20, "after_pct": 3}))

    assert sess.busy_msg_id == 777
    assert sess.stack_top_kind() == "busy"
    bot._start_animation.assert_not_called()


@pytest.mark.parametrize("owner", ["busy", "interactive", "job_open", "finishing"])
def test_compaction_a_turn_still_owns_resumes_its_card(
    mk_bot, run_async, monkeypatch, owner,
):
    """While a turn (or its background job, or its finish) is still on,
    the compaction message is that turn's card: re-tracked, re-anchored
    and animated again, as before."""
    bot = mk_bot()
    status = {"busy": Status.BUSY, "interactive": Status.INTERACTIVE}.get(
        owner, Status.IDLE)
    sess = _sess(status=status, busy_msg_id=None)
    if owner == "job_open":
        sess.active_subagents["a1"] = {"type": "pipeline-runner"}
    if owner == "finishing":
        sess.finish_gate = asyncio.Event()
    _idle_compaction(bot, run_async, monkeypatch, sess)

    assert sess.busy_msg_id == 555
    assert sess.stack_top_kind() == "busy"
    assert sess.busy_card_trigger == 9
    bot._start_animation.assert_called_once()


def test_absorption_after_compaction_bypass_still_reanchors(
    mk_bot, run_async, monkeypatch,
):
    """review-1 rev-iter1-002, end to end: no card is live, "compacting"
    sends a fresh one (a bypass site, never send_busy), "compact_done"
    resolves it in place, and only THEN does a message get absorbed
    mid-turn. Before this fix, none of the compaction bypass sites
    recorded busy_card_trigger, so this exact sequence left it at None
    forever and the later absorption never re-anchored — the same
    failure mode a `busy_card_trigger is not None` guard would also
    have produced. Proves the fix, not just the guard's removal."""
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=None)
    sess.trigger_msg_id = 1
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_card_trigger == 1

    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))
    assert sess.busy_msg_id == 555
    assert sess.busy_card_trigger == 1  # still correct after compact_done

    # A message gets absorbed mid-turn — the target (and the card) must
    # still move.
    sess.trigger_msg_id = 2
    sess.stream_consumed_notes = [{"msg_id": 2, "chat_id": 555, "raw_text": "x"}]
    bot.registry.track_message = MagicMock()
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    bot._reanchor_busy_card.assert_awaited_once_with(sess, 2, final=False)


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


def test_idle_compaction_over_a_tracked_card_keeps_tracking_it(
    mk_bot, run_async, monkeypatch,
):
    """A card still tracked underneath the compaction (a restored card in
    its adoption window, or one waiting for its settle) stays tracked:
    whoever owns it settles it, as before."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=100)
    sess.push_compacting(100, time.monotonic(), deadline_seconds=180.0)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    run_async(bot.notify(sess, "compact_done", {"before_pct": 20, "after_pct": 3}))

    assert bot._edit_busy_raw.await_args.args[0] == 100
    assert sess.busy_msg_id == 100
    assert sess.stack_top_kind() == "busy"
    bot._start_animation.assert_called_once()


def test_idle_compact_done_lands_after_a_card_edit_already_out(
    mk_bot, run_async, monkeypatch,
):
    """A card edit already holding the card-edit lock (a phantom
    SubagentStop's "Working" repaint of the compaction message) lands
    before the "Compacted" text, never over it: that repaint would leave
    a Stop button nothing tracks any more."""
    from aipager.bot.animation import _card_edit_lock

    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    sess.push_compacting(555, time.monotonic(), deadline_seconds=180.0)
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)
    order = []

    async def _edit(msg_id, text, **kwargs):
        order.append("compacted")
        return True

    bot._edit_busy_raw = AsyncMock(side_effect=_edit)

    async def scenario():
        lock = _card_edit_lock(sess)
        await lock.acquire()
        done = asyncio.ensure_future(bot.notify(
            sess, "compact_done", {"before_pct": 20, "after_pct": 3}))
        for _ in range(20):
            await asyncio.sleep(0)
        order.append("working")
        lock.release()
        await done

    run_async(scenario())

    assert order == ["working", "compacted"]
    assert sess.busy_msg_id is None
