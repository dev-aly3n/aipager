"""Unit tests for "⚡ Send now" (aipager/bot/send_now.py, inject.send_now).

The line under a message Claude Code still holds in its queue, its button,
``/now``, and the send-now chord. Scenario rows on the virtual loop live in
tests/integration/send-now-queued-message/.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import send_now as sn
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args,
)
from aipager.bot.transport import MUTED, SKIPPED
from aipager.dtach import inject
from aipager.state import SessionRegistry, Status, TrackedSession
from aipager.team import Role, Team, User as TeamUser

CHAT = 256113222
NAME = "claude-dev"
PREFIX = "[via Telegram · @owner]\n"


# ── helpers ──────────────────────────────────────────────────────────────

def _bot(mk_bot, tmp_path, *, text="and this too", held=True, team=None):
    bot = mk_bot(team=team)
    sess = bot.registry.get_or_create(NAME)
    sess.label = "dev"
    sess.scope_chat_id = CHAT
    sess.status = Status.BUSY
    transcript = tmp_path / "t.jsonl"
    lines = []
    if held:
        lines.append({"type": "queue-operation", "operation": "enqueue",
                      "content": PREFIX + text})
    transcript.write_text("".join(json.dumps(e) + "\n" for e in lines))
    sess.transcript_path = str(transcript)
    sess.queued_targets.append({"msg_id": 2, "chat_id": CHAT,
                                "raw_text": text, "driver_user_id": 12345})
    bot.registry.last_active_session = NAME
    bot._app.bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=7001))
    bot._app.bot.delete_message = AsyncMock(return_value=True)
    return bot, sess


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.fixture
def instant(monkeypatch):
    """The line timer's wait, made instant (never asyncio.sleep itself)."""
    async def _now(_seconds):
        return None
    monkeypatch.setattr(sn, "_queued_line_sleep", _now)


@pytest.fixture
def keys(monkeypatch):
    """Record terminal writes at the one seam they all share."""
    writes: list[bytes] = []
    state = {"ok": True}

    async def _run(args, stdin=b"", timeout=5):
        writes.append(bytes(stdin))
        return state["ok"], ""

    monkeypatch.setattr(inject, "_run", _run)
    writes_state = SimpleNamespace(writes=writes, state=state)
    return writes_state


def _query(data, *, user_id=12345, chat_id=CHAT, message_id=7001):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock()
    q.from_user.id = user_id
    q.message = MagicMock()
    q.message.message_id = message_id
    q.message.chat = MagicMock()
    q.message.chat.id = chat_id
    q.message.text = sn.LINE_TEXT
    q.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = q
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update, q


def _toasts(q):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in q.answer.await_args_list
            if (c.args and c.args[0] is not None) or c.kwargs.get("text")]


def _cmd_update(mk_update, *, user_id=12345, chat_id=CHAT):
    update = mk_update("/now", message_id=900, user_id=user_id, chat_id=chat_id)
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.effective_message = update.message
    return update


def _replies(update):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in update.message.reply_text.await_args_list]


# ── inject.send_now: the chord ───────────────────────────────────────────

def test_send_now_writes_ctrl_x_then_ctrl_s_as_two_writes(keys, run_async):
    assert run_async(inject.send_now(NAME)) is True
    assert keys.writes == [b"\x18", b"\x13"]


def test_send_now_waits_the_chord_gap_between_the_writes(monkeypatch, run_async):
    stamps = []

    async def _run(args, stdin=b"", timeout=5):
        stamps.append(asyncio.get_running_loop().time())
        return True, ""

    monkeypatch.setattr(inject, "_run", _run)
    run_async(inject.send_now(NAME))
    assert stamps[1] - stamps[0] >= inject.SEND_NOW_CHORD_GAP * 0.9


def test_send_now_never_writes_ctrl_s_alone_after_a_failed_ctrl_x(keys, run_async):
    keys.state["ok"] = False
    assert run_async(inject.send_now(NAME)) is False
    assert keys.writes == [b"\x18"]


