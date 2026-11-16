"""The ``statusline`` datagram keeps the plan usage its payload carried
(roadmap 8.131). The hook writes the raw payload to the session's status
file before it sends the datagram; the daemon reads ``rate_limits`` from
that file. A payload without them never erases a reading."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager import statusline_file, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry
from aipager.statusline_file import Window

SESSION = "claude-frontend"
LATER = 4_000_000_000 - 10


@pytest.fixture
def receiver(tmp_state_file, monkeypatch):
    registry = SessionRegistry()
    monkeypatch.setattr(usage_store, "login_for",
                        lambda sess: SessionLogin("saved", "", "/home/alice/.claude"))
    return registry, hr.HookReceiver(registry, AsyncMock())


_seq = iter(range(1, 1_000_000))


def _payload(rate_limits=None) -> dict:
    data = {"session_id": "conv-a", "context_window": {"used_percentage": 12},
            "model": {"id": "claude-haiku", "display_name": "Haiku"}}
    if rate_limits is not None:
        data["rate_limits"] = rate_limits
    return data


def _datagram(recv, run_async, payload):
    statusline_file.status_file_path(SESSION).write_text(json.dumps(payload))
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": SESSION, "context_pct": 12,
        "seq": next(_seq)}).encode()))


def _plan():
    return usage_store.snapshot(None).plan


def test_a_payload_with_rate_limits_stores_a_reading(receiver, run_async):
    _registry, recv = receiver
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": 83,
                                                      "resets_at": LATER}}))
    (reading,) = _plan()
    assert reading.windows == {"seven_day": Window(83.0, LATER)}
    assert reading.session == "frontend" and reading.kind == "saved"


def test_the_reading_keeps_the_sessions_chat(receiver, run_async):
    """Its name is shown only to who may see that chat (rev-iter1-001)."""
    registry, recv = receiver
    registry.get_or_create(SESSION).scope_chat_id = -100123
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": 83,
                                                      "resets_at": LATER}}))
    (reading,) = _plan()
    assert reading.scope == -100123


def test_a_payload_without_rate_limits_leaves_the_reading_alone(receiver, run_async):
    _registry, recv = receiver
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": 83,
                                                      "resets_at": LATER}}))
    _datagram(recv, run_async, _payload())
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": "x",
                                                      "resets_at": LATER}}))
    (reading,) = _plan()
    assert reading.windows == {"seven_day": Window(83.0, LATER)}


def test_no_status_file_and_a_bad_file_change_nothing_and_never_break(receiver, run_async):
    registry, recv = receiver
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": SESSION, "context_pct": 40,
        "seq": next(_seq)}).encode()))
    statusline_file.status_file_path(SESSION).write_text("{not json")
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": SESSION, "context_pct": 41,
        "seq": next(_seq)}).encode()))
    assert _plan() == ()
    # The branch's own work still happened.
    assert registry.get_or_create(SESSION).statusline_ctx_pct == 41


def test_a_failing_store_never_breaks_the_branch(receiver, run_async, monkeypatch):
    registry, recv = receiver

    def boom(*a, **kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(usage_store, "note_plan", boom)
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": 1,
                                                      "resets_at": LATER}}))
    assert registry.get_or_create(SESSION).statusline_ctx_pct == 12
