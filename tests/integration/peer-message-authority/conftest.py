"""Fixtures for the "peer message authority" tests. Everything stays in
the test's tmp folder: the root conftest redirects the snapshot, notes
and receipt store. No socket is bound."""

from __future__ import annotations

import io
import json
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from aipager.dtach import notify_hook

RECEIVER = "claude-mohandes"


@pytest.fixture(autouse=True)
def _runtime_dir():
    """The root conftest points the session files at a folder it does not
    create; here they are written, so make it."""
    from aipager import policy_snapshot as ps
    ps.snapshot_path(RECEIVER).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def run_hook(monkeypatch, tmp_path):
    """Run aipager-hook's body on a payload; returns its stdout. Datagrams
    go nowhere (no daemon socket exists)."""
    def run(payload: dict, session: str = RECEIVER) -> str:
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "no-daemon"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        notify_hook._ERROR_CONTEXT.update(event=None, tool=None)
        notify_hook._run(session, [b"", False, False])
        return out.getvalue()
    return run


@pytest.fixture
def mk_query():
    """A mocked Telegram CallbackQuery (as tests/test_bot_callbacks.py)."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query
    return _mk
