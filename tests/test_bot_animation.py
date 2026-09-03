"""Tests for aipager.bot.animation.AnimationMixin.

Targets the spinner / busy-message lifecycle: send_busy, _build_busy_text,
_edit_busy_raw, _start_animation, _stop_animation, _animate_compact,
_send_busy_and_animate, _safe_edit_callback.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


# ===== _safe_edit_callback ===============================================

def test_safe_edit_callback_swallows_error(mk_bot, run_async):
    bot = mk_bot()
    query = MagicMock()
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("nope"))
    # MUST NOT raise
    run_async(bot._safe_edit_callback(query, "hi"))


def test_safe_edit_callback_passes_parse_mode(mk_bot, run_async):
    bot = mk_bot()
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    run_async(bot._safe_edit_callback(query, "hi", parse_mode="HTML"))
    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.kwargs.get("parse_mode") == "HTML"


# ===== send_busy =========================================================

def test_send_busy_returns_message_id(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    msg_id = run_async(bot.send_busy(sess))
    assert msg_id == 42


def test_send_busy_no_app_returns_none(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    sess = TrackedSession(name="claude-jim", label="jim")
    assert run_async(bot.send_busy(sess)) is None


def test_send_busy_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    assert run_async(bot.send_busy(sess)) is None


# ===== _fmt_tokens =======================================================

@pytest.mark.parametrize("n,expected", [
    (0, "0"),
    (500, "500"),
    (999, "999"),
    (1_000, "1.0k"),
    (1_500, "1.5k"),
    (15_000, "15.0k"),
    (99_999, "100.0k"),
    (100_000, "100k"),
    (150_000, "150k"),
])
def test_fmt_tokens(mk_bot, n, expected):
    bot = mk_bot()
    assert bot._fmt_tokens(n) == expected


# ===== _build_busy_text ==================================================

def test_build_busy_text_basic(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    text = bot._build_busy_text("jim", "Thinking", sess)
    assert "jim" in text
    assert "Thinking" in text


def test_build_busy_text_elapsed_appears_after_2s(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 10
    text = bot._build_busy_text("jim", "Thinking", sess)
    assert "10s" in text or "s" in text


def test_build_busy_text_with_cost_delta(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.42
    text = bot._build_busy_text("jim", "Working", sess)
    assert "$0.42" in text


def test_build_busy_text_with_subagent_count(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.10
    sess.subagent_count_this_turn = 3
    text = bot._build_busy_text("jim", "Working", sess)
    assert "3 agents" in text


def test_build_busy_text_with_one_subagent_singular(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.10
    sess.subagent_count_this_turn = 1
    text = bot._build_busy_text("jim", "Working", sess)
    assert "1 agent)" in text  # singular


def test_build_busy_text_with_tool_history(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [
        ("Bash: ls", True),
        ("Read: /x", False),
        ("Edit: /y", "failed"),
    ]
    text = bot._build_busy_text("jim", "Working", sess)
    assert "Bash" in text and "✅" in text
    assert "Read" in text and "⏳" in text
    assert "Edit" in text and "❌" in text


def test_build_busy_text_collapses_long_history(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    # 20 done tools + 5 in-progress
    sess.tool_history = [
        (f"done{i}", True) for i in range(20)
    ] + [(f"todo{i}", False) for i in range(5)]
    text = bot._build_busy_text("jim", "Working", sess)
    # Last 15 are visible; first 10 collapsed into "10 earlier tools"
    assert "earlier tool" in text


def test_build_busy_text_with_subagent_elapsed(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [("🤖 explore", False)]
    sess.active_subagents["a1"] = {
        "type": "explore",
        "started_at": time.monotonic() - 15,
        "history_idx": 0,
    }
    text = bot._build_busy_text("jim", "Working", sess)
    # Subagent elapsed time shown
    assert "15s" in text or "m" in text


def test_build_busy_text_with_inline_permission_ask(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = {
        "ask_question": True,
        "question": "Pick one",
        "options": [{"label": "A"}, {"label": "B"}],
    }
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert "Pick one" in text
    assert "1." in text


def test_build_busy_text_with_inline_permission_tool(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = {
        "ask_question": False,
        "tool_summary": "Bash: ls",
    }
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert "🔐" in text
    assert "Bash" in text


# ===== _edit_busy_raw ====================================================

def test_edit_busy_raw_no_app(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    assert run_async(bot._edit_busy_raw(42, "text")) is False


def test_edit_busy_raw_success(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    assert run_async(bot._edit_busy_raw(42, "text")) is True


def test_edit_busy_raw_not_modified_returns_true(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock(
        side_effect=RuntimeError("Bad Request: message is not modified"),
    )
    assert run_async(bot._edit_busy_raw(42, "text")) is True


def test_edit_busy_raw_message_not_found_returns_none(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock(
        side_effect=RuntimeError("Bad Request: message to edit not found"),
    )
    assert run_async(bot._edit_busy_raw(42, "text")) is None


def test_edit_busy_raw_transient_returns_false(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock(
        side_effect=RuntimeError("Rate limit"),
    )
    assert run_async(bot._edit_busy_raw(42, "text")) is False


# ===== _start_animation / _stop_animation ===============================

def test_stop_animation_cancels_running_task(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    loop = asyncio.new_event_loop()
    async def _long():
        await asyncio.sleep(100)
    sess.animate_task = loop.create_task(_long())
    bot._stop_animation(sess)
    assert sess.animate_task is None
    loop.close()


def test_stop_animation_no_task_is_noop(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.animate_task = None
    bot._stop_animation(sess)  # MUST NOT raise


def test_start_animation_creates_task(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42

    async def _go():
        bot._start_animation(sess)
        # Immediately cancel so we don't actually loop
        bot._stop_animation(sess)

    run_async(_go())


# ===== _animate_compact ==================================================

def test_animate_compact_loops_dot_then_message_gone(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    # _animate_compact is tied to the stack: production only starts it from
    # notify's "compacting" branch, which pushes this entry first.
    sess.push_compacting(42, time.monotonic(), deadline_seconds=None)

    calls = []
    async def _no_sleep(_):
        # After first iteration, simulate message being gone
        if len(calls) >= 1:
            sess.busy_msg_id = -1

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    edit_calls = []
    async def _edit(msg_id, text, **k):
        edit_calls.append(text)
        calls.append(1)
        return True
    monkeypatch.setattr(bot, "_edit_busy_raw", _edit)
    run_async(bot._animate_compact(sess))
    assert any("Compacting" in t for t in edit_calls)


def test_animate_compact_handles_message_gone(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.push_compacting(42, time.monotonic(), deadline_seconds=None)

    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    monkeypatch.setattr(bot, "_edit_busy_raw",
                        AsyncMock(return_value=None))  # message gone
    run_async(bot._animate_compact(sess))
    assert sess.busy_msg_id is None


# ===== _send_busy_and_animate ============================================

def test_send_busy_and_animate_happy(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.busy_msg_id == 42
    bot._start_animation.assert_called_once()


def test_send_busy_and_animate_clears_stale_state(mk_bot, run_async):
    """If busy_msg_id is set but animation is dead, clear and resend."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 99  # leftover from previous cycle
    # Set animate_task to a done task
    loop = asyncio.new_event_loop()
    async def _done(): return None
    sess.animate_task = loop.create_task(_done())
    loop.run_until_complete(sess.animate_task)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    # Fresh msg_id picked up
    assert sess.busy_msg_id == 42
    loop.close()


