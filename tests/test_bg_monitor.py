"""Finding a session parked on a Claude Code background job (P1-P3).

The session monitor finds each session's own claude in Claude's session
registry (``<config>/sessions/<pid>.json``), reads ``parkedJobId`` there
and keeps ``parked_job`` (persisted), the job's link file and the pid map
in step with it. Only when no entry is found does the transcript decide (a
last record ``continued-in``). Nothing readable changes nothing (S7).

Everything is read from a fake Claude config folder under tmp_path
(``claude_jobs.config_dir`` replaced); the /proc lookups are the module's
seams. The runtime files are the conftest-redirected ones.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aipager import claude_jobs
from aipager.dtach import bg_jobs
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status

NAME = "claude-frontend"
JOB = "ab12cd34"
SID = "11111111-1111-4111-8111-111111111111"
JOB_SID = "ab12cd34-2222-4222-8222-222222222222"
CLIENT = 4100
WORKER = 4200


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "claude-config"
    (root / "sessions").mkdir(parents=True)
    (root / "projects" / "-home-alice-app").mkdir(parents=True)
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: root)
    bg_jobs.link_path(JOB).parent.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def proc(monkeypatch):
    """The /proc seams: start times, parents, dtach masters, liveness."""
    p = SimpleNamespace(start={}, parent={}, masters=[], alive=set())
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: p.start.get(pid))
    monkeypatch.setattr(claude_jobs, "_ppid", lambda pid: p.parent.get(pid))
    monkeypatch.setattr(claude_jobs, "_dtach_pids", lambda name: list(p.masters))
    monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in p.alive)
    return p


_bump = [0]


def reg(cfg, pid, **fields):
    """Write ``sessions/<pid>.json`` with a fresh mtime (the reader caches
    by mtime and size)."""
    path = cfg / "sessions" / f"{pid}.json"
    path.write_text(json.dumps({"pid": pid, **fields}))
    _bump[0] += 1
    ns = 1_700_000_000_000_000_000 + _bump[0] * 1_000_000
    os.utime(path, ns=(ns, ns))
    return path


def transcript(cfg, sid, records):
    path = cfg / "projects" / "-home-alice-app" / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


@pytest.fixture
def mon():
    registry = SessionRegistry()
    notify = AsyncMock()
    monitor = SessionMonitor(registry, notify)
    sess = registry.get_or_create(NAME)
    sess.claude_session_id = SID
    registry.transition(NAME, Status.IDLE)
    return registry, monitor, notify, sess


def _scan(monitor, run_async, monkeypatch, names=(NAME,)):
    async def live():
        return list(names)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", live)
    run_async(monitor._scan())


def _link():
    return bg_jobs.read_own_json(bg_jobs.link_path(JOB))


def _pidmap():
    return bg_jobs.read_own_json(bg_jobs.pidmap_path())


# ---- P1: find_client -----------------------------------------------------------

def test_the_own_pid_with_its_start_is_the_client(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId="other", procStart="777")
    proc.start[CLIENT] = 777
    sess.claude_pid = CLIENT
    found = claude_jobs.find_client(sess, claude_jobs.read_registry())
    assert found is not None and found["pid"] == CLIENT


def test_a_reused_pid_is_not_the_client(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId="other", procStart="777")
    proc.start[CLIENT] = 778
    sess.claude_pid = CLIENT
    assert claude_jobs.find_client(sess, claude_jobs.read_registry()) is None


def test_the_dtach_ancestry_finds_the_client_when_no_pid_is_known(cfg, proc, mon):
    """The launch shell sits between the dtach master and claude."""
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId="other")
    reg(cfg, 4999, kind="interactive", sessionId="another")
    proc.masters = [100]
    proc.parent = {CLIENT: 200, 200: 100, 4999: 1}
    found = claude_jobs.find_client(sess, claude_jobs.read_registry())
    assert found is not None and found["pid"] == CLIENT
    assert sess.claude_pid == CLIENT, "the pid found is kept"


def test_the_ancestry_is_not_asked_when_a_pid_is_known(cfg, proc, mon):
    """Only when no client pid is cached: a known (stale) pid falls
    through to the id levels, never to a guess by process tree."""
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId="other")
    proc.masters = [100]
    proc.parent = {CLIENT: 100}
    sess.claude_pid = 9999
    assert claude_jobs.find_client(sess, claude_jobs.read_registry()) is None


def test_the_ancestry_walk_is_bounded(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId="other")
    proc.masters = [100]
    chain = {CLIENT: 5000}
    for i in range(5000, 5000 + claude_jobs.ANCESTRY_LEVELS):
        chain[i] = i + 1
    chain[5000 + claude_jobs.ANCESTRY_LEVELS] = 100
    proc.parent = chain
    assert claude_jobs.find_client(sess, claude_jobs.read_registry()) is None


def test_a_client_parked_on_the_conversations_job_is_found(cfg, proc, mon):
    """After a restart (no pid known) with the job's id adopted from its
    hooks: the entry parked on the job its first 8 characters name."""
    registry, monitor, notify, sess = mon
    sess.claude_session_id = JOB_SID
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    proc.alive.add(CLIENT)
    found = claude_jobs.find_client(sess, claude_jobs.read_registry())
    assert found is not None and found["pid"] == CLIENT


def test_the_entry_holding_the_conversation_is_found(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    reg(cfg, CLIENT, kind="interactive", sessionId=SID)
    reg(cfg, WORKER, kind="bg", sessionId=SID, jobId=JOB)
    proc.alive.update({CLIENT, WORKER})
    found = claude_jobs.find_client(sess, claude_jobs.read_registry())
    assert found is not None and found["pid"] == CLIENT


@pytest.mark.parametrize("level", ["parked_job", "session_id"])
def test_a_dead_clients_file_is_no_client(cfg, proc, mon, level):
    """By id alone only a live process counts: the file Claude Code left
    behind for a killed client (the one a bring-back replaced) is parked
    on the very job the session's new id starts with."""
    registry, monitor, notify, sess = mon
    if level == "parked_job":
        sess.claude_session_id = JOB_SID
        reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    else:
        reg(cfg, CLIENT, kind="interactive", sessionId=SID)
    assert claude_jobs.find_client(sess, claude_jobs.read_registry()) is None
    proc.alive.add(CLIENT)
    assert claude_jobs.find_client(sess, claude_jobs.read_registry())["pid"] == CLIENT


