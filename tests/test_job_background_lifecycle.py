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
    # Contract change ("one response per background job"): the interim is
    # RECORDED, never sent standalone — the card's timeline is its live
    # surface and the single final message its delivery.
    assert rich_sends == []
    assert sess.job_interim_buffer == [INTERIM_ANSWER]

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
    # Identical stray content: recorded once, still nothing sent.
    assert rich_sends == []
    assert sess.job_interim_buffer == [INTERIM_ANSWER]

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
    # The table is empty but the job stays OPEN: an interim idle already
    # happened, so the <task-notification> continuation is imminent and
    # the grace window keeps a stray idle from closing the job here
    # ("close the background-job endgame" requirement 2 — the ishaq
    # endgame test covers the stray-idle case itself).
    assert sess.job_background_open() is True

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

    # ── Assertions on this sequence ("one response per background job") ──
    #  exactly ONE standalone message for the whole job, carrying the
    #  interim material and the briefing together, interim first.
    assert len(rich_sends) == 1
    combined = rich_sends[0]
    assert INTERIM_ANSWER in combined
    assert REAL_BRIEFING in combined
    assert combined.index(INTERIM_ANSWER) < combined.index(REAL_BRIEFING)

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


def test_ishaq_endgame_job_stays_open_through_continuation(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """The observed ishaq endgame (2026-08-24, live): the real SubagentStop
    empties the table BEFORE the continuation turn runs, and today the very
    next idle takes the Finished path — premature "Done" card, stale
    re-delivery, headerless briefing. After the fix the job stays open
    across the gap (grace) and through the continuation turn
    (job_continuation_active); the continuation's own Stop is the one true
    Finished."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)

    tp = tmp_path / "ishaq.jsonl"
    _write_transcript(tp, with_continuation=False)

    sent_messages: list[dict] = []
    next_id = [2000]

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
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    # steps 1-3: prompt → agent → interim Stop (agents open)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=123]\nanalyze X and web-search Y",
          transcript_path=str(tp))
    sess = registry.get(SESSION)
    sess.trigger_msg_id = TRIGGER_MSG_ID
    sess.scope_chat_id = -1001
    busy_started_at_0 = sess.busy_started_at
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    # Contract change ("one response per background job"): nothing goes
    # out standalone during the job-open window.
    assert rich_sends == []
    assert sess.job_interim_buffer == [INTERIM_ANSWER]
    assert sess.job_background_open() is True

    # step 4: the real SubagentStop empties the table — but an interim
    # idle has already happened, so the continuation is imminent and the
    # job must STAY open (grace window), not close.
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id=AGENT_ID, agent_type="Explore")
    assert AGENT_ID not in sess.active_subagents
    assert sess.job_background_open() is True

    # step 5: a stray idle event in the gap (the monitor-recovery /
    # duplicate-Stop class) must take the interim path, not finish.
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert [c for c in edit_calls if c["final"]] == []
    assert rich_sends == []  # still nothing standalone
    assert sess.job_interim_buffer == [INTERIM_ANSWER]

    # step 6: the continuation turn arrives and takes over from the grace.
    _write_transcript(tp, with_continuation=True)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt=(f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                  "Background agent finished."),
          transcript_path=str(tp))
    assert sess.status == Status.BUSY
    assert sess.job_continuation_active is True
    assert sess.job_background_open() is True
    assert sess.busy_started_at == busy_started_at_0

    # step 7: the continuation's own Stop = the one true Finished.
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=REAL_BRIEFING, transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_continuation_active is False
    assert sess.job_background_open() is False
    # ONE message for the whole job: interim + briefing, interim first.
    assert len(rich_sends) == 1
    assert INTERIM_ANSWER in rich_sends[0]
    assert REAL_BRIEFING in rich_sends[0]
    assert rich_sends[0].index(INTERIM_ANSWER) < rich_sends[0].index(REAL_BRIEFING)
    assert sess.job_interim_buffer == []
    finals = [c for c in edit_calls if c["final"]]
    assert len(finals) == 1
    assert edit_calls[-1]["final"] is True


def test_final_path_dedup_suppresses_stale_redelivery(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Requirement 3: content identical to the last delivered summary is
    never re-posted, even on the FINAL (job-closed) path — and the hash
    resets at a genuine new turn, so a legitimately repeated answer across
    two real turns still delivers."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)
    tp = tmp_path / "ishaq.jsonl"
    _write_transcript(tp, with_continuation=False)

    next_id = [3000]
    async def _send_message(chat_id, text, **kwargs):
        next_id[0] += 1
        return MagicMock(message_id=next_id[0])
    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.send_chat_action = AsyncMock(return_value=None)
    bot._app.bot.delete_message = AsyncMock(return_value=None)
    rich_sends: list[str] = []
    async def _send_rich(chat_id, content, **kwargs):
        rich_sends.append(content)
        return {"message_id": next_id[0]}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    # A plain finished turn delivers the answer once.
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ndo the thing", transcript_path=str(tp))
    sess = registry.get(SESSION)
    sess.scope_chat_id = -1001
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=REAL_BRIEFING, transcript_path=str(tp))
    assert rich_sends.count(REAL_BRIEFING) == 1

    # A background job whose briefing is byte-identical to the interim:
    # the interim is never pre-sent ("one response per background job"),
    # so the close delivers that content exactly once, un-duplicated.
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1b]\ndo a background thing",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert rich_sends.count(INTERIM_ANSWER) == 0  # buffered, not sent
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt=(f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                  "done."), transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert rich_sends.count(INTERIM_ANSWER) == 1  # once, at close, no dup

    # A turn started by a PreToolUse after a LOST UserPromptSubmit
    # datagram is a genuine new turn (review rev-iter1-002): the
    # transition-level reset clears the hash, so an answer legitimately
    # identical to the previous turn's still delivers.
    _send(recv, run_async, hook_event_name="PreToolUse",
          tool_name="Bash", tool_input={"command": "ls"})
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert rich_sends.count(INTERIM_ANSWER) == 2

    # A genuine new prompt resets the hash too — the same answer to a
    # repeated question still delivers.
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=2]\ndo the thing again",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert rich_sends.count(INTERIM_ANSWER) == 3


def test_job_grace_expired_finalizes_card_as_plain_finished(
    mk_bot, run_async, monkeypatch,
):
    """The job_grace_expired handler closes the card as an honest plain
    Finished (the interim answer stands as the result) — not the
    "background agent lost" wording, which is for agents that vanished
    without reporting."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(SESSION)
    sess.status = Status.IDLE
    sess.label = SESSION
    sess.busy_msg_id = 777
    sess.busy_started_at = 1.0

    edits: list[str] = []
    async def _edit_raw(msg_id, text, chat_id=None):
        edits.append(text)
        return True
    bot._edit_busy_raw = _edit_raw

    run_async(bot.notify(sess, "job_grace_expired", {}))

    assert len(edits) == 1
    assert "Finished" in edits[0]
    assert "lost" not in edits[0]
    assert sess.busy_msg_id is None
    assert sess.trigger_msg_id is None


def test_double_hop_continuation_spawning_new_agents(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Reviewer coverage gap: a continuation turn that spawns NEW
    background agents and then stops demotes to plain interim (waiting
    again), and the SECOND continuation cycle closes the job normally."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)
    tp = tmp_path / "hop.jsonl"
    _write_transcript(tp, with_continuation=False)

    next_id = [4000]
    async def _send_message(chat_id, text, **kwargs):
        next_id[0] += 1
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
    real_edit = bot._edit_busy_rich
    async def _spy(sess_, verb, *, final=False, waiting=False):
        edit_calls.append({"final": final, "waiting": waiting})
        return await real_edit(sess_, verb, final=final, waiting=waiting)
    bot._edit_busy_rich = _spy
    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    # hop 1
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = registry.get(SESSION)
    sess.scope_chat_id = -1001
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id="agent-1", agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="interim one", transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id="agent-1", agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="<task-notification>\n<task-id>agent-1</task-id>\ndone.",
          transcript_path=str(tp))
    assert sess.job_continuation_active is True
    # the continuation spawns a NEW background agent, then stops
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id="agent-2", agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="interim two", transcript_path=str(tp))
    # demoted to plain waiting: continuation flag cleared, no Finished yet
    assert sess.job_continuation_active is False
    assert sess.job_background_open() is True
    assert [c for c in edit_calls if c["final"]] == []
    # hop 2 closes normally
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id="agent-2", agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="<task-notification>\n<task-id>agent-2</task-id>\ndone.",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="the real final answer",
          transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is False
    # One message for the whole double-hop job, all three parts in order.
    assert len(rich_sends) == 1
    combined = rich_sends[0]
    assert combined.index("interim one") < combined.index("interim two")
    assert combined.index("interim two") < combined.index("the real final answer")
    finals = [c for c in edit_calls if c["final"]]
    assert len(finals) == 1


