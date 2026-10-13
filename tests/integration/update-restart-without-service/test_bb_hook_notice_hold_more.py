"""Black box, iteration 3: the owner-chat holds added in fixes-3.md.

The contract (fixes-3.md, review-2#rev-iter2-001/002): with the owner's
private-chat session IDLE, the "hooks newer" notice is still held back
while, in the owner's private chat,

- a background agent is still running after an interim Stop (sent once,
  after the agent ends, without a new datagram);
- an answer is held for the owner's chat (a held answer for another chat
  does not hold it);
- a compaction is running;
- a tool call is in flight.

Each condition is reached the way production reaches it where a public
path exists: the agent starts and ends through real ``SubagentStart`` /
``SubagentStop`` datagrams fed to ``HookReceiver._on_datagram`` (as the
existing receiver tests do), and a tool call through ``PreToolUse`` /
``PostToolUse``; the held answer goes into the held buffer through
``HELD.hold`` and leaves through the bot's own ``flush_held_answers``.
Telegram's ``send_message`` is BBEnv's recorder. The notice is released
by ``TelegramBot.monitor_tick`` alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import _version
from aipager import preferences as prefs
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.dtach import hook_receiver as hr
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

from bb_helpers import BBEnv, markup_data, owner_id, run_scenario

RS = "_:up:rs"
GROUP = -100777002
SHORT = 0.05
AGENT = "a7c1d2e3f4a5b6c7d"
SESSION = "claude-web"


def _bump(v: str, minor=1) -> str:
    major, mi, _patch = (int(x) for x in v.split(".")[:3])
    return f"{major}.{mi + minor}.0"


NEWER = _bump(_version.__version__)


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    env = BBEnv(monkeypatch, tmp_path, mk_bot)
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", 600.0)
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", SHORT)
    HELD.clear()
    yield env
    HELD.clear()


def _notices(bb):
    return [m for m in bb.sent if RS in markup_data(m.get("reply_markup"))]


async def _ticks(bb, n=5, pause=0.01):
    for _ in range(n):
        await bb.bot.monitor_tick()
        await asyncio.sleep(pause)


async def _datagrams(bb, n=4):
    """n stamped hook datagrams, each followed by a tick."""
    for _ in range(n):
        bb.manager.note_hook_version(NEWER)
        await bb.drain(3)
        await _ticks(bb, 1)


async def _until_notice(bb, budget=2.0):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while not _notices(bb) and loop.time() - t0 < budget:
        await _ticks(bb, 1, pause=SHORT)
    await _ticks(bb, 5, pause=SHORT)


def _receiver(bb):
    return hr.HookReceiver(bb.registry, AsyncMock())


async def _hook(recv, **fields):
    fields.setdefault("session", SESSION)
    await recv._on_datagram(json.dumps(fields).encode())


def _scope_mode(bb):
    oid = owner_id()
    bb.bot.scopes = [
        Scope(chat_id=oid, kind="dm", label="alice DM",
              members=(Member(id=oid, label="alice", role="owner"),)),
        Scope(chat_id=GROUP, kind="group", label="team",
              members=(Member(id=oid, label="alice", role="owner"),)),
    ]
    bb.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                Path("/nonexistent/policy.d"))


# ----- a background agent running after an interim Stop --------------------------------------

async def _agent_after_interim_stop(bb, sess, recv):
    """The turn launched a background agent, then Stop fired (IDLE)."""
    sess.status = Status.BUSY
    await _hook(recv, hook_event_name="SubagentStart", agent_id=AGENT,
                agent_type="ship-developer")
    sess.status = Status.IDLE          # the interim Stop


def test_bg_agent_setup_leaves_the_session_idle(bb):
    """Guard against a vacuous hold: the session really is IDLE."""
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        await _agent_after_interim_stop(bb, sess, _receiver(bb))
        await _datagrams(bb, 2)
        return sess.status
    assert run_scenario(bb, body) == Status.IDLE


def test_bg_agent_setup_keeps_the_job_open(bb):
    """Guard: the SubagentStart datagram really opened background work."""
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        await _agent_after_interim_stop(bb, sess, _receiver(bb))
        await _ticks(bb, 3)
        return sess.job_background_open()
    assert run_scenario(bb, body) is True


def test_bg_agent_after_interim_stop_holds_the_notice(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        await _agent_after_interim_stop(bb, sess, _receiver(bb))
        await _datagrams(bb, 5)
        await _ticks(bb, 8, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_bg_agent_hold_is_logged_as_deferred(bb, caplog):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        with caplog.at_level(logging.DEBUG):
            await _agent_after_interim_stop(bb, sess, _receiver(bb))
            await _datagrams(bb, 3)
    run_scenario(bb, body)
    assert any("update.hooks_newer.notice" in r.getMessage()
               and "result=deferred" in r.getMessage() for r in caplog.records)


def test_bg_agent_end_releases_the_notice_without_a_new_datagram(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        recv = _receiver(bb)
        await _agent_after_interim_stop(bb, sess, recv)
        await _datagrams(bb, 6)        # more hold-backs than the 3 attempts
        await _hook(recv, hook_event_name="SubagentStop", agent_id=AGENT,
                    agent_type="ship-developer")
        await _until_notice(bb)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_bg_agent_end_notice_goes_to_the_owner_chat(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        recv = _receiver(bb)
        await _agent_after_interim_stop(bb, sess, recv)
        await _datagrams(bb, 3)
        await _hook(recv, hook_event_name="SubagentStop", agent_id=AGENT,
                    agent_type="ship-developer")
        await _until_notice(bb)
    run_scenario(bb, body)
    assert [m["chat_id"] for m in _notices(bb)] == [owner_id()]


def test_bg_agent_end_then_more_datagrams_sends_no_duplicate(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        recv = _receiver(bb)
        await _agent_after_interim_stop(bb, sess, recv)
        await _datagrams(bb, 3)
        await _hook(recv, hook_event_name="SubagentStop", agent_id=AGENT,
                    agent_type="ship-developer")
        await _until_notice(bb)
        await _datagrams(bb, 5)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_bg_agent_notice_is_sent_only_after_the_agent_ended(bb):
    """Every notice sent saw no background work open."""
    sess = bb.add_session(name=SESSION, status=Status.IDLE)
    seen: list = []
    orig = bb._on_send

    async def spy(*a, **kw):
        if RS in markup_data(kw.get("reply_markup")):
            seen.append(sess.job_background_open())
        return await orig(*a, **kw)
    bb.bot._app.bot.send_message = AsyncMock(side_effect=spy)

    async def body():
        recv = _receiver(bb)
        await _agent_after_interim_stop(bb, sess, recv)
        await _datagrams(bb, 4)
        await _hook(recv, hook_event_name="SubagentStop", agent_id=AGENT,
                    agent_type="ship-developer")
        await _until_notice(bb)
    run_scenario(bb, body)
    assert seen == [False]


def test_bg_agent_in_a_group_session_does_not_hold_the_notice(bb):
    _scope_mode(bb)
    sess = bb.add_session(name=SESSION, status=Status.IDLE, scope_chat_id=GROUP)

    async def body():
        await _agent_after_interim_stop(bb, sess, _receiver(bb))
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- a held answer --------------------------------------------------------------------------

def _hold_answer(chat_id, session=SESSION):
    HELD.hold(chat_id=chat_id, session=session, label="web",
              rich_text="the answer", plain_text="the answer")


def test_held_answer_in_the_owner_chat_holds_the_notice(bb):
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(owner_id())

    async def body():
        await _datagrams(bb, 5)
        await _ticks(bb, 8, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_held_answer_in_the_owner_chat_is_logged_as_deferred(bb, caplog):
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(owner_id())

    async def body():
        with caplog.at_level(logging.DEBUG):
            await _datagrams(bb, 3)
    run_scenario(bb, body)
    assert any("update.hooks_newer.notice" in r.getMessage()
               and "result=deferred" in r.getMessage() for r in caplog.records)


def test_held_answer_cleared_releases_the_notice_once(bb):
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(owner_id())

    async def body():
        await _datagrams(bb, 6)
        HELD.clear()
        await _until_notice(bb)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


async def _hold_through_the_mute_then_deliver(bb, sess, posts):
    """The real path: the turn ends while the owner's chat is muted, so
    its answer is held; the mute lifts and the bot's flush delivers it."""
    MUTE.mute(owner_id(), 600)
    await bb.bot.notify(sess, "idle_prompt", {"summary": "All done here."})
    held = HELD.count(owner_id())
    MUTE.clear()
    await _datagrams(bb, 4)            # held answer, chat no longer muted
    posts.append(("held-before-flush", held, len(_notices(bb))))
    await bb.bot.flush_held_answers(sess)
    posts.append(("flushed",))
    await _until_notice(bb)


