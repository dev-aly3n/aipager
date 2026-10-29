"""Roadmap 8.121 F5 (design-6 section 4): how the hook receiver tells the
turn Claude Code starts by itself after its kill chord from a person's
terminal turn, and how it ends agents that no SubagentStop will end.

- S1: a markerless UserPromptSubmit inside the window aipager's own chord
  armed is the self-woken wake (one-shot), and waits for that Stop's
  teardown (``stop_gate``).
- S2: a markerless UserPromptSubmit while agents run, with an
  ``agents_killed`` transcript line after the last Stop, is the wake of a
  kill pressed at the terminal.
- S3: at a Stop, agents Claude Code listed before in ``background_tasks``
  and no longer lists are ended; an id it never listed is kept.
- A late SubagentStop of an agent a kill ended adds no card row.
- A prompt typed at the terminal during an agents-only wait clears
  ``wait_from_idle``.
- The session monitor counts a kill no wake confirmed.

Structural signals only: the tests' wake sentence is never what decides.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import BG, OTHER, S, Rig

WAKE = 'Background agent "Review" was stopped by the user.'


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(hr, "HOOK_DEDUP_WINDOW_SECONDS", -1.0)

    async def _ok(args, stdin=b"", timeout=5):
        return True, ""

    async def _gap(_delay):
        return None

    monkeypatch.setattr(inject, "_run", _ok)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _gap)
    from unittest.mock import AsyncMock
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    return Rig(mk_bot, monkeypatch, run_async)


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _killed_line(path, epoch):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "system", "subtype": "agents_killed",
                            "timestamp": _stamp(epoch)}) + "\n")


def _tasks(*ids):
    return [{"id": i, "type": "subagent", "status": "running"} for i in ids]


def _waiting(rig, *agents, tasks=None, transcript=""):
    for agent in agents or (BG,):
        rig.launch(agent)
    fields = {"stop_hook_active": False, "last_assistant_message": "Started."}
    if tasks is not None:
        fields["background_tasks"] = tasks
    if transcript:
        fields["transcript_path"] = transcript
    rig.hook("Stop", **fields)
    assert rig.sess.status is Status.IDLE
    return rig.sess


def _spy(rig):
    seen: list[str] = []
    real = rig.recv.notify_fn

    async def spy(sess, event, context=None):
        seen.append(event)
        return await real(sess, event, context or {})

    rig.recv.notify_fn = spy
    return seen


# ---- S1 -----------------------------------------------------------------------------

def test_s1_a_markerless_prompt_in_the_window_is_the_self_woken_wake(rig):
    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    origin = sess.last_prompt_origin
    rig.hook("UserPromptSubmit", prompt=WAKE)
    assert sess.status is Status.BUSY
    assert sess.last_prompt_origin == origin != "terminal"
    assert sess.turn_prompt != WAKE
    assert sess.turn_sender_id is None


def test_s1_is_one_shot(rig):
    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    rig.hook("UserPromptSubmit", prompt=WAKE)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Noted.")
    rig.hook("UserPromptSubmit", prompt="and now the README please")
    assert sess.last_prompt_origin == "terminal"
    assert sess.turn_prompt == "and now the README please"


def test_s1_needs_the_window(rig):
    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    sess.kill_expected_until = time.monotonic() - 1.0
    rig.hook("UserPromptSubmit", prompt="a person typed this")
    assert sess.last_prompt_origin == "terminal"


@pytest.mark.parametrize("prompt", [
    "[via Telegram msg=55]\nhello",
    "<task-notification>\n<task-id>x</task-id>\n</task-notification>",
    "<cross-session-message from=\"frontend\">hi</cross-session-message>"],
    ids=["telegram", "task-notification", "peer"])
def test_a_marked_prompt_never_consumes_the_window(rig, prompt):
    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    until = sess.kill_expected_until
    assert until > time.monotonic()
    rig.hook("UserPromptSubmit", prompt=prompt)
    assert sess.kill_expected_until == until


def test_s1_waits_for_the_stops_teardown(rig):
    """The wake arrives while the Stop still edits its card: it is routed
    only after the Stop is done, so the Stop never overwrites it."""
    sess = _waiting(rig)
    order: list[str] = []
    real_settle = rig.bot._settle_card_text

    async def settle(s, text):
        payload = {"session": S, "hook_event_name": "UserPromptSubmit", "prompt": WAKE,
                   "transcript_path": "", "_seq": 9999}
        task = asyncio.ensure_future(rig.recv._on_datagram(json.dumps(payload).encode()))
        for _ in range(20):
            await asyncio.sleep(0)
        order.append(f"settle:{sess.status.name}")
        result = await real_settle(s, text)
        rig.wake = task
        return result

    rig.bot._settle_card_text = settle

    async def go():
        await rig.bot._stop_session_core(sess)
        order.append("stop-done")
        await rig.wake
        order.append(f"wake-done:{sess.status.name}")

    rig.run_async(go())
    assert order == ["settle:IDLE", "stop-done", "wake-done:BUSY"]


# ---- S2 ---------------------------------------------------------------------------

def test_s2_a_kill_line_after_the_last_stop_ends_the_agents(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript=str(transcript))
    sess.files_turn_closed_wall = time.time() - 30
    _killed_line(transcript, time.time())
    events = _spy(rig)
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert sess.last_prompt_origin != "terminal"
    assert "job_continuation" in events
    assert sess.agents_killed_seen_wall > 0


def test_s2_a_kill_line_before_the_last_stop_is_old(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    _killed_line(transcript, time.time() - 600)
    sess = _waiting(rig, transcript=str(transcript))
    sess.files_turn_closed_wall = time.time() - 30
    rig.hook("UserPromptSubmit", prompt="what about the tests?",
             transcript_path=str(transcript))
    assert list(sess.active_subagents) == [BG]
    assert sess.last_prompt_origin == "terminal"


def test_s2_a_line_already_handled_is_not_taken_twice(rig, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    sess = _waiting(rig, transcript=str(transcript))
    _killed_line(transcript, time.time())
    from aipager.transcript import agents_killed_since
    sess.agents_killed_seen_wall = agents_killed_since(str(transcript), 0.0)
    rig.hook("UserPromptSubmit", prompt="what about the tests?",
             transcript_path=str(transcript))
    assert list(sess.active_subagents) == [BG]


# ---- S3 ---------------------------------------------------------------------------

def test_s3_agents_no_longer_listed_end_before_the_finish(rig):
    sess = _waiting(rig, BG, OTHER, tasks=_tasks(BG, OTHER))
    rig.hook("UserPromptSubmit", prompt="a person typed this")
    events = _spy(rig)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done.",
             background_tasks=[])
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert events.count("subagent_stop") == 2
    # Finished, not another interim: nothing of the job is left open.
    assert sess.job_background_open() is False
    assert sess.shown_state().state == "idle"


def test_s3_keeps_an_agent_it_never_listed(rig):
    sess = _waiting(rig, BG, OTHER, tasks=_tasks(BG))
    rig.hook("UserPromptSubmit", prompt="a person typed this")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done.",
             background_tasks=[])
    assert list(sess.active_subagents) == [OTHER]


def test_s3_keeps_an_agent_still_listed(rig):
    sess = _waiting(rig, BG, OTHER, tasks=_tasks(BG, OTHER))
    rig.hook("UserPromptSubmit", prompt="a person typed this")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done.",
             background_tasks=_tasks(OTHER))
    assert list(sess.active_subagents) == [OTHER]
    assert BG not in sess.bg_agents


def test_s3_with_no_kill_line_ends_them_as_done(rig):
    sess = _waiting(rig, BG, tasks=_tasks(BG))
    sess.agents_lines.append({"ids": [BG], "ends": {}})
    rig.hook("UserPromptSubmit", prompt="a person typed this")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done.",
             background_tasks=[])
    assert sess.agents_lines[0]["ends"][BG]["outcome"] == "done"


# ---- the late SubagentStop ------------------------------------------------------------

def test_a_late_subagent_stop_of_a_killed_agent_adds_no_row(rig):
    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    events = _spy(rig)
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert "subagent_stop" not in events


def test_a_subagent_stop_of_an_agent_not_killed_still_counts(rig):
    sess = _waiting(rig, BG, OTHER)
    rig.run_async(rig.bot._stop_session_core(sess))
    sess.bg_agents_recent[OTHER].pop("ended_by")
    events = _spy(rig)
    rig.hook("SubagentStop", agent_id=OTHER, agent_type="ship-reviewer")
    assert "subagent_stop" in events


# ---- wait_from_idle -------------------------------------------------------------------

def test_a_terminal_prompt_during_an_agents_only_wait_clears_it(rig):
    sess = _waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    assert sess.wait_from_idle is True
    rig.hook("UserPromptSubmit", prompt="look at the README meanwhile")
    assert sess.status is Status.INTERACTIVE
    assert sess.wait_from_idle is False
    assert sess.stop_mode() == "keys"


def test_claudes_own_tool_call_clears_it(rig):
    sess = _waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a.py"},
             tool_use_id="toolu_r1")
    assert sess.wait_from_idle is False


# ---- an unconfirmed kill ---------------------------------------------------------------

def test_the_monitor_counts_a_kill_no_wake_confirmed(rig, monkeypatch):
    from aipager.session_monitor import SessionMonitor

    sess = _waiting(rig)
    rig.run_async(rig.bot._stop_session_core(sess))
    counted: list[str] = []
    monkeypatch.setattr("aipager.session_monitor.report_store.record_counter",
                        lambda name, *a, **k: counted.append(name))

    async def _alive():
        return [S]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)
    rig.run_async(monitor._scan())
    assert "kill_agents_unconfirmed" not in counted  # the window is still open
    sess.kill_expected_until = time.monotonic() - 0.1
    rig.run_async(monitor._scan())
    rig.run_async(monitor._scan())
    assert counted.count("kill_agents_unconfirmed") == 1
    assert sess.kill_expected_until == 0.0