def test_send_now_reports_a_failed_ctrl_s(monkeypatch, run_async):
    results = iter([True, False])

    async def _run(args, stdin=b"", timeout=5):
        return next(results), ""

    monkeypatch.setattr(inject, "_run", _run)
    assert run_async(inject.send_now(NAME)) is False


def test_send_now_clears_its_in_flight_mark_even_when_a_write_raises(
        monkeypatch, run_async):
    async def _run(args, stdin=b"", timeout=5):
        raise RuntimeError("boom")

    monkeypatch.setattr(inject, "_run", _run)
    with pytest.raises(RuntimeError):
        run_async(inject.send_now(NAME))
    assert NAME not in inject._CHORD_IN_FLIGHT


@pytest.mark.parametrize("other", [
    lambda: inject.send_keys(NAME, "Escape"),
    lambda: inject.send_text_and_enter(NAME, "hi"),
    lambda: inject.discard_queued_input(NAME),
])
def test_no_other_write_lands_inside_the_chord(keys, run_async, other):
    async def scenario():
        chord = asyncio.ensure_future(inject.send_now(NAME))
        await asyncio.sleep(0)  # the chord's Ctrl+X is out
        await other()
        await chord

    run_async(scenario())
    assert keys.writes[:2] == [b"\x18", b"\x13"], keys.writes


def test_the_chord_wait_is_bounded(keys, run_async):
    """A mark left behind can delay another write, never block it."""
    inject._CHORD_IN_FLIGHT.add(NAME)
    try:
        assert run_async(asyncio.wait_for(inject.send_keys(NAME, "Escape"), 2))
    finally:
        inject._CHORD_IN_FLIGHT.discard(NAME)
    assert keys.writes == [b"\x1b"]


def test_now_is_a_reserved_session_name(run_async):
    assert "now" in inject._RESERVED
    ok, err = run_async(inject.launch_session("now"))
    assert ok is False and "reserved" in err


def test_now_is_registered_in_the_command_list_and_help(mk_bot, mk_update,
                                                        run_async):
    from aipager.bot.lifecycle import LifecycleMixin
    names = [c.command for c in LifecycleMixin._command_list(set())]
    assert "now" in names
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._handle_start_cmd(mk_update("/help"), MagicMock()))
    texts = [str(c.args[1]) for c in bot._app.bot.send_message.await_args_list
             if len(c.args) > 1]
    assert any("/now - " in t for t in texts)


# ── the registry's owed deletes ──────────────────────────────────────────

def test_owed_line_deletes_survive_a_save_and_load(tmp_state_file):
    reg = SessionRegistry()
    reg.queued_line_deletes = [[CHAT, 7001], [-100123, 5]]
    reg.save()
    again = SessionRegistry()
    again.load()
    assert again.queued_line_deletes == [[CHAT, 7001], [-100123, 5]]


@pytest.mark.parametrize("raw", [
    None, "x", 5, [[0, 5]], [[CHAT, 0]], [[CHAT, -3]], [[CHAT]],
    [["1", 2]], [[True, 2]], [[CHAT, 2.5]], [{"a": 1}],
])
def test_malformed_owed_line_deletes_are_dropped_on_load(tmp_state_file, raw):
    from aipager import state
    reg = SessionRegistry()
    reg.save()
    data = json.loads(open(state.SESSION_STATE_FILE).read())
    data["queued_line_deletes"] = raw
    open(state.SESSION_STATE_FILE, "w").write(json.dumps(data))
    again = SessionRegistry()
    again.load()
    assert again.queued_line_deletes == []


