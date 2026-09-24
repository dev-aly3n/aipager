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
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import enforce
from aipager.dtach import hook_receiver as hr
from aipager.dtach import notify_hook as nh
from aipager import policy_snapshot as ps
from aipager.state import SessionRegistry, Status

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


def _strip_result_line(markdown: str) -> str:
    """Every answer now opens with its session's result line — `💬 **name**`,
    or the stats form when no card remains (contract change
    "session-name-on-every-message"). These tests pin the BODY that follows
    it, so the line is peeled off at capture time."""
    first, sep, rest = markdown.partition("\n\n")
    return rest if sep and first.startswith("💬 ") else markdown


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
        rich_sends.append(_strip_result_line(content))
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
    # Roadmap 8.42 (operator decision 2026-09-24) reversed "one response
    # per background job": the interim answer goes out NOW, on its own,
    # ending with the "⏳ 1 agent still running" line.
    assert len(rich_sends) == 1
    assert rich_sends[0].startswith(INTERIM_ANSWER + "\n\n⏳ 1 agent still")

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
    # Identical stray content: refused by the delivered-digest ring.
    assert len(rich_sends) == 1

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

    # ── Assertions on this sequence (roadmap 8.42) ──
    #  two standalone messages for the whole job: the interim, sent at
    #  step 3, and the briefing on its own — the interim never again.
    assert len(rich_sends) == 2
    assert rich_sends[1] == REAL_BRIEFING
    assert sum(INTERIM_ANSWER in m for m in rich_sends) == 1

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
        rich_sends.append(_strip_result_line(content))
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
    # Roadmap 8.42: the interim answer goes out at once, on its own.
    assert len(rich_sends) == 1
    assert rich_sends[0].startswith(INTERIM_ANSWER)
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
    assert len(rich_sends) == 1  # the stray is not re-sent

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
    # The briefing goes out on its own; the interim is not sent again.
    assert len(rich_sends) == 2
    assert rich_sends[1] == REAL_BRIEFING
    finals = [c for c in edit_calls if c["final"]]
    assert len(finals) == 1
    assert edit_calls[-1]["final"] is True


