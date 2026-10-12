"""A message sent while a dialog is open waits for the answer, however long.

The incident (2026-10-12, session aipager_boss): Claude asked a question,
nobody answered, and after 5 minutes with no hook the INTERACTIVE watchdog
demoted the session to BUSY ("likely a crashed permission prompt"). The
operator's next message was then typed into the question still on screen;
its Enter picked the highlighted first option, and the message itself never
reached Claude. Ten minutes in, a "still working (no news ...)" notice had
gone out too, although Claude was only waiting for the answer.

Now the watchdog leaves a dialog open until the transcript shows it is over,
the hold lasts as long as the dialog, the first held message per person per
dialog gets one notice (a reply to the dialog's card), and the answer sends
the held messages on, in order, and deletes the notice.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from aipager import i18n
from aipager.bot.handlers import HELD_PERMISSION, HELD_QUESTION, HELD_TERMINAL
from aipager.dtach import hook_receiver as hr
from aipager.session_monitor import (
    INTERACTIVE_TIMEOUT_SECONDS,
    STALE_BUSY_TIMEOUT,
    SessionMonitor,
)
from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-boss"
CARD = 500
TUID = "toolu_q1"
QUESTION = {
    "ask_question": True, "question": "Which way?",
    "options": [{"label": "A"}, {"label": "B"}], "questions": [],
    "current_idx": 0, "multi_select": False, "cursor_pos": 0,
    "selected": set(),
    "tool_info": {"name": "AskUserQuestion", "input": {}, "tool_use_id": TUID},
}

EN = i18n.get("en")


@pytest.fixture
def wired(mk_bot, monkeypatch):
    """A bot whose session is alive, with injection captured, not done."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    notices = iter(range(900, 1000))
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda **kw: MagicMock(message_id=next(notices)))
    injected: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: injected.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._resume_animation_if_dead = MagicMock(return_value=False)
    sess = bot.registry.get_or_create(NAME)
    sess.label = "boss"
    sess.scope_chat_id = CHAT_ID  # its card is in this chat
    bot.registry.last_active_session = NAME
    return bot, sess, injected


def _question_open(bot, sess, *, permission=None):
    """The session waits on a question shown inline on its card."""
    bot.registry.transition(NAME, Status.BUSY)
    sess.busy_msg_id = CARD
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = dict(permission if permission is not None else QUESTION)


def _send(bot, mk_update, run_async, text, *, msg_id, user_id=12345,
          chat_id=CHAT_ID, path="text"):
    update = mk_update(text, message_id=msg_id, user_id=user_id, chat_id=chat_id)
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    if path == "text":
        run_async(bot._handle_message(update, ctx))
    elif path == "direct":
        run_async(bot._direct_send(update, "boss", text))
    else:
        run_async(bot._send_command(update, text))
    return update


def _notices(bot):
    return [c.kwargs for c in bot._app.bot.send_message.await_args_list]


def _bodies(injected):
    return [b.split("\n", 1)[1] if b.startswith("[via Telegram") else b
            for b in injected]


def _deleted(bot):
    return [c.kwargs.get("message_id")
            for c in bot._app.bot.delete_message.await_args_list]


# ── the incident ─────────────────────────────────────────────────────────