def test_a_state_file_without_the_key_loads_empty(tmp_state_file):
    from aipager import state
    reg = SessionRegistry()
    reg.save()
    data = json.loads(open(state.SESSION_STATE_FILE).read())
    del data["queued_line_deletes"]
    open(state.SESSION_STATE_FILE, "w").write(json.dumps(data))
    again = SessionRegistry()
    again.load()
    assert again.queued_line_deletes == []


def test_the_per_session_send_now_state_is_never_persisted():
    for f in ("queued_lines", "queued_line_timers", "prompt_injecting",
              "send_now_inflight"):
        assert f not in SessionRegistry._PERSIST_FIELDS
        assert hasattr(TrackedSession(name="x", label="x"), f)


# ── arming and the timer ─────────────────────────────────────────────────

def test_one_timer_per_message_even_for_a_duplicate_pick_up(mk_bot, tmp_path,
                                                           run_async):
    bot, sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        first = sess.queued_line_timers[2]
        bot._arm_queued_lines(sess, [{"msg_id": 2}, {"msg_id": None},
                                     {"msg_id": "3"}])
        assert sess.queued_line_timers == {2: first}
        first.cancel()
        await _settle()

    run_async(scenario())


def test_no_timer_for_a_message_that_already_has_a_line(mk_bot, tmp_path,
                                                       run_async):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])

    run_async(scenario())
    assert sess.queued_line_timers == {}


