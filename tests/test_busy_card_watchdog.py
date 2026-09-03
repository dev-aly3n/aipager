"""Busy-card staleness watchdog: a live card can never sit frozen while
the session is BUSY.

Three layers, one contract:

* ``session_monitor.busy_card_watchdog_action`` (pure) decides per scan:
  restart a dead animate task, force one refresh of a card no task has
  edited in ``CARD_STALE_SECONDS``, or leave it alone.
* ``AnimationMixin._watchdog_busy_card`` carries the decision out through
  the ordinary start path / ``_edit_busy_rich``.
* ``_animate_busy`` itself survives a tick that raises, and the
  ``tool_use`` notify path resumes an animation a terminal-answered
  permission prompt left stopped — the primary INTERACTIVE → BUSY resume
  alongside the Telegram-button path in callbacks.py, which is pinned
  here too.

No ``asyncio.sleep`` / ``create_task`` patching: the animator's delays are
module constants (``FIRST_TICK_DELAY``, ``STREAM_EDIT_INTERVAL``) and the
tests shrink those instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import animation
from aipager.session_monitor import (
    CARD_STALE_SECONDS,
    INTERACTIVE_TIMEOUT_SECONDS,
    SessionMonitor,
    busy_card_watchdog_action,
)
from aipager.state import SessionRegistry, Status, TrackedSession


async def _coroutine_returning(value):
    return value


def _live_task() -> MagicMock:
    task = MagicMock()
    task.done.return_value = False
    return task


def _dead_task() -> MagicMock:
    task = MagicMock()
    task.done.return_value = True
    return task


def _session(
    now: float, *, status: Status = Status.BUSY, msg_id: int | None = 42,
    task: str = "live", edited_ago: float = 1.0,
) -> TrackedSession:
    """A session mid-turn with a busy card, ``edited_ago`` seconds since
    its last successful rich edit and an animate task in the given state
    (``"live"`` / ``"dead"`` / ``"none"``)."""
    sess = TrackedSession(name="claude-jim", label="jim", status=status)
    sess.busy_msg_id = msg_id
    sess.busy_started_at = now - 120
    sess.last_hook_at = now - 1
    sess.last_tool_edit_at = now - edited_ago
    sess.animate_task = {
        "live": _live_task(), "dead": _dead_task(), "none": None,
    }[task]
    return sess


def _scan(registry: SessionRegistry, monkeypatch, run_async):
    """Run one monitor scan over ``registry`` with the dtach socket list
    pinned to the registry's sessions, recording every notify call."""
    calls: list[tuple[str, dict]] = []

    async def _notify(sess, event, ctx):
        calls.append((event, dict(ctx)))

    monitor = SessionMonitor(registry, _notify)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(list(registry._sessions)),
    )
    run_async(monitor._scan())
    return calls


def _watchdog_calls(calls):
    return [ctx for event, ctx in calls if event == "busy_card_watchdog"]


# ===== session monitor: restart a dead animation ==========================

@pytest.mark.parametrize("task", ["none", "dead"])
def test_scan_restarts_dead_animation_and_warns(
    steady_clock, monkeypatch, run_async, caplog, task,
):
    registry = SessionRegistry()
    sess = _session(steady_clock(), task=task)
    registry._sessions["claude-jim"] = sess

    with caplog.at_level(logging.WARNING, logger="aipager.session_monitor"):
        calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == [{"action": "restart", "since": 0.0}]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no animate task while BUSY" in warnings[0].getMessage()
    assert "[jim]" in warnings[0].getMessage()
    assert sess.card_watchdog_at > 0


def test_scan_restart_works_for_open_background_job(
    steady_clock, monkeypatch, run_async,
):
    """The waiting card of an IDLE session with a background agent still
    open is the same animate task's responsibility (its loop condition
    holds on job_background_open()) — so it is watched the same way."""
    registry = SessionRegistry()
    sess = _session(steady_clock(), status=Status.IDLE, task="none")
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": steady_clock()}
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == [{"action": "restart", "since": 0.0}]


