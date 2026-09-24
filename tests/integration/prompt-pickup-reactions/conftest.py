"""Fixtures for the reaction-lifecycle scenarios (👀 handed off → 👍 taken,
🤷 never delivered).

The dtach layer and the Telegram HTTP boundary are mocked; every scenario
asserts on what reached ``bot._app.bot.set_message_reaction``. Helpers are
fixtures, not imports: this directory's name is not a Python identifier
(see ``tests/integration/turn-anchor-follows-consumption/conftest.py``).
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

CHAT_ID = -3003
NAME = "claude-x"
LABEL = "x"
SID = "5f1c0b6e-0000-4000-8000-000000000000"


@pytest.fixture
def wired(mk_bot, monkeypatch, tmp_path):
    """A bot with a live-looking session ``claude-x`` whose busy card and
    re-anchor paths are real; pty writes and keys are captured, not sent.

    Returns ``(bot, sess, injected, keys)``."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    counter = {"n": 50_000}

    async def _send_message(*_a, **_kw):
        counter["n"] += 1
        return SimpleNamespace(message_id=counter["n"])

    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.set_message_reaction = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()

    injected: list[str] = []
    keys: list[str] = []

    async def _send_text_and_enter(_name, body):
        injected.append(body)
        return True

    async def _send_keys(_name, key):
        keys.append(key)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        _send_text_and_enter)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))

    def _fake_start_animation(target_sess):
        target_sess.animate_task = asyncio.create_task(asyncio.sleep(999))

    bot._start_animation = MagicMock(side_effect=_fake_start_animation)

    sess = bot.registry.get_or_create(NAME)
    sess.label = LABEL
    sess.scope_chat_id = CHAT_ID
    sess.scope_kind = "dm"
    bot.registry.last_active_session = NAME

    # Named by Claude Code's session id, as real transcripts are: the hook
    # receiver stamps ``claude_session_id`` from this file's stem.
    transcript = tmp_path / f"{SID}.jsonl"
    transcript.write_bytes(b"")
    sess.transcript_path = str(transcript)
    sess.claude_session_id = SID
    return bot, sess, injected, keys


@pytest.fixture
def rich_calls(monkeypatch):
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


@pytest.fixture
def send_text(mk_update):
    """Send a Telegram text message through the real handler."""
    async def _send(bot, text, message_id, user_id=12345):
        update = mk_update(text, message_id=message_id, user_id=user_id,
                           chat_id=CHAT_ID)
        await bot._handle_message(update, MagicMock())
        return update
    return _send


@pytest.fixture
def pickup():
    """What ``aipager-hook`` does on ``UserPromptSubmit`` for a Telegram
    message: delete the message's note (``_match_and_promote``) and send
    the daemon a ``queue_pickup`` datagram naming it."""
    async def _pickup(bot, sess, msg_id):
        from aipager.policy_snapshot import delete_notes, list_outstanding_notes
        notes = [n for n in list_outstanding_notes(sess.name)
                 if n.get("msg_id") == msg_id]
        delete_notes(sess.name, notes)
        wire = [{"msg_id": n.get("msg_id"), "chat_id": n.get("chat_id"),
                 "raw_text": n.get("raw_text", "")} for n in notes]
        await bot.notify(sess, "queue_pickup", {"consumed": wire,
                                                "expired": []})
        return notes
    return _pickup


@pytest.fixture
def append_queue_op():
    """Append a transcript ``queue-operation`` line, as Claude Code writes
    it, to the session's pinned transcript."""
    def _append(sess, operation, reason, content):
        line = {"type": "queue-operation", "operation": operation,
                "content": content, "timestamp": time.time()}
        if reason is not None:
            line["reason"] = reason
        with open(sess.stream_transcript_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    return _append


@pytest.fixture
def reactions():
    """``reactions(bot)`` -> ``{msg_id: [emoji, ...]}`` in call order."""
    def _collect(bot):
        out: dict[int, list[str]] = {}
        for call in bot._app.bot.set_message_reaction.await_args_list:
            out.setdefault(call.args[1], []).append(call.args[2])
        return out
    return _collect