def test_the_incident_a_message_after_the_timeout_waits_for_the_answer(
        wired, mk_update, run_async, monkeypatch, steady_clock, tmp_path):
    """Question open, the watchdog's timeout passes with nothing in the
    transcript, a message arrives: nothing typed, the message held, one
    notice under the question's card. The answer sends it on and deletes
    the notice."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    sess.interactive_entered_at = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.last_hook_at = sess.interactive_entered_at
    sess.busy_started_at = sess.interactive_entered_at - 80

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    monitor = SessionMonitor(bot.registry, AsyncMock())
    run_async(monitor._scan())
    assert sess.status is Status.INTERACTIVE, "the watchdog ended a dialog still open"
    assert sess.dialog_is_open()

    _send(bot, mk_update, run_async, "wtf? this alarm was 2hrs", msg_id=41)

    assert injected == [], "a message was typed into the open question"
    assert [q[0] for q in sess.pending_queue] == ["wtf? this alarm was 2hrs"]
    notices = _notices(bot)
    assert len(notices) == 1
    assert notices[0]["reply_to_message_id"] == CARD
    assert notices[0]["chat_id"] == CHAT_ID
    assert notices[0]["text"] == EN.text(HELD_QUESTION)

    # Answered with a Telegram button: the callback moves it to BUSY.
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot.notify(sess, "dialog_hold_settle", {}))

    assert _bodies(injected) == ["wtf? this alarm was 2hrs"]
    assert sess.pending_queue == []
    assert _deleted(bot) == [900]
    assert sess.dialog_hold is None


def test_a_second_message_gets_no_second_notice_and_both_go_in_order(
        wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "first", msg_id=41)
    _send(bot, mk_update, run_async, "second", msg_id=42)

    assert injected == []
    assert len(_notices(bot)) == 1, "a second notice for the same dialog"
    assert bot._react.await_count == 2  # both get the 👀

    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    # A message sent after the answer, before the held ones went out, waits
    # behind them.
    _send(bot, mk_update, run_async, "third", msg_id=43)
    assert injected == []
    run_async(bot.notify(sess, "dialog_hold_settle", {}))
    assert _bodies(injected) == ["first", "second", "third"]
    assert len(_notices(bot)) == 1


def test_a_terminal_answer_releases_through_the_hook(wired, mk_update, run_async):
    """Answered in the terminal: the question's own PostToolUse ends the
    wait at once, and the held message goes out with it."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PostToolUse", "session": NAME,
        "tool_name": "AskUserQuestion", "tool_input": {},
        "tool_use_id": TUID}).encode()))
    assert sess.status is Status.BUSY
    assert _bodies(injected) == ["held"]
    assert _deleted(bot) == [900]
    # The card ticks again (the prompt had stopped its animation).
    bot._resume_animation_if_dead.assert_called_with(sess, reason="tool_done")


def test_another_tools_end_does_not_release(wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PostToolUse", "session": NAME,
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "tool_use_id": "toolu_other"}).encode()))
    assert sess.status is Status.INTERACTIVE
    assert injected == []


@pytest.mark.parametrize("path", ["text", "direct", "command"])
def test_every_prompt_path_is_held_with_one_notice(wired, mk_update, run_async, path):
    bot, sess, injected = wired
    _question_open(bot, sess)
    text = "/compact" if path == "command" else "hello"
    _send(bot, mk_update, run_async, text, msg_id=41, path=path)
    assert injected == []
    assert len(sess.pending_queue) == 1
    assert len(_notices(bot)) == 1


# ── what the notice says, and where ──────────────────────────────────────

def test_a_permission_request_shown_as_its_own_message(wired, mk_update, run_async):
    bot, sess, injected = wired
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = {
        "text": "perm", "keyboard": None, "summary": "Bash: rm -rf build",
        "prompt_token": "t", "msg_id": 600, "chat_id": CHAT_ID,
        "perm": {"tool_summary": "Bash: rm -rf build",
                 "tool_info": {"name": "Bash"},
                 "wait_started_at": sess.interactive_entered_at}}
    _send(bot, mk_update, run_async, "held", msg_id=41)
    notices = _notices(bot)
    assert len(notices) == 1
    assert notices[0]["reply_to_message_id"] == 600
    assert notices[0]["text"] == EN.text(HELD_PERMISSION)


def test_an_inline_permission_request(wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess, permission={
        "tool_summary": "Bash: ls", "tool_info": {"name": "Bash"}})
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert _notices(bot)[0]["text"] == EN.text(HELD_PERMISSION)
    assert _notices(bot)[0]["reply_to_message_id"] == CARD


