"""The hook receiver and Claude Code background workers (D1-D5).

A background worker's hooks carry ``bg_job``. Such a datagram counts only
for the session parked on that job (linked); any other one is dropped
before it can create, stamp or move a session (D2), after one synchronous
``parked_check`` (the worker's first hook can beat the monitor's tick).
Own hooks report the client's pid (D1); while parked they no longer move
the session's ids (D4). A linked SessionEnd ends the job's turn, not the
session (D3), and a linked status line counts like an own one (D5).

Fed straight into ``HookReceiver._on_datagram``; the link files live under
the conftest-redirected runtime folder.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager import claude_jobs
from aipager.dtach import bg_jobs
from aipager.dtach import hook_receiver as hr
from aipager.report import store as report_store
from aipager.state import SessionRegistry, Status

NAME = "claude-frontend"
JOB = "ab12cd34"
OLD_SID = "11111111-1111-4111-8111-111111111111"
JOB_SID = "ab12cd34-2222-4222-8222-222222222222"
OLD_TP = f"/home/alice/.claude/projects/-home-alice-app/{OLD_SID}.jsonl"
JOB_TP = f"/home/alice/.claude/projects/-home-alice-app/{JOB_SID}.jsonl"


@pytest.fixture
def rx(run_async):
    registry = SessionRegistry()
    notify = AsyncMock()
    recv = hr.HookReceiver(registry, notify)

    def send(**fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))

    return registry, recv, notify, send


@pytest.fixture
def counters(monkeypatch):
    seen: list[str] = []
    real = report_store.record_counter

    def spy(key, *a, **kw):
        seen.append(key)
        return real(key, *a, **kw)

    monkeypatch.setattr(report_store, "record_counter", spy)
    return seen


def _known(registry, *, parked=""):
    sess = registry.get_or_create(NAME)
    sess.claude_session_id = OLD_SID
    sess.transcript_path = OLD_TP
    sess.cwd = "/home/alice/app"
    sess.parked_job = parked
    registry.transition(NAME, Status.IDLE)
    return sess


def _events(notify) -> list[str]:
    return [c.args[1] for c in notify.await_args_list]


# ---- D2: unlinked datagrams change nothing ----------------------------------

def test_an_unlinked_worker_creates_no_session(rx, counters):
    registry, recv, notify, send = rx
    calls = []
    recv.parked_check = calls.append
    send(hook_event_name="SessionStart", source="startup", session=NAME,
         bg_job=JOB, transcript_path=JOB_TP, cwd="/home/alice/elsewhere")
    assert registry.get(NAME) is None
    assert calls == [], "no session to check"
    assert counters.count("bg_job_unlinked") == 1
    assert notify.await_count == 0


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "Stop",
                                   "PreToolUse", "statusline", "SessionEnd"])
def test_an_unlinked_worker_leaves_a_known_session_alone(rx, counters, event):
    """T2.1: a spare's (or a stale link's) hooks never stamp, move or end
    the session they inherited the tag of; parked_check runs once."""
    registry, recv, notify, send = rx
    sess = _known(registry)
    calls = []
    recv.parked_check = calls.append
    before = (sess.last_hook_at, sess.claude_session_id, sess.transcript_path,
              sess.status, sess.cwd, sess.last_token_pct)
    fields = {"hook_event_name": event, "session": NAME, "bg_job": JOB,
              "transcript_path": JOB_TP, "cwd": "/home/alice/elsewhere",
              "source": "startup", "prompt": "hi"}
    if event == "statusline":
        fields = {"type": "statusline", "session": NAME, "bg_job": JOB,
                  "context_pct": 55, "session_id": JOB_SID}
    send(**fields)
    after = (sess.last_hook_at, sess.claude_session_id, sess.transcript_path,
             sess.status, sess.cwd, sess.last_token_pct)
    assert after == before
    assert calls == [sess], "parked_check runs exactly once"
    assert counters.count("bg_job_unlinked") == 1
    assert notify.await_count == 0


def test_a_worker_of_another_job_is_unlinked(rx, counters):
    registry, recv, notify, send = rx
    sess = _known(registry, parked="ffff0000")
    send(hook_event_name="UserPromptSubmit", session=NAME, bg_job=JOB,
         transcript_path=JOB_TP)
    assert sess.claude_session_id == OLD_SID and sess.last_hook_at == 0.0
    assert counters.count("bg_job_unlinked") == 1


def test_the_synchronous_check_can_link_the_worker(rx, counters):
    """T2.2: the hook saw the park before the monitor's tick; the
    receiver's own check parks the session, so the datagram counts."""
    registry, recv, notify, send = rx
    sess = _known(registry)

    def park(s):
        s.parked_job = JOB
        return True

    recv.parked_check = park
    send(hook_event_name="SessionStart", source="fork", session=NAME,
         bg_job=JOB, transcript_path=JOB_TP)
    assert sess.last_hook_at > 0
    assert sess.claude_session_id == JOB_SID
    assert "bg_job_unlinked" not in counters