def test_final_path_dedup_suppresses_stale_redelivery(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Requirement 3: content identical to the last delivered summary is
    never re-posted, even on the FINAL (job-closed) path. The single hash
    still resets at a genuine new turn, but the delivered-digest ring does
    not: a body that already went out this session is never re-posted
    across turns either — the live triple delivery was exactly a text-less
    turn whose transcript still ended on the previous answer. A genuinely
    different answer delivers as before."""
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
        rich_sends.append(_strip_result_line(content))
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
    # the interim goes out at once (roadmap 8.42), and the close refuses
    # the identical briefing — that content is delivered exactly once.
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=1b]\ndo a background thing",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="SubagentStart",
          agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sum(INTERIM_ANSWER in m for m in rich_sends) == 1  # sent now
    _send(recv, run_async, hook_event_name="SubagentStop",
          agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt=(f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                  "done."), transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sum(INTERIM_ANSWER in m for m in rich_sends) == 1  # no dup

    # A turn started by a PreToolUse after a LOST UserPromptSubmit
    # datagram is a genuine new turn (review rev-iter1-002): the
    # transition-level reset clears the single hash — but the ring of
    # delivered digests survives it, so an answer byte-identical to one
    # already posted this session stays suppressed.
    _send(recv, run_async, hook_event_name="PreToolUse",
          tool_name="Bash", tool_input={"command": "ls"})
    assert sess.last_idle_summary_hash == ""
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sum(INTERIM_ANSWER in m for m in rich_sends) == 1

    # A genuine new prompt: same rule — the repeat stays suppressed …
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=2]\ndo the thing again",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message=INTERIM_ANSWER, transcript_path=str(tp))
    assert sum(INTERIM_ANSWER in m for m in rich_sends) == 1

    # … while a genuinely different answer still delivers.
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt="[via Telegram msg=3]\nsomething else entirely",
          transcript_path=str(tp))
    _send(recv, run_async, hook_event_name="Stop",
          last_assistant_message="z" * 300, transcript_path=str(tp))
    assert rich_sends.count("z" * 300) == 1


def test_task_notification_after_restart_still_sends_a_busy_card(
    mk_bot, run_async, tmp_state_file,
):
    """Root cause of "no busy card after restart" (intent.md Mechanism,
    last bullet): a daemon restart persists ``busy_msg_id`` (it's in
    ``_PERSIST_FIELDS``) but never ``status`` or ``active_subagents`` —
    ``SessionRegistry.load()`` always reconstructs GONE/UNKNOWN, and the
    session monitor's socket-reappear scan recovers that straight to
    IDLE. The stale, pre-crash card (msg_id restored from disk) sits on
    the reloaded session's live-message stack the whole time.

    The turns observed live right after the 12:00 restart were BOTH a
    self-triggered ``<task-notification>`` continuation, not a fresh
    human prompt — and the OLD dispatch treated every task-notification
    as "the same job waking itself up" unconditionally, entering BUSY
    with ``preserve_job_state=True`` and dispatching ``job_continuation``,
    which deliberately never calls ``_send_busy_and_animate`` (it expects
    a live card to re-render). With no job state surviving the restart,
    that produced a card-less BUSY turn whose eventual answer then split
    into a bare header plus a body (requirement 2's other symptom).

    The fix (hook_receiver.py's UserPromptSubmit branch) only takes the
    continuation path when the daemon actually has something to
    continue (BUSY/INTERACTIVE, or a background job still open); with
    neither, it starts a genuine fresh turn — dispatching
    ``user_prompt_submit`` — so a real busy card goes out and settles the
    stale one restored from disk."""
    # ---- pre-crash: a session mid-turn with a live busy card ----
    pre = SessionRegistry()
    pre_sess = pre.get_or_create(SESSION)
    pre_sess.label = SESSION
    pre_sess.status = Status.BUSY
    pre_sess.busy_msg_id = 3515  # the pre-restart card
    pre_sess.gone_at = time.time() - 3600  # unrelated earlier disconnect
    pre.save()

    # ---- daemon restarts: a fresh registry loads the saved file ----
    bot = mk_bot()
    registry = bot.registry
    registry.load()
    sess = registry.get(SESSION)
    assert sess is not None
    assert sess.busy_msg_id == 3515  # stale card survived the restart
    assert sess.status in (Status.GONE, Status.UNKNOWN)
    assert sess.active_subagents == {}  # job state did NOT survive

    # ---- session monitor's socket-reappear recovery ("GONE → IDLE") ----
    if sess.status == Status.GONE:
        sess.gone_at = None
    registry.transition(SESSION, Status.IDLE)
    assert sess.status == Status.IDLE
    assert sess.busy_msg_id == 3515  # untouched by the IDLE recovery itself

    # ---- mock the Telegram transport only ----
    sent: list[str] = []

    async def _send_message(chat_id, text, **kwargs):
        sent.append(text)
        return MagicMock(message_id=9999)
    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.send_chat_action = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()

    recv = hr.HookReceiver(registry, bot.notify)

    # ---- the self-triggered <task-notification> continuation arrives ----
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          prompt=f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                 "Background agent finished.",
          transcript_path="")

    assert sess.status == Status.BUSY
    assert sess.job_continuation_active is False  # a fresh turn, not a continuation
    # The stale, pre-restart card is settled and forgotten as before...
    assert sess.busy_msg_id is None
    # ...but a self-woken turn's own card is DEFERRED until it is earned
    # (roadmap 8.32): nothing is sent at the prompt.
    assert not any("Thinking" in t for t in sent)
    assert sess.lazy_card_at > 0

    # The turn's first tool call earns it: the real busy card goes out.
    _send(recv, run_async, hook_event_name="PreToolUse", tool_name="Bash",
          tool_input={"command": "ls"}, transcript_path="")
    assert any("Thinking" in t for t in sent)  # a real busy card went out
    assert sess.busy_msg_id == 9999  # superseded the stale, pre-restart one
    assert sess.lazy_card_at == 0.0


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
        rich_sends.append(_strip_result_line(content))
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
    # Roadmap 8.42: three messages, each sent as it was written, in order,
    # none repeated — the final one carries only the final answer.
    assert len(rich_sends) == 3
    assert rich_sends[0].startswith("interim one\n\n⏳")
    assert rich_sends[1].startswith("interim two\n\n⏳")
    assert rich_sends[2] == "the real final answer"
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
        rich_sends.append(_strip_result_line(content))
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


def _delivered_interim_sess(bot, run_async, monkeypatch, rich_sends,
                            msg_id=888):
    """A session waiting on a job whose interim answer already went out
    (roadmap 8.42)."""
    sess = bot.registry.get_or_create(SESSION)
    sess.status = Status.IDLE
    sess.label = SESSION
    sess.busy_msg_id = msg_id
    sess.busy_started_at = 1.0
    sess.trigger_msg_id = TRIGGER_MSG_ID
    sess.scope_chat_id = -1001
    sess.job_interim_seen = True
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": 1.0}

    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(_strip_result_line(content))
        return {"message_id": 9001 + len(rich_sends)}
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send_rich)
    run_async(bot._deliver_job_interim(sess, "interim work", 0.0))
    assert rich_sends == ["interim work"]
    return sess


@pytest.mark.parametrize("close", ["job_grace_expired", "job_agents_lost",
                                   "stop", "api_error", "session_end",
                                   "kill"])
def test_no_close_path_sends_the_interim_again(
    mk_bot, run_async, monkeypatch, close,
):
    """Until roadmap 8.42 each of these close paths flushed the job's
    buffered interim answers (``_flush_job_buffer``). 8.42 (operator decision 2026-09-24) sends an interim
    the moment its turn ends, so a close path owes nothing and must send
    none of it again."""
    bot = mk_bot()
    rich_sends: list[str] = []
    sess = _delivered_interim_sess(bot, run_async, monkeypatch, rich_sends)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9104))
    bot._app.bot.delete_message = AsyncMock(return_value=None)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    if close in ("job_grace_expired", "job_agents_lost"):
        sess.active_subagents.clear()
        run_async(bot.notify(sess, close, {}))
    elif close == "stop":
        run_async(bot._stop_session_core(sess))
        assert sess.active_subagents == {}
    elif close == "api_error":
        sess.active_subagents.clear()
        sess.job_continuation_active = True
        sess.last_prompt = "go"
        run_async(bot.notify(sess, "idle_prompt",
                             {"summary": "API Error: 529 overloaded"}))
    elif close == "session_end":
        run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    else:
        run_async(bot._kill_session_core(sess.name, sess.label))

    assert rich_sends == ["interim work"]
    for call in bot._app.bot.send_message.await_args_list:
        assert "interim work" not in str(call)


def test_an_overflowing_interim_and_the_final_answer_both_stay_visible(
    mk_bot, run_async, tmp_path, monkeypatch,
):
    """Was review rev-iter1-003's composed-overflow case (interim + final
    over the 32 KB ceiling in ONE message). Since roadmap 8.42 each goes out
    on its own: the oversized interim is cut to fit, with its full text
    attached, and the final answer is untouched by it."""
    bot = mk_bot()
    registry = bot.registry
    recv = hr.HookReceiver(registry, bot.notify)
    tp = tmp_path / "big.jsonl"
    _write_transcript(tp, with_continuation=False)

    rich_sends = []
    async def _send_rich(chat_id, content, **kw):
        rich_sends.append(_strip_result_line(content))
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

    big_interim = "INTERIM-" + ("x" * 40000)
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

    assert len(rich_sends) == 2
    interim, final = rich_sends
    assert interim.startswith("INTERIM-")
    assert len(interim.encode("utf-8")) <= 32768
    assert final == final_answer
    bot._app.bot.send_document.assert_called_once()  # the interim, in full
    doc = bot._app.bot.send_document.await_args
    assert doc.kwargs["filename"].endswith("_answer.txt")


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


# ---- the delivery rule (roadmap 8.42) -------------------------------------
#
# The orphaned-promise stripper (``_strip_promise_lines`` and its five
# tests) is gone with the interim buffer: an interim answer now goes out
# on its own, so "I'll send the results when the agent finishes" is true.

def test_style_text_carries_the_delivery_rule():
    """Every Telegram prompt tells Claude its reply is delivered now and
    the agents' results arrive later on their own (operator decision
    2026-09-24) — never the old "ONE message / never promise" rule."""
    from aipager import preferences as prefs
    text = prefs.style_text(prefs.Preferences(
        layout="card", simple_formatting=False,
        answer_length="none", language_level="none",
    ))
    assert "your reply is delivered now" in text
    assert "arrive later as a separate message" in text
    assert "ONE message" not in text
    assert "never promise" not in text


def test_job_grace_expired_without_a_card_sends_the_result_form(
    mk_bot, run_async,
):
    """With no live card to settle, the grace-expired notice is the turn's
    only message: it opens with the result glyph like every other result
    ("session-name-on-every-message"), while a settled CARD keeps ✅."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
    sess = bot.registry.get_or_create(SESSION)
    sess.status = Status.IDLE
    sess.label = SESSION
    sess.busy_msg_id = None
    sess.busy_started_at = 1.0

    run_async(bot.notify(sess, "job_grace_expired", {}))

    texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert texts and texts[0].startswith(f"💬 <b>{SESSION}</b> · Finished"), texts
    assert sess.trigger_msg_id is None
