"""The chat around a session parked on a Claude Code background job
(P5, P6, the three buttons).

- P5: a message from someone who may Restart the session brings it back by
  itself when the job is idle (a spawned bring-back; the message goes out
  after it), asks once per person per park when the job is busy, and only
  waits while a bring-back is already running. Anyone Restart refuses
  (the same predicate: may prompt here, this chat's session, the members'
  root rule) gets the held notice instead and brings nothing back.
- P6: one park notice per job, with "Bring it back now"; not again on the
  next tick, nor after a daemon restart; a new job gets a new one.
- ``bgback``/``bgforce``/``bgleave``: PROMPT taps, re-checked like Restart;
  ``bgforce`` forces, ``bgleave`` runs no core, an ended session resumes.

The fake world is tests/test_bring_back_core.py's.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager.bot import reactions, session_parity, tap_gate
from aipager.bot.session_ops import (
    BG_BACK,
    BG_BUSY_QUESTION,
    BG_HELD_NOTICE,
    BG_LEFT,
    BG_PARK_NOTICE,
    RestartOutcome,
)
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status
from tests.test_bring_back_core import (
    CHAT,
    JOB,
    JS,
    NAME,
    WORKER,
    World,
)


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path):
    """tests/test_bring_back_core.py's parked session and its seams."""
    w = World(mk_bot, monkeypatch, tmp_path)
    yield w
    w.close()


EN = i18n.get("en")
OTHER_CHAT = -2002


@pytest.fixture
def chat(world, monkeypatch):
    """The world's bot, ready to take messages: what it types is kept."""
    bot = world.bot
    world.typed = []
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        inject, "send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: world.typed.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    bot.registry.last_active_session = NAME
    return world


def _send(world, mk_update, run_async, text, *, msg_id=9, user_id=12345):
    update = mk_update(text, message_id=msg_id, user_id=user_id, chat_id=CHAT)
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    run_async(world.bot._handle_message(update, ctx))
    return update


def _texts(world):
    return [c.kwargs.get("text", "") for c in world.bot._app.bot.send_message.await_args_list]


def _run_spawned(world, run_async):
    while world.spawned:
        run_async(world.spawned.pop(0))


def _session_start(world, run_async):
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "resume", "session": NAME,
        "session_id": JS}).encode()))


# ---- T3.10: P5, the message gate ---------------------------------------------------

def test_a_permitted_senders_message_brings_the_session_back(chat, mk_update, run_async):
    world = chat
    _send(world, mk_update, run_async, "carry on please", msg_id=21)
    assert world.typed == [] and [q[0] for q in world.sess.pending_queue] == [
        "carry on please"]
    assert len(world.spawned) == 1, "one bring-back spawned"
    assert EN.html(BG_HELD_NOTICE, session="x") not in _texts(world)
    _run_spawned(world, run_async)
    assert world.sess.parked_job == "" and world.runs()[0] == ["stop", JOB]
    assert world.typed == [], "it goes out once the relaunched claude is up"
    _session_start(world, run_async)
    assert len(world.typed) == 1 and world.typed[0].endswith("carry on please")
    assert _texts(world).count(EN.html(BG_BACK, session="x")) == 1


def test_a_busy_job_asks_each_sender_once(chat, mk_update, run_async):
    world = chat
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    question = EN.html(BG_BUSY_QUESTION, session="x")
    _send(world, mk_update, run_async, "one", msg_id=31)
    _run_spawned(world, run_async)
    _send(world, mk_update, run_async, "two", msg_id=32)
    _run_spawned(world, run_async)
    asked = [c for c in world.bot._app.bot.send_message.await_args_list
             if c.kwargs.get("text") == question]
    assert len(asked) == 1
    assert asked[0].kwargs["reply_to_message_id"] == 31
    buttons = [b for row in asked[0].kwargs["reply_markup"].inline_keyboard for b in row]
    assert [b.callback_data.rsplit(":", 1)[1] for b in buttons] == [f"bgforce{JOB}", "bgleave"]
    _send(world, mk_update, run_async, "three", msg_id=33, user_id=777)
    _run_spawned(world, run_async)
    assert _texts(world).count(question) == 2, "another sender is asked too"
    assert world.typed == [] and len(world.sess.pending_queue) == 3
    assert "kill" not in world.kinds() and "launch" not in world.kinds()


