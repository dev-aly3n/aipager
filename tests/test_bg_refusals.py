"""Everything that would type into a parked session says "bring it back
first" instead (P4, P7).

A session parked on a Claude Code background job is never typed into.
Every caller that would type for a person refuses up front and says so:
compact, a model switch, an in-place mode switch, /now and Send now,
Retry, Compact Now and the keystroke answers to a prompt. /clearqueue
drops the held messages without typing anything. An answer through the
permission hook (no keys) still works: the linked job's prompts go there.

The terminal writes are captured through the module attributes the
production code calls (``inject.send_keys``, ``inject.send_text_and_enter``,
...), never by patching asyncio.
"""

from __future__ import annotations

import html
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import callbacks, mode_switch, new_flow
from aipager.bot.session_ops import (
    BG_ANSWER_REFUSED,
    BG_REFUSED_PARKED,
    BG_TOAST_ANSWER_REFUSED,
    BG_TOAST_REFUSED_PARKED,
)
from aipager.dtach import inject
from aipager.state import ModeSwitch, Status, TrackedSession
from tests.test_miniapp_session_actions_api import (  # noqa: F401 - fixtures
    ADMIN_ID,
    _client_for,
    _configured_bot_token,
    _hdr,
    _mk_session,
    server,
)
from tests.test_new_flow import OWNER, _last_edit, _ready_session, mk_cb as _new_flow_cb, wbot  # noqa: F401
from tests.test_separate_permission_prompt import BOB, GROUP, _World

JOB = "ab12cd34"


def _render(msg, label):
    return msg.replace("{session}", label)


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


# ---- T2.16: answers typed as keys -------------------------------------------

def test_a_keystroke_allow_is_not_typed_while_parked(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, hook=None)
    sess.parked_job = JOB
    q = world.tap(run_async, GROUP, BOB, "allow")
    assert world.keys == []
    assert _render(BG_TOAST_ANSWER_REFUSED, "api") in _answers(q)


def test_the_keystroke_allow_is_typed_when_not_parked(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, hook=None)
    world.tap(run_async, GROUP, BOB, "allow")
    assert world.keys == ["Enter"]


@pytest.mark.parametrize("verb", ["deny", "allow_always", "opt1"])
def test_other_typed_answers_are_refused_while_parked(world, run_async, verb):
    sess = world.session()
    tool_info = None
    if verb == "opt1":
        tool_info = {"name": "AskUserQuestion", "summary": "AskUserQuestion",
                     "input": {"questions": [{"question": "Pick one",
                                              "options": [{"label": "A"},
                                                          {"label": "B"}]}]}}
    world.prompt(sess, tool_info=tool_info, run_async=run_async, hook=None)
    sess.parked_job = JOB
    q = world.tap(run_async, GROUP, BOB, verb)
    assert world.keys == []
    assert _render(BG_TOAST_ANSWER_REFUSED, "api") in _answers(q)


def test_an_answer_through_the_hook_still_goes_while_parked(world, run_async):
    """The linked job's permission request waits on the hook's reply
    socket: answering it types nothing, so it is never refused."""
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    sess.parked_job = JOB
    world.tap(run_async, GROUP, BOB, "allow")
    assert world.keys == []
    assert [d for _r, d in world.decisions] == [{"behavior": "allow"}]


def test_an_auto_deny_is_not_typed_while_parked(world, run_async):
    sess = world.session()
    sess.parked_job = JOB
    assert run_async(world.bot._type_auto_deny(sess, "Bash")) is False
    assert world.keys == []
    assert [t for t, _ in world.lines(GROUP)] == [_render(BG_ANSWER_REFUSED, "api")]


def test_an_auto_deny_is_typed_when_not_parked(world, run_async):
    sess = world.session()
    assert run_async(world.bot._type_auto_deny(sess, "Bash")) is True
    assert world.keys == ["Escape"]


def test_a_mode_switch_retry_presses_nothing_while_parked():
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    pending = ModeSwitch(target="auto", presses=1, sent_at=time.monotonic())
    sess.mode_switch = pending
    assert mode_switch._retry_may_press(sess, pending) is True
    sess.parked_job = JOB
    assert mode_switch._retry_may_press(sess, pending) is False


# ---- T2.17: the cores ---------------------------------------------------------

@pytest.fixture
def parked(mk_bot, monkeypatch):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    typed = AsyncMock(return_value=True)
    keys = AsyncMock(return_value=True)
    presses = AsyncMock(return_value=True)
    discard = AsyncMock(return_value=True)
    chord = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", typed)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", keys)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", presses)
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", discard)
    monkeypatch.setattr("aipager.dtach.inject.send_now", chord)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    sess = bot.registry.get_or_create("claude-x")
    sess.label = "x"
    bot.registry.transition("claude-x", Status.IDLE)
    sess.parked_job = JOB
    writes = {"typed": typed, "keys": keys, "presses": presses,
              "discard": discard, "chord": chord}
    return bot, sess, writes


