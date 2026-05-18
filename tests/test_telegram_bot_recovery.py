"""Tests for `TelegramBot.recover_sessions` and `_recover_busy_message`.

The bot's `_app.bot` is the only Telegram surface we need to stub. We
construct a `TelegramBot` directly without going through `start()`,
hand it a mock `_app` with a faux `bot.edit_message_text`, and exercise
every outcome path: edited, vanished, too_old, blocked, flooded,
error.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager import telegram_bot as tb
from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_bot(registry: SessionRegistry, edit_side_effect=None) -> tb.TelegramBot:
    """Construct a TelegramBot with a mocked `_app.bot`."""
    bot = tb.TelegramBot(registry)
    fake_bot = MagicMock()
    fake_bot.edit_message_text = AsyncMock(side_effect=edit_side_effect)
    app = MagicMock()
    app.bot = fake_bot
    bot._app = app
    return bot


def _make_sess(name: str, label: str, busy_msg_id: int) -> TrackedSession:
    sess = TrackedSession(name=name, label=label)
    sess.busy_msg_id = busy_msg_id
    sess.status = Status.BUSY
    return sess


# ----- _recover_busy_message: each outcome path -----

def _edit_text(call) -> str:
    """Extract the text arg whether passed positionally or as kwarg."""
    if "text" in call.kwargs:
        return call.kwargs["text"]
    return call.args[0]


def test_recover_edited_when_dead_session(monkeypatch):
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry)
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "edited"
    assert sess.busy_msg_id is None  # cleared synchronously before await
    # Edited text reflects "Session ended" because session is not in live_names
    assert "Session ended" in _edit_text(bot._app.bot.edit_message_text.await_args)


def test_recover_edited_when_alive_session():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry)
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names={"claude-jim"}
    ))
    assert outcome == "edited"
    assert "Daemon restarted" in _edit_text(bot._app.bot.edit_message_text.await_args)


def test_recover_vanished_when_message_deleted():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=BadRequest("Bad Request: message to edit not found"))
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "vanished"
    assert sess.busy_msg_id is None


def test_recover_too_old_when_telegram_refuses_edit():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=BadRequest("Bad Request: message can't be edited"))
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "too_old"


def test_recover_blocked(monkeypatch):
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=Forbidden("Forbidden: bot was blocked by the user"))
    # Force the throttle gate open
    monkeypatch.setattr(tb, "_LAST_BLOCKED_LOG_TS", -1e9)
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "blocked"


def test_recover_flooded():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=RetryAfter(5))
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "flooded"


def test_recover_unexpected_badrequest_returns_error():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=BadRequest("Bad Request: something weird happened"))
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome.startswith("error:")
    assert "weird" in outcome


def test_recover_generic_exception_returns_error():
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=RuntimeError("network blip"))
    outcome = _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert outcome == "error:RuntimeError"


def test_recover_clears_busy_msg_id_even_on_failure():
    """The critical invariant: busy_msg_id is None after recovery,
    regardless of whether the Telegram edit succeeded."""
    registry = SessionRegistry()
    sess = _make_sess("claude-jim", "jim", 100)
    bot = _make_bot(registry,
                    edit_side_effect=BadRequest("Bad Request: some other error"))
    _run(bot._recover_busy_message(
        bot._app.bot, "claude-jim", sess, live_names=set()
    ))
    assert sess.busy_msg_id is None


# ----- recover_sessions: aggregate behavior -----

def test_recover_sessions_skips_when_no_orphans(monkeypatch):
    registry = SessionRegistry()
    # Sessions without busy_msg_id should be skipped entirely
    s1 = TrackedSession(name="claude-jim", label="jim")
    s1.busy_msg_id = None
    registry._sessions["claude-jim"] = s1
    bot = _make_bot(registry)
    # Patch inject.list_sessions to async-return empty set
    monkeypatch.setattr(tb.inject, "list_sessions",
                        AsyncMock(return_value=[]))
    _run(bot.recover_sessions())
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_recover_sessions_processes_only_targets_with_busy_msg_id(monkeypatch):
    registry = SessionRegistry()
    s1 = _make_sess("claude-jim", "jim", 100)
    s2 = TrackedSession(name="claude-john", label="john")
    s2.busy_msg_id = None  # not a target
    s3 = _make_sess("claude-tim", "tim", 102)
    registry._sessions.update({s1.name: s1, s2.name: s2, s3.name: s3})
    bot = _make_bot(registry)
    monkeypatch.setattr(tb.inject, "list_sessions",
                        AsyncMock(return_value=["claude-jim", "claude-john", "claude-tim"]))
    _run(bot.recover_sessions())
    # 2 sessions had busy_msg_id, both got edited
    assert bot._app.bot.edit_message_text.await_count == 2


def test_recover_sessions_stops_early_on_forbidden(monkeypatch, caplog):
    registry = SessionRegistry()
    s1 = _make_sess("claude-a", "a", 1)
    s2 = _make_sess("claude-b", "b", 2)
    s3 = _make_sess("claude-c", "c", 3)
    registry._sessions.update({s1.name: s1, s2.name: s2, s3.name: s3})

    # First edit raises Forbidden; further edits must NOT be called
    bot = _make_bot(registry,
                    edit_side_effect=Forbidden("Forbidden: bot was blocked"))
    monkeypatch.setattr(tb, "_LAST_BLOCKED_LOG_TS", -1e9)
    monkeypatch.setattr(tb.inject, "list_sessions",
                        AsyncMock(return_value=[]))
    caplog.set_level("INFO", logger="aipager.telegram_bot")
    _run(bot.recover_sessions())

    # Only the first session was attempted; the other two skipped
    assert bot._app.bot.edit_message_text.await_count == 1
    # All three had their busy_msg_id cleared regardless
    assert s1.busy_msg_id is None
    assert s2.busy_msg_id is None
    assert s3.busy_msg_id is None
    # Summary mentions the early stop
    assert any("bot blocked" in r.message for r in caplog.records)


def test_recover_sessions_logs_summary(monkeypatch, caplog):
    registry = SessionRegistry()
    s1 = _make_sess("claude-a", "a", 1)
    s2 = _make_sess("claude-b", "b", 2)
    registry._sessions.update({s1.name: s1, s2.name: s2})

    # Two different outcomes — one edits cleanly, one vanishes
    call_count = {"n": 0}

    async def _edit(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # success
        raise BadRequest("Bad Request: message to edit not found")

    bot = _make_bot(registry)
    bot._app.bot.edit_message_text = AsyncMock(side_effect=_edit)
    monkeypatch.setattr(tb.inject, "list_sessions",
                        AsyncMock(return_value=["claude-a", "claude-b"]))

    caplog.set_level("INFO", logger="aipager.telegram_bot")
    _run(bot.recover_sessions())

    summary_lines = [r.message for r in caplog.records
                     if "recovered" in r.message and "sessions" in r.message]
    assert len(summary_lines) == 1
    assert "2 sessions" in summary_lines[0]
    assert "edited" in summary_lines[0]
    assert "vanished" in summary_lines[0]


def test_recover_sessions_skips_when_no_app():
    registry = SessionRegistry()
    bot = tb.TelegramBot(registry)
    bot._app = None
    # Must not raise
    _run(bot.recover_sessions())
