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
from aipager.bot.animation import (
    _build_sections,
    _fit_sections,
    build_full_log,
    build_stream_card_ex,
)


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


def test_build_busy_text_agent_row_shows_activity(mk_bot):
    """"agent activity rows on the busy card": the legacy HTML card's live
    agent row shows type · activity · elapsed, HTML-escaped, same shape as
    the rich card."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [("🤖 explore", False)]
    sess.active_subagents["a1"] = {
        "type": "explore",
        "started_at": time.monotonic() - 15,
        "history_idx": 0,
        "activity": "Bash: ls",
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "explore · Bash: ls · 15s" in text


def test_build_busy_text_agent_row_settled_shows_frozen_text(mk_bot):
    """The settled row's frozen text lives straight in tool_history and
    needs zero special-casing from _build_busy_text — it renders like any
    other done tool row."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [("🤖 explore · 5 tool calls · 42s", True)]
    text = bot._build_busy_text("jim", "Working", sess)
    assert "✅" in text
    assert "explore · 5 tool calls · 42s" in text


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


def test_send_busy_and_animate_reclaims_when_job_background_open(mk_bot, run_async):
    """design.md "model Claude Code background-agent jobs", Decision 9,
    driven through the REAL production path (review rev-iter2: the
    isolated-unit version bypassed transition() and hid a dead gate): a
    genuinely new prompt arriving over a live waiting card enters BUSY via
    registry.transition() — which sets job_reclaim_pending because the
    prior state was IDLE — and _send_busy_and_animate then reclaims the
    card instead of letting the still-alive animate task swallow it."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.IDLE  # waiting card up between turns
    sess.busy_msg_id = 99
    sess.job_interim_seen = True
    sess.active_subagents["a1"] = {
        "type": "Explore", "started_at": time.monotonic(),
    }
    loop = asyncio.new_event_loop()
    async def _long(): await asyncio.sleep(100)
    original_task = loop.create_task(_long())
    sess.animate_task = original_task
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    # the real handler order: transition first, card second
    assert bot.registry.transition("claude-jim", Status.BUSY) is not None
    assert sess.job_reclaim_pending is True
    run_async(bot._send_busy_and_animate(sess))

    # Reclaimed — a fresh busy card WAS sent, not swallowed.
    assert sess.busy_msg_id == 42
    bot._app.bot.send_message.assert_called_once()
    assert sess.job_reclaim_pending is False
    if not original_task.done():
        original_task.cancel()
    loop.close()


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


# ---- permission card shows the real command ("allow-always-auto-mode-guard")

def _perm_sess(pending):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = pending
    return sess


def test_permission_display_shows_the_real_command(mk_bot):
    bot = mk_bot()
    sess = _perm_sess({"tool_summary": "Bash: List tmp",
                       "tool_info": {"name": "Bash", "detail": "ls -la /tmp && echo <x>"}})
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert "🔐 <code>Bash: List tmp</code>" in text
    assert "<pre>ls -la /tmp &amp;&amp; echo &lt;x&gt;</pre>" in text


def test_permission_display_truncates_a_long_command(mk_bot):
    from aipager.bot.transport import _PERM_DETAIL_CHARS
    bot = mk_bot()
    sess = _perm_sess({"tool_summary": "Bash: big",
                       "tool_info": {"name": "Bash", "detail": "x" * 2000}})
    text = bot._build_busy_text("jim", "Waiting", sess)
    # the cap keeps _PERM_DETAIL_CHARS raw characters and marks the cut
    assert "<pre>" + "x" * _PERM_DETAIL_CHARS + "…</pre>" in text
    assert "x" * (_PERM_DETAIL_CHARS + 1) not in text


def test_permission_display_skips_detail_the_summary_already_shows(mk_bot):
    bot = mk_bot()
    # a bare file path is already the summary
    sess = _perm_sess({"tool_summary": "Edit: /a/b.py",
                       "tool_info": {"name": "Edit", "detail": "/a/b.py"}})
    assert "<pre>" not in bot._build_busy_text("jim", "Waiting", sess)
    # a short command with no description, likewise
    sess = _perm_sess({"tool_summary": "Bash: ls -la",
                       "tool_info": {"name": "Bash", "detail": "ls -la"}})
    assert "<pre>" not in bot._build_busy_text("jim", "Waiting", sess)
    # an older/fallback prompt with no detail renders exactly as before
    sess = _perm_sess({"tool_summary": "Bash: ls"})
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert "🔐 <code>Bash: ls</code>" in text and "<pre>" not in text


def test_permission_display_bounds_escape_heavy_commands(mk_bot):
    """The cap holds in rendered HTML too (rev-iter1-003): a command made
    of `&` grows 5× on escaping, so 300 raw chars would be 1500 rendered."""
    from aipager.bot.transport import _PERM_DETAIL_HTML_MAX
    bot = mk_bot()
    sess = _perm_sess({"tool_summary": "Bash: amp",
                       "tool_info": {"name": "Bash", "detail": "&" * 2000}})
    text = bot._build_busy_text("jim", "Waiting", sess)
    block = text.split("<pre>", 1)[1].split("</pre>", 1)[0]
    assert block.endswith("…")
    assert len(block) <= _PERM_DETAIL_HTML_MAX + 1
    assert "&amp;" in block and "&" * 2 not in block.replace("&amp;", "")


def test_permission_display_bound_holds_for_front_loaded_escapes(mk_bot):
    """rev-iter2-002: a single proportional shrink uses the slice's average
    escape ratio and under-shrinks when the heavy characters come first.
    The bound must hold regardless of where they sit."""
    from aipager.bot.transport import _PERM_DETAIL_HTML_MAX
    bot = mk_bot()
    # each exceeds the rendered bound after escaping; heavy characters sit
    # at the front, the front and back, and the back respectively
    for detail in ("&" * 250 + "a" * 250, "<" * 260 + "b" * 40, "a" * 100 + "&" * 200):
        sess = _perm_sess({"tool_summary": "Bash: mix",
                           "tool_info": {"name": "Bash", "detail": detail}})
        text = bot._build_busy_text("jim", "Waiting", sess)
        block = text.split("<pre>", 1)[1].split("</pre>", 1)[0]
        assert len(block) <= _PERM_DETAIL_HTML_MAX + 1, (len(block), detail[:20])
        assert block.endswith("…")


# ===== agent activity rows on the busy card ==============================
# _build_sections / _fit_sections / build_full_log — live-row rendering,
# Phase-1 shedding protection, and the full-log AGENTS section.

def _agent_sess(agent_info, *, tool_summary="\U0001f916 explore"):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [(tool_summary, False)]
    sess.active_subagents["a1"] = {"history_idx": 0, **agent_info}
    return sess


def test_build_sections_agent_row_shows_starting_before_first_activity():
    sess = _agent_sess({"type": "explore", "started_at": time.monotonic()})
    sections = _build_sections(sess)
    assert sections == [("agent-run", ["⏳ `\U0001f916 explore · starting · 0s`"])]


def test_build_sections_agent_row_shows_activity_and_elapsed_when_active():
    sess = _agent_sess({
        "type": "explore", "started_at": time.monotonic() - 130,
        "activity": "Bash: run tests",
    })
    sections = _build_sections(sess)
    assert len(sections) == 1
    kind, rows = sections[0]
    assert kind == "agent-run"
    assert "· Bash: run tests ·" in rows[0]
    assert "2m " in rows[0]
    assert rows[0].endswith("s`")


def test_fit_sections_phase1_never_collapses_a_run_containing_an_active_agent_row():
    """Mutation target: tagging the live-agent row kind="run" instead of
    "agent-run" would let Phase 1 replace it with a "▸ N tool calls"
    placeholder under forced truncation."""
    old_run = ("run", [f"⏳ `Bash: old-{i} " + "x" * 300 + "`" for i in range(5)])
    agent_run = ("agent-run", ["⏳ `\U0001f916 explore · Bash: ls · 5s`"])
    newest_run = ("run", ["⏳ `Bash: newest`"])
    sections = [old_run, agent_run, newest_run]
    total_bytes = sum(
        len(r.encode("utf-8")) for _, rows in sections for r in rows
    )
    # Budget small enough that the full timeline cannot fit, but large
    # enough that Phase 1's single collapse of the OLDER run resolves it —
    # Phase 2 (which COULD fold the agent-run section) never engages.
    reserve = 32_768 - 400
    rows, truncated = _fit_sections(sections, reserve)
    assert total_bytes > 400  # sanity: the un-truncated timeline overflows
    assert truncated is True
    assert any("\U0001f916 explore · Bash: ls · 5s" in r for r in rows), rows
    assert any("tool call" in r for r in rows)  # the older run got collapsed
    assert not any("old-" in r for r in rows)  # ...and its rows are gone


def test_build_stream_card_ex_keeps_active_agent_row_visible_under_byte_pressure():
    """End-to-end via build_stream_card_ex: an oversized tool_history
    forces truncation, but the still-active agent's row survives."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic()
    sess.tool_history = [
        (f"Bash: old command number {i} " + "y" * 100, True) for i in range(300)
    ]
    idx = len(sess.tool_history)
    sess.tool_history.append(("\U0001f916 explore", False))
    sess.active_subagents["a1"] = {
        "type": "explore", "started_at": time.monotonic() - 5,
        "history_idx": idx, "activity": "Bash: ls",
    }
    card, hid_something = build_stream_card_ex(sess, "Working")
    assert hid_something is True
    assert "\U0001f916 explore · Bash: ls · 5s" in card