def _nothing_written(writes):
    return all(m.await_count == 0 for m in writes.values())


@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY])
def test_compact_is_refused_while_parked(parked, run_async, status):
    bot, sess, writes = parked
    bot.registry.transition("claude-x", status)
    out = run_async(bot._compact_session_core(sess))
    assert (out.ok, out.reason) == (False, "parked")
    assert sess.pending_queue == [], "not queued either"
    assert _nothing_written(writes)


def test_a_model_switch_is_refused_while_parked(parked, run_async):
    bot, sess, writes = parked
    out = run_async(bot._switch_model_core(sess, "opus"))
    assert (out.ok, out.reason) == (False, "parked")
    assert out.detail == _render(BG_TOAST_REFUSED_PARKED, "x")
    assert _nothing_written(writes)
    assert sess.model_switch_pending() is False


def test_an_in_place_mode_switch_is_refused_while_parked(parked, run_async):
    bot, sess, writes = parked
    sess.perm_mode = "ask"
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason, out.in_place) == (False, "parked", True)
    assert sess.perm_mode == "ask" and sess.mode_switch is None
    assert _nothing_written(writes)
    assert callbacks._switch_refused(out) == _render(BG_REFUSED_PARKED, "x")


def test_now_is_refused_while_parked(parked, run_async):
    bot, sess, writes = parked
    sess.queued_targets.append({"msg_id": 5, "chat_id": -1, "text": "hi"})
    out = run_async(bot._send_now_core(sess, None))
    assert out.result == "parked"
    assert _nothing_written(writes)


def test_now_says_bring_it_back_first(parked, run_async, mk_update):
    bot, sess, writes = parked
    bot._authorize = AsyncMock(return_value=True)
    bot.registry.target_for = MagicMock(return_value=sess)
    update = mk_update("/now")
    run_async(bot._handle_now_cmd(update, MagicMock()))
    texts = [c.args[0] if c.args else c.kwargs.get("text")
             for c in update.message.reply_text.await_args_list]
    assert texts == [_render(BG_REFUSED_PARKED, "x")]
    assert _nothing_written(writes)


def test_inject_prompt_types_nothing_and_writes_no_note(parked, run_async, monkeypatch):
    bot, sess, writes = parked
    notes = []
    monkeypatch.setattr("aipager.policy_snapshot.write_note",
                        lambda *a, **k: notes.append(a))
    assert run_async(bot._inject_prompt(sess, "hello")) is False
    assert _nothing_written(writes)
    assert notes == []


def test_the_probe_alone_also_refuses(parked, run_async):
    """The cores ask the terminal guard's probe too, not only the field."""
    bot, sess, writes = parked
    sess.parked_job = ""
    inject.set_parked_probe(lambda name: name == "claude-x")
    out = run_async(bot._compact_session_core(sess))
    assert out.reason == "parked"
    assert run_async(bot._inject_prompt(sess, "hello")) is False
    assert _nothing_written(writes)


def test_clearqueue_drops_the_held_messages_without_typing(parked, run_async,
                                                          monkeypatch):
    """Even with Claude's own queue said to hold a message (which makes an
    ordinary /clearqueue wipe the input box), nothing is typed."""
    bot, sess, writes = parked
    monkeypatch.setattr("aipager.bot.session_ops.held_by_claude",
                        lambda _s, _c: [{"msg_id": 43, "chat_id": -1, "text": "x"}])
    sess.queue_prompt("one", 41)
    sess.queue_prompt("two", 42)
    bot._mark_not_delivered = AsyncMock()
    out = run_async(bot._clear_queue_core(sess))
    assert (out.ok, out.dropped) == (True, 2)
    assert sess.pending_queue == []
    dropped = [e.get("msg_id") for c in bot._mark_not_delivered.await_args_list
               for e in c.args[1]]
    assert sorted(dropped) == [41, 42]
    assert _nothing_written(writes)


def test_clearqueue_with_nothing_held_is_refused(parked, run_async):
    bot, sess, writes = parked
    out = run_async(bot._clear_queue_core(sess))
    assert (out.ok, out.dropped) == (False, 0)
    assert _nothing_written(writes)


