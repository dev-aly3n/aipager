"""Roadmap 8.143, what the daemon tells the typing about dialogs: the probe
cli/daemon.py installs answers "open" while the session waits on a prompt,
and from the moment a datagram telling of a drawn dialog arrives until it is
handled (it can wait behind an older turn's finish while the dialog is
already on screen)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from aipager.cli.daemon import _wire_dialog_probe
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.state import SessionRegistry, Status

NAME = "claude-probe"


def _datagram(**fields) -> bytes:
    return json.dumps({"session": NAME, **fields}).encode()


@pytest.mark.parametrize("fields,counted", [
    ({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, True),
    ({"hook_event_name": "Notification", "notification_type": "permission_prompt"}, True),
    ({"hook_event_name": "Elicitation"}, True),
    ({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}, True),
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, False),
    ({"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion"}, False),
    ({"hook_event_name": "permission_reply_timeout"}, False),
])
def test_which_datagrams_tell_of_a_dialog(fields, counted):
    assert (hr._dialog_datagram_session(_datagram(**fields)) == NAME) is counted


@pytest.mark.parametrize("data", [b"PermissionRequest", b"[\"PermissionRequest\"]",
                                  b'{"hook_event_name": "PermissionRequest", "session": 3}'])
def test_garbage_is_never_counted(data):
    assert hr._dialog_datagram_session(data) == ""


def test_a_dialog_datagram_is_pending_from_arrival_until_handled(run_async):
    gate = asyncio.Event()
    seen = []

    class Receiver(hr.HookReceiver):
        async def _on_datagram(self, data):
            seen.append(hr.dialog_datagram_pending(NAME))
            await gate.wait()

    recv = Receiver(SessionRegistry(), None)

    async def scenario():
        coro = recv._on_datagram_counted(_datagram(hook_event_name="PermissionRequest"))
        # Counted synchronously, before the handler has even started.
        before = hr.dialog_datagram_pending(NAME)
        task = asyncio.ensure_future(coro)
        await asyncio.sleep(0)
        during = hr.dialog_datagram_pending(NAME)
        gate.set()
        await task
        return before, during, hr.dialog_datagram_pending(NAME)

    assert run_async(scenario()) == (True, True, False)
    assert seen == [True]
    assert hr._DIALOG_DATAGRAMS == {}


def test_a_handler_that_raises_still_uncounts(run_async):
    class Receiver(hr.HookReceiver):
        async def _on_datagram(self, data):
            raise RuntimeError("boom")

    recv = Receiver(SessionRegistry(), None)
    with pytest.raises(RuntimeError):
        run_async(recv._on_datagram_counted(_datagram(hook_event_name="PermissionRequest")))
    assert hr.dialog_datagram_pending(NAME) is False


def test_other_datagrams_are_never_counted(run_async):
    seen = []

    class Receiver(hr.HookReceiver):
        async def _on_datagram(self, data):
            seen.append(dict(hr._DIALOG_DATAGRAMS))

    recv = Receiver(SessionRegistry(), None)
    run_async(recv._on_datagram_counted(_datagram(hook_event_name="PreToolUse",
                                                  tool_name="Bash")))
    assert seen == [{}]


@pytest.fixture
def probe():
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    _wire_dialog_probe(registry, hr)
    return SimpleNamespace(registry=registry, sess=sess)


def test_the_probe_says_closed_for_a_plain_busy_session(probe):
    probe.registry.transition(NAME, Status.BUSY)
    assert inject.dialog_open(NAME) is False
    assert inject.dialog_open("claude-unknown") is False


def test_the_probe_says_open_while_the_session_waits_on_a_prompt(probe):
    probe.registry.transition(NAME, Status.BUSY)
    probe.registry.transition(NAME, Status.INTERACTIVE)
    assert inject.dialog_open(NAME) is True


@pytest.mark.parametrize("state", ["carry", "pending", "extra", "datagram"])
def test_the_probe_says_open_for_every_other_sign_of_a_prompt(probe, state):
    probe.registry.transition(NAME, Status.BUSY)
    if state == "carry":
        probe.sess.prompt_wait_carry = True
    elif state == "pending":
        probe.sess.pending_permission = {"tool_summary": "Bash: ls"}
    elif state == "extra":
        probe.sess.extra_prompts = [{"perm": {}}]
    else:
        hr._DIALOG_DATAGRAMS[NAME] = 1
    assert inject.dialog_open(NAME) is True


def test_probe_reports_terminal_dialog(probe):
    """Roadmap 8.127: a dialog in Claude Code's terminal that no hook tells
    of (from its session record) is a dialog for the typing too."""
    probe.registry.transition(NAME, Status.IDLE)
    assert inject.dialog_open(NAME) is False
    probe.sess.term_dialog = {"epoch": 1, "pid": 4100, "kind": "other"}
    assert inject.dialog_open(NAME) is True
    probe.sess.term_dialog = None
    assert inject.dialog_open(NAME) is False