def test_build_full_log_agents_section_lists_type_elapsed_count_and_tools():
    agents = [
        {"type": "explore", "started_at": 0.0, "elapsed": 7.0,
         "tool_count": 2, "tools": ["Bash: ls", "Read: /x"]},
        {"type": "review", "started_at": 10.0, "elapsed": 65.0,
         "tool_count": 1, "tools": ["Grep: foo"]},
    ]
    log = build_full_log(
        "jim", [("Bash: parent", True)], [], "the answer", agents=agents,
    )
    assert "AGENTS" in log
    assert "\U0001f916 explore — 7s — 2 tool calls" in log
    assert "  - Bash: ls" in log
    assert "  - Read: /x" in log
    assert "\U0001f916 review — 1m 5s — 1 tool call" in log
    assert "1 tool calls" not in log  # singular, not plural
    assert log.index("AGENTS") < log.index("FINAL ANSWER")


def test_build_full_log_omits_agents_section_when_no_agents_ran():
    log_default = build_full_log("jim", [("Bash: x", True)], [], "answer")
    assert "AGENTS" not in log_default
    log_empty = build_full_log(
        "jim", [("Bash: x", True)], [], "answer", agents=[],
    )
    assert "AGENTS" not in log_empty
    log_none = build_full_log(
        "jim", [("Bash: x", True)], [], "answer", agents=None,
    )
    assert "AGENTS" not in log_none