@pytest.fixture
def answer_path(bb, monkeypatch):
    """A DM session whose answer goes out through Telegram's raw API
    (``rich_message._post``, the boundary), recorded in order with the
    notice."""
    events: list = []

    async def _post(method, payload, **_kw):
        events.append(("post", method))
        return {"ok": True, "result": {"message_id": 9100 + len(events)}}
    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    orig = bb._on_send

    async def spy(*a, **kw):
        if RS in markup_data(kw.get("reply_markup")):
            events.append(("notice",))
        return await orig(*a, **kw)
    bb.bot._app.bot.send_message = AsyncMock(side_effect=spy)
    sess = bb.add_session(name=SESSION, status=Status.IDLE, scope_kind="dm")
    sess.busy_started_at = time.monotonic() - 5
    sess.trigger_msg_id = 7
    prefs.set_preference(owner_id(), "layout", "card")
    yield sess, events
    MUTE.clear()


def test_held_answer_real_path_really_held_one_answer(bb, answer_path):
    """Guard: the muted turn end really left one answer held."""
    sess, events = answer_path
    run_scenario(bb, lambda: _hold_through_the_mute_then_deliver(bb, sess, events))
    assert [e for e in events if e[0] == "held-before-flush"][0][1] == 1


def test_held_answer_real_path_holds_the_notice_after_the_mute_lifts(bb, answer_path):
    sess, events = answer_path
    run_scenario(bb, lambda: _hold_through_the_mute_then_deliver(bb, sess, events))
    assert [e for e in events if e[0] == "held-before-flush"][0][2] == 0


