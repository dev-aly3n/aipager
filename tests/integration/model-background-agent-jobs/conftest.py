"""Shared fixtures for the "model Claude Code background-agent jobs"
black-box integration tests.

Independent of the Developer's own adapted unit tests
(``tests/test_job_background_lifecycle.py``, ``tests/test_state.py``,
``tests/test_hook_receiver*.py``, ``tests/test_bot_notify_idle*.py``,
``tests/test_session_monitor.py``, ``tests/test_hook_enforcement.py``,
``tests/test_notify_hook_continuation.py``) — this suite drives only
the surface documented in ``entrypoints.md``: ``HookReceiver._on_datagram``,
the ``notify_fn``/``TelegramBot.notify`` event contract, ``TrackedSession``
fields/methods, ``enforce.py``'s pure decision functions,
``notify_hook.py``'s stdin-driven hook body, and
``animation.build_stream_card``.

This directory's name (``model-background-agent-jobs``) is not a valid
Python identifier, so test modules here cannot ``from .conftest import
...`` — pytest still auto-discovers this file as a conftest regardless
(matching ``tests/integration/queue-handoff/conftest.py``'s documented
convention).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status, TrackedSession

SESSION = "hiva"
LABEL = "hiva"

# The exact continuation-turn prefix spec.md/entrypoints.md pin. Not
# imported from the source (the three module constants are deliberately
# unshared and NOT-exported per entrypoints.md) — the literal string
# IS the contract.
TASK_NOTIFICATION_PREFIX = "<task-notification>"

TELEGRAM_MARKER = "[via Telegram msg=123]"


@pytest.fixture(autouse=True)
def _mock_rich_message(monkeypatch):
    """Prevent real HTTP calls to Telegram for every test in this
    directory that reaches ``TelegramBot.notify`` — mirrors
    ``tests/test_bot_notify_idle.py``'s local autouse fixture. The
    suite-wide ``_block_real_telegram_http`` in ``tests/conftest.py``
    already refuses the raw transport; this additionally makes
    ``send_rich_message`` succeed so the higher-level dispatch in
    ``notify()`` doesn't rely on exception handling to reach the
    assertions under test.
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
    plain module-level function) so every test module in this
    hyphenated, non-importable directory can reach it without a
    ``from conftest import ...`` (which pytest's default import mode
    cannot resolve for a directory name that isn't a valid Python
    identifier)."""
    def _send(recv, **fields):
        payload = json.dumps(fields).encode()
        run_async(recv._on_datagram(payload))
    return _send


@pytest.fixture
def mk_job_session():
    """Returns a factory building a ``TrackedSession`` pre-seeded as if a
    real prompt already started a job — the state a job-open session is
    always in by the time an idle-class event, a continuation, or a TTL
    sweep reaches it."""
    def _mk(*, status=Status.BUSY, trigger_msg_id=123, busy_msg_id=42,
           active_subagents=None, last_prompt_origin="telegram"):
        # Bare "hiva", matching entrypoints.md's literal hook payloads
        # ("session": "hiva") verbatim — SessionRegistry.get_or_create()
        # uses whatever string a hook datagram's "session" field carries
        # as the registry key AND the resulting TrackedSession.name with
        # no prefix added, so a fixture-built session that a test will
        # ALSO drive through HookReceiver._on_datagram(session="hiva",
        # ...) must share this exact name or the hook ends up creating
        # and mutating a second, disjoint session object while every
        # assertion keeps reading the untouched original — a silent
        # false pass.
        sess = TrackedSession(name=SESSION, label=LABEL, status=status)
        sess.trigger_msg_id = trigger_msg_id
        sess.busy_msg_id = busy_msg_id
        sess.last_prompt_origin = last_prompt_origin
        sess.active_subagents = (active_subagents if active_subagents is not None
                                 else {})
        return sess
    return _mk


@pytest.fixture
def all_sent_texts():
    """Returns a callable collecting every string argument ever passed to
    any Telegram-boundary mock this suite wires up, across whichever
    channel the implementation actually used (``send_message``,
    ``send_rich_message``, ``_edit_busy_rich``, ``_edit_busy_raw``) — a
    black-box test must not assume which one carries a given piece of
    content, only that the documented text appears (or doesn't)
    somewhere on the wire."""
    def _collect(bot) -> list[str]:
        texts: list[str] = []
        candidates = []
        app = getattr(bot, "_app", None)
        if app is not None:
            candidates.append(getattr(app.bot, "send_message", None))
            candidates.append(getattr(app.bot, "edit_message_text", None))
        for name in ("_edit_busy_rich", "_edit_busy_raw"):
            candidates.append(getattr(bot, name, None))
        try:
            from aipager.bot import notify as _notify_mod
            candidates.append(_notify_mod.send_rich_message)
        except Exception:
            pass
        for mock in candidates:
            if mock is None or not hasattr(mock, "call_args_list"):
                continue
            for call in mock.call_args_list:
                for arg in list(call.args) + list(call.kwargs.values()):
                    if isinstance(arg, str):
                        texts.append(arg)
        return texts
    return _collect