def test_send_busy_and_animate_skips_when_already_busy(mk_bot, run_async):
    """If busy_msg_id is set and animation is alive, skip."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 99
    # Live animate_task (just a sleeping coroutine)
    loop = asyncio.new_event_loop()
    async def _long(): await asyncio.sleep(100)
    sess.animate_task = loop.create_task(_long())
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    run_async(bot._send_busy_and_animate(sess))
    # busy_msg_id NOT replaced
    assert sess.busy_msg_id == 99
    bot._app.bot.send_message.assert_not_called()
    sess.animate_task.cancel()
    loop.close()


def test_send_busy_and_animate_send_failure_clears_sentinel(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    run_async(bot._send_busy_and_animate(sess))
    # Sentinel cleared back to None on failure
    assert sess.busy_msg_id is None


_INTERIM = "interim answer text"
# A single commentary block far over the 32 768-byte card ceiling: the
# final render clips it and reports last_card_truncated=True.
_INTERIM_HUGE = (_INTERIM + " ") * 2500


def _reclaim_setup(mk_bot, *, commentary=None):
    """The exact state the reclaim branch sees, built through the REAL
    production path (review rev-iter2: an isolated-unit version bypassed
    transition() and hid a dead gate): a session parked on a live waiting
    card (busy_msg_id=99, one background agent open, the old job's tool
    rows and commentary still in memory, the animate task still alive),
    then a genuinely new prompt entering BUSY via registry.transition() —
    which sets job_reclaim_pending because the prior state was IDLE.

    Returns ``(bot, sess, seen, original_task, loop)``. ``seen`` is the
    ordered log of outbound calls — ("edit", msg_id, markdown, kwargs,
    animate_task-at-edit-time, last_card_truncated-at-edit-time),
    ("doc", kwargs) and ("send", text) — recorded by mocks the caller may
    still replace. Callers must cancel ``original_task`` and close
    ``loop``."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.IDLE  # waiting card up between turns
    sess.busy_msg_id = 99
    sess.job_interim_seen = True
    sess.busy_started_at = time.monotonic() - 5
    sess.tool_history = [("Read: /a", True), ("Grep: x in y", True)]
    sess.stream_commentary = list(
        commentary if commentary is not None else [(2, _INTERIM)],
    )
    sess.active_subagents["a1"] = {
        "type": "Explore", "started_at": time.monotonic(),
    }
    loop = asyncio.new_event_loop()
    async def _long(): await asyncio.sleep(100)
    original_task = loop.create_task(_long())
    sess.animate_task = original_task

    seen: list[tuple] = []

    async def _send(chat_id, text, **kw):
        seen.append(("send", text))
        return MagicMock(message_id=42)

    async def _doc(chat_id, **kw):
        seen.append(("doc", kw))
        return MagicMock(message_id=43)

    bot._app.bot.send_message = AsyncMock(side_effect=_send)
    bot._app.bot.send_document = AsyncMock(side_effect=_doc)
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    # the real handler order: transition first, card second
    assert bot.registry.transition("claude-jim", Status.BUSY) is not None
    assert sess.job_reclaim_pending is True
    return bot, sess, seen, original_task, loop


