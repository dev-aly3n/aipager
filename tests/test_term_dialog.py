"""Roadmap 8.127: a dialog open in Claude Code's terminal that no hook tells
of (the resume questions), found from Claude Code's own session record.

The record is a fake ``<config>/sessions/<pid>.json`` under tmp_path
(``claude_jobs.config_dir`` replaced), the /proc lookups and the owner
check are the modules' seams, and the clocks are ``term_dialog``'s own.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aipager import claude_jobs, term_dialog
from aipager.cli.daemon import _wire_dialog_probe
from aipager.dtach import bg_jobs
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status

NAME = "claude-frontend"
SID = "11111111-1111-4111-8111-111111111111"
CLIENT = 4100


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "claude-config"
    (root / "sessions").mkdir(parents=True)
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: root)
    return root


@pytest.fixture
def proc(monkeypatch):
    p = SimpleNamespace(start={CLIENT: 777}, alive={CLIENT})
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: p.start.get(pid))
    monkeypatch.setattr(claude_jobs, "_ppid", lambda pid: None)
    monkeypatch.setattr(claude_jobs, "_dtach_pids", lambda name: [])
    monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in p.alive)
    return p


_bump = [0]


def reg(cfg, pid=CLIENT, **fields):
    """Write ``sessions/<pid>.json`` with a fresh mtime (the reader caches
    by mtime and size)."""
    path = cfg / "sessions" / f"{pid}.json"
    body = {"pid": pid, "kind": "interactive", "sessionId": SID, "procStart": "777",
            **fields}
    path.write_text(json.dumps(body))
    _bump[0] += 1
    ns = 1_700_000_000_000_000_000 + _bump[0] * 1_000_000
    os.utime(path, ns=(ns, ns))
    return path


def waiting(cfg, *, at=None, waiting_for="dialog open", pid=CLIENT):
    at = time.time() if at is None else at
    return reg(cfg, pid, status="waiting", waitingFor=waiting_for,
               statusUpdatedAt=int(at * 1000))


def idle(cfg, *, at=None, pid=CLIENT):
    at = time.time() if at is None else at
    return reg(cfg, pid, status="idle", statusUpdatedAt=int(at * 1000))


@pytest.fixture
def mon():
    registry = SessionRegistry()
    notify = AsyncMock()
    monitor = SessionMonitor(registry, notify)
    sess = registry.get_or_create(NAME)
    sess.claude_session_id = SID
    sess.claude_pid = CLIENT
    registry.transition(NAME, Status.IDLE)
    return registry, monitor, notify, sess


def scan(monitor, run_async, monkeypatch, names=(NAME,)):
    async def live():
        return list(names)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", live)
    run_async(monitor._scan())


def events(notify, name="term_dialog"):
    return [c.args[2] for c in notify.await_args_list if c.args[1] == name]


# ---- detection ------------------------------------------------------------------

def test_waiting_record_opens_terminal_dialog(cfg, proc, mon, run_async, monkeypatch):
    registry, monitor, notify, sess = mon
    _wire_dialog_probe(registry, hr)
    waiting(cfg)
    assert inject.dialog_open(NAME) is False
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open()
    assert sess.any_dialog_open() and not sess.dialog_is_open()
    assert inject.dialog_open(NAME) is True
    assert sess.term_dialog["pid"] == CLIENT
    assert sess.term_dialog["waiting_for"] == "dialog open"
    assert events(notify) == [{"change": "opened"}]


@pytest.mark.parametrize("state", ["carry", "perm", "extra"])
def test_hook_tracked_prompt_is_not_terminal_dialog(cfg, proc, mon, run_async,
                                                    monkeypatch, state):
    registry, monitor, notify, sess = mon
    if state == "carry":
        sess.prompt_wait_carry = True
    elif state == "perm":
        sess.pending_permission = {"tool_summary": "Bash: ls"}
    else:
        sess.extra_prompts = [{"msg_id": 5}]
    sess.status = Status.IDLE
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


@pytest.mark.parametrize("status", ["busy", "interactive"])
def test_no_terminal_dialog_unless_idle(cfg, proc, mon, run_async, monkeypatch, status):
    """A permission dialog shows the same record during a turn; resume
    dialogs never open during one."""
    registry, monitor, notify, sess = mon
    registry.transition(NAME, Status.BUSY)
    if status == "interactive":
        registry.transition(NAME, Status.INTERACTIVE)
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


def test_idle_record_closes_terminal_dialog(cfg, proc, mon, run_async, monkeypatch):
    registry, monitor, notify, sess = mon
    opened_at = time.time() - 5
    waiting(cfg, at=opened_at)
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open()
    idle(cfg)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()
    assert events(notify)[-1] == {"change": "closed"}
    assert sess.term_dialog_settle is not None
    assert sess.term_dialog_settle["after_wall"] == pytest.approx(opened_at, abs=0.01)


def test_foreign_owned_record_is_ignored(cfg, proc, mon, run_async, monkeypatch):
    registry, monitor, notify, sess = mon
    waiting(cfg)
    real = os.getuid()
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: real + 1)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()
    # The same file, owned by us: open (the control).
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: real)
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open()


@pytest.mark.parametrize("body", ['{"pid": 4100, "status": "waiting"', '["waiting"]',
                                  '"waiting"'])
def test_malformed_record_is_ignored(cfg, proc, mon, run_async, monkeypatch, body):
    registry, monitor, notify, sess = mon
    path = cfg / "sessions" / f"{CLIENT}.json"
    path.write_text(body)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


def test_dead_or_reused_pid_record_is_ignored(cfg, proc, mon, run_async, monkeypatch):
    registry, monitor, notify, sess = mon
    proc.alive.discard(CLIENT)
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


def test_incomplete_registry_changes_nothing(cfg, proc, mon, run_async, monkeypatch):
    """An opening stays on record while the registry cannot be read whole:
    nothing is typed on a guess. A whole read with no client closes it."""
    registry, monitor, notify, sess = mon
    path = waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open()
    path.unlink()
    bad = cfg / "sessions" / "4999.json"
    bad.write_text("{not json")
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open(), "an incomplete read closed the dialog"
    bad.unlink()
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


@pytest.mark.parametrize("case", ["parked", "relaunch", "gone"])
def test_not_observed_while_parked_or_relaunching(case, monkeypatch):
    """A parked session's client, a session being relaunched (its old
    claude's record), an ended one: none opens a dialog."""
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    registry.transition(NAME, Status.IDLE)
    if case == "parked":
        sess.parked_job = "ab12cd34"
    elif case == "relaunch":
        sess.relaunch_in_flight = True
    else:
        registry.transition(NAME, Status.GONE)
    entry = {"pid": CLIENT, "status": "waiting", "waitingFor": "dialog open",
             "statusAt": time.time()}
    assert term_dialog.observe(sess, entry, complete=True, now_mono=1.0,
                               now_wall=time.time()) is None
    assert not sess.terminal_dialog_open()


