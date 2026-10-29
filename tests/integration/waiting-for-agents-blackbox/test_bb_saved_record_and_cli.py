"""entrypoints.md side effects and CLI: the ``job_wait`` record in
``~/.claude/aipager-sessions.json`` (under the test's redirected home),
written by the next ``save_if_dirty()`` after the derived state or its
agent count changes and dropped on the first save after it ends; and the CLI
rows (``_gather_sessions``, ``tasks_text``, ``aipager session ls --json``,
``aipager status --json``) read from that file.

Precedence per row: GONE, then WAITING, then TASKS, then BUSY, then IDLE.
Agents only (design-amend-5 A): the record and the rows carry the agent
count alone; a job with only shells, or nothing left running, writes no
record.
Only the dtach side of the CLI is faked (``_live_sessions``,
``_read_statusline``), as tests/test_status_waiting.py does.
"""

from __future__ import annotations

import argparse
import importlib
import json
import time

import pytest

from aipager import cli, status
from aipager.state import SessionRegistry, Status

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")
NAME = "claude-x1"


def _registry(*sessions) -> SessionRegistry:
    reg = SessionRegistry()
    kit.register(reg, *sessions)
    return reg


def _saved(reg, name=NAME) -> dict:
    reg.save()
    return kit.saved_sessions()[name]


def _write(entries: dict) -> None:
    status.SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    status.SESSION_STATE_FILE.write_text(json.dumps({"sessions": entries}))


# ---- the saved record ------------------------------------------------------------

def test_save_writes_the_agent_count_for_a_waiting_session():
    reg = _registry(kit.make_session(agents=2, job_shells=1, other_agents=1, other_shells=1))
    assert _saved(reg)["job_wait"] == {"agents": 2}


def test_save_writes_no_record_while_the_grace_window_runs():
    sess = kit.make_session()
    sess.job_grace_until = time.monotonic() + 60
    assert "job_wait" not in _saved(_registry(sess))


def test_save_writes_no_record_for_a_job_with_only_shells():
    assert "job_wait" not in _saved(_registry(kit.make_session(job_shells=2)))


@pytest.mark.parametrize("status_, agents", [
    (Status.IDLE, 0), (Status.BUSY, 2), (Status.INTERACTIVE, 2), (Status.GONE, 2),
], ids=["idle", "busy", "interactive", "gone"])
def test_save_has_no_record_outside_the_waiting_state(status_, agents):
    sess = kit.make_session(status=status_, agents=agents, job_shells=1 if agents else 0,
                            other_shells=1, other_agents=1)
    if status_ is Status.GONE:
        sess.gone_at = time.monotonic()
    assert "job_wait" not in _saved(_registry(sess))


def test_save_if_dirty_writes_the_record_when_an_agent_starts():
    """design.md guard 10: no other dirtying, the derived state changing
    is enough."""
    sess = kit.make_session()
    reg = _registry(sess)
    reg.save()
    sess.active_subagents["late"] = {"type": "Plan", "started_at": time.monotonic(),
                                     "history_idx": None}
    reg.save_if_dirty()
    assert kit.saved_sessions()[NAME].get("job_wait") == {"agents": 1}


def test_save_if_dirty_follows_a_count_change():
    sess = kit.make_session(agents=2, job_shells=1)
    reg = _registry(sess)
    reg.save()
    sess.active_subagents.pop("a0")
    reg.save_if_dirty()
    assert kit.saved_sessions()[NAME]["job_wait"] == {"agents": 1}


def test_save_if_dirty_drops_the_record_when_the_last_agent_ends_and_a_shell_runs():
    sess = kit.make_session(agents=1, job_shells=1)
    reg = _registry(sess)
    reg.save()
    sess.active_subagents.clear()
    sess.job_grace_until = time.monotonic() + 60
    reg.save_if_dirty()
    assert "job_wait" not in kit.saved_sessions()[NAME]


