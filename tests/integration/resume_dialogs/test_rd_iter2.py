"""Black-box rows for the behaviour roadmap 8.127 changed in iteration 2:
the post-resume look is 1.5 s; "Start a new conversation" releases held
messages on the new conversation's SessionStart (source clear), falls back
to the idle record when /clear could not be typed, and has the 180 s
backstop; only activity before the dialog's statusAt counts against the
idle cold question; messages during the post-resume look keep their order;
an in-place mode switch looks at the record before Shift+Tab; a session
whose claude pid is unknown is looked up before typing; the resume card is
sent when it falls due."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager import term_dialog
from aipager.bot import session_ops
from aipager.dtach import inject
from aipager.state import Status
from tests.resume_dialog_kit import CHAT_ID, ESC, NAME, SID, USER

SHIFT_TAB = b"\x1b[Z"
UNSURE_TEXT = "Claude Code is asking a question in the terminal before it goes on"
COLD_TEXT = "Claude Code asks whether to resume this conversation."
NOTE_TEXT = "is waiting on a question in its terminal."


def _cards(world, needle):
    return [s for s in world.sent() if needle in (s.get("text") or "")]


def _close_on_escape(world):
    def on_write(data):
        if data == ESC:
            world.idle(ago=0.0)
    world.on_write = on_write


def _time_passes_on_sleep(world):
    """Each post-resume poll lets its time pass on the seam clocks."""
    world.on_sleep = lambda n: world.clock.advance(world.sleeps[-1])


def _resume_started(world, ago):
    world.sess.session_start_at["resume"] = world.clock.wall - ago


# -- F1: the post-resume look is 1.5 s ---------------------------------------------------

def test_resume_type_settle_window_is_one_and_a_half_seconds():
    assert term_dialog.RESUME_TYPE_SETTLE_SECONDS == 1.5


def test_message_1_4_s_after_resume_still_looks(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 1.4)
    _time_passes_on_sleep(world)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sleeps != []


def test_message_1_6_s_after_resume_is_not_delayed(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 1.6)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sleeps == []


def test_message_1_6_s_after_resume_is_typed(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 1.6)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_look_after_resume_ends_by_the_window_end(world, mk_update):
    """Started 0.5 s after SessionStart: at most 1.0 s more of waiting."""
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    _time_passes_on_sleep(world)
    world.send(mk_update, "hello there", msg_id=10)
    assert sum(world.sleeps) <= 1.0 + 1e-6


def test_message_typed_after_an_empty_look(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    _time_passes_on_sleep(world)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_dialog_drawing_1_25_s_after_resume_holds_message(world, mk_update):
    world.idle(ago=0.0)
    start = world.clock.wall - 0.1
    world.sess.session_start_at["resume"] = start

    def on_sleep(_n):
        world.clock.advance(world.sleeps[-1])
        if world.clock.wall >= start + 1.25:
            world.waiting(ago=0.0)
    world.on_sleep = on_sleep
    world.send(mk_update, "hello there", msg_id=10)
    assert world.writes == []


# -- messages during the post-resume look keep their order -------------------------------

def _two_during_look(world, mk_update, *, draw=False):
    """A arrives, waits out the post-resume look; B arrives during A's look."""
    world.idle(ago=0.0)
    _resume_started(world, 0.2)

    async def look_sleep(seconds):
        world.sleeps.append(seconds)
        world.clock.advance(seconds)
        if draw and len(world.sleeps) == 2:
            world.waiting(ago=0.0)
        await asyncio.sleep(0.02)

    world.mp.setattr(session_ops, "_sleep", look_sleep)
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    first = mk_update("first one", message_id=10, user_id=USER, chat_id=CHAT_ID)
    second = mk_update("second one", message_id=11, user_id=USER, chat_id=CHAT_ID)

    async def later():
        await asyncio.sleep(0.03)
        await world.bot._handle_message(second, ctx)

    async def both():
        await asyncio.gather(world.bot._handle_message(first, ctx), later())

    world.run_async(both())


def _drain(world, rounds=4):
    for _ in range(rounds):
        world.bot.registry.transition(NAME, Status.IDLE)
        world.clock.advance(1)
        world.idle()
        world.scan()


def test_two_messages_in_post_resume_look_first_typed_first(world, mk_update):
    _two_during_look(world, mk_update)
    _drain(world)
    assert world.bodies()[:1] == ["first one"]


