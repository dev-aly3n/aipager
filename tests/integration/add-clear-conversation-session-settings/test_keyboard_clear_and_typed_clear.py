"""Commands -> Clear (the reply keyboard) keeps its contract, and a typed
"/clear" keeps today's answer: design.md success criteria 5 and 16.

The keyboard path is driven through ``bot._send_command(update, "/clear")``
and, in a group, through the real message handler with the keyboard's own
button text, exactly as entrypoints.md describes.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import group_intake
from aipager.dtach import inject
from aipager.state import Status, TrackedSession

G = -1001234
BOB = 22
X1 = "claude-x1__g1001234"


@pytest.fixture
def kb(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    # Alive only for a session that exists: an any-name True would make a
    # typed "/clear" look like a live session called clear.
    alive = AsyncMock(side_effect=lambda name: name in bot.registry._sessions)
    typed = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", alive)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", typed)
    monkeypatch.setattr("aipager.bot.session_ops._spawn", lambda coro: coro.close())
    bot._react = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._card_for_injected = AsyncMock()
    bot.sess, bot.typed, bot.alive = sess, typed, alive
    return bot


def _send(kb, run_async, mk_update):
    update = mk_update("/clear")
    run_async(kb._send_command(update, "/clear"))
    return update


def _replies(update):
    return [re.sub(r"<[^>]+>", "", c.args[0])
            for c in update.message.reply_text.await_args_list]


def _reactions(kb):
    return [str(a) for c in kb._react.await_args_list
            for a in list(c.args) + list(c.kwargs.values())]


# ---- criterion 5: idle ---------------------------------------------------------

def test_keyboard_clear_types_clear_once(kb, run_async, mk_update):
    _send(kb, run_async, mk_update)
    assert [c.args[:2] for c in kb.typed.await_args_list] == [("claude-jim", "/clear")]


def test_keyboard_clear_replies_sent_to(kb, run_async, mk_update):
    update = _send(kb, run_async, mk_update)
    assert _replies(update) == ["🧹 /clear sent to jim"]


def test_keyboard_clear_reacts_with_the_ack(kb, run_async, mk_update):
    _send(kb, run_async, mk_update)
    assert any("👌" in r for r in _reactions(kb))


def test_keyboard_clear_draws_no_confirm(kb, run_async, mk_update):
    """The keyboard button stays a one-tap action (unchanged)."""
    update = _send(kb, run_async, mk_update)
    assert update.message.reply_text.await_args.kwargs.get("reply_markup") is None


# ---- criterion 5: busy ---------------------------------------------------------

def test_keyboard_clear_on_busy_replies_cant_clear(kb, run_async, mk_update):
    kb.sess.status = Status.BUSY
    update = _send(kb, run_async, mk_update)
    assert _replies(update) == ["⚠️ Can't clear while session is busy"]


def test_keyboard_clear_on_busy_types_nothing(kb, run_async, mk_update):
    kb.sess.status = Status.BUSY
    _send(kb, run_async, mk_update)
    kb.typed.assert_not_awaited()


# ---- criterion 5: an open dialog holds it --------------------------------------

def _dialog(kb):
    kb.registry.transition(kb.sess.name, Status.INTERACTIVE)
    kb.sess.pending_permission = {"tool_summary": "Bash: ls"}


def test_keyboard_clear_with_dialog_open_types_nothing(kb, run_async, mk_update):
    _dialog(kb)
    _send(kb, run_async, mk_update)
    kb.typed.assert_not_awaited()


def test_keyboard_clear_with_dialog_open_is_queued(kb, run_async, mk_update):
    _dialog(kb)
    _send(kb, run_async, mk_update)
    assert [item[0] for item in kb.sess.pending_queue] == ["/clear"]


def test_keyboard_clear_with_dialog_open_reacts_eyes(kb, run_async, mk_update):
    _dialog(kb)
    _send(kb, run_async, mk_update)
    assert any("👀" in r for r in _reactions(kb))


def test_keyboard_clear_while_background_agents_run_still_types(kb, run_async, mk_update):
    """Design D4: the keyboard keeps today's rule (only BUSY refuses)."""
    kb.sess.add_subagent("a1", {"type": "explore", "started_at": 1.0})
    _send(kb, run_async, mk_update)
    assert kb.typed.await_count == 1


