"""The INTERACTIVE watchdog's quiet time (design.md success criterion 7,
entrypoints.md ``AIPAGER_INTERACTIVE_TIMEOUT`` and
``AIPAGER_SUBAGENT_SILENCE``): hooks of agents that own no open prompt do
not reset it; Claude's own hooks and the prompt owner's do. Past the quiet
time, transcript evidence closes only Claude's own prompts; an agent's
prompt is never closed by the parent transcript, and an agent that owns an
open prompt is never dropped for silence.

The two windows are import-time constants read from those env vars; the
tests set them short and let real time pass (no clock is patched). The
transcript read is the filesystem boundary and is stubbed to "the dialog
is over", so only the quiet-time and ownership rules decide.
"""

from __future__ import annotations

import time

import pytest

from aipager import session_monitor as sm
from aipager.session_monitor import SessionMonitor
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, OTHER, S

QUIET = 0.6


@pytest.fixture
def watch(rig, monkeypatch, run_async):
    monkeypatch.setattr(sm, "INTERACTIVE_TIMEOUT_SECONDS", QUIET)

    async def _alive():
        return [S]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monkeypatch.setattr(sm, "dialog_closed_in_transcript", lambda *a, **k: True)
    monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)

    def scan():
        run_async(monitor._scan())

    return scan


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def quiet():
    time.sleep(QUIET + 0.3)


def agent_read_done(rig, agent, n=1):
    """A hook from *agent* that proves no dialog closed: a PostToolUse of
    a parallel Read."""
    rig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": "/src/a.py"},
             tool_use_id=f"toolu_{agent}_read{n}", agent_id=agent,
             agent_type="ship-reviewer", tool_response={"content": "x"})


def claude_read_done(rig):
    rig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": "/src/a.py"},
             tool_use_id="toolu_c_read", tool_response={"content": "x"})


# -- a lone question of Claude's, an agent working that owns nothing ------------------

def test_other_agent_hooks_do_not_reset_quiet(rig, watch):
    rig.launch(BG)
    rig.question()
    quiet()
    agent_read_done(rig, BG)
    watch()
    assert owners(rig) == []


def test_other_agent_hooks_do_not_keep_session_interactive(rig, watch):
    rig.launch(BG)
    rig.question()
    quiet()
    agent_read_done(rig, BG)
    watch()
    assert rig.sess.status == Status.BUSY


def test_parent_hooks_reset_quiet(rig, watch):
    rig.question()
    quiet()
    claude_read_done(rig)
    watch()
    assert owners(rig) == [None]


def test_within_quiet_time_nothing_closes(rig, watch):
    """Boundary: just after the question, well inside the window."""
    rig.question()
    watch()
    assert rig.sess.status == Status.INTERACTIVE


def test_no_transcript_evidence_keeps_question_open(rig, watch, monkeypatch):
    """Partition: quiet but the transcript does not show the dialog over."""
    monkeypatch.setattr(sm, "dialog_closed_in_transcript", lambda *a, **k: False)
    rig.question()
    quiet()
    watch()
    assert owners(rig) == [None]


# -- Claude's question plus an agent's prompt ---------------------------------------------

def both_open(rig):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()


def test_owner_hooks_reset_quiet(rig, watch):
    both_open(rig)
    quiet()
    agent_read_done(rig, BG)
    watch()
    assert owners(rig) == [BG, None]


def test_non_owner_hooks_do_not_reset_quiet_with_two_open(rig, watch):
    both_open(rig)
    quiet()
    agent_read_done(rig, OTHER)
    watch()
    assert None not in owners(rig)


def test_parent_transcript_does_not_close_agent_prompt(rig, watch):
    both_open(rig)
    quiet()
    watch()
    assert owners(rig) == [BG]


def test_parent_transcript_with_agent_prompt_left_stays_interactive(rig, watch):
    both_open(rig)
    quiet()
    watch()
    assert rig.sess.status == Status.INTERACTIVE


def test_lone_agent_prompt_never_closed_by_parent_transcript(rig, watch):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    quiet()
    watch()
    assert owners(rig) == [BG]


def test_agent_prompt_answerable_after_watchdog_closed_question(rig, watch):
    both_open(rig)
    quiet()
    watch()
    rig.tap(CARD, "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == ["req-bg"]


# -- the silence sweep spares an agent that owns an open prompt -------------------------------

def test_agent_waiting_on_prompt_not_dropped_for_silence(rig, watch, monkeypatch):
    monkeypatch.setattr(sm, "SUBAGENT_SILENCE_SECONDS", QUIET)
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    quiet()
    watch()
    watch()
    assert owners(rig) == [BG]
