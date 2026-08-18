"""Black-box integration tests for design.md success criteria 13 and 14.

Criterion 13 is driven end to end: real ``/tmp`` artifacts are created
via an actual ``_handle_message`` reply-to-older-message call (not
pre-seeded directly through ``policy_snapshot.write_snapshot``/
``write_reply_context_file`` the way the developer's own
``tests/test_state_reply_context.py`` does it), then both GONE-inducing
paths (crash/socket-vanish via ``SessionRegistry.transition`` and
explicit kill via ``SessionRegistry.remove``, both plain public
``aipager.state`` entrypoints) are driven and the artifacts checked.

Criterion 14 drives a real reply to a session that is no longer alive
through ``_handle_message`` and asserts on the outbound Telegram call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

CHAT_ID = -1001
BOT_ID = 87654321


@pytest.fixture(autouse=True)
def _isolate_snapshot_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _reply_target(*, message_id, text="(old text)"):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = None
    m.from_user = None
    return m


def _produce_real_artifacts(mk_bot, mk_update, run_async, monkeypatch):
    """Creates BOTH /tmp artifacts via a genuine reply-to-an-older-message
    turn, returning (bot, sess) with the session left IDLE afterward."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(1, sess.name, CHAT_ID)
    bot.registry.track_message(999, sess.name, CHAT_ID)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()

    update = mk_update("re your earlier point", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="the target text " * 5)
    run_async(bot._handle_message(update, _ctx()))

    assert ps.snapshot_path(sess.name).exists()
    assert ps.reply_context_path(sess.name).exists()
    sess.status = Status.IDLE
    return bot, sess


# ===== Criterion 13: GONE transition (crash/socket-vanish path) ============

def test_gone_transition_removes_both_real_artifacts_produced_by_a_live_turn(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot, sess = _produce_real_artifacts(mk_bot, mk_update, run_async, monkeypatch)

    bot.registry.transition(sess.name, Status.GONE)

    assert not ps.snapshot_path(sess.name).exists()
    assert not ps.reply_context_path(sess.name).exists()


# ===== Criterion 13: explicit kill (disjoint path, skips GONE entirely) ====

def test_explicit_kill_removes_both_real_artifacts_produced_by_a_live_turn(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot, sess = _produce_real_artifacts(mk_bot, mk_update, run_async, monkeypatch)

    bot.registry.remove(sess.name)

    assert not ps.snapshot_path(sess.name).exists()
    assert not ps.reply_context_path(sess.name).exists()
    assert bot.registry.get(sess.name) is None  # kill deletes the record outright


# ===== Criterion 14: reply to a GONE session keeps the text, adds resume ===

def test_reply_to_gone_session_shows_not_found_verbatim_with_resume_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(1, sess.name, CHAT_ID)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))

    update = mk_update("about that", chat_id=CHAT_ID, message_id=2000)
    update.message.reply_to_message = _reply_target(message_id=1, text="an old message")
    run_async(bot._handle_message(update, _ctx()))

    text = update.message.reply_text.await_args.args[0]
    assert text == f"⚠️ Session '{sess.name}' not found"

    kb = update.message.reply_text.await_args.kwargs.get("reply_markup")
    assert kb is not None
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"{sess.name}:resume" in callbacks
