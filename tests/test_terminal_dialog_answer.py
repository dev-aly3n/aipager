"""Roadmap 8.113: a dialog answered "No" at the terminal ends the turn soon.

Choosing "No" in Claude Code's own permission dialog at the terminal (or
Escape on a question there) ends the turn like an interrupt: Claude Code
writes ``[Request interrupted by user for tool use]`` and fires no hook.
aipager used to notice only through the INTERACTIVE watchdog, 5 minutes
later. The session monitor now reads the transcript of a session waiting
on a dialog whenever it changed, and the interrupt marker written since the
dialog appeared ends the turn the way aipager's own typed Deny does. An
Allow (the tool's result, or nothing yet) and a dialog still open never do,
and neither do aipager's own keys (a Deny typed from Telegram, /stop's
Escapes), which write the same marker while the session still waits: the
path that typed them ends the turn, once.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot
from aipager.bot import session_ops
from aipager.dtach import inject
from aipager.session_monitor import (
    INTERACTIVE_TIMEOUT_SECONDS,
    SessionMonitor,
    dialog_interrupted_in_terminal,
)
from aipager.state import SessionRegistry, Status, TrackedSession

NAME = "claude-dlg"
MARKER = "[Request interrupted by user for tool use]"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path, *entries) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _tool_use(t):
    return {"type": "assistant", "timestamp": _iso(t), "message": {
        "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}]}}


def _tool_result(t, *, error=False):
    return {"type": "user", "timestamp": _iso(t), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": error,
         "content": "ok" if not error else "The user doesn't want to proceed."}]}}


def _marker(t):
    return {"type": "user", "timestamp": _iso(t),
            "message": {"role": "user", "content": [{"type": "text", "text": MARKER}]}}


def _waiting(tmp_path, *, dialog_age: float = 5.0) -> TrackedSession:
    """A session waiting on a dialog that appeared *dialog_age* s ago, its
    transcript ending at the tool call the dialog is about."""
    sess = TrackedSession(name=NAME, label="dlg", status=Status.INTERACTIVE)
    sess.transcript_path = str(tmp_path / "t.jsonl")
    sess.interactive_entered_at = time.monotonic() - dialog_age
    sess.last_hook_at = sess.interactive_entered_at
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    _write(sess.transcript_path, _tool_use(time.time() - dialog_age - 1))
    return sess


# ---- the signal --------------------------------------------------------------

def test_a_marker_written_after_the_dialog_appeared_is_a_terminal_no(tmp_path):
    sess = _waiting(tmp_path)
    _write(sess.transcript_path, _tool_result(time.time() - 1, error=True),
           _marker(time.time() - 1))
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is True


@pytest.mark.parametrize("tail", ["nothing", "allowed_result", "rejected_result_only"])
def test_an_open_dialog_or_an_allow_is_never_a_terminal_no(tmp_path, tail):
    sess = _waiting(tmp_path)
    if tail == "allowed_result":
        _write(sess.transcript_path, _tool_result(time.time() - 1))
    elif tail == "rejected_result_only":
        _write(sess.transcript_path, _tool_result(time.time() - 1, error=True))
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is False


def test_a_marker_from_before_the_dialog_does_not_count(tmp_path):
    sess = _waiting(tmp_path, dialog_age=5.0)
    _write(sess.transcript_path, _marker(time.time() - 60))
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is False


@pytest.mark.parametrize("change", ["busy", "idle", "no_transcript", "no_start"])
def test_only_a_session_waiting_on_a_dialog_is_read(tmp_path, change):
    sess = _waiting(tmp_path)
    _write(sess.transcript_path, _marker(time.time() - 1))
    if change == "busy":
        sess.status = Status.BUSY
    elif change == "idle":
        sess.status = Status.IDLE
    elif change == "no_transcript":
        sess.transcript_path = ""
    else:
        sess.interactive_entered_at = 0.0
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is False


# ---- the monitor ---------------------------------------------------------------

async def _sessions(*names):
    return list(names)


@pytest.fixture
def monitored(tmp_path, monkeypatch):
    registry = SessionRegistry()
    sess = _waiting(tmp_path)
    registry._sessions[NAME] = sess
    notify = AsyncMock()
    monitor = SessionMonitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", lambda: _sessions(NAME))
    return monitor, sess, notify


def _events(notify):
    return [c.args[1] for c in notify.await_args_list]


def test_the_monitor_tells_the_bot_when_the_terminal_said_no(monitored, run_async):
    monitor, sess, notify = monitored
    _write(sess.transcript_path, _tool_result(time.time() - 1, error=True),
           _marker(time.time() - 1))
    run_async(monitor._scan())
    assert _events(notify) == ["dialog_ended_in_terminal"]
    assert notify.await_args.args[2] == {"entered_at": sess.interactive_entered_at}


def test_an_open_dialog_is_left_to_wait(monitored, run_async):
    monitor, sess, notify = monitored
    run_async(monitor._scan())
    assert _events(notify) == [] and sess.status is Status.INTERACTIVE


def test_an_unchanged_transcript_is_not_read_again(monitored, run_async, monkeypatch):
    monitor, sess, notify = monitored
    reads = []
    import aipager.session_monitor as sm
    real = sm.turn_tail_since
    monkeypatch.setattr(sm, "turn_tail_since", lambda *a: reads.append(a) or real(*a))
    run_async(monitor._scan())
    run_async(monitor._scan())
    assert len(reads) == 1
    # An append a few ms after the last read can share the file's coarse
    # time: the size still tells it changed.
    st = os.stat(sess.transcript_path)
    _write(sess.transcript_path, _tool_result(time.time()))
    os.utime(sess.transcript_path, ns=(st.st_atime_ns, st.st_mtime_ns))
    run_async(monitor._scan())
    assert len(reads) == 2 and _events(notify) == []


def test_the_watchdog_still_ends_a_dialog_nobody_answers(monitored, run_async, steady_clock):
    monitor, sess, notify = monitored
    sess.last_hook_at = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - 60
    run_async(monitor._scan())
    assert sess.status is Status.BUSY and sess.pending_permission is None
    assert "dialog_ended_in_terminal" not in _events(notify)


# ---- the bot ending the turn -------------------------------------------------------

@pytest.fixture
def world(mk_bot, tmp_path):
    bot = mk_bot()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    bot._drain_next_queued = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "dlg"
    sess.status = Status.INTERACTIVE
    sess.busy_msg_id = 60
    sess.busy_started_at = time.monotonic() - 9
    sess.interactive_entered_at = time.monotonic() - 5
    sess.pending_tool_started_at = time.monotonic() - 6
    sess.parent_tool_started_at = sess.pending_tool_started_at
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    sess.transcript_path = str(tmp_path / "t.jsonl")
    policy_snapshot.mark_turn_open(NAME)
    return bot, sess


def _card_texts(bot):
    return [c.args[1] for c in bot._edit_busy_raw.await_args_list]


def _ended(bot, sess, run_async, entered=None):
    run_async(bot.notify(sess, "dialog_ended_in_terminal", {
        "entered_at": sess.interactive_entered_at if entered is None else entered}))


def test_a_terminal_no_ends_the_turn_like_a_typed_deny(world, run_async):
    bot, sess = world
    _ended(bot, sess, run_async)
    assert sess.status is Status.IDLE
    assert _card_texts(bot) == ["🚫 <b>dlg</b> · Denied in the terminal"]
    assert sess.busy_msg_id is None and sess.pending_permission is None
    assert sess.pending_tool_started_at is None and sess.parent_tool_started_at is None
    assert policy_snapshot.turn_is_open(NAME) is False
    assert sess.user_stopped_at > 0                   # a late Stop owes no answer
    assert sess.closed_turn_seq == sess.turn_seq
    bot._drain_next_queued.assert_awaited_once_with(sess)


def test_a_question_left_with_escape_reads_as_stopped(world, run_async):
    bot, sess = world
    sess.pending_permission = {"ask_question": True, "question": "Which one?"}
    _ended(bot, sess, run_async)
    assert sess.status is Status.IDLE
    assert _card_texts(bot) == ["⚠️ <b>dlg</b> · Stopped in the terminal"]


@pytest.mark.parametrize("moved_on", ["answered_in_telegram", "new_dialog", "gone"])
def test_nothing_changes_once_the_session_moved_on(world, run_async, moved_on):
    bot, sess = world
    entered = sess.interactive_entered_at
    if moved_on == "answered_in_telegram":
        sess.status = Status.BUSY          # a Telegram answer got there first
    elif moved_on == "new_dialog":
        sess.interactive_entered_at = time.monotonic()
    else:
        bot.registry._sessions.pop(NAME)
    _ended(bot, sess, run_async, entered=entered)
    assert _card_texts(bot) == []
    assert sess.pending_permission == {"tool_summary": "Bash: ls"}
    bot._drain_next_queued.assert_not_awaited()
    if moved_on != "gone":
        assert sess.status in (Status.BUSY, Status.INTERACTIVE)


def test_a_second_notice_for_the_same_dialog_does_nothing(world, run_async):
    bot, sess = world
    entered = sess.interactive_entered_at
    _ended(bot, sess, run_async)
    _ended(bot, sess, run_async, entered=entered)
    assert _card_texts(bot) == ["🚫 <b>dlg</b> · Denied in the terminal"]
    bot._drain_next_queued.assert_awaited_once_with(sess)


def test_a_telegram_answer_landing_while_waiting_for_the_card_wins(world, run_async):
    # The handler checks again once it holds the card's lock: a Telegram
    # answer that moved the session on while it waited is left alone.
    bot, sess = world

    async def _race():
        await sess.animate_lock.acquire()
        ending = asyncio.ensure_future(bot.notify(sess, "dialog_ended_in_terminal", {
            "entered_at": sess.interactive_entered_at}))
        for _ in range(5):
            await asyncio.sleep(0)          # it passed its first check, waits on the lock
        sess.status = Status.BUSY           # a Telegram answer got there meanwhile
        sess.animate_lock.release()
        await ending

    run_async(_race())
    assert sess.status is Status.BUSY
    assert _card_texts(bot) == []
    bot._drain_next_queued.assert_not_awaited()


def test_the_monitor_tries_again_when_telling_the_bot_failed(monitored, run_async):
    # After a terminal "No" the transcript usually stays as it is, so a
    # failed notice must not wait for it to change again.
    monitor, sess, notify = monitored
    notify.side_effect = [RuntimeError("bot busy"), None]
    _write(sess.transcript_path, _marker(time.time() - 1))
    run_async(monitor._scan())
    run_async(monitor._scan())
    assert _events(notify) == ["dialog_ended_in_terminal"] * 2


@pytest.mark.parametrize("shown", ["this_wait", "an_earlier_wait"])
def test_a_question_sent_as_its_own_message_reads_as_stopped(world, run_async, shown):
    # A prompt sent before the card existed keeps its fields in its own
    # record (8.99); one left from an earlier wait says nothing of this one.
    bot, sess = world
    sess.pending_permission = None
    wait = (sess.interactive_entered_at + 1 if shown == "this_wait"
            else sess.interactive_entered_at - 30)
    sess.pending_prompt_msg = {"msg_id": 70, "chat_id": 1, "text": "Which one?",
                               "perm": {"ask_question": True, "question": "Which one?",
                                        "wait_started_at": wait}}
    _ended(bot, sess, run_async)
    assert _card_texts(bot) == [
        "⚠️ <b>dlg</b> · Stopped in the terminal" if shown == "this_wait"
        else "🚫 <b>dlg</b> · Denied in the terminal"]


def test_an_old_answer_button_on_the_card_types_nothing_afterwards(world, run_async):
    bot, sess = world
    _ended(bot, sess, run_async)
    assert sess.prompt_known_closed(60) is True


# ---- aipager's own keys write the same marker ------------------------------------

class _Terminal:
    """Claude Code's side of the terminal for aipager's real writes
    (``inject._run``): records each write, and writes the interrupt marker
    the moment a refusal lands, as Claude Code does: an Escape, or the
    Enter after the Deny fallback's Downs."""

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.writes: list[bytes] = []
        self.marked = False

    async def run(self, args, stdin=b"", timeout=5):
        self.writes.append(stdin)
        refusal = stdin == b"\x1b" or (stdin == b"\r" and b"\x1b[B" in self.writes)
        if refusal and not self.marked:
            self.marked = True
            _write(self.transcript, _marker(time.time()))
        return True, ""


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    term = _Terminal(str(tmp_path / "t.jsonl"))
    monkeypatch.setattr("aipager.dtach.inject._run", term.run)
    return term


