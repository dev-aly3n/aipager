"""Shared fixtures for the "busy card agent rows" black-box integration
tests.

Independent of the Developer's own adapted unit tests
(``tests/test_bot_notify.py``, ``tests/test_bot_animation.py``,
``tests/test_state.py``, ``tests/test_hook_receiver.py``,
``tests/test_hook_receiver_extra.py``, ``tests/test_stream_card.py``) —
this suite drives only the surface documented in
``.ship/busy-card-agent-rows/entrypoints.md``:
``HookReceiver._on_datagram``, the ``notify_fn``/``TelegramBot.notify``
event contract, ``TrackedSession`` fields/methods, and the rendering
functions in ``aipager/bot/animation.py``
(``build_stream_card``/``build_stream_card_ex``/``build_full_log``) plus
``TelegramBot._build_busy_text``.

This directory's name (``busy-card-agent-rows``) is not a valid Python
identifier, so test modules here cannot ``from .conftest import ...`` —
pytest still auto-discovers this file as a conftest regardless (matching
``tests/integration/queue-handoff/conftest.py``'s documented convention,
also followed by ``tests/integration/model-background-agent-jobs/conftest.py``).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status, TrackedSession

SESSION = "claude-jim"
LABEL = "jim"


@pytest.fixture(autouse=True)
def _mock_rich_message(monkeypatch):
    """Prevent real HTTP calls to Telegram for every test in this
    directory that reaches ``TelegramBot.notify`` — mirrors
    ``tests/test_bot_notify_idle.py``'s local autouse fixture and
    ``tests/integration/model-background-agent-jobs/conftest.py``'s own
    copy. The suite-wide ``_block_real_telegram_http`` in
    ``tests/conftest.py`` already refuses the raw transport; this
    additionally makes ``send_rich_message`` succeed so the higher-level
    dispatch in ``notify()`` doesn't rely on exception handling to reach
    the assertions under test.
    """
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(return_value={}),
    )


@pytest.fixture
def receiver():
    """A wired-up ``HookReceiver`` with a mocked ``notify_fn`` — mirrors
    ``tests/test_hook_receiver.py``'s ``receiver`` fixture (pre-feature
    convention), independently re-declared here so this suite has no
    import-time dependency on the Developer's own test module.

    Returns ``(registry, recv, notify_fn)``.
    """
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


@pytest.fixture
def send_hook(run_async):
    """Returns a callable ``send_hook(recv, **fields)`` that feeds a JSON
    datagram into ``HookReceiver._on_datagram``. A fixture (rather than a
    plain module-level function) so every test module in this hyphenated,
    non-importable directory can reach it without a ``from conftest
    import ...``."""
    def _send(recv, **fields):
        payload = json.dumps(fields).encode()
        run_async(recv._on_datagram(payload))
    return _send


@pytest.fixture
def mk_sess():
    """Returns a factory building a bare ``TrackedSession`` — a busy
    session with no live-edit side effects (``busy_msg_id=None``) unless
    the caller overrides it."""
    def _mk(*, name=SESSION, label=LABEL, status=Status.BUSY, busy_msg_id=None):
        sess = TrackedSession(name=name, label=label, status=status)
        sess.busy_msg_id = busy_msg_id
        return sess
    return _mk
