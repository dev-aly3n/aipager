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
    PRIORITY_INSTANT,
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


@pytest.mark.parametrize("other,its_writes", [
    (lambda: inject.send_keys(NAME, "Escape"), [b"\x1b"]),
    (lambda: inject.send_text_and_enter(NAME, "hi"), [b"hi", b"\r"]),
    (lambda: inject.discard_queued_input(NAME), [b"\x1b", b"\x15"]),
], ids=["send_keys", "send_text_and_enter", "discard_queued_input"])
def test_a_write_inside_a_slow_chord_means_no_ctrl_s(monkeypatch, run_async,
                                                     other, its_writes):
    """A Ctrl+X write that outlasts the other writer's bounded wait: that
    writer goes ahead, so the chord is broken, and its Ctrl+S (which would
    stash the input box) is never written."""
    monkeypatch.setattr(inject, "_CHORD_WAIT_LIMIT", 0.05)
    writes: list[bytes] = []

    async def _run(args, stdin=b"", timeout=5):
        writes.append(bytes(stdin))
        if stdin == b"\x18":
            await asyncio.sleep(0.2)  # a slow dtach -p, past the wait limit
        return True, ""

    monkeypatch.setattr(inject, "_run", _run)

    async def scenario():
        chord = asyncio.ensure_future(inject.send_now(NAME))
        await asyncio.sleep(0)  # the chord's Ctrl+X write has started
        other_ok = await other()
        return await chord, other_ok

    chord_ok, other_ok = run_async(scenario())
    assert chord_ok is False
    assert other_ok is True
    assert b"\x13" not in writes, writes
    assert writes == [b"\x18", *its_writes]


def test_a_write_before_the_chord_does_not_block_its_ctrl_s(keys, run_async):
    """Only a write AFTER the Ctrl+X breaks the chord: earlier ones never
    stop the Ctrl+S."""
    async def scenario():
        await inject.send_keys(NAME, "Escape")
        return await inject.send_now(NAME)

    assert run_async(scenario()) is True
    assert keys.writes == [b"\x1b", b"\x18", b"\x13"]


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
    update = mk_update("/help")
    run_async(bot._handle_help_cmd(update, MagicMock()))
    assert "/now" in update.message.reply_text.await_args.args[0]


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


def test_the_timer_sends_at_once_with_no_step_running(mk_bot, tmp_path,
                                                     run_async, monkeypatch):
    """No due delay any more (operator, 2026-09-29): with the queue record
    there the line goes at once, whatever Claude's step is doing."""
    bot, sess = _bot(mk_bot, tmp_path)
    waited = []

    async def _wait(seconds):
        waited.append(seconds)

    monkeypatch.setattr(sn, "_queued_line_sleep", _wait)
    sess.parent_tool_started_at = None
    run_async(bot._queued_line_timer(sess, 2))
    assert waited == []
    bot._app.bot.send_message.assert_awaited_once()


