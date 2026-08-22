"""Iteration 2, task item #1 — independently re-verify the fix for the
three findings from iteration 1 (kill, kill-confirm, new_replace) plus
new_resume (explicitly asked for this round), WITHOUT trusting
test_old_longform_compat.py's own assertions: different session names,
a stronger side-effect probe (checks inject.launch_session too, even
on the kill verbs, and checks no outward message was sent), and one
extra scenario iteration 1 did not cover -- a name that WAS a real,
tracked session at render time but is gone by the time the button is
tapped (session removed between render and tap), not just a name that
never existed.

entrypoints.md: "no matching session -> a 'Session not found' toast,
never silence, never a crash."
design.md: "A long-form tap for a missing session answers 'Session not
found' -- not silence, not a crash."
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


def _patched_side_effects(monkeypatch):
    """Every dangerous action any of the four verbs under test could
    plausibly reach. Returns the mocks so the test can assert on
    call counts."""
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock()
    launch_session = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "send_keys", send_keys)
    monkeypatch.setattr(inject, "kill_session", kill_session)
    monkeypatch.setattr(inject, "launch_session", launch_session)
    return send_keys, kill_session, launch_session


@pytest.mark.parametrize("verb", ["kill", "kill-confirm", "new_replace", "new_resume"])
def test_never_existed_name_fails_closed_with_no_side_effect(scb_bot, helpers, monkeypatch, verb):
    """A name that was never a real session at all (typo'd or hand
    crafted callback_data) must not reach ANY of the three dangerous
    actions, for each of the four verbs this round is re-checking."""
    send_keys, kill_session, launch_session = _patched_side_effects(monkeypatch)
    bot = scb_bot()
    cb_upd, q = helpers.make_callback_update(
        f"claude-neverexisted__d135792468:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert kill_session.await_args_list == [], (
        f"verb={verb!r}: kill_session was invoked for a name that never "
        f"existed: {kill_session.await_args_list!r}"
    )
    assert launch_session.await_args_list == [], (
        f"verb={verb!r}: launch_session was invoked for a name that never "
        f"existed: {launch_session.await_args_list!r}"
    )
    assert send_keys.await_args_list == [], (
        f"verb={verb!r}: send_keys was invoked for a name that never "
        f"existed: {send_keys.await_args_list!r}"
    )
    assert bot._app.bot.send_message.await_args_list == [], (
        f"verb={verb!r}: a new outward message was sent for a name that "
        f"never existed: {bot._app.bot.send_message.await_args_list!r}"
    )
    assert q.answer.await_args is not None, f"verb={verb!r}: silence"
    assert q.answer.await_args.args[0] == NOT_FOUND_MESSAGE, (
        f"verb={verb!r}: expected {NOT_FOUND_MESSAGE!r}, got {q.answer.await_args!r}"
    )


@pytest.mark.parametrize("verb", ["kill", "kill-confirm", "new_replace", "new_resume"])
def test_name_removed_between_render_and_tap_fails_closed(scb_bot, helpers, monkeypatch, verb):
    """A stronger scenario than 'never existed': the session WAS real
    and tracked, a button referencing it (by its real name, long-form)
    is sitting in chat history, and then the session is removed from
    the registry (killed elsewhere, GONE and cleared, etc.) before the
    stale button gets tapped. This is the realistic path a genuine old
    button takes -- not just a hand-typo'd name."""
    send_keys, kill_session, launch_session = _patched_side_effects(monkeypatch)
    bot = scb_bot()
    s = TrackedSession(name="claude-wasreal", label="wasreal",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    del bot.registry._sessions[s.name]  # gone by the time the tap arrives

    cb_upd, q = helpers.make_callback_update(f"claude-wasreal:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert kill_session.await_args_list == [], (
        f"verb={verb!r}: kill_session fired for a session removed before the tap"
    )
    assert launch_session.await_args_list == [], (
        f"verb={verb!r}: launch_session fired for a session removed before the tap"
    )
    assert send_keys.await_args_list == [], (
        f"verb={verb!r}: send_keys fired for a session removed before the tap"
    )
    assert q.answer.await_args is not None, f"verb={verb!r}: silence"
    assert q.answer.await_args.args[0] == NOT_FOUND_MESSAGE, (
        f"verb={verb!r}: expected {NOT_FOUND_MESSAGE!r}, got {q.answer.await_args!r}"
    )