def test_scan_leaves_idle_session_alone(steady_clock, monkeypatch, run_async, caplog):
    registry = SessionRegistry()
    sess = _session(steady_clock(), status=Status.IDLE, task="none", edited_ago=90)
    registry._sessions["claude-jim"] = sess

    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == []
    assert sess.card_watchdog_at == 0.0
    assert not [r for r in caplog.records if "animate task" in r.getMessage()]


def test_scan_leaves_interactive_session_alone(
    steady_clock, monkeypatch, run_async, caplog,
):
    """The INTERACTIVE card IS the permission prompt: its animation was
    stopped on purpose and repainting it would wipe the keyboard."""
    registry = SessionRegistry()
    sess = _session(steady_clock(), status=Status.INTERACTIVE, task="none",
                    edited_ago=90)
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions["claude-jim"] = sess

    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == []
    assert sess.status == Status.INTERACTIVE
    assert sess.card_watchdog_at == 0.0
    assert not [r for r in caplog.records if "animate task" in r.getMessage()]


@pytest.mark.parametrize("msg_id", [None, 0, -1])
def test_scan_leaves_cardless_session_alone(
    steady_clock, monkeypatch, run_async, msg_id,
):
    registry = SessionRegistry()
    sess = _session(steady_clock(), msg_id=msg_id, task="none", edited_ago=90)
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == []


def test_scan_leaves_compacting_card_alone(steady_clock, monkeypatch, run_async):
    """While the live-stack top is the compaction card, ``animate_task``
    holds ``_animate_compact`` and ``last_tool_edit_at`` is never
    stamped — a busy-card refresh here would paint over the dots."""
    now = steady_clock()
    registry = SessionRegistry()
    sess = _session(now, task="live", edited_ago=90)
    sess.push_compacting(43, now, 600.0)
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == []


def test_scan_skips_session_mid_send(steady_clock, monkeypatch, run_async):
    """``_send_busy_and_animate`` holds ``animate_lock`` while it stops
    the old task and replaces the card — not a dead animation."""
    registry = SessionRegistry()
    sess = _session(steady_clock(), task="none")
    registry._sessions["claude-jim"] = sess
    calls: list[tuple[str, dict]] = []

    async def _notify(s, event, ctx):
        calls.append((event, dict(ctx)))

    monitor = SessionMonitor(registry, _notify)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )

    async def _run():
        async with sess.animate_lock:
            await monitor._scan()

    run_async(_run())
    assert _watchdog_calls(calls) == []


# ===== session monitor: force a refresh of a stale card ====================

def test_scan_forces_refresh_when_live_task_stale(
    steady_clock, monkeypatch, run_async,
):
    registry = SessionRegistry()
    sess = _session(steady_clock(), task="live", edited_ago=25)
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    forced = _watchdog_calls(calls)
    assert len(forced) == 1
    assert forced[0]["action"] == "refresh"
    assert 24.5 <= forced[0]["since"] <= 26


def test_scan_no_refresh_when_recently_edited(
    steady_clock, monkeypatch, run_async,
):
    registry = SessionRegistry()
    sess = _session(steady_clock(), task="live", edited_ago=5)
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    assert _watchdog_calls(calls) == []
    assert sess.card_watchdog_at == 0.0


def test_refresh_forced_again_only_after_another_window():
    """Pure decision: one force per CARD_STALE_SECONDS per session, even
    while the card stays stale (an edit that keeps failing)."""
    now = 1_000_000.0
    sess = _session(now, task="live", edited_ago=25)

    assert busy_card_watchdog_action(sess, now) == ("refresh", 25.0)
    sess.card_watchdog_at = now  # what the scan stamps on action

    assert busy_card_watchdog_action(sess, now + 5) is None
    assert busy_card_watchdog_action(sess, now + CARD_STALE_SECONDS - 0.5) is None
    assert busy_card_watchdog_action(sess, now + CARD_STALE_SECONDS) == (
        "refresh", 25.0 + CARD_STALE_SECONDS,
    )


def test_restart_throttled_by_the_same_window():
    """A card whose every edit is refused (bot blocked) leaves each
    restarted task dead again within a tick; without the throttle it
    would be retried on every 2s scan."""
    now = 1_000_000.0
    sess = _session(now, task="none")

    assert busy_card_watchdog_action(sess, now) == ("restart", 0.0)
    sess.card_watchdog_at = now

    assert busy_card_watchdog_action(sess, now + 5) is None
    assert busy_card_watchdog_action(sess, now + CARD_STALE_SECONDS) == ("restart", 0.0)