def test_the_timer_sends_at_once_behind_an_old_step(mk_bot, tmp_path,
                                                    run_async, monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    waited = []

    async def _wait(seconds):
        waited.append(seconds)

    monkeypatch.setattr(sn, "_queued_line_sleep", _wait)

    async def scenario():
        sess.parent_tool_started_at = asyncio.get_running_loop().time() - 7
        await bot._queued_line_timer(sess, 2)

    run_async(scenario())
    assert waited == []
    bot._app.bot.send_message.assert_awaited_once()


def test_the_due_rule_is_gone():
    """The 10 s / 3 s due rule was removed (2026-09-29): nothing may bring
    it back quietly."""
    from aipager import config
    assert not hasattr(sn, "_queued_line_wait")
    assert not hasattr(config, "QUEUED_LINE_DELAY")
    assert not hasattr(config, "QUEUED_LINE_TOOL_AGE")


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
    # INSTANT: never waits for a chat token (operator, 2026-09-29).
    assert call.kwargs["rate_limit_args"] == rate_limit_args(
        priority=PRIORITY_INSTANT)
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


def test_the_line_is_sent_in_minimal_mode(mk_bot, tmp_path, run_async,
                                          instant, monkeypatch):
    """Minimal mode suspends the card's decoration, not this line
    (operator, 2026-09-29: the flood manager must not block it)."""
    from aipager import session_monitor
    bot, sess = _bot(mk_bot, tmp_path)
    monkeypatch.setattr(session_monitor, "cards_suppressed",
                        lambda chat_id: chat_id == CHAT)
    run_async(bot._queued_line_timer(sess, 2))
    bot._app.bot.send_message.assert_awaited_once()


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


def test_a_delete_is_instant_and_settles_the_debt(mk_bot, tmp_path,
                                                   run_async):
    bot, _sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._delete_queued_line_later(CHAT, 7001)
        assert bot.registry.queued_line_deletes == [[CHAT, 7001]]
        await _settle()

    run_async(scenario())
    kwargs = bot._app.bot.delete_message.await_args.kwargs
    assert kwargs["rate_limit_args"] == rate_limit_args(
        priority=PRIORITY_INSTANT)
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


def test_a_tap_deletes_every_line(mk_bot, tmp_path, run_async, keys):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_targets.append({"msg_id": 3, "chat_id": CHAT,
                                "raw_text": "three"})
    sess.queued_lines = {2: (CHAT, 7001), 3: (CHAT, 7002)}
    update, q = _query(_cb(bot, sess))

    async def scenario():
        await bot._handle_callback(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {}
    # Message 3's target stays: it still routes the answer and reactions.
    assert [t["msg_id"] for t in sess.queued_targets] == [2, 3]


# ── a tap whose chord outlasts the ack bound ────────────────────────────

def test_the_taps_wait_for_its_chord_is_below_the_ack_bound():
    from aipager.bot.callbacks import CALLBACK_ACK_BOUND
    assert 0 < sn.SEND_NOW_TOAST_WAIT < CALLBACK_ACK_BOUND


def test_no_em_dash_in_the_slow_chord_texts():
    assert "\u2014" not in sn.TOAST_SENDING + sn.LINE_TEXT_UNREACHED


def _slow_chord(monkeypatch, *, ok=True, ctrl_x=1.0):
    """A Ctrl+X ``dtach -p`` of *ctrl_x* seconds; the Ctrl+S returns *ok*."""
    writes: list[bytes] = []

    async def _run(args, stdin=b"", timeout=5):
        writes.append(bytes(stdin))
        if stdin == b"\x18":
            await asyncio.sleep(ctrl_x)
            return True, ""
        return ok, ""

    monkeypatch.setattr(inject, "_run", _run)
    return writes


def _slow_tap(bot, sess, run_async):
    """Tap, and return ``(seconds to the first toast, toasts)``."""
    update, q = _query(_cb(bot, sess))
    stamps: list[float] = []

    async def _answer(*args, **kwargs):
        stamps.append(asyncio.get_running_loop().time())

    q.answer = AsyncMock(side_effect=_answer)

    async def scenario():
        t0 = asyncio.get_running_loop().time()
        await bot._handle_callback(update, MagicMock())
        await _settle()
        return stamps[0] - t0

    first = run_async(scenario())
    return first, _toasts(q)


def test_a_slow_chord_tap_toasts_sending_before_the_ack_bound(
        mk_bot, tmp_path, run_async, monkeypatch):
    from aipager.bot.callbacks import CALLBACK_ACK_BOUND
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    writes = _slow_chord(monkeypatch)
    first, toasts = _slow_tap(bot, sess, run_async)
    assert toasts == [sn.TOAST_SENDING]
    assert first < CALLBACK_ACK_BOUND
    assert writes == [b"\x18", b"\x13"]


def test_a_slow_chord_that_goes_through_deletes_the_line(
        mk_bot, tmp_path, run_async, monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    bot._app.bot.edit_message_text = AsyncMock()
    _slow_chord(monkeypatch)
    _slow_tap(bot, sess, run_async)
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_a_slow_chord_that_fails_says_so_on_the_line(mk_bot, tmp_path,
                                                     run_async, monkeypatch):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    bot._app.bot.edit_message_text = AsyncMock()
    _slow_chord(monkeypatch, ok=False)
    _, toasts = _slow_tap(bot, sess, run_async)
    assert toasts == [sn.TOAST_SENDING]
    assert sess.queued_lines == {2: (CHAT, 7001)}
    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.edit_message_text.assert_awaited_once()
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    assert (kw.get("chat_id"), kw.get("message_id")) == (CHAT, 7001)
    assert kw.get("text") == sn.LINE_TEXT_UNREACHED
    markup = kw.get("reply_markup")
    assert markup is not None, "the edit would remove the button"
    button = markup.inline_keyboard[0][0]
    assert button.text == sn.BUTTON_TEXT
    assert button.callback_data == _cb(bot, sess)


def test_a_slow_chord_that_fails_after_the_line_went_edits_nothing(
        mk_bot, tmp_path, run_async, monkeypatch):
    """The message was taken while the chord was out: its line is gone."""
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.edit_message_text = AsyncMock()
    _slow_chord(monkeypatch, ok=False)

    async def _taken():
        await asyncio.sleep(0.9)
        sess.queued_targets.clear()

    async def scenario():
        update, _q = _query(_cb(bot, sess))
        side = asyncio.ensure_future(_taken())
        await bot._handle_callback(update, MagicMock())
        await side

    run_async(scenario())
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_a_fast_chord_tap_still_toasts_its_outcome(mk_bot, tmp_path,
                                                   run_async, monkeypatch):
    """A chord inside the wait is toasted as before, never as Sending."""
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    _slow_chord(monkeypatch, ctrl_x=0.5)
    _, toasts = _slow_tap(bot, sess, run_async)
    assert toasts == [sn.TOAST_SENT]
    assert sess.queued_lines == {}


def test_now_waits_for_a_slow_chord(mk_bot, tmp_path, run_async, monkeypatch,
                                    mk_update):
    """/now is a message reply, not a callback: it waits for the outcome."""
    bot, sess = _bot(mk_bot, tmp_path)
    _slow_chord(monkeypatch, ok=False)
    update = _cmd_update(mk_update)
    run_async(bot._handle_now_cmd(update, MagicMock()))
    assert _replies(update) == [sn.TOAST_FAILED]


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
    # The target is per chat (2026-09-30): this chat has none, and the
    # other chat's session is never typed into.
    assert _replies(update) == [sn.REPLY_NO_SESSION]
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


def test_now_deletes_every_line(mk_bot, tmp_path, run_async, keys,
                                mk_update):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    update = _cmd_update(mk_update)

    async def scenario():
        await bot._handle_now_cmd(update, MagicMock())
        await _settle()

    run_async(scenario())
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited()


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


# ── one line per queued message (live tests 2026-09-29) ─────────────────

def _two_held(mk_bot, tmp_path, *, two_held=True):
    """Messages 2 and 3 queued; 3 is always in Claude's queue, 2 only when
    *two_held*."""
    bot, sess = _bot(mk_bot, tmp_path, text="the second one")
    sess.queued_targets.append({"msg_id": 3, "chat_id": CHAT,
                                "raw_text": "the third one",
                                "driver_user_id": 12345})
    lines = [{"type": "queue-operation", "operation": "enqueue",
              "content": PREFIX + "the third one"}]
    if two_held:
        lines.insert(0, {"type": "queue-operation", "operation": "enqueue",
                         "content": PREFIX + "the second one"})
    with open(sess.transcript_path, "w") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in lines))
    return bot, sess


def test_each_queued_message_gets_its_own_line_at_once(
        mk_bot, tmp_path, run_async, instant):
    """One line per queued message (operator, 2026-09-29), not one per
    session."""
    bot, sess = _two_held(mk_bot, tmp_path)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()
        bot._arm_queued_lines(sess, [{"msg_id": 3}])
        await _settle()

    run_async(scenario())
    replies = [c.kwargs["reply_to_message_id"]
               for c in bot._app.bot.send_message.await_args_list]
    assert replies == [2, 3]
    assert sorted(sess.queued_lines) == [2, 3]


def test_a_message_without_a_queue_record_gets_no_line_the_other_does(
        mk_bot, tmp_path, run_async, instant):
    """2 is not in Claude's queue (no evidence): no line for it; 3 still
    gets its own."""
    bot, sess = _two_held(mk_bot, tmp_path, two_held=False)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}, {"msg_id": 3}])
        for _ in range(5):
            await _settle()

    run_async(scenario())
    replies = [c.kwargs["reply_to_message_id"]
               for c in bot._app.bot.send_message.await_args_list]
    assert replies == [3]


