"""A kill pressed at the terminal by a person (roadmap 8.121 F5,
requirement (d), design-6 section 4). No aipager Stop: Claude Code's
``agents_killed`` line and its wake are replayed. The agents are ended at
the wake's UserPromptSubmit (S2), the wake continues the job, and its Stop
is the job's one Finished: no "waiting" anywhere afterwards. When the line
is not flushed yet at the UserPromptSubmit, the wake is taken as a
terminal turn (as on main), and the Stop, whose ``background_tasks`` no
longer lists the agents, ends them (S3): Finished all the same."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from aipager.state import Status
from tests.two_prompts_rig import BG, OTHER

WAKE = '2 background agents were stopped by the user: "Review", "Review".'


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _line(path, entry):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _tasks(*ids):
    return [{"id": i, "type": "subagent", "status": "running", "description": "Review"}
            for i in ids]


def _events(rig):
    seen: list[str] = []
    real = rig.recv.notify_fn

    async def spy(sess, event, context=None):
        seen.append(event)
        return await real(sess, event, context or {})

    rig.recv.notify_fn = spy
    return seen


def _waiting(rig, transcript):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-other")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.",
             background_tasks=_tasks(BG, OTHER), transcript_path=str(transcript))
    assert rig.sess.shown_state().state == "tasks"
    return rig.sess


def test_a_terminal_kill_continues_and_finishes_the_job(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript)
    events = _events(rig)
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time())})
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    # Ended at the wake: no SubagentStop will come for them.
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert sess.job_continuation_active is True
    assert sess.status is Status.BUSY
    assert "job_continuation" in events
    assert events.count("subagent_stop") == 2
    assert sess.last_prompt_origin != "terminal"
    assert sess.turn_prompt != WAKE
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Stopped, as asked.",
             background_tasks=[], transcript_path=str(transcript))
    assert sess.status is Status.IDLE
    assert sess.job_background_open() is False
    assert sess.shown_state().state == "idle"
    assert sess.bg_agents_recent[BG]["ended_by"] == "killed"
    # The card: Working again for the wake, then the job's one Done, with
    # both agents' rows settled.
    assert "· Working" in rig.frames[-2]
    assert "<b>api</b> · Done" in rig.frames[-1]
    assert "⏳ `🤖" not in rig.frames[-1]


def test_a_line_before_the_last_stop_is_not_this_kill(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time() - 3600)})
    sess = _waiting(rig, transcript)
    sess.files_turn_closed_wall = time.time() - 60
    rig.hook("UserPromptSubmit", prompt="what is left to do?",
             transcript_path=str(transcript))
    assert sorted(sess.active_subagents) == sorted([BG, OTHER])
    assert sess.last_prompt_origin == "terminal"


def test_an_unflushed_line_leaves_it_to_the_stop(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript)
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    # Taken as a terminal turn, as on main: the agents are still on record.
    assert sorted(sess.active_subagents) == sorted([BG, OTHER])
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time())})
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Stopped, as asked.",
             background_tasks=[], transcript_path=str(transcript))
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert sess.status is Status.IDLE
    assert sess.job_background_open() is False
    assert sess.shown_state().state == "idle"
    assert sess.bg_agents_recent[BG]["ended_by"] == "killed"
    assert "<b>api</b> · Done" in rig.frames[-1]


# ---- a killed agent's card row reads stopped, never done -----------------------------------

def _agent_rows(frame: str) -> list[str]:
    return [line for line in frame.split("\n") if "🤖" in line]


def test_the_jobs_card_says_the_killed_agents_were_stopped(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    _waiting(rig, transcript)
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time())})
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Stopped, as asked.",
             background_tasks=[], transcript_path=str(transcript))
    rows = _agent_rows(rig.frames[-1])
    assert len(rows) == 2, rig.frames[-1]
    assert all("⏹" in r and "· stopped (" in r for r in rows), rows
    assert not any("✅" in r for r in rows), rows


def test_an_unflushed_line_still_shows_them_stopped(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    _waiting(rig, transcript)
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time())})
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Stopped, as asked.",
             background_tasks=[], transcript_path=str(transcript))
    rows = _agent_rows(rig.frames[-1])
    assert rows and all("· stopped (" in r and "✅" not in r for r in rows), rig.frames[-1]


def test_control_an_agent_that_finished_still_reads_done(rig, tmp_path):
    """A lost SubagentStop (no kill line): the Stop that no longer lists
    the agent ends it as done, and its row keeps the tick."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    _waiting(rig, transcript)
    rig.hook("UserPromptSubmit", prompt="a person typed this", transcript_path=str(transcript))
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="All done.",
             background_tasks=[], transcript_path=str(transcript))
    rows = _agent_rows(rig.frames[-1])
    assert rows and all("✅" in r and "stopped" not in r for r in rows), rig.frames[-1]


def test_after_aipagers_stop_the_wakes_card_lists_no_stopped_agent(rig, tmp_path):
    """The turn Claude Code starts after aipager's Stop opens a card of its
    own: the stopped job's agents are not on it (and never as done)."""
    from aipager.bot import animation
    from tests.two_prompts_rig import ALY, CARD

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript)
    rig.tap(CARD, "stop", uid=ALY)
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    assert sess.status is Status.BUSY
    assert not any("🤖" in str(summary) for summary, _done in sess.tool_history)
    assert sess.finished_subagents == [] and sess.active_subagents == {}
    drawn = "\n".join("\n".join(rows) for _kind, rows, _tools
                      in animation._build_sections(sess, final=True))
    assert "🤖" not in drawn and "✅" not in drawn


def test_an_unflushed_kill_reads_stopped_when_the_wake_starts_a_new_agent(rig, tmp_path):
    """The wake turn starts agent C after the kill line; at its Stop,
    ``background_tasks`` no longer lists A and B but lists C. The line is
    judged against the starts of the agents it ends (A and B), not C's:
    A and B read stopped, never done, and C keeps running."""
    from tests.two_prompts_rig import FG

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript)
    # A and B started well before the kill, C after it.
    for info in sess.active_subagents.values():
        info["started_at"] -= 20.0
    for info in sess.bg_agents.values():
        info["started"] -= 20.0
    sess.files_turn_closed_wall -= 10.0
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    assert sorted(sess.active_subagents) == sorted([BG, OTHER])
    _line(transcript, {"type": "system", "subtype": "agents_killed",
                       "timestamp": _stamp(time.time() - 5)})
    rig.launch(FG, agent_type="ship-third")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started another.",
             background_tasks=_tasks(FG), transcript_path=str(transcript))
    assert list(sess.active_subagents) == [FG]
    assert list(sess.bg_agents) == [FG]
    rows = _agent_rows(rig.frames[-1])
    ended = [r for r in rows if "ship-third" not in r]
    assert len(ended) == 2 and len(rows) == 3, rig.frames[-1]
    assert "stopped" not in next(r for r in rows if "ship-third" in r), rows
    assert all("⏹" in r and "· stopped (" in r and "✅" not in r for r in ended), rows
