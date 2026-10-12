"""The "context almost full" notice is a one-time heads-up per Claude
conversation (operator report 2026-10-12: eight identical notices for one
session, one after every hourly auto-compaction).

Compaction keeps Claude Code's session_id, so it never re-arms the notice;
/clear or another conversation changes the id and gets one heads-up again;
the conversation it was sent for survives a daemon restart."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry

SESSION = "claude-boss"


@pytest.fixture
def receiver(tmp_state_file):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


_seq = iter(range(1, 1_000_000))


def _send(recv, run_async, **fields):
    fields.setdefault("session", SESSION)
    # Each datagram distinct, so the receiver's duplicate filter (same
    # payload within a short window) never swallows a repeated reading.
    fields["seq"] = next(_seq)
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def _statusline(recv, run_async, pct, conversation=None):
    fields = {"type": "statusline", "context_pct": pct}
    if conversation is not None:
        fields["session_id"] = conversation
    _send(recv, run_async, **fields)


def _warnings(notify_fn) -> int:
    return [c.args[1] for c in notify_fn.await_args_list].count("context_warning")


def _compact_via_session_start(recv, run_async, trigger, conversation):
    """A compaction closed by Claude Code's SessionStart(source=compact),
    then the first statusLine after it reading 0%."""
    _send(recv, run_async, hook_event_name="PreCompact", trigger=trigger,
          sl_tokens={"context_pct": 95})
    _send(recv, run_async, hook_event_name="SessionStart", source="compact",
          session_id=conversation, sl_tokens={"context_pct": 3})
    _statusline(recv, run_async, 0, conversation)


def _compact_via_statusline_fallback(recv, run_async, trigger, conversation):
    """A compaction whose SessionStart was missed: the statusLine fallback
    ("Compacted (statusLine fallback): 96% -> 0%") closes it."""
    _send(recv, run_async, hook_event_name="PreCompact", trigger=trigger,
          sl_tokens={"context_pct": 96})
    _statusline(recv, run_async, 0, conversation)


def test_two_compaction_cycles_in_one_conversation_send_one_notice(receiver, run_async):
    registry, recv, notify_fn = receiver
    _statusline(recv, run_async, 82, "conv-a")
    assert _warnings(notify_fn) == 1
    # Auto-compaction, then the context climbs past 80% again.
    _compact_via_session_start(recv, run_async, "auto", "conv-a")
    _statusline(recv, run_async, 40, "conv-a")
    _statusline(recv, run_async, 85, "conv-a")
    # A second cycle closed by the statusLine fallback, then manual /compact.
    _compact_via_statusline_fallback(recv, run_async, "auto", "conv-a")
    _statusline(recv, run_async, 88, "conv-a")
    _compact_via_session_start(recv, run_async, "manual", "conv-a")
    _statusline(recv, run_async, 91, "conv-a")
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert events.count("compact_done") == 3  # compaction notices unchanged
    assert _warnings(notify_fn) == 1


def test_a_new_conversation_gets_one_heads_up_again(receiver, run_async):
    registry, recv, notify_fn = receiver
    _statusline(recv, run_async, 85, "conv-a")
    _statusline(recv, run_async, 90, "conv-a")
    assert _warnings(notify_fn) == 1
    # /clear: a new conversation id, a fresh context that fills up again.
    _statusline(recv, run_async, 2, "conv-b")
    _statusline(recv, run_async, 81, "conv-b")
    _statusline(recv, run_async, 86, "conv-b")
    assert _warnings(notify_fn) == 2
    assert registry.get(SESSION).context_warned_conversation == "conv-b"


def test_without_an_id_on_the_statusline_the_transcript_names_the_conversation(
        receiver, run_async, tmp_path):
    """An older aipager-statusline sends no session_id: the transcript
    stem the hooks reported (Claude Code's session id) stands in."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="Notification", notification_type="idle_prompt",
          transcript_path=str(tmp_path / "conv-a.jsonl"))
    _statusline(recv, run_async, 85)
    _compact_via_statusline_fallback(recv, run_async, "auto", None)
    _statusline(recv, run_async, 87)
    assert _warnings(notify_fn) == 1
    _send(recv, run_async, hook_event_name="Notification", notification_type="idle_prompt",
          transcript_path=str(tmp_path / "conv-b.jsonl"))
    _statusline(recv, run_async, 3)
    _statusline(recv, run_async, 84)
    assert _warnings(notify_fn) == 2


def test_the_sent_notice_survives_a_daemon_restart(receiver, run_async, tmp_state_file):
    registry, recv, notify_fn = receiver
    _statusline(recv, run_async, 85, "conv-a")
    assert _warnings(notify_fn) == 1
    registry.save()

    registry2 = SessionRegistry()
    registry2.load()
    assert registry2.get(SESSION).context_warned_conversation == "conv-a"
    notify2 = AsyncMock()
    recv2 = hr.HookReceiver(registry2, notify2)
    _statusline(recv2, run_async, 90, "conv-a")
    assert _warnings(notify2) == 0
    # ...and a new conversation after the restart still gets its heads-up.
    _statusline(recv2, run_async, 88, "conv-b")
    assert _warnings(notify2) == 1


def test_a_registry_file_without_the_field_loads(tmp_state_file):
    r1 = SessionRegistry()
    r1.get_or_create(SESSION)
    r1.save()
    data = json.loads(tmp_state_file.read_text())
    for sd in data["sessions"].values():
        sd.pop("context_warned_conversation", None)
    tmp_state_file.write_text(json.dumps(data))
    r2 = SessionRegistry()
    r2.load()
    assert r2.get(SESSION).context_warned_conversation == ""