def test_a_press_after_the_line_was_armed_stops_its_send(
        mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        sess.mark_send_now_pressed()
        await _settle()

    run_async(scenario())
    bot._app.bot.send_message.assert_not_awaited()


def test_a_line_landing_after_a_press_is_dropped_at_once(
        mk_bot, tmp_path, run_async, instant):
    """The send waited for its token and Send now was pressed meanwhile:
    the line that lands is deleted, never left up."""
    bot, sess = _bot(mk_bot, tmp_path)

    async def _send(*_a, **_kw):
        sess.mark_send_now_pressed()
        return SimpleNamespace(message_id=7001)

    bot._app.bot.send_message = AsyncMock(side_effect=_send)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()

    run_async(scenario())
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.queued_lines == {}
    bot._app.bot.delete_message.assert_awaited()


def test_a_message_queued_after_a_press_still_gets_its_line(
        mk_bot, tmp_path, run_async, instant):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.mark_send_now_pressed()

    async def scenario():
        await asyncio.sleep(0.01)
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()

    run_async(scenario())
    bot._app.bot.send_message.assert_awaited_once()
    assert list(sess.queued_lines) == [2]


def test_a_line_dropped_as_it_lands_leaves_the_others(
        mk_bot, tmp_path, run_async, instant):
    """Line 2's send was out while Claude took 2: line 2 is dropped as it
    lands, line 3 (its own message still queued) stays."""
    bot, sess = _two_held(mk_bot, tmp_path)
    ids = iter([7001, 7002])

    async def _send(*_a, **kw):
        if kw.get("reply_to_message_id") == 2:
            with open(sess.transcript_path, "a") as fh:
                fh.write(json.dumps({
                    "type": "queue-operation", "operation": "remove",
                    "reason": "absorbed_mid_turn",
                    "content": PREFIX + "the second one"}) + "\n")
        return SimpleNamespace(message_id=next(ids))

    bot._app.bot.send_message = AsyncMock(side_effect=_send)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}, {"msg_id": 3}])
        for _ in range(5):
            await _settle()

    run_async(scenario())
    replies = [c.kwargs["reply_to_message_id"]
               for c in bot._app.bot.send_message.await_args_list]
    assert replies == [2, 3]
    assert list(sess.queued_lines) == [3]


