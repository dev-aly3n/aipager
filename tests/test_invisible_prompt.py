"""A Telegram message holding invisible characters reaches Claude (roadmap
8.124): ``_inject_prompt`` types and notes the text Claude Code will send,
and presses Enter once more when Claude Code held a message back."""

from __future__ import annotations

import pytest

from aipager import policy_snapshot as ps
from aipager.bot import session_ops
from aipager.dtach import inject
from aipager.state import Status, TrackedSession

NAME = "claude-jim"
CHAT = -1001
ZWNJ = "\u200c"


@pytest.fixture
def typed(monkeypatch, tmp_path):
    """Records what is typed and pressed; notes go under tmp_path; the
    second-Enter watch is captured, not started."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    monkeypatch.setattr(session_ops, "_HELD_BACK", {})   # never the module's own
    out = {"text": [], "keys": [], "watches": []}

    async def _text(name, text):
        out["text"].append(text)
        return True

    async def _keys(name, key):
        out["keys"].append(key)
        return True

    def _spawn(coro):
        out["watches"].append(coro)
        coro.close()
    monkeypatch.setattr(inject, "send_text_and_enter", _text)
    monkeypatch.setattr(inject, "send_keys", _keys)
    monkeypatch.setattr(inject, "press_enter_gated", lambda name: _keys(name, "Enter"))
    monkeypatch.setattr(session_ops, "_spawn", _spawn)
    return out


def _bot(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    bot.registry._sessions[NAME] = sess
    return bot, sess


def test_the_typed_text_and_the_note_are_what_claude_code_sends(mk_bot, run_async, typed,
                                                               tmp_path):
    bot, sess = _bot(mk_bot)
    assert run_async(bot._inject_prompt(sess, "reply\u200b\ufeff with\r\nOK",
                                        msg_id=5, chat_id=CHAT)) is True
    assert typed["text"] and typed["text"][0].endswith("reply with\nOK")
    assert "\u200b" not in typed["text"][0] and "\ufeff" not in typed["text"][0]
    notes = ps.list_outstanding_notes(NAME)
    assert [n["body"] for n in notes] == typed["text"]
    assert typed["watches"] == []                      # nothing left to hold it back


def test_a_persian_half_space_is_typed_and_watched(mk_bot, run_async, typed):
    bot, sess = _bot(mk_bot)
    text = f"می{ZWNJ}خواهم این را"
    assert run_async(bot._inject_prompt(sess, text, msg_id=6, chat_id=CHAT)) is True
    assert typed["text"][0].endswith(text)
    assert len(typed["watches"]) == 1


def test_a_slash_command_is_never_watched(mk_bot, run_async, typed):
    bot, sess = _bot(mk_bot)
    run_async(bot._inject_prompt(sess, f"/review می{ZWNJ}خواهم", msg_id=7, chat_id=CHAT))
    assert typed["watches"] == []


def test_a_failed_send_is_never_watched(mk_bot, run_async, typed, monkeypatch):
    bot, sess = _bot(mk_bot)

    async def _fail(name, text):
        return False
    monkeypatch.setattr(inject, "send_text_and_enter", _fail)
    run_async(bot._inject_prompt(sess, f"می{ZWNJ}خواهم", msg_id=8, chat_id=CHAT))
    assert typed["watches"] == []


# ---- the watch ----------------------------------------------------------------

@pytest.fixture
def no_wait(monkeypatch):
    waits = []

    async def _sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(session_ops, "_sleep", _sleep)
    monkeypatch.setattr(session_ops, "_HELD_BACK", {})
    return waits


def _held(tmp_path, name="note.json"):
    note = tmp_path / name
    note.write_text("{}")                          # not picked up
    session_ops._HELD_BACK[NAME] = note
    return note


def test_enter_is_pressed_once_more_when_the_message_was_held_back(
        mk_bot, run_async, typed, no_wait, tmp_path):
    bot, sess = _bot(mk_bot)
    note = _held(tmp_path)
    run_async(bot._enter_again_if_held_back(sess, note))
    assert typed["keys"] == ["Enter"]
    assert no_wait == [session_ops.HELD_BACK_ENTER_AFTER_SECONDS]
    assert session_ops.HELD_BACK_ENTER_AFTER_SECONDS >= 1.0
    assert NAME not in session_ops._HELD_BACK


def test_nothing_is_pressed_when_the_message_was_picked_up(mk_bot, run_async, typed,
                                                           no_wait, tmp_path):
    bot, sess = _bot(mk_bot)
    note = _held(tmp_path)
    note.unlink()                                  # its UserPromptSubmit consumed it
    run_async(bot._enter_again_if_held_back(sess, note))
    assert typed["keys"] == []


def test_nothing_is_pressed_when_a_later_message_already_did(mk_bot, run_async, typed,
                                                             no_wait, tmp_path):
    bot, sess = _bot(mk_bot)
    note = _held(tmp_path)
    session_ops._HELD_BACK[NAME] = tmp_path / "a-later-message.json"
    run_async(bot._enter_again_if_held_back(sess, note))
    assert typed["keys"] == []


@pytest.mark.parametrize("how", ["parked", "gone", "replaced", "dialog", "carried-dialog",
                                 "releasing-held", "turn-state-held", "pending-permission"])
def test_nothing_is_pressed_into_a_session_that_cannot_take_it(
        mk_bot, run_async, typed, no_wait, tmp_path, how):
    """Above all never into an open dialog: the Enter would answer it
    (row 1, "Yes") with nobody's consent."""
    bot, sess = _bot(mk_bot)
    note = _held(tmp_path)
    if how == "parked":
        sess.parked_job = "bg-1"
    elif how == "gone":
        sess.status = Status.GONE
    elif how == "replaced":
        bot.registry._sessions[NAME] = TrackedSession(name=NAME, label="jim")
    elif how == "dialog":
        sess.status = Status.INTERACTIVE
    elif how == "carried-dialog":
        sess.status = Status.BUSY
        sess.prompt_wait_carry = True
    elif how == "releasing-held":
        sess.status = Status.BUSY
        sess.dialog_hold = {"closed": "answered"}
    elif how == "turn-state-held":
        # The new turn's events (its PermissionRequest among them) wait
        # behind the old turn's card: BUSY, yet a dialog may be drawn.
        sess.status = Status.BUSY
        sess.turn_seq, sess.card_turn_seq = 2, 1
        sess.turn_state_hold, sess.turn_state_hold_for = {"events": []}, 2
    else:
        sess.status = Status.BUSY
        sess.pending_permission = {"tool_info": {"name": "Bash"}}
    run_async(bot._enter_again_if_held_back(sess, note))
    assert typed["keys"] == []
    dropped = how in ("parked", "gone", "replaced")
    assert len(no_wait) == (1 if dropped else session_ops.HELD_BACK_TRIES)
    # Behind a dialog it is kept for the next message's flush.
    assert (NAME in session_ops._HELD_BACK) is not dropped
    assert session_ops.HELD_BACK_TRIES * session_ops.HELD_BACK_ENTER_AFTER_SECONDS >= 300


