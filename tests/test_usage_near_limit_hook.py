"""The ``statusline`` datagram stamps the session's sign-in key
(``TrackedSession.usage_plan_key``) where the reading arrives, so the busy
card's near-limit note looks up that sign-in's reading only (roadmap 8.131
follow-up). The key is transient: never saved, so a restart waits for the
session's next reading."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager import statusline_file, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry

SESSION = "claude-frontend"
LATER = 4_000_000_000 - 10
ALICE = SessionLogin("saved", "", "/home/alice/.claude")
WEEK = {"seven_day": {"used_percentage": 92, "resets_at": LATER}}

_seq = iter(range(1, 1_000_000))


@pytest.fixture
def logins(monkeypatch):
    """The sign-in each call of `login_for` answers (a seam on the store,
    never `time`/`os`)."""
    current = {"login": ALICE}
    monkeypatch.setattr(usage_store, "login_for", lambda sess: current["login"])
    return current


@pytest.fixture
def receiver(tmp_state_file, logins):
    registry = SessionRegistry()
    return registry, hr.HookReceiver(registry, AsyncMock())


def _datagram(recv, run_async, rate_limits=None):
    data = {"session_id": "conv-a", "context_window": {"used_percentage": 12},
            "model": {"id": "claude-haiku", "display_name": "Haiku"}}
    if rate_limits is not None:
        data["rate_limits"] = rate_limits
    statusline_file.status_file_path(SESSION).write_text(json.dumps(data))
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": SESSION, "context_pct": 12,
        "seq": next(_seq)}).encode()))


def test_statusline_reading_stamps_session_key(receiver, run_async):
    registry, recv = receiver
    sess = registry.get_or_create(SESSION)
    assert sess.usage_plan_key == ""
    _datagram(recv, run_async, WEEK)
    assert sess.usage_plan_key == usage_store.plan_key(ALICE)
    assert usage_store.plan_windows(sess.usage_plan_key) is not None


def test_payload_without_rate_limits_keeps_key(receiver, run_async):
    registry, recv = receiver
    sess = registry.get_or_create(SESSION)
    _datagram(recv, run_async, WEEK)
    _datagram(recv, run_async)
    assert sess.usage_plan_key == usage_store.plan_key(ALICE)


def test_unknown_login_stamps_empty_key(receiver, run_async, logins):
    registry, recv = receiver
    sess = registry.get_or_create(SESSION)
    _datagram(recv, run_async, WEEK)
    logins["login"] = SessionLogin("unknown")
    _datagram(recv, run_async, WEEK)
    assert sess.usage_plan_key == ""


def test_a_new_sign_in_restamps_the_key(receiver, run_async, logins):
    registry, recv = receiver
    sess = registry.get_or_create(SESSION)
    _datagram(recv, run_async, WEEK)
    other = SessionLogin("token", "env", "/home/alice/.claude-work")
    logins["login"] = other
    _datagram(recv, run_async, WEEK)
    assert sess.usage_plan_key == usage_store.plan_key(other)


def test_plan_key_not_persisted(tmp_state_file):
    registry = SessionRegistry()
    sess = registry.get_or_create(SESSION)
    sess.usage_plan_key = usage_store.plan_key(ALICE)
    sess.card_usage_windows = {"seven_day": None}
    registry.save()
    saved = json.loads(tmp_state_file.read_text())
    entry = saved["sessions"][SESSION]
    assert "usage_plan_key" not in entry and "card_usage_windows" not in entry
    again = SessionRegistry()
    again.load()
    loaded = again.get_or_create(SESSION)
    assert loaded.usage_plan_key == "" and loaded.card_usage_windows is None