def test_a_line_landing_beside_another_stays(
        mk_bot, tmp_path, run_async, instant):
    """Lines are per message: another message's line being up is no reason
    to drop this one."""
    bot, sess = _bot(mk_bot, tmp_path)

    async def _send(*_a, **_kw):
        sess.queued_lines[3] = (CHAT, 6000)  # another message's line
        return SimpleNamespace(message_id=7001)

    bot._app.bot.send_message = AsyncMock(side_effect=_send)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        await _settle()

    run_async(scenario())
    assert sorted(sess.queued_lines) == [2, 3]
    bot._app.bot.delete_message.assert_not_awaited()


def test_a_line_is_sent_while_another_is_up(mk_bot, tmp_path, run_async):
    """Per message: message 3's line does not stop message 2's."""
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[3] = (CHAT, 6000)
    run_async(bot._send_queued_line_checked(sess, 2))
    bot._app.bot.send_message.assert_awaited_once()


def test_a_message_queued_while_the_chord_is_out_still_gets_its_line(
        mk_bot, tmp_path, run_async, instant, monkeypatch, mk_update):
    """Review rev-iter2-001: the chord takes the queue as it stood (2); a
    message queued while the keys were going out (3) is still in Claude's
    queue afterwards and gets the line."""
    bot, sess = _bot(mk_bot, tmp_path)
    sess.queued_lines[2] = (CHAT, 7001)
    sess.queued_targets[0]["line_armed"] = True

    async def _run(args, stdin=b"", timeout=5):
        if stdin == b"\x13":
            with open(sess.transcript_path, "a") as fh:
                fh.write(json.dumps({"type": "queue-operation",
                                     "operation": "dequeue"}) + "\n")
                fh.write(json.dumps({"type": "queue-operation",
                                     "operation": "enqueue",
                                     "content": PREFIX + "sent meanwhile"})
                         + "\n")
            sess.queued_targets.append({"msg_id": 3, "chat_id": CHAT,
                                        "raw_text": "sent meanwhile",
                                        "driver_user_id": 12345})
            bot._arm_queued_lines(sess, [{"msg_id": 3}])
        return True, ""

    monkeypatch.setattr(inject, "_run", _run)
    update = _cmd_update(mk_update)

    async def scenario():
        await bot._handle_now_cmd(update, MagicMock())
        for _ in range(5):
            await _settle()

    run_async(scenario())
    replies = [c.kwargs["reply_to_message_id"]
               for c in bot._app.bot.send_message.await_args_list]
    assert replies == [3]
    assert list(sess.queued_lines) == [3]


