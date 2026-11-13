"""Clear conversation: the one ``/clear`` path (the Commands keyboard's
Clear, the ⋮ menu's confirm and the Mini App's), the ⋮ menu row and its
confirm card, their gates, and what stays out of Telegram's ``/`` menu.

Nothing here reaches Claude: typing is the ``inject.send_text_and_enter``
seam (or ``bot._inject_prompt``), liveness ``inject.is_alive``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import reactions
from aipager.bot.transport import PROMPT_DIALOG, PROMPT_REFUSED
from aipager.state import Status, TrackedSession


# ---- helpers -------------------------------------------------------------

def _live(bot, *, name="claude-dev", label="dev", status=Status.IDLE,
          target=True) -> TrackedSession:
    sess = TrackedSession(name=name, label=label, status=status)
    bot.registry._sessions[name] = sess
    if target:
        bot.registry.last_active_session = name
    return sess


@pytest.fixture
def typed(monkeypatch):
    """What reaches Claude: (session name, text) per typed message, with the
    session alive."""
    calls: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        calls.append((name, text))
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", _send)
    return calls


def _core(bot, sess, run_async, **kw):
    kw.setdefault("driver_user_id", 12345)
    kw.setdefault("chat_id", None)
    return run_async(bot._clear_session_core(sess, **kw))


# ---- the shared core -----------------------------------------------------

def test_core_types_clear_once_into_an_idle_session(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async)
    assert (outcome.ok, outcome.reason, outcome.label) == (True, "sent", "dev")
    assert typed == [("claude-dev", "/clear")]


def test_core_gone_is_not_live(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.GONE)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "not_live"
    assert typed == []


def test_core_dead_socket_is_missing(mk_bot, run_async, typed, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "missing"
    assert typed == []


def test_core_busy_refused(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.BUSY)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "busy"
    assert typed == []


def test_core_busy_refused_even_with_a_hold(mk_bot, run_async, typed):
    """The keyboard's hold would queue it; a busy session refuses first."""
    bot = mk_bot()
    sess = _live(bot, status=Status.BUSY)
    hold = AsyncMock(return_value=True)
    outcome = _core(bot, sess, run_async, hold=hold)
    assert outcome.reason == "busy"
    hold.assert_not_awaited()
    assert typed == []


def test_core_without_hold_refuses_open_dialog(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.INTERACTIVE)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "prompt_open"
    assert typed == []
    assert sess.pending_queue == []


def test_core_without_hold_refuses_background_agents(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.job_continuation_active = True
    assert sess.job_background_open()
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_refuses_model_hold(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.model_hold = True
    assert sess.queue_prompt("hello", 7)
    assert sess.model_hold_active()
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_refuses_other_senders_note(mk_bot, run_async, typed,
                                                      monkeypatch):
    monkeypatch.setattr("aipager.policy_snapshot.outstanding_sender_keys",
                        lambda name, **kw: {(0, 4242)})
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async, driver_user_id=12345)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_takes_the_senders_own_note(mk_bot, run_async, typed,
                                                      monkeypatch):
    """Control for the test above: the sender's own outstanding note does
    not hold their clear."""
    monkeypatch.setattr("aipager.policy_snapshot.outstanding_sender_keys",
                        lambda name, **kw: {(0, 12345)})
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async, driver_user_id=12345)
    assert outcome.reason == "sent"


def test_core_parked_refused(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.parked_job = "job-1"
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "parked"
    assert typed == []


def test_core_parked_after_typing_failed_is_parked(mk_bot, run_async, monkeypatch):
    """Parked between the checks and the typing: said as parked, not as
    a send failure."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)

    async def _inject(s, text, **kw):
        s.parked_job = "job-1"
        return False

    bot._inject_prompt = _inject
    assert _core(bot, sess, run_async).reason == "parked"


def test_core_maps_refused_role_to_needs_admin(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    assert _core(bot, sess, run_async).reason == "needs_admin"


def test_core_dialog_met_while_typing_is_prompt_open(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=PROMPT_DIALOG)
    assert _core(bot, sess, run_async).reason == "prompt_open"


def test_core_typing_failed_is_send_failed(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=False)
    assert _core(bot, sess, run_async).reason == "send_failed"


def test_core_passes_the_sender_and_chat_to_the_typing(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=True)
    _core(bot, sess, run_async, driver_user_id=77, chat_id=-100, msg_id=5)
    bot._inject_prompt.assert_awaited_once_with(
        sess, "/clear", msg_id=5, chat_id=-100, driver_user_id=77)


# ---- Commands -> Clear (the keyboard), unchanged to the person ------------

def test_keyboard_clear_types_clear_and_says_where(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == [("claude-jim", "/clear")]
    assert update.message.reply_text.await_args.args[0] == "🧹 /clear sent to jim"
    bot._react.assert_awaited_once_with(update, reactions.ACK)


def test_keyboard_clear_busy_refused(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim", status=Status.BUSY)
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert update.message.reply_text.await_args.args[0] == (
        "⚠️ Can't clear while session is busy")


def test_keyboard_clear_dead_socket(mk_bot, mk_update, run_async, typed, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert update.message.reply_text.await_args.args[0] == (
        "⚠️ Session 'claude-jim' not found")


def test_keyboard_clear_with_dialog_open_is_held_not_typed(
        mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert [entry[0] for entry in sess.pending_queue] == ["/clear"]
    bot._react.assert_awaited_with(update, reactions.HANDED_OFF)


def test_keyboard_clear_meeting_dialog_is_held(mk_bot, mk_update, run_async, monkeypatch):
    """A dialog that opens while it is typed: held for it, as before."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=PROMPT_DIALOG)
    bot._hold_met_dialog = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    bot._hold_met_dialog.assert_awaited_once_with(update, sess, "/clear")


def test_keyboard_clear_refused_role_needs_admin(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert update.message.reply_text.await_args.args[0] == "That command needs an admin."


def test_keyboard_clear_typing_failed(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=False)
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert update.message.reply_text.await_args.args[0] == "❌ Failed to send to [jim]"


def test_keyboard_other_command_keeps_its_glyph(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._react = AsyncMock()
    update = mk_update("/cost")
    run_async(bot._send_command(update, "/cost"))
    assert update.message.reply_text.await_args.args[0] == "↪️ /cost sent to jim"


# ---- a typed "/clear" stays what it was ------------------------------------

def test_typed_clear_types_nothing_into_a_live_session(mk_bot, mk_update, run_async,
                                                      monkeypatch):
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(side_effect=lambda name: name == "claude-x1"))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-x1", label="x1")
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._handle_message(update, MagicMock()))
    sent.assert_not_awaited()
    assert update.message.reply_text.await_args.args[0] == "⚠️ Unknown session: clear"