def test_real_prompt_during_grace_supersedes_and_late_continuation_closes(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Reviewer walk (review-2): a REAL prompt arriving during the grace
    window supersedes the job (transition's genuine-new-turn branch clears
    the endgame state and arms the reclaim), and the superseded job's late
    <task-notification> — arriving mid-new-turn — must not produce a rogue
    Finished or a stuck waiting card: the new turn's Stop closes normally,
    exactly once."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)
    tp = tmp_path / "sup.jsonl"
    _write_transcript(tp, with_continuation=False)

    next_id = [5000]
    async def _send_message(chat_id, text, **kwargs):
        next_id[0] += 1
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
    real_edit = bot._edit_busy_rich
    async def _spy(sess_, verb, *, final=False, waiting=False):
        edit_calls.append({"final": final, "waiting": waiting})
        return await real_edit(sess_, verb, final=final, waiting=waiting)
    bot._edit_busy_rich = _spy
    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    # job with an interim + armed grace
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = registry.get(SESSION)
    sess.scope_chat_id = -1001
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id="agent-1", agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="interim", transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id="agent-1", agent_type="Explore")
    assert sess.job_background_open() is True  # grace armed

    # a REAL prompt supersedes the job
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=2]\nnew business",
          transcript_path=str(tp))
    assert sess.status == Status.BUSY
    assert sess.job_grace_until == 0.0
    assert sess.job_interim_seen is False

    # the old job's late task-notification lands mid-new-turn
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="<task-notification>\n<task-id>agent-1</task-id>\ndone.",
          transcript_path=str(tp))
    # the next Stop closes cleanly — one Finished, no stuck waiting card
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="new answer", transcript_path=str(tp))
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is False
    assert sess.job_continuation_active is False
    assert rich_sends[-1] == "new answer"
    assert [c for c in edit_calls if c["final"]] != []