def test_a_dialog_left_to_the_terminal(wired, mk_update, run_async):
    """No answerable message of this wait in the chat: the notice says to
    answer in the terminal or tap Stop, under the held message."""
    bot, sess, injected = wired
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = None
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert injected == []
    notices = _notices(bot)
    assert len(notices) == 1
    assert notices[0]["reply_to_message_id"] == 41
    assert notices[0]["text"] == EN.text(HELD_TERMINAL)


def test_a_card_that_is_gone_gets_the_notice_under_the_message(
        wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)

    def send(**kw):
        if kw.get("reply_to_message_id") == CARD:
            raise BadRequest("Message to be replied not found")
        return MagicMock(message_id=901)
    bot._app.bot.send_message = AsyncMock(side_effect=send)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    targets = [n["reply_to_message_id"] for n in _notices(bot)]
    assert targets == [CARD, 41]
    assert sess.dialog_hold["notices"] == [(CHAT_ID, 901)]


def test_the_notice_is_in_the_chats_language(wired, mk_update, run_async, monkeypatch):
    bot, sess, injected = wired
    _question_open(bot, sess)
    fa = i18n.get("fa")
    monkeypatch.setattr(i18n, "for_update", lambda update: fa)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert _notices(bot)[0]["text"] == fa.text(HELD_QUESTION)
    assert fa.text(HELD_QUESTION) != EN.text(HELD_QUESTION)


