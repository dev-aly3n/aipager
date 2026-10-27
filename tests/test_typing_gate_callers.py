"""Roadmap 8.143, the callers: a message that meets a dialog while it is
typed (``inject.send_text_and_enter`` stops, nothing reaches Claude) is held
as one sent while a dialog is open, and goes out once the dialog is
answered, whole. Nothing answers the dialog; nothing is lost."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager.bot.handlers import HELD_PERMISSION
from aipager.bot.transport import PROMPT_DIALOG
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.policy_snapshot import list_outstanding_notes
from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-gatecall"
CARD = 500
EN = i18n.get("en")
PERM = {"tool_summary": "Bash: rm -rf build", "tool_info": {"name": "Bash"}}


@pytest.fixture
def wired(mk_bot, monkeypatch):
    """A busy session whose next send meets a dialog: the fake send opens
    one (the session goes INTERACTIVE) and says so, as the real one does
    when the probe sees it. Later sends go through."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    notices = iter(range(900, 1000))
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda **kw: MagicMock(message_id=next(notices)))
    injected: list[str] = []
    meet: list = []

    async def send(name, body):
        if meet:
            result = meet.pop(0)
            if result.get("open", True):
                bot.registry.transition(NAME, Status.INTERACTIVE)
                sess.pending_permission = dict(PERM)
            return result["result"]
        injected.append(body)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._card_for_injected = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._resume_animation_if_dead = MagicMock(return_value=False)
    sess = bot.registry.get_or_create(NAME)
    sess.label = "gc"
    sess.scope_chat_id = CHAT_ID
    sess.busy_msg_id = CARD
    bot.registry.last_active_session = NAME
    bot.registry.transition(NAME, Status.BUSY)
    return bot, sess, injected, meet


def _send(bot, mk_update, run_async, text, *, msg_id, path="text"):
    update = mk_update(text, message_id=msg_id, user_id=12345, chat_id=CHAT_ID)
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    if path == "text":
        run_async(bot._handle_message(update, ctx))
    elif path == "direct":
        run_async(bot._direct_send(update, "gc", text))
    else:
        run_async(bot._send_command(update, text))
    return update


def _bodies(injected):
    return [b.split("\n", 1)[1] if b.startswith("[via Telegram") else b
            for b in injected]


def _answer(bot, sess, run_async):
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot.notify(sess, "dialog_hold_settle", {}))


@pytest.mark.parametrize("result", [inject.DIALOG_BEFORE_TYPING, inject.DIALOG_WHILE_TYPING])
@pytest.mark.parametrize("path", ["text", "direct", "command"])
def test_a_message_that_meets_a_dialog_is_held_then_sent_once_it_is_answered(
        wired, mk_update, run_async, path, result):
    bot, sess, injected, meet = wired
    meet.append({"result": result})
    text = "/compact" if path == "command" else "hello there"
    _send(bot, mk_update, run_async, text, msg_id=41, path=path)

    assert injected == []
    assert [e[0] for e in sess.pending_queue] == [text]
    assert sess.status is Status.INTERACTIVE, "the hold flipped the dialog away"
    assert sess.dialog_hold is not None
    bot._react.assert_awaited()
    notices = [c.kwargs for c in bot._app.bot.send_message.await_args_list]
    assert [n["text"] for n in notices] == [EN.text(HELD_PERMISSION)]
    # Its note went with it: nothing of it may be matched as picked up.
    assert list_outstanding_notes(NAME) == []

    _answer(bot, sess, run_async)
    assert _bodies(injected) == [text]
    assert sess.pending_queue == []


def test_the_note_written_for_the_message_is_removed(wired, mk_update, run_async,
                                                     monkeypatch):
    bot, sess, injected, meet = wired
    seen = []

    async def send(name, body):
        seen.append(len(list_outstanding_notes(NAME)))
        bot.registry.transition(NAME, Status.INTERACTIVE)
        sess.pending_permission = dict(PERM)
        return inject.DIALOG_BEFORE_TYPING
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    _send(bot, mk_update, run_async, "hello", msg_id=41)
    assert seen == [1]                        # written before the send
    assert list_outstanding_notes(NAME) == []   # gone after it