def _buffered_sess(bot, msg_id=888):
    sess = bot.registry.get_or_create(SESSION)
    sess.status = Status.IDLE
    sess.label = SESSION
    sess.busy_msg_id = msg_id
    sess.busy_started_at = 1.0
    sess.trigger_msg_id = TRIGGER_MSG_ID
    sess.scope_chat_id = -1001
    sess.job_interim_seen = True
    sess.job_interim_buffer.append("buffered interim work")
    return sess


def test_grace_expired_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    """"one response per background job" requirement 4: the buffer is the
    only full copy of the job's output when no continuation ever arrives —
    job_grace_expired must deliver it."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9001}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    async def _edit_raw(msg_id, text, chat_id=None):
        return True
    bot._edit_busy_raw = _edit_raw

    run_async(bot.notify(sess, "job_grace_expired", {}))

    assert rich_sends == ["buffered interim work"]
    assert sess.job_interim_buffer == []


def test_agents_lost_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _buffered_sess(bot)
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9002}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    async def _edit_raw(msg_id, text, chat_id=None):
        return True
    bot._edit_busy_raw = _edit_raw

    run_async(bot.notify(sess, "job_agents_lost", {}))

    assert rich_sends == ["buffered interim work"]
    assert sess.job_interim_buffer == []


def test_stop_during_wait_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    """An operator stopping a waiting job still receives what it
    produced."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": 1.0}
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9003}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9004))

    run_async(bot._stop_session_core(sess))

    assert rich_sends == ["buffered interim work"]
    assert sess.job_interim_buffer == []
    assert sess.active_subagents == {}


