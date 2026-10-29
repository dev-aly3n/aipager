"""Roadmap 8.121: `aipager status` / `aipager session ls` show a session
waiting for its own background agents as TASKS.

The CLI is another process: it reads only the daemon's state file. The
daemon writes a computed ``job_wait`` record (the job's agent count) for
a session in the ``tasks`` state, and nothing in any other state. Agents
only (design-amend-5 A): a job shell or the grace window alone writes no
record. Agents start and end without marking the registry dirty, so
``save_if_dirty()`` also saves when a session's shown state moved since
the last save.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import status
from aipager.state import SessionRegistry, Status, TrackedSession


def _sess(name: str, st: Status = Status.IDLE, **fields) -> TrackedSession:
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"), status=st)
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def _registry(*sessions: TrackedSession) -> SessionRegistry:
    registry = SessionRegistry()
    for sess in sessions:
        registry._sessions[sess.name] = sess
    return registry


def _file() -> dict:
    return json.loads(open(status.SESSION_STATE_FILE).read())["sessions"]


def _rows(monkeypatch, *, live) -> dict[str, dict]:
    monkeypatch.setattr(status, "_live_sessions", lambda: set(live))
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _ = status._gather_sessions()
    return {r["name"]: r for r in rows}


def _write_state(sessions: dict) -> None:
    status.SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    status.SESSION_STATE_FILE.write_text(json.dumps({"sessions": sessions}))


def _agent(sess: TrackedSession, agent_id: str) -> None:
    sess.active_subagents[agent_id] = {"type": "Explore", "started_at": time.monotonic()}


def _shell(sess: TrackedSession, task_id: str, in_job: bool = True) -> None:
    sess.bg_shells[task_id] = {"label": "npm test", "in_job": in_job,
                               "started_at": time.monotonic()}


# ---- the daemon's save ----------------------------------------------------------------

def test_the_save_carries_the_agent_count_only_while_waiting():
    waiting = _sess("claude-w", busy_msg_id=7)
    _agent(waiting, "a1")
    _agent(waiting, "a2")
    _shell(waiting, "s1")
    _shell(waiting, "s2", in_job=False)
    busy = _sess("claude-b", Status.BUSY, busy_msg_id=8)
    _agent(busy, "a3")
    idle = _sess("claude-i")
    _shell(idle, "s3", in_job=False)
    shells_only = _sess("claude-s", busy_msg_id=9)
    _shell(shells_only, "s4")
    _registry(waiting, busy, idle, shells_only).save()
    saved = _file()
    assert saved["claude-w"]["job_wait"] == {"agents": 2}
    assert "job_wait" not in saved["claude-b"]
    assert "job_wait" not in saved["claude-i"]
    assert "job_wait" not in saved["claude-s"], "a job shell alone writes no record"


def test_load_ignores_the_record():
    waiting = _sess("claude-w")
    _agent(waiting, "a1")
    _registry(waiting).save()
    loaded = SessionRegistry()
    loaded.load()
    sess = loaded.get("claude-w")
    assert not hasattr(sess, "job_wait")
    assert sess.active_subagents == {}


def test_save_if_dirty_saves_when_an_agent_starts_unmarked():
    """Guard 10: the registry is clean, an agent starts (no event marks
    it dirty), and the next save_if_dirty() writes the record; when it
    ends, the next one drops it."""
    sess = _sess("claude-w")
    registry = _registry(sess)
    registry.save()
    assert "job_wait" not in _file()["claude-w"]
    assert registry._dirty is False
    _agent(sess, "a1")
    registry.save_if_dirty()
    assert _file()["claude-w"]["job_wait"] == {"agents": 1}
    _agent(sess, "a2")
    registry.save_if_dirty()
    assert _file()["claude-w"]["job_wait"] == {"agents": 2}
    sess.active_subagents.clear()
    registry.save_if_dirty()
    assert "job_wait" not in _file()["claude-w"]


def test_a_grace_window_or_a_job_shell_writes_no_record():
    sess = _sess("claude-w")
    sess.job_grace_until = time.monotonic() + 60
    _shell(sess, "s1")
    registry = _registry(sess)
    registry.save_if_dirty()
    registry.save()
    assert "job_wait" not in _file()["claude-w"]


def test_nothing_moved_means_no_save(monkeypatch):
    sess = _sess("claude-w")
    _agent(sess, "a1")
    registry = _registry(sess)
    registry.save()
    saves = []
    monkeypatch.setattr(registry, "save", lambda: saves.append(1))
    _shell(sess, "s1")  # a shell does not move the shown state
    registry.save_if_dirty()
    assert saves == []


# ---- what the CLI reads -------------------------------------------------------------

def test_a_waiting_session_reads_tasks_with_its_count(monkeypatch):
    """Guard 9: the waiting card keeps its busy_msg_id, so TASKS is read
    before BUSY."""
    sess = _sess("claude-w", busy_msg_id=7)
    _agent(sess, "a1")
    _agent(sess, "a2")
    _shell(sess, "s1")
    _registry(sess).save()
    row = _rows(monkeypatch, live={"claude-w"})["claude-w"]
    assert (row["status"], row["job_agents"]) == ("TASKS", 2)
    assert "job_shells" not in row
    assert status.tasks_text(row) == "waiting for 2 agents"


def test_a_job_with_only_shells_reads_as_its_card(monkeypatch):
    sess = _sess("claude-w", busy_msg_id=7)
    _shell(sess, "s1")
    _registry(sess).save()
    row = _rows(monkeypatch, live={"claude-w"})["claude-w"]
    assert (row["status"], row["job_agents"]) == ("BUSY", 0)


def test_a_file_without_the_record_reads_as_before(monkeypatch):
    _write_state({"claude-x": {"label": "x", "busy_msg_id": 9, "status": "IDLE",
                               "pending_queue": []}})
    row = _rows(monkeypatch, live={"claude-x"})["claude-x"]
    assert (row["status"], row["job_agents"]) == ("BUSY", 0)
    assert status.tasks_text(row) == ""


@pytest.mark.parametrize("saved, live, expected", [
    ({"status": "INTERACTIVE"}, True, "WAITING"),
    ({"status": "IDLE"}, False, "GONE"),
], ids=["needs-you-wins", "gone-wins"])
def test_waiting_on_a_person_and_gone_come_first(monkeypatch, saved, live, expected):
    entry = {"label": "x", "busy_msg_id": 9, "pending_queue": [],
             "job_wait": {"agents": 1}, **saved}
    _write_state({"claude-x": entry})
    row = _rows(monkeypatch, live={"claude-x"} if live else set())["claude-x"]
    assert (row["status"], row["job_agents"]) == (expected, 0)


@pytest.mark.parametrize("record, count", [
    ({"agents": "2"}, 0),
    ({"agents": -1}, 0),
    ({"agents": True}, 0),
    ({"agents": 3, "shells": 4}, 3),
    ({}, 0),
], ids=["text", "negative", "bool", "old-shells-field", "empty"])
def test_a_hand_edited_record_reads_a_safe_count(monkeypatch, record, count):
    _write_state({"claude-x": {"label": "x", "pending_queue": [], "job_wait": record}})
    row = _rows(monkeypatch, live={"claude-x"})["claude-x"]
    assert row["status"] == "TASKS"
    assert row["job_agents"] == count
    assert "job_shells" not in row


def test_an_unregistered_live_socket_has_a_zero_count(monkeypatch):
    _write_state({})
    row = _rows(monkeypatch, live={"claude-new"})["claude-new"]
    assert (row["status"], row["job_agents"]) == ("IDLE", 0)


@pytest.mark.parametrize("agents, text", [
    (2, "waiting for 2 agents"),
    (1, "waiting for 1 agent"),
], ids=["agents", "agent"])
def test_tasks_text(agents, text):
    row = {"status": "TASKS", "job_agents": agents}
    assert status.tasks_text(row) == text


def test_tasks_text_is_empty_for_any_other_row():
    assert status.tasks_text({"status": "BUSY", "job_agents": 2}) == ""


def test_the_rows_print_the_state_and_what_it_waits_for(monkeypatch, capsys):
    rows = [{"name": "claude-w", "label": "w", "status": "TASKS", "model": "",
             "context_pct": None, "cost_usd": None, "queue_depth": 0,
             "waiting_kind": None, "waiting_summary": None,
             "job_agents": 2}]
    status.render_sessions_plain(rows)
    out = capsys.readouterr().out
    assert "w  TASKS  waiting for 2 agents" in out
    status.render_sessions_rich(rows)
    out = capsys.readouterr().out
    assert "TASKS" in out and "waiting for 2 agents" in out and "↻" in out
