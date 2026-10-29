"""Roadmap 8.121 F5 (design-amend-5 B, design-6): Stop never types Escape
into a session whose own turn has ended.

Live, a Stop on a session waiting for its background agents typed two
Escapes into Claude Code's idle prompt, which opened its Rewind menu, and
the next Telegram message was typed into that menu and lost. Every Stop
path reaches the Escapes through one helper (``_send_stop_keys``, called by
``_stop_session_core``), which types none while Claude's own turn is over.
Since design-6 such a Stop presses Claude Code's kill chord instead
(exactly two writes, no Escape) when the job's agents run, and types
nothing at all with only the grace window or the job's shells left.

Each entry point is driven the way its user drives it: the busy card's
Stop button, /status's Stop button, the Mini App's stop route over HTTP
with signed initData, and the /stop command. The session gets there
through the real ``HookReceiver``: a Telegram prompt, a background agent
starts, Claude's turn stops (IDLE, waiting), or not yet (BUSY, the
control, which still gets today's two Escapes). The keys are read on the
dtach seam (``inject.send_keys``, ``inject.discard_queued_input``); no
real dtach is reached.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.dtach import hook_receiver as hr
from aipager.miniapp.server import MiniAppServer
from aipager.state import Status

# The same values as this directory's conftest (which cannot be imported).
CHAT = -100
ADMIN_ID = 555
SESSION = "frontend"
AGENT = "b7c8d9e0f1a2"
PROMPT = "[via Telegram msg=3421]\nsurvey the docs in the background"


@pytest.fixture
def keys(monkeypatch) -> list[str]:
    """Every key Stop types, in order (the dtach seam, never a socket)."""
    typed: list[str] = []

    async def send_keys(name, key):
        typed.append(key)
        return True

    async def discard(name, chars=0):
        typed.append("discard")
        return True

    async def run(args, stdin=b"", timeout=5):
        # Only the kill chord reaches the dtach seam here (send_keys and the
        # discard are recorded above by name).
        typed.append("KillAgents" if stdin == b"\x18\x0b" else repr(stdin))
        return True, ""

    async def gap(_delay):
        return None

    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", discard)
    monkeypatch.setattr("aipager.dtach.inject._run", run)
    monkeypatch.setattr("aipager.dtach.inject._key_gap_sleep", gap)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    return typed


async def _hook(recv, **fields):
    await recv._on_datagram(json.dumps({"session": SESSION, **fields}).encode())


async def _bring_up(bot, recv, *, turn_ended: bool):
    """A prompt from the chat, a background agent, and (when *turn_ended*)
    Claude's own Stop while the agent runs."""
    await _hook(recv, hook_event_name="UserPromptSubmit", prompt=PROMPT)
    sess = bot.registry.get(SESSION)
    sess.label = SESSION
    sess.scope_chat_id, sess.scope_kind = CHAT, "group"
    sess.cwd = "/home/alice/frontend"
    await _hook(recv, hook_event_name="SubagentStart", agent_id=AGENT,
                agent_type="Explore")
    if turn_ended:
        await _hook(recv, hook_event_name="Stop",
                    last_assistant_message="Started a survey in the background.")
    return sess


def _query(data: str, message_id: int):
    query = MagicMock()
    query.data = data
    query.message = MagicMock(text="card", message_id=message_id)
    query.message.chat = MagicMock(id=CHAT, type="supergroup")
    query.message.edit_text = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=ADMIN_ID, username="alice", first_name="Alice")
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT, type="supergroup")
    update.effective_user = query.from_user
    update.effective_message = SimpleNamespace(sender_chat=None, chat=query.message.chat)
    update.message = None
    return update


async def _card_stop(bot, sess, ctx):
    """The Stop button on the busy card."""
    await bot._handle_callback(_query(f"{sess.name}:stop", sess.busy_msg_id), MagicMock())


async def _status_stop(bot, sess, ctx):
    """The Stop button on /status's row (keyed by the turn it was shown for)."""
    await bot._handle_callback(_query(f"{sess.name}:ststop{sess.turn_key}", 777),
                               MagicMock())


async def _miniapp_stop(bot, sess, ctx):
    """``POST /api/sessions/{label}/stop`` with the admin's signed initData."""
    client = TestClient(TestServer(ctx["server"]._build_app()))
    await client.start_server()
    try:
        resp = await client.post(f"/api/sessions/{SESSION}/stop",
                                 headers=ctx["init_header"]())
        assert resp.status == 200, await resp.text()
    finally:
        await client.close()