@pytest.mark.parametrize("case", ["gone", "relaunch"])
def test_no_record_lookup_for_gone_or_relaunching(cfg, proc, mon, run_async,
                                                  monkeypatch, case):
    """The scan looks for the session's claude only while it is live and not
    being relaunched (the cost stays with live sessions)."""
    registry, monitor, notify, sess = mon
    looked = []
    real = claude_jobs.find_client
    monkeypatch.setattr(claude_jobs, "find_client",
                        lambda s, e: looked.append(s.name) or real(s, e))
    waiting(cfg)
    if case == "gone":
        registry.transition(NAME, Status.GONE)
    else:
        sess.relaunch_in_flight = True
    scan(monitor, run_async, monkeypatch, names=() if case == "gone" else (NAME,))
    assert looked == []
    # The control: a live session is looked for.
    sess.relaunch_in_flight = False
    if case == "gone":
        registry.transition(NAME, Status.IDLE)
    scan(monitor, run_async, monkeypatch)
    assert NAME in looked


def test_turn_start_closes_terminal_dialog(cfg, proc, mon, run_async, monkeypatch):
    """A turn started (typed in the terminal after answering there): the
    dialog is over, whatever the record says now."""
    registry, monitor, notify, sess = mon
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    assert sess.terminal_dialog_open()
    registry.transition(NAME, Status.BUSY)
    waiting(cfg, at=time.time() - 0.5)      # a permission dialog of the turn
    scan(monitor, run_async, monkeypatch)
    assert not sess.terminal_dialog_open()