@pytest.mark.parametrize("level", ["ancestry", "parked_job", "session_id"])
def test_two_candidates_on_one_level_are_no_answer(cfg, proc, mon, level):
    registry, monitor, notify, sess = mon
    if level == "ancestry":
        proc.masters = [100]
        proc.parent = {CLIENT: 100, CLIENT + 1: 100}
        reg(cfg, CLIENT, kind="interactive", sessionId="a")
        reg(cfg, CLIENT + 1, kind="interactive", sessionId="b")
    elif level == "parked_job":
        sess.claude_session_id = JOB_SID
        reg(cfg, CLIENT, kind="interactive", parkedJobId=JOB)
        reg(cfg, CLIENT + 1, kind="interactive", parkedJobId=JOB)
    else:
        reg(cfg, CLIENT, kind="interactive", sessionId=SID)
        reg(cfg, CLIENT + 1, kind="interactive", sessionId=SID)
    proc.alive.update({CLIENT, CLIENT + 1})   # two real candidates
    assert claude_jobs.find_client(sess, claude_jobs.read_registry()) is None
    assert sess.claude_pid == 0


# ---- P2: parking and its clearing rule ------------------------------------------

def test_parked_job_id_parks_the_session(cfg, proc, mon, run_async, monkeypatch):
    """T2.8: the three fields, the link file and the pid map; one
    bg_parked event."""
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    reg(cfg, WORKER, kind="bg", sessionId=JOB_SID, jobId=JOB, status="busy")
    _scan(monitor, run_async, monkeypatch)
    assert sess.parked_job == JOB
    assert sess.parked_job_session_id == JOB_SID
    assert sess.parked_since > 0
    link = _link()
    assert link["job"] == JOB and link["session"] == NAME
    assert link["job_session_id"] == JOB_SID and link["ended"] is False
    assert _pidmap() == {"pids": {str(CLIENT): {"session": NAME, "start": None}}}
    events = [c.args[1:] for c in notify.await_args_list if c.args[1] == "bg_parked"]
    assert events == [("bg_parked", {"job": JOB})]
    _scan(monitor, run_async, monkeypatch)
    assert len([c for c in notify.await_args_list if c.args[1] == "bg_parked"]) == 1