def test_enter_waits_for_a_dialog_to_close_then_is_pressed(mk_bot, run_async, typed,
                                                          tmp_path, monkeypatch):
    bot, sess = _bot(mk_bot)
    monkeypatch.setattr(session_ops, "_HELD_BACK", {})
    note = _held(tmp_path)
    sess.status = Status.INTERACTIVE
    checks = []

    async def _sleep(seconds):
        checks.append(seconds)
        if len(checks) == 2:
            sess.status = Status.BUSY            # answered meanwhile
    monkeypatch.setattr(session_ops, "_sleep", _sleep)
    run_async(bot._enter_again_if_held_back(sess, note))
    assert typed["keys"] == ["Enter"] and len(checks) == 2


def test_the_next_message_presses_a_held_back_enter_while_held_messages_go_out(
        mk_bot, run_async, typed, no_wait, tmp_path):
    """After a dialog is answered, the messages it held are typed: the one
    held back before them goes first (its own watch would wait)."""
    bot, sess = _bot(mk_bot)
    _held(tmp_path)
    sess.status = Status.BUSY
    sess.dialog_hold = {"closed": "answered"}
    run_async(bot._enter_held_back_now(sess))
    assert typed["keys"] == ["Enter"]
    assert NAME not in session_ops._HELD_BACK