def test_while_a_bring_back_runs_a_message_only_waits(chat, mk_update, run_async):
    world = chat
    world.sess.relaunch_in_flight = True
    _send(world, mk_update, run_async, "held", msg_id=41)
    assert world.spawned == [] and world.calls == []
    assert len(world.sess.pending_queue) == 1
    assert EN.html(BG_HELD_NOTICE, session="x") not in _texts(world)


def test_a_sender_restart_refuses_is_held_not_brought_back(chat, mk_update, run_async,
                                                           monkeypatch):
    """The members' root rule (``relaunch_refusal``) is part of the one
    predicate: such a sender's message waits, and nothing is stopped."""
    world = chat
    from aipager import member_root
    monkeypatch.setattr(member_root, "relaunch_refusal",
                        lambda bot, uid, chat_id, cwd: member_root.NO_ROOT)
    _send(world, mk_update, run_async, "held", msg_id=51)
    assert world.spawned == [] and world.calls == []
    assert _texts(world) == [EN.html(BG_HELD_NOTICE, session="x")]
    assert [q[0] for q in world.sess.pending_queue] == ["held"]
    assert any(c.args[1:] == (reactions.HANDED_OFF,)
               for c in world.bot._react.await_args_list)


def test_a_sender_who_may_not_prompt_is_held(chat, mk_update, run_async):
    world = chat
    world.bot._can_prompt_user = MagicMock(return_value=False)
    assert world.bot._bring_back_refusal(world.sess, 12345, CHAT) == "prompt"
    world.bot._can_prompt_user = MagicMock(return_value=True)
    assert world.bot._bring_back_refusal(world.sess, 12345, OTHER_CHAT) == "foreign"
    assert world.bot._bring_back_refusal(world.sess, 12345, CHAT) is None


def test_a_failed_automatic_bring_back_says_why(chat, mk_update, run_async):
    world = chat
    world.stop_rc = 1
    _send(world, mk_update, run_async, "go", msg_id=61)
    _run_spawned(world, run_async)
    assert ("Could not bring <b>x</b> back from Claude Code's background: claude stop "
            "ended with exit code 1") in _texts(world)
    assert world.sess.parked_job == JOB and len(world.sess.pending_queue) == 1


# ---- T3.11: P6, the park notice ----------------------------------------------------

def _notices(world):
    return [c for c in world.bot._app.bot.send_message.await_args_list
            if c.kwargs.get("text") == EN.html(BG_PARK_NOTICE, session="x")]


def test_one_park_notice_per_job(world, run_async):
    sess = world.sess
    run_async(world.bot.notify(sess, "bg_parked", {"job": JOB}))
    notices = _notices(world)
    assert len(notices) == 1 and notices[0].kwargs["chat_id"] == CHAT
    button = notices[0].kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "↩️ Bring it back now"
    assert button.callback_data.rsplit(":", 1)[1] == f"bgback{JOB}"
    assert sess.park_notified_job == JOB
    run_async(world.bot.notify(sess, "bg_parked", {"job": JOB}))
    assert len(_notices(world)) == 1, "not on the next tick"


def test_the_notice_comes_from_the_monitors_tick_once(world, run_async):
    sess = world.sess
    sess.parked_job = ""
    sess.parked_job_session_id = ""
    monitor = SessionMonitor(world.bot.registry, world.bot.notify)
    run_async(monitor._check_parked_tick(NAME, sess))
    assert sess.parked_job == JOB and len(_notices(world)) == 1
    run_async(monitor._check_parked_tick(NAME, sess))
    assert len(_notices(world)) == 1


def test_no_notice_again_after_a_restart(world, run_async, tmp_state_file, monkeypatch):
    sess = world.sess
    run_async(world.bot.notify(sess, "bg_parked", {"job": JOB}))
    world.bot.registry.save()
    monkeypatch.setattr(inject, "live_session_names", lambda: {NAME})
    loaded = SessionRegistry()
    loaded.load()
    again = loaded.get(NAME)
    assert again.park_notified_job == JOB
    world.bot.registry = loaded
    run_async(world.bot.notify(again, "bg_parked", {"job": JOB}))
    assert len(_notices(world)) == 1