def test_the_timer_waits_the_configured_delay(mk_bot, tmp_path, run_async,
                                              monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    waited = []

    async def _wait(seconds):
        waited.append(seconds)

    monkeypatch.setattr(sn, "_queued_line_sleep", _wait)
    run_async(bot._queued_line_timer(sess, 2))
    from aipager import config
    assert waited == [config.QUEUED_LINE_DELAY] == [10.0]


def test_a_held_message_gets_the_line_and_owes_its_delete(
        mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()

    run_async(scenario())
    call = bot._app.bot.send_message.await_args
    assert call.args[:2] == (CHAT, sn.LINE_TEXT)
    assert call.kwargs["reply_to_message_id"] == 2
    button = call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == sn.BUTTON_TEXT
    assert button.callback_data.startswith("_:sx:")
    assert button.callback_data.endswith(":now:2")
    assert len(button.callback_data.encode()) <= 64
    assert call.kwargs["rate_limit_args"] == rate_limit_args(
        kind="skip", priority=PRIORITY_ORNAMENT)
    assert sess.queued_lines == {2: (CHAT, 7001)}
    assert bot.registry.queued_line_deletes == [[CHAT, 7001]]
    assert sess.queued_line_timers == {}


def test_no_line_without_transcript_evidence(mk_bot, tmp_path, run_async,
                                             instant):
    bot, sess = _bot(mk_bot, tmp_path, held=False)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_once_the_message_left_the_queue(mk_bot, tmp_path, run_async,
                                                 instant):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.clear()
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_second_line_for_a_message_that_has_one(mk_bot, tmp_path,
                                                  run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 6000)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_for_a_session_the_registry_no_longer_holds(
        mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)
    bot.registry._sessions.pop(NAME)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_for_a_gone_session(mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.status = Status.GONE
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_for_a_message_from_another_chat(mk_bot, tmp_path, run_async,
                                                 instant):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets[0]["chat_id"] = -100999
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_while_the_chat_is_muted(mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)
    MUTE.mute(CHAT, 600)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


def test_no_line_in_minimal_mode(mk_bot, tmp_path, run_async, instant,
                                 monkeypatch):
    from aipager import session_monitor
    bot, sess = _bot(mk_bot, tmp_path)
    monkeypatch.setattr(session_monitor, "cards_suppressed",
                        lambda chat_id: chat_id == CHAT)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("result", [MUTED, SKIPPED, None,
                                    SimpleNamespace(message_id=None)])
def test_a_refused_send_records_no_line(mk_bot, tmp_path, run_async, instant,
                                        result):
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.send_message = AsyncMock(return_value=result)
    run_async(bot._queued_line_timer(sess, 2))
    assert sess.queued_lines == {}
    assert bot.registry.queued_line_deletes == []


def test_a_send_that_raises_records_no_line(mk_bot, tmp_path, run_async,
                                            instant):
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("gone"))
    run_async(bot._queued_line_timer(sess, 2))
    assert sess.queued_lines == {}


def test_a_message_taken_during_the_send_loses_its_line_at_once(
        mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)

    async def _send(*args, **kwargs):
        sess.queued_targets.clear()  # absorbed while the send was out
        return SimpleNamespace(message_id=7001)

    bot._app.bot.send_message = _send

    async def scenario():
        await bot._queued_line_timer(sess, 2)
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs["message_id"] == 7001


def test_a_timer_cancelled_mid_send_still_records_the_line(
        mk_bot, tmp_path, run_async, instant):
    """Past the wait the send is shielded: cancelled half-way, a line could
    land with nobody holding its id."""
    bot, sess = _bot(mk_bot, tmp_path)
    gate = asyncio.Event

    async def scenario():
        release = gate()

        async def _send(*args, **kwargs):
            await release.wait()
            return SimpleNamespace(message_id=7001)

        bot._app.bot.send_message = _send
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()
        sess.queued_line_timers[2].cancel()
        await _settle()
        release.set()
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {2: (CHAT, 7001)}
    assert bot.registry.queued_line_deletes == [[CHAT, 7001]]


# ── reconcile and delete ─────────────────────────────────────────────────

def test_sync_drops_only_the_lines_whose_message_left(mk_bot, tmp_path,
                                                      run_async):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.append({"msg_id": 3, "chat_id": CHAT,
                                "raw_text": "three"})
    sess.queued_lines = {2: (CHAT, 7001), 3: (CHAT, 7002)}

    async def scenario():
        sess.queued_targets.pop(0)  # 2 absorbed
        bot._sync_queued_lines(sess)
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {3: (CHAT, 7002)}
    assert [c.kwargs["message_id"]
            for c in bot._app.bot.delete_message.await_args_list] == [7001]


def test_sync_cancels_the_timer_of_a_message_that_left(mk_bot, tmp_path,
                                                       run_async):
    bot, sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        timer = sess.queued_line_timers[2]
        sess.queued_targets.clear()
        bot._sync_queued_lines(sess)
        await _settle()
        return timer

    timer = run_async(scenario())
    assert timer.cancelled()
    assert sess.queued_line_timers == {}
    bot._app.bot.send_message.assert_not_awaited()


def test_discard_forgets_the_targets_and_drops_their_lines(mk_bot, tmp_path,
                                                           run_async):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines = {2: (CHAT, 7001)}

    async def scenario():
        bot._discard_queued_targets(sess)
        await _settle()

    run_async(scenario())
    assert sess.queued_targets == [] and sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited_once()


def test_no_bare_queued_targets_clear_left_in_bot_code():
    """Every teardown forgets targets through _discard_queued_targets, so
    the lines go with them."""
    from pathlib import Path
    root = Path(inject.__file__).resolve().parent.parent / "bot"
    offenders = [p.name for p in root.glob("*.py")
                 if "queued_targets.clear()" in p.read_text()
                 and p.name != "send_now.py"]
    assert offenders == []


def test_a_delete_is_an_ornament_and_settles_the_debt(mk_bot, tmp_path,
                                                      run_async):
    bot, _sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._delete_queued_line_later(CHAT, 7001)
        assert bot.registry.queued_line_deletes == [[CHAT, 7001]]
        await _settle()

    run_async(scenario())
    kwargs = bot._app.bot.delete_message.await_args.kwargs
    assert kwargs["rate_limit_args"] == rate_limit_args(
        priority=PRIORITY_ORNAMENT)
    assert bot.registry.queued_line_deletes == []


def test_a_delete_is_not_attempted_while_muted_and_stays_owed(
        mk_bot, tmp_path, run_async):
    bot, _sess = _bot(mk_bot, tmp_path)
    MUTE.mute(CHAT, 600)

    async def scenario():
        bot._delete_queued_line_later(CHAT, 7001)
        await _settle()

    run_async(scenario())
    bot._app.bot.delete_message.assert_not_awaited()
    assert bot.registry.queued_line_deletes == [[CHAT, 7001]]


@pytest.mark.parametrize("exc", [FloodSkipped("short"), FloodMuted(60.0, CHAT)])
def test_a_delete_the_gate_refuses_stays_owed(mk_bot, tmp_path, run_async, exc):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_message = AsyncMock(side_effect=exc)

    async def scenario():
        bot._delete_queued_line_later(CHAT, 7001)
        await _settle()

    run_async(scenario())
    assert bot.registry.queued_line_deletes == [[CHAT, 7001]]


def test_a_delete_of_a_message_already_gone_settles_the_debt(
        mk_bot, tmp_path, run_async):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_message = AsyncMock(side_effect=RuntimeError("gone"))

    async def scenario():
        bot._delete_queued_line_later(CHAT, 7001)
        await _settle()

    run_async(scenario())
    assert bot.registry.queued_line_deletes == []


def test_startup_deletes_every_owed_line_but_a_muted_chats(mk_bot, tmp_path,
                                                          run_async):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot.registry.queued_line_deletes = [[CHAT, 7001], [-100555, 8], [-100777, 9]]
    MUTE.mute(-100555, 600)
    bot._app.bot.delete_message = AsyncMock(
        side_effect=[True, RuntimeError("gone")])
    run_async(bot._delete_owed_queued_lines(bot._app.bot))
    deleted = [c.kwargs["message_id"]
               for c in bot._app.bot.delete_message.await_args_list]
    assert deleted == [7001, 9]
    assert bot.registry.queued_line_deletes == [[-100555, 8]]


def test_startup_keeps_a_line_whose_delete_hit_a_mute(mk_bot, tmp_path,
                                                      run_async):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot.registry.queued_line_deletes = [[CHAT, 7001]]
    bot._app.bot.delete_message = AsyncMock(side_effect=FloodMuted(60.0, CHAT))
    run_async(bot._delete_owed_queued_lines(bot._app.bot))
    assert bot.registry.queued_line_deletes == [[CHAT, 7001]]


def test_recover_sessions_runs_the_owed_deletes(mk_bot, tmp_path, run_async,
                                                monkeypatch):
    bot, _sess = _bot(mk_bot, tmp_path)
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
    bot.registry.queued_line_deletes = [[CHAT, 7001]]
    run_async(bot.recover_sessions())
    bot._app.bot.delete_message.assert_awaited_once_with(
        chat_id=CHAT, message_id=7001)
    assert bot.registry.queued_line_deletes == []


# ── the shared core ──────────────────────────────────────────────────────

def test_core_presses_the_chord_for_a_held_message(mk_bot, tmp_path,
                                                   run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    out = run_async(bot._send_now_core(sess, 2))
    assert out.result == "sent"
    assert keys.writes == [b"\x18", b"\x13"]
    assert sess.send_now_inflight is False


def test_core_reports_a_failed_write(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    keys.state["ok"] = False
    assert run_async(bot._send_now_core(sess, 2)).result == "failed"


@pytest.mark.parametrize("target,expected", [(2, "taken"), (None, "nothing")])
def test_core_without_evidence_sends_nothing(mk_bot, tmp_path, run_async, keys,
                                             target, expected):
    bot, sess = _bot(mk_bot, tmp_path, held=False)
    assert run_async(bot._send_now_core(sess, target)).result == expected
    assert keys.writes == []


def test_core_for_a_message_no_longer_queued_is_taken(mk_bot, tmp_path,
                                                      run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    assert run_async(bot._send_now_core(sess, 99)).result == "taken"
    assert keys.writes == []


def test_core_for_now_with_an_empty_queue_is_nothing(mk_bot, tmp_path,
                                                     run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.clear()
    assert run_async(bot._send_now_core(sess, None)).result == "nothing"
    assert keys.writes == []


def test_core_for_now_acts_on_any_held_message(mk_bot, tmp_path, run_async,
                                               keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.insert(0, {"msg_id": 1, "chat_id": CHAT,
                                   "raw_text": "not in the transcript"})
    assert run_async(bot._send_now_core(sess, None)).result == "sent"


def test_core_refuses_while_a_dialog_is_open(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.status = Status.INTERACTIVE
    assert run_async(bot._send_now_core(sess, 2)).result == "interactive"
    assert keys.writes == []


def test_core_refuses_while_a_permission_prompt_is_pending(mk_bot, tmp_path,
                                                           run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.pending_permission = {"tool": "Bash"}
    assert run_async(bot._send_now_core(sess, 2)).result == "interactive"
    assert keys.writes == []


def _hold_turn(sess):
    """The session's newest turn has its hook events held (roadmap 8.62):
    it waits for its card state behind an older turn's finish."""
    sess.turn_seq = 2
    sess.card_turn_seq = 1
    sess.hold_turn_state(2)
    assert sess.turn_state_held()


def test_core_refuses_while_the_newest_turns_hooks_are_held(
        mk_bot, tmp_path, run_async, keys):
    """A PermissionRequest or AskUserQuestion may be among the held events:
    a dialog can be open in Claude's terminal while the status still reads
    BUSY (review rev-iter1-001)."""
    bot, sess = _bot(mk_bot, tmp_path)
    _hold_turn(sess)
    assert sess.status == Status.BUSY and not sess.pending_permission
    assert run_async(bot._send_now_core(sess, 2)).result == "held"
    assert run_async(bot._send_now_core(sess, None)).result == "held"
    assert keys.writes == []


def test_core_sends_once_the_hold_is_released(mk_bot, tmp_path, run_async,
                                              keys):
    bot, sess = _bot(mk_bot, tmp_path)
    _hold_turn(sess)
    sess.release_turn_state(2)
    assert run_async(bot._send_now_core(sess, 2)).result == "sent"
    assert keys.writes == [b"\x18", b"\x13"]


def test_core_refuses_while_a_prompt_is_being_typed(mk_bot, tmp_path,
                                                    run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.prompt_injecting = 1
    assert run_async(bot._send_now_core(sess, 2)).result == "typing"
    assert keys.writes == []


def test_core_writes_the_chord_once_for_two_quick_taps(mk_bot, tmp_path,
                                                       run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)

    async def scenario():
        return await asyncio.gather(bot._send_now_core(sess, 2),
                                    bot._send_now_core(sess, None))

    outs = run_async(scenario())
    assert [o.result for o in outs] == ["sent", "sent"]
    assert keys.writes == [b"\x18", b"\x13"]


def test_inject_prompt_marks_the_session_as_typing(mk_bot, tmp_path, run_async,
                                                   monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    seen = []

    async def _type(name, body):
        seen.append(sess.prompt_injecting)
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _type)
    run_async(bot._inject_prompt(sess, "hello"))
    assert seen == [1]
    assert sess.prompt_injecting == 0


def test_inject_prompt_clears_the_typing_mark_when_the_write_raises(
        mk_bot, tmp_path, run_async, monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    monkeypatch.setattr(inject, "send_text_and_enter",
                        AsyncMock(side_effect=RuntimeError("pty")))
    with pytest.raises(RuntimeError):
        run_async(bot._inject_prompt(sess, "hello"))
    assert sess.prompt_injecting == 0


# ── the tap ──────────────────────────────────────────────────────────────

def _cb(bot, sess, msg_id=2):
    from aipager.bot import session_parity
    return session_parity.session_cb(bot, CHAT, sess, f"now:{msg_id}")


def test_tap_sends_now_and_deletes_its_line(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    update, q = _query(_cb(bot, sess))

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == [sn.TOAST_SENT]
    assert keys.writes == [b"\x18", b"\x13"]
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited_once()
    assert b"\x1b" not in b"".join(keys.writes)


def test_tap_on_a_taken_message_deletes_the_line_without_keys(
        mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path, held=False)
    sess.queued_lines[2] = (CHAT, 7001)
    update, q = _query(_cb(bot, sess))

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == [sn.TOAST_TAKEN]
    assert keys.writes == []
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited_once()


def test_tap_on_a_line_whose_record_is_gone_deletes_the_tapped_message(
        mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.clear()
    update, q = _query(_cb(bot, sess), message_id=6500)

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == [sn.TOAST_TAKEN]
    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs["message_id"] == 6500


@pytest.mark.parametrize("setup,toast", [
    (lambda s: setattr(s, "status", Status.INTERACTIVE), sn.TOAST_INTERACTIVE),
    (_hold_turn, sn.TOAST_BUSY),
    (lambda s: setattr(s, "prompt_injecting", 1), sn.TOAST_TYPING),
])
def test_tap_refusals_keep_the_line(mk_bot, tmp_path, run_async, keys, setup,
                                    toast):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    setup(sess)
    update, q = _query(_cb(bot, sess))

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == [toast]
    assert keys.writes == []
    assert sess.queued_lines == {2: (CHAT, 7001)}
    bot._app.bot.delete_message.assert_not_awaited()


def test_a_failed_chord_keeps_the_line(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    keys.state["ok"] = False
    update, q = _query(_cb(bot, sess))
    run_async(bot._handle_callback(update, MagicMock()))
    assert _toasts(q) == [sn.TOAST_FAILED]
    assert sess.queued_lines == {2: (CHAT, 7001)}


def test_a_read_only_member_cannot_tap(mk_bot, tmp_path, run_async, keys):
    team = Team(group_id=CHAT, users={
        12345: TeamUser(id=12345, label="owner", role=Role.ADMIN),
        999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot, sess = _bot(mk_bot, tmp_path, team=team)
    sess.queued_lines[2] = (CHAT, 7001)
    update, q = _query(_cb(bot, sess), user_id=999)

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == [sn.TOAST_CANNOT_PROMPT]
    assert keys.writes == []
    assert sess.queued_lines == {2: (CHAT, 7001)}


def test_a_tap_from_another_chat_is_refused(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    update, q = _query(_cb(bot, sess), chat_id=-100999)
    run_async(bot._handle_callback(update, MagicMock()))
    assert _toasts(q) == [sn.TOAST_UNAVAILABLE]
    assert keys.writes == []


def test_a_long_form_tap_for_a_session_scoped_elsewhere_is_refused(
        mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    update, q = _query(f"{NAME}:now:2", chat_id=-100999)
    run_async(bot._handle_callback(update, MagicMock()))
    assert _toasts(q) == [sn.TOAST_UNAVAILABLE]
    assert keys.writes == []


def test_a_tap_with_a_malformed_message_id_is_invalid(mk_bot, tmp_path,
                                                      run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    update, q = _query(f"{NAME}:now:abc")
    run_async(bot._handle_callback(update, MagicMock()))
    assert _toasts(q) == ["Invalid callback"]
    assert keys.writes == []


def test_a_tap_for_a_missing_session_deletes_the_tapped_line(
        mk_bot, tmp_path, run_async, keys):
    bot, _sess = _bot(mk_bot, tmp_path)
    update, q = _query("claude-nope:now:2", message_id=6600)

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert _toasts(q) == ["Session not found"]
    assert keys.writes == []
    assert bot._app.bot.delete_message.await_args.kwargs["message_id"] == 6600


def test_a_tap_deletes_only_its_own_line(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.append({"msg_id": 3, "chat_id": CHAT,
                                "raw_text": "three"})
    sess.queued_lines = {2: (CHAT, 7001), 3: (CHAT, 7002)}
    update, q = _query(_cb(bot, sess))

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {3: (CHAT, 7002)}


# ── /now ────────────────────────────────────────────────────────────────

def test_now_sends_the_chord(mk_bot, tmp_path, run_async, keys, mk_update):
    bot, sess = _bot(mk_bot, tmp_path)
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.REPLY_SENT]
    assert keys.writes == [b"\x18", b"\x13"]


@pytest.mark.parametrize("setup,reply", [
    (lambda s: s.queued_targets.clear(), sn.REPLY_NOTHING),
    (lambda s: setattr(s, "status", Status.INTERACTIVE), sn.TOAST_INTERACTIVE),
    (_hold_turn, sn.TOAST_BUSY),
    (lambda s: setattr(s, "prompt_injecting", 2), sn.TOAST_TYPING),
])
def test_now_refusals(mk_bot, tmp_path, run_async, keys, mk_update, setup,
                      reply):
    bot, sess = _bot(mk_bot, tmp_path)
    setup(sess)
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [reply]
    assert keys.writes == []


def test_now_reports_a_failed_write(mk_bot, tmp_path, run_async, keys,
                                    mk_update):
    bot, _sess = _bot(mk_bot, tmp_path)
    keys.state["ok"] = False
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.TOAST_FAILED]


def test_now_with_no_active_session(mk_bot, tmp_path, run_async, keys,
                                    mk_update):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot.registry.last_active_session = ""
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.REPLY_NO_SESSION]
    bot.registry.last_active_session = "claude-gone"
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.REPLY_NO_SESSION]
    assert keys.writes == []


def test_now_for_a_session_of_another_chat(mk_bot, tmp_path, run_async, keys,
                                           mk_update):
    bot, _sess = _bot(mk_bot, tmp_path)
    update = _cmd_update(mk_update, chat_id=-100999)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.REPLY_OTHER_CHAT]
    assert keys.writes == []


def test_now_from_a_read_only_member_is_refused(mk_bot, tmp_path, run_async,
                                                keys, mk_update):
    team = Team(group_id=CHAT, users={
        999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot, _sess = _bot(mk_bot, tmp_path, team=team)
    update = _cmd_update(mk_update, user_id=999)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert keys.writes == []
    assert len(_replies(update)) == 1 and "read_only" in _replies(update)[0]


def test_now_deletes_no_line_itself(mk_bot, tmp_path, run_async, keys,
                                    mk_update):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    update = _cmd_update(mk_update)

    async def scenario():
        await bot._handle_now_cmd(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {2: (CHAT, 7001)}
    bot._app.bot.delete_message.assert_not_awaited()


# ── the queue_pickup hook arms the timer ─────────────────────────────────

def test_a_message_queued_while_busy_arms_its_line(mk_bot, tmp_path,
                                                   run_async):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.clear()
    sess.trigger_msg_id = 1

    async def scenario():
        await bot.notify(sess, "queue_pickup", {"consumed": [
            {"msg_id": 2, "chat_id": CHAT, "raw_text": "and this too"}],
            "expired": []})
        armed = dict(sess.queued_line_timers)
        for t in armed.values():
            t.cancel()
        await _settle()
        return armed

    armed = run_async(scenario())
    assert list(armed) == [2]


def test_the_turns_own_message_arms_nothing(mk_bot, tmp_path, run_async):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.clear()
    sess.trigger_msg_id = 2

    async def scenario():
        await bot.notify(sess, "queue_pickup", {"consumed": [
            {"msg_id": 2, "chat_id": CHAT, "raw_text": "and this too"}],
            "expired": []})
        return dict(sess.queued_line_timers)

    assert run_async(scenario()) == {}
