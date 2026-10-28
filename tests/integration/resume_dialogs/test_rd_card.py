"""Black-box rows for roadmap 8.127's chat side (design.md success criteria
6 to 11): which dialog gets a card or a note and when, what each button
writes (exact bytes through ``inject._run``), stale and double taps, and
how a card or note ends."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aipager import term_dialog
from aipager.bot import resume_dialog
from aipager.state import Status
from tests.resume_dialog_kit import CHAT_ID, EN, ESC, NAME, USER, buttons

UNSURE_TEXT = "Claude Code is asking a question in the terminal before it goes on"
COLD_TEXT = "Claude Code asks whether to resume this conversation."
NOTE_TEXT = "is waiting on a question in its terminal."
NO_LONGER_OPEN = "This question is no longer open."
ANSWERING = "This question is being answered already."
NOT_IDLE = "Claude is working right now. Try again when it is done."


def _cards(world, needle):
    return [s for s in world.sent() if needle in (s.get("text") or "")]


def _close_on_escape(world):
    def on_write(data):
        if data == ESC:
            world.idle(ago=0.0)
    world.on_write = on_write


def _edits(world):
    return [c for c in world.bot._app.bot.method_calls if c[0] == "edit_message_text"]


def _deletes(world):
    return [c for c in world.bot._app.bot.method_calls if c[0] == "delete_message"]


# -- criterion 6: the resume card ------------------------------------------------------

def test_dialog_after_resume_gets_card(world):
    world.after_resume()
    world.scan()
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_dialog_after_resume_card_once_over_ticks(world):
    world.after_resume()
    for _ in range(4):
        world.scan()
        world.clock.advance(2)
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_resume_card_goes_to_session_chat(world):
    world.after_resume()
    world.scan()
    assert _cards(world, UNSURE_TEXT)[0]["chat_id"] == CHAT_ID


def test_resume_card_offers_only_continue(world):
    world.after_resume()
    world.scan()
    markup = _cards(world, UNSURE_TEXT)[0]["reply_markup"]
    assert [t for t, _ in buttons(markup)] == ["Continue this conversation"]


def test_resume_card_button_carries_epoch_verb(world):
    world.after_resume()
    world.scan()
    markup = _cards(world, UNSURE_TEXT)[0]["reply_markup"]
    assert buttons(markup)[0][1].endswith(f"rdcont{world.sess.term_dialog_epoch}")


def test_resume_card_never_offers_dont_ask_again(world):
    world.after_resume()
    world.scan()
    markup = _cards(world, UNSURE_TEXT)[0]["reply_markup"]
    assert not any("ask" in t.lower() for t, _ in buttons(markup))


def test_resume_card_not_before_one_second_old(world):
    world.after_resume(ago=0.2)
    world.scan()
    assert _cards(world, UNSURE_TEXT) == []


def test_resume_card_after_one_second(world):
    world.after_resume(ago=0.2)
    world.scan()
    world.clock.advance(term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_resume_card_not_sent_when_closed_within_one_second(world):
    world.after_resume(ago=0.2)
    world.scan()
    world.idle()
    world.clock.advance(term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    assert _cards(world, UNSURE_TEXT) == []


def test_continue_writes_exactly_one_escape(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdcont")
    assert world.writes == [ESC]


def test_continue_closes_card_as_continued(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdcont")
    world.scan()
    assert any("the conversation goes on" in str(c) for c in _edits(world))


def test_continue_keeps_session_id(world):
    sid = world.sess.claude_session_id
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdcont")
    world.scan()
    assert world.sess.claude_session_id == sid


def test_continue_types_no_clear(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdcont")
    for _ in range(3):
        world.clock.advance(2)
        world.idle()
        world.scan()
    assert world.typed() == [ESC]


def test_dialog_29_s_after_resume_gets_card(world):
    world.after_resume(ago=1.5)
    world.sess.session_start_at["resume"] = world.clock.wall - 1.5 - 29.0
    world.scan()
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_dialog_31_s_after_resume_gets_no_card(world):
    world.after_resume(ago=1.5)
    world.sess.session_start_at["resume"] = world.clock.wall - 1.5 - 31.0
    world.scan()
    assert _cards(world, UNSURE_TEXT) == []


def test_dialog_after_startup_gets_no_resume_card(world):
    world.sess.session_start_at["startup"] = world.clock.wall - 2.5
    world.waiting(ago=1.5)
    world.scan()
    assert _cards(world, UNSURE_TEXT) == []


def test_new_opening_after_resume_gets_its_own_card(world):
    """A newer-model offer after the first answer is its own opening."""
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdcont")
    world.scan()
    world.on_write = None
    world.clock.advance(1.0)
    world.waiting(ago=0.0)
    world.clock.advance(term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    world.waiting(ago=term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    assert len(_cards(world, UNSURE_TEXT)) == 2


# -- criterion 7: the idle cold dialog ---------------------------------------------------

def test_idle_cold_dialog_gets_nothing_when_it_opens(world):
    world.cold_idle()
    for _ in range(3):
        world.scan()
        world.clock.advance(5)
    assert world.sent() == []


def test_message_meeting_idle_cold_dialog_gets_cold_card(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert len(_cards(world, COLD_TEXT)) == 1


def test_cold_card_replies_to_the_message(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    card = _cards(world, COLD_TEXT)[0]
    assert card.get("reply_to_message_id") == 10 or getattr(
        card.get("reply_parameters"), "message_id", None) == 10


def test_cold_card_buttons(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    markup = _cards(world, COLD_TEXT)[0]["reply_markup"]
    assert [t for t, _ in buttons(markup)] == ["Resume", "Start a new conversation"]


def test_cold_card_once_for_two_messages(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.send(mk_update, "again", msg_id=11)
    assert len(_cards(world, COLD_TEXT)) == 1


def test_cold_card_held_notice_once_per_person(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.send(mk_update, "again", msg_id=11)
    assert len(_cards(world, "Answer the question above")) == 1


def test_cold_card_message_typed_nothing(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert world.writes == []


def test_resume_button_writes_exactly_one_escape(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    _close_on_escape(world)
    world.tap("rdresume")
    assert world.writes == [ESC]


def test_resume_then_held_message_delivered(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    _close_on_escape(world)
    world.tap("rdresume")
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["hello"]


def _new_conversation(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    _close_on_escape(world)
    return world.tap("rdnew")


def test_new_conversation_first_write_is_escape(world, mk_update):
    _new_conversation(world, mk_update)
    assert world.writes[0] == ESC


def test_new_conversation_wipes_before_clear(world, mk_update):
    _new_conversation(world, mk_update)
    clear = world.writes.index(b"/clear")
    wiped = sum(len(w) for w in world.writes[1:clear] if w and set(w) <= {0x15})
    assert wiped >= term_dialog.WIPE_DRAFT_CHARS


def test_new_conversation_wipe_only_ctrl_u(world, mk_update):
    _new_conversation(world, mk_update)
    clear = world.writes.index(b"/clear")
    assert all(set(w) <= {0x15} for w in world.writes[1:clear])


def test_new_conversation_types_clear_then_enter(world, mk_update):
    _new_conversation(world, mk_update)
    assert world.typed() == [ESC, b"/clear", b"\r"]


def test_new_conversation_refused_if_dialog_never_closes(world, mk_update):
    """Escape went out but the record stays waiting: /clear is never typed
    into a dialog."""
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.tap("rdnew")
    assert b"/clear" not in world.writes


def test_new_conversation_refused_without_clear_permission(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    _close_on_escape(world)
    world.bot._command_needs_admin = MagicMock(return_value=True)
    world.tap("rdnew")
    assert world.writes == []


def test_new_conversation_starts_no_busy_card(world, mk_update):
    _new_conversation(world, mk_update)
    assert world.bot._send_busy_and_animate.await_count == 0


def test_new_conversation_leaves_session_idle(world, mk_update):
    _new_conversation(world, mk_update)
    assert world.sess.status == Status.IDLE


def test_new_conversation_held_message_not_typed_with_the_clear(world, mk_update):
    """Room for /clear to start the new conversation before the held
    message goes."""
    _new_conversation(world, mk_update)
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS - 0.5)
    world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_held_message_lands_after_clear(world, mk_update):
    _new_conversation(world, mk_update)
    world.sess.claude_session_id = "33333333-3333-4333-8333-333333333333"
    world.sess.session_start_at["clear"] = world.clock.wall + 0.2
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear", "hello"]


def test_new_conversation_card_closed_as_new(world, mk_update):
    _new_conversation(world, mk_update)
    world.scan()
    assert any("a new conversation was started" in str(c) for c in _edits(world))


# -- classification boundaries (criteria 6 and 7 via term_dialog.classify) -----------

def _entry(world, status_at, waiting_for="dialog open"):
    return {"pid": 4100, "kind": "interactive", "status": "waiting",
            "waitingFor": waiting_for, "statusAt": status_at,
            "sessionId": world.sess.claude_session_id, "procStart": 777}


def _classify(world, *, age=600.0, quiet=600.0, hook_quiet=None, waiting_for="dialog open",
              resume_ago=None):
    at = world.clock.wall - 1.0
    if resume_ago is not None:
        world.sess.session_start_at["resume"] = at - resume_ago
    return term_dialog.classify(
        world.sess, _entry(world, at, waiting_for), now_wall=world.clock.wall,
        last_write_wall=at - quiet, last_exchange_wall=at - age,
        last_hook_wall=0.0 if hook_quiet is None else at - hook_quiet)


def test_classify_old_quiet_is_cold_idle(world):
    assert _classify(world) == term_dialog.COLD_IDLE


def test_classify_age_just_over_240_is_cold_idle(world):
    assert _classify(world, age=term_dialog.IDLE_DIALOG_MIN_AGE + 0.5) == term_dialog.COLD_IDLE


def test_classify_age_just_under_240_is_other(world):
    assert _classify(world, age=term_dialog.IDLE_DIALOG_MIN_AGE - 0.5) == term_dialog.OTHER


def test_classify_write_just_over_55_s_is_cold_idle(world):
    assert _classify(world, quiet=term_dialog.IDLE_DIALOG_MIN_QUIET + 0.5) == term_dialog.COLD_IDLE


def test_classify_write_just_under_55_s_is_other(world):
    assert _classify(world, quiet=term_dialog.IDLE_DIALOG_MIN_QUIET - 0.5) == term_dialog.OTHER


def test_classify_hook_just_under_55_s_is_other(world):
    assert _classify(world, hook_quiet=term_dialog.IDLE_DIALOG_MIN_QUIET - 0.5) == term_dialog.OTHER


def test_classify_hook_just_over_55_s_is_cold_idle(world):
    assert _classify(world, hook_quiet=term_dialog.IDLE_DIALOG_MIN_QUIET + 0.5) == term_dialog.COLD_IDLE


def test_classify_unknown_exchange_is_other(world):
    at = world.clock.wall - 1.0
    assert term_dialog.classify(
        world.sess, _entry(world, at), now_wall=world.clock.wall,
        last_write_wall=0.0, last_exchange_wall=None) == term_dialog.OTHER


def test_classify_input_needed_is_other(world):
    assert _classify(world, waiting_for="input needed") == term_dialog.OTHER


def test_classify_input_needed_after_resume_is_other(world):
    assert _classify(world, waiting_for="input needed", resume_ago=2.0) == term_dialog.OTHER


def test_classify_resume_wins_over_cold(world):
    assert _classify(world, resume_ago=2.0) == term_dialog.RESUME_UNSURE


def test_classify_resume_29_s_is_unsure(world):
    assert _classify(world, resume_ago=term_dialog.RESUME_CONTEXT_SECONDS - 1) \
        == term_dialog.RESUME_UNSURE


def test_classify_resume_31_s_is_not_unsure(world):
    assert _classify(world, resume_ago=term_dialog.RESUME_CONTEXT_SECONDS + 1) \
        != term_dialog.RESUME_UNSURE


def test_classify_dialog_before_resume_start_is_not_unsure(world):
    """A record older than the resume start belongs to the old claude."""
    assert _classify(world, resume_ago=-60.0) != term_dialog.RESUME_UNSURE


# -- criterion 8: the note -------------------------------------------------------------

def test_unknown_dialog_note_when_message_meets_it(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert len(_cards(world, NOTE_TEXT)) == 1


def test_unknown_dialog_note_has_no_buttons(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert _cards(world, NOTE_TEXT)[0].get("reply_markup") is None


def test_unknown_dialog_note_once_for_two_messages(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.send(mk_update, "again", msg_id=11)
    assert len(_cards(world, NOTE_TEXT)) == 1


def test_unknown_dialog_not_involved_gets_nothing(world):
    world.waiting(ago=1.5)
    for _ in range(3):
        world.scan()
        world.clock.advance(3)
    assert world.sent() == []


def test_unknown_dialog_note_after_aipager_typed(world, mk_update):
    world.idle(ago=5)
    world.send(mk_update, "check this", msg_id=10)
    world.bot.registry.transition(NAME, Status.IDLE)
    world.waiting(ago=0.0)
    world.scan()
    world.clock.advance(term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    assert len(_cards(world, NOTE_TEXT)) == 1


def test_unknown_dialog_11_s_after_aipager_typed_gets_nothing(world, mk_update):
    world.idle(ago=5)
    world.send(mk_update, "check this", msg_id=10)
    world.bot.registry.transition(NAME, Status.IDLE)
    world.clock.advance(term_dialog.AFTER_WRITE_NOTE_SECONDS + 1)
    world.waiting(ago=0.0)
    world.scan()
    world.clock.advance(term_dialog.CARD_SETTLE_SECONDS + 0.5)
    world.scan()
    assert _cards(world, NOTE_TEXT) == []


def test_unknown_dialog_note_again_for_new_opening(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.idle()
    world.scan()
    world.clock.advance(1)
    world.waiting(ago=0.5)
    world.scan()
    world.send(mk_update, "again", msg_id=11)
    assert len(_cards(world, NOTE_TEXT)) == 2


def test_unknown_dialog_message_types_nothing(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert world.writes == []


# -- criterion 9: stale and double taps ----------------------------------------------

def test_tap_after_closed_writes_nothing(world):
    world.after_resume()
    world.scan()
    world.idle()
    world.tap("rdcont")
    assert world.writes == []


def test_tap_after_closed_says_no_longer_open(world):
    world.after_resume()
    world.scan()
    world.idle()
    q = world.tap("rdcont")
    assert NO_LONGER_OPEN in world.toasts(q)


def test_tap_after_closed_and_seen_writes_nothing(world):
    world.after_resume()
    world.scan()
    epoch = world.sess.term_dialog_epoch
    world.idle()
    world.scan()
    world.tap("rdcont", epoch=epoch)
    assert world.writes == []


def test_tap_after_closed_and_seen_says_no_longer_open(world):
    world.after_resume()
    world.scan()
    epoch = world.sess.term_dialog_epoch
    world.idle()
    world.scan()
    q = world.tap("rdcont", epoch=epoch)
    assert NO_LONGER_OPEN in world.toasts(q)


def test_tap_older_opening_writes_nothing(world):
    world.after_resume()
    world.scan()
    old = world.sess.term_dialog_epoch
    world.clock.advance(1)
    world.waiting(ago=0.5)
    world.scan()
    world.tap("rdcont", epoch=old)
    assert world.writes == []


def test_tap_older_opening_says_no_longer_open(world):
    world.after_resume()
    world.scan()
    old = world.sess.term_dialog_epoch
    world.clock.advance(1)
    world.waiting(ago=0.5)
    world.scan()
    q = world.tap("rdcont", epoch=old)
    assert NO_LONGER_OPEN in world.toasts(q)


def test_tap_older_opening_drops_keyboard(world):
    world.after_resume()
    world.scan()
    old = world.sess.term_dialog_epoch
    world.clock.advance(1)
    world.waiting(ago=0.5)
    world.scan()
    q = world.tap("rdcont", epoch=old)
    assert q.edit_message_reply_markup.await_count + q.edit_message_text.await_count >= 1


def test_tap_record_changed_to_new_opening_unseen_writes_nothing(world):
    """The record shows a newer opening the tick has not seen yet."""
    world.after_resume()
    world.scan()
    world.clock.advance(1)
    world.waiting(ago=0.2)
    world.tap("rdcont")
    assert world.writes == []


def test_tap_when_claude_gone_writes_nothing(world):
    world.after_resume()
    world.scan()
    (world.cfg / "sessions" / "4100.json").unlink()
    world.tap("rdcont")
    assert world.writes == []


def test_double_tap_answers_once(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    epoch = world.sess.term_dialog_epoch

    async def both():
        q1, q2 = world.query(), world.query()
        await asyncio.gather(*(resume_dialog.handle_tap(
            world.bot, MagicMock(), q, world.sess, "rdcont", epoch, CHAT_ID, USER, EN, EN)
            for q in (q1, q2)))
    world.run_async(both())
    assert world.writes == [ESC]


def test_double_tap_second_says_answering(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    epoch = world.sess.term_dialog_epoch
    q1, q2 = world.query(), world.query()

    async def both():
        await asyncio.gather(*(resume_dialog.handle_tap(
            world.bot, MagicMock(), q, world.sess, "rdcont", epoch, CHAT_ID, USER, EN, EN)
            for q in (q1, q2)))
    world.run_async(both())
    assert ANSWERING in world.toasts(q1) + world.toasts(q2)


def test_second_tap_after_answer_writes_nothing_more(world):
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    epoch = world.sess.term_dialog_epoch
    world.tap("rdcont", epoch=epoch)
    world.tap("rdcont", epoch=epoch)
    assert world.writes == [ESC]


def test_tap_while_busy_writes_nothing(world):
    world.after_resume()
    world.scan()
    world.bot.registry.transition(NAME, Status.BUSY)
    world.tap("rdcont")
    assert world.writes == []


def test_tap_while_busy_says_working(world):
    world.after_resume()
    world.scan()
    world.bot.registry.transition(NAME, Status.BUSY)
    q = world.tap("rdcont")
    assert NOT_IDLE in world.toasts(q)


def test_resume_tap_on_unsure_card_writes_only_escape(world):
    """A forged rdnew on a card that never offered it never types /clear."""
    world.after_resume()
    world.scan()
    _close_on_escape(world)
    world.tap("rdnew")
    assert b"/clear" not in world.writes


# -- criterion 10: closing -------------------------------------------------------------

def test_card_edited_when_dialog_closes_in_terminal(world):
    world.after_resume()
    world.scan()
    world.idle()
    world.scan()
    assert any("answered in the terminal" in str(c) for c in _edits(world))


def test_card_edit_on_close_drops_buttons(world):
    world.after_resume()
    world.scan()
    world.idle()
    world.scan()
    assert all(c.kwargs.get("reply_markup") is None for c in _edits(world))


def test_card_edited_once_on_close(world):
    world.after_resume()
    world.scan()
    world.idle()
    for _ in range(3):
        world.scan()
        world.clock.advance(2)
    assert len(_edits(world)) == 1


def test_card_edited_on_session_end(world):
    world.after_resume()
    world.scan()

    async def none():
        return []
    world.mp.setattr("aipager.dtach.inject.list_sessions", none)
    world.run_async(world.monitor._scan())
    assert any("the session ended" in str(c) for c in _edits(world))


def test_note_deleted_when_dialog_closes(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    note_id = 900
    world.idle()
    world.scan()
    assert any(c.kwargs.get("message_id") == note_id for c in _deletes(world))


def test_note_not_edited_when_dialog_closes(world, mk_update):
    world.waiting(ago=1.5)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.idle()
    world.scan()
    assert _edits(world) == []


def test_held_notice_deleted_at_release(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    notice = [i for i, s in enumerate(world.sent()) if "Answer the question above" in s["text"]]
    notice_id = 900 + notice[0]
    _close_on_escape(world)
    world.tap("rdresume")
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert any(c.kwargs.get("message_id") == notice_id for c in _deletes(world))


# -- callback verbs and tap capabilities ---------------------------------------------

@pytest.mark.parametrize("action,expected", [
    ("rdcont1", ("rdcont", 1)),
    ("rdresume42", ("rdresume", 42)),
    ("rdnew7", ("rdnew", 7)),
    ("rdcont", None),
    ("rdcontx", None),
    ("rdnew-1", None),
    ("rdnever1", None),
    ("", None),
], ids=["cont", "resume", "new", "no-epoch", "letters", "negative", "never", "empty"])
def test_parse_verb(action, expected):
    assert resume_dialog.parse_verb(action) == expected


@pytest.mark.parametrize("verb,cap", [
    ("rdcont3", "APPROVE"), ("rdresume3", "APPROVE"), ("rdnew3", "PROMPT"),
], ids=["cont", "resume", "new"])
def test_tap_capability(verb, cap):
    from aipager.bot import tap_gate
    assert tap_gate.required_capability(NAME, verb) == getattr(tap_gate, cap)


def test_keyboard_for_other_is_none(world):
    assert resume_dialog.keyboard(world.bot, CHAT_ID, world.sess, term_dialog.OTHER) is None