def test_a_check_that_raises_drops_the_datagram(rx, counters):
    registry, recv, notify, send = rx
    sess = _known(registry)

    def broken(_s):
        raise RuntimeError("registry unreadable")

    recv.parked_check = broken
    send(hook_event_name="Stop", session=NAME, bg_job=JOB, transcript_path=JOB_TP)
    assert sess.last_hook_at == 0.0
    assert counters.count("bg_job_unlinked") == 1


# ---- D1: the client's pid ---------------------------------------------------

@pytest.fixture
def pid_seams(monkeypatch):
    tagged: dict[int, str] = {}
    monkeypatch.setattr(claude_jobs, "_pid_tagged",
                        lambda pid, name: tagged.get(pid) == name)
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: 900 + pid)
    return tagged


def test_an_own_hook_records_its_claude_pid(rx, pid_seams):
    registry, recv, notify, send = rx
    sess = _known(registry)
    pid_seams[4242] = NAME
    send(hook_event_name="PreToolUse", session=NAME, claude_pid=4242,
         tool_name="Read")
    assert (sess.claude_pid, sess.claude_pid_start) == (4242, 900 + 4242)


def test_a_linked_worker_never_sets_the_claude_pid(rx, pid_seams):
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    pid_seams[5151] = NAME
    send(hook_event_name="PreToolUse", session=NAME, bg_job=JOB,
         claude_pid=5151, tool_name="Read")
    assert sess.last_hook_at > 0, "the linked datagram was handled"
    assert sess.claude_pid == 0


@pytest.mark.parametrize("pid", [0, -3, "4242", True, 2 ** 31])
def test_a_claude_pid_that_is_no_pid_is_ignored(rx, monkeypatch, pid):
    """Even a process check that would accept anything never sees it."""
    registry, recv, notify, send = rx
    sess = _known(registry)
    monkeypatch.setattr(claude_jobs, "_pid_tagged", lambda _pid, _name: True)
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda _pid: 1)
    send(hook_event_name="PreToolUse", session=NAME, claude_pid=pid, tool_name="Read")
    assert sess.claude_pid == 0


def test_another_sessions_claude_is_not_taken_as_this_ones(rx, pid_seams):
    """A datagram naming a pid whose process carries another session's
    tag (any local process can write to the socket) is not kept."""
    registry, recv, notify, send = rx
    sess = _known(registry)
    pid_seams[4242] = "claude-backend"
    send(hook_event_name="PreToolUse", session=NAME, claude_pid=4242, tool_name="Read")
    assert sess.claude_pid == 0


# ---- D4: whose ids are adopted ------------------------------------------------

def test_an_own_hook_while_parked_keeps_the_ids(rx):
    """S6: the parked client's own hooks name the old copy of the
    conversation; its id and folder stay as they are."""
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    sess.claude_session_id = JOB_SID
    sess.transcript_path = JOB_TP
    send(hook_event_name="Notification", notification_type="idle_prompt",
         session=NAME, transcript_path=OLD_TP, cwd="/home/alice/elsewhere")
    assert sess.last_hook_at > 0
    assert (sess.claude_session_id, sess.transcript_path) == (JOB_SID, JOB_TP)
    assert sess.cwd == "/home/alice/app"


