"""Phase E: daemon surfaces a safety_blocked event in chat + audit."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status, TrackedSession


def _sess(scope=999):
    s = TrackedSession(name="claude-x__g100", label="x", status=Status.BUSY)
    s.scope_chat_id = scope
    return s


def test_safety_blocked_notifies_chat(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    import aipager.audit as audit_mod
    monkeypatch.setattr(audit_mod, "append", lambda **k: None)
    sess = _sess(scope=999)
    run_async(bot.notify(sess, "safety_blocked",
                         {"tool": "Read", "reason": "Read on protected path"}))
    bot._app.bot.send_message.assert_awaited_once()
    args = bot._app.bot.send_message.await_args
    assert args.args[0] == 999  # routed to the session's scope
    assert "Blocked by safety policy" in args.args[1]


def test_safety_blocked_writes_audit(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    captured = {}
    import aipager.audit as audit_mod
    monkeypatch.setattr(audit_mod, "append", lambda **k: captured.update(k))
    run_async(bot.notify(_sess(), "safety_blocked",
                         {"tool": "Bash", "reason": "nested claude"}))
    assert captured["action"] == "Blocked"
    assert captured["tool"] == "Bash"
