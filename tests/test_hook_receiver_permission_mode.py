"""The hook receiver records the permission mode Claude Code reports: the
main agent's own events only, never aipager's internal datagrams, never
while a switch's presses settle; a pending switch is told; a bypass report
on a session not launched in Full access is logged."""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import ModeSwitch, SessionRegistry


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    return sess, recv, notify_fn


def _send(recv, run_async, **fields):
    fields.setdefault("session", "claude-jim")
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def _reported(notify_fn):
    return [c.args for c in notify_fn.await_args_list if c.args[1] == "mode_reported"]


def test_main_agent_event_records_the_mode(receiver, run_async):
    sess, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="Notification", permission_mode="auto")
    assert sess.reported_mode == "auto"
    assert sess.current_mode() == "auto"
    assert _reported(notify_fn) == []  # no switch waits


def test_a_subagents_mode_is_not_the_sessions(receiver, run_async):
    sess, recv, _ = receiver
    _send(recv, run_async, hook_event_name="Notification", permission_mode="plan",
          agent_id="a1")
    assert sess.reported_mode == ""


@pytest.mark.parametrize("event", sorted(hr._INTERNAL_HOOK_EVENTS))
def test_internal_datagrams_are_not_a_report(receiver, run_async, event):
    sess, recv, _ = receiver
    _send(recv, run_async, hook_event_name=event, permission_mode="plan")
    assert sess.reported_mode == ""


@pytest.mark.parametrize("raw", ["", "Weird", 3], ids=["empty", "unknown", "not-a-string"])
def test_an_unknown_mode_is_ignored(receiver, run_async, raw):
    sess, recv, _ = receiver
    _send(recv, run_async, hook_event_name="Notification", permission_mode=raw)
    assert sess.reported_mode == ""


def test_a_pending_switch_is_told(receiver, run_async):
    sess, recv, notify_fn = receiver
    sess.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic() - 5)
    _send(recv, run_async, hook_event_name="Notification", permission_mode="auto")
    assert _reported(notify_fn) == [(sess, "mode_reported", {"dialog": False})]


@pytest.mark.parametrize("fields", [
    {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {}},
    {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {}},
], ids=["permission-request", "ask-user-question"])
def test_a_dialog_event_says_so(receiver, run_async, fields):
    sess, recv, notify_fn = receiver
    sess.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic() - 5)
    _send(recv, run_async, permission_mode="default", **fields)
    assert _reported(notify_fn)[0][2] == {"dialog": True}


def test_a_report_while_presses_settle_is_not_recorded(receiver, run_async):
    sess, recv, notify_fn = receiver
    sess.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic())
    _send(recv, run_async, hook_event_name="Notification", permission_mode="default")
    assert sess.reported_mode == ""
    assert _reported(notify_fn) == []


def test_bypass_not_launched_is_logged_and_never_widens(receiver, run_async, caplog):
    sess, recv, _ = receiver
    sess.perm_mode = "auto"
    with caplog.at_level(logging.WARNING, logger=hr.log.name):
        _send(recv, run_async, hook_event_name="Notification",
              permission_mode="bypassPermissions")
    assert "reports bypassPermissions" in caplog.text
    assert sess.perm_mode == "auto"


def test_bypass_in_full_access_is_not_logged(receiver, run_async, caplog):
    sess, recv, _ = receiver
    sess.perm_mode = "full"
    with caplog.at_level(logging.WARNING, logger=hr.log.name):
        _send(recv, run_async, hook_event_name="Notification",
              permission_mode="bypassPermissions")
    assert "reports bypassPermissions" not in caplog.text


def test_bypass_is_logged_once_not_on_every_hook(receiver, run_async, caplog):
    # A CLI session started with the bypass flag reports it on every event:
    # one WARNING when it turns to bypass, not one per hook.
    sess, recv, _ = receiver
    sess.perm_mode = "ask"
    with caplog.at_level(logging.WARNING, logger=hr.log.name):
        for i, event in enumerate(("PreToolUse", "PostToolUse", "PreToolUse")):
            _send(recv, run_async, hook_event_name=event, tool_name="Bash",
                  tool_input={"command": f"ls {i}"}, tool_use_id=f"toolu_{i}",
                  permission_mode="bypassPermissions")
    assert sess.reported_mode == "bypassPermissions"
    assert caplog.text.count("reports bypassPermissions") == 1


def test_bypass_again_after_leaving_it_is_logged_again(receiver, run_async, caplog):
    sess, recv, _ = receiver
    sess.perm_mode = "ask"
    with caplog.at_level(logging.WARNING, logger=hr.log.name):
        for i, raw in enumerate(("bypassPermissions", "default", "bypassPermissions")):
            _send(recv, run_async, hook_event_name="PreToolUse", tool_name="Bash",
                  tool_input={"command": f"ls {i}"}, tool_use_id=f"toolu_{i}",
                  permission_mode=raw)
    assert caplog.text.count("reports bypassPermissions") == 2
