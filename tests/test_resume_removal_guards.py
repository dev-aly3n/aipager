"""No removal path drops a session while its resume is in flight
(roadmap 8.15 + 8.16).

`2225ac1` taught the ageing sweep (`expire_gone`) to leave a resuming
session alone. It is not the only door. Reproduced 2026-09-09 by driving
`_do_resume_core` with each removal called inside a stubbed
`launch_session`: the count cap (`_evict_gone_overflow`), `/kill` and the
delete routes each removed the entry mid-launch, the later
`transition()` fabricated a blank replacement, and the resume still
reported "Resumed" while the real object — cwd, chat scope, permission
mode, message routing — was orphaned.

The count cap now skips a resuming session; the three DELIBERATE
removals (kill, Mini App delete, the chat delete-confirm button) refuse
instead of silently succeeding, because the window is seconds and the
operator can simply try again.

No real Telegram, dtach or claude.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.dtach import inject
from aipager.state import (
    MAX_GONE_HISTORY,
    SessionRegistry,
    Status,
    TrackedSession,
)

NAME = "claude-proj__d123"
CHAT = 123
MSG = 555


def _gone_session(registry: SessionRegistry, *, name: str = NAME,
                  gone_days_ago: float = 1.0) -> TrackedSession:
    """A GONE session carrying everything a resume has to restore."""
    sess = TrackedSession(
        name=name, label=name.removeprefix("claude-").split("__")[0],
        status=Status.GONE,
    )
    sess.gone_at = time.time() - gone_days_ago * 86400.0
    sess.claude_session_id = "uuid-1234"
    sess.cwd = "/home/aly/aipager"
    sess.scope_chat_id = CHAT
    sess.skip_perms = True
    registry._sessions[name] = sess
    return sess


def _bot(mk_bot, registry):
    bot = mk_bot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._session_system_prompt = lambda *a, **kw: ""
    return bot


def _resuming(sess: TrackedSession) -> TrackedSession:
    sess.resuming_until = time.monotonic() + 30
    return sess


@pytest.fixture
def mk_query():
    def _mk(callback_data, *, user_id=12345, message_id=42):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = ""
        query.from_user = MagicMock()
        query.from_user.id = user_id
        return query
    return _mk


def _cb_update(chat_id=CHAT, user_id=12345):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


# ===== 8.15 — the count cap =============================================

def test_the_count_cap_cannot_evict_a_session_mid_resume(
    mk_bot, run_async, monkeypatch,
):
    """The reproduction: at the cap, with the resuming session the oldest
    by `gone_at`, a brand-new session appearing during the launch used to
    evict it and hand the resume an orphan."""
    registry = SessionRegistry()
    sess = _gone_session(registry, gone_days_ago=100.0)  # the oldest → first victim
    registry.track_message(MSG, NAME, CHAT)
    for i in range(MAX_GONE_HISTORY):
        _gone_session(registry, name=f"claude-filler{i}__d{CHAT}", gone_days_ago=1.0)
    bot = _bot(mk_bot, registry)

    async def _launch_while_a_new_session_appears(*a, **kw):
        registry.get_or_create("claude-brandnew__d123")
        return True, ""
    monkeypatch.setattr(inject, "launch_session", _launch_while_a_new_session_appears)

    outcome = run_async(bot._do_resume_core(sess))

    assert outcome.ok and outcome.reason == "resumed"
    assert registry.get(NAME) is sess, "the cap evicted the session mid-resume"
    assert sess.status == Status.IDLE
    assert sess.cwd == "/home/aly/aipager"
    assert sess.scope_chat_id == CHAT
    assert sess.skip_perms is True
    assert registry.get_session_by_msg(MSG, CHAT) is sess


def test_evict_gone_overflow_skips_a_resuming_session():
    registry = SessionRegistry()
    sess = _resuming(_gone_session(registry, gone_days_ago=100.0))
    for i in range(MAX_GONE_HISTORY):
        _gone_session(registry, name=f"claude-filler{i}__d{CHAT}", gone_days_ago=1.0)

    registry._evict_gone_overflow()

    assert registry.get(NAME) is sess
    # The cap is honoured on the NEXT eviction instead, not abandoned: the
    # oldest non-resuming entry goes in its place.
    assert "claude-filler0__d123" not in registry.all_sessions()


def test_evict_gone_overflow_takes_it_once_the_guard_has_lapsed():
    registry = SessionRegistry()
    sess = _gone_session(registry, gone_days_ago=100.0)
    sess.resuming_until = time.monotonic() - 1
    for i in range(MAX_GONE_HISTORY):
        _gone_session(registry, name=f"claude-filler{i}__d{CHAT}", gone_days_ago=1.0)

    registry._evict_gone_overflow()

    assert NAME not in registry.all_sessions()


# ===== 8.16 — /kill =====================================================

def test_kill_refuses_while_a_resume_is_in_flight(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    sess = _resuming(_gone_session(registry))
    bot = _bot(mk_bot, registry)
    killer = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "kill_session", killer)
    outcome = run_async(bot._kill_session_core(NAME, "proj"))

    assert outcome.result == "resuming"
    killer.assert_not_awaited()
    assert registry.get(NAME) is sess, "the kill removed a resuming session"


def test_kill_still_works_on_an_ordinary_gone_session(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    _gone_session(registry)
    bot = _bot(mk_bot, registry)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.create_task",
                        lambda coro: coro.close())

    outcome = run_async(bot._kill_session_core(NAME, "proj"))

    assert outcome.result == "killed"
    assert NAME not in registry.all_sessions()


def test_kill_by_label_tells_the_operator_to_retry(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    _resuming(_gone_session(registry))
    bot = _bot(mk_bot, registry)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    source = MagicMock()
    source.effective_chat = MagicMock()
    source.effective_chat.id = CHAT   # else find_by_label scopes to a mock
    source.message = MagicMock()
    source.message.reply_text = AsyncMock()

    run_async(bot._kill_session_by_label(source, "proj"))

    text = source.message.reply_text.await_args.args[0]
    assert "proj" in text
    assert "resum" in text.lower(), text


# ===== 8.16 — the chat delete button ====================================

def test_delete_confirm_refuses_while_a_resume_is_in_flight(
    mk_bot, run_async, mk_query,
):
    registry = SessionRegistry()
    sess = _resuming(_gone_session(registry))
    bot = _bot(mk_bot, registry)
    query = mk_query(f"{NAME}:delete-confirm")

    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(), query, NAME, "delete-confirm"))

    assert handled is True
    assert registry.get(NAME) is sess, "delete-confirm removed a resuming session"
    assert "resum" in (query.answer.await_args.args[0] or "").lower()


def test_delete_menu_refuses_while_a_resume_is_in_flight(mk_bot, run_async, mk_query):
    """The confirm card must not even be drawn — offering an action that
    will be refused a tap later is worse than refusing here."""
    registry = SessionRegistry()
    _resuming(_gone_session(registry))
    bot = _bot(mk_bot, registry)
    query = mk_query(f"{NAME}:delete")

    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(), query, NAME, "delete"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    assert "resum" in (query.answer.await_args.args[0] or "").lower()


def test_delete_confirm_still_works_on_an_ordinary_gone_session(
    mk_bot, run_async, mk_query,
):
    registry = SessionRegistry()
    _gone_session(registry)
    bot = _bot(mk_bot, registry)
    query = mk_query(f"{NAME}:delete-confirm")

    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(), query, NAME, "delete-confirm"))

    assert handled is True
    assert NAME not in registry.all_sessions()
    assert registry._dirty is True