def test_the_next_message_leaves_a_held_back_enter_to_its_watch_while_a_dialog_is_up(
        mk_bot, run_async, typed, no_wait, tmp_path):
    bot, sess = _bot(mk_bot)
    note = _held(tmp_path)
    sess.status = Status.INTERACTIVE
    run_async(bot._enter_held_back_now(sess))
    assert typed["keys"] == [] and session_ops._HELD_BACK[NAME] == note


def test_stop_wipes_a_held_back_message_and_never_sends_it(mk_bot, run_async, monkeypatch,
                                                         tmp_path, no_wait):
    from unittest.mock import AsyncMock, MagicMock
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    body = "سلام\u200fدنیا"
    note = ps.write_note(NAME, None, None, None, msg_id=9, chat_id=1, sender_key=(1, 1),
                         body=body, raw_text=body)
    session_ops._HELD_BACK[NAME] = note
    keys, wiped = [], []

    async def _keys(name, k):
        keys.append(k)
        return True

    async def _discard(name, chars=0):
        keys.append("discard")
        wiped.append(chars)
        return True
    monkeypatch.setattr(inject, "send_keys", _keys)
    monkeypatch.setattr(inject, "press_enter_gated", lambda name: _keys(name, "Enter"))
    monkeypatch.setattr(inject, "discard_queued_input", _discard)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(bot._stop_session_core(sess))
    assert keys == ["Escape", "Escape", "discard"]
    assert wiped and wiped[0] >= len(body)
    assert NAME not in session_ops._HELD_BACK


def test_a_kill_drops_the_pending_enter(mk_bot, run_async, monkeypatch, tmp_path, no_wait):
    from unittest.mock import AsyncMock
    bot, sess = _bot(mk_bot)
    _held(tmp_path)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    bot._update_bot_commands = AsyncMock()
    assert run_async(bot._kill_session_core(NAME, "jim")).result == "killed"
    assert NAME not in session_ops._HELD_BACK


def test_clearqueue_with_nothing_queued_leaves_a_held_back_message_alone(
        mk_bot, run_async, monkeypatch, tmp_path, no_wait):
    """Its wipe types Escape, which into an empty queue interrupts the
    turn: with nothing else queued it refuses with no side effects, and the
    held-back message keeps its second Enter."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    note = _held(tmp_path)
    assert run_async(bot._clear_queue_core(sess)).ok is False
    assert session_ops._HELD_BACK[NAME] == note


def test_clearqueue_wipes_a_held_back_message_with_the_queue(
        mk_bot, run_async, monkeypatch, tmp_path, no_wait):
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    body = "سلام\u200fدنیا"
    note = ps.write_note(NAME, None, None, None, msg_id=9, chat_id=1, sender_key=(1, 1),
                         body=body, raw_text=body)
    session_ops._HELD_BACK[NAME] = note
    queued = {"body": "queued text"}
    monkeypatch.setattr(session_ops, "held_by_claude",
                        lambda sess, candidates: [queued])
    wiped = []

    async def _discard(name, chars=0):
        wiped.append(chars)
        return True
    monkeypatch.setattr(inject, "discard_queued_input", _discard)
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok is True and outcome.dropped == 2
    assert wiped and NAME not in session_ops._HELD_BACK


def test_a_typed_refusal_takes_a_held_back_message_with_the_turn(
        mk_bot, run_async, monkeypatch, tmp_path, no_wait):
    """A typed Deny ends the turn as /stop does (roadmap 8.99/8.126): a
    message still held back in the box goes with it, never sent after."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    body = "سلام\u200fدنیا"
    note = ps.write_note(NAME, None, None, None, msg_id=9, chat_id=1, sender_key=(1, 1),
                         body=body, raw_text=body)
    session_ops._HELD_BACK[NAME] = note
    held = run_async(bot._claude_queue_before_typed_refusal(sess))
    assert [n["body"] for n in held] == [body]
    assert NAME not in session_ops._HELD_BACK