@pytest.fixture
def quick_wait(monkeypatch):
    from aipager.bot import session_ops
    monkeypatch.setattr(session_ops, "MET_DIALOG_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(session_ops, "MET_DIALOG_POLL_SECONDS", 0.01)


def test_a_dialog_gone_without_a_record_types_the_message_again(wired, mk_update, run_async,
                                                                quick_wait):
    """The probe saw a dialog, none is on record and the probe no longer
    sees one: nothing to hold for, so the message is typed again."""
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_BEFORE_TYPING, "open": False})
    _send(bot, mk_update, run_async, "hello", msg_id=41)
    assert _bodies(injected) == ["hello"]
    assert sess.pending_queue == []


def test_a_dialog_recorded_a_moment_later_holds_the_message_for_it(
        wired, mk_update, run_async, quick_wait, monkeypatch):
    """Its event came behind an older turn's finish: the message waits for
    the dialog to be on record, then is held for it like any other, so its
    answer releases it and a later message queues behind it."""
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_BEFORE_TYPING, "open": False})
    inject.set_dialog_probe(lambda _name: True)
    from aipager.bot import session_ops
    looks = []
    real_sleep = session_ops._sleep

    async def recording(secs):
        looks.append(secs)
        if len(looks) == 3:
            bot.registry.transition(NAME, Status.INTERACTIVE)
            sess.pending_permission = dict(PERM)
        await real_sleep(0)
    monkeypatch.setattr(session_ops, "_sleep", recording)
    _send(bot, mk_update, run_async, "hello", msg_id=41)
    assert injected == []
    assert [e[0] for e in sess.pending_queue] == ["hello"]
    assert sess.dialog_hold is not None
    notices = [c.kwargs for c in bot._app.bot.send_message.await_args_list]
    assert [n["text"] for n in notices] == [EN.text(HELD_PERMISSION)]
    inject.set_dialog_probe(None)
    _answer(bot, sess, run_async)
    assert _bodies(injected) == ["hello"]


def test_a_dialog_never_recorded_holds_the_message_for_the_turns_end(
        wired, mk_update, run_async, quick_wait):
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_BEFORE_TYPING, "open": False})
    inject.set_dialog_probe(lambda _name: True)
    _send(bot, mk_update, run_async, "hello", msg_id=41)
    assert injected == []
    assert [e[0] for e in sess.pending_queue] == ["hello"]
    assert sess.dialog_hold is None
    bot._app.bot.send_message.assert_not_awaited()
    inject.set_dialog_probe(None)
    bot.registry.transition(NAME, Status.IDLE)
    run_async(bot._drain_next_queued(sess))
    assert _bodies(injected) == ["hello"]


def test_a_model_switch_that_meets_a_dialog_is_refused_never_held(wired, mk_update,
                                                                  run_async):
    """A /model is never held: typed later it would skip its checks and its
    marker, and Claude Code's "Switch model?" question would take the next
    message's keys."""
    from aipager.bot.handlers import REFUSAL_FRAME
    from aipager.bot.session_ops import model_refusal_text
    from aipager.miniapp.sessions import MODEL_SWITCH_PROMPT_OPEN_REASON
    bot, sess, injected, meet = wired
    sess.status = Status.IDLE
    meet.append({"result": inject.DIALOG_BEFORE_TYPING})
    update = _send(bot, mk_update, run_async, "/model sonnet", msg_id=41, path="command")
    assert injected == []
    assert sess.pending_queue == []
    assert not sess.model_switch_pending()
    said = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert said == [EN.text(REFUSAL_FRAME, session="gc", reason=model_refusal_text(
        EN, "prompt_open", MODEL_SWITCH_PROMPT_OPEN_REASON))]


def test_a_full_queue_says_so(wired, mk_update, run_async, monkeypatch):
    from aipager.bot.handlers import QUEUE_CAP, QUEUE_FULL
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_BEFORE_TYPING})
    monkeypatch.setattr(sess, "queue_prompt", lambda *a, **k: False)
    update = _send(bot, mk_update, run_async, "hello", msg_id=41)
    assert injected == []
    said = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert said == [EN.n(QUEUE_FULL, QUEUE_CAP, session="gc")]
    bot._react.assert_not_awaited()


