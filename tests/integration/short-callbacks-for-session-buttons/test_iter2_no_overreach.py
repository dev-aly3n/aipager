"""Iteration 2, task item #1 (second half) — the fix for kill /
kill-confirm / new_replace / new_resume must not have overreached: a
LIVE session must still be killable via /kill, and /new's conflict
prompt must still work for a session that genuinely exists.

Exercises the real rendered short-form callback (the grammar new code
actually emits, per entrypoints.md), not a hand-built long-form string
-- this is the genuine end-to-end path an operator takes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager.dtach import inject
from aipager.state import Status, TrackedSession

CHAT_ID = -100


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _verb_cb(cbs, verb):
    for cb in cbs:
        if cb.rsplit(":", 1)[-1] == verb:
            return cb
    raise AssertionError(f"no button for verb={verb!r} among {cbs!r}")


# ---- /kill: picker's immediate-kill button on a LIVE session -------------

def test_kill_picker_button_still_kills_a_live_session(scb_bot, helpers, monkeypatch):
    kill_session = AsyncMock()
    monkeypatch.setattr(inject, "kill_session", kill_session)
    bot = scb_bot()
    s = TrackedSession(name="claude-liveone__d123456789012", label="liveone",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s

    upd = helpers.make_message_update("/kill", chat_id=CHAT_ID)
    _run(bot._handle_kill_cmd(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cb = _verb_cb(helpers.callback_data_in(kb), "kill")

    cb_upd, q = helpers.make_callback_update(cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert kill_session.await_args_list != [], (
        "the fix for the missing-session case must not have also blocked "
        "the picker's immediate-kill button for a session that genuinely "
        "exists -- kill_session was never called"
    )


# ---- /kill <label>: two-step confirm on a LIVE session --------------------

def test_kill_confirm_button_still_kills_a_live_session(scb_bot, helpers, monkeypatch):
    kill_session = AsyncMock()
    monkeypatch.setattr(inject, "kill_session", kill_session)
    bot = scb_bot()
    s = TrackedSession(name="claude-liveone__d123456789012", label="liveone",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s

    upd = helpers.make_message_update("/kill liveone", chat_id=CHAT_ID)
    _run(bot._handle_kill_cmd(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cb = _verb_cb(helpers.callback_data_in(kb), "kill-confirm")

    cb_upd, q = helpers.make_callback_update(cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert kill_session.await_args_list != [], (
        "kill-confirm for a session that genuinely exists must still kill it"
    )


# ---- /new conflict prompt: new_resume on a genuinely existing session ----

def test_new_resume_still_switches_to_a_genuinely_existing_session(scb_bot, helpers):
    bot = scb_bot()
    s = TrackedSession(name="claude-liveone__d123456789012", label="liveone",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s

    upd = helpers.make_message_update("/new liveone do a thing", chat_id=CHAT_ID)
    _run(bot._handle_new_cmd(upd, MagicMock()))
    assert s.name in bot._new_conflict_pending, (
        "expected a genuine /new collision to register a pending conflict"
    )
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cb = _verb_cb(helpers.callback_data_in(kb), "new_resume")

    cb_upd, q = helpers.make_callback_update(cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert bot.registry.last_active_session == s.name, (
        "new_resume on a genuinely pending conflict must still switch to "
        f"the existing session; last_active_session={bot.registry.last_active_session!r}"
    )
    assert s.name not in bot._new_conflict_pending, (
        "the pending conflict should be consumed by a successful new_resume"
    )


# ---- /new conflict prompt: new_replace on a genuinely existing session ---

def test_new_replace_still_kills_and_relaunches_a_genuinely_existing_session(
        scb_bot, helpers, monkeypatch):
    kill_session = AsyncMock()
    launch_session = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "kill_session", kill_session)
    monkeypatch.setattr(inject, "launch_session", launch_session)
    from pathlib import Path
    monkeypatch.setattr(Path, "is_socket", lambda self: False)

    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    bot = scb_bot()
    s = TrackedSession(name="claude-liveone__d123456789012", label="liveone",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s

    upd = helpers.make_message_update("/new liveone do a thing", chat_id=CHAT_ID)
    _run(bot._handle_new_cmd(upd, MagicMock()))
    assert s.name in bot._new_conflict_pending
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cb = _verb_cb(helpers.callback_data_in(kb), "new_replace")

    cb_upd, q = helpers.make_callback_update(cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert kill_session.await_args_list != [], (
        "new_replace on a genuinely pending conflict must still kill the "
        "existing session before relaunching"
    )
    assert launch_session.await_args_list != [], (
        "new_replace on a genuinely pending conflict must still launch a "
        "fresh session"
    )