def test_in_a_group_each_person_gets_their_own_notice(wired, mk_update, run_async):
    """Two members write to the session while its question is open: each
    gets one notice (the first of theirs), nobody a second one."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "from ann", msg_id=41, user_id=111)
    _send(bot, mk_update, run_async, "again ann", msg_id=42, user_id=111)
    _send(bot, mk_update, run_async, "from bob", msg_id=43, user_id=222)
    assert injected == []
    notices = _notices(bot)
    assert len(notices) == 2
    assert all(n["reply_to_message_id"] == CARD for n in notices)
    assert sorted(sess.dialog_hold["told"]) == [111, 222]
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot.notify(sess, "dialog_hold_settle", {}))
    assert sorted(_deleted(bot)) == [900, 901]


# ── how a dialog that was not answered ends ──────────────────────────────

def test_a_turn_ended_without_an_answer_leaves_the_queue_to_the_turn_end(
        wired, mk_update, run_async):
    """Stopped, denied or interrupted: the dialog's close is not an answer.
    The held message is not pushed out mid-turn; the turn end's own drain
    (or /stop's clear) decides, as before. The notice goes."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.IDLE)
    run_async(bot.notify(sess, "dialog_hold_settle", {}))
    assert injected == []
    assert len(sess.pending_queue) == 1
    assert _deleted(bot) == [900]
    assert sess.dialog_hold is None


def test_a_session_that_ends_with_the_dialog_open_drops_held_messages(
        wired, mk_update, run_async, monkeypatch):
    """Claude Code died with the question open: nothing will ever take the
    held messages, so they are dropped with a 🤷 rather than kept for a
    turn end that never comes, and the notice goes."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    bot._mark_not_delivered = AsyncMock()
    bot._settle_queued_targets = AsyncMock(return_value=[])

    async def _none():
        return []
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _none)
    monitor = SessionMonitor(bot.registry, bot.notify)
    run_async(monitor._scan())

    assert sess.status is Status.GONE
    assert sess.pending_queue == []
    dropped = [e for c in bot._mark_not_delivered.await_args_list
               for e in c.args[1]]
    assert {"msg_id": 41} in dropped
    assert 900 in _deleted(bot)
    assert injected == []
    assert sess.dialog_hold is None


# ── the watchdogs ────────────────────────────────────────────────────────

def test_no_stale_busy_notice_while_a_dialog_waits(
        monkeypatch, run_async, steady_clock, tmp_path, mk_bot):
    """Long past both timeouts, with nothing in the transcript: the session
    still waits for its answer, and the wait is not "no news"."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "boss"
    _question_open(bot, sess)
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    long_ago = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - STALE_BUSY_TIMEOUT - 600
    sess.interactive_entered_at = long_ago
    sess.last_hook_at = long_ago
    sess.busy_started_at = long_ago - 30

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    notify = AsyncMock()
    monitor = SessionMonitor(bot.registry, notify)
    for _ in range(3):
        run_async(monitor._scan())
    events = [c.args[1] for c in notify.await_args_list]
    assert "stale_busy" not in events
    assert sess.status is Status.INTERACTIVE
    assert sess.pending_permission is not None  # the card keeps its question


# ── the release, step by step ────────────────────────────────────────────

def test_the_session_monitor_releases_after_a_tap(
        wired, mk_update, run_async, monkeypatch):
    """A Telegram answer fires no hook of its own: the monitor's tick is
    what sends the held message on."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    events = []

    async def notify(s, event, ctx):
        events.append(event)
        await bot.notify(s, event, ctx)
    run_async(SessionMonitor(bot.registry, notify)._scan())
    assert events[0] == "dialog_hold_settle"
    assert _bodies(injected) == ["held"]
    assert _deleted(bot) == [900]


def test_a_settle_while_the_dialog_is_open_does_nothing(wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    sess.dialog_hold["closed"] = "answered"  # a stale mark, dialog still up
    run_async(bot._settle_dialog_hold(sess))
    assert injected == []
    assert _deleted(bot) == []
    assert sess.dialog_hold is not None


def test_a_new_dialog_mid_release_keeps_the_rest_held(
        wired, mk_update, run_async, monkeypatch):
    """Claude asks again while the held messages go out: the rest wait for
    that dialog, and its first new message gets a notice of its own."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "first", msg_id=41)
    _send(bot, mk_update, run_async, "second", msg_id=42)

    def typed(name, body):
        injected.append(body)
        # Claude asks a new question the moment the first one goes in.
        bot.registry.transition(NAME, Status.INTERACTIVE)
        sess.pending_permission = dict(QUESTION)
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(side_effect=typed))
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot.notify(sess, "dialog_hold_settle", {}))

    assert _bodies(injected) == ["first"]
    assert [q[0] for q in sess.pending_queue] == ["second"]
    assert sess.dialog_hold is not None and not sess.dialog_release_pending()
    _send(bot, mk_update, run_async, "third", msg_id=43)
    assert len(_notices(bot)) == 2, "the new dialog's first message got no notice"