# ---- criterion 5: socket gone --------------------------------------------------

def test_keyboard_clear_with_socket_gone_says_not_found(kb, run_async, mk_update):
    kb.alive.side_effect = None
    kb.alive.return_value = False
    update = _send(kb, run_async, mk_update)
    assert _replies(update) == ["⚠️ Session 'claude-jim' not found"]


def test_keyboard_clear_with_socket_gone_types_nothing(kb, run_async, mk_update):
    kb.alive.side_effect = None
    kb.alive.return_value = False
    _send(kb, run_async, mk_update)
    kb.typed.assert_not_awaited()


# ---- criterion 5: a role that may not send /clear (group) ----------------------

@pytest.fixture
def bob_keyboard(gbot, run_async, mk_update, monkeypatch):
    """bob talks to x1 (``/x1``), then the real prompt path is restored so
    its role rule runs; the PTY is a seam."""
    _handle(gbot, run_async, _gmsg(mk_update, "/x1", BOB, 100))
    del gbot._inject_prompt
    typed = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_text_and_enter", typed)
    gbot.typed = typed
    return gbot


def _gmsg(mk_update, text, user, message_id):
    u = mk_update(text, user_id=user, chat_id=G, message_id=message_id)
    u.effective_user.username = f"u{user}"
    u.effective_message = u.message
    u.effective_chat.type = "supergroup"
    u.message.sender_chat = None
    u.message.from_user = u.effective_user
    u.message.photo = None
    u.message.document = None
    u.message.entities = ()
    u.message.caption_entities = ()
    u.message.parse_entities = MagicMock(return_value={})
    u.message.parse_caption_entities = MagicMock(return_value={})
    return u


def _handle(bot, run_async, update):
    run_async(bot._handle_message(update, MagicMock()))


def test_group_keyboard_clear_by_a_user_types_into_their_session(bob_keyboard, run_async,
                                                                 mk_update):
    up = _gmsg(mk_update, group_intake.KEYBOARD_MARKER + "Clear", BOB, 101)
    _handle(bob_keyboard, run_async, up)
    assert [c.args[:2] for c in bob_keyboard.typed.await_args_list] == [(X1, "/clear")]


def test_role_that_may_not_send_clear_gets_needs_admin(bob_keyboard, run_async, mk_update):
    bob_keyboard._command_map = {}
    up = _gmsg(mk_update, "/clear", BOB, 102)
    run_async(bob_keyboard._send_command(up, "/clear"))
    assert _replies(up) == ["That command needs an admin."]


def test_role_that_may_not_send_clear_types_nothing(bob_keyboard, run_async, mk_update):
    bob_keyboard._command_map = {}
    up = _gmsg(mk_update, "/clear", BOB, 103)
    run_async(bob_keyboard._send_command(up, "/clear"))
    bob_keyboard.typed.assert_not_awaited()


# ---- criterion 16: a typed "/clear" ---------------------------------------------

def test_typed_clear_in_a_group_types_nothing(gbot, run_async, mk_update):
    _handle(gbot, run_async, _gmsg(mk_update, "/clear", BOB, 200))
    gbot._inject_prompt.assert_not_awaited()


def test_typed_clear_in_a_group_answers_unknown_session(gbot, run_async, mk_update):
    up = _gmsg(mk_update, "/clear", BOB, 201)
    _handle(gbot, run_async, up)
    assert _replies(up) == ["⚠️ Unknown session: clear"]


def test_typed_clear_in_a_dm_types_nothing(kb, run_async, mk_update):
    update = mk_update("/clear")
    update.message.entities = ()
    run_async(kb._handle_message(update, MagicMock()))
    kb.typed.assert_not_awaited()


def test_typed_clear_in_a_dm_answers_unknown_session(kb, run_async, mk_update):
    update = mk_update("/clear")
    update.message.entities = ()
    run_async(kb._handle_message(update, MagicMock()))
    assert _replies(update) == ["⚠️ Unknown session: clear"]


def test_typed_clear_does_not_switch_the_target(kb, run_async, mk_update):
    update = mk_update("/clear")
    update.message.entities = ()
    run_async(kb._handle_message(update, MagicMock()))
    assert kb.registry.last_active_session == "claude-jim"