# ── Telegram asking to slow down (review rev-iter1-003/008) ─────────────

@pytest.fixture
def instant_retry(monkeypatch):
    """The 429 retry's wait, made instant (never asyncio.sleep itself)."""
    waits: list[float] = []

    async def _now(seconds):
        waits.append(seconds)
    monkeypatch.setattr(sn, "_line_retry_sleep", _now)
    return waits


def test_a_small_429_on_the_line_is_waited_out_and_sent_again(
        mk_bot, tmp_path, run_async, instant, instant_retry):
    from telegram.error import RetryAfter
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.send_message = AsyncMock(
        side_effect=[RetryAfter(3), SimpleNamespace(message_id=7001)])

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert instant_retry == [3.0]
    assert sess.queued_lines == {2: (CHAT, 7001)}


def test_a_small_429_on_a_delete_is_waited_out_and_deleted_again(
        mk_bot, tmp_path, run_async, instant_retry):
    from telegram.error import RetryAfter
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_messages = AsyncMock(side_effect=[RetryAfter(2), True])

    async def scenario():
        bot._delete_queued_lines_later(CHAT, [7001, 7002])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert bot._app.bot.delete_messages.await_count == 2
    assert instant_retry == [2.0]
    assert bot.registry.queued_line_deletes == []


def test_a_delete_refused_twice_stays_owed(mk_bot, tmp_path, run_async,
                                           instant_retry):
    from telegram.error import RetryAfter
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_messages = AsyncMock(
        side_effect=[RetryAfter(2), RetryAfter(2)])

    async def scenario():
        bot._delete_queued_lines_later(CHAT, [7001, 7002])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert sorted(bot.registry.queued_line_deletes) == [[CHAT, 7001],
                                                        [CHAT, 7002]]


# A ban-sized 429, or a small one while the chat is muted, is not slept out
# and retried: the retry would fire at the ban's end (review rev-iter2-002).

BAN = 19289   # the 2026-09-11 incident's retry_after, over the 90 s ceiling


def _muting_429(seconds: int):
    """A call the gate answers the way it does a ban: mute armed, 429
    re-raised."""
    from telegram.error import RetryAfter

    async def _call(*_a, **_k):
        MUTE.mute(CHAT, 600)
        raise RetryAfter(seconds)
    return AsyncMock(side_effect=_call)


def test_a_ban_sized_429_on_the_line_gives_up_at_once(
        mk_bot, tmp_path, run_async, instant, instant_retry):
    from telegram.error import RetryAfter
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.send_message = AsyncMock(side_effect=[RetryAfter(BAN)])

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert (instant_retry, bot._app.bot.send_message.await_count,
            sess.queued_lines) == ([], 1, {})


def test_a_small_429_on_the_line_while_muted_gives_up_at_once(
        mk_bot, tmp_path, run_async, instant, instant_retry):
    bot, sess = _bot(mk_bot, tmp_path)
    bot._app.bot.send_message = _muting_429(3)

    async def scenario():
        bot._arm_queued_lines(sess, [{"msg_id": 2}])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert (instant_retry, bot._app.bot.send_message.await_count,
            sess.queued_lines) == ([], 1, {})


def test_a_ban_sized_429_on_a_delete_gives_up_at_once_and_stays_owed(
        mk_bot, tmp_path, run_async, instant_retry):
    from telegram.error import RetryAfter
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_messages = AsyncMock(side_effect=[RetryAfter(BAN)])

    async def scenario():
        bot._delete_queued_lines_later(CHAT, [7001, 7002])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert (instant_retry, bot._app.bot.delete_messages.await_count) == ([], 1)
    assert sorted(bot.registry.queued_line_deletes) == [[CHAT, 7001],
                                                        [CHAT, 7002]]


def test_a_small_429_on_a_delete_while_muted_gives_up_at_once(
        mk_bot, tmp_path, run_async, instant_retry):
    bot, _sess = _bot(mk_bot, tmp_path)
    bot._app.bot.delete_messages = _muting_429(2)

    async def scenario():
        bot._delete_queued_lines_later(CHAT, [7001, 7002])
        for _ in range(3):
            await _settle()

    run_async(scenario())
    assert (instant_retry, bot._app.bot.delete_messages.await_count) == ([], 1)
    assert sorted(bot.registry.queued_line_deletes) == [[CHAT, 7001],
                                                        [CHAT, 7002]]


