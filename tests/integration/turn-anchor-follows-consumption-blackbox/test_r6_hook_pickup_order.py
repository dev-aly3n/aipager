"""R6: "the hook must emit queue_pickup BEFORE it forwards the
UserPromptSubmit event... the datagram socket preserves order for one
sender."

Driven entirely at the ``notify_hook.py`` level (upstream of the
daemon), per entrypoints.md: "call the hook's own main()/_run() with a
stubbed _udp... since R6 lives entirely in notify_hook.py." ``_udp`` is
a closure (not independently patchable), so the stub sits one level
lower, at its own transport seam: ``notify_hook.socket.socket`` is
replaced with a fake socket object whose ``sendto`` is recorded instead
of performed — no real OS socket is ever created (the hard rule against
touching a real socket under /tmp is honored by never constructing one
at all, real or under tmp_path).
"""

from __future__ import annotations

import io
import json
import sys

from aipager import policy_snapshot as ps
from aipager.dtach import notify_hook as nh

SESSION = "claude-r6"


class _FakeSocket:
    def __init__(self, sink):
        self._sink = sink

    def sendto(self, data, addr):
        self._sink.append(json.loads(data.decode()))
        return len(data)

    def close(self):
        pass


def _set_stdin(monkeypatch, payload: dict):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def _run_hook(monkeypatch, payload: dict) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(nh.socket, "socket",
                        lambda *a, **k: _FakeSocket(sent))
    _set_stdin(monkeypatch, payload)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    nh.main()
    return sent


def test_queue_pickup_emitted_before_the_user_prompt_submit_forward(monkeypatch):
    ps.write_note(
        SESSION, None, None, None,
        msg_id=42, chat_id=-1001, sender_key=(-1001, 12345),
        body="do the thing", raw_text="do the thing",
    )

    sent = _run_hook(monkeypatch, {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "do the thing",
        "session": SESSION,
    })

    event_names = [d.get("hook_event_name") for d in sent]
    assert event_names == ["queue_pickup", "UserPromptSubmit"], (
        f"R6's pinned order was not preserved: {event_names}")


def test_a_prompt_matching_no_note_forwards_without_any_pickup(monkeypatch):
    """Equivalence partition: no outstanding note at all — no
    queue_pickup datagram should be emitted, only the forward."""
    sent = _run_hook(monkeypatch, {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "nothing was ever queued for this text",
        "session": SESSION,
    })

    event_names = [d.get("hook_event_name") for d in sent]
    assert event_names == ["UserPromptSubmit"], (
        f"a no-match prompt must forward alone, no phantom pickup: "
        f"{event_names}")


def test_task_notification_continuation_never_emits_a_pickup(monkeypatch):
    """B5-adjacent negative: a background-job continuation prompt must
    never trigger a pick-up match/emit at all (spec.md's own documented
    safety leak this feature must not reopen)."""
    ps.write_note(
        SESSION, None, None, None,
        msg_id=43, chat_id=-1001, sender_key=(-1001, 12345),
        body="Background agent finished.", raw_text="Background agent finished.",
    )

    sent = _run_hook(monkeypatch, {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<task-notification>\n<task-id>abc</task-id>\n"
                  "Background agent finished.",
        "session": SESSION,
    })

    event_names = [d.get("hook_event_name") for d in sent]
    assert "queue_pickup" not in event_names, (
        f"a <task-notification> continuation must never match/consume "
        f"a note: {event_names}")