def test_fresh_card_measures_from_busy_started_until_first_edit():
    """``_send_busy_and_animate`` zeroes ``last_tool_edit_at`` on send;
    the first tick lands ~1.5s later. A brand-new card must not be
    "stale since the epoch"."""
    now = 1_000_000.0
    sess = _session(now, task="live")
    sess.last_tool_edit_at = 0.0

    sess.busy_started_at = now - 3
    assert busy_card_watchdog_action(sess, now) is None

    sess.busy_started_at = now - 25
    assert busy_card_watchdog_action(sess, now) == ("refresh", 25.0)


def test_no_baseline_no_refresh():
    now = 1_000_000.0
    sess = _session(now, task="live")
    sess.last_tool_edit_at = 0.0
    sess.busy_started_at = 0.0
    assert busy_card_watchdog_action(sess, now) is None


def test_stale_threshold_is_inclusive_at_the_boundary():
    now = 1_000_000.0
    sess = _session(now, task="live", edited_ago=CARD_STALE_SECONDS)
    assert busy_card_watchdog_action(sess, now) == ("refresh", CARD_STALE_SECONDS)
    sess.last_tool_edit_at = now - CARD_STALE_SECONDS + 0.5
    assert busy_card_watchdog_action(sess, now) is None


def test_interactive_demotion_restarts_animation_in_the_same_scan(
    steady_clock, monkeypatch, run_async,
):
    """The monitor's own INTERACTIVE-timeout demotion transitions to BUSY
    without touching the animation the prompt stopped — the watchdog runs
    right after it so the card comes back in the same scan."""
    now = steady_clock()
    registry = SessionRegistry()
    sess = _session(now, status=Status.INTERACTIVE, task="none")
    sess.last_hook_at = now - INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.busy_started_at = now - INTERACTIVE_TIMEOUT_SECONDS - 120
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions["claude-jim"] = sess

    calls = _scan(registry, monkeypatch, run_async)

    assert sess.status == Status.BUSY
    assert _watchdog_calls(calls) == [{"action": "restart", "since": 0.0}]


def test_notify_failure_is_logged_not_raised(
    steady_clock, monkeypatch, run_async, caplog,
):
    registry = SessionRegistry()
    sess = _session(steady_clock(), task="none")
    registry._sessions["claude-jim"] = sess

    async def _boom(s, event, ctx):
        if event == "busy_card_watchdog":
            raise RuntimeError("telegram down")

    monitor = SessionMonitor(registry, _boom)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-jim"]),
    )
    with caplog.at_level(logging.WARNING, logger="aipager.session_monitor"):
        run_async(monitor._scan())  # must not raise

    assert any("Failed to notify busy_card_watchdog" in r.getMessage()
               for r in caplog.records)


# ===== bot: carrying the decision out ======================================

def _bot_session(**kw) -> TrackedSession:
    return _session(time.monotonic(), **kw)


def test_watchdog_restart_starts_animation_once(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(task="none")
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "busy_card_watchdog",
                         {"action": "restart", "since": 0.0}))

    bot._start_animation.assert_called_once_with(sess)


@pytest.mark.parametrize("status", [Status.INTERACTIVE, Status.IDLE])
def test_watchdog_restart_never_touches_prompt_or_idle(mk_bot, run_async, status):
    bot = mk_bot()
    sess = _bot_session(status=status, task="none")
    bot._start_animation = MagicMock()

    run_async(bot._watchdog_busy_card(sess, "restart", 0.0))

    bot._start_animation.assert_not_called()


