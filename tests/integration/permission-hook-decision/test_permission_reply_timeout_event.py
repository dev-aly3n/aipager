"""Black-box coverage of the new ``permission_reply_timeout`` event
documented in entrypoints.md's Events section: a hook's self-reported
give-up, fed directly into HookReceiver._on_datagram, must clear the
in-memory "hook is waiting" state without needing a real timing race,
and must never raise or resurrect a gone session.
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_timeout_event_is_handled_without_error(receiver, run_async):
    registry, recv, _notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_permission = {
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "hook_reply": {"addr": "/run/user/1000/x.sock", "request_id": "req-1"},
    }
    # Must not raise.
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-jim", aipager_request_id="req-1")


def test_timeout_event_clears_only_hook_reply(receiver, run_async):
    registry, recv, _notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_permission = {
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "hook_reply": {"addr": "/run/user/1000/x.sock", "request_id": "req-1"},
    }
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-jim", aipager_request_id="req-1")

    assert sess.pending_permission is not None
    assert sess.pending_permission["hook_reply"] is None
    # The rest of pending_permission must survive -- the Telegram keyboard
    # and tool_info stay valid; only the hook offer expired, so the
    # existing keystroke path can still answer via Claude Code's own
    # dialog.
    assert sess.pending_permission["tool_summary"] == "Bash: ls"
    assert sess.pending_permission["tool_info"] == {"name": "Bash"}


def test_timeout_event_with_mismatched_request_id_leaves_hook_reply_alone(
    receiver, run_async,
):
    """A stale/duplicate timeout notice for an OLDER request must never
    clobber a NEWER, still-live hook_reply for the same session."""
    registry, recv, _notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    live = {"addr": "/run/user/1000/newer.sock", "request_id": "req-2"}
    sess.pending_permission = {
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "hook_reply": live,
    }
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-jim", aipager_request_id="req-1")  # stale id
    assert sess.pending_permission["hook_reply"] == live


def test_timeout_event_for_unknown_session_never_fabricates_pending_permission(
    receiver, run_async,
):
    """A gone/never-existed session must not gain a pending_permission
    (with a hook_reply key materialized out of nowhere) from a stale
    timeout notice. The datagram dispatcher generically tracks any
    session name it sees (matching every other event, per
    tests/test_hook_receiver.py's own
    test_unknown_event_just_ensures_tracking), so a session record MAY
    now exist -- what must never happen is a bogus pending_permission
    appearing on it."""
    registry, recv, _notify_fn = receiver
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-ghost", aipager_request_id="req-1")
    sess = registry.get("claude-ghost")
    assert sess is None or sess.pending_permission is None


def test_timeout_event_with_no_pending_permission_is_a_safe_noop(receiver, run_async):
    registry, recv, _notify_fn = receiver
    registry.get_or_create("claude-jim")  # tracked, but no pending_permission
    # Must not raise.
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-jim", aipager_request_id="req-1")
    assert registry.get("claude-jim").pending_permission is None


def test_timeout_event_when_hook_reply_already_none_is_a_safe_noop(receiver, run_async):
    """A duplicate timeout notice arriving after the state was already
    cleared (e.g. by an earlier timeout, or a tap that already fired)
    must not raise or resurrect a stale addr/request_id."""
    registry, recv, _notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_permission = {
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "hook_reply": None,
    }
    _send(recv, run_async, hook_event_name="permission_reply_timeout",
          session="claude-jim", aipager_request_id="req-1")
    assert sess.pending_permission["hook_reply"] is None
