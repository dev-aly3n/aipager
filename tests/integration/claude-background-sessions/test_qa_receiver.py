"""QA: what the daemon does with a worker's datagram (D1-D5, S4, S6).

DESIGN.md scenario 4: other background jobs and Claude's spare workers are
ignored ("no false 'Session exited', no turn or card loss, no change to the
saved id"). Scenario 3: output from the linked job counts for the session.

Partitions of a datagram: own (no ``bg_job``), linked (``bg_job`` equals
the session's ``parked_job``), unlinked (any other ``bg_job``, for a parked
session, a session that is not parked, or a name aipager does not know).
"""

from __future__ import annotations

from aipager.state import Status


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()
OTHER = "cccccccc-0000-4000-8000-000000000009"


def _tp(stack, sid):
    """The transcript path a real hook payload carries."""
    return str(stack.claude.projects / f"{sid}.jsonl")


def _state(sess):
    return (sess.status, sess.claude_session_id, sess.last_hook_at, sess.cwd,
            sess.transcript_path)


# ---- D2: unlinked worker datagrams change nothing ----------------------------------

def test_a_spares_start_for_an_unknown_name_creates_no_session(stack):
    stack.hook(session="claude-nobody", hook_event_name="SessionStart", source="startup",
               bg_job=stack.ids.SPARE_JOB, session_id=stack.ids.SPARE_ID)
    assert stack.registry.get("claude-nobody") is None


def test_a_spares_start_leaves_a_busy_session_untouched(stack):
    """Not parked at all: a spare under the inherited tag must not end the
    turn or move the id (vm3's false turn loss)."""
    before = _state(stack.sess)
    stack.hook(hook_event_name="SessionStart", source="startup",
               bg_job=stack.ids.SPARE_JOB, session_id=stack.ids.SPARE_ID,
               cwd="/home/alice/elsewhere")
    assert _state(stack.sess) == before


def test_a_spares_start_sends_the_bot_nothing(stack):
    stack.hook(hook_event_name="SessionStart", source="startup",
               bg_job=stack.ids.SPARE_JOB, session_id=stack.ids.SPARE_ID)
    assert stack.events == []


def test_a_spares_session_end_is_no_session_exited(stack):
    stack.hook(hook_event_name="SessionEnd", reason="other",
               bg_job=stack.ids.SPARE_JOB, session_id=stack.ids.SPARE_ID)
    assert (stack.sess.status, stack.events) == (Status.BUSY, [])


def test_a_spares_stop_does_not_finish_the_turn(stack):
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.SPARE_JOB,
               session_id=stack.ids.SPARE_ID, last_assistant_message="not yours")
    assert stack.sess.status == Status.BUSY


def test_another_jobs_datagram_for_a_parked_session_changes_nothing(stack):
    qa.park_and_tick(stack)
    before = _state(stack.sess)
    stack.hook(hook_event_name="UserPromptSubmit", prompt="hi",
               bg_job=stack.ids.SPARE_JOB, session_id=stack.ids.SPARE_ID)
    assert _state(stack.sess) == before


def test_an_unlinked_statusline_is_dropped(stack):
    qa.park_and_tick(stack)
    events = list(stack.events)
    stack.hook(type="statusline", bg_job=stack.ids.SPARE_JOB,
               session_id=stack.ids.SPARE_ID, context_pct=88)
    assert stack.events == events


# ---- D3 / S4: a linked SessionEnd never ends the session -----------------------------

def test_a_linked_session_end_never_makes_the_session_gone(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="SessionEnd", reason="other", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert stack.sess.status != Status.GONE


def test_a_linked_session_end_sends_no_end_notice(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="SessionEnd", reason="other", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert "session_end" not in stack.events


def test_a_linked_session_end_closes_an_open_turn(stack):
    qa.park_and_tick(stack, job_status="busy")     # the moved turn goes on in the job
    assert stack.sess.status == Status.BUSY, "precondition: the moved turn is open"
    stack.hook(hook_event_name="SessionEnd", reason="other", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert "idle_prompt" in stack.events


def test_a_linked_session_end_keeps_the_session_parked(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="SessionEnd", reason="other", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert stack.sess.parked_job == stack.ids.JOB


def test_a_linked_session_end_marks_the_link_ended(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="SessionEnd", reason="other", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert (qa.link(stack.ids.JOB) or {}).get("ended") is True


# ---- D4 / S6: ids --------------------------------------------------------------------

def test_a_linked_hook_adopts_the_jobs_conversation(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Notification", notification_type="x",
               bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               transcript_path=_tp(stack, stack.ids.JS))
    assert stack.sess.claude_session_id == stack.ids.JS


def test_an_own_hook_of_the_parked_client_keeps_the_jobs_id(stack):
    """The parked client's own transcript is the stale pre-fork copy."""
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Notification", notification_type="x",
               bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               transcript_path=_tp(stack, stack.ids.JS))
    assert stack.sess.claude_session_id == stack.ids.JS, "precondition"
    stack.hook(hook_event_name="Notification", notification_type="x",
               session_id=stack.ids.X, transcript_path=_tp(stack, stack.ids.X))
    assert stack.sess.claude_session_id == stack.ids.JS


def test_an_unlinked_hook_never_adopts_its_id(stack):
    stack.claude.transcript(OTHER, {"type": "user"})
    stack.hook(hook_event_name="Notification", notification_type="x",
               bg_job=stack.ids.SPARE_JOB, session_id=OTHER,
               transcript_path=_tp(stack, OTHER))
    assert stack.sess.claude_session_id == stack.ids.X


def test_an_own_hook_still_adopts_when_not_parked(stack):
    """S7/scenario 7: a session with no background job behaves as today."""
    stack.claude.transcript(OTHER, {"type": "user"})
    stack.hook(hook_event_name="SessionStart", source="clear", session_id=OTHER,
               transcript_path=_tp(stack, OTHER))
    assert stack.sess.claude_session_id == OTHER


# ---- D1: the claude pid ----------------------------------------------------------------

def test_an_own_hook_records_the_claude_pid(stack):
    qa.own_pid_hook(stack)
    assert stack.sess.claude_pid == stack.ids.CLIENT


def test_a_linked_hook_never_records_its_pid_as_the_clients(stack):
    qa.own_pid_hook(stack)
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Notification", notification_type="x",
               bg_job=stack.ids.JOB, session_id=stack.ids.JS, claude_pid=stack.ids.WORKER)
    assert stack.sess.claude_pid == stack.ids.CLIENT


# ---- P7: the linked job drives the card ---------------------------------------------------

def test_a_linked_prompt_opens_a_turn(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               last_assistant_message="first")
    assert stack.sess.status == Status.IDLE, "precondition"
    stack.hook(hook_event_name="UserPromptSubmit", prompt="next", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS)
    assert stack.sess.status == Status.BUSY


def test_a_linked_stop_finishes_the_turn(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               last_assistant_message="Done in the background.")
    assert stack.sess.status == Status.IDLE


def test_a_linked_stop_relays_the_answer(stack):
    qa.park_and_tick(stack)
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               last_assistant_message="Done in the background.")
    assert "idle_prompt" in stack.events
