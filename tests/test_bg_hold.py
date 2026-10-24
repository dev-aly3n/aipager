"""Messages for a session parked on a Claude Code background job (P5).

Typed into the parked client, a message can start a stray background job,
so the one message gate holds it (👀, ``pending_queue``) whatever else is
going on, after looking for a park again itself (the park can be newer
than the monitor's tick). Each sender is told once per park why it waits.
A turn end never drains the queue while parked, and when the parked
session ends instead, what was held is dropped with 🤷.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_jobs
from aipager.bot import reactions
from aipager.bot.session_ops import BG_HELD_NOTICE
from aipager.session_monitor import SessionMonitor
from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"
JOB = "ab12cd34"
SID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def wired(mk_bot, monkeypatch):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    injected: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: injected.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    sess = bot.registry.get_or_create(NAME)
    sess.label = "x"
    sess.claude_session_id = SID
    bot.registry.transition(NAME, Status.IDLE)
    bot.registry.last_active_session = NAME
    return bot, sess, injected


def _send(bot, mk_update, run_async, text, *, msg_id=9, user_id=12345):
    update = mk_update(text, message_id=msg_id, user_id=user_id, chat_id=CHAT_ID)
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    run_async(bot._handle_message(update, ctx))
    return update


def _notices(bot) -> list:
    out = []
    for call in bot._app.bot.send_message.await_args_list:
        text = call.kwargs.get("text") or (call.args[1] if len(call.args) > 1 else "")
        if "brings it back from Claude Code's background" in str(text):
            out.append(call)
    return out


def _handed_off(bot) -> int:
    return sum(1 for c in bot._react.await_args_list
               if c.args[1:] == (reactions.HANDED_OFF,))


def test_a_message_for_a_parked_session_is_held(wired, mk_update, run_async):
    bot, sess, injected = wired
    sess.parked_job = JOB
    _send(bot, mk_update, run_async, "please carry on")
    assert injected == []
    assert [q[0] for q in sess.pending_queue] == ["please carry on"]
    assert _handed_off(bot) == 1


def test_each_sender_is_told_once_per_park(wired, mk_update, run_async):
    """T2.13: the held notice goes once per person per park; later
    messages only get the 👀. A new park tells them again."""
    bot, sess, injected = wired
    sess.parked_job = JOB
    _send(bot, mk_update, run_async, "one", msg_id=11)
    _send(bot, mk_update, run_async, "two", msg_id=12)
    notices = _notices(bot)
    assert len(notices) == 1
    assert notices[0].kwargs["reply_to_message_id"] == 11
    assert notices[0].kwargs["text"] == BG_HELD_NOTICE.replace("{session}", "x")
    assert _handed_off(bot) == 2
    _send(bot, mk_update, run_async, "three", msg_id=13, user_id=777)
    assert len(_notices(bot)) == 2, "another sender is told too"
    sess.park_told.clear()  # what the monitor does when the job changes
    _send(bot, mk_update, run_async, "four", msg_id=14)
    assert len(_notices(bot)) == 3
    assert injected == [] and len(sess.pending_queue) == 4


def test_the_park_holds_ahead_of_every_other_reason(wired, mk_update, run_async):
    """An open dialog would also hold it; the park's notice is the one
    sent (the dialog's would say "answer it first")."""
    bot, sess, injected = wired
    sess.parked_job = JOB
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    _send(bot, mk_update, run_async, "held")
    assert injected == [] and len(sess.pending_queue) == 1
    assert len(_notices(bot)) == 1
    assert sess.dialog_hold is None


def test_the_gate_looks_for_the_park_itself(wired, mk_update, run_async, tmp_path,
                                            monkeypatch):
    """T2.20: the monitor has not ticked since ``←``; the gate's own
    synchronous check finds the park, so the message is held, not typed."""
    bot, sess, injected = wired
    cfg = tmp_path / "claude-config"
    (cfg / "sessions").mkdir(parents=True)
    (cfg / "sessions" / "4100.json").write_text(json.dumps(
        {"pid": 4100, "kind": "interactive", "sessionId": SID, "parkedJobId": JOB}))
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: cfg)
    monitor = SessionMonitor(bot.registry, AsyncMock())
    bot.parked_check = monitor.check_parked
    assert sess.parked_job == ""
    _send(bot, mk_update, run_async, "carry on")
    assert sess.parked_job == JOB
    assert injected == [] and len(sess.pending_queue) == 1


def test_a_failing_check_does_not_lose_the_message(wired, mk_update, run_async):
    bot, sess, injected = wired

    def broken(_s):
        raise RuntimeError("boom")

    bot.parked_check = broken
    _send(bot, mk_update, run_async, "go")
    assert injected and injected[-1].endswith("go")


# ---- T2.14: no drain while parked ---------------------------------------------

def test_a_turn_end_does_not_drain_while_parked(wired, run_async):
    bot, sess, injected = wired
    sess.parked_job = JOB
    sess.queue_prompt("held for the park", 21)
    run_async(bot._drain_next_queued(sess))
    assert injected == []
    assert [q[0] for q in sess.pending_queue] == ["held for the park"]


def test_the_drain_goes_on_once_not_parked(wired, run_async):
    bot, sess, injected = wired
    sess.queue_prompt("held for the park", 21)
    run_async(bot._drain_next_queued(sess))
    assert injected and injected[-1].endswith("held for the park")
    assert sess.pending_queue == []


# ---- T2.15: the parked session ends -------------------------------------------

def _end(bot, sess, run_async):
    bot._mark_not_delivered = AsyncMock()
    bot._settle_queued_targets = AsyncMock(return_value=[])
    bot.registry.transition(NAME, Status.GONE)
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))


def test_a_parked_session_that_ends_drops_what_it_held(wired, mk_update, run_async):
    bot, sess, injected = wired
    sess.parked_job = JOB
    sess.parked_job_session_id = "ab12cd34-0000"
    sess.parked_since = time.time()
    _send(bot, mk_update, run_async, "one", msg_id=31)
    _send(bot, mk_update, run_async, "two", msg_id=32)
    _end(bot, sess, run_async)
    assert sess.pending_queue == []
    dropped = [e for c in bot._mark_not_delivered.await_args_list for e in c.args[1]]
    assert sorted(e.get("msg_id") for e in dropped) == [31, 32]
    assert (sess.parked_job, sess.parked_job_session_id, sess.parked_since) == ("", "", 0.0)
    assert sess.park_told == set()
    assert injected == []


def test_a_restart_keeps_what_the_parked_session_held(wired, mk_update, run_async):
    """A deliberate relaunch (the bring-back): the held messages wait for
    the session that comes back."""
    bot, sess, injected = wired
    sess.parked_job = JOB
    _send(bot, mk_update, run_async, "one", msg_id=31)
    sess.restarting_until = time.monotonic() + 60
    _end(bot, sess, run_async)
    assert [q[0] for q in sess.pending_queue] == ["one"]
    assert sess.parked_job == JOB


def test_the_park_event_is_inert_until_the_notice_exists(wired, run_async):
    """``bg_parked`` (the monitor saw a park) sends nothing yet and never
    reaches the status branches (an IDLE session would take it for a
    turn's end)."""
    bot, sess, injected = wired
    sess.parked_job = JOB
    sess.queue_prompt("held", 51)
    run_async(bot.notify(sess, "bg_parked", {"job": JOB}))
    assert bot._app.bot.send_message.await_count == 0
    assert injected == [] and len(sess.pending_queue) == 1
    assert sess.status == Status.IDLE