def test_backstop_settles_a_busy_session(monkeypatch):
    """The settle's backstop also holds for a session that is not idle."""
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    registry.transition(NAME, Status.BUSY)
    sess.term_dialog_settle = {"since": 0.0, "after_wall": 1.0, "expect": ""}
    assert term_dialog.observe(sess, None, complete=True, now_mono=10.0,
                               now_wall=time.time(), settle=True) is None
    assert term_dialog.observe(sess, None, complete=True,
                               now_mono=term_dialog.AFTER_ANSWER_MAX_WAIT + 1,
                               now_wall=time.time(), settle=True) == "settled"
    assert sess.term_dialog_settle is None


@pytest.mark.parametrize("status", ["idle", "busy"])
def test_only_the_scan_ends_a_settle(status, monkeypatch):
    """A look that is not the monitor's scan (right before typing, a
    button's) never takes the settle: only the scan releases the hold it
    belongs to (review rev-iter1-001)."""
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    registry.transition(NAME, Status.IDLE if status == "idle" else Status.BUSY)
    sess.term_dialog_settle = {"since": 0.0, "after_wall": 1.0, "expect": ""}
    idle = {"pid": CLIENT, "status": "idle", "statusAt": time.time()}
    late = term_dialog.AFTER_ANSWER_MAX_WAIT + 1
    assert term_dialog.observe(sess, idle, complete=True, now_mono=late,
                               now_wall=time.time()) is None
    assert sess.term_dialog_settle is not None
    assert term_dialog.observe(sess, idle, complete=True, now_mono=late,
                               now_wall=time.time(), settle=True) == "settled"
    assert sess.term_dialog_settle is None


def test_new_status_at_is_new_opening(cfg, proc, mon, run_async, monkeypatch):
    """Waiting again with another status time (a newer-model offer drawn
    after a cold-resume dialog): a new opening, its own epoch and card."""
    registry, monitor, notify, sess = mon
    waiting(cfg, at=time.time() - 10)
    scan(monitor, run_async, monkeypatch)
    first = sess.term_dialog
    sess.term_dialog["card"] = (-1001, 77)
    waiting(cfg, at=time.time() - 1)
    scan(monitor, run_async, monkeypatch)
    assert sess.term_dialog is not first
    assert sess.term_dialog["epoch"] == first["epoch"] + 1
    assert [r["card"] for r in sess.term_dialog_closing] == [(-1001, 77)]
    assert events(notify)[-1] == {"change": "opened"}
    # Same record again: nothing new.
    scan(monitor, run_async, monkeypatch)
    assert sess.term_dialog["epoch"] == first["epoch"] + 1


def test_session_end_ends_terminal_dialog_hold(cfg, proc, mon, run_async, monkeypatch):
    from aipager.bot.handlers import _claim_dialog_hold
    registry, monitor, notify, sess = mon
    waiting(cfg)
    scan(monitor, run_async, monkeypatch)
    sess.term_dialog["card"] = (-1001, 88)
    sess.term_dialog["card_kind"] = "card"
    hold = _claim_dialog_hold(sess)
    assert hold["terminal"] is True
    registry.transition(NAME, Status.GONE)
    assert not sess.terminal_dialog_open()
    assert hold["closed"] == "ended"
    assert sess.term_dialog_closing[-1]["outcome"] == "ended"
    assert sess.term_dialog_closing[-1]["card"] == (-1001, 88)


# ---- which dialog ---------------------------------------------------------------

NOW = 1_800_000_000.0
MONO = 50_000.0


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(term_dialog, "_now_wall", lambda: NOW)
    monkeypatch.setattr(term_dialog, "_now_mono", lambda: MONO)


@pytest.fixture
def idle_sess():
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    registry.transition(NAME, Status.IDLE)
    return sess


def _entry(at, waiting_for="dialog open"):
    return {"pid": CLIENT, "status": "waiting", "waitingFor": waiting_for, "statusAt": at}


def _observe(sess, entry, exchange=None):
    return term_dialog.observe(sess, entry, complete=True, now_mono=MONO, now_wall=NOW,
                               last_exchange_wall=exchange)


def test_dialog_after_resume_is_resume_unsure(idle_sess, clock):
    idle_sess.session_start_at["resume"] = NOW - 3
    assert _observe(idle_sess, _entry(NOW - 1.5)) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.RESUME_UNSURE


def test_dialog_long_after_resume_is_not_resume(idle_sess, clock):
    idle_sess.session_start_at["resume"] = NOW - 100
    assert _observe(idle_sess, _entry(NOW - 1)) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.OTHER