def test_held_answer_delivered_by_the_bot_releases_the_notice_once(bb, answer_path):
    sess, events = answer_path
    run_scenario(bb, lambda: _hold_through_the_mute_then_deliver(bb, sess, events))
    assert len(_notices(bb)) == 1


def test_held_answer_is_delivered_before_the_notice(bb, answer_path):
    sess, events = answer_path
    run_scenario(bb, lambda: _hold_through_the_mute_then_deliver(bb, sess, events))
    order = [e[0] for e in events if e[0] in ("post", "notice", "flushed")]
    assert order.index("notice") > order.index("flushed")


def test_held_answer_in_another_chat_does_not_hold_the_notice(bb):
    _scope_mode(bb)
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(GROUP, session="claude-team")

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_held_answer_in_another_chat_is_still_held(bb):
    """Guard: the other chat's answer was really in the buffer."""
    _scope_mode(bb)
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(GROUP, session="claude-team")

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
        return HELD.count(GROUP)
    assert run_scenario(bb, body) == 1


def test_held_answer_in_another_chat_notice_goes_to_the_owner_chat(bb):
    _scope_mode(bb)
    bb.add_session(name=SESSION, status=Status.IDLE)
    _hold_answer(GROUP, session="claude-team")

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
    run_scenario(bb, body)
    assert [m["chat_id"] for m in _notices(bb)] == [owner_id()]