def test_a_new_dialog_before_the_release_takes_the_answer_back(wired):
    """Answered, and a new dialog opens before the held messages went out:
    they wait for the new one."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    sess.dialog_hold = {"entered_at": sess.interactive_entered_at,
                        "told": set(), "notices": []}
    bot.registry.transition(NAME, Status.BUSY)
    assert sess.dialog_release_pending()
    bot.registry.transition(NAME, Status.INTERACTIVE)
    assert not sess.dialog_release_pending()
    assert "closed" not in sess.dialog_hold


def test_the_watchdog_reads_the_transcript_only_when_it_changed(
        monkeypatch, run_async, steady_clock, tmp_path, mk_bot):
    from aipager import session_monitor as sm
    bot = mk_bot()
    sess = bot.registry.get_or_create(NAME)
    _question_open(bot, sess)
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    long_ago = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.interactive_entered_at = sess.last_hook_at = long_ago

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    reads = []
    monkeypatch.setattr(sm, "saved_prompt_evidence",
                        lambda *a: reads.append(a) or "none")
    monitor = SessionMonitor(bot.registry, AsyncMock())
    run_async(monitor._scan())
    run_async(monitor._scan())
    assert len(reads) == 1
    (tmp_path / "t.jsonl").write_text("{}\n")
    run_async(monitor._scan())
    assert len(reads) == 2
    assert reads[-1][2:] == (TUID, "AskUserQuestion")  # the dialog's own call


def test_a_notice_that_lands_after_the_release_is_deleted(wired, mk_update, run_async):
    """The answer came while the notice was on its way: it is deleted as
    soon as it lands, never left saying Claude still waits."""
    bot, sess, injected = wired
    _question_open(bot, sess)

    async def send(**kw):
        # The question is answered and the hold settled meanwhile.
        sess.pending_permission = None
        bot.registry.transition(NAME, Status.BUSY)
        await bot._settle_dialog_hold(sess)
        return MagicMock(message_id=950)
    bot._app.bot.send_message = AsyncMock(side_effect=send)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert _bodies(injected) == ["held"]
    assert _deleted(bot) == [950]
    assert sess.dialog_hold is None


# ── review iteration 1 ───────────────────────────────────────────────────

def test_a_stale_separate_prompt_is_no_evidence_for_a_new_dialog(
        monkeypatch, run_async, steady_clock, tmp_path, mk_bot):
    """An earlier question's own message lingers (``pending_prompt_msg`` is
    never cleared on an answer), and its tool's result is in the
    transcript. A new dialog with nothing shown in the chat must not borrow
    that call's id: read as "answered", the watchdog would end the new
    dialog and the next message would be typed into it."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(NAME)
    bot.registry.transition(NAME, Status.INTERACTIVE)
    long_ago = steady_clock() - INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.interactive_entered_at = sess.last_hook_at = long_ago
    sess.pending_permission = None
    sess.pending_prompt_msg = {
        "msg_id": 600, "chat_id": CHAT_ID, "text": "q",
        "perm": {"ask_question": True, "wait_started_at": long_ago - 500,
                 "tool_info": {"name": "AskUserQuestion", "tool_use_id": "toolu_old"}}}
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({
        "type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {
            "role": "user", "content": [{"type": "tool_result",
                                         "tool_use_id": "toolu_old", "content": "A"}]}}) + "\n")
    sess.transcript_path = str(path)

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    run_async(SessionMonitor(bot.registry, AsyncMock())._scan())
    assert sess.status is Status.INTERACTIVE


def test_no_wait_start_is_no_evidence(tmp_path):
    from aipager.session_monitor import dialog_closed_in_transcript
    from aipager.state import TrackedSession
    sess = TrackedSession(name=NAME, label="boss", status=Status.INTERACTIVE)
    path = tmp_path / "t.jsonl"
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(time.time() - 10, tz=timezone.utc).isoformat()
    path.write_text(json.dumps({"type": "user", "timestamp": stamp,
                                "message": {"role": "user", "content": "hi"}}) + "\n")
    sess.transcript_path = str(path)
    sess.interactive_entered_at = 0.0
    assert dialog_closed_in_transcript(sess, time.monotonic(), time.time()) is False
    sess.interactive_entered_at = time.monotonic() - 3600
    assert dialog_closed_in_transcript(sess, time.monotonic(), time.time()) is True


def test_a_nested_settle_during_the_release_sends_each_message_once(
        wired, mk_update, run_async, monkeypatch):
    """A hook (the injected message's own UserPromptSubmit, a tick) settles
    again while a held message is being typed: one release at a time."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "first", msg_id=41)
    _send(bot, mk_update, run_async, "second", msg_id=42)

    typing = [0]
    overlaps = []

    async def typed(name, body):
        typing[0] += 1
        overlaps.append(typing[0])
        injected.append(body)
        await bot._settle_dialog_hold(sess)  # a hook arrives meanwhile
        typing[0] -= 1
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(side_effect=typed))
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot._settle_dialog_hold(sess))
    assert _bodies(injected) == ["first", "second"]
    assert max(overlaps) == 1, "a second release typed while the first was typing"


def test_a_relaunch_keeps_the_held_messages(wired, mk_update, run_async):
    """/perms kills and relaunches the session: its end is deliberate, and
    what was held waits for the relaunched session."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    bot._mark_not_delivered = AsyncMock()
    bot._settle_queued_targets = AsyncMock(return_value=[])
    sess.restarting_until = time.monotonic() + 60
    bot.registry.transition(NAME, Status.GONE)
    run_async(bot.notify(sess, "session_end", {"source": "other"}))
    assert [q[0] for q in sess.pending_queue] == ["held"]


