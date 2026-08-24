"""Integration test — model Claude Code background-agent jobs.

Replays the EXACT "hiva" hook sequence from entrypoints.md / spec.md's
reproduced defect, end to end, through ``HookReceiver._on_datagram`` →
``TelegramBot.notify`` (real ``notify.py`` + ``animation.py`` logic; only
the Telegram HTTP transport and the safety enforcement's file I/O are
mocked). Per spec.md requirement 7 this test MUST fail on today's code
and pass after the fix.

The 11 steps, verbatim from entrypoints.md's payload shapes:
  1. Original real prompt (Telegram-marked) → BUSY.
  2. SubagentStart (Explore, agent_id=ab2ae82400fc97e4c).
  3. Interim Stop (foreground turn ends, agent still running) → IDLE,
     interim answer delivered once.
  4. Five phantom SubagentStops (empty type, unmatched ids).
  5. PreToolUse re-BUSY (background agent's own tool call).
  6. PostToolUse.
  7. Stray idle (byte-identical interim content) → IDLE again; delivery
     must be SKIPPED (hash match).
  8. PreToolUse again → BUSY.
  9. Real SubagentStop (matching id) → active_subagents empties.
  10. Continuation UserPromptSubmit (<task-notification> prefix).
  11. Final Stop → IDLE, real briefing delivered, ONE real Finished card.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import enforce
from aipager.dtach import hook_receiver as hr
from aipager.dtach import notify_hook as nh
from aipager import policy_snapshot as ps
from aipager.state import Status

SESSION = "hiva"
AGENT_ID = "ab2ae82400fc97e4c"
INTERIM_ANSWER = "x" * 2122
REAL_BRIEFING = "y" * 4997
TRIGGER_MSG_ID = 3420


def _send(recv, run_async, **fields):
    fields.setdefault("session", SESSION)
    payload = json.dumps(fields).encode()
    # HookReceiver's own datagram-level dedup (HOOK_DEDUP_WINDOW_SECONDS,
    # unrelated to this feature — a defence against a double-wired hook
    # entry in settings.json) would otherwise drop step 7's payload as a
    # "duplicate" of step 3's byte-identical one: real elapsed time
    # between the two hiva events was ~1 minute, but this test drives the
    # whole sequence in milliseconds. Clearing before every send is the
    # test-only equivalent of that elapsed time.
    recv._recent_fingerprints.clear()
    run_async(recv._on_datagram(payload))


def _write_transcript(tp: Path, *, with_continuation: bool) -> None:
    lines = [
        {"type": "user", "message": {"role": "user", "content":
         "[via Telegram msg=123]\nanalyze X and web-search Y"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Task",
             "input": {"description": "Explore"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text",
                 "text": '{"isAsync": true, "status": "async_launched"}'}]}]}},
    ]
    if with_continuation:
        lines.append({"type": "user", "message": {"content":
            f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
            "Background agent finished."}})
    tp.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


@pytest.fixture
def _sent_messages():
    return []


def test_hiva_sequence_replayed_end_to_end(mk_bot, run_async, tmp_path, monkeypatch):
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)

    tp = tmp_path / "hiva.jsonl"
    _write_transcript(tp, with_continuation=False)

    # ── mock the Telegram transport only ──
    sent_messages: list[dict] = []
    next_id = [1000]

    async def _send_message(chat_id, text, **kwargs):
        next_id[0] += 1
        sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return MagicMock(message_id=next_id[0])
    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.send_chat_action = AsyncMock(return_value=None)
    bot._app.bot.delete_message = AsyncMock(return_value=None)

    rich_sends: list[str] = []
    async def _send_rich(chat_id, content, **kwargs):
        rich_sends.append(content)
        return {"message_id": next_id[0]}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)

    edit_calls: list[dict] = []
    real_edit_busy_rich = bot._edit_busy_rich
    async def _spy_edit_busy_rich(sess_, verb, *, final=False, waiting=False):
        edit_calls.append({"verb": verb, "final": final, "waiting": waiting})
        return await real_edit_busy_rich(sess_, verb, final=final, waiting=waiting)
    bot._edit_busy_rich = _spy_edit_busy_rich

    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)

    # Force "card" layout so every disposal (waiting AND final) goes
    # through the spied _edit_busy_rich above, making the assertions below
    # independent of the ambient KEEP_FINISHED_CARD default.
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    # ---- step 1: original real prompt (Telegram-marked) ----
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=123]\nanalyze X and web-search Y",
          transcript_path=str(tp))
    sess = registry.get(SESSION)
    assert sess is not None
    assert sess.status == Status.BUSY
    assert sess.last_prompt_origin == "telegram"
    # The Telegram-side handler stamps these before the hook fires in
    # production; this test drives only the hook side, so seed them the
    # same way the real _handle_message call would.
    sess.trigger_msg_id = TRIGGER_MSG_ID
    sess.scope_chat_id = -1001
    busy_started_at_0 = sess.busy_started_at
    busy_started_wall_0 = sess.busy_started_wall
    assert busy_started_at_0 > 0
    assert busy_started_wall_0 > 0

    # ---- step 2: SubagentStart ----
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id=AGENT_ID, agent_type="Explore")
    assert AGENT_ID in sess.active_subagents
    assert sess.active_subagents[AGENT_ID]["type"] == "Explore"
    assert sess.subagent_count_this_turn == 1

    # ---- step 3: interim Stop (foreground ends, agent still running) ----
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is True
    assert rich_sends == [INTERIM_ANSWER]  # delivered once so far

    # ---- step 4: five phantom SubagentStops ----
    tool_history_len_before = len(sess.tool_history)
    for i in range(1, 6):
        _send(recv, run_async, hook_event_name="SubagentStop",
              agent_id=f"unknown-{i}", agent_type="")
    assert AGENT_ID in sess.active_subagents  # untouched
    assert len(sess.tool_history) == tool_history_len_before  # no noise rows

    # ---- step 5: PreToolUse re-BUSY (background agent's own tool call) ----
    _send(recv, run_async, hook_event_name="PreToolUse",
          tool_name="Bash", tool_input={"command": "ls"})
    assert sess.status == Status.BUSY
    assert sess.busy_started_at == busy_started_at_0
    assert sess.busy_started_wall == busy_started_wall_0
    assert sess.trigger_msg_id == TRIGGER_MSG_ID

    # ---- step 6: PostToolUse ----
    _send(recv, run_async, hook_event_name="PostToolUse",
          tool_name="Bash", tool_input={"command": "ls"})

    # ---- step 7: stray idle, byte-identical interim content ----
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is True
    # SKIPPED — hash match. Still exactly one delivered so far.
    assert rich_sends == [INTERIM_ANSWER]

    # ---- step 8: PreToolUse again → BUSY ----
    _send(recv, run_async, hook_event_name="PreToolUse",
          tool_name="Bash", tool_input={"command": "ls"})
    assert sess.status == Status.BUSY
    assert sess.busy_started_at == busy_started_at_0
    assert sess.busy_started_wall == busy_started_wall_0

    # ---- step 9: real SubagentStop (matching id) ----
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id=AGENT_ID, agent_type="Explore")
    assert AGENT_ID not in sess.active_subagents
    assert sess.job_background_open() is False

    # ---- step 10: continuation UserPromptSubmit ----
    _write_transcript(tp, with_continuation=True)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt=(f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                  "Background agent finished."),
          transcript_path=str(tp))
    # origin never flips to terminal — the exact spec.md safety leak.
    assert sess.last_prompt_origin == "telegram"
    assert sess.trigger_msg_id == TRIGGER_MSG_ID  # unchanged
    assert sess.subagent_count_this_turn == 1  # not reset by the continuation
    assert sess.busy_started_at == busy_started_at_0
    assert sess.busy_started_wall == busy_started_wall_0

    # ---- step 11: final Stop ----
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=REAL_BRIEFING, transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is False

    # ── Assertions the black-box test makes on this sequence ──
    #  exactly one interim message delivered (step 3, suppressed at step 7),
    #  and the real briefing delivered once at the true end.
    assert rich_sends.count(INTERIM_ANSWER) == 1
    assert rich_sends.count(REAL_BRIEFING) == 1
    assert rich_sends == [INTERIM_ANSWER, REAL_BRIEFING]

    #  zero "Finished" headers before step 11; exactly one Finished (final)
    #  card render, and it is the LAST edit in the whole sequence.
    finals = [c for c in edit_calls if c["final"]]
    assert len(finals) == 1
    assert edit_calls[-1]["final"] is True

    #  the waiting frame WAS rendered at least once during the interim
    #  window (steps 3/7), and never simultaneously with final=True.
    waitings = [c for c in edit_calls if c["waiting"]]
    assert len(waitings) >= 1
    assert all(not c["final"] for c in waitings)

    #  enforce.decide() against the transcript after step 10 never treats
    #  origin as terminal — spec.md's documented safety leak (b).
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: {
        "bypass_safety": False, "deny_tools": ["Bash"], "allow_tools": [],
        "deny_paths_no_access": [], "deny_paths_no_write": [],
        "deny_bash_patterns": [],
    })
    block = enforce.decide({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "ls"}, "session": SESSION,
        "transcript_path": str(tp),
    })
    assert block is not None
    assert "deny_tools" in block["reason"]
    assert enforce._origin_from_transcript(str(tp)) == "telegram"


def test_hiva_continuation_never_touches_policy_snapshot(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """The notify_hook.py half of spec.md's safety leak (c): a
    <task-notification> UserPromptSubmit must never call
    _match_and_promote or rewrite the pinned policy snapshot."""
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(nh, "SOCKET_PATH", str(tmp_path / "nope.sock"))

    ps.write_merged_snapshot(SESSION, {
        "bypass_safety": False, "deny_tools": ["Bash"], "allow_tools": [],
        "deny_paths_no_access": [], "deny_paths_no_write": [],
        "deny_bash_patterns": [],
    })
    snap_path = ps.snapshot_path(SESSION)
    before_mtime = snap_path.stat().st_mtime_ns
    before_content = snap_path.read_text()

    real_match_and_promote = nh._match_and_promote
    calls: list[tuple] = []
    def _spy(*a, **k):
        calls.append((a, k))
        return real_match_and_promote(*a, **k)
    monkeypatch.setattr(nh, "_match_and_promote", _spy)

    stdin_payload = json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": (f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                   "Background agent finished."),
        "session": SESSION,
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_payload))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)

    nh.main()

    assert calls == []  # _match_and_promote must never run for a continuation
    after_mtime = snap_path.stat().st_mtime_ns
    after_content = snap_path.read_text()
    assert after_mtime == before_mtime
    assert after_content == before_content