# ----- compacting -----------------------------------------------------------------------------

@pytest.mark.parametrize("card", [-1, 4242], ids=["card-being-sent", "card-shown"])
def test_compacting_in_the_owner_chat_holds_the_notice(bb, card):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)
    sess.push_compacting(card, time.monotonic(), deadline_seconds=180.0)

    async def body():
        await _datagrams(bb, 5)
        await _ticks(bb, 8, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_compacting_end_releases_the_notice_once(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)
    sess.push_compacting(-1, time.monotonic(), deadline_seconds=180.0)

    async def body():
        await _datagrams(bb, 6)
        sess.pop_compacting()
        await _until_notice(bb)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_compacting_in_a_group_session_does_not_hold_the_notice(bb):
    _scope_mode(bb)
    sess = bb.add_session(name=SESSION, status=Status.IDLE, scope_chat_id=GROUP)
    sess.push_compacting(-1, time.monotonic(), deadline_seconds=180.0)

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- a tool call in flight ------------------------------------------------------------------

async def _tool_started(bb, recv):
    await _hook(recv, hook_event_name="PreToolUse", tool_name="Bash",
                tool_input={"command": "make test"}, tool_use_id="toolu_bb1")


async def _tool_ended(bb, recv):
    await _hook(recv, hook_event_name="PostToolUse", tool_name="Bash",
                tool_input={"command": "make test"}, tool_use_id="toolu_bb1",
                tool_response={"stdout": "ok"})


def test_tool_in_flight_set_directly_holds_the_notice(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)
    sess.pending_tool_started_at = time.monotonic()

    async def body():
        await _datagrams(bb, 5)
        await _ticks(bb, 8, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


async def _tool_held_after_recovery(bb, sess, recv):
    """PreToolUse starts a long tool call (BUSY); the session monitor then
    recovers the session to IDLE while the tool still runs (seen live in
    iteration 2, tester-iter2-001)."""
    await _tool_started(bb, recv)
    sess.status = Status.IDLE


def test_tool_in_flight_via_pretooluse_holds_the_notice_on_an_idle_session(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        await _tool_held_after_recovery(bb, sess, _receiver(bb))
        await _datagrams(bb, 5)
        await _ticks(bb, 8, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_tool_in_flight_setup_is_idle_when_held(bb):
    """Guard: the hold above is not a BUSY status."""
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        await _tool_held_after_recovery(bb, sess, _receiver(bb))
        await _datagrams(bb, 3)
        return sess.status
    assert run_scenario(bb, body) == Status.IDLE


def test_tool_end_then_stop_releases_the_notice_once(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        recv = _receiver(bb)
        await _tool_held_after_recovery(bb, sess, recv)
        await _datagrams(bb, 6)
        await _tool_ended(bb, recv)
        sess.status = Status.IDLE      # the turn's Stop
        await _until_notice(bb)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_tool_in_flight_cleared_releases_the_notice_once(bb):
    sess = bb.add_session(name=SESSION, status=Status.IDLE)
    sess.pending_tool_started_at = time.monotonic()

    async def body():
        await _datagrams(bb, 6)
        sess.pending_tool_started_at = None
        await _until_notice(bb)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_tool_in_flight_in_a_group_session_does_not_hold_the_notice(bb):
    _scope_mode(bb)
    sess = bb.add_session(name=SESSION, status=Status.IDLE, scope_chat_id=GROUP)
    sess.pending_tool_started_at = time.monotonic()

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await _ticks(bb, 3)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- control: none of the conditions, sent at once ------------------------------------------

def test_idle_owner_chat_without_any_condition_sends_at_once(bb):
    bb.add_session(name=SESSION, status=Status.IDLE)

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1
