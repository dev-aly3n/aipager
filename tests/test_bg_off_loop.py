"""Claude's files are read off the event loop on the hot paths (review
rev-iter1-012).

Reading the registry stats every ``<config>/sessions/*.json``, and looking
for a transcript lists every project folder: on a machine with many
projects either can take a while, and the daemon has one event loop. The
message gate, the hook receiver and the bring-back read through
``claude_jobs.off_loop`` (a worker thread). The monitor's own scan reads the
registry once per tick on the loop (each file parsed once per change) and
caches its transcript misses. These tests record the thread every registry
read and transcript search runs on, and require that none ran on the
loop's.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from aipager import claude_jobs
from aipager.dtach import hook_receiver as hr
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status
from tests.test_bring_back_core import CLIENT, JOB, JS, NAME, X, World


@pytest.fixture
def threads(monkeypatch):
    """``{"registry": [...], "glob": [...]}``: the thread of each read."""
    seen = {"registry": [], "glob": []}
    read, glob = claude_jobs.read_registry, claude_jobs._glob_transcripts

    def read_registry(config=None):
        seen["registry"].append(threading.get_ident())
        return read(config)

    def glob_transcripts(projects, session_id):
        seen["glob"].append(threading.get_ident())
        return glob(projects, session_id)

    monkeypatch.setattr(claude_jobs, "read_registry", read_registry)
    monkeypatch.setattr(claude_jobs, "_glob_transcripts", glob_transcripts)
    claude_jobs._FIND_CACHE.clear()
    return seen


def _off_the_loop(seen, *, glob=True):
    loop_thread = threading.get_ident()
    assert seen["registry"], "the registry was read"
    if glob:
        assert seen["glob"], "a transcript was looked for"
    on_loop = [k for k, ids in seen.items() for i in ids if i == loop_thread]
    assert on_loop == [], f"read on the event loop: {on_loop}"


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path):
    w = World(mk_bot, monkeypatch, tmp_path)
    yield w
    w.close()


def test_a_bring_back_reads_claudes_files_off_the_loop(world, threads, run_async):
    world.holder_gone_after = None
    out = run_async(world.bot._bring_back_core(world.sess))
    assert out.ok
    _off_the_loop(threads)


def test_a_restart_with_a_held_conversation_reads_off_the_loop(world, threads,
                                                              run_async):
    """R2 (a session not parked whose conversation a job holds)."""
    sess = world.sess
    sess.parked_job = ""
    sess.claude_session_id = JS
    assert run_async(world.bot._restart_session_core(sess)).ok
    _off_the_loop(threads)


def test_a_jobs_state_without_a_registry_read_reads_it_off_the_loop(world, threads,
                                                                   run_async):
    """The nit of review 2: ``job_state`` given no entries (the caller's
    read failed) reads the registry again, in a worker thread."""
    assert run_async(claude_jobs.job_state(JOB, None)) == claude_jobs.IDLE
    _off_the_loop(threads, glob=False)


def test_the_parked_check_reads_off_the_loop(world, threads, run_async):
    """The check the gate and the receiver call: here the registry gives
    no client, so the transcript decides."""
    sess = world.sess
    sess.parked_job = ""
    world.unreg(CLIENT)
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert run_async(monitor.check_parked_async(sess)) is True
    assert sess.parked_job == JOB and sess.parked_source == "transcript"
    _off_the_loop(threads)


def test_the_receiver_awaits_the_daemons_check(tmp_path, monkeypatch, run_async):
    """D2 with the daemon's (async) check: a worker's first datagram,
    before any tick, parks the session and is taken as linked."""
    cfg = tmp_path / "claude-config"
    (cfg / "sessions").mkdir(parents=True)
    (cfg / "projects").mkdir()
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: cfg)
    monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid == CLIENT)
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: None)
    (cfg / "sessions" / f"{CLIENT}.json").write_text(json.dumps(
        {"pid": CLIENT, "kind": "interactive", "sessionId": X, "parkedJobId": JOB}))
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    sess.claude_session_id = X
    sess.claude_pid = CLIENT
    registry.transition(NAME, Status.BUSY)
    notify = AsyncMock()
    monitor = SessionMonitor(registry, notify)
    recv = hr.HookReceiver(registry, notify)
    recv.parked_check = monitor.check_parked_async
    before = sess.last_hook_at
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "fork", "session": NAME,
        "bg_job": JOB, "session_id": JS, "n": int(time.time())}).encode()))
    assert sess.parked_job == JOB
    assert sess.last_hook_at > before, "taken as linked, not dropped (D2)"


def test_the_gate_awaits_the_daemons_check(world, run_async, mk_update):
    """The message gate with the daemon's (async) check: a park it finds
    holds the message."""
    sess = world.sess
    sess.parked_job = ""

    async def check(s):
        s.parked_job = JOB
        return True

    world.bot.parked_check = check
    world.bot._react = AsyncMock()
    world.bot._bring_back_refusal = lambda *a: "prompt"
    world.bot._tell_held_for_park = AsyncMock()
    update = mk_update("hello", chat_id=-1001)
    assert run_async(world.bot._hold_for_open_dialog(update, sess, "hello")) is True
    assert [q[0] for q in sess.pending_queue] == ["hello"]