def test_a_new_job_gets_a_new_notice(world, run_async):
    sess = world.sess
    run_async(world.bot.notify(sess, "bg_parked", {"job": JOB}))
    sess.parked_job = "ef56ab78"
    run_async(world.bot.notify(sess, "bg_parked", {"job": "ef56ab78"}))
    assert len(_notices(world)) == 2


def test_a_stale_park_event_sends_nothing(world, run_async):
    world.sess.parked_job = ""
    run_async(world.bot.notify(world.sess, "bg_parked", {"job": JOB}))
    assert _notices(world) == []


# ---- T3.12: the buttons ------------------------------------------------------------

@pytest.mark.parametrize("verb", ["bgback", "bgforce", "bgleave"])
def test_the_buttons_need_prompt(verb):
    assert tap_gate.required_capability(NAME, verb) is tap_gate.PROMPT
    assert verb in session_parity._SESSION_ACTIONS


@pytest.mark.parametrize("verb", [f"bgback{JOB}", f"bgforce{JOB}"])
def test_the_keyed_buttons_need_prompt(verb):
    assert tap_gate.required_capability(NAME, verb) is tap_gate.PROMPT


def _query(chat_id=CHAT, user_id=12345):
    q = MagicMock()
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = 77
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = MagicMock(id=chat_id)
    u.effective_user = MagicMock(id=user_id)
    return u, q


def _tap(world, run_async, verb, **kw):
    update, query = _query(**kw)
    handled = run_async(session_parity.handle_callback(
        world.bot, update, query, NAME, verb))
    assert handled is True
    return query


def _answers(query):
    return [(c.args[0] if c.args else "", c.kwargs) for c in query.answer.await_args_list]


@pytest.fixture
def core(world):
    world.bot._bring_back_core = AsyncMock(return_value=RestartOutcome(
        ok=True, reason="done", label="x"))
    world.bot._do_resume = AsyncMock()
    return world.bot._bring_back_core


def test_bring_back_now_brings_it_back_unforced(world, core, run_async):
    q = _tap(world, run_async, f"bgback{JOB}")
    core.assert_awaited_once_with(world.sess, force=False)
    assert _answers(q)[0][0] == "Bringing x back…"
    q.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_stop_and_bring_back_forces(world, core, run_async):
    _tap(world, run_async, f"bgforce{JOB}")
    core.assert_awaited_once_with(world.sess, force=True)


def test_leave_it_runs_no_core(world, core, run_async):
    q = _tap(world, run_async, "bgleave")
    core.assert_not_awaited()
    world.bot._do_resume.assert_not_awaited()
    assert q.edit_message_text.await_args.args[0] == EN.html(BG_LEFT, session="x")


@pytest.mark.parametrize("verb", [f"bgback{JOB}", f"bgforce{JOB}", "bgleave"])
def test_a_role_held_to_its_root_gets_an_alert(world, core, run_async, monkeypatch, verb):
    from aipager import member_root
    monkeypatch.setattr(member_root, "relaunch_refusal",
                        lambda bot, uid, chat_id, cwd: member_root.NO_ROOT)
    q = _tap(world, run_async, verb)
    core.assert_not_awaited()
    assert _answers(q)[0][1].get("show_alert") is True
    q.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("verb", [f"bgback{JOB}", f"bgforce{JOB}", "bgleave"])
def test_another_chats_tap_is_refused(world, core, run_async, verb):
    q = _tap(world, run_async, verb, chat_id=OTHER_CHAT)
    core.assert_not_awaited()
    assert _answers(q)[0][0] == "That session isn't running here."


def test_someone_who_may_not_prompt_is_refused(world, core, run_async):
    world.bot._can_prompt_user = MagicMock(return_value=False)
    q = _tap(world, run_async, f"bgforce{JOB}")
    core.assert_not_awaited()
    assert _answers(q)[0][0] == "You can't restart this session."


def test_a_busy_job_turns_the_card_into_the_question(world, core, run_async):
    core.return_value = RestartOutcome(ok=False, reason="bg_busy", label="x")
    q = _tap(world, run_async, f"bgback{JOB}")
    text, kb = q.edit_message_text.await_args.args[0], q.edit_message_text.await_args.kwargs
    assert text == EN.html(BG_BUSY_QUESTION, session="x")
    buttons = [b for row in kb["reply_markup"].inline_keyboard for b in row]
    assert [b.callback_data.rsplit(":", 1)[1] for b in buttons] == [f"bgforce{JOB}", "bgleave"]