def test_a_typed_model_command_is_refused_while_parked(parked, run_async, mk_update):
    bot, sess, writes = parked
    bot._authorize = AsyncMock(return_value=True)
    bot._target = MagicMock(return_value=sess)
    bot._react = AsyncMock()
    update = mk_update("/model opus")
    run_async(bot._send_command(update, "/model opus"))
    texts = [c.args[0] if c.args else c.kwargs.get("text")
             for c in update.message.reply_text.await_args_list]
    assert texts == [_render(BG_REFUSED_PARKED, "x")]
    assert sess.pending_queue == [], "never held for later either"
    assert _nothing_written(writes)


# ---- T2.17: the buttons ---------------------------------------------------------

@pytest.fixture
def mk_query():
    def _mk(data, *, user_id=12345):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.message = MagicMock(message_id=42, text="")
        query.from_user = MagicMock(id=user_id)
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query
    return _mk


@pytest.mark.parametrize("action", ["retry", "compact"])
def test_retry_and_compact_now_are_refused_while_parked(parked, run_async, mk_query,
                                                       action):
    bot, sess, writes = parked
    sess.last_prompt = "do it again"
    update, query = mk_query(f"claude-x:{action}")
    run_async(bot._handle_callback(update, MagicMock()))
    assert _render(BG_TOAST_REFUSED_PARKED, "x") in [
        c.args[0] for c in query.answer.await_args_list if c.args]
    assert _nothing_written(writes)


def test_send_now_tap_is_refused_while_parked(parked, run_async, mk_query):
    bot, sess, writes = parked
    sess.queued_targets.append({"msg_id": 5, "chat_id": -1, "text": "hi"})
    update, query = mk_query("claude-x:now:5")
    update.effective_chat = MagicMock(id=-1)
    query.message.chat = MagicMock(id=-1)
    query.message.chat_id = -1
    run_async(bot._handle_send_now_tap(update, query, "claude-x", "5"))
    assert _render(BG_TOAST_REFUSED_PARKED, "x") in [
        c.args[0] for c in query.answer.await_args_list if c.args]
    assert _nothing_written(writes)


# ---- the Mini App routes --------------------------------------------------------

@pytest.mark.parametrize("route, body", [
    ("compact", None), ("perms", {"mode": "auto"}), ("model", {"model": "opus"})])
def test_the_mini_app_refuses_while_parked(server, run_async, monkeypatch,  # noqa: F811
                                           route, body):
    send = AsyncMock(return_value=True)
    presses = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", presses)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))

    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE, mode="ask")
        sess.parked_job = JOB
        client = await _client_for(server)
        try:
            resp = await client.post(f"/api/sessions/dev/{route}", headers=_hdr(ADMIN_ID),
                                     json=body)
            assert resp.status == 409
            assert await resp.json() == {
                "error": "parked", "detail": _render(BG_TOAST_REFUSED_PARKED, "dev")}
        finally:
            await client.close()
    run_async(_run())
    assert send.await_count == 0 and presses.await_count == 0


# ---- the ready card (/new) and the viewer's own language --------------------

@pytest.fixture
def ready(wbot, monkeypatch):  # noqa: F811
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    typed = AsyncMock(return_value=True)
    presses = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", typed)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", presses)
    bot = wbot()
    sess = _ready_session(bot, mode="ask")
    sess.parked_job = JOB
    return bot, sess, typed, presses


def _cb(user_id=OWNER):
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=user_id)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=OWNER, type="private")
    update.message = None
    return update, query


def test_the_ready_cards_model_button_refuses_in_the_tappers_language(ready, run_async):
    bot, sess, typed, presses = ready
    preferences.set_ui_language(OWNER, "de")
    update, query = _cb()
    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0"))
    want = i18n.get("de").text(BG_TOAST_REFUSED_PARKED, session="dev")
    assert want != _render(BG_TOAST_REFUSED_PARKED, "dev")
    assert query.answer.await_args.args[0] == want
    assert typed.await_count == 0


def test_the_ready_cards_in_place_switch_says_bring_it_back(ready, run_async):
    bot, sess, typed, presses = ready
    update, query = _cb()
    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_auto"))
    assert _render(BG_TOAST_REFUSED_PARKED, "dev") in html.unescape(_last_edit(bot)["text"])
    assert presses.await_count == 0 and sess.perm_mode == "ask"


def test_the_mini_app_refuses_a_model_in_the_viewers_language(server, run_async,  # noqa: F811
                                                             monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    preferences.set_ui_language(ADMIN_ID, "de")

    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        sess.parked_job = JOB
        client = await _client_for(server)
        try:
            resp = await client.post("/api/sessions/dev/model", headers=_hdr(ADMIN_ID),
                                     json={"model": "opus"})
            assert resp.status == 409
            return await resp.json()
        finally:
            await client.close()
    body = run_async(_run())
    assert body == {"error": "parked",
                    "detail": i18n.get("de").text(BG_TOAST_REFUSED_PARKED, session="dev")}
    assert send.await_count == 0