def test_a_held_back_message_goes_before_the_next_one_is_typed(
        mk_bot, run_async, no_wait, tmp_path, monkeypatch):
    """Typed onto a message still sitting in the box, the next one would be
    glued to it: the earlier one's Enter comes first."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    events = []

    async def _text(name, text):
        events.append(("text", text[-8:]))
        return True

    async def _keys(name, key):
        events.append(("key", key))
        return True
    monkeypatch.setattr(inject, "send_text_and_enter", _text)
    monkeypatch.setattr(inject, "send_keys", _keys)
    monkeypatch.setattr(inject, "press_enter_gated", lambda name: _keys(name, "Enter"))
    monkeypatch.setattr(session_ops, "_spawn", lambda coro: coro.close())
    bot, sess = _bot(mk_bot)
    run_async(bot._inject_prompt(sess, f"می{ZWNJ}خواهم", msg_id=11, chat_id=CHAT))
    assert NAME in session_ops._HELD_BACK              # waiting for its second Enter
    run_async(bot._inject_prompt(sess, "and another", msg_id=12, chat_id=CHAT))
    assert [e[0] for e in events] == ["text", "key", "text"]
    assert events[1] == ("key", "Enter") and events[2] == ("text", " another")
    assert NAME not in session_ops._HELD_BACK


# ---- what is typed ---------------------------------------------------------------

def test_a_message_that_is_blank_once_cleaned_is_not_sent(mk_bot, run_async, typed):
    bot, sess = _bot(mk_bot)
    assert run_async(bot._inject_prompt(sess, "\u3164\u115f \u200b", msg_id=9,
                                        chat_id=CHAT)) is False
    assert typed["text"] == [] and ps.list_outstanding_notes(NAME) == []


def test_the_admin_check_sees_the_cleaned_text(mk_bot, run_async, typed, monkeypatch):
    """A zero-width space in front of a slash command must not hide it from
    the role check: it is removed before the check, then refused."""
    bot, sess = _bot(mk_bot)
    seen = []

    def _needs_admin(text, user_id, *, chat_id=None):
        seen.append(text)
        return True
    monkeypatch.setattr(bot, "_command_needs_admin", _needs_admin)
    result = run_async(bot._inject_prompt(sess, "\u200b/anything", msg_id=10,
                                          chat_id=CHAT, driver_user_id=42))
    assert seen == ["/anything"]
    assert result is session_ops.PROMPT_REFUSED and typed["text"] == []


def test_the_marker_is_cleaned_too(mk_bot, run_async, typed, monkeypatch):
    """A member's label comes from their Telegram name: an invisible
    character there would hold back every message they send."""
    bot, sess = _bot(mk_bot)
    monkeypatch.setattr(bot, "_prompt_marker",
                        lambda sess, sender: "[via Telegram · @al\u2067ice\u3164]")
    run_async(bot._inject_prompt(sess, "hello", msg_id=13, chat_id=CHAT))
    assert typed["text"] == ["[via Telegram · @alice]\nhello"]
    assert [n["body"] for n in ps.list_outstanding_notes(NAME)] == typed["text"]


# ---- roadmap 8.143: the second Enter never goes into a dialog --------------------

def test_a_second_enter_a_dialog_came_before_is_kept_for_later(mk_bot, run_async, no_wait,
                                                               monkeypatch, tmp_path):
    """The gated Enter found a dialog (nothing pressed): the message stays
    held back, for its own watch to press once the dialog is gone."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    note = ps.write_note(NAME, None, None, None, msg_id=9, chat_id=1, sender_key=(1, 1),
                         body="x", raw_text="x")
    session_ops._HELD_BACK[NAME] = note

    async def gated(_name):
        return inject.DIALOG_BEFORE_TYPING
    monkeypatch.setattr(inject, "press_enter_gated", gated)
    run_async(bot._enter_held_back_now(sess))
    assert session_ops._HELD_BACK.get(NAME) is note


def test_the_watch_tries_again_after_a_dialog_came_first(mk_bot, run_async, no_wait,
                                                         monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    bot, sess = _bot(mk_bot)
    sess.status = Status.BUSY
    note = ps.write_note(NAME, None, None, None, msg_id=9, chat_id=1, sender_key=(1, 1),
                         body="x", raw_text="x")
    session_ops._HELD_BACK[NAME] = note
    results = [inject.DIALOG_BEFORE_TYPING, True]
    presses = []

    async def gated(name):
        presses.append(name)
        return results.pop(0)
    monkeypatch.setattr(inject, "press_enter_gated", gated)
    run_async(bot._enter_again_if_held_back(sess, note))
    assert presses == [NAME, NAME]
    assert NAME not in session_ops._HELD_BACK