def _recording_edit(sess, seen, *, result=None, raises=None):
    """A stand-in for aipager.bot.animation.edit_message_text_rich that
    records what _edit_busy_rich sent and the session state at that
    moment (the old animate task must already be stopped, and the
    renderer's truncation verdict is what the attachment rule reads)."""
    async def _edit(chat_id, msg_id, markdown, **kw):
        seen.append((
            "edit", msg_id, markdown, kw,
            sess.animate_task, sess.last_card_truncated,
        ))
        if raises is not None:
            raise raises
        return {} if result is None else result
    return _edit


def _teardown(original_task, loop):
    if not original_task.done():
        original_task.cancel()
    loop.close()


def test_send_busy_and_animate_reclaims_when_job_background_open(
    mk_bot, run_async, monkeypatch,
):
    """design.md "model Claude Code background-agent jobs", Decision 9:
    a genuinely new prompt arriving over a live waiting card is reclaimed
    instead of being swallowed by the still-alive animate task — and the
    superseded card is SETTLED in place first, not left frozen reading
    "still working" under a live Stop button (the 2026-09-03 card 3502
    report). The old message gets one final render — ✅ status line, no
    keyboard, the interim prose still inside it — with the old animate
    task already stopped, THEN the fresh card goes out. Nothing else is
    sent for the superseded job."""
    bot, sess, seen, original_task, loop = _reclaim_setup(mk_bot)
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen),
    )

    run_async(bot._send_busy_and_animate(sess))

    # Settle the old card FIRST, then send the fresh one — nothing else.
    assert [c[0] for c in seen] == ["edit", "send"]
    _, msg_id, markdown, kw, task_at_edit, _ = seen[0]
    assert msg_id == 99
    assert markdown.splitlines()[-1].startswith("✅")
    assert "still working" not in markdown
    assert kw["reply_markup"] is None  # Stop button comes off
    assert _INTERIM in markdown  # the interim prose stays in the card
    # The old animate task was stopped BEFORE the final edit, so it cannot
    # wake mid-POST and re-arm the Stop button over the settled card.
    assert task_at_edit is None
    # No composed answer message for the superseded job — the one send is
    # the NEW turn's busy card.
    bot._app.bot.send_message.assert_awaited_once()
    assert _INTERIM not in seen[1][1]
    bot._app.bot.send_document.assert_not_awaited()  # nothing was hidden
    # Reclaimed — a fresh busy card WAS sent, not swallowed.
    assert sess.busy_msg_id == 42
    assert sess.job_reclaim_pending is False
    _teardown(original_task, loop)


