"""Additional tests for aipager.bot.handlers.CommandHandlersMixin.

Existing test files cover /clearqueue, /kill, /new, /resume. This file
fills in coverage for /start, /status, /help, _handle_message (the text
router), _send_template / _send_command / _direct_send, and _react.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock


from aipager.state import Status, TrackedSession


# ---- _handle_start_cmd ---------------------------------------------------

def test_start_with_no_sessions_shows_friendly_text(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._send_keyboard = AsyncMock()
    update = mk_update("/start")
    run_async(bot._handle_start_cmd(update, MagicMock()))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "aipager" in text
    assert "no sessions yet" in text
    bot._send_keyboard.assert_awaited_once()


def test_start_with_existing_sessions_lists_them(mk_bot, mk_update, run_async):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s2 = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = s1
    bot.registry._sessions["claude-dev"] = s2
    bot._app.bot.send_message = AsyncMock()
    bot._send_keyboard = AsyncMock()
    update = mk_update("/start")
    run_async(bot._handle_start_cmd(update, MagicMock()))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "jim" in text
    assert "dev" in text


def test_start_filters_out_gone_sessions(mk_bot, mk_update, run_async):
    bot = mk_bot()
    s = TrackedSession(name="claude-gone", label="gone", status=Status.GONE)
    bot.registry._sessions["claude-gone"] = s
    bot._app.bot.send_message = AsyncMock()
    bot._send_keyboard = AsyncMock()
    update = mk_update("/start")
    run_async(bot._handle_start_cmd(update, MagicMock()))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "gone" not in text or "no sessions yet" in text


def test_start_swallows_send_failure(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
    bot._send_keyboard = AsyncMock()
    update = mk_update("/start")
    # MUST NOT raise
    run_async(bot._handle_start_cmd(update, MagicMock()))


# ---- _handle_status ----------------------------------------------------

def test_status_with_empty_registry_returns_no_sessions(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))
    update = mk_update("/status")
    run_async(bot._handle_status(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No sessions" in text


def test_status_with_sessions_renders_dashboard(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.model_name = "Opus 4.7"
    sess.last_token_pct = 25
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._read_status_file = MagicMock(return_value=None)
    update = mk_update("/status")
    run_async(bot._handle_status(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "jim" in text
    assert "25%" in text
    assert "Opus 4.7" in text


def test_status_offers_clear_button_when_gone_sessions(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-gone", label="gone", status=Status.GONE)
    bot.registry._sessions["claude-gone"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    bot._read_status_file = MagicMock(return_value=None)
    update = mk_update("/status")
    run_async(bot._handle_status(update, MagicMock()))
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert kb is not None
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "_:clear_gone" in cb


def test_status_recovers_gone_session_when_socket_alive(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._read_status_file = MagicMock(return_value=None)
    update = mk_update("/status")
    run_async(bot._handle_status(update, MagicMock()))
    # The status should have been corrected (no longer GONE)
    assert bot.registry.get("claude-jim").status != Status.GONE


# ---- _read_status_file -------------------------------------------------

def test_read_status_file_missing_returns_none(mk_bot, tmp_path, monkeypatch):
    bot = mk_bot()
    # Path lookup uses /tmp; redirect via monkeypatch
    _real_path = bot.__class__.__module__  # noqa
    from aipager.bot import handlers
    monkeypatch.setattr(handlers, "Path",
                        lambda p: tmp_path / p.split("/")[-1])
    assert bot._read_status_file("missing") is None


def test_read_status_file_parses_used_percentage(mk_bot, tmp_path, monkeypatch):
    f = tmp_path / "claude-status-jim.json"
    f.write_text(json.dumps({
        "context_window": {"used_percentage": 50},
        "cost": {"total_cost_usd": 0.42},
        "model": {"display_name": "Sonnet"},
    }))
    from aipager.bot import handlers
    _real = handlers.Path
    monkeypatch.setattr(handlers, "Path",
                        lambda p: _real(tmp_path / p.split("/")[-1]))
    bot = mk_bot()
    out = bot._read_status_file("jim")
    assert out["ctx_pct"] == 50
    assert out["cost"] == 0.42
    assert out["model"] == "Sonnet"


def test_read_status_file_uses_remaining_when_no_used(mk_bot, tmp_path, monkeypatch):
    f = tmp_path / "claude-status-jim.json"
    f.write_text(json.dumps({
        "context_window": {"remaining_percentage": 75},
        "cost": {},
        "model": {},
    }))
    from aipager.bot import handlers
    _real = handlers.Path
    monkeypatch.setattr(handlers, "Path",
                        lambda p: _real(tmp_path / p.split("/")[-1]))
    bot = mk_bot()
    out = bot._read_status_file("jim")
    assert out["ctx_pct"] == 25  # 100 - 75


# ---- _handle_message routing -------------------------------------------

def test_handle_message_empty_text_is_noop(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("   ")
    run_async(bot._handle_message(update, MagicMock()))
    # Nothing should have been sent
    bot._app.bot.send_message.assert_not_called()


def test_handle_message_templates_button_switches_level(mk_bot, mk_update, run_async):
    from aipager.config import TEMPLATES_BUTTON
    bot = mk_bot()
    bot._send_keyboard = AsyncMock()
    update = mk_update(TEMPLATES_BUTTON)
    run_async(bot._handle_message(update, MagicMock()))
    bot._send_keyboard.assert_awaited_once()
    assert bot._send_keyboard.await_args.kwargs.get("level") == "templates"


def test_handle_message_commands_button_switches_level(mk_bot, mk_update, run_async):
    from aipager.config import COMMANDS_BUTTON
    bot = mk_bot()
    bot._send_keyboard = AsyncMock()
    update = mk_update(COMMANDS_BUTTON)
    run_async(bot._handle_message(update, MagicMock()))
    assert bot._send_keyboard.await_args.kwargs.get("level") == "commands"


def test_handle_message_back_button_returns_to_parent(mk_bot, mk_update, run_async):
    from aipager.config import BACK_BUTTON
    bot = mk_bot()
    bot._keyboard_level = "models"
    bot._send_keyboard = AsyncMock()
    update = mk_update(BACK_BUTTON)
    run_async(bot._handle_message(update, MagicMock()))
    # models → commands per KEYBOARD_PARENTS
    assert bot._send_keyboard.await_args.kwargs.get("level") == "commands"


def test_handle_message_template_button_invokes_template_send(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._template_map = {"Continue": "Continue"}
    bot._send_template = AsyncMock()
    update = mk_update("Continue")
    run_async(bot._handle_message(update, MagicMock()))
    bot._send_template.assert_awaited_once()


def test_handle_message_slash_label_with_prompt_does_direct_send(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._direct_send = AsyncMock()
    update = mk_update("/jim do something")
    run_async(bot._handle_message(update, MagicMock()))
    bot._direct_send.assert_awaited_once()


def test_handle_message_slash_label_stop_routes_to_stop_by_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot._stop_by_label = AsyncMock()
    update = mk_update("/jim stop")
    run_async(bot._handle_message(update, MagicMock()))
    bot._stop_by_label.assert_awaited_once()


def test_handle_message_bare_label_switches_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._switch_session = AsyncMock()
    update = mk_update("/jim")
    run_async(bot._handle_message(update, MagicMock()))
    bot._switch_session.assert_awaited_once()


def test_handle_message_bare_status_routes_to_status_handler(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._handle_status = AsyncMock()
    update = mk_update("status")
    run_async(bot._handle_message(update, MagicMock()))
    bot._handle_status.assert_awaited_once()


def test_handle_message_bare_label_text_switches(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._switch_session = AsyncMock()
    update = mk_update("jim")  # no slash
    run_async(bot._handle_message(update, MagicMock()))
    bot._switch_session.assert_awaited_once()


def test_handle_message_no_session_to_route_to_warns(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("free-form text")
    run_async(bot._handle_message(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert "don't know which session" in update.message.reply_text.await_args.args[0]


def test_handle_message_routes_to_last_active(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("hello")
    run_async(bot._handle_message(update, MagicMock()))
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


def test_handle_message_queues_when_session_busy(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._react = AsyncMock()
    update = mk_update("queue me")
    run_async(bot._handle_message(update, MagicMock()))
    assert any(t == "queue me" for t, *_ in sess.pending_queue)
    bot._react.assert_awaited_once()


def test_handle_message_warns_when_queue_full(mk_bot, mk_update, run_async, monkeypatch):
    from aipager.state import QUEUE_CAP
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    # Fill the queue
    for i in range(QUEUE_CAP):
        sess.queue_prompt(f"existing{i}", i)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update = mk_update("overflow")
    run_async(bot._handle_message(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Queue is full" in text


def test_handle_message_dead_session_warns(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("hello")
    run_async(bot._handle_message(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_handle_message_send_failure_reports_error(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update = mk_update("hello")
    run_async(bot._handle_message(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to send" in text


def test_handle_message_reply_to_msg_routes_to_owning_session(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry._msg_map[200] = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("reply text")
    reply_target = MagicMock()
    reply_target.message_id = 200
    reply_target.text = "(original)"
    reply_target.caption = None
    update.message.reply_to_message = reply_target
    run_async(bot._handle_message(update, MagicMock()))
    # Routed to claude-jim, not last_active
    assert sess.status == Status.BUSY


# ---- _send_template / _send_command / _direct_send --------------------

def test_send_template_with_no_active_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("Continue")
    run_async(bot._send_template(update, "Continue"))
    text = update.message.reply_text.await_args.args[0]
    assert "No active session" in text


def test_send_template_with_busy_session_queues(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    bot._react = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update = mk_update("Continue")
    run_async(bot._send_template(update, "Continue"))
    assert any(t == "Continue" for t, *_ in sess.pending_queue)


def test_send_command_with_dead_session_warns(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("/compact")
    run_async(bot._send_command(update, "/compact"))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_send_command_happy_path(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    bot._react = AsyncMock()
    update = mk_update("/compact")
    run_async(bot._send_command(update, "/compact"))
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "/compact"


def test_direct_send_unknown_label_warns(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/nope hi")
    run_async(bot._direct_send(update, "nope", "hi"))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown session" in text


def test_direct_send_routes_to_target(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("/jim do thing")
    run_async(bot._direct_send(update, "jim", "do thing"))
    assert sess.status == Status.BUSY


# ---- _react -------------------------------------------------------------

def test_react_calls_telegram_api(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._app.bot.set_message_reaction = AsyncMock()
    update = mk_update("hi")
    run_async(bot._react(update, "👀"))
    bot._app.bot.set_message_reaction.assert_awaited_once()


def test_react_swallows_failure(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._app.bot.set_message_reaction = AsyncMock(side_effect=RuntimeError("nope"))
    update = mk_update("hi")
    # MUST NOT raise
    run_async(bot._react(update, "👀"))
