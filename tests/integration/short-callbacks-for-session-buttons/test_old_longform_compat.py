"""Task instruction #3 — old long-form buttons.

entrypoints.md: "A Tester building this form directly (simulating a
chat-history button) should expect: live matching session -> the
action fires; no matching session -> a 'Session not found' toast,
never silence, never a crash."

design.md's own success criteria repeats this as an unconditional,
per-verb promise: "A long-form tap for a missing session answers
'Session not found' -- not silence, not a crash."

Every callback string here is built directly by the test (an f-string
literal), simulating a button already sitting in someone's chat from
before this ship -- never rendered by a builder.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import inject
from aipager.state import Status, TrackedSession

from _verbs import NOT_FOUND_MESSAGE

CHAT_ID = -100


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- live session -> the action fires (representative sample across ------
# ---- the verb table's behavioural shapes: immediate keystroke inject,   --
# ---- a picker-style action, a no-op cancel) -------------------------------

def test_old_longform_allow_fires_on_a_live_session(scb_bot, helpers, monkeypatch):
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_keys", sent)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    bot = scb_bot()
    s = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY,
                        scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    cb_upd, q = helpers.make_callback_update("claude-jim:allow", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    sent.assert_awaited_with("claude-jim", "Enter")


def test_old_longform_stop_fires_on_a_live_session(scb_bot, helpers, monkeypatch):
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_keys", sent)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    bot = scb_bot()
    s = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY,
                        scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    cb_upd, q = helpers.make_callback_update("claude-jim:stop", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert sent.await_count > 0, "expected /stop to inject at least one keystroke"


def test_old_longform_retry_fires_on_a_live_session(scb_bot, helpers, monkeypatch):
    """retry re-injects the last prompt -- goes through a different
    branch than allow/deny/stop (it doesn't require is_alive==True the
    same way), so it's tested as its own equivalence class."""
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_keys", sent)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    bot = scb_bot()
    s = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                        scope_chat_id=CHAT_ID)
    s.last_prompt = "do the thing"
    bot.registry._sessions[s.name] = s
    cb_upd, q = helpers.make_callback_update("claude-jim:retry", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None, "expected SOME acknowledgement, not silence"


# ---- missing session -> "Session not found", never silence ---------------
# ---- (the core of task instruction #3) ------------------------------------

@pytest.mark.parametrize("verb", [
    "allow", "allow_always", "deny", "stop", "retry", "compact", "submit",
    "opt0", "opt1", "opt2", "opt3", "resume",
])
def test_old_longform_missing_session_answers_not_found(scb_bot, helpers, verb):
    bot = scb_bot()
    cb_upd, q = helpers.make_callback_update(
        f"claude-ghost__d999999999:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None, (
        f"verb={verb!r}: expected a {NOT_FOUND_MESSAGE!r} toast for a missing "
        f"session on an old long-form tap, got no answer() call at all"
    )
    assert q.answer.await_args.args[0] == NOT_FOUND_MESSAGE, (
        f"verb={verb!r}: expected a {NOT_FOUND_MESSAGE!r} toast for a missing "
        f"session on an old long-form tap, got answer={q.answer.await_args!r}"
    )


# ---- the deviations this suite's exploration actually found --------------

def test_old_longform_kill_on_missing_session_should_answer_not_found(
        scb_bot, helpers, monkeypatch):
    """FAILS on this ship's current behaviour: an old `<name>:kill`
    button for a session that no longer exists does not answer
    'Session not found' -- it proceeds to call ``inject.kill_session``
    with the tapped name and answers "Killing <name>...". That is
    exactly the "worst outcome" spec.md itself calls out (an old button
    doing something other than fail closed), for a verb design.md's own
    success criteria promises is covered:
    "A long-form tap for a missing session answers 'Session not found'
    -- not silence, not a crash." No crash occurs here, but the promise
    is still broken: the action fires against a name that was never a
    real, tracked session.
    """
    killed = AsyncMock()
    monkeypatch.setattr(inject, "kill_session", killed)
    bot = scb_bot()
    cb_upd, q = helpers.make_callback_update(
        "claude-ghost__d999999999:kill", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None
    assert q.answer.await_args.args[0] == NOT_FOUND_MESSAGE, (
        f"expected {NOT_FOUND_MESSAGE!r} for a missing session, got "
        f"{q.answer.await_args!r}; kill_session called with "
        f"{killed.await_args_list!r}"
    )


def test_old_longform_kill_confirm_on_missing_session_should_answer_not_found(
        scb_bot, helpers, monkeypatch):
    """Same class of deviation as kill, for kill-confirm."""
    killed = AsyncMock()
    monkeypatch.setattr(inject, "kill_session", killed)
    bot = scb_bot()
    cb_upd, q = helpers.make_callback_update(
        "claude-ghost__d999999999:kill-confirm", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None
    assert q.answer.await_args.args[0] == NOT_FOUND_MESSAGE, (
        f"expected {NOT_FOUND_MESSAGE!r} for a missing session, got "
        f"{q.answer.await_args!r}; kill_session called with "
        f"{killed.await_args_list!r}"
    )


def test_old_longform_new_replace_on_missing_session_must_not_launch_a_session(
        scb_bot, helpers, monkeypatch):
    """The most severe deviation this suite found: an old
    `<name>:new_replace` button, tapped after the session it referred
    to is long gone (no pending /new conflict, no tracked session),
    does not answer 'Session not found' -- it calls
    ``inject.launch_session(...)`` and actually starts a brand-new
    Claude session under the tapped (attacker/typo/history-controlled)
    name. This is strictly worse than "silence": design.md's own "Old
    buttons" section promises a stale tap answers 'Session not found',
    with zero new side effects -- not a real process spawn.
    """
    launched = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "launch_session", launched)
    bot = scb_bot()
    cb_upd, q = helpers.make_callback_update(
        "claude-ghost__d999999999:new_replace", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert launched.await_args_list == [], (
        "an old new_replace button for a name with no pending conflict and "
        f"no tracked session launched a session anyway: {launched.await_args_list!r}"
    )
