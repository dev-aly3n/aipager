"""Roadmap 8.118: `aipager status` shows a session that is waiting for you.

A session waiting on a permission or a question (INTERACTIVE) read BUSY in
`aipager status` and `aipager session ls`, which only looked at whether a
busy card existed. The daemon now saves each session's live status (and,
when known, what it waits on) in its state file, and the CLI reads it:
WAITING, with ``waiting_kind`` / ``waiting_summary`` in the JSON. The saved
status is for the CLI only: a restart never restores it.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import status
from aipager.state import SessionRegistry, Status, TrackedSession


def _registry(*sessions: TrackedSession) -> SessionRegistry:
    registry = SessionRegistry()
    for sess in sessions:
        registry._sessions[sess.name] = sess
    return registry


def _sess(name: str, st: Status, **fields) -> TrackedSession:
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"), status=st)
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def _rows(monkeypatch, *, live) -> dict[str, dict]:
    monkeypatch.setattr(status, "_live_sessions", lambda: set(live))
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _ = status._gather_sessions()
    return {r["name"]: r for r in rows}


def _write_state(sessions: dict) -> None:
    status.SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    status.SESSION_STATE_FILE.write_text(json.dumps({"sessions": sessions}))


def _saved(registry) -> dict:
    registry.save()
    return json.loads(open(status.SESSION_STATE_FILE).read())["sessions"]


# ---- the daemon's save -----------------------------------------------------------

def test_the_save_carries_the_live_status_and_what_a_waiting_session_waits_on():
    saved = _saved(_registry(
        _sess("claude-perm", Status.INTERACTIVE, busy_msg_id=7,
              pending_permission={"tool_summary": "Bash: make deploy"}),
        _sess("claude-ask", Status.INTERACTIVE, busy_msg_id=8,
              pending_permission={"ask_question": True, "question": "Which one?"}),
        _sess("claude-sep", Status.INTERACTIVE),     # a prompt sent as its own message
        _sess("claude-work", Status.BUSY, busy_msg_id=9),
    ))
    assert saved["claude-perm"]["status"] == "INTERACTIVE"
    assert (saved["claude-perm"]["waiting_kind"], saved["claude-perm"]["waiting_summary"]) \
        == ("permission", "Bash: make deploy")
    assert (saved["claude-ask"]["waiting_kind"], saved["claude-ask"]["waiting_summary"]) \
        == ("question", "Which one?")
    assert saved["claude-sep"]["status"] == "INTERACTIVE"
    assert "waiting_kind" not in saved["claude-sep"]
    assert saved["claude-work"]["status"] == "BUSY"
    assert "waiting_kind" not in saved["claude-work"]


def test_a_restart_never_restores_the_saved_status():
    _saved(_registry(_sess("claude-perm", Status.INTERACTIVE, busy_msg_id=7,
                           pending_permission={"tool_summary": "Bash: ls"})))
    loaded = SessionRegistry()
    loaded.load()
    assert loaded.get("claude-perm").status is Status.UNKNOWN


# ---- what the CLI reads ------------------------------------------------------------

def test_a_session_waiting_on_you_reads_waiting_with_what_it_waits_on(monkeypatch):
    _saved(_registry(_sess("claude-perm", Status.INTERACTIVE, busy_msg_id=7,
                           pending_permission={"tool_summary": "Bash: make deploy"})))
    row = _rows(monkeypatch, live={"claude-perm"})["claude-perm"]
    assert row["status"] == "WAITING"
    assert (row["waiting_kind"], row["waiting_summary"]) == ("permission", "Bash: make deploy")


def test_a_prompt_sent_as_its_own_message_still_reads_waiting(monkeypatch):
    # No card, and nothing known about the prompt: still waiting on you.
    _saved(_registry(_sess("claude-sep", Status.INTERACTIVE)))
    row = _rows(monkeypatch, live={"claude-sep"})["claude-sep"]
    assert row["status"] == "WAITING"
    assert row["waiting_kind"] is None and row["waiting_summary"] is None


@pytest.mark.parametrize("saved_status, card, expected", [
    ("BUSY", 9, "BUSY"),
    ("IDLE", None, "IDLE"),
    (None, 9, "BUSY"),          # a file saved before the status was
    (None, None, "IDLE"),
])
def test_the_other_states_read_as_before(monkeypatch, saved_status, card, expected):
    entry = {"label": "x", "busy_msg_id": card, "pending_queue": [],
             # left over in a hand-edited file: shown only while waiting
             "waiting_kind": "permission", "waiting_summary": "Bash: ls"}
    if saved_status:
        entry["status"] = saved_status
    _write_state({"claude-x": entry})
    row = _rows(monkeypatch, live={"claude-x"})["claude-x"]
    assert row["status"] == expected
    assert row["waiting_kind"] is None and row["waiting_summary"] is None


def test_a_waiting_session_whose_terminal_is_gone_reads_gone(monkeypatch):
    _saved(_registry(_sess("claude-perm", Status.INTERACTIVE, busy_msg_id=7,
                           pending_permission={"tool_summary": "Bash: ls"})))
    row = _rows(monkeypatch, live=set())["claude-perm"]
    assert row["status"] == "GONE" and row["waiting_kind"] is None


def test_a_terminal_aipager_does_not_know_yet_has_the_same_keys(monkeypatch):
    _write_state({})
    row = _rows(monkeypatch, live={"claude-new"})["claude-new"]
    assert row["status"] == "IDLE"
    assert row["waiting_kind"] is None and row["waiting_summary"] is None


# ---- how it is shown ----------------------------------------------------------------

def _row(**fields) -> dict:
    row = {"name": "claude-x", "label": "x", "status": "WAITING", "model": "",
           "context_pct": None, "cost_usd": None, "queue_depth": 0,
           "waiting_kind": "permission", "waiting_summary": "Bash: make deploy"}
    row.update(fields)
    return row


def test_the_waiting_text_is_one_short_line():
    assert status.waiting_text(_row()) == "permission: Bash: make deploy"
    assert status.waiting_text(_row(waiting_kind="question",
                                    waiting_summary="Which\n  one?")) == "question: Which one?"
    assert status.waiting_text(_row(waiting_summary=None)) == "permission"
    assert status.waiting_text(_row(waiting_kind=None)) == ""
    long = status.waiting_text(_row(waiting_summary="Bash: " + "x" * 200))
    assert len(long) == status.WAITING_TEXT_MAX and long.endswith("…")


def test_the_waiting_text_shows_only_on_a_waiting_row():
    assert status.waiting_text(_row(status="BUSY")) == ""


@pytest.mark.parametrize("render", ["rich", "plain"])
def test_both_renderers_show_it_as_written(capsys, render):
    # A command is free text: brackets in it are not markup.
    rows = [_row(waiting_summary="Bash: echo [/x] [bold]y")]
    if render == "rich":
        status.render_sessions_rich(rows)
    else:
        status.render_sessions_plain(rows)
    out = capsys.readouterr().out
    assert "WAITING" in out
    assert "permission: Bash: echo [/x] [bold]y" in out


def test_a_quiet_idle_is_saved_too():
    # An IDLE right after another is not announced (the debounce), but the
    # file must still say it: `aipager status` would show WAITING forever.
    registry = _registry(_sess("claude-perm", Status.INTERACTIVE, busy_msg_id=7,
                               pending_permission={"tool_summary": "Bash: ls"}))
    registry.get("claude-perm").last_idle_at = time.monotonic()
    registry.save()
    registry._dirty = False
    assert registry.transition("claude-perm", Status.IDLE) is None    # not announced
    registry.save_if_dirty()
    saved = json.loads(open(status.SESSION_STATE_FILE).read())["sessions"]
    assert saved["claude-perm"]["status"] == "IDLE"