def test_keys_aipager_typed_into_the_dialog_are_not_a_terminal_answer(
        tmp_path, terminal, run_async):
    sess = _waiting(tmp_path)
    run_async(inject.send_keys(NAME, "Escape"))         # /stop's first Escape
    assert terminal.marked
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is False


def test_keys_typed_before_the_dialog_appeared_do_not_count(tmp_path, terminal, run_async):
    run_async(inject.send_keys(NAME, "Enter"))          # the prompt's submit
    sess = _waiting(tmp_path)
    sess.interactive_entered_at = inject.last_write_at(NAME) + 0.001
    _write(sess.transcript_path, _marker(time.time()))
    assert dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()) is True


@pytest.fixture
def answering(world, terminal, monkeypatch):
    """The world's bot answering its dialog with real keys, watched by a
    session monitor whose notices reach that bot."""
    bot, sess = world
    notify = AsyncMock(side_effect=bot.notify)
    monitor = SessionMonitor(bot.registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", lambda: _sessions(NAME))
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.audit.append", MagicMock(return_value=True))
    _write(sess.transcript_path, _tool_use(time.time() - 7))
    return bot, sess, monitor, notify


def _tap(data: str, message_id: int):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = 12345
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    return update


def test_a_deny_typed_from_telegram_ends_the_turn_once(answering, terminal, run_async):
    # No hook waits, so the Deny is typed (Downs, then Enter on the refusal
    # row) and Claude Code writes the marker at once, while the session is
    # still waiting. A monitor tick lands right then, before the callback
    # moves the session on: the turn is still ended once, by the typed
    # Deny's own watch.
    bot, sess, monitor, notify = answering
    ticks_in_the_window = []
    real_answer = bot._safe_answer

    async def answer(query, text=None, **kwargs):
        if terminal.marked and sess.status is Status.INTERACTIVE:
            ticks_in_the_window.append(text)
            await monitor._scan()
        return await real_answer(query, text, **kwargs)

    bot._safe_answer = answer

    async def _go():
        await bot._handle_callback(_tap(f"{NAME}:deny", 60), MagicMock())
        watch = session_ops._KEYSTROKE_WATCHES.get(NAME)
        assert watch is not None, "the typed Deny starts its watch"
        await watch

    run_async(_go())
    assert ticks_in_the_window, "no tick landed between the keys and the move on"
    assert "dialog_ended_in_terminal" not in _events(notify)
    assert sess.status is Status.IDLE
    assert _card_texts(bot)[-1] == "🚫 <b>dlg</b> · Denied"
    bot._drain_next_queued.assert_awaited_once_with(sess)


def test_stop_on_a_dialog_is_not_read_as_a_terminal_answer(answering, terminal, run_async):
    # /stop's first Escape writes the marker; a tick between it and the
    # Stop's own end must leave the end to the Stop.
    bot, sess, monitor, notify = answering

    async def _race():
        stopping = asyncio.ensure_future(bot._stop_session_core(sess))
        for _ in range(200):
            if terminal.marked:
                break
            await asyncio.sleep(0)
        assert terminal.marked and sess.status is Status.INTERACTIVE
        await monitor._scan()
        return await stopping

    outcome = run_async(_race())
    assert outcome.ok
    assert "dialog_ended_in_terminal" not in _events(notify)
    assert sess.status is Status.IDLE
    assert _card_texts(bot) == ["⚠️ <b>dlg</b> · Stopped"]
    bot._drain_next_queued.assert_not_awaited()


def test_keys_typed_while_the_notice_waited_for_the_card_win(world, terminal, run_async):
    # The monitor saw a marker, and while its notice waited for the card's
    # lock aipager typed into the dialog itself (a Stop): that path owns
    # the end, and the notice changes nothing.
    bot, sess = world

    async def _race():
        await sess.animate_lock.acquire()
        ending = asyncio.ensure_future(bot.notify(sess, "dialog_ended_in_terminal", {
            "entered_at": sess.interactive_entered_at}))
        for _ in range(5):
            await asyncio.sleep(0)
        await inject.send_keys(NAME, "Escape")
        sess.animate_lock.release()
        await ending

    run_async(_race())
    assert sess.status is Status.INTERACTIVE
    assert _card_texts(bot) == []
    bot._drain_next_queued.assert_not_awaited()