def test_save_if_dirty_drops_the_record_on_gone():
    sess = kit.make_session(agents=1)
    reg = _registry(sess)
    reg.save()
    reg.transition(NAME, Status.GONE)
    reg.save_if_dirty()
    assert "job_wait" not in kit.saved_sessions()[NAME]


def test_save_if_dirty_drops_the_record_on_a_continuation_turn():
    sess = kit.make_session(agents=1)
    reg = _registry(sess)
    reg.save()
    reg.transition(NAME, Status.BUSY, preserve_job_state=True)
    reg.save_if_dirty()
    assert "job_wait" not in kit.saved_sessions()[NAME]


def test_save_if_dirty_with_nothing_changed_does_not_rewrite(monkeypatch):
    sess = kit.make_session(agents=1)
    reg = _registry(sess)
    reg.save()
    calls = []
    real = SessionRegistry.save
    monkeypatch.setattr(SessionRegistry, "save",
                        lambda self, *a, **k: (calls.append(1), real(self, *a, **k))[1])
    reg.save_if_dirty()
    assert calls == []


def test_load_ignores_the_record_and_the_next_save_drops_it():
    """An older daemon's view: the computed key is never restored."""
    _write({NAME: {"label": "x1", "busy_msg_id": None, "pending_queue": [],
                   "job_wait": {"agents": 4}}})
    reg = SessionRegistry()
    reg.load()
    reg.save()
    assert "job_wait" not in kit.saved_sessions().get(NAME, {})


# ---- the CLI rows ------------------------------------------------------------------

def test_cli_row_is_tasks_for_a_waiting_session(monkeypatch):
    _registry(kit.make_session(agents=2, job_shells=1)).save()
    assert kit.cli_rows(monkeypatch, {NAME})[NAME]["status"] == "TASKS"


def test_cli_row_carries_the_agent_count(monkeypatch):
    _registry(kit.make_session(agents=2, job_shells=1, other_shells=2)).save()
    row = kit.cli_rows(monkeypatch, {NAME})[NAME]
    assert row["job_agents"] == 2
    assert "job_shells" not in row


def test_cli_row_for_a_job_with_only_a_shell_reads_as_on_main(monkeypatch):
    """Its card is still up (``busy_msg_id``), so BUSY, as before 8.121."""
    sess = kit.make_session(job_shells=1)
    sess.busy_msg_id = 42
    _registry(sess).save()
    row = kit.cli_rows(monkeypatch, {NAME})[NAME]
    assert (row["status"], row["job_agents"]) == ("BUSY", 0)


def test_cli_row_tasks_wins_over_a_kept_busy_card(monkeypatch):
    """design.md guard 9: the waiting card keeps ``busy_msg_id``."""
    sess = kit.make_session(agents=1)
    sess.busy_msg_id = 42
    _registry(sess).save()
    assert kit.cli_rows(monkeypatch, {NAME})[NAME]["status"] == "TASKS"


def test_cli_row_without_a_record_and_a_card_reads_busy(monkeypatch):
    """Back-compat: a file written by an older daemon."""
    _write({NAME: {"label": "x1", "busy_msg_id": 42, "pending_queue": []}})
    row = kit.cli_rows(monkeypatch, {NAME})[NAME]
    assert (row["status"], row["job_agents"]) == ("BUSY", 0)


def test_cli_row_waiting_wins_over_a_record(monkeypatch):
    _write({NAME: {"label": "x1", "busy_msg_id": 42, "pending_queue": [],
                   "status": "INTERACTIVE", "job_wait": {"agents": 1}}})
    assert kit.cli_rows(monkeypatch, {NAME})[NAME]["status"] == "WAITING"


def test_cli_row_gone_wins_over_a_record(monkeypatch):
    _write({NAME: {"label": "x1", "busy_msg_id": None, "pending_queue": [],
                   "job_wait": {"agents": 1}}})
    assert kit.cli_rows(monkeypatch, set())[NAME]["status"] == "GONE"


