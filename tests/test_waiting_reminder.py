"""A session waiting on a human is never announced as idle (roadmap 8.4).

Claude Code's ``Notification`` hook with ``notification_type: idle_prompt``
("Claude is waiting for your input") reaches the receiver as an idle-type
event. For a session that is INTERACTIVE — blocked on a permission or an
AskUserQuestion — the old idle branch flipped it to IDLE and the chat
said "idle" while the prompt above still waited for an answer. Now that
datagram keeps the state and posts one reminder per wait pointing at the
prompt; a real Stop while INTERACTIVE still ends the turn as before, and
an idle notification while BUSY still means what it always did.

Datagram replays through ``HookReceiver._on_datagram``; no real Telegram,
claude or dtach.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.miniapp.sessions import _derive_status
from aipager.state import SessionRegistry, Status, TrackedSession

NAME = "claude-jim"
PERM = {"tool_summary": "Bash: rm -rf /tmp/x", "tool_info": {}, "wait_started_at": 0.0}
QUESTION = {"ask_question": True, "question": "Tea or coffee?", "options": ["tea", "coffee"],
            "tool_info": {}, "wait_started_at": 0.0}
ARROW = "still waiting for your answer above"


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


def _send(recv, run_async, **fields):
    fields.setdefault("session", NAME)
    fields.setdefault("transcript_path", "")
    run_async(recv._on_datagram(json.dumps(fields).encode()))


_SEQ = [0]


def _idle_notification(recv, run_async):
    """One nudge. Carries a sequence number because the receiver drops a
    byte-identical datagram inside its dedup window — real nudges differ
    in their payload, so the test's repeats must too."""
    _SEQ[0] += 1
    _send(recv, run_async, hook_event_name="Notification", notification_type="idle_prompt",
          message="Claude is waiting for your input", seq=_SEQ[0])


def _waiting(registry, pending):
    registry.transition(NAME, Status.BUSY)
    sess = registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = pending
    sess.busy_msg_id = 4242
    return sess


def _events(notify_fn):
    return [c.args[1] for c in notify_fn.await_args_list]


# ===== R1: the shared derivation =========================================

def test_waiting_on_human_derives_kind_and_summary():
    sess = TrackedSession(name=NAME, label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = dict(PERM)
    assert sess.waiting_on_human() == ("permission", "Bash: rm -rf /tmp/x")
    sess.pending_permission = dict(QUESTION)
    assert sess.waiting_on_human() == ("question", "Tea or coffee?")
    sess.pending_permission = None
    assert sess.waiting_on_human() == (None, None)


def test_miniapp_status_delegates_to_the_same_derivation(monkeypatch):
    sess = TrackedSession(name=NAME, label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = dict(QUESTION)
    assert _derive_status(sess) == ("waiting", "question", "Tea or coffee?")
    monkeypatch.setattr(TrackedSession, "waiting_on_human", lambda self: ("permission", "stub"))
    assert _derive_status(sess) == ("waiting", "permission", "stub")


# ===== R2: the guard in the receiver ====================================

def test_idle_notification_while_waiting_on_permission_reminds_instead_of_idling(
    receiver, run_async, caplog,
):
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, dict(PERM))
    notify_fn.reset_mock()

    with caplog.at_level(logging.INFO, logger="aipager.dtach.hook_receiver"):
        _idle_notification(recv, run_async)

    assert sess.status == Status.INTERACTIVE
    assert sess.pending_permission == PERM
    assert _events(notify_fn) == ["waiting_reminder"]
    _, _event, ctx = notify_fn.await_args.args
    assert ctx == {"kind": "permission", "summary": "Bash: rm -rf /tmp/x"}
    assert any("reminding, not idling" in r.getMessage() for r in caplog.records
               if r.levelno == logging.INFO)


def test_idle_notification_while_waiting_on_a_question_reminds_with_the_question(
    receiver, run_async,
):
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, dict(QUESTION))
    notify_fn.reset_mock()

    _idle_notification(recv, run_async)

    assert sess.status == Status.INTERACTIVE
    assert notify_fn.await_args.args[1:] == ("waiting_reminder",
                                             {"kind": "question", "summary": "Tea or coffee?"})


def test_idle_notification_while_waiting_without_inline_prompt_still_reminds(
    receiver, run_async,
):
    """The separate-message fallback leaves pending_permission None while
    the status is still INTERACTIVE — status alone decides."""
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, None)
    notify_fn.reset_mock()

    _idle_notification(recv, run_async)

    assert sess.status == Status.INTERACTIVE
    assert notify_fn.await_args.args[1:] == ("waiting_reminder", {"kind": None, "summary": None})


def test_one_reminder_per_wait_then_again_on_the_next_wait(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, dict(PERM))
    notify_fn.reset_mock()

    _idle_notification(recv, run_async)
    _idle_notification(recv, run_async)
    assert _events(notify_fn) == ["waiting_reminder"]
    assert sess.status == Status.INTERACTIVE

    registry.transition(NAME, Status.BUSY)
    registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = dict(PERM)
    _idle_notification(recv, run_async)
    assert _events(notify_fn) == ["waiting_reminder", "waiting_reminder"]


