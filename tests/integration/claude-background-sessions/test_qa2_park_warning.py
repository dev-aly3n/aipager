"""QA iteration 2: the refused-write warning is logged once per park.

Contract (iteration-2 brief; entrypoints.md "Logs: one warning per park
when a write is refused"; DESIGN.md P4 "logs once per park").

Partitions:
- writes in one park: one / many, through every writer;
- parks: one / a second job straight after the first (no unpark in
  between) / three parks with unparks between / a park after a bring-back;
- a park loaded from disk (no tick);
- control: no park, no warning.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager.dtach import inject

REAL = {name: getattr(inject, name) for name in (
    "send_now", "send_keys", "send_text_and_enter", "discard_queued_input",
    "press_mode_cycle")}
ARGS = {
    "send_now": (),
    "send_keys": ("Enter",),
    "send_text_and_enter": ("hello",),
    "discard_queued_input": (0,),
    "press_mode_cycle": (1,),
}


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

JOB2, JS2, WORKER2 = "ef90ab12", "ef90ab12-0000-4000-8000-000000000007", 4201
JOB3, JS3, WORKER3 = "0a1b2c3d", "0a1b2c3d-0000-4000-8000-000000000008", 4202


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    class Proc:
        returncode = 0

        async def communicate(self, _stdin=None):
            return b"", b""

        async def wait(self):
            return 0

    async def fake_exec(*argv, **_kw):
        calls.append(argv)
        return Proc()
    monkeypatch.setattr(inject, "_create_subprocess_exec", fake_exec)
    return calls


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.WARNING)
    return caplog


def _write(stack, writer="send_keys"):
    result = REAL[writer](stack.ids.NAME, *ARGS[writer])
    if asyncio.iscoroutine(result):
        result = stack.run(result)
    return result


def _warnings(stack, caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING
            and r.name.startswith("aipager.dtach.inject")
            and stack.ids.NAME in r.getMessage()]


def _unpark(stack):
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert not stack.sess.parked_job, "precondition: the park ended"


def test_control_no_park_no_warning(stack, spawned, logged):
    _write(stack)
    assert _warnings(stack, logged) == []


def test_one_refused_write_warns_once(stack, spawned, logged):
    qa.park_and_tick(stack)
    assert _write(stack) is False, "precondition: refused"
    assert len(_warnings(stack, logged)) == 1


def test_many_refused_writes_through_every_writer_warn_once(stack, spawned, logged):
    qa.park_and_tick(stack)
    for _ in range(3):
        for writer in REAL:
            _write(stack, writer)
    assert len(_warnings(stack, logged)) == 1


def test_a_second_job_straight_after_the_first_warns_again(stack, spawned, logged):
    qa.park_and_tick(stack)
    _write(stack)
    qa.park_and_tick(stack, job=JOB2, js=JS2, worker=WORKER2)
    _write(stack)
    _write(stack)
    assert len(_warnings(stack, logged)) == 2


def test_three_parks_warn_three_times(stack, spawned, logged):
    for job, js, worker in ((None, None, None), (JOB2, JS2, WORKER2), (JOB3, JS3, WORKER3)):
        qa.park_and_tick(stack, job=job, js=js, worker=worker)
        _write(stack)
        _write(stack)
        _unpark(stack)
    assert len(_warnings(stack, logged)) == 3


def test_a_park_after_a_bring_back_warns_again(stack, spawned, logged):
    qa.park_and_tick(stack)
    _write(stack)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert out.ok, "precondition: brought back"
    qa.relaunched_start(stack)
    assert _write(stack) is True, "precondition: writes go out again"
    # The relaunched claude parks on a new job.
    stack.claude.reg(stack.ids.RELAUNCHED, kind="interactive", sessionId=stack.ids.JS,
                     status="idle", parkedJobId=JOB2)
    stack.claude.reg(WORKER2, kind="bg", jobId=JOB2, sessionId=JS2, status="idle")
    # Later: the bring-back's restart quiet window is over (a park inside it is
    # tester-iter2-002, test_qa2_fresh_park_mid_scan.py).
    stack.sess.restarting_until = 0
    stack.tick()
    assert stack.sess.parked_job == JOB2, "precondition: parked again"
    _write(stack)
    assert len(_warnings(stack, logged)) == 2


def test_a_park_loaded_from_disk_warns_once(stack, spawned, logged):
    stack.sess.parked_job = stack.ids.JOB
    stack.sess.parked_job_session_id = stack.ids.JS
    for _ in range(4):
        _write(stack)
    assert len(_warnings(stack, logged)) == 1


def test_the_same_park_across_ticks_warns_once(stack, spawned, logged):
    qa.park_and_tick(stack)
    _write(stack)
    stack.tick()
    stack.tick()
    _write(stack)
    assert len(_warnings(stack, logged)) == 1
