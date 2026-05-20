"""Phase E: daemon surfaces a safety_blocked event in chat + audit."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status, TrackedSession


def _sess(scope=999):
    s = TrackedSession(name="claude-x__g100", label="x", status=Status.BUSY)
    s.scope_chat_id = scope
    return s


def _mock_keys(monkeypatch):
    """Stub inject.send_keys (no real dtach I/O) and return the recorder."""
    keys = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", keys)
    return keys


def test_safety_blocked_notifies_chat(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    _mock_keys(monkeypatch)
    import aipager.audit as audit_mod
    monkeypatch.setattr(audit_mod, "append", lambda **k: None)
    sess = _sess(scope=999)  # no busy_msg_id → fresh message
    run_async(bot.notify(sess, "safety_blocked",
                         {"tool": "Read", "reason": "Read on protected path"}))
    bot._app.bot.send_message.assert_awaited_once()
    args = bot._app.bot.send_message.await_args
    assert args.args[0] == 999  # routed to the session's scope
    assert "Blocked by safety policy" in args.args[1]
    assert "stopped" in args.args[1]  # turn was cleanly halted


def test_safety_blocked_halts_session(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    keys = _mock_keys(monkeypatch)
    import aipager.audit as audit_mod
    monkeypatch.setattr(audit_mod, "append", lambda **k: None)
    sess = _sess()
    bot.registry._sessions[sess.name] = sess
    run_async(bot.notify(sess, "safety_blocked",
                         {"tool": "Bash", "reason": "blocked"}))
    # Escape×2 (interrupt) + clean reset to IDLE (so 'thinking' stops).
    assert keys.await_count == 2
    assert all(c.args[1] == "Escape" for c in keys.await_args_list)
    assert keys.await_args_list[0].args[0] == "claude-x__g100"
    assert sess.status == Status.IDLE


def test_safety_blocked_sticky_repeat_is_quiet(mk_bot, run_async, monkeypatch):
    """A sticky repeat ('session halted …') only audits — no second halt,
    no key injection, no 🛑 spam."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    keys = _mock_keys(monkeypatch)
    import aipager.audit as audit_mod
    captured = {}
    monkeypatch.setattr(audit_mod, "append", lambda **k: captured.update(k))
    run_async(bot.notify(_sess(), "safety_blocked",
                         {"tool": "Bash",
                          "reason": "session halted — a prior tool call …"}))
    keys.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert captured["action"] == "Blocked"  # still audited


def test_safety_blocked_writes_audit(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    _mock_keys(monkeypatch)
    captured = {}
    import aipager.audit as audit_mod
    monkeypatch.setattr(audit_mod, "append", lambda **k: captured.update(k))
    run_async(bot.notify(_sess(), "safety_blocked",
                         {"tool": "Bash", "reason": "nested claude"}))
    assert captured["action"] == "Blocked"
    assert captured["tool"] == "Bash"