def test_an_own_hook_adopts_the_ids_when_not_parked(rx):
    registry, recv, notify, send = rx
    sess = _known(registry)
    send(hook_event_name="Notification", notification_type="idle_prompt",
         session=NAME, transcript_path=JOB_TP, cwd="/home/alice/elsewhere")
    assert sess.claude_session_id == JOB_SID
    assert sess.cwd == "/home/alice/elsewhere"


def test_a_linked_hook_adopts_the_jobs_ids(rx):
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    send(hook_event_name="UserPromptSubmit", session=NAME, bg_job=JOB,
         transcript_path=JOB_TP, cwd="/home/alice/app2", prompt="go on")
    assert (sess.claude_session_id, sess.transcript_path) == (JOB_SID, JOB_TP)
    assert sess.cwd == "/home/alice/app2"


def test_a_linked_fork_start_does_not_close_the_files_turn(rx):
    """The fork's SessionStart: a turn that was running goes on in the
    job, so the files turn stays open (an own start closes it)."""
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    sess.files_turn_open = True
    sess.files_turn_wall = time.time()
    send(hook_event_name="SessionStart", source="fork", session=NAME,
         bg_job=JOB, transcript_path=JOB_TP)
    assert sess.files_turn_open is True
    assert sess.claude_session_id == JOB_SID
    sess.parked_job = ""
    send(hook_event_name="SessionStart", source="startup", session=NAME,
         transcript_path=OLD_TP)
    assert sess.files_turn_open is False


# ---- D3: the linked job's SessionEnd ------------------------------------------

def _link(job=JOB, **extra):
    path = bg_jobs.link_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert bg_jobs.write_own_json(path, {"job": job, "session": NAME, **extra})
    return path


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE])
def test_a_linked_session_end_ends_the_turn_not_the_session(rx, status):
    """S4: ``claude stop`` (or the job ending) fires the worker's
    SessionEnd. The session's own claude is still parked: no GONE, no
    session_end notice; the job's turn is finished (idle_prompt)."""
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    registry.transition(NAME, status)
    link = _link()
    send(hook_event_name="SessionEnd", reason="other", session=NAME, bg_job=JOB,
         transcript_path=JOB_TP, session_id=JOB_SID)
    assert sess.status == Status.IDLE
    assert "session_end" not in _events(notify)
    assert "idle_prompt" in _events(notify)
    assert json.loads(link.read_text())["ended"] is True


def test_a_linked_session_end_on_an_idle_session_sends_nothing(rx):
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    link = _link()
    send(hook_event_name="SessionEnd", reason="other", session=NAME, bg_job=JOB,
         transcript_path=JOB_TP)
    assert sess.status == Status.IDLE
    assert _events(notify) == []
    assert json.loads(link.read_text())["ended"] is True
    assert link.exists(), "the link stays until a bring-back or the session's end"


def test_an_own_session_end_still_ends_the_session(rx):
    """Control: the parked client's own SessionEnd (the terminal quit) is
    today's GONE."""
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    send(hook_event_name="SessionEnd", reason="prompt_input_exit", session=NAME,
         transcript_path=OLD_TP)
    assert sess.status == Status.GONE
    assert "session_end" in _events(notify)


# ---- D5: the status line ------------------------------------------------------

def test_a_linked_status_line_updates_the_tokens(rx):
    registry, recv, notify, send = rx
    sess = _known(registry, parked=JOB)
    send(type="statusline", session=NAME, bg_job=JOB, context_pct=42,
         session_id=JOB_SID)
    assert sess.last_token_pct == 42


def test_an_unlinked_status_line_is_dropped(rx, counters):
    registry, recv, notify, send = rx
    sess = _known(registry)
    send(type="statusline", session=NAME, bg_job=JOB, context_pct=42,
         session_id=JOB_SID)
    assert sess.last_token_pct == 0
    assert counters.count("bg_job_unlinked") == 1