def test_a_session_no_longer_parked_says_so(world, core, run_async):
    world.sess.parked_job = ""
    q = _tap(world, run_async, f"bgback{JOB}")
    core.assert_not_awaited()
    assert _answers(q)[0][0] == "x is not in Claude Code's background."


@pytest.mark.parametrize("verb, force", [("bgback", False), ("bgforce", True)])
def test_on_an_ended_session_the_buttons_resume_it(world, core, run_async, verb, force):
    world.bot.registry.transition(NAME, Status.GONE)
    _tap(world, run_async, verb)
    core.assert_not_awaited()
    kwargs = world.bot._do_resume.await_args.kwargs
    assert kwargs["force"] is force and kwargs["sess"] is world.sess


def test_a_real_tap_brings_it_back(world, run_async):
    """Through the real core: stop, kill, launch, one "is back"."""
    q = _tap(world, run_async, f"bgback{JOB}")
    assert world.sess.parked_job == "" and world.runs()[0] == ["stop", JOB]
    assert _texts(world).count(EN.html(BG_BACK, session="x")) == 1
    q.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_restart_confirm_of_a_busy_parked_session_asks(world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    sess = world.sess
    q = _tap(world, run_async, f"restartok{sess.turn_key}")
    text = q.edit_message_text.await_args.args[0]
    assert text == EN.html(BG_BUSY_QUESTION, session="x")
    kb = q.edit_message_text.await_args.kwargs["reply_markup"]
    assert [b.callback_data.rsplit(":", 1)[1] for row in kb.inline_keyboard
            for b in row] == [f"bgforce{JOB}", "bgleave"]
    assert world.calls == [] and sess.parked_job == JOB


def test_the_callback_data_fits_for_a_long_name(world):
    long_name = "claude-" + "a" * 50 + "__g1001234567890"
    sess = world.bot.registry.get_or_create(long_name)
    sess.label = "a" * 50
    for verb in ("bgback", "bgforce", "bgleave", f"bgforce{'z' * 8}"):
        data = session_parity.session_cb(world.bot, CHAT, sess, verb)
        assert len(data.encode()) <= 64, data



# ---- review rev-iter1-011: a button acts only on the park it was shown for -------------

@pytest.mark.parametrize("verb", ["bgforce", "bgforce0f0f0f0f", "bgback", "bgback0f0f0f0f"])
def test_a_button_from_another_park_is_out_of_date(world, core, run_async, verb):
    """A "Stop and bring back" from an earlier park (another job), or one
    that names no job at all, never stops the job of now."""
    q = _tap(world, run_async, verb)
    core.assert_not_awaited()
    assert _answers(q)[0][0] == "x is not in Claude Code's background."
    q.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert world.sess.parked_job == JOB


def test_the_question_of_an_earlier_park_does_not_stop_a_new_one(world, run_async):
    """Through the real core: the busy question of park A, tapped once the
    session is parked on job B."""
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    q = _tap(world, run_async, f"bgback{JOB}")
    kb = q.edit_message_text.await_args.kwargs["reply_markup"]
    force_verb = kb.inline_keyboard[0][0].callback_data.rsplit(":", 1)[1]
    assert force_verb == f"bgforce{JOB}"
    world.sess.parked_job = "cd34ef56"
    q2 = _tap(world, run_async, force_verb)
    assert world.calls == [], "nothing stopped"
    assert _answers(q2)[0][0] == "x is not in Claude Code's background."


@pytest.mark.parametrize("action, parsed", [
    ("bgback", ("bgback", None)), ("bgforce", ("bgforce", None)),
    ("bgbackab12cd34", ("bgback", "ab12cd34")), ("bgforceab12cd34", ("bgforce", "ab12cd34")),
    ("bgforceab12cd34x", None), ("bgback../x", None), ("bgleave", None)])
def test_the_bring_back_verbs_are_read_strictly(action, parsed):
    assert session_parity.parse_bg_verb(action) == parsed
