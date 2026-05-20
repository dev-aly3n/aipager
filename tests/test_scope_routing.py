"""Phase B: outbound notifications route to session.scope_chat_id (Layer 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.bot.transport import resolve_chat_id
from aipager.state import Status, TrackedSession


def _sess(scope_chat_id=0):
    s = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s.scope_chat_id = scope_chat_id
    return s


# ---- resolve_chat_id ----------------------------------------------------

def test_resolve_returns_scope_chat_when_set(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    assert resolve_chat_id(_sess(scope_chat_id=999)) == 999


def test_resolve_falls_back_to_chat_id_str(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    # Unstamped session → original CHAT_ID string (preserves old behavior)
    assert resolve_chat_id(_sess(scope_chat_id=0)) == "111"


# ---- notify routing -----------------------------------------------------

def test_context_warning_routes_to_scope(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess(scope_chat_id=999)
    run_async(bot.notify(sess, "context_warning", {"context_pct": 90}))
    bot._app.bot.send_message.assert_awaited_once()
    assert bot._app.bot.send_message.await_args.args[0] == 999


def test_send_busy_routes_to_scope(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    sess = _sess(scope_chat_id=-4152307515)
    run_async(bot.send_busy(sess))
    assert bot._app.bot.send_message.await_args.args[0] == -4152307515


def test_cross_scope_no_bleed(mk_bot, run_async):
    """Two sessions in different scopes each notify only their own chat."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    a = _sess(scope_chat_id=111)
    a.name = "claude-a"
    b = _sess(scope_chat_id=222)
    b.name = "claude-b"
    run_async(bot.notify(a, "context_warning", {"context_pct": 90}))
    run_async(bot.notify(b, "context_warning", {"context_pct": 90}))
    chats = [c.args[0] for c in bot._app.bot.send_message.await_args_list]
    assert chats == [111, 222]


def test_unstamped_session_uses_chat_id(mk_bot, run_async, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "555")
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess(scope_chat_id=0)
    run_async(bot.notify(sess, "context_warning", {"context_pct": 90}))
    assert bot._app.bot.send_message.await_args.args[0] == "555"
