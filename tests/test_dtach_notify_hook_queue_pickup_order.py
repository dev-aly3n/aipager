"""R6 (design.md "turn anchor follows consumption"): the hook's
``queue_pickup`` datagram must reach the daemon BEFORE the forwarded
``UserPromptSubmit`` does — otherwise the receiver's IDLE→BUSY card for
the new turn is built with the OLD ``trigger_msg_id`` and then
re-anchored: correct, but two sends per turn instead of one.

``_udp`` is a closure defined inside ``_run()`` — not a module
attribute, so it can't be monkeypatched directly. The socket layer it
calls into is: replacing ``socket.socket`` records every datagram this
hook invocation sends, in the exact order it sends them, which is the
observable this rule is actually about (the UDS datagram socket
preserves per-sender order).

Mirrors ``tests/test_notify_hook_continuation.py``'s harness (stdin-
driven ``notify_hook.main()``, ``snapshot_path``/``notes_dir`` isolated
to ``tmp_path``).
"""

from __future__ import annotations

import io
import json
import socket as socket_mod
import sys

from aipager.dtach import notify_hook


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _isolate_snapshot_path(monkeypatch, tmp_path):
    from aipager import policy_snapshot
    monkeypatch.setattr(policy_snapshot, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    return policy_snapshot


class _FakeDatagramSocket:
    """Records every ``sendto`` payload, decoded, in call order."""

    def __init__(self, sink):
        self._sink = sink

    def sendto(self, data, _addr):
        self._sink.append(json.loads(data.decode()))

    def close(self):
        pass


def _record_udp_sends(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        socket_mod, "socket",
        lambda *_a, **_k: _FakeDatagramSocket(sent),
    )
    return sent


def test_queue_pickup_emitted_before_the_userpromptsubmit_forward(
    monkeypatch, tmp_path,
):
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps.write_note(
        "claude-hiva", None, None, None,
        msg_id=7, chat_id=999, sender_key=(1, 1),
        body="no it was a test", raw_text="no it was a test",
    )
    sent = _record_udp_sends(monkeypatch)

    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "no it was a test",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()

    event_sequence = [p.get("hook_event_name") for p in sent]
    assert event_sequence == ["queue_pickup", "UserPromptSubmit"], (
        f"R6 order violated: {event_sequence}"
    )
    queue_pickup_payload = sent[0]
    assert [n["msg_id"] for n in queue_pickup_payload["consumed"]] == [7]


def test_no_match_still_forwards_userpromptsubmit_alone(monkeypatch, tmp_path):
    """No outstanding note at all — no queue_pickup datagram fires, but
    the forward must still happen (unaffected by this feature)."""
    _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    sent = _record_udp_sends(monkeypatch)

    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "brand new prompt",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()

    event_sequence = [p.get("hook_event_name") for p in sent]
    assert event_sequence == ["UserPromptSubmit"]


def test_task_notification_continuation_skips_pickup_but_still_forwards(
    monkeypatch, tmp_path,
):
    """A <task-notification> continuation must never consume notes
    (test_notify_hook_continuation.py's own contract) — pinned again
    here at the ordering level: no queue_pickup datagram, ever, even
    with a matching note outstanding."""
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps.write_note(
        "claude-hiva", None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body="<task-notification>\n<task-id>abc</task-id>\ndone.",
        raw_text="<task-notification>\n<task-id>abc</task-id>\ndone.",
    )
    sent = _record_udp_sends(monkeypatch)

    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<task-notification>\n<task-id>abc</task-id>\ndone.",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()

    event_sequence = [p.get("hook_event_name") for p in sent]
    assert event_sequence == ["UserPromptSubmit"]


def test_other_events_unaffected_by_the_reordering(monkeypatch, tmp_path):
    """PreToolUse (and every other non-UserPromptSubmit event) still
    goes through the plain `else: _udp(data)` forward, unchanged."""
    _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    sent = _record_udp_sends(monkeypatch)

    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()

    event_sequence = [p.get("hook_event_name") for p in sent]
    assert event_sequence == ["PreToolUse"]
