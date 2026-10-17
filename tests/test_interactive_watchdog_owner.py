"""Roadmap 8.120 (rev-iter1-004): the INTERACTIVE watchdog's quiet time.

It counted every hook, a background agent's included, so a working agent
kept it from ever firing while Claude's own dialog waited. Its quiet time
is now measured from Claude's own hooks and the open prompts' owners'
hooks; the parent's transcript closes only Claude's own prompts; and an
agent waiting on its dialog is never dropped as silent.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager import open_prompt
from aipager import session_monitor as sm
from aipager.session_monitor import INTERACTIVE_TIMEOUT_SECONDS, SessionMonitor
from aipager.state import Status
from tests.two_prompts_rig import BG, FG, Rig

PAST = INTERACTIVE_TIMEOUT_SECONDS + 60


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def _claude_question_while_bg_works(rig):
    rig.launch(BG)
    rig.question()


# ---- quiet time --------------------------------------------------------------

def test_other_agent_hooks_do_not_reset_quiet(rig):
    _claude_question_while_bg_works(rig)
    sess = rig.sess
    quiet = sm._interactive_quiet_since(sess)
    assert quiet is not None
    # A background agent that owns no open prompt keeps working.
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
             tool_use_id="toolu_a", agent_id=BG, agent_type="ship-reviewer")
    rig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": "/a"},
             tool_use_id="toolu_a", agent_id=BG, agent_type="ship-reviewer",
             tool_response={})
    assert sess.last_hook_at > quiet
    assert sm._interactive_quiet_since(sess) == quiet


def test_owner_hooks_reset_quiet(rig):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make")
    sess = rig.sess
    before = sm._interactive_quiet_since(sess)
    rig.hook("MessageDisplay", delta="still thinking", message_id="m1",
             agent_id=FG, agent_type="ship-tester")
    assert sm._interactive_quiet_since(sess) == before
    rig.hook("MessageDisplay", delta="waiting on the dialog", message_id="m2",
             agent_id=BG, agent_type="ship-reviewer")
    assert sm._interactive_quiet_since(sess) > before


def test_parent_hooks_reset_quiet(rig):
    _claude_question_while_bg_works(rig)
    sess = rig.sess
    before = sm._interactive_quiet_since(sess)
    rig.hook("MessageDisplay", delta="Here is my question.", message_id="m3")
    assert sess.parent_hook_at > before
    assert sm._interactive_quiet_since(sess) == sess.parent_hook_at


def test_the_watchdog_fires_while_a_background_agent_works(rig, run_async, tmp_path,
                                                           monkeypatch):
    """The live symptom: past the timeout with only the agent's hooks, the
    transcript says Claude's question is over, and the session works again."""
    _claude_question_while_bg_works(rig)
    sess = rig.sess
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    monkeypatch.setattr(sm, "saved_prompt_evidence", lambda *a: "answered")
    monitor = SessionMonitor(rig.bot.registry, AsyncMock())
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
             tool_use_id="toolu_a", agent_id=BG, agent_type="ship-reviewer")
    now = sm._interactive_quiet_since(sess) + PAST
    run_async(monitor._interactive_watchdog(sess.name, sess, now))
    assert sess.status == Status.BUSY


# ---- the parent's transcript proves nothing about an agent's dialog -----------

def test_parent_transcript_does_not_close_agent_prompt(rig, run_async, tmp_path,
                                                       monkeypatch):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.question()
    sess = rig.sess
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    asked = []
    monkeypatch.setattr(sm, "saved_prompt_evidence",
                        lambda *a: asked.append(a) or "answered")
    notify = AsyncMock()
    monitor = SessionMonitor(rig.bot.registry, notify)
    now = sm._interactive_quiet_since(sess) + PAST
    run_async(monitor._interactive_watchdog(sess.name, sess, now))
    # Only Claude's question was asked about, and only it closed.
    assert [a[3] for a in asked] == ["AskUserQuestion"]
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"].get("agent_id") for p in sess.open_prompts()] == [BG]
    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "prompt_closed"


def test_a_lone_agent_prompt_is_not_closed_by_the_parents_transcript(
        rig, run_async, tmp_path, monkeypatch):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    sess = rig.sess
    sess.transcript_path = str(tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text("")
    monkeypatch.setattr(sm, "saved_prompt_evidence", lambda *a: "moved_on")
    monitor = SessionMonitor(rig.bot.registry, AsyncMock())
    now = sm._interactive_quiet_since(sess) + PAST
    run_async(monitor._interactive_watchdog(sess.name, sess, now))
    assert sess.status == Status.INTERACTIVE


# ---- an agent waiting on its dialog is not silent ----------------------------

def test_agent_waiting_on_prompt_not_swept(rig, run_async, monkeypatch):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make")
    sess = rig.sess
    long_ago = -1e9
    for table in (sess.bg_agents, sess.active_subagents):
        for info in table.values():
            info["last_seen"] = long_ago
    # The 8.41 running set.
    assert sess.sweep_bg_agents(time.monotonic(), 60.0) == [FG]
    assert BG in sess.bg_agents
    # The job's table, through the monitor's own sweep.

    async def _live():
        return [sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    monitor = SessionMonitor(rig.bot.registry, AsyncMock())
    run_async(monitor._scan())
    assert BG in sess.active_subagents
    assert FG not in sess.active_subagents


# ---- a restored prompt counts from the restore -------------------------------

def test_restored_prompt_quiet_from_restore(mk_bot, monkeypatch):
    bot = mk_bot()
    reg = bot.registry
    name = "claude-wdrestore"
    sess = reg.get_or_create(name)
    sess.label = "wd"
    reg.transition(name, Status.BUSY)
    reg.transition(name, Status.INTERACTIVE)
    sess.busy_msg_id = 77
    sess.pending_permission = {
        "tool_summary": "Bash: ls", "wait_started_at": time.monotonic(),
        "shown_wall": time.time() - 5, "hook_reply": None,
        "tool_info": {"name": "Bash", "input": {"command": "ls"},
                      "summary": "Bash: ls", "always_available": False,
                      "standing_rule_suggestion": None, "detail": "",
                      "tool_use_id": "toolu_1"}}
    rec = json.loads(json.dumps(open_prompt.snapshot(sess, time.monotonic(),
                                                     time.time())))
    assert rec is not None
    sess.pending_permission = None
    sess.status = Status.UNKNOWN
    sess.parent_hook_at = 0.0
    monkeypatch.setattr(bot, "_open_prompt_drop_reason", lambda *a, **k: None)
    before = time.monotonic()
    assert bot._restore_open_prompt(name, sess, rec, {name}) is True
    assert sess.status == Status.INTERACTIVE
    assert sess.parent_hook_at >= before
