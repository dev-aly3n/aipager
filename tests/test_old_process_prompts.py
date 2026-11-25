"""A kill+relaunch under the same entry closes the old claude's prompts.

/new's Replace and a restart (``_kill_and_relaunch_core``: /restart, a
mode switch, a bring-back) keep the session's entry for the claude they
start in place of the old one. Before this, the old claude's prompts
stayed open on that entry: a tap on its question typed Down, Enter into
the fresh claude (found 2026-11-24 by a reproduction run), and a Replace
of a working session left the entry BUSY under its old Working card.
Once the old socket is confirmed gone every prompt closes and its own
message loses its buttons; Replace also ends the old turn as a Stop
does. When the kill fails the old claude and its dialogs live on, so
nothing is closed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.dtach import inject
from aipager.state import Status
from tests.card_tasks import drain_card_tasks
from tests.two_prompts_rig import ALICE, BG, CARD, GROUP, S, Rig, answers


def _draining(r):
    """Each of the rig's loops waits for the background steps it started
    (the old claude's buttons, card and reactions) before it ends."""
    run = r.run_async

    async def _then_drain(coro):
        out = await coro
        await drain_card_tasks()
        return out

    r.run_async = lambda coro: run(_then_drain(coro))
    return r


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    r = _draining(Rig(mk_bot, monkeypatch, run_async))
    r.bot._app.bot.edit_message_reply_markup = AsyncMock()
    r.launches = []

    async def _launch(short, **kw):
        r.launches.append(short)
        return True, ""

    async def _no_sleep(_s):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    return r


def _socket_stays(monkeypatch, rig):
    """The kill fails: the old socket is still there (and the launch beside
    it refuses, as ``launch_session`` does for a live name)."""
    real = Path.is_socket
    monkeypatch.setattr(Path, "is_socket",
                        lambda self: "claude-dtach-" in str(self) or real(self))

    async def _refused(short, **kw):
        rig.launches.append(short)
        return False, "already exists"

    monkeypatch.setattr(inject, "launch_session", _refused)


def _replace(rig):
    rig.bot._new_conflict_pending[S] = {"prompt": "", "mode": "ask", "user_id": ALICE,
                                        "msg_id": CARD + 500, "was_working": True}
    rig.tap(CARD + 500, "new_replace", uid=ALICE)


def _restart(rig, run_async):
    return rig.run_async(rig.bot._kill_and_relaunch_core(
        rig.sess, target_mode="ask", interrupt_first=False))


def _two_prompts(rig):
    """An agent's Bash prompt on the card and Claude's question as its own
    message."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()
    assert rig.sess.status == Status.INTERACTIVE
    (extra,) = rig.sess.extra_prompts
    return extra["msg_id"]


def _stripped(rig) -> list[int]:
    return [c.kwargs.get("message_id")
            for c in rig.bot._app.bot.edit_message_reply_markup.await_args_list
            if c.kwargs.get("reply_markup") is None]


# ── /new Replace ────────────────────────────────────────────────────────────────

def test_replace_closes_the_old_question_so_a_tap_types_nothing(rig):
    """The reproduction: Claude's question on the card, Replace, then a tap
    on its option. Mutation: the Replace step not run (``old_gone`` never
    set) and the tap types Down, Enter into the fresh claude."""
    rig.question()
    _replace(rig)
    assert rig.launches == [S.removeprefix("claude-")]
    rig.keys.clear()
    q = rig.tap(CARD, "opt1", uid=ALICE)
    assert rig.keys == []
    assert answers(q) == [str(ALREADY_ANSWERED)]
    assert rig.sess.pending_permission is None
    assert rig.sess.status == Status.IDLE


def test_replace_closes_every_prompt_and_strips_their_messages(rig):
    """Both prompts close: the card's agent prompt answers no hook and the
    question's own message loses its buttons. Mutation: the buttons not
    stripped (``_show_prompts_closed`` skipped)."""
    extra_id = _two_prompts(rig)
    _replace(rig)
    assert extra_id in _stripped(rig)
    rig.keys.clear()
    rig.tap(CARD, "allow", uid=ALICE)
    rig.tap(extra_id, "opt0", uid=ALICE)
    assert rig.keys == []
    assert rig.decisions == []
    assert rig.sess.open_prompts() == []
    assert rig.sess.extra_prompts == []


def test_replace_of_a_working_session_ends_its_turn(rig):
    """A working session replaced: the entry is idle for the fresh claude,
    its animation stops and the card says Stopped with no buttons, the job
    and the reply target are gone. Mutations: the IDLE transition removed
    (BUSY stays), or the card not settled."""
    rig.hook("UserPromptSubmit", prompt="x")
    assert rig.sess.status == Status.BUSY
    rig.card_edits.clear()
    _replace(rig)
    sess = rig.sess
    assert sess.status == Status.IDLE
    rig.bot._stop_animation.assert_called()
    ((mid, text, markup),) = [e for e in rig.card_edits if e[0] == CARD]
    assert "Stopped" in text and markup is None
    assert sess.busy_msg_id is None
    assert sess.trigger_msg_id is None
    assert sess.active_subagents == {}


def test_replace_whose_kill_fails_closes_nothing(rig, monkeypatch):
    """The old claude outlived the kill: its question is still on screen,
    so it stays open (the launch refuses beside it). Mutation: the step run
    whatever the socket wait found."""
    rig.question()
    _socket_stays(monkeypatch, rig)
    rig.card_edits.clear()
    _replace(rig)
    assert rig.sess.status == Status.INTERACTIVE
    assert rig.sess.pending_permission is not None
    assert [e for e in rig.card_edits if e[0] == CARD] == []


# ── restart (kill and relaunch in place) ────────────────────────────────────────

def test_a_restart_closes_the_old_prompts_before_the_relaunch(rig, run_async):
    """Mutation: the close step removed from ``_kill_and_relaunch_core``
    (a tap on the old question types into the relaunched claude)."""
    extra_id = _two_prompts(rig)
    out = _restart(rig, run_async)
    assert out.ok
    assert extra_id in _stripped(rig)
    rig.keys.clear()
    rig.tap(CARD, "allow", uid=ALICE)
    rig.tap(extra_id, "opt1", uid=ALICE)
    assert rig.keys == []
    assert rig.decisions == []
    assert rig.sess.pending_permission is None


def test_a_restart_whose_old_claude_survives_keeps_its_prompts(rig, run_async, monkeypatch):
    """still_stopping: the old claude and its dialog live on, so the
    prompt stays answerable."""
    rig.question()
    _socket_stays(monkeypatch, rig)
    out = _restart(rig, run_async)
    assert (out.ok, out.reason) == (False, "still_stopping")
    assert rig.sess.pending_permission is not None
    assert rig.sess.status == Status.INTERACTIVE


def test_replace_with_no_card_closes_the_prompt_sent_as_its_own_message(
        mk_bot, monkeypatch, run_async):
    """No card yet: Claude's question went out as its own message (the
    primary prompt, not the card's). Replace closes it too: no prompt is
    left on the entry and its message loses its buttons."""
    r = _draining(Rig(mk_bot, monkeypatch, run_async, card=False))
    r.bot._app.bot.edit_message_reply_markup = AsyncMock()

    async def _launch(short, **kw):
        return True, ""

    async def _no_sleep(_s):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    r.question()
    (msg,) = r.prompt_messages()
    _replace(r)
    assert r.sess.pending_permission is None
    assert r.sess.pending_prompt_msg is None
    assert msg["msg_id"] in _stripped(r)
    r.keys.clear()
    r.tap(msg["msg_id"], "opt0", uid=ALICE)
    assert r.keys == []


# ── nothing waits on Telegram between the kill and the launch ───────────────────

def _held_edits(rig):
    """Every button edit waits until released (a slow chat's pace)."""
    gate = {}

    async def _slow(**kw):
        await gate["event"].wait()

    rig.bot._app.bot.edit_message_reply_markup = AsyncMock(side_effect=_slow)
    return gate


def test_a_slow_chat_does_not_hold_the_replace_launch(rig):
    """The old prompts' button edits wait for the chat: the fresh claude is
    launched anyway (the prompts are closed before it, the buttons go
    after). Mutation: the strip awaited before the launch (the launch never
    comes while the edit is held)."""
    extra_id = _two_prompts(rig)
    gate = _held_edits(rig)
    seen = {}

    async def go():
        gate["event"] = asyncio.Event()
        rig.bot._new_conflict_pending[S] = {"prompt": "", "mode": "ask", "user_id": ALICE,
                                            "msg_id": CARD + 500, "was_working": True}
        tap = asyncio.ensure_future(_tap_coro(rig, CARD + 500, "new_replace"))
        for _ in range(50):
            await asyncio.sleep(0)
        seen["launched_while_held"] = list(rig.launches)
        seen["closed"] = rig.sess.open_prompts() == []
        gate["event"].set()
        await tap
        await drain_card_tasks()

    rig.run_async(go())
    assert seen["launched_while_held"] == [S.removeprefix("claude-")]
    assert seen["closed"] is True
    assert extra_id in [c.kwargs.get("message_id") for c in
                        rig.bot._app.bot.edit_message_reply_markup.await_args_list]


def test_a_slow_chat_does_not_hold_the_relaunch(rig):
    """The same for a restart: its quiet window is short, and the monitor
    would call the session ended if the relaunch waited for the chat."""
    _two_prompts(rig)
    gate = _held_edits(rig)
    seen = {}

    async def go():
        gate["event"] = asyncio.Event()
        task = asyncio.ensure_future(rig.bot._kill_and_relaunch_core(
            rig.sess, target_mode="ask", interrupt_first=False))
        for _ in range(50):
            await asyncio.sleep(0)
        seen["done_while_held"] = task.done() and task.result().ok
        seen["launches"] = list(rig.launches)
        gate["event"].set()
        await task
        await drain_card_tasks()

    rig.run_async(go())
    assert seen["done_while_held"] is True
    assert seen["launches"] == [S.removeprefix("claude-")]


def test_replace_drops_the_messages_held_for_the_old_claude(rig):
    """Messages held for the old claude's dialog were for the old
    conversation: Replace drops them as a Stop does (not delivered, the
    "answer it first" notices deleted), so none reaches the fresh claude
    ahead of what /new was for. Mutation: the queue not cleared."""
    rig.question()
    rig.sess.pending_queue.append(("old message", 321, "", None, ALICE))
    rig.sess.dialog_hold = {"notices": [(GROUP, 333)]}
    rig.bot._mark_not_delivered = AsyncMock()
    rig.bot._app.bot.delete_message = AsyncMock()
    _replace(rig)
    assert rig.sess.pending_queue == []
    assert rig.sess.dialog_hold is None
    rig.bot._mark_not_delivered.assert_awaited_once()
    assert rig.bot._mark_not_delivered.await_args.args[1] == [{"msg_id": 321}]
    rig.bot._app.bot.delete_message.assert_awaited_once_with(chat_id=GROUP, message_id=333)


async def _tap_coro(rig, msg_id, verb, uid=ALICE):
    from unittest.mock import MagicMock
    q = MagicMock()
    q.data = f"{S}:{verb}"
    q.from_user = MagicMock(id=uid)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=GROUP)
    q.message.chat_id = GROUP
    q.message.message_id = msg_id
    q.message.text = ""
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    await rig.bot._handle_callback(u, MagicMock())


# ── the background settle against the fresh claude's first turn ─────────────────

def test_the_settle_waits_for_the_old_card_and_spares_the_fresh_turn(
        mk_bot, monkeypatch, run_async):
    """A slow chat: the old turn's card is still being sent (it holds the
    card lock) when Replace runs and the fresh claude starts its first
    turn. The settle waits for that send, marks THAT card Stopped, and
    closes only the replaced turn: the fresh turn still gets its own card.
    Mutations: the settle not under ``animate_lock`` (the old card lands
    after it and is never settled), or ``close_turn()`` with no turn (the
    fresh turn gets no card)."""
    import json

    r = Rig(mk_bot, monkeypatch, run_async, card=False)
    r.bot._app.bot.edit_message_reply_markup = AsyncMock()

    async def _launch(short, **kw):
        return True, ""

    async def _no_sleep(_s):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    sess, bot = r.sess, r.bot
    gate = {}
    ids = iter(range(700, 800))
    card_sends: list[int] = []

    async def _send(chat_id=None, text=None, **kwargs):
        msg_id = next(ids)
        if not card_sends and "first" in gate:
            await gate["first"].wait()
        card_sends.append(msg_id)
        return type("M", (), {"message_id": msg_id})()

    bot._app.bot.send_message = AsyncMock(side_effect=_send)
    seen = {}

    async def go():
        gate["first"] = asyncio.Event()
        old_turn = sess.turn_seq
        old_card = asyncio.ensure_future(bot._send_busy_and_animate(sess, turn=old_turn))
        for _ in range(5):
            await asyncio.sleep(0)
        seen["claimed"] = sess.busy_msg_id
        bot._new_conflict_pending[S] = {"prompt": "", "mode": "ask", "user_id": ALICE,
                                        "msg_id": CARD + 500, "was_working": True}
        await _tap_coro(r, CARD + 500, "new_replace")
        # The fresh claude's first turn begins while the old card still waits.
        # (Its own hooks wait for its card state, which waits behind the
        # old card's lock, so the datagram is handled as its own task.)
        r.recv._recent_fingerprints.clear()
        hook = asyncio.ensure_future(r.recv._on_datagram(json.dumps({
            "session": S, "hook_event_name": "UserPromptSubmit", "prompt": "hello",
            "transcript_path": "", "_seq": 999}).encode()))
        for _ in range(5):
            await asyncio.sleep(0)
        seen["fresh_turn"] = (old_turn, sess.turn_seq)
        gate["first"].set()
        await old_card
        await asyncio.wait_for(hook, 5)
        await drain_card_tasks()
        for _ in range(20):
            await asyncio.sleep(0)
        await drain_card_tasks()

    run_async(asyncio.wait_for(go(), 10))
    assert seen["claimed"] == -1
    assert seen["fresh_turn"][1] > seen["fresh_turn"][0]
    old_id = card_sends[0]
    assert [t for mid, t, _m in r.card_edits if mid == old_id and "Stopped" in t]
    # The fresh turn's own card went out after the settle and is live.
    assert len(card_sends) == 2
    assert sess.busy_msg_id == card_sends[1]
    assert sess.status == Status.BUSY
