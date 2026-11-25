"""design.md success criteria "Hook stamping": a ``statusline`` datagram whose
status file carries ``rate_limits`` sets ``sess.usage_plan_key`` to the
session's sign-in key (``""`` when unknown); one without leaves it alone;
the key is never saved across restarts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager import statusline_file, usage_store
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

from ._kit import LOGIN_A, LOGIN_B, UNKNOWN

SESSION = "claude-frontend"
LATER = 4_000_000_000 - 10
_seq = iter(range(1, 1_000_000))


class _Login:
    """The sign-in seam (``usage_store.login_for``), switchable mid-test."""

    def __init__(self, login):
        self.login = login

    def __call__(self, sess):
        return self.login


@pytest.fixture
def world(tmp_state_file, monkeypatch):
    registry = SessionRegistry()
    login = _Login(LOGIN_A)
    monkeypatch.setattr(usage_store, "login_for", login)
    notifier = AsyncMock()
    return registry, hr.HookReceiver(registry, notifier), login, notifier


def _payload(rate_limits=None) -> dict:
    data = {"session_id": "conv-a", "context_window": {"used_percentage": 12},
            "model": {"id": "claude-haiku", "display_name": "Haiku"}}
    if rate_limits is not None:
        data["rate_limits"] = rate_limits
    return data


def _datagram(recv, run_async, payload, session=SESSION):
    statusline_file.status_file_path(session).write_text(json.dumps(payload))
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": session, "context_pct": 12,
        "seq": next(_seq)}).encode()))


NEAR = {"seven_day": {"used_percentage": 92, "resets_at": LATER}}
LOW = {"five_hour": {"used_percentage": 10, "resets_at": LATER}}


def test_statusline_reading_stamps_session_key(world, run_async):
    registry, recv, _login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    assert registry.get_or_create(SESSION).usage_plan_key == usage_store.plan_key(LOGIN_A)


def test_low_reading_stamps_the_key_too(world, run_async):
    """The key follows the reading, whatever its level."""
    registry, recv, _login, _n = world
    _datagram(recv, run_async, _payload(LOW))
    assert registry.get_or_create(SESSION).usage_plan_key == usage_store.plan_key(LOGIN_A)


def test_new_session_key_default_is_empty(world):
    registry, _recv, _login, _n = world
    assert registry.get_or_create(SESSION).usage_plan_key == ""


def test_new_session_card_windows_default_is_none(world):
    registry, _recv, _login, _n = world
    assert registry.get_or_create(SESSION).card_usage_windows is None


def test_payload_without_rate_limits_keeps_key(world, run_async):
    registry, recv, login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    login.login = LOGIN_B
    _datagram(recv, run_async, _payload())
    assert registry.get_or_create(SESSION).usage_plan_key == usage_store.plan_key(LOGIN_A)


def test_payload_without_rate_limits_sets_no_key(world, run_async):
    registry, recv, _login, _n = world
    _datagram(recv, run_async, _payload())
    assert registry.get_or_create(SESSION).usage_plan_key == ""


def test_bad_rate_limits_keeps_key(world, run_async):
    """Error guessing: an unparsable window is no reading, so no re-stamp."""
    registry, recv, login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    login.login = LOGIN_B
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": "x",
                                                      "resets_at": LATER}}))
    assert registry.get_or_create(SESSION).usage_plan_key == usage_store.plan_key(LOGIN_A)


def test_sign_in_change_restamps_on_next_reading(world, run_async):
    registry, recv, login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    login.login = LOGIN_B
    _datagram(recv, run_async, _payload(NEAR))
    assert registry.get_or_create(SESSION).usage_plan_key == usage_store.plan_key(LOGIN_B)


def test_unknown_login_stamps_empty_key(world, run_async):
    registry, recv, login, _n = world
    login.login = UNKNOWN
    _datagram(recv, run_async, _payload(NEAR))
    assert registry.get_or_create(SESSION).usage_plan_key == ""


def test_known_then_unknown_login_clears_key(world, run_async):
    registry, recv, login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    login.login = UNKNOWN
    _datagram(recv, run_async, _payload(NEAR))
    assert registry.get_or_create(SESSION).usage_plan_key == ""


def test_reading_lands_under_the_stamped_key(world, run_async):
    registry, recv, _login, _n = world
    _datagram(recv, run_async, _payload(NEAR))
    key = registry.get_or_create(SESSION).usage_plan_key
    assert usage_store.plan_windows(key)["seven_day"].pct == 92


def test_stamping_one_session_leaves_another_alone(world, run_async):
    registry, recv, _login, _n = world
    other = registry.get_or_create("claude-backend")
    _datagram(recv, run_async, _payload(NEAR))
    assert other.usage_plan_key == ""


def test_reading_sends_no_telegram_message(world, run_async):
    _registry, recv, _login, notifier = world
    _datagram(recv, run_async, _payload({"seven_day": {"used_percentage": 100,
                                                      "resets_at": LATER}}))
    assert [c for c in notifier.mock_calls if "send" in str(c[0])] == []


def test_plan_key_not_persisted(world, run_async):
    registry, recv, _login, _n = world
    registry.transition(SESSION, Status.IDLE)
    _datagram(recv, run_async, _payload(NEAR))
    assert registry.get(SESSION).usage_plan_key  # precondition: stamped
    registry.save()
    fresh = SessionRegistry()
    fresh.load()
    assert fresh.get(SESSION).usage_plan_key == ""


def test_plan_key_absent_from_saved_file(world, run_async, tmp_state_file):
    registry, recv, _login, _n = world
    registry.transition(SESSION, Status.IDLE)
    _datagram(recv, run_async, _payload(NEAR))
    registry.save()
    raw = json.loads(tmp_state_file.read_text())["sessions"][SESSION]
    assert not {"usage_plan_key", "card_usage_windows"} & set(raw)
