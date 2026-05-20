"""Additional callbacks.py tests — multi-question advance, audit corners,
and the no-pending-permission separate-message fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_query():
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


def _setup_perm_session(bot, monkeypatch, perm):
    sess = TrackedSession(name="claude-jim", label="jim",
                          status=Status.INTERACTIVE)
    sess.busy_msg_id = 100
    sess.busy_started_at = 0
    sess.pending_permission = perm
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    return sess


# ---- multi-question advance --------------------------------------------

def test_allow_advances_to_next_question(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "question": "Q1",
        "options": [{"label": "A"}],
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}]},
            {"question": "Q2", "options": [{"label": "B"}]},
        ],
        "current_idx": 0,
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    sess = _setup_perm_session(bot, monkeypatch, perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # Pending advanced to Q2
    assert sess.pending_permission["current_idx"] == 1
    assert sess.pending_permission["question"] == "Q2"


def test_allow_last_question_completes(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "question": "Q1",
        "options": [{"label": "A"}],
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}]},
            {"question": "Q2", "options": [{"label": "B"}]},
        ],
        "current_idx": 1,  # already on last
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    sess = _setup_perm_session(bot, monkeypatch, perm)
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
    # Cleared + transitioned to BUSY
    assert sess.pending_permission is None
    assert sess.status == Status.BUSY


def test_audit_send_failure_swallowed(mk_bot, mk_query, run_async, monkeypatch):
    """Telegram audit-reply send failure must not propagate."""
    bot = mk_bot()
    perm = {
        "ask_question": False,
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "wait_started_at": 0,
    }
    _setup_perm_session(bot, monkeypatch, perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, query = mk_query("claude-jim:allow")
    # MUST NOT raise
    run_async(bot._handle_callback(update, MagicMock()))


def test_multi_select_submit_no_options_selected(mk_bot, mk_query, run_async, monkeypatch):
    """Submit with empty selected set → 'Submitted (none)'."""
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}],
        "selected": set(),  # nothing picked
        "cursor_pos": 0,
        "questions": [{"question": "Q1", "options": [], "multiSelect": True}],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    sess = _setup_perm_session(bot, monkeypatch, perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, query = mk_query("claude-jim:submit")
    run_async(bot._handle_callback(update, MagicMock()))
    # Successfully completed
    assert sess.pending_permission is None


def test_multi_select_submit_send_keys_fails(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}],
        "selected": {0},
        "cursor_pos": 0,
        "questions": [{"question": "Q1", "options": [], "multiSelect": True}],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_perm_session(bot, monkeypatch, perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))  # send fails

    update, query = mk_query("claude-jim:submit")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send" in (a or "") for a in answers)


# ---- ask_question multi-question final submit path ---------------------

def test_allow_multi_question_final_submit_sends_extra_enter(mk_bot, mk_query, run_async, monkeypatch):
    """When the last question is reached and there were multiple questions,
    an extra Enter is sent to land on the Submit tab."""
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "question": "Q2",
        "options": [{"label": "A"}],
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}]},
            {"question": "Q2", "options": [{"label": "B"}]},
        ],
        "current_idx": 1,  # last question
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_perm_session(bot, monkeypatch, perm)
    sent_keys = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent_keys)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)

    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # Multiple Enter calls expected: one for allow + one for submit
    enter_count = sum(
        1 for c in sent_keys.await_args_list if c.args[1] == "Enter"
    )
    assert enter_count >= 2


# ---- wait_time elapsed-discounting ------------------------------------

def test_allow_discounts_wait_time_from_busy_started_at(mk_bot, mk_query, run_async, monkeypatch):
    """When wait_started_at is set, busy_started_at is bumped forward."""
    bot = mk_bot()
    import time as _time
    perm = {
        "ask_question": False,
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "wait_started_at": _time.monotonic() - 5,  # waited 5 seconds
    }
    sess = _setup_perm_session(bot, monkeypatch, perm)
    sess.busy_started_at = _time.monotonic() - 10
    original_started = sess.busy_started_at
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
    # busy_started_at should have been bumped forward (later)
    assert sess.busy_started_at > original_started
