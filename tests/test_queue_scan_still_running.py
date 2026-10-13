"""An agent's <task-notification> taken mid-turn fires no UserPromptSubmit:
the card's queue scan reads its still-running note from the transcript's
queue-operation lines (agents-line-waiting design.md S2)."""

from __future__ import annotations

import json

from aipager.bot import animation
from aipager.state import TrackedSession

P = "a505c081ca3926ed5"

_NOTE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<summary>Agent \"Run the pipeline\" finished</summary>\n"
    "<note>This agent stopped with background work of its own still "
    "running. It may resume on its own when that work completes or "
    "reports, and the same task-id notifies again if it does; the result "
    "below may be interim.</note>\n<result>Waiting.</result>\n"
    "</task-notification>")
_DONE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>A task-notification fires each time this agent stops "
    "with no live background children of its own.</note>\n"
    "</task-notification>")


def _queue_lines(path, content: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for op, reason in (("enqueue", None), ("remove", "absorbed_mid_turn")):
            entry = {"type": "queue-operation", "operation": op,
                     "content": content,
                     "timestamp": "2026-10-12T10:10:04.000Z"}
            if reason:
                entry["reason"] = reason
            fh.write(json.dumps(entry) + "\n")


def _stopped_agent(tmp_path) -> tuple[TrackedSession, dict]:
    sess = TrackedSession(name="claude-frontend", label="frontend")
    sess.stream_transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    sess.bg_agent_started(P, "pipeline-runner", 100.0)
    rec = {"chat_id": 1, "msg_id": 2, "ids": [P], "ends": {}}
    sess.add_agents_line(rec)
    sess.bg_agent_stopped(P, 101.0)
    assert rec["ends"][P]["outcome"] == "done"
    return sess, rec


def test_a_mid_turn_still_running_note_revives_the_agent(tmp_path):
    sess, rec = _stopped_agent(tmp_path)
    _queue_lines(sess.stream_transcript_path, _NOTE.format(tid=P))
    animation._scan_queue_operations(sess)
    assert sess.bg_agent_labels() == ["pipeline-runner"]
    assert rec["ends"] == {}


def test_a_mid_turn_final_note_revives_nothing(tmp_path):
    sess, rec = _stopped_agent(tmp_path)
    _queue_lines(sess.stream_transcript_path, _DONE.format(tid=P))
    animation._scan_queue_operations(sess)
    assert sess.bg_agent_labels() == []
    assert P in rec["ends"]


def test_a_note_for_an_unknown_id_adds_nothing(tmp_path):
    sess, _rec = _stopped_agent(tmp_path)
    _queue_lines(sess.stream_transcript_path, _NOTE.format(tid="bhvkhjl4x"))
    animation._scan_queue_operations(sess)
    assert sess.bg_agent_labels() == []