def test_two_messages_in_post_resume_look_keep_order(world, mk_update):
    _two_during_look(world, mk_update)
    _drain(world)
    assert [b for b in world.bodies() if b.endswith(" one")] == ["first one", "second one"]


def test_two_messages_held_by_post_resume_dialog_type_nothing(world, mk_update):
    _two_during_look(world, mk_update, draw=True)
    assert world.writes == []


def test_two_messages_held_by_post_resume_dialog_keep_order(world, mk_update):
    _two_during_look(world, mk_update, draw=True)
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    _drain(world)
    assert [b for b in world.bodies() if b.endswith(" one")] == ["first one", "second one"]


# -- "Start a new conversation": release waits for the new conversation --------------------

def _new_conversation(world, mk_update):
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    _close_on_escape(world)
    return world.tap("rdnew")


def test_new_conversation_not_released_by_idle_record_alone(world, mk_update):
    """The idle record the Escape produced is not the new conversation."""
    _new_conversation(world, mk_update)
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_not_released_by_later_idle_ticks(world, mk_update):
    _new_conversation(world, mk_update)
    for _ in range(5):
        world.clock.advance(5)
        world.idle()
        world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_clear_start_before_tap_does_not_release(world, mk_update):
    world.sess.session_start_at["clear"] = world.clock.wall - 30
    _new_conversation(world, mk_update)
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_released_on_clear_start(world, mk_update):
    _new_conversation(world, mk_update)
    world.sess.session_start_at["clear"] = world.clock.wall + 0.2
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear", "hello"]


def test_new_conversation_resume_start_does_not_release(world, mk_update):
    """Only the SessionStart of the /clear (source clear) counts."""
    _new_conversation(world, mk_update)
    world.sess.session_start_at["resume"] = world.clock.wall + 0.2
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_backstop_releases(world, mk_update):
    _new_conversation(world, mk_update)
    world.clock.advance(1)
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.AFTER_ANSWER_MAX_WAIT + 1)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear", "hello"]


def test_new_conversation_not_released_before_backstop(world, mk_update):
    _new_conversation(world, mk_update)
    world.clock.advance(1)
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.AFTER_ANSWER_MAX_WAIT - 10)
    world.idle()
    world.scan()
    assert world.bodies() == ["/clear"]


def test_new_conversation_without_clear_released_on_idle_record(world, mk_update):
    """Escape went out but the dialog stayed open past the close wait, so
    /clear was never typed: once it closes, the idle record releases."""
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    world.tap("rdnew")
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.bodies() == ["hello"]


def test_new_conversation_clear_write_failed_released_on_idle_record(world, mk_update):
    """Escape closed the dialog but typing /clear failed: the idle record
    releases the held message well before the backstop."""
    world.cold_idle()
    world.scan()
    world.send(mk_update, "hello", msg_id=10)

    async def run(args, stdin=b"", timeout=5):
        data = bytes(stdin)
        world.writes.append(data)
        if data == ESC:
            world.idle(ago=0.0)
        return (b"/clear" not in data), ""

    world.mp.setattr(inject, "_run", run)
    world.tap("rdnew")
    world.mp.setattr(inject, "_run", _ok_run(world))
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()
    assert world.writes[-2:] == [b"hello", b"\r"]


def _ok_run(world):
    async def run(args, stdin=b"", timeout=5):
        world.writes.append(bytes(stdin))
        return True, ""
    return run


# -- only activity before statusAt counts against the idle cold question ------------------

def _entry(status_at):
    return {"pid": 4100, "kind": "interactive", "status": "waiting",
            "waitingFor": "dialog open", "statusAt": status_at,
            "sessionId": SID, "procStart": 777}


def _classify(world, *, write_offset=-600.0, hook_offset=None):
    at = world.clock.wall - 20.0
    return term_dialog.classify(
        world.sess, _entry(at), now_wall=world.clock.wall,
        last_write_wall=at + write_offset, last_exchange_wall=at - 600.0,
        last_hook_wall=0.0 if hook_offset is None else at + hook_offset)


def test_classify_write_after_status_at_is_still_cold_idle(world):
    assert _classify(world, write_offset=5.0) == term_dialog.COLD_IDLE


def test_classify_write_just_after_status_at_is_cold_idle(world):
    assert _classify(world, write_offset=0.5) == term_dialog.COLD_IDLE


def test_classify_write_just_before_status_at_is_other(world):
    assert _classify(world, write_offset=-0.5) == term_dialog.OTHER