def test_an_entry_without_parked_job_id_clears_the_park(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    assert monitor.check_parked(sess) is True
    assert bg_jobs.link_path(JOB).exists()
    reg(cfg, CLIENT, kind="interactive", sessionId=SID)
    assert monitor.check_parked(sess) is False
    assert (sess.parked_job, sess.parked_job_session_id, sess.parked_since) == ("", "", 0.0)
    assert not bg_jobs.link_path(JOB).exists()


def test_a_stopped_job_keeps_the_session_parked(cfg, proc, mon):
    """Claude Code leaves parkedJobId set after the job stops (sticky):
    the job's entry going away never clears the park."""
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    worker = reg(cfg, WORKER, kind="bg", sessionId=JOB_SID, jobId=JOB)
    assert monitor.check_parked(sess) is True
    worker.unlink()
    assert monitor.check_parked(sess) is True
    assert sess.parked_job == JOB and sess.parked_job_session_id == JOB_SID


def test_an_ended_mark_survives_a_link_rewrite(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    monitor.check_parked(sess)
    data = _link()
    data["ended"] = True
    bg_jobs.write_own_json(bg_jobs.link_path(JOB), data)
    monitor._links_written.clear()  # a daemon restart forgets what it wrote
    monitor.check_parked(sess)
    assert _link()["ended"] is True


def test_a_gone_or_restarting_session_is_not_checked(cfg, proc, mon):
    import time
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    sess.restarting_until = time.monotonic() + 30
    assert monitor.check_parked(sess) is False
    assert sess.parked_job == ""


# ---- P2: the transcript fallback ----------------------------------------------

def test_a_last_continued_in_record_parks_without_a_registry(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    (cfg / "sessions").rmdir()
    transcript(cfg, SID, [{"type": "user"}, {"type": "assistant"},
                          {"type": "continued-in", "continuedInSessionId": JOB_SID}])
    assert monitor.check_parked(sess) is True
    assert (sess.parked_job, sess.parked_job_session_id) == (JOB, JOB_SID)


def test_records_after_continued_in_do_not_park(cfg, proc, mon):
    """The old copy was resumed on purpose after the move."""
    registry, monitor, notify, sess = mon
    (cfg / "sessions").rmdir()
    transcript(cfg, SID, [{"type": "continued-in", "continuedInSessionId": JOB_SID},
                          {"type": "user"}])
    assert monitor.check_parked(sess) is False


def test_a_found_client_wins_over_the_transcript(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    reg(cfg, CLIENT, kind="interactive", sessionId=SID)
    transcript(cfg, SID, [{"type": "continued-in", "continuedInSessionId": JOB_SID}])
    assert monitor.check_parked(sess) is False


def test_a_huge_transcript_is_read_only_at_its_end(cfg, proc, mon, monkeypatch):
    """T2.9: a 5 MB transcript costs at most TAIL_READ_BYTES of reading."""
    registry, monitor, notify, sess = mon
    (cfg / "sessions").rmdir()
    path = cfg / "projects" / "-home-alice-app" / f"{SID}.jsonl"
    filler = json.dumps({"type": "assistant", "text": "x" * 1000}) + "\n"
    with open(path, "w") as f:
        for _ in range(5 * 1024):
            f.write(filler)
        f.write(json.dumps({"type": "continued-in", "continuedInSessionId": JOB_SID}) + "\n")
    assert path.stat().st_size > 5 * 1024 * 1024
    read = [0]
    real_open = claude_jobs._open_binary

    class Counting:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def seek(self, *a):
            return self.f.seek(*a)

        def tell(self):
            return self.f.tell()

        def read(self, n=-1):
            data = self.f.read(n)
            read[0] += len(data)
            return data

    monkeypatch.setattr(claude_jobs, "_open_binary",
                        lambda p, mode: Counting(real_open(p, mode)))
    assert monitor.check_parked(sess) is True
    assert 0 < read[0] <= claude_jobs.TAIL_READ_BYTES


def test_a_line_not_ended_yet_is_no_record(tmp_path):
    """Claude Code may be writing it right now: only a line its newline
    ended counts, even when it already parses."""
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(
        {"type": "continued-in", "continuedInSessionId": JOB_SID}))
    assert claude_jobs.transcript_continued_in(path) == ""
    path.write_text(path.read_text() + "\n")
    assert claude_jobs.transcript_continued_in(path) == JOB_SID


# ---- S7: nothing readable changes nothing --------------------------------------

@pytest.mark.parametrize("parked", ["", JOB], ids=["not-parked", "parked"])
@pytest.mark.parametrize("broken", ["no-folder", "bad-json"])
def test_an_unreadable_registry_changes_nothing(cfg, proc, mon, parked, broken):
    registry, monitor, notify, sess = mon
    sess.parked_job = parked
    sess.claude_pid = CLIENT
    if broken == "no-folder":
        (cfg / "sessions").rmdir()
    else:
        (cfg / "sessions" / f"{CLIENT}.json").write_text('{"pid": 4100, "kind": "inter')
    assert monitor.check_parked(sess) is bool(parked)
    assert sess.parked_job == parked


def test_a_broken_file_does_not_hide_the_others(cfg, proc, mon):
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    (cfg / "sessions" / "4000.json").write_text("{not json")
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    assert monitor.check_parked(sess) is True


# ---- T2.18: persistence --------------------------------------------------------

def test_the_park_survives_a_restart(cfg, mon, tmp_state_file, monkeypatch):
    registry, monitor, notify, sess = mon
    sess.parked_job = JOB
    sess.parked_job_session_id = JOB_SID
    sess.parked_since = 1_700_000_000.0
    sess.park_notified_job = JOB
    registry.save()
    raw = json.loads(tmp_state_file.read_text())
    raw["sessions"][NAME]["a_key_from_a_newer_aipager"] = {"x": 1}
    tmp_state_file.write_text(json.dumps(raw))
    monkeypatch.setattr("aipager.dtach.inject.live_session_names", lambda: {NAME})

    loaded = SessionRegistry()
    loaded.load()
    again = loaded.get(NAME)
    assert (again.parked_job, again.parked_job_session_id, again.parked_since,
            again.park_notified_job) == (JOB, JOB_SID, 1_700_000_000.0, JOB)
    assert again.claude_pid == 0 and again.park_told == set()

    from aipager.cli.daemon import _wire_parked_state
    _wire_parked_state(loaded, SimpleNamespace(), SimpleNamespace(check_parked=None),
                       SimpleNamespace())
    assert inject.is_parked(NAME) is True, "a loaded park is honoured at once"
    assert inject.is_parked("claude-other") is False


@pytest.mark.parametrize("job", ["../x", "", 5, "pids"])
def test_a_saved_job_id_that_names_no_file_loads_as_no_park(
        cfg, mon, tmp_state_file, monkeypatch, job):
    registry, monitor, notify, sess = mon
    registry.save()
    raw = json.loads(tmp_state_file.read_text())
    raw["sessions"][NAME].update(parked_job=job, parked_job_session_id=JOB_SID)
    tmp_state_file.write_text(json.dumps(raw))
    monkeypatch.setattr("aipager.dtach.inject.live_session_names", lambda: {NAME})
    loaded = SessionRegistry()
    loaded.load()
    assert loaded.get(NAME).parked_job == ""
    assert loaded.get(NAME).parked_job_session_id == ""


# ---- T2.19: an ended session's files ------------------------------------------

def _plant(sess):
    sess.parked_job = JOB
    sess.claude_pid = CLIENT
    bg_jobs.write_own_json(bg_jobs.link_path(JOB), {"job": JOB, "session": NAME})
    bg_jobs.write_own_json(bg_jobs.pidmap_path(), {"pids": {
        str(CLIENT): {"session": NAME, "start": None},
        "4300": {"session": "claude-backend", "start": None}}})


def test_gone_removes_the_link_and_the_pid_map_entry(cfg, mon):
    registry, monitor, notify, sess = mon
    _plant(sess)
    registry.transition(NAME, Status.GONE)
    assert not bg_jobs.link_path(JOB).exists()
    assert _pidmap() == {"pids": {"4300": {"session": "claude-backend", "start": None}}}
    assert sess.parked_job == JOB, "session_end still reads the park"


def test_remove_removes_the_link_and_the_pid_map_entry(cfg, mon):
    registry, monitor, notify, sess = mon
    _plant(sess)
    registry.remove(NAME)
    assert not bg_jobs.link_path(JOB).exists()
    assert str(CLIENT) not in _pidmap()["pids"]


def test_another_sessions_link_is_left_alone(cfg, mon):
    registry, monitor, notify, sess = mon
    _plant(sess)
    bg_jobs.write_own_json(bg_jobs.link_path(JOB), {"job": JOB, "session": "claude-backend"})
    registry.transition(NAME, Status.GONE)
    assert bg_jobs.link_path(JOB).exists()


def test_the_daemon_wires_one_source_of_truth(mon):
    """cli/daemon.py: the terminal guard reads the registry, and the
    receiver and the message gate both look for a park through the
    monitor's own check."""
    from aipager.cli.daemon import _wire_parked_state
    registry, monitor, notify, sess = mon
    receiver, bot = SimpleNamespace(), SimpleNamespace()
    _wire_parked_state(registry, receiver, monitor, bot)
    assert receiver.parked_check == monitor.check_parked
    assert bot.parked_check == monitor.check_parked
    assert inject.is_parked(NAME) is False
    sess.parked_job = JOB
    assert inject.is_parked(NAME) is True


def test_a_new_park_tells_every_sender_again(cfg, proc, mon):
    """The held notice goes once per sender per park: a new job empties
    the list of those told."""
    registry, monitor, notify, sess = mon
    sess.claude_pid = CLIENT
    sess.park_told = {12345}
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId=JOB)
    monitor.check_parked(sess)
    assert sess.park_told == set()
    sess.park_told.add(12345)
    monitor.check_parked(sess)
    assert sess.park_told == {12345}, "the same park keeps it"
    reg(cfg, CLIENT, kind="interactive", sessionId=SID, parkedJobId="ffff0000")
    monitor.check_parked(sess)
    assert sess.parked_job == "ffff0000" and sess.park_told == set()