def test_the_notice_names_this_waits_prompt_not_an_older_one(
        wired, mk_update, run_async):
    """An older wait's separate prompt message is not this dialog's: the
    notice says to answer in the terminal, under the held message."""
    bot, sess, injected = wired
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = {
        "msg_id": 600, "chat_id": CHAT_ID, "text": "q",
        "perm": {"tool_info": {"name": "Bash"},
                 "wait_started_at": sess.interactive_entered_at - 100}}
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert _notices(bot)[0]["reply_to_message_id"] == 41
    assert _notices(bot)[0]["text"] == EN.text(HELD_TERMINAL)


def test_a_card_in_another_chat_gets_the_notice_under_the_message(
        wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)
    sess.scope_chat_id = -2002  # the card lives in another chat
    update = mk_update("held", message_id=41, chat_id=CHAT_ID)
    from aipager.bot.handlers import _claim_dialog_hold
    _claim_dialog_hold(sess)  # as the hold gate does
    run_async(bot._tell_held_for_dialog(update, sess))
    assert _notices(bot)[0]["reply_to_message_id"] == 41
    assert _notices(bot)[0]["chat_id"] == CHAT_ID


def test_a_second_dialog_gets_its_own_notice(wired, mk_update, run_async):
    """Two dialogs in a row with no settle between them: the second one's
    first message is told again."""
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "one", msg_id=41)
    sess.interactive_entered_at += 30  # a new dialog's wait
    _send(bot, mk_update, run_async, "two", msg_id=42)
    assert len(_notices(bot)) == 2


def test_clearqueue_takes_the_notice_away(wired, mk_update, run_async, monkeypatch):
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    bot._mark_not_delivered = AsyncMock()
    bot._settle_queued_targets = AsyncMock(return_value=[])
    monkeypatch.setattr("aipager.bot.session_ops.held_by_claude", lambda *a: [])
    run_async(bot._clear_queue_core(sess))
    assert sess.pending_queue == []
    assert _deleted(bot) == [900]
    assert sess.dialog_hold is None
    _send(bot, mk_update, run_async, "again", msg_id=42)
    assert len(_notices(bot)) == 2  # told again: the first notice is gone


# ── review iteration 2 ───────────────────────────────────────────────────

def test_an_answer_during_the_reaction_still_releases_the_message(
        wired, mk_update, run_async):
    """The question is answered while the 👀 goes out: the message was
    already recorded as held for it, so it goes to Claude with the answer
    rather than at the turn's end, and no notice is sent."""
    bot, sess, injected = wired
    _question_open(bot, sess)

    async def react(update, emoji):
        sess.pending_permission = None
        bot.registry.transition(NAME, Status.BUSY)
    bot._react = AsyncMock(side_effect=react)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    assert _notices(bot) == []
    run_async(bot.notify(sess, "dialog_hold_settle", {}))
    assert _bodies(injected) == ["held"]


def test_an_answered_hold_on_a_session_that_ended_types_nothing(
        wired, mk_update, run_async):
    bot, sess, injected = wired
    _question_open(bot, sess)
    _send(bot, mk_update, run_async, "held", msg_id=41)
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)  # answered...
    sess.status = Status.GONE                   # ...and the process is gone
    run_async(bot._settle_dialog_hold(sess))
    assert injected == []
    assert _deleted(bot) == [900]