@pytest.mark.parametrize("status_", [Status.IDLE, Status.BUSY, Status.INTERACTIVE],
                         ids=["idle", "busy", "interactive"])
def test_cli_rows_outside_tasks_have_a_zero_int_count(monkeypatch, status_):
    sess = kit.make_session(status=status_, agents=0 if status_ is Status.IDLE else 2,
                            other_shells=1)
    if status_ is Status.BUSY:
        sess.busy_msg_id = 7
    _registry(sess).save()
    row = kit.cli_rows(monkeypatch, {NAME})[NAME]
    assert row["job_agents"] == 0 and type(row["job_agents"]) is int


def test_cli_status_word_is_not_waiting_or_busy(monkeypatch):
    _registry(kit.make_session(agents=1)).save()
    assert kit.cli_rows(monkeypatch, {NAME})[NAME]["status"] not in ("WAITING", "BUSY")


def test_cli_row_returns_to_idle_within_one_save_after_the_job_ends(monkeypatch):
    sess = kit.make_session(agents=1)
    reg = _registry(sess)
    reg.save()
    sess.active_subagents.clear()
    reg.save_if_dirty()
    assert kit.cli_rows(monkeypatch, {NAME})[NAME]["status"] == "IDLE"


# ---- tasks_text --------------------------------------------------------------------

@pytest.mark.parametrize("agents, expected", [
    (1, "waiting for 1 agent"),
    (2, "waiting for 2 agents"),
    (21, "waiting for 21 agents"),
], ids=["1a", "2a", "21a"])
def test_tasks_text(agents, expected):
    assert status.tasks_text({"status": "TASKS", "job_agents": agents}) == expected


@pytest.mark.parametrize("word", ["IDLE", "BUSY", "WAITING", "GONE"])
def test_tasks_text_is_empty_for_other_rows(word):
    assert status.tasks_text({"status": word, "job_agents": 0}) == ""


# ---- the commands ------------------------------------------------------------------

def _fake_dtach(monkeypatch, live):
    monkeypatch.setattr(status, "_live_sessions", lambda: set(live))
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})


def test_session_ls_json_row(monkeypatch, capsys):
    _registry(kit.make_session(agents=2, job_shells=1)).save()
    _fake_dtach(monkeypatch, {NAME})
    assert cli._cmd_session(argparse.Namespace(name="ls", claude_args=["--json"])) == 0
    row = next(r for r in json.loads(capsys.readouterr().out)["sessions"] if r["name"] == NAME)
    assert (row["status"], row["job_agents"]) == ("TASKS", 2)
    assert "job_shells" not in row


def test_session_ls_text_names_what_it_waits_for(monkeypatch, capsys):
    _registry(kit.make_session(agents=2, job_shells=1)).save()
    _fake_dtach(monkeypatch, {NAME})
    cli._cmd_session(argparse.Namespace(name="ls", claude_args=[]))
    out = capsys.readouterr().out
    assert "TASKS" in out and "waiting for 2 agents" in out and "shell" not in out, out


def test_status_json_row(monkeypatch, capsys):
    _registry(kit.make_session(agents=1, job_shells=2)).save()
    _fake_dtach(monkeypatch, {NAME})
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    status.cmd_status(argparse.Namespace(as_json=True))
    row = next(r for r in json.loads(capsys.readouterr().out)["sessions"] if r["name"] == NAME)
    assert (row["status"], row["job_agents"]) == ("TASKS", 1)
    assert "job_shells" not in row


def test_status_text_names_what_it_waits_for(monkeypatch, capsys):
    _registry(kit.make_session(agents=1)).save()
    _fake_dtach(monkeypatch, {NAME})
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    status.cmd_status(argparse.Namespace(as_json=False))
    out = capsys.readouterr().out
    assert "TASKS" in out and "waiting for 1 agent" in out, out