def test_a_real_stop_while_waiting_still_ends_the_turn(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, dict(PERM))
    notify_fn.reset_mock()

    _send(recv, run_async, hook_event_name="Stop", last_assistant_message="Ok, skipping it.")

    assert sess.status == Status.IDLE
    assert "idle_prompt" in _events(notify_fn)
    assert "waiting_reminder" not in _events(notify_fn)


def test_idle_notification_while_busy_still_means_idle(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.transition(NAME, Status.BUSY)
    notify_fn.reset_mock()

    _idle_notification(recv, run_async)

    assert sess.status == Status.IDLE
    assert "idle_prompt" in _events(notify_fn)
    assert "waiting_reminder" not in _events(notify_fn)


# ===== R3: the flag ======================================================

def test_reminder_flag_is_reset_on_entering_interactive_and_never_persisted(tmp_state_file):
    registry = SessionRegistry()
    sess = registry.transition(NAME, Status.BUSY)
    sess.waiting_reminder_sent = True
    registry.transition(NAME, Status.INTERACTIVE)
    assert sess.waiting_reminder_sent is False

    sess.waiting_reminder_sent = True
    registry.save()
    loaded = SessionRegistry()
    loaded.load()
    assert loaded.get(NAME).waiting_reminder_sent is False


# ===== R4: the notify handler ============================================

def _reminder_sends(bot):
    return [c for c in bot._app.bot.send_message.await_args_list if ARROW in str(c.args[1:2])]


def _bot_with(mk_bot, pending, *, busy_msg_id=4242, label="jim"):
    registry = SessionRegistry()
    sess = TrackedSession(name=NAME, label=label, status=Status.INTERACTIVE)
    sess.pending_permission = pending
    sess.busy_msg_id = busy_msg_id
    registry._sessions[NAME] = sess
    return mk_bot(registry), sess


def test_reminder_replies_to_the_prompt_card_and_names_the_permission(mk_bot, run_async):
    bot, sess = _bot_with(mk_bot, dict(PERM), label="a<b")

    run_async(bot.notify(sess, "waiting_reminder",
                         {"kind": "permission", "summary": "Bash: rm -rf /tmp/x"}))

    sends = _reminder_sends(bot)
    assert len(sends) == 1
    text, kwargs = sends[0].args[1], sends[0].kwargs
    assert text.startswith("⬆️ <b>a&lt;b</b> · still waiting for your answer above")
    assert "Permission: Bash: rm -rf /tmp/x" in text
    assert kwargs.get("reply_to_message_id") == 4242
    assert kwargs.get("parse_mode") == "HTML"
    assert kwargs.get("reply_markup") is None
    assert sess.status == Status.INTERACTIVE and sess.busy_msg_id == 4242


def test_reminder_without_a_card_is_standalone_and_names_the_question(mk_bot, run_async):
    bot, sess = _bot_with(mk_bot, dict(QUESTION), busy_msg_id=None)

    run_async(bot.notify(sess, "waiting_reminder", {"kind": "question", "summary": "Tea or coffee?"}))

    sends = _reminder_sends(bot)
    assert len(sends) == 1
    assert "Question: Tea or coffee?" in sends[0].args[1]
    assert sends[0].kwargs.get("reply_to_message_id") is None
    assert sess.status == Status.INTERACTIVE


def test_reminder_caps_a_long_summary_and_escapes_it(mk_bot, run_async):
    bot, sess = _bot_with(mk_bot, dict(PERM))
    long = "<x>" * 300

    run_async(bot.notify(sess, "waiting_reminder", {"kind": "permission", "summary": long}))

    text = _reminder_sends(bot)[0].args[1]
    assert "<x>" not in text and "&lt;x&gt;" in text
    assert len(text) < 800


def test_reminder_without_a_summary_is_the_arrow_line_only(mk_bot, run_async):
    bot, sess = _bot_with(mk_bot, None)

    run_async(bot.notify(sess, "waiting_reminder", {"kind": None, "summary": None}))

    text = _reminder_sends(bot)[0].args[1]
    assert "\n" not in text
    assert "Permission" not in text and "Question" not in text


def test_a_stop_failure_while_waiting_still_ends_the_turn(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = _waiting(registry, dict(PERM))
    notify_fn.reset_mock()

    _send(recv, run_async, hook_event_name="StopFailure", last_assistant_message="")

    assert sess.status == Status.IDLE
    assert "waiting_reminder" not in _events(notify_fn)


def test_repeated_nudge_is_logged_at_debug_not_sent(receiver, run_async, caplog):
    registry, recv, notify_fn = receiver
    _waiting(registry, dict(PERM))
    notify_fn.reset_mock()
    _idle_notification(recv, run_async)

    with caplog.at_level(logging.DEBUG, logger="aipager.dtach.hook_receiver"):
        _idle_notification(recv, run_async)

    assert _events(notify_fn) == ["waiting_reminder"]
    assert any("already reminded" in r.getMessage() for r in caplog.records
               if r.levelno == logging.DEBUG)