def test_reclaim_final_edit_raising_still_sends_fresh_card(
    mk_bot, run_async, monkeypatch,
):
    """Best-effort: an exception out of the final render must not block
    the new turn's card."""
    bot, sess, seen, original_task, loop = _reclaim_setup(mk_bot)
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen, raises=RuntimeError("edit exploded")),
    )

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "send"]
    assert sess.busy_msg_id == 42
    bot._app.bot.send_document.assert_not_awaited()
    _teardown(original_task, loop)


def test_reclaim_final_edit_transient_failure_still_sends_fresh_card(
    mk_bot, run_async, monkeypatch,
):
    """A transient edit failure (edit_message_text_rich → None, which
    _edit_busy_rich reports as False) is logged and skipped the same
    way."""
    bot, sess, seen, original_task, loop = _reclaim_setup(mk_bot)

    async def _edit_none(chat_id, msg_id, markdown, **kw):
        seen.append(("edit", msg_id, markdown, kw, None, None))
        return None
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich", _edit_none,
    )

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "send"]
    assert sess.busy_msg_id == 42
    _teardown(original_task, loop)


def test_reclaim_truncated_card_attaches_full_log_under_old_card(
    mk_bot, run_async, monkeypatch,
):
    """When the final render had to hide anything, the complete
    play-by-play goes out as {label}_full_log.txt threaded under the OLD
    card (the same last_card_truncated rule as the idle close) — before
    the fresh card, and as the only extra message."""
    bot, sess, seen, original_task, loop = _reclaim_setup(
        mk_bot, commentary=[(2, _INTERIM_HUGE)],
    )
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen),
    )

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "doc", "send"]
    assert seen[0][5] is True  # the renderer reported truncation
    doc_kw = seen[1][1]
    assert doc_kw["filename"] == "jim_full_log.txt"
    assert doc_kw["reply_to_message_id"] == 99
    body = doc_kw["document"].decode("utf-8")
    assert "jim — complete play-by-play" in body
    assert "[v] Read: /a" in body
    assert _INTERIM_HUGE.strip() in body  # the full prose, unclipped
    assert "FINAL ANSWER" not in body  # no composed answer exists
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 42
    _teardown(original_task, loop)


def test_reclaim_full_log_skipped_when_old_card_is_gone(
    mk_bot, run_async, monkeypatch,
):
    """A permanent edit failure (message deleted) leaves nothing to
    thread the attachment under — no document, fresh card still sent."""
    from aipager.bot.rich_message import RichMessageGone

    bot, sess, seen, original_task, loop = _reclaim_setup(
        mk_bot, commentary=[(2, _INTERIM_HUGE)],
    )
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen, raises=RichMessageGone("deleted")),
    )

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "send"]
    bot._app.bot.send_document.assert_not_awaited()
    assert sess.busy_msg_id == 42
    _teardown(original_task, loop)


def test_reclaim_full_log_over_doc_limit_not_attached(
    mk_bot, run_async, monkeypatch,
):
    """Mirror of the idle close: a play-by-play over Telegram's document
    ceiling is dropped with a warning, never sent."""
    bot, sess, seen, original_task, loop = _reclaim_setup(
        mk_bot, commentary=[(2, _INTERIM_HUGE)],
    )
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen),
    )
    monkeypatch.setattr("aipager.bot.animation.TELEGRAM_MAX_DOC_BYTES", 100)

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "send"]
    bot._app.bot.send_document.assert_not_awaited()
    assert sess.busy_msg_id == 42
    _teardown(original_task, loop)


def test_reclaim_full_log_send_failure_still_sends_fresh_card(
    mk_bot, run_async, monkeypatch,
):
    """Best-effort: a failed attachment send must not block the new
    turn's card."""
    bot, sess, seen, original_task, loop = _reclaim_setup(
        mk_bot, commentary=[(2, _INTERIM_HUGE)],
    )
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        _recording_edit(sess, seen),
    )
    bot._app.bot.send_document = AsyncMock(side_effect=RuntimeError("io"))

    run_async(bot._send_busy_and_animate(sess))

    assert [c[0] for c in seen] == ["edit", "send"]
    bot._app.bot.send_document.assert_awaited_once()
    assert sess.busy_msg_id == 42
    _teardown(original_task, loop)