def test_watchdog_restart_keeps_a_running_task(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(task="live")
    bot._start_animation = MagicMock()

    run_async(bot._watchdog_busy_card(sess, "restart", 0.0))

    bot._start_animation.assert_not_called()


def test_watchdog_refresh_forces_one_edit_and_reports_it(
    mk_bot, run_async, caplog,
):
    bot = mk_bot()
    sess = _bot_session(task="live", edited_ago=25)

    async def _edit(s, verb, *, final=False, waiting=False):
        s.last_tool_edit_at = time.monotonic()  # what a landed POST stamps
        return True

    bot._edit_busy_rich = AsyncMock(side_effect=_edit)
    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot.notify(sess, "busy_card_watchdog",
                             {"action": "refresh", "since": 25.0}))

    bot._edit_busy_rich.assert_awaited_once()
    assert bot._edit_busy_rich.await_args.args[1] == "Working"
    assert bot._edit_busy_rich.await_args.kwargs.get("waiting") is False
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert [r.getMessage() for r in infos] == [
        "[jim] forced stale-card refresh (25s since last edit)",
    ]


def test_watchdog_refresh_uses_waiting_frame_for_open_job(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(status=Status.IDLE, task="live", edited_ago=25)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": time.monotonic()}
    bot._edit_busy_rich = AsyncMock(return_value=True)

    run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    bot._edit_busy_rich.assert_awaited_once()
    assert bot._edit_busy_rich.await_args.kwargs.get("waiting") is True


def test_watchdog_refresh_deduped_edit_is_not_reported(mk_bot, run_async, caplog):
    """``_edit_busy_rich`` returns True without a POST when the render is
    unchanged — nothing landed, so nothing is claimed."""
    bot = mk_bot()
    sess = _bot_session(task="live", edited_ago=25)
    bot._edit_busy_rich = AsyncMock(return_value=True)

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    assert not [r for r in caplog.records if r.levelno == logging.INFO]


def test_watchdog_refresh_permanent_failure_stops_animation(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(task="live", edited_ago=25)
    bot._edit_busy_rich = AsyncMock(return_value=None)
    bot._stop_animation = MagicMock()

    run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    bot._stop_animation.assert_called_once_with(sess)


@pytest.mark.parametrize("status", [Status.INTERACTIVE, Status.IDLE])
def test_watchdog_refresh_never_touches_prompt_or_idle(mk_bot, run_async, status):
    bot = mk_bot()
    sess = _bot_session(status=status, task="live", edited_ago=25)
    bot._edit_busy_rich = AsyncMock(return_value=True)

    run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    bot._edit_busy_rich.assert_not_awaited()


def test_watchdog_refresh_timeout_replaces_the_task(
    mk_bot, run_async, monkeypatch, caplog,
):
    """An edit that cannot get through the per-session lock is a wedged
    task; the monitor's loop gets its cadence back and the task is
    replaced rather than waited on."""
    bot = mk_bot()
    sess = _bot_session(task="live", edited_ago=25)
    monkeypatch.setattr(animation, "CARD_REFRESH_TIMEOUT", 0.05)

    async def _hangs(*a, **kw):
        await asyncio.sleep(2)
        return True

    bot._edit_busy_rich = _hangs
    bot._start_animation = MagicMock()

    with caplog.at_level(logging.WARNING, logger="aipager.bot.animation"):
        run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    bot._start_animation.assert_called_once_with(sess)
    assert any("forced stale-card refresh did not complete" in r.getMessage()
               for r in caplog.records)


# ===== animate task: a raising tick does not end the loop ==================

def test_animate_busy_survives_raising_tick(mk_bot, run_async, monkeypatch, caplog):
    bot = mk_bot()
    sess = _bot_session(task="none")
    sess.last_tool_edit_at = 0.0
    monkeypatch.setattr(animation, "FIRST_TICK_DELAY", 0.01)
    monkeypatch.setattr(animation, "STREAM_EDIT_INTERVAL", 0.01)
    # Two ticks raise, the third is a permanent failure that ends the loop
    # the ordinary way — proving the loop outlived both exceptions.
    bot._edit_busy_rich = AsyncMock(
        side_effect=[RuntimeError("boom"), RuntimeError("boom again"), None],
    )
    bot._app.bot.send_chat_action = AsyncMock()

    async def _run():
        await asyncio.wait_for(bot._animate_busy(sess), timeout=5.0)

    with caplog.at_level(logging.DEBUG, logger="aipager.bot.animation"):
        run_async(_run())

    assert bot._edit_busy_rich.await_count == 3
    raised = [r for r in caplog.records if "busy-card tick raised" in r.getMessage()]
    assert [r.levelno for r in raised] == [logging.WARNING, logging.DEBUG]
    assert raised[0].exc_info is not None


def test_animate_busy_still_stops_on_cancel(mk_bot, run_async, monkeypatch):
    """The per-tick guard must not swallow cancellation."""
    bot = mk_bot()
    sess = _bot_session(task="none")
    sess.last_tool_edit_at = 0.0
    monkeypatch.setattr(animation, "FIRST_TICK_DELAY", 0.01)
    monkeypatch.setattr(animation, "STREAM_EDIT_INTERVAL", 0.01)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_chat_action = AsyncMock()

    async def _run():
        task = asyncio.ensure_future(bot._animate_busy(sess))
        await asyncio.sleep(0.1)
        assert not task.done()
        task.cancel()
        await asyncio.wait_for(task, timeout=1.0)
        return task.done()

    assert run_async(_run()) is True


# ===== INTERACTIVE → BUSY resume: the real paths ===========================

def test_tool_use_after_terminal_answer_resumes_animation(mk_bot, run_async):
    """A prompt answered in the terminal reaches BUSY through
    hook_receiver's PreToolUse transition and the ``tool_use`` notify —
    the one place that transition is visible to the bot."""
    bot = mk_bot()
    sess = _bot_session(status=Status.INTERACTIVE, task="none")
    bot.registry._sessions["claude-jim"] = sess
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    bot.registry.transition("claude-jim", Status.BUSY)  # hook_receiver's step
    run_async(bot.notify(sess, "tool_use",
                         {"tool_summary": "Bash: ls", "tool_name": "Bash"}))

    bot._start_animation.assert_called_once_with(sess)


def test_tool_use_leaves_a_running_animation_alone(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(task="live")
    bot.registry._sessions["claude-jim"] = sess
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "tool_use",
                         {"tool_summary": "Bash: ls", "tool_name": "Bash"}))

    bot._start_animation.assert_not_called()


def test_tool_use_does_not_resume_after_permanent_edit_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _bot_session(task="none")
    bot.registry._sessions["claude-jim"] = sess
    bot._edit_busy_rich = AsyncMock(return_value=None)
    bot._start_animation = MagicMock()
    bot._stop_animation = MagicMock()

    run_async(bot.notify(sess, "tool_use",
                         {"tool_summary": "Bash: ls", "tool_name": "Bash"}))

    bot._stop_animation.assert_called_once_with(sess)
    bot._start_animation.assert_not_called()


def test_tool_use_never_resumes_while_interactive(mk_bot, run_async):
    """PostToolUse/PreToolUse rows can land while the prompt is still up
    (AskUserQuestion, or a hook racing the answer) — the prompt card is
    left alone."""
    bot = mk_bot()
    sess = _bot_session(status=Status.INTERACTIVE, task="none")
    bot.registry._sessions["claude-jim"] = sess
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    run_async(bot.notify(sess, "tool_use",
                         {"tool_summary": "Bash: ls", "tool_name": "Bash"}))

    bot._start_animation.assert_not_called()


def _mk_query(callback_data, *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    return update, query


@pytest.mark.parametrize("answer", ["allow", "deny"])
def test_telegram_permission_answer_resumes_animation(
    mk_bot, run_async, monkeypatch, answer,
):
    """The Telegram-button path: transition to BUSY, then start the
    animation the INTERACTIVE handler stopped. Pinned so the watchdog
    stays the backstop, never the mechanism.

    ``pending_permission`` is what notify.py's INTERACTIVE branch sets
    whenever a busy card exists (the inline prompt); it is the callback's
    switch between the inline branch — the one that restarts the
    animation — and the legacy separate-message branch."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.busy_msg_id = 100
    sess.busy_started_at = time.monotonic() - 30
    sess.pending_permission = {
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash", "input": {}, "summary": "Bash: ls"},
        "wait_started_at": time.monotonic() - 10,
    }
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()

    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, _query = _mk_query(f"claude-jim:{answer}")
    run_async(bot._handle_callback(update, MagicMock()))

    assert sess.status == Status.BUSY
    bot._start_animation.assert_called_once_with(sess)