async def _stop_command(bot, sess, ctx):
    """``/stop frontend`` typed in the group."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = f"/stop {SESSION}"
    update.message.message_id = 990
    update.message.reply_to_message = None
    update.message.quote = None
    update.message.sender_chat = None
    update.message.reply_text = AsyncMock()
    update.message.chat = MagicMock(id=CHAT, type="supergroup")
    update.effective_message = update.message
    update.effective_chat = MagicMock(id=CHAT, type="supergroup")
    update.effective_user = MagicMock(id=ADMIN_ID, username="alice", first_name="Alice")
    update.callback_query = None
    await bot._handle_stop_cmd(update, MagicMock())


ENTRY_POINTS = {
    "card": _card_stop,
    "status": _status_stop,
    "miniapp": _miniapp_stop,
    "command": _stop_command,
}


def _run_stop(bot, run_async, init_header, entry, *, turn_ended: bool):
    ctx = {"server": MiniAppServer(bot, bot.registry, port=8766),
           "init_header": init_header}
    recv = hr.HookReceiver(bot.registry, bot.notify)
    seen: dict = {}

    async def drive():
        sess = await _bring_up(bot, recv, turn_ended=turn_ended)
        seen["before"] = (sess.status, sess.shown_state().state)
        await ENTRY_POINTS[entry](bot, sess, ctx)
        seen["after"] = (sess.status, sess.shown_state().state,
                         dict(sess.active_subagents), sess.busy_msg_id)

    run_async(drive())
    return seen


@pytest.mark.parametrize("entry", list(ENTRY_POINTS))
def test_stop_on_a_session_whose_turn_ended_presses_the_chord_and_no_escape(
        bot, run_async, init_header, keys, entry):
    seen = _run_stop(bot, run_async, init_header, entry, turn_ended=True)
    assert seen["before"] == (Status.IDLE, "tasks")
    assert keys == ["KillAgents", "KillAgents"]
    # The job is over, its agents gone, the card retired.
    assert seen["after"] == (Status.IDLE, "idle", {}, None)


@pytest.mark.parametrize("left", ["grace", "shells"])
@pytest.mark.parametrize("entry", list(ENTRY_POINTS))
def test_stop_with_only_grace_or_shells_left_types_nothing(bot, run_async, init_header,
                                                           keys, entry, left):
    import time as _time

    ctx = {"server": MiniAppServer(bot, bot.registry, port=8766),
           "init_header": init_header}
    recv = hr.HookReceiver(bot.registry, bot.notify)
    seen: dict = {}

    async def drive():
        sess = await _bring_up(bot, recv, turn_ended=True)
        sess.active_subagents.clear()
        sess.bg_agents.clear()
        if left == "grace":
            sess.job_grace_until = _time.monotonic() + 30.0
        else:
            sess.bg_shells["s1"] = {"label": "npm run dev", "in_job": True,
                                    "started_at": _time.monotonic()}
        assert sess.can_be_stopped()
        await ENTRY_POINTS[entry](bot, sess, ctx)
        seen["after"] = (sess.status, sess.job_background_open(), sess.busy_msg_id)

    run_async(drive())
    assert keys == []
    assert seen["after"] == (Status.IDLE, False, None)


@pytest.mark.parametrize("entry", list(ENTRY_POINTS))
def test_stop_on_a_working_session_still_types_two_escapes(bot, run_async, init_header,
                                                          keys, entry):
    """Control: a running turn is interrupted exactly as before."""
    seen = _run_stop(bot, run_async, init_header, entry, turn_ended=False)
    assert seen["before"] == (Status.BUSY, "busy")
    assert keys == ["Escape", "Escape"]
    assert seen["after"] == (Status.IDLE, "idle", {}, None)


def test_the_skip_is_logged(bot, run_async, init_header, keys, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="aipager.bot.session_ops"):
        _run_stop(bot, run_async, init_header, "card", turn_ended=True)
    assert any("Stop typed no keys" in r.getMessage() for r in caplog.records)


def test_stop_on_a_session_that_needs_you_still_types_two_escapes(bot, run_async, keys):
    """Control: a prompt open on a running turn (INTERACTIVE) is
    interrupted exactly as before; only an ended turn gets no keys."""
    recv = hr.HookReceiver(bot.registry, bot.notify)
    seen: dict = {}

    async def drive():
        sess = await _bring_up(bot, recv, turn_ended=False)
        bot.registry.transition(sess.name, Status.INTERACTIVE)
        seen["before"] = sess.status
        outcome = await bot._stop_session_core(sess)
        seen["ok"] = outcome.ok

    run_async(drive())
    assert (seen["before"], seen["ok"]) == (Status.INTERACTIVE, True)
    assert keys == ["Escape", "Escape"]
