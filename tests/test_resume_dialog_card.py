"""Roadmap 8.127, the chat side: the card a question in Claude Code's
terminal gets (one per opening, only where aipager caused it or a message
met it), its buttons (exactly the keys verified live: Escape, never a
digit or an Enter into the dialog), the taps' gates, and the card closing
with the dialog. Every terminal write is captured (exact bytes)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import term_dialog
from aipager.bot import resume_dialog, session_parity
from aipager.bot.handlers import HELD_RESUME_DIALOG
from aipager.dtach import inject
from aipager.state import Status
from tests.resume_dialog_kit import CHAT_ID, EN, ESC, KILLS, LABEL, NAME, World, buttons


@pytest.fixture
def w(mk_bot, monkeypatch, tmp_path, run_async):
    return World(mk_bot, monkeypatch, tmp_path, run_async)


def _verbs(markup):
    return [cb.rsplit(":", 1)[-1] for _text, cb in buttons(markup)]


def _closes_on_escape(w):
    """Claude Code closes the dialog on Escape: its record goes idle."""
    def on_write(data):
        if data == ESC:
            w.idle(ago=0.0)
    w.on_write = on_write


# ---- when a card goes out ----------------------------------------------------------

def test_resume_card_sent_once_per_opening(w):
    w.after_resume()
    w.scan()
    sent = w.sent()
    assert len(sent) == 1
    assert sent[0]["text"].startswith(EN.html(resume_dialog.UNSURE_QUESTION, session=LABEL))
    assert sent[0]["chat_id"] == CHAT_ID
    w.scan()
    w.scan()
    assert len(w.sent()) == 1
    assert w.sess.term_dialog["card"] == (CHAT_ID, 900)


def test_idle_dialog_gets_nothing_until_message(w):
    w.cold_idle()
    w.scan()
    assert w.sess.term_dialog["kind"] == term_dialog.COLD_IDLE
    w.clock.advance(30)
    w.scan()
    assert w.sent() == []


def test_idle_dialog_card_when_message_held(w, mk_update):
    w.cold_idle()
    w.scan()
    w.send(mk_update, "hello", msg_id=41)
    sent = w.sent()
    assert sent[0]["text"] == EN.html(resume_dialog.COLD_QUESTION, session=LABEL)
    assert sent[0]["reply_to_message_id"] == 41
    assert _verbs(sent[0]["reply_markup"]) == [f"rdresume{w.sess.term_dialog['epoch']}",
                                               f"rdnew{w.sess.term_dialog['epoch']}"]
    # The held notice, under the card, once per person.
    assert sent[1]["text"] == EN.text(HELD_RESUME_DIALOG)
    assert sent[1]["reply_to_message_id"] == 900
    w.send(mk_update, "again", msg_id=42)
    assert len(w.sent()) == 2
    assert w.writes == []


def test_unknown_dialog_note_once_per_opening(w, mk_update):
    w.waiting()
    w.scan()
    assert w.sess.term_dialog["kind"] == term_dialog.OTHER
    assert w.sent() == []
    w.send(mk_update, "hello", msg_id=41)
    w.send(mk_update, "again", msg_id=42)
    sent = w.sent()
    assert [s["text"] for s in sent] == [EN.html(resume_dialog.OTHER_NOTE, session=LABEL)]
    assert "reply_markup" not in sent[0]
    assert sent[0]["reply_to_message_id"] == 41
    # The next opening gets its own.
    w.waiting(ago=0.5)
    w.scan()
    w.send(mk_update, "third", msg_id=43)
    assert len(w.sent()) == 2


def test_ensure_card_sends_once(w):
    """The card is claimed before its send: a second call (another message
    held meanwhile, the monitor's tick) sends nothing."""
    w.cold_idle()
    w.scan()

    async def both():
        import asyncio as _asyncio
        await _asyncio.gather(resume_dialog.ensure_card(w.bot, w.sess),
                              resume_dialog.ensure_card(w.bot, w.sess))
    w.run_async(both())
    assert len(w.sent()) == 1


def test_note_after_typed_command_only(w, monkeypatch):
    """An unknown dialog aipager's own typing opened (a bare /config from
    Telegram): its note goes out at once. One nobody typed into: nothing
    until a message meets it."""
    w.waiting(ago=1.5)
    w.scan()
    assert w.sent() == []
    w.sess.term_dialog = None
    monkeypatch.setitem(inject._LAST_WRITE_AT, NAME, w.clock.mono - 4.0)
    w.waiting(ago=1.4)
    w.scan()
    assert [s["text"] for s in w.sent()] == [EN.html(resume_dialog.OTHER_NOTE, session=LABEL)]


def test_card_waits_for_opening_to_settle(w):
    w.after_resume(ago=0.2)
    w.scan()
    assert w.sess.terminal_dialog_open()
    assert w.sent() == []
    w.clock.advance(term_dialog.CARD_SETTLE_SECONDS)
    w.scan()
    assert len(w.sent()) == 1


def test_unsure_card_offers_only_continue(w):
    w.after_resume()
    w.scan()
    markup = w.sent()[0]["reply_markup"]
    epoch = w.sess.term_dialog["epoch"]
    assert buttons(markup)[0][0] == EN.text(resume_dialog.BTN_CONTINUE)
    assert _verbs(markup) == [f"rdcont{epoch}"]


def test_cold_card_buttons(w):
    w.cold_idle()
    w.scan()
    markup = resume_dialog.keyboard(w.bot, CHAT_ID, w.sess, term_dialog.COLD_IDLE, EN)
    epoch = w.sess.term_dialog["epoch"]
    assert [t for t, _cb in buttons(markup)] == [EN.text(resume_dialog.BTN_RESUME),
                                                 EN.text(resume_dialog.BTN_NEW)]
    assert _verbs(markup) == [f"rdresume{epoch}", f"rdnew{epoch}"]
    assert resume_dialog.keyboard(w.bot, CHAT_ID, w.sess, term_dialog.OTHER, EN) is None


# ---- what a tap types ---------------------------------------------------------------

def test_continue_presses_escape_once(w, mk_update):
    """Through the dispatcher, as a real tap: exactly one Escape; the card
    then says the conversation goes on; the held message goes after."""
    w.after_resume()
    w.scan()
    w.send(mk_update, "hello", msg_id=41)
    epoch = w.sess.term_dialog["epoch"]
    _closes_on_escape(w)
    query = w.query()
    update = MagicMock()
    update.effective_chat.id = CHAT_ID
    handled = w.run_async(session_parity.handle_callback(
        w.bot, update, query, NAME, f"rdcont{epoch}"))
    assert handled is True
    assert w.writes == [ESC]
    assert not w.sess.terminal_dialog_open()
    edits = w.bot._app.bot.edit_message_text.await_args_list
    assert edits[-1].kwargs["text"] == EN.html(resume_dialog.CLOSED_CONTINUED, session=LABEL)
    assert edits[-1].kwargs["reply_markup"] is None
    w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    w.scan()
    assert w.bodies() == ["hello"]


def test_resume_presses_escape_once(w):
    w.cold_idle()
    w.scan()
    _closes_on_escape(w)
    w.tap("rdresume")
    assert w.writes == [ESC]
    assert not w.sess.terminal_dialog_open()


def test_new_conversation_escape_wipe_then_clear(w):
    w.cold_idle()
    w.scan()
    w.sess.term_dialog["card"] = (CHAT_ID, 900)
    _closes_on_escape(w)
    w.tap("rdnew")
    wipes = inject.discard_presses(term_dialog.WIPE_DRAFT_CHARS) // inject.DISCARD_KILL_BATCH
    assert w.writes == [ESC] + [KILLS] * wipes + [b"/clear", b"\r"]
    # A hold waits for the new conversation.
    assert w.sess.dialog_hold is not None and w.sess.dialog_hold.get("settling")
    assert w.sess.term_dialog_settle["expect"] == "clear"
    edits = w.bot._app.bot.edit_message_text.await_args_list
    assert edits[-1].kwargs["text"] == EN.html(resume_dialog.CLOSED_NEW, session=LABEL)


def test_new_conversation_settles_on_clear_start(w):
    """After Start a new conversation, the new conversation's SessionStart
    (source clear) settles the close, even when the record shows no newer
    idle status."""
    w.cold_idle()
    w.scan()
    opened = w.sess.term_dialog["status_at"]

    def on_write(data):
        if data == ESC:
            w.record(status="idle", statusUpdatedAt=int((opened - 5) * 1000))
    w.on_write = on_write
    w.tap("rdnew")
    hold = w.sess.dialog_hold
    assert hold.get("settling") is True
    w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    w.scan()
    assert hold.get("settling") is True
    w.sess.session_start_at["clear"] = w.clock.wall
    w.scan()
    assert w.sess.dialog_hold is None or not w.sess.dialog_hold.get("settling")


def test_card_closed_at_once_when_dialog_closed_while_sending(w):
    w.after_resume()

    async def send_message(*a, **kw):
        w.sess.term_dialog = None        # closed while the card went out
        return MagicMock(message_id=901)
    w.bot._app.bot.send_message = AsyncMock(side_effect=send_message)
    w.scan()
    edits = w.bot._app.bot.edit_message_text.await_args_list
    assert [e.kwargs["message_id"] for e in edits] == [901]
    assert edits[0].kwargs["reply_markup"] is None


def test_new_conversation_refused_before_escape(w, monkeypatch):
    w.cold_idle()
    w.scan()
    monkeypatch.setattr(w.bot, "_command_needs_admin", lambda *a, **k: True)
    query = w.tap("rdnew")
    assert w.writes == []
    assert w.sess.terminal_dialog_open()
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_new_conversation_starts_no_busy_card(w):
    w.cold_idle()
    w.scan()
    _closes_on_escape(w)
    w.tap("rdnew")
    assert b"/clear" in w.writes
    assert w.sess.status is Status.IDLE
    w.bot._send_busy_and_animate.assert_not_awaited()


def test_tap_after_dialog_closed_types_nothing(w):
    w.after_resume()
    w.scan()
    w.idle(ago=0.0)                 # answered in the terminal, not seen yet
    query = w.tap("rdcont")
    assert w.writes == []
    assert EN.text(resume_dialog.TOAST_CLOSED) in w.toasts(query)
    assert not w.sess.terminal_dialog_open()


def test_tap_for_older_opening_types_nothing(w):
    """The record shows another opening (the newer-model offer after it)
    that the monitor has not seen yet: the old card's key is not sent."""
    w.after_resume(ago=3.0)
    w.scan()
    w.waiting(ago=0.2)
    w.tap("rdcont")
    assert w.writes == []


def test_stale_card_tap_refused(w):
    w.after_resume()
    w.scan()
    epoch = w.sess.term_dialog["epoch"]
    query = w.tap("rdcont", epoch=epoch - 1)
    assert w.writes == []
    assert w.toasts(query) == [EN.text(resume_dialog.TOAST_CLOSED)]
    query.edit_message_reply_markup.assert_awaited()
    # And a verb the opening's card does not carry.
    query = w.tap("rdnew", epoch=epoch)
    assert w.writes == []
    assert w.toasts(query) == [EN.text(resume_dialog.TOAST_CLOSED)]


def test_double_tap_answers_once(w):
    """The dialog stays up after the first Escape (so a second one would
    find it open): the second tap is refused all the same."""
    w.after_resume()
    w.scan()
    epoch = w.sess.term_dialog["epoch"]
    q1, q2 = w.query(), w.query()

    async def both():
        await asyncio.gather(
            resume_dialog.handle_tap(w.bot, MagicMock(), q1, w.sess, "rdcont", epoch,
                                     CHAT_ID, 1, EN, EN),
            resume_dialog.handle_tap(w.bot, MagicMock(), q2, w.sess, "rdcont", epoch,
                                     CHAT_ID, 1, EN, EN))
    w.run_async(both())
    assert w.writes == [ESC]
    assert EN.text(resume_dialog.TOAST_ANSWERING) in w.toasts(q2)


def test_tap_refused_while_busy(w):
    w.after_resume()
    w.scan()
    w.sess.status = Status.BUSY
    query = w.tap("rdcont")
    assert w.writes == []
    assert w.toasts(query) == [EN.text(resume_dialog.TOAST_NOT_IDLE)]


# ---- the key primitive ---------------------------------------------------------------

def test_dialog_key_refuses_parked(w):
    inject.set_parked_probe(lambda name: ("ab12cd34", 1))
    asked = []

    async def still_open():
        asked.append(1)
        return True
    assert w.run_async(inject.press_key_in_dialog(NAME, "Escape", still_open)) is False
    assert w.writes == []
    inject.set_parked_probe(None)
    assert w.run_async(inject.press_key_in_dialog(NAME, "Escape", still_open)) is True
    assert w.writes == [ESC]


def test_dialog_key_under_write_lock(w):
    seen = []

    async def still_open():
        seen.append(inject._write_lock(NAME).locked())
        return True

    async def closed():
        return False
    assert w.run_async(inject.press_key_in_dialog(NAME, "Escape", still_open)) is True
    assert seen == [True]
    assert w.writes == [ESC]
    assert w.run_async(inject.press_key_in_dialog(NAME, "Escape", closed)) is False
    assert w.writes == [ESC]
    assert w.run_async(inject.press_key_in_dialog(NAME, "1", still_open)) is False


# ---- the card closes with the dialog -------------------------------------------------

def test_card_closed_when_dialog_closes(w):
    w.after_resume()
    w.scan()
    w.idle(ago=0.0)
    w.scan()
    edits = w.bot._app.bot.edit_message_text.await_args_list
    assert len(edits) == 1
    assert edits[0].kwargs["message_id"] == 900
    assert edits[0].kwargs["text"] == EN.html(resume_dialog.CLOSED_TERMINAL, session=LABEL)
    assert edits[0].kwargs["reply_markup"] is None


def test_card_closed_on_session_end(w):
    w.after_resume()
    w.scan()
    w.bot.registry.transition(NAME, Status.GONE)
    w.run_async(w.bot.notify(w.sess, "dialog_hold_settle", {}))
    edits = w.bot._app.bot.edit_message_text.await_args_list
    assert edits[-1].kwargs["text"] == EN.html(resume_dialog.CLOSED_ENDED, session=LABEL)
    assert w.sess.term_dialog_closing == []


def test_note_deleted_on_close(w, mk_update):
    w.waiting()
    w.scan()
    w.send(mk_update, "hello", msg_id=41)
    w.bot._app.bot.delete_message = AsyncMock()
    w.idle(ago=0.0)
    w.scan()
    w.bot._app.bot.delete_message.assert_any_await(chat_id=CHAT_ID, message_id=900)
    w.bot._app.bot.edit_message_text.assert_not_awaited()
