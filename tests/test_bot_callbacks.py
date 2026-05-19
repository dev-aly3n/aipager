"""Tests for aipager.bot.callbacks.CallbackDispatchMixin._handle_callback.

The dispatcher routes inline-button taps based on ``callback_data`` of
the form ``"<session_name>:<action>"``. Each action gets its own
branch; we test the easy paths here (early returns + simple state
mutations) and avoid the deep tool-permission injection paths that
require full dtach key-injection mocking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_query():
    """Build a mocked Telegram CallbackQuery."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query
    return _mk


# ---- early returns -------------------------------------------------------

def test_invalid_callback_no_colon(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("invalid-no-colon")
    run_async(bot._handle_callback(update, MagicMock()))
    # First answer is the eager-ack (no text — None). Then the toast.
    answers = [c.args[0] if c.args else None for c in query.answer.await_args_list]
    assert any(a and "Invalid callback" in a for a in answers)


def test_team_mode_unauthorized_returns_early(mk_bot, mk_query, run_async):
    """When _authorize_callback returns None (unauthorized), handler exits."""
    bot = mk_bot()
    bot._authorize_callback = AsyncMock(return_value=None)
    bot._stop_session = AsyncMock()
    update, query = mk_query("claude-jim:stop", user_id=99999)
    run_async(bot._handle_callback(update, MagicMock()))
    # No action method should have been called
    bot._stop_session.assert_not_awaited()


# ---- action: stop ------------------------------------------------------

def test_stop_with_unknown_session(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:stop")
    run_async(bot._handle_callback(update, MagicMock()))
    # Should toast "Session not found"
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_stop_with_existing_session_invokes_stop_session(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot._stop_session = AsyncMock()
    update, query = mk_query("claude-jim:stop")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._stop_session.assert_awaited_once()


# ---- action: kill / kill-confirm / kill-cancel -------------------------

def test_kill_calls_kill_by_label(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._kill_session_by_label = AsyncMock()
    update, query = mk_query("claude-jim:kill")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._kill_session_by_label.assert_awaited_once()


def test_kill_confirm_calls_kill_by_label(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._kill_session_by_label = AsyncMock()
    update, query = mk_query("claude-jim:kill-confirm")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._kill_session_by_label.assert_awaited_once()


def test_kill_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-jim:kill-cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "Cancelled" in text


def test_kill_cancel_swallows_edit_failure(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-jim:kill-cancel")
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("boom"))
    # MUST NOT raise
    run_async(bot._handle_callback(update, MagicMock()))


# ---- voice extra subactions --------------------------------------------

def test_voice_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("__voice__:cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    assert "not installed" in query.edit_message_text.await_args.args[0]


def test_voice_install_fires_task(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._install_voice_extra = AsyncMock()
    update, query = mk_query("__voice__:install")
    run_async(bot._handle_callback(update, MagicMock()))
    # task scheduled — we can't await it but the AsyncMock should
    # at least have been bound; just verify no crash
    # (create_task means the call site is reached)


def test_voice_restart_fires_task(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._restart_daemon = AsyncMock()
    update, query = mk_query("__voice__:restart")
    run_async(bot._handle_callback(update, MagicMock()))


def test_voice_unknown_action_is_noop(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("__voice__:bogus")
    run_async(bot._handle_callback(update, MagicMock()))


# ---- action: retry ------------------------------------------------------

def test_retry_with_no_session_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_retry_with_no_last_prompt_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = ""  # nothing to retry
    bot.registry._sessions["claude-jim"] = sess
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Nothing to retry" in (a or "") for a in answers)


def test_retry_with_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "do thing"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not alive" in (a or "").lower() for a in answers)


def test_retry_happy_path(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "the prompt"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


def test_retry_send_text_fails_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "x"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to retry" in (a or "") for a in answers)


# ---- action: compact ---------------------------------------------------

def test_compact_with_no_session_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_compact_with_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_compact_happy_path_sends_slash_compact(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    bot._app.bot.delete_message = AsyncMock()
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "/compact"


def test_compact_send_failure_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send" in (a or "") for a in answers)


# ---- action: clear_gone ------------------------------------------------

def test_clear_gone_with_no_gone_sessions(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    # Insert one alive session
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("No gone sessions" in (a or "") for a in answers)
    # Session still present
    assert bot.registry.get("claude-jim") is not None


def test_clear_gone_removes_dead_sessions(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-old", label="old", status=Status.GONE)
    s2 = TrackedSession(name="claude-keep", label="keep", status=Status.IDLE)
    bot.registry._sessions["claude-old"] = s1
    bot.registry._sessions["claude-keep"] = s2
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(side_effect=lambda n: n == "claude-keep"))
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    assert bot.registry.get("claude-old") is None
    assert bot.registry.get("claude-keep") is not None
    query.edit_message_text.assert_awaited_once()


# ---- action: resume ----------------------------------------------------

def test_resume_callback_invokes_do_resume(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.gone_at = 1234.0
    bot.registry._sessions["claude-jim"] = sess
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-jim:resume")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    assert bot._do_resume.await_args.kwargs["label"] == "jim"


def test_resume_page_edits_picker(mk_bot, mk_query, run_async):
    bot = mk_bot()
    # Populate enough GONE sessions to render a picker
    for i in range(12):
        s = TrackedSession(name=f"claude-old{i:02d}", label=f"old{i:02d}",
                            status=Status.GONE)
        s.gone_at = 1000.0 - i
        s.claude_session_id = f"UUID-{i}"
        bot.registry._sessions[s.name] = s
    update, query = mk_query("_:resume_page:1")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()


def test_resume_page_malformed_index_falls_to_zero(mk_bot, mk_query, run_async):
    bot = mk_bot()
    # Just one GONE session — pagination call should still work
    s = TrackedSession(name="claude-old", label="old", status=Status.GONE)
    s.gone_at = 1234.0
    s.claude_session_id = "x"
    bot.registry._sessions[s.name] = s
    update, query = mk_query("_:resume_page:notanumber")
    run_async(bot._handle_callback(update, MagicMock()))
    # Edit fires (or not), but no exception
    # We just care it didn't crash.


def test_resume_noop_does_nothing(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:resume_noop")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_not_awaited()


# ---- /new conflict callbacks -------------------------------------------

def test_new_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "Cancelled" in text
    # Pending entry should be popped
    assert "claude-jim" not in bot._new_conflict_pending


def test_new_resume_alive_session_switches(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "go", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_resume")
    run_async(bot._handle_callback(update, MagicMock()))
    # Switched: last_active_session updated
    assert bot.registry.last_active_session == "claude-jim"
    # Prompt should be queued
    assert any(t == "go" for t, *_ in sess.pending_queue)


def test_new_resume_gone_session_routes_to_do_resume(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    bot._do_resume = AsyncMock()
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_resume")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()


def test_new_replace_kills_alive_then_launches(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.claude_session_id = "old"  # state to be cleared
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}

    kill_called = AsyncMock()
    launch_called = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_called)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch_called)
    # Pretend the socket disappears immediately
    from pathlib import Path
    monkeypatch.setattr(Path, "is_socket", lambda self: False)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, query = mk_query("claude-jim:new_replace")
    run_async(bot._handle_callback(update, MagicMock()))
    kill_called.assert_awaited_once()
    launch_called.assert_awaited_once()
    # The resume metadata was cleared
    assert sess.claude_session_id == ""


def test_new_replace_launch_failure_messages_error(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))
    update, query = mk_query("claude-jim:new_replace")
    run_async(bot._handle_callback(update, MagicMock()))
    # Should have called send_message with the failure text via _app.bot.
    # Args can be positional or kwargs depending on PTB version.
    found = False
    for c in bot._app.bot.send_message.await_args_list:
        joined = " ".join(str(a) for a in c.args) + " " + " ".join(
            str(v) for v in c.kwargs.values()
        )
        if "dtach broken" in joined:
            found = True
            break
    assert found, (
        "Expected 'dtach broken' in some send_message call; got "
        f"{[(c.args, c.kwargs) for c in bot._app.bot.send_message.await_args_list]}"
    )


# ---- unknown action toast ----------------------------------------------

def test_unknown_action_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    update, query = mk_query("claude-jim:totally_unknown_action")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Unknown" in (a or "") for a in answers)


# ---- tool-permission actions (allow / deny / continue / opt / submit) ----

def _setup_alive_session(bot, monkeypatch, *, pending_permission=None,
                          busy_msg_id=100):
    """Helper: build an alive session ready for permission-action tests."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.busy_msg_id = busy_msg_id
    if pending_permission is not None:
        sess.pending_permission = pending_permission
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    return sess


def test_action_no_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update, query = mk_query("claude-nope:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_action_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_allow_sends_enter(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # Allow sends one Enter
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "Enter"


def test_deny_sends_down_then_enter(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:deny")
    run_async(bot._handle_callback(update, MagicMock()))
    # Deny sends Down then Enter
    keys_sent = [c.args[1] for c in sent.await_args_list]
    assert keys_sent == ["Down", "Enter"]


def test_continue_sends_enter(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    update, query = mk_query("claude-jim:continue")
    run_async(bot._handle_callback(update, MagicMock()))
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "Enter"


def test_allow_send_key_failure_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send" in (a or "") for a in answers)


def test_opt_single_select_navigates_down_then_enter(mk_bot, mk_query, run_async, monkeypatch):
    """opt2 → press Down twice, then Enter (no multi_select)."""
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)  # no pending_permission → single-select
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:opt2")
    run_async(bot._handle_callback(update, MagicMock()))
    keys = [c.args[1] for c in sent.await_args_list]
    # opt2 → Down twice, then Enter
    assert keys == ["Down", "Down", "Enter"]


def test_opt_multi_select_toggles_checkbox(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
        "selected": set(),
        "cursor_pos": 0,
        "questions": [{}],
        "current_idx": 0,
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:opt1")
    run_async(bot._handle_callback(update, MagicMock()))
    # Down once to opt1, then Enter to toggle
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Down", "Enter"]
    # Selection now has index 1
    assert 1 in perm["selected"]


def test_opt_multi_select_send_fail_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}],
        "selected": set(),
        "cursor_pos": 0,
        "questions": [{}],
        "current_idx": 0,
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:opt1")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send keys" in (a or "") for a in answers)


def test_submit_multi_select_advances_question(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}],
        "selected": {0},  # A selected
        "cursor_pos": 0,
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}, {"label": "B"}],
             "multiSelect": True},
            {"question": "Q2", "options": [{"label": "C"}, {"label": "D"}],
             "multiSelect": False},
        ],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:submit")
    run_async(bot._handle_callback(update, MagicMock()))
    # After submit on a non-last question, pending_permission advances to next
    assert bot.registry.get("claude-jim").pending_permission["current_idx"] == 1
    assert bot.registry.get("claude-jim").pending_permission["question"] == "Q2"


def test_submit_multi_select_last_question_finishes(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}],
        "selected": {0},
        "cursor_pos": 0,
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}], "multiSelect": True},
        ],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:submit")
    run_async(bot._handle_callback(update, MagicMock()))
    # On last question: pending_permission cleared, session transitions to BUSY
    sess = bot.registry.get("claude-jim")
    assert sess.pending_permission is None
    assert sess.status == Status.BUSY


def test_allow_with_pending_permission_records_tool(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": False,
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    sess = bot.registry.get("claude-jim")
    # tool_history got a "Allowed" entry
    assert any("Allowed" in s for s, _ in sess.tool_history)
    # pending_permission cleared
    assert sess.pending_permission is None


def test_allow_without_pending_permission_edits_separate_message(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)  # no pending_permission
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # The separate-message path edits query (not _edit_busy_raw)
    query.edit_message_text.assert_awaited_once()