def test_a_drained_message_that_meets_a_dialog_goes_back_to_the_head(wired, run_async):
    bot, sess, injected, meet = wired
    bot.registry.transition(NAME, Status.IDLE)
    sess.queue_prompt("first", 41, "", 12345)
    sess.queue_prompt("second", 42, "", 12345)
    meet.append({"result": inject.DIALOG_WHILE_TYPING})
    run_async(bot._drain_next_queued(sess))
    assert injected == []
    assert [e[0] for e in sess.pending_queue] == ["first", "second"]
    assert sess.dialog_hold is not None
    # Answered: both go, in order (the second at the first one's turn end).
    _answer(bot, sess, run_async)
    assert _bodies(injected) == ["first", "second"]


def test_a_dialog_on_record_later_still_releases_through_the_hook(wired, mk_update,
                                                                   run_async):
    """Held for a dialog shown inline, the hook's own PostToolUse for that
    tool ends the wait and the message goes out with it."""
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_BEFORE_TYPING})
    _send(bot, mk_update, run_async, "held", msg_id=41)
    sess.pending_permission = {"tool_summary": "AskUserQuestion",
                               "tool_info": {"name": "AskUserQuestion", "input": {},
                                             "tool_use_id": "toolu_q"},
                               "ask_question": True, "question": "?", "options": [],
                               "questions": [], "current_idx": 0}
    recv = hr.HookReceiver(bot.registry, bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PostToolUse", "session": NAME,
        "tool_name": "AskUserQuestion", "tool_input": {},
        "tool_use_id": "toolu_q"}).encode()))
    assert _bodies(injected) == ["held"]


def _retry_query(update_chat=CHAT_ID):
    query = MagicMock()
    query.data = f"{NAME}:retry"
    query.message = MagicMock(text="error", message_id=700)
    edits: list[str] = []
    query.message.edit_text = AsyncMock(side_effect=lambda text, **kw: edits.append(text))
    query.message.chat = MagicMock(id=update_chat)
    query.from_user = MagicMock(id=1)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=update_chat)
    update.effective_user = MagicMock(id=1)
    return update, edits


def test_retry_that_meets_a_dialog_keeps_its_button(wired, run_async):
    bot, sess, injected, meet = wired
    sess.last_prompt = "the earlier prompt"
    bot.team = None
    bot.scopes = None
    answers: list[str] = []
    bot._safe_answer = AsyncMock(side_effect=lambda q, text="", **kw: answers.append(text))
    bot._app.bot.delete_message = AsyncMock()
    meet.append({"result": inject.DIALOG_BEFORE_TYPING})
    update, edits = _retry_query()
    run_async(bot._handle_callback(update, MagicMock()))
    assert injected == []
    assert sess.status is Status.INTERACTIVE
    assert edits and "Not retried" in edits[-1]
    bot._app.bot.delete_message.assert_not_awaited()


def test_the_compact_button_that_meets_a_dialog_is_not_sent(wired, run_async):
    bot, sess, injected, meet = wired
    answers: list[str] = []
    bot._safe_answer = AsyncMock(side_effect=lambda q, text="", **kw: answers.append(text))
    bot._app.bot.delete_message = AsyncMock()
    meet.append({"result": inject.DIALOG_BEFORE_TYPING})
    update, _edits = _retry_query()
    update.callback_query.data = f"{NAME}:compact"
    run_async(bot._handle_callback(update, MagicMock()))
    assert injected == []
    bot._app.bot.delete_message.assert_not_awaited()
    from aipager.bot.callbacks import TOAST_RETRY_PROMPT_OPEN
    assert answers[0] == EN.text(TOAST_RETRY_PROMPT_OPEN)


def test_the_inject_result_is_the_dialog_sentinel(wired, run_async):
    bot, sess, injected, meet = wired
    meet.append({"result": inject.DIALOG_WHILE_TYPING})
    bot.registry.transition(NAME, Status.IDLE)
    before = sess.prompt_sent_at
    ok = run_async(bot._inject_prompt(sess, "hello", msg_id=41, chat_id=CHAT_ID))
    assert ok is PROMPT_DIALOG
    assert not ok
    # Not a send: nothing for the "prompt not taken" watchdog to judge.
    assert sess.prompt_sent_at == before
