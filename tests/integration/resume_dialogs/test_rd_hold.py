"""Black-box rows for roadmap 8.127 holding and release (design.md success
criteria 3, 4 and 5): nothing is typed while a terminal dialog is open, a
message that meets it is held and goes to Claude after the dialog closes
and the session settles, in order."""

from __future__ import annotations

from aipager import term_dialog
from aipager.dtach import inject
from aipager.state import Status
from tests.resume_dialog_kit import ESC, NAME


def _eyes(world) -> bool:
    return any("👀" in repr(c) for c in world.bot._react.await_args_list)


def _settle(world):
    """The dialog closed in the terminal; the session settles."""
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    world.idle()
    world.scan()


# -- criterion 3: nothing written while open ------------------------------------------

def test_message_while_open_types_nothing(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    assert world.writes == []


def test_message_while_open_gets_eyes(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    assert _eyes(world)


def test_message_while_open_is_queued(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    assert len(world.sess.pending_queue) == 1


def test_gated_enter_not_pressed_while_open(world):
    """8.124's extra Enter goes through the typing gate."""
    world.waiting()
    world.scan()
    world.run_async(inject.press_enter_gated(NAME))
    assert world.writes == []


def test_gated_text_not_typed_while_open(world):
    world.waiting()
    world.scan()
    world.run_async(inject.send_text_and_enter(NAME, "hello"))
    assert world.writes == []


def test_held_message_not_sent_while_still_open_after_ticks(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    for _ in range(3):
        world.clock.advance(5)
        world.scan()
    assert world.writes == []


def test_held_message_sent_after_close_and_settle(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    _settle(world)
    assert world.bodies() == ["hello there"]


def test_held_message_not_sent_at_the_close_tick(world, mk_update):
    """Room for a dialog that follows the first (the newer-model offer)."""
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    world.idle()
    world.scan()
    assert world.typed() == []


def test_held_message_not_sent_before_settle_seconds(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    world.idle()
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS - 0.5)
    world.idle()
    world.scan()
    assert world.typed() == []


def test_held_messages_sent_in_order(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "first one", msg_id=10)
    world.send(mk_update, "second one", msg_id=11)
    world.send(mk_update, "third one", msg_id=12)
    _settle(world)
    for _ in range(4):
        world.bot.registry.transition(NAME, Status.IDLE)
        world.clock.advance(1)
        world.scan()
    assert [b for b in world.bodies() if b.endswith(" one")][:1] == ["first one"]


def test_held_message_not_sent_into_a_second_opening(world, mk_update):
    """A newer opening right after the first closes holds the message on."""
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    world.idle()
    world.scan()
    world.clock.advance(1)
    world.waiting(ago=0.2)
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 1)
    world.scan()
    assert world.typed() == []


def test_settle_backstop_releases(world, mk_update):
    """No idle record ever shows after the close: released after the
    backstop on a live session."""
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    path = world.cfg / "sessions" / "4100.json"
    world.idle(ago=60)   # an idle record older than the close: no settle proof
    world.scan()
    world.clock.advance(term_dialog.AFTER_ANSWER_MAX_WAIT + 1)
    world.scan()
    assert world.bodies() == ["hello there"], path


def test_no_release_before_backstop_without_proof(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    world.idle(ago=60)
    world.scan()
    world.clock.advance(term_dialog.AFTER_ANSWER_MAX_WAIT - 10)
    world.scan()
    assert world.typed() == []


def test_message_after_dialog_closed_and_settled_is_typed_at_once(world, mk_update):
    world.waiting()
    world.scan()
    _settle(world)
    world.send(mk_update, "after it", msg_id=20)
    assert world.bodies() == ["after it"]


def test_no_escape_written_by_a_hold(world, mk_update):
    world.waiting()
    world.scan()
    world.send(mk_update, "hello there", msg_id=10)
    assert ESC not in world.writes


# -- criterion 4: fresh read before typing ---------------------------------------------

def test_dialog_unseen_by_tick_refuses_first_character(world, mk_update):
    world.waiting(ago=0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.writes == []


def test_dialog_unseen_by_tick_holds_message(world, mk_update):
    world.waiting(ago=0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert len(world.sess.pending_queue) == 1


def test_dialog_unseen_by_tick_is_now_known_open(world, mk_update):
    world.waiting(ago=0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sess.terminal_dialog_open() is True


def test_idle_record_message_is_typed(world, mk_update):
    world.idle(ago=30)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_no_record_at_all_message_is_typed(world, mk_update):
    """No record (an older Claude Code): main's behaviour."""
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_dialog_unseen_by_tick_while_pid_unknown_still_refused(world, mk_update):
    world.sess.claude_pid = 0
    world.waiting(ago=0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.writes == []


# -- criterion 5: within 6 s of a resume SessionStart ------------------------------------

def _resume_started(world, ago):
    world.sess.session_start_at["resume"] = world.clock.wall - ago


def _draw_after(world, polls):
    def on_sleep(n):
        if n == polls:
            world.waiting(ago=0.0)
    world.on_sleep = on_sleep


def test_message_before_dialog_draws_after_resume_is_held(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    _draw_after(world, 4)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.writes == []


def test_message_before_dialog_draws_after_resume_is_queued(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    _draw_after(world, 4)
    world.send(mk_update, "hello there", msg_id=10)
    assert len(world.sess.pending_queue) == 1


def test_message_after_resume_without_dialog_is_typed(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.bodies() == ["hello there"]


def test_resume_settle_wait_bounded_by_six_seconds(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert sum(world.sleeps) <= term_dialog.RESUME_TYPE_SETTLE_SECONDS


def test_resume_settle_polls_at_quarter_seconds(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, 0.5)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sleeps and set(world.sleeps) == {term_dialog.FRESH_POLL_SECONDS}


def test_message_seven_seconds_after_resume_is_not_delayed(world, mk_update):
    world.idle(ago=0.0)
    _resume_started(world, term_dialog.RESUME_TYPE_SETTLE_SECONDS + 1)
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sleeps == []


def test_message_after_startup_session_start_is_not_delayed(world, mk_update):
    world.idle(ago=0.0)
    world.sess.session_start_at["startup"] = world.clock.wall - 0.5
    world.send(mk_update, "hello there", msg_id=10)
    assert world.sleeps == []