def test_classify_hook_after_status_at_is_still_cold_idle(world):
    assert _classify(world, hook_offset=5.0) == term_dialog.COLD_IDLE


def test_classify_hook_just_before_status_at_is_other(world):
    assert _classify(world, hook_offset=-0.5) == term_dialog.OTHER


def test_write_after_idle_dialog_opened_still_gets_cold_card(world, mk_update):
    """aipager typed after the timer opened the question (memory still
    closed): the question is still the idle cold one."""
    world.transcript(last_exchange_ago=600)
    world.waiting(ago=20.0)
    world.run_async(inject.send_text_and_enter(NAME, "typed meanwhile"))
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert len(_cards(world, COLD_TEXT)) == 1


def test_write_before_dialog_opened_gets_note_not_cold_card(world, mk_update):
    """Control: aipager typed 5 s before the question opened."""
    world.transcript(last_exchange_ago=600)
    world.run_async(inject.send_text_and_enter(NAME, "typed before"))
    world.clock.advance(10.0)
    world.waiting(ago=5.0)
    world.scan()
    world.send(mk_update, "hello", msg_id=10)
    assert _cards(world, COLD_TEXT) == []


# -- an in-place mode switch looks at the record before Shift+Tab -------------------------

def _perms(world, mk_update, target="auto"):
    world.sess.perm_mode = "ask"
    world.bot._is_admin = MagicMock(return_value=False)
    update = mk_update(f"/perms {target}", chat_id=CHAT_ID, user_id=USER)
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    world.run_async(world.bot._handle_perms_cmd(update, MagicMock()))
    return update


def test_mode_switch_no_shift_tab_into_unseen_dialog(world, mk_update):
    world.waiting(ago=0.5)
    _perms(world, mk_update)
    assert SHIFT_TAB not in world.writes


def test_mode_switch_no_shift_tab_into_seen_dialog(world, mk_update):
    world.waiting(ago=0.5)
    world.scan()
    _perms(world, mk_update)
    assert SHIFT_TAB not in world.writes


def test_mode_switch_into_unseen_dialog_keeps_mode(world, mk_update):
    world.waiting(ago=0.5)
    _perms(world, mk_update)
    assert world.sess.perm_mode == "ask"


def test_mode_switch_with_idle_record_presses_shift_tab(world, mk_update):
    """Control: no dialog, the switch goes ahead."""
    world.idle(ago=30)
    _perms(world, mk_update)
    assert SHIFT_TAB in world.writes


# -- a session whose claude pid is unknown is looked up before typing ---------------------

def test_pid_unknown_waiting_record_holds_message(world, mk_update):
    world.sess.claude_pid = 0
    world.waiting(ago=0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert len(world.sess.pending_queue) == 1


def test_pid_unknown_idle_record_is_typed(world, mk_update):
    world.sess.claude_pid = 0
    world.idle(ago=30)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_pid_unknown_other_sessions_waiting_record_is_typed(world, mk_update):
    world.sess.claude_pid = 0
    world.record(status="waiting", waitingFor="dialog open",
                 statusUpdatedAt=int((world.clock.wall - 0.5) * 1000),
                 sessionId="22222222-2222-4222-8222-222222222222")
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_pid_unknown_no_record_is_typed(world, mk_update):
    world.sess.claude_pid = 0
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


# -- the resume card is sent when it falls due, not at the next tick ----------------------

def _scan_then_wait(world, *, close_first=False, wait=1.3):
    async def live():
        return [NAME]
    world.mp.setattr("aipager.dtach.inject.list_sessions", live)

    async def go():
        await world.monitor._scan()
        if close_first:
            world.idle()
            await world.monitor._scan()
        world.clock.advance(1.0)
        await asyncio.sleep(wait)

    world.run_async(go())


def test_resume_card_sent_when_due_without_a_tick(world):
    world.after_resume(ago=0.2)
    _scan_then_wait(world)
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_resume_card_due_then_tick_sends_once(world):
    world.after_resume(ago=0.2)
    _scan_then_wait(world)
    world.clock.advance(1.0)
    world.scan()
    assert len(_cards(world, UNSURE_TEXT)) == 1


def test_resume_card_not_sent_when_closed_before_due(world):
    world.after_resume(ago=0.2)
    _scan_then_wait(world, close_first=True)
    assert _cards(world, UNSURE_TEXT) == []