def test_send_busy_and_animate_still_skips_when_no_job_open(mk_bot, run_async):
    """Unchanged behaviour: with no background job open, the original
    "already showing busy" race guard still applies."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 99
    loop = asyncio.new_event_loop()
    async def _long(): await asyncio.sleep(100)
    sess.animate_task = loop.create_task(_long())
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    run_async(bot._send_busy_and_animate(sess))
    assert sess.busy_msg_id == 99
    bot._app.bot.send_message.assert_not_called()
    sess.animate_task.cancel()
    loop.close()


# ===== _animate_busy ======================================================

def test_animate_busy_keeps_ticking_while_job_background_open(mk_bot, run_async):
    """The loop condition widens from status == BUSY to status == BUSY or
    job_background_open() (design.md "model Claude Code background-agent
    jobs") — proven by observing the task is STILL RUNNING (blocked in its
    sleep, not returned) shortly after being scheduled, while status is
    IDLE but a background agent is still open.

    Not implemented via ``asyncio.wait_for(..., timeout=...)`` expecting
    ``TimeoutError``: ``_animate_busy`` catches ``asyncio.CancelledError``
    itself (``except asyncio.CancelledError: pass``), which swallows
    ``wait_for``'s own cancellation-on-timeout signal and makes it return
    normally instead of raising — unrelated to whether the loop condition
    even holds. Scheduling the coroutine as a bare ``Task`` and checking
    ``.done()`` directly sidesteps that. No ``asyncio.sleep`` patching
    (forbidden by spec.md).
    """
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.active_subagents["a1"] = {
        "type": "Explore", "started_at": time.monotonic(),
    }

    async def _run() -> bool:
        task = asyncio.ensure_future(bot._animate_busy(sess))
        await asyncio.sleep(0.05)
        still_running = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return still_running

    assert run_async(_run()) is True


def test_animate_busy_returns_immediately_when_idle_and_no_job_open(mk_bot, run_async):
    """Unchanged behaviour: with no background job open, the loop
    condition is False on entry and the coroutine returns right away."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 42

    async def _run():
        # Must NOT raise TimeoutError — the coroutine returns instantly.
        await asyncio.wait_for(bot._animate_busy(sess), timeout=0.05)

    run_async(_run())


def test_animate_busy_tick_computes_waiting_frame_when_not_busy(mk_bot, run_async):
    """Each tick computes waiting = status != BUSY, forwarded to
    _edit_busy_rich, and skips the typing indicator while genuinely idle.
    Real-time bounded to the animator's fixed ~1.5s first-tick delay
    rather than patching asyncio.sleep (forbidden by spec.md)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.active_subagents["a1"] = {
        "type": "Explore", "started_at": time.monotonic(),
    }
    # None → "permanent failure" → the loop breaks after exactly one tick.
    bot._edit_busy_rich = AsyncMock(return_value=None)
    bot._app.bot.send_chat_action = AsyncMock()

    async def _run():
        await asyncio.wait_for(bot._animate_busy(sess), timeout=3.0)

    run_async(_run())
    bot._edit_busy_rich.assert_awaited_once()
    assert bot._edit_busy_rich.await_args.kwargs.get("waiting") is True
    bot._app.bot.send_chat_action.assert_not_called()  # no typing while idle



def test_send_busy_and_animate_no_reclaim_mid_continuation(mk_bot, run_async):
    """Review rev-iter1-001 / rev-iter2: an ordinary Telegram message
    arriving while the continuation turn is RUNNING is a same-state
    BUSY→BUSY transition no-op — job_reclaim_pending is never set — so the
    card path gives it the "already showing busy" no-op any busy turn
    gets, never a reclaim that wipes the continuation state."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.BUSY
    sess.busy_msg_id = 99
    sess.job_continuation_active = True
    sess.job_interim_seen = True
    loop = asyncio.new_event_loop()
    async def _long(): await asyncio.sleep(100)
    original_task = loop.create_task(_long())
    sess.animate_task = original_task
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=43))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    # the real handler order: transition (same-state no-op), card second
    assert bot.registry.transition("claude-jim", Status.BUSY) is None
    assert sess.job_reclaim_pending is False
    run_async(bot._send_busy_and_animate(sess))

    # Swallowed by the race guard — no fresh card, continuation state intact.
    assert sess.busy_msg_id == 99
    bot._app.bot.send_message.assert_not_called()
    assert sess.job_continuation_active is True
    assert sess.job_interim_seen is True
    if not original_task.done():
        original_task.cancel()
    loop.close()
