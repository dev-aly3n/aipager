"""Shared fixtures for the "turn anchor follows consumption" black-box
integration tests (design.md / entrypoints.md).

Independent of the Developer's own adapted unit tests
(``tests/test_bot_notify.py``, ``tests/test_bot_animation.py``,
``tests/test_transcript_queue_operations.py``,
``tests/test_policy_snapshot_consume_matching.py``) — this suite drives
only the surface documented in ``entrypoints.md``: ``TelegramBot``
handler methods and ``notify()``, with the dtach layer and the Telegram
HTTP boundary mocked, asserting on what reached the mocked
``send_message``/``delete_message``/``set_message_reaction`` calls, the
``rich_message._post`` seam, and ``TrackedSession`` state.

Note: this directory's name (``turn-anchor-follows-consumption``) is not
a valid Python identifier, so test modules here cannot ``from .conftest
import ...`` — pytest still auto-discovers this file as a conftest
regardless. Shared constants are therefore redefined locally in each
test module, matching the convention already used by
``tests/integration/queue-handoff/``.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

CHAT_ID = -2002
NAME = "claude-x"
LABEL = "x"


@pytest.fixture
def wired(mk_bot, monkeypatch, tmp_path):
    """A bot wired for a REAL (unmocked) ``_send_busy_and_animate`` /
    ``send_busy`` / re-anchor path, unlike ``queue-handoff``'s ``wired``
    fixture (which stubs ``_send_busy_and_animate`` out entirely — fine
    for testing injection/queueing, useless for testing what a turn's
    card/answer actually gets sent under).

    ``_start_animation`` is stubbed to plant a genuinely PENDING
    (never-completing) task on ``sess.animate_task`` rather than the
    real ``_animate_busy`` loop — this suite drives ticks explicitly via
    ``bot.notify(sess, "assistant_text", …)`` rather than relying on a
    live spinner loop, exactly as design.md's R4 says absorption
    detection is observable. A bare no-op stub (never touching
    ``animate_task`` at all) would leave it ``None`` forever, which
    defeats ``_send_busy_and_animate``'s OWN "already showing busy"
    guard (``sess.animate_task and not sess.animate_task.done()``) —
    every subsequent message while BUSY would then look like a dead
    animation and send a SECOND fresh card, exactly the double-send
    this whole feature is trying to eliminate. Matches
    ``tests/test_bot_animation.py``'s own established pattern
    (``sess.animate_task = loop.create_task(_long())``) for exercising
    that guard without a real ticking loop.

    ``_app.bot.send_message`` returns a fresh, strictly increasing
    ``message_id`` on every call so a test can tell which send produced
    which card without hand-wiring return values.
    """
    bot = mk_bot()
    bot._app.bot = AsyncMock()

    _next_id = {"n": 10_000}

    async def _send_message(*_a, **_kw):
        _next_id["n"] += 1
        return SimpleNamespace(message_id=_next_id["n"])

    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.set_message_reaction = AsyncMock()
    bot._app.bot.send_chat_action = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()

    injected: list[str] = []

    async def _send_text_and_enter(_name, body):
        injected.append(body)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        _send_text_and_enter)

    def _fake_start_animation(target_sess):
        target_sess.animate_task = asyncio.create_task(asyncio.sleep(999))

    bot._start_animation = MagicMock(side_effect=_fake_start_animation)

    sess = bot.registry.get_or_create(NAME)
    sess.label = LABEL
    sess.scope_chat_id = CHAT_ID
    sess.scope_kind = "dm"
    bot.registry.last_active_session = NAME

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    sess.transcript_path = str(transcript)

    return bot, sess, injected


@pytest.fixture
def rich_calls(monkeypatch):
    """Capture every raw HTTP call (method, payload) made through
    ``rich_message._post`` — the single transport for ``editMessageText``
    and ``sendRichMessage`` alike (mirrors
    ``tests/integration/stream_busy_message/test_layout_modes.py``)."""
    calls = []

    async def _fake_post(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


# This directory has no `__init__.py` (hyphenated name, not a valid
# Python identifier) — a plain `from conftest import helper` risks
# resolving to a DIFFERENT directory's same-named `conftest.py` module
# already sitting in `sys.modules` (`queue-handoff/conftest.py` collects
# first alphabetically). Exposing helpers as fixtures instead sidesteps
# the whole import-identity question: pytest resolves fixtures by name
# per-directory, never via `sys.modules`.
@pytest.fixture
def append_queue_op():
    """Append one ``queue-operation`` JSONL line to a session's pinned
    transcript, the way Claude Code writes an absorption/pick-up/discard
    event (entrypoints.md "the transcript file (black-box input
    surface)"). Model: ``tests/test_transcript_exact_anchors.py``'s
    ``_append_round``."""
    def _append(sess, operation, reason, content, ts=None):
        line = {
            "type": "queue-operation",
            "operation": operation,
            "content": content,
            "timestamp": ts if ts is not None else time.time(),
        }
        if reason is not None:
            line["reason"] = reason
        with open(sess.stream_transcript_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    return _append
