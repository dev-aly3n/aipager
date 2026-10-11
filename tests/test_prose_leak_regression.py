"""A background agent's prose is never shown as the parent's (2026-10-10).

The parent's own hook events carry no ``agent_id``. A background agent
keeps running after the card that launched it lost track of it: a new
turn's card reset, /stop and the silence sweep each clear
``active_subagents`` while the agent works on. Its next MessageDisplay
(with its ``agent_id``) used to be forwarded as ``assistant_text`` and
appended to the CURRENT turn's commentary: on the live card, in the Mini
App timeline and in the turn's log, as if the parent had said it.

End to end: the real hook receiver feeding the real ``bot.notify``, each
of the three ways the agent leaves ``active_subagents``, then its prose.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.animation import build_stream_card_ex
from aipager.dtach import hook_receiver as hr
from aipager.miniapp.sessions import build_timeline
from aipager.state import Status

PROSE = "Writing final.md for the pipeline"
PARENT = "The parent's own words."


def _sess(bot):
    s = bot.registry.get_or_create("claude-leak")
    s.label = "leak"
    s.status = Status.BUSY
    s.scope_chat_id = 555
    s.scope_kind = "dm"
    s.busy_started_at = time.monotonic() - 5
    return s


def _hook(run_async, recv, sess, event, **fields):
    recv._recent_fingerprints.clear()
    run_async(asyncio.wait_for(recv._on_datagram(json.dumps(dict(
        {"session": sess.name, "hook_event_name": event, "transcript_path": ""},
        **fields)).encode()), timeout=5))


def _launch(run_async, recv, sess):
    _hook(run_async, recv, sess, "SubagentStart", agent_id="bg1",
          agent_type="pipeline-runner")
    assert "bg1" in sess.active_subagents and "bg1" in sess.bg_agents


def _quiet(bot):
    async def _none(*_a, **_kw):
        return None
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._maybe_update_bot_name = _none


def _by_card_reset(run_async, bot, sess, monkeypatch):
    """A new turn's card: the turn-start reset clears active_subagents."""
    sess.busy_msg_id = None
    bot._open_busy_card = AsyncMock(return_value=0)
    run_async(asyncio.wait_for(bot._send_busy_and_animate(sess), timeout=5))
    bot._stop_animation(sess)


def _by_stop(run_async, bot, sess, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    run_async(asyncio.wait_for(bot._stop_session_core(sess), timeout=5))
    sess.status = Status.BUSY            # a next turn is running


def _by_silence_sweep(run_async, bot, sess, monkeypatch):
    from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS, SessionMonitor

    sess.active_subagents["bg1"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60

    async def _names():
        return [sess.name]

    async def _notify(*_a, **_kw):
        return None

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)
    run_async(asyncio.wait_for(SessionMonitor(bot.registry, _notify)._scan(), timeout=5))


WAYS = {"card-reset": _by_card_reset, "stop": _by_stop, "silence-sweep": _by_silence_sweep}


@pytest.mark.parametrize("way", list(WAYS), ids=list(WAYS))
def test_a_detached_agents_prose_never_reaches_the_parents_commentary(
    mk_bot, run_async, monkeypatch, way,
):
    bot = mk_bot()
    _quiet(bot)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    sess = _sess(bot)
    _launch(run_async, recv, sess)
    WAYS[way](run_async, bot, sess, monkeypatch)
    assert "bg1" not in sess.active_subagents
    assert "bg1" in sess.bg_agents                  # it is still running
    sess.status = Status.BUSY
    commentary = list(sess.stream_commentary)
    timeline = build_timeline(sess)

    _hook(run_async, recv, sess, "MessageDisplay", agent_id="bg1", message_id="m9",
          index=0, final=False, delta=PROSE)

    assert sess.stream_commentary == commentary
    assert build_timeline(sess) == timeline
    card, _hid = build_stream_card_ex(sess, "Working")
    assert PROSE not in card
    assert sess.bg_agents["bg1"]["prose"] == PROSE   # it is that agent's


def test_the_parents_own_prose_still_reaches_its_commentary(mk_bot, run_async):
    """The guard is the agent_id, nothing else: the parent's prose (no
    agent_id) while a background agent runs is the commentary."""
    bot = mk_bot()
    _quiet(bot)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    sess = _sess(bot)
    _launch(run_async, recv, sess)
    _hook(run_async, recv, sess, "MessageDisplay", message_id="p1", index=0,
          final=False, delta=PARENT)
    assert any(PARENT in text for _a, text in sess.stream_commentary)
    assert sess.bg_agents["bg1"]["prose"] == ""