@pytest.mark.parametrize("who", ["main", "agent", "bg_job"])
def test_bg_job_session_start_is_not_resume(who, run_async, monkeypatch):
    """Only the session's own claude's SessionStart stamps the resume
    clock: not a subagent's, not a background job's."""
    registry = SessionRegistry()
    recv = hr.HookReceiver(registry, AsyncMock())
    fields = {"hook_event_name": "SessionStart", "session": NAME, "source": "resume"}
    if who == "agent":
        fields["agent_id"] = "a1"
    elif who == "bg_job":
        fields["bg_job"] = "ab12cd34"
        monkeypatch.setattr(recv, "_bg_job_linked", AsyncMock(return_value=True))
    run_async(recv._on_datagram(json.dumps(fields).encode()))
    sess = registry.get(NAME)
    stamped = sess is not None and "resume" in sess.session_start_at
    assert stamped is (who == "main")


def test_recent_write_is_not_cold_idle(idle_sess, clock, monkeypatch):
    at = NOW - 1
    # The control: quiet for long enough, an old conversation.
    assert _observe(idle_sess, _entry(at), exchange=at - 300) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.COLD_IDLE
    idle_sess.term_dialog = None
    # aipager typed 30 s before the dialog: not the idle timer's.
    monkeypatch.setitem(inject._LAST_WRITE_AT, NAME, MONO - 31)
    assert _observe(idle_sess, _entry(at), exchange=at - 300) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.OTHER
    idle_sess.term_dialog = None
    # A hook 30 s before it: the same.
    monkeypatch.setitem(inject._LAST_WRITE_AT, NAME, MONO - 600)
    idle_sess.last_hook_at = MONO - 31
    assert _observe(idle_sess, _entry(at), exchange=at - 300) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.OTHER


def test_activity_after_the_draw_does_not_count_against_cold_idle(idle_sess, clock,
                                                                  monkeypatch):
    """The quiet rule looks back from the dialog's own time: a statusline
    redraw (or any hook) or a write the scan sees after it does not make
    the idle question "other" (review rev-iter1-003)."""
    at = NOW - 1.5
    monkeypatch.setitem(inject._LAST_WRITE_AT, NAME, MONO - 600)
    idle_sess.last_hook_at = MONO - 0.5          # 1 s after the draw
    assert _observe(idle_sess, _entry(at), exchange=at - 300) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.COLD_IDLE
    idle_sess.term_dialog = None
    idle_sess.last_hook_at = MONO - 600
    monkeypatch.setitem(inject._LAST_WRITE_AT, NAME, MONO - 0.5)
    assert _observe(idle_sess, _entry(at), exchange=at - 300) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.COLD_IDLE


def test_young_conversation_is_not_cold_idle(idle_sess, clock):
    at = NOW - 1
    assert _observe(idle_sess, _entry(at), exchange=at - 100) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.OTHER


def test_other_waiting_for_is_other(idle_sess, clock):
    idle_sess.session_start_at["resume"] = NOW - 3
    assert _observe(idle_sess, _entry(NOW - 1, "input needed"),
                    exchange=NOW - 1000) == "opened"
    assert idle_sess.term_dialog["kind"] == term_dialog.OTHER


def test_the_transcript_is_read_only_for_a_new_opening(idle_sess, clock):
    reads = []

    def exchange():
        reads.append(1)
        return NOW - 1000
    term_dialog.observe(idle_sess, _entry(NOW - 1), complete=True, now_mono=MONO,
                        now_wall=NOW, last_exchange_wall=exchange)
    term_dialog.observe(idle_sess, _entry(NOW - 1), complete=True, now_mono=MONO,
                        now_wall=NOW, last_exchange_wall=exchange)
    assert reads == [1]


def test_last_exchange_at_reads_the_last_user_or_assistant_entry(tmp_path):
    from aipager.transcript import last_exchange_at
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "timestamp": "2026-10-28T10:00:00.000Z"},
        {"type": "assistant", "timestamp": "2026-10-28T10:00:05.000Z"},
        {"type": "system", "timestamp": "2026-10-28T11:00:00.000Z"},
    ]) + "\n")
    from datetime import datetime, timezone
    want = datetime(2026, 10, 28, 10, 0, 5, tzinfo=timezone.utc).timestamp()
    assert last_exchange_at(str(path)) == want
    assert last_exchange_at(str(tmp_path / "missing.jsonl")) is None
    assert last_exchange_at("") is None
