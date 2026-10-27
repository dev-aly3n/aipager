"""Roadmap 8.142: three follow-ups to the typed answers of 8.126.

(a) An answer given through a waiting PermissionRequest hook types nothing,
so a refusal typed for the next prompt waits from that answer too, never
landing on the dialog Claude Code is still closing.
(b) A refusal typed for the last open prompt is watched like a lone
prompt's: if its Escape ended the turn, the session does not stay Working.
(c) On Claude Code's Auto-mode question before the first read outside the
working directories, Enter takes "Yes, and keep allowing any reads outside
the working directories": a typed Allow there types nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import i18n
from aipager.dtach import hook_reply
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status, TrackedSession

NAME = "claude-jim"
EN = i18n.get("en")


# ── (a) the answer through the hook is stamped ───────────────────────────

@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(prefix="ta-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def listener(short_dir):
    path = os.path.join(short_dir, "r.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    yield path
    sock.close()


def test_an_answer_delivered_to_a_sessions_hook_is_stamped(listener):
    before = time.monotonic()
    assert hook_reply.send_decision({"addr": listener, "request_id": "r1", "session": NAME},
                                    hook_reply.allow_decision())
    assert hook_reply.last_answer_at(NAME) >= before


def test_an_answer_that_did_not_go_out_is_not_stamped(short_dir):
    dead = {"addr": os.path.join(short_dir, "gone.sock"), "request_id": "r1",
            "session": NAME}
    assert not hook_reply.send_decision(dead, hook_reply.allow_decision())
    assert hook_reply.last_answer_at(NAME) == 0.0


def test_a_target_with_no_session_stamps_nothing(listener):
    assert hook_reply.send_decision({"addr": listener, "request_id": "r1"},
                                    hook_reply.allow_decision())
    assert hook_reply._ANSWERED_AT == {}


def test_the_reply_target_names_its_session(run_async):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition(NAME, Status.BUSY)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PermissionRequest", "session": NAME,
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "aipager_reply_addr": "/x/r.sock", "aipager_request_id": "abc"}).encode()))
    contexts = [c.args[2] for c in notify_fn.await_args_list
                if isinstance(c.args[2], dict) and c.args[2].get("hook_reply")]
    assert contexts and contexts[0]["hook_reply"] == {
        "addr": "/x/r.sock", "request_id": "abc", "session": NAME}


def _due(sess, tools=("Write",)):
    sess.extra_prompts = [
        {"text": "", "perm": {"tool_info": {"name": tool}}, "auto_deny_due": True}
        for tool in tools]


def _keys(events):
    from aipager.dtach import inject

    async def _send(name, key):
        inject._note_write(name)
        events.append(key)
        return True
    return _send


def _world(mk_bot, monkeypatch):
    from aipager.bot import auth
    bot = mk_bot()
    sess = TrackedSession(name=NAME, label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions[NAME] = sess
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")
    events = []
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _keys(events))

    async def _pause(seconds):
        events.append(("pause", round(seconds, 1)))
        for stamps in (hook_reply._ANSWERED_AT,):
            if NAME in stamps:
                stamps[NAME] -= seconds                  # the time passes
    monkeypatch.setattr(auth, "_sleep", _pause)
    bot._watch_keystroke_answer = MagicMock()
    bot._claude_queue_before_typed_refusal = AsyncMock(return_value=[])
    return bot, sess, events


def test_a_due_refusal_waits_after_an_answer_through_the_hook(mk_bot, run_async,
                                                              monkeypatch):
    from aipager.bot import auth
    bot, sess, events = _world(mk_bot, monkeypatch)
    hook_reply._ANSWERED_AT[NAME] = time.monotonic()       # the prompt above, just now
    _due(sess)
    run_async(bot._run_due_auto_denies(sess))
    assert events == [("pause", auth.AUTO_DENY_SETTLE_SECONDS), "Escape"]


def test_a_due_refusal_long_after_an_answer_does_not_wait(mk_bot, run_async, monkeypatch):
    bot, sess, events = _world(mk_bot, monkeypatch)
    hook_reply._ANSWERED_AT[NAME] = time.monotonic() - 60
    _due(sess)
    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape"]


# ── (b) the last refusal is watched ──────────────────────────────────────

def test_only_the_refusal_that_closes_the_last_prompt_is_watched(mk_bot, run_async,
                                                                 monkeypatch):
    from aipager.dtach import inject
    bot, sess, events = _world(mk_bot, monkeypatch)
    held = [{"msg_id": 7, "chat_id": -100, "text": "queued"}]
    bot._claude_queue_before_typed_refusal = AsyncMock(return_value=held)
    _due(sess, tools=("Write", "Edit"))

    async def no_pause(_s):
        inject._LAST_WRITE_AT[NAME] -= 10
    from aipager.bot import auth
    monkeypatch.setattr(auth, "_sleep", no_pause)
    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape", "Escape"]
    assert sess.open_prompts() == [] and sess.status is Status.BUSY
    bot._claude_queue_before_typed_refusal.assert_awaited_once_with(sess)
    bot._watch_keystroke_answer.assert_called_once()
    kw = bot._watch_keystroke_answer.call_args.kwargs
    assert kw["refusal"] is True and kw["held"] == held and kw["answered_at"] > 0


def test_a_last_question_refused_by_keys_is_watched_but_not_as_a_refusal(mk_bot, run_async,
                                                                         monkeypatch):
    bot, sess, events = _world(mk_bot, monkeypatch)
    _due(sess, tools=("AskUserQuestion",))
    run_async(bot._run_due_auto_denies(sess))
    bot._claude_queue_before_typed_refusal.assert_not_awaited()
    assert bot._watch_keystroke_answer.call_args.kwargs["refusal"] is False


def test_a_refusal_that_was_not_typed_is_not_watched(mk_bot, run_async, monkeypatch):
    bot, sess, events = _world(mk_bot, monkeypatch)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=False))
    _due(sess)
    run_async(bot._run_due_auto_denies(sess))
    bot._watch_keystroke_answer.assert_not_called()


# ── (c) the outside-reads question ───────────────────────────────────────

def _permission_tool_info(run_async, tool, tool_input, mode, cwd="/home/alice/proj"):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    sess = registry.get_or_create(NAME)
    sess.cwd = cwd
    registry.transition(NAME, Status.BUSY)
    payload = {"hook_event_name": "PermissionRequest", "session": NAME,
               "tool_name": tool, "tool_input": tool_input}
    if mode is not None:
        payload["permission_mode"] = mode
    run_async(recv._on_datagram(json.dumps(payload).encode()))
    infos = [c.args[2].get("tool_info") for c in notify_fn.await_args_list
             if isinstance(c.args[2], dict) and c.args[2].get("tool_info")]
    assert infos
    return infos[0]


@pytest.mark.parametrize("tool,tool_input,mode,flagged", [
    ("Read", {"file_path": "/etc/hosts"}, "auto", True),
    ("Read", {"file_path": "/etc/hosts"}, None, True),
    ("Read", {"file_path": "/etc/hosts"}, "weird", True),
    ("Grep", {"pattern": "x", "path": "/etc"}, "auto", True),
    ("Glob", {"pattern": "*"}, "auto", False),            # no path: the cwd
    ("Read", {"file_path": "/etc/hosts"}, "default", False),
    ("Read", {"file_path": "/etc/hosts"}, "bypassPermissions", False),
    ("Read", {"file_path": "/etc/hosts"}, "plan", True),
    ("Read", {"file_path": "/etc/hosts"}, "acceptEdits", True),
    ("Read", {"file_path": "/home/alice/proj/a.py"}, "auto", False),
    ("SendUserFile", {"files": ["/etc/hosts"], "status": "normal"}, "auto", True),
    ("SendUserFile", {"files": ["/home/alice/proj/a.png"], "status": "normal"}, "auto",
     False),
    ("Bash", {"command": "cat /etc/hosts"}, "auto", False),
])
def test_which_prompts_may_be_the_outside_reads_question(run_async, tool, tool_input, mode,
                                                         flagged):
    info = _permission_tool_info(run_async, tool, tool_input, mode)
    assert bool(info.get("outside_reads_question")) is flagged


@pytest.fixture
def mk_query():
    def _mk(data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock(message_id=42, text="")
        query.message.chat = MagicMock(id=-100)
        query.from_user = MagicMock(id=12345)
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock(id=-100)
        return update, query
    return _mk


def _tap(bot, mk_query, run_async, action, tool_info):
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Read: /etc/hosts", "tool_info": tool_info}
    bot.registry._sessions["claude-dev"] = sess
    update, query = mk_query(f"claude-dev:{action}")
    keys = []
    answers = []

    async def send_keys(_name, key):
        keys.append(key)
        return True

    async def is_alive(_name):
        return True
    bot._safe_answer = AsyncMock(side_effect=lambda q, text="", **kw: answers.append(text))
    with patch("aipager.dtach.inject.send_keys", side_effect=send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=is_alive):
        run_async(bot._handle_callback(update, MagicMock()))
    return keys, answers, sess


QUESTION = {"name": "Read", "always_available": False, "outside_reads_question": True}


@pytest.mark.parametrize("action", ["allow", "allow_always"])
def test_a_typed_allow_on_the_question_types_nothing_and_says_why(mk_bot, mk_query,
                                                                  run_async, action):
    from aipager.bot.callbacks import OUTSIDE_READS_TYPED_ALLOW
    keys, answers, sess = _tap(mk_bot(), mk_query, run_async, action, dict(QUESTION))
    assert keys == []
    assert answers[0] == EN.text(OUTSIDE_READS_TYPED_ALLOW)
    assert sess.pending_permission is not None          # the buttons stay
    assert sess.status is Status.INTERACTIVE


def test_a_typed_deny_on_the_question_still_refuses(mk_bot, mk_query, run_async):
    keys, _answers, _sess = _tap(mk_bot(), mk_query, run_async, "deny", dict(QUESTION))
    assert keys == ["Escape"]


def test_an_ordinary_read_prompt_is_allowed_by_enter(mk_bot, mk_query, run_async):
    info = {"name": "Read", "always_available": False}
    keys, _answers, _sess = _tap(mk_bot(), mk_query, run_async, "allow", info)
    assert keys == ["Enter"]


def test_an_answer_through_the_hook_is_unchanged_on_the_question(mk_bot, mk_query,
                                                                 run_async, listener):
    """Within the hook's window the Allow is the hook's own one-time allow,
    never a row of the question: it goes through as before."""
    sess_info = dict(QUESTION)
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Read: /etc/hosts", "tool_info": sess_info,
                               "hook_reply": {"addr": listener, "request_id": "r9",
                                              "session": "claude-dev"}}
    bot.registry._sessions["claude-dev"] = sess
    update, _query = mk_query("claude-dev:allow")
    keys = []

    async def send_keys(_name, key):
        keys.append(key)
        return True

    async def is_alive(_name):
        return True
    with patch("aipager.dtach.inject.send_keys", side_effect=send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=is_alive):
        run_async(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert hook_reply.last_answer_at("claude-dev") > 0


def test_a_prompt_answered_while_the_queue_is_read_gets_no_escape(mk_bot, run_async,
                                                                  monkeypatch):
    """The last refusal reads Claude's queue first, which can wait: the
    prompt answered meanwhile (closed) gets no Escape."""
    bot, sess, events = _world(mk_bot, monkeypatch)
    _due(sess)

    async def reading(s):
        sess.extra_prompts = []                 # answered in the terminal meanwhile
        return []
    bot._claude_queue_before_typed_refusal = reading
    run_async(bot._run_due_auto_denies(sess))
    assert events == []


def test_a_refusal_is_marked_while_the_queue_is_read(mk_bot, run_async, monkeypatch):
    bot, sess, events = _world(mk_bot, monkeypatch)
    _due(sess)
    marks = []

    async def reading(s):
        marks.append(sess.extra_prompts[0].get("auto_deny_typing"))
        return []
    bot._claude_queue_before_typed_refusal = reading
    run_async(bot._run_due_auto_denies(sess))
    assert marks == [True]
    assert events == ["Escape"]
    assert sess.open_prompts() == []


def test_a_held_back_enter_is_given_back_when_no_escape_goes_in(mk_bot, run_async,
                                                                monkeypatch, tmp_path):
    """Reading Claude's queue forgets a held-back message's second Enter
    (roadmap 8.124); when nothing is typed after all, it is given back."""
    from aipager.bot import session_ops
    bot, sess, events = _world(mk_bot, monkeypatch)
    monkeypatch.setattr(session_ops, "_HELD_BACK", {})
    note = tmp_path / "note.json"
    session_ops._HELD_BACK[NAME] = note
    _due(sess)

    async def reading(s):
        session_ops._HELD_BACK.pop(NAME, None)        # what the real read does
        sess.extra_prompts = []                      # answered meanwhile
        return []
    bot._claude_queue_before_typed_refusal = reading
    run_async(bot._run_due_auto_denies(sess))
    assert events == []
    assert session_ops._HELD_BACK.get(NAME) == note


def test_the_notification_fallback_prompt_is_flagged_too(run_async, tmp_path):
    """No PermissionRequest came (an older hook, a failed reply channel):
    the prompt raised from the Notification hook gets the same flag, and
    a read whose input it cannot see counts as possibly outside."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    sess = registry.get_or_create(NAME)
    sess.cwd = "/home/alice/proj"
    registry.transition(NAME, Status.BUSY)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "Notification", "notification_type": "permission_prompt",
        "session": NAME, "permission_mode": "auto",
        "message": "Claude needs your permission to use Grep"}).encode()))
    infos = [c.args[2].get("tool_info") for c in notify_fn.await_args_list
             if isinstance(c.args[2], dict) and c.args[2].get("tool_info")]
    # A Grep with no path searches the cwd; here its input is not known.
    assert infos and infos[0]["name"] == "Grep"
    assert infos[0].get("outside_reads_question") is True


@pytest.mark.parametrize("mode", ["default", "bypassPermissions"])
def test_the_notification_fallback_in_ask_or_full_is_not_flagged(run_async, mode):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    sess = registry.get_or_create(NAME)
    sess.cwd = "/home/alice/proj"
    registry.transition(NAME, Status.BUSY)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "Notification", "notification_type": "permission_prompt",
        "session": NAME, "permission_mode": mode,
        "message": "Claude needs your permission to use Read"}).encode()))
    infos = [c.args[2].get("tool_info") for c in notify_fn.await_args_list
             if isinstance(c.args[2], dict) and c.args[2].get("tool_info")]
    assert infos and "outside_reads_question" not in infos[0]