# The queue watcher moves the card the moment Claude takes a message, but
# only while a turn runs: after a Stop the finish path owns the reply
# target (a popped turn keeps its own), live 2026-09-30.

def _watch_one_absorption(mk_bot, tmp_path, run_async, monkeypatch, status):
    bot, sess = _bot(mk_bot, tmp_path)
    sess.status = status
    sess.stream_transcript_path = sess.transcript_path
    sess.queued_lines[2] = (CHAT, 7001)
    sess.busy_msg_id = 500
    bot._app.bot.set_message_reaction = AsyncMock()
    with open(sess.transcript_path, "a") as fh:
        fh.write(json.dumps({"type": "queue-operation", "operation": "remove",
                             "reason": "absorbed_mid_turn",
                             "content": PREFIX + "and this too"}) + "\n")
    moves = []

    async def _spy(s):
        moves.append(s.name)
    monkeypatch.setattr(bot, "_move_card_now", _spy)

    async def _fast(_seconds):
        await asyncio.sleep(0)
    monkeypatch.setattr(sn, "_queue_watch_sleep", _fast)

    async def scenario():
        await asyncio.wait_for(bot._queue_watch(sess), timeout=5)
        await _settle()
    run_async(scenario())
    return bot, sess, moves


def test_the_watcher_moves_the_card_while_the_turn_runs(
        mk_bot, tmp_path, run_async, monkeypatch):
    bot, _sess, moves = _watch_one_absorption(
        mk_bot, tmp_path, run_async, monkeypatch, Status.BUSY)
    assert moves == [NAME]
    bot._app.bot.delete_message.assert_awaited()     # its line went too


def test_the_watcher_leaves_the_card_to_the_finish_after_a_stop(
        mk_bot, tmp_path, run_async, monkeypatch):
    bot, _sess, moves = _watch_one_absorption(
        mk_bot, tmp_path, run_async, monkeypatch, Status.IDLE)
    assert moves == []
    bot._app.bot.delete_message.assert_awaited()     # the line still goes


def _staged(sess, *mids):
    sess.stream_consumed_notes = [
        {"msg_id": m, "chat_id": CHAT, "raw_text": f"m{m}"} for m in mids]


def test_the_watchers_move_takes_nothing_once_the_turn_ended(
        mk_bot, tmp_path, run_async):
    """The move task was started while the turn ran but runs after the
    Stop: the finish path owns the staged notes and the target now."""
    bot, sess = _bot(mk_bot, tmp_path)
    sess.status = Status.IDLE
    sess.trigger_msg_id = 1
    sess.busy_msg_id, sess.busy_card_trigger = 500, 1
    _staged(sess, 2)
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._move_card_now(sess))

    assert [n["msg_id"] for n in sess.stream_consumed_notes] == [2]
    assert sess.trigger_msg_id == 1
    bot._reanchor_busy_card.assert_not_awaited()


def test_the_watchers_move_does_not_wait_for_the_thumbs_up(
        mk_bot, tmp_path, run_async):
    """The 👍 round trips go in the background: the card moves first
    (review rev-iter1-002), and the target is set before anything waits."""
    bot, sess = _bot(mk_bot, tmp_path)
    sess.trigger_msg_id = 1
    sess.busy_msg_id, sess.busy_card_trigger = 500, 1
    _staged(sess, 2, 3)
    order = []
    gate = asyncio.Event()

    async def _slow_reaction(*_a, **_k):
        order.append("reaction")
        await gate.wait()
    bot._app.bot.set_message_reaction = AsyncMock(side_effect=_slow_reaction)

    async def _move(s, target, *, final):
        order.append(("move", target, s.trigger_msg_id))
    bot._reanchor_busy_card = _move

    async def scenario():
        task = asyncio.create_task(bot._move_card_now(sess))
        await _settle()
        moved_while_the_thumbs_up_was_out = list(order)
        gate.set()
        await task
        await _settle()
        return moved_while_the_thumbs_up_was_out

    early = run_async(scenario())
    assert ("move", 3, 3) in early, early
    assert sess.stream_consumed_notes == []