def test_supersede_clears_buffer_without_flush(mk_bot, run_async):
    """A genuine new prompt superseding the job clears the buffer WITHOUT
    delivering it (documented tradeoff: the superseded card's last render
    stays in scrollback; the operator chose to move on)."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    got = bot.registry.transition(sess.name, Status.BUSY)
    assert got is not None
    assert sess.job_interim_buffer == []


def test_api_error_final_still_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    """Review rev-iter1-001: an API-error-shaped final turn must not eat
    the buffered interim answers — they flush in the error branch."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    sess.status = Status.IDLE
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9101}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9102))
    bot._app.bot.delete_message = AsyncMock(return_value=None)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    sess.last_prompt = "go"

    run_async(bot.notify(sess, "idle_prompt",
                         {"summary": "API Error: 529 overloaded"}))

    assert rich_sends == ["buffered interim work"]
    assert sess.job_interim_buffer == []


def test_session_end_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    """Review rev-iter1-002: a session dying out from under a job still
    delivers what the job produced."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9103}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9104))
    bot._app.bot.delete_message = AsyncMock(return_value=None)

    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))

    assert rich_sends == ["buffered interim work"]
    assert sess.job_interim_buffer == []


def test_kill_flushes_the_buffer(mk_bot, run_async, monkeypatch):
    """Review rev-iter1-002: /kill delivers buffered work before the
    session record is destroyed."""
    bot = mk_bot()
    sess = _buffered_sess(bot)
    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9105}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9106))
    bot._app.bot.delete_message = AsyncMock(return_value=None)

    run_async(bot._kill_session_core(sess.name, sess.label))

    assert rich_sends == ["buffered interim work"]


def test_composed_overflow_keeps_the_final_answer_visible(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Review rev-iter1-003: when interim + final overflow the 32 KB
    message ceiling, the VISIBLE message keeps the tail (the actual final
    answer); the .txt attachment carries the full chronological text."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)
    tp = tmp_path / "big.jsonl"
    _write_transcript(tp, with_continuation=False)

    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(content)
        return {"message_id": 9107}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9108))
    bot._app.bot.send_document = AsyncMock(return_value=MagicMock(message_id=9109))
    bot._app.bot.send_chat_action = AsyncMock(return_value=None)
    bot._app.bot.delete_message = AsyncMock(return_value=None)
    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)

    big_interim = "INTERIM-" + ("x" * 30000)
    final_answer = "FINAL-ANSWER-" + ("y" * 8000)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = registry.get(SESSION)
    sess.scope_chat_id = -1001
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id="agent-1", agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=big_interim, transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id="agent-1", agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="<task-notification>\n<task-id>agent-1</task-id>\ndone.",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=final_answer, transcript_path=str(tp))

    assert len(rich_sends) == 1
    visible = rich_sends[0]
    assert len(visible.encode("utf-8")) <= 32768
    assert "FINAL-ANSWER-" in visible  # the answer survives in the preview
    bot._app.bot.send_document.assert_called_once()  # full text attached


def _drive_plain_turn(bot, recv, run_async, tp, answer, n_tools=0, commentary=None):
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = bot.registry.get(SESSION)
    sess.scope_chat_id = -1001
    for i in range(n_tools):
        sess.record_tool(f"Bash: {'t' * 250}-{i}", True)
    if commentary:
        sess.stream_commentary = list(commentary)
        sess.stream_hook_live = True
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=answer, transcript_path=str(tp))
    return sess


def _attachment_harness(mk_bot, monkeypatch):
    bot = mk_bot()
    recv = hr.HookReceiver(bot.registry, bot.notify)
    docs = []
    async def _send_document(chat_id, document=None, filename=None, **kw):
        docs.append({"filename": filename, "content": document.read().decode()})
        return MagicMock(message_id=9200)
    bot._app.bot.send_document = AsyncMock(side_effect=_send_document)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9201))
    bot._app.bot.send_chat_action = AsyncMock(return_value=None)
    bot._app.bot.delete_message = AsyncMock(return_value=None)
    async def _send_rich(chat_id, content, **kw):
        return {"message_id": 9202}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    async def _edit_rich_transport(chat_id, msg_id, markdown, **kwargs):
        return {}
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        _edit_rich_transport)
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    return bot, recv, docs


def test_full_log_attached_when_final_card_hid_rows(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """"layered-card-shedding" requirement 2: a final card that had to
    collapse or remove anything ships the complete play-by-play as ONE
    .txt — commentary, tool rows, and the answer all inside."""
    bot, recv, docs = _attachment_harness(mk_bot, monkeypatch)
    tp = tmp_path / "t.jsonl"
    _write_transcript(tp, with_continuation=False)
    commentary = [(200, "NARRATIVE-BLOCK " + "c" * 200)]
    _drive_plain_turn(bot, recv, run_async, tp, "short answer",
                      n_tools=400, commentary=commentary)

    assert len(docs) == 1
    assert docs[0]["filename"] == f"{SESSION}_full_log.txt"
    body = docs[0]["content"]
    assert "NARRATIVE-BLOCK" in body
    assert "-399" in body          # every tool row, even hidden ones
    assert "short answer" in body  # and the answer


def test_no_attachment_on_a_small_clean_turn(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    bot, recv, docs = _attachment_harness(mk_bot, monkeypatch)
    tp = tmp_path / "t.jsonl"
    _write_transcript(tp, with_continuation=False)
    _drive_plain_turn(bot, recv, run_async, tp, "tiny answer", n_tools=3)
    assert docs == []


def test_full_log_preserves_failed_tool_rows(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Review rev-iter1-003: the play-by-play snapshot is taken BEFORE the
    close marks every row done, so a failed tool row reads [x] in the
    file, not [v]."""
    bot, recv, docs = _attachment_harness(mk_bot, monkeypatch)
    tp = tmp_path / "t.jsonl"
    _write_transcript(tp, with_continuation=False)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = bot.registry.get(SESSION)
    sess.scope_chat_id = -1001
    for i in range(200):
        sess.record_tool(f"Bash: {'q' * 250}-{i}", True)
    sess.record_tool("Bash: THE-FAILING-ONE", "failed")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="ans", transcript_path=str(tp))
    assert len(docs) == 1
    body = docs[0]["content"]
    assert "[x] Bash: THE-FAILING-ONE" in body


def test_merged_layout_truncated_final_still_attaches_log(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Review rev-iter1-002: the merged layout's own final render reports
    truncation, so a merged close whose card had to hide rows still ships
    the full-log attachment."""
    bot, recv, docs = _attachment_harness(mk_bot, monkeypatch)
    tp = tmp_path / "t.jsonl"
    _write_transcript(tp, with_continuation=False)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1]\ngo", transcript_path=str(tp))
    sess = bot.registry.get(SESSION)
    sess.scope_chat_id = -1001
    sess.override_layout = "merged"
    for i in range(200):
        sess.record_tool(f"Bash: {'w' * 250}-{i}", True)
    async def _edit_rich_ok(chat_id, msg_id, markdown, **kwargs):
        return {"message_id": msg_id}
    monkeypatch.setattr("aipager.bot.notify.edit_message_text_rich",
                        _edit_rich_ok)
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="tiny answer", transcript_path=str(tp))
    assert len(docs) == 1
    assert "tiny answer" in docs[0]["content"]
