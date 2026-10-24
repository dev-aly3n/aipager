"""QA: no byte reaches a parked session's terminal (P4, S1), through the
daemon's own wiring of ``inject``'s parked probe.

design.md success criterion 4: "No byte is written to a parked session's
dtach by any path."

The real writers are taken from ``aipager.dtach.inject`` when this module
is imported (the stack replaces two of them with recorders). Only the
documented seam ``inject._create_subprocess_exec`` sees what would run.

Partitions: parked by the monitor / parked as loaded from disk (no tick) /
not parked / another session / a probe that fails (counts as parked).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager.dtach import inject

REAL = {name: getattr(inject, name) for name in (
    "send_now", "send_keys", "send_text_and_enter", "discard_queued_input",
    "press_mode_cycle")}


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

ARGS = {
    "send_now": (),
    "send_keys": ("Enter",),
    "send_text_and_enter": ("hello",),
    "discard_queued_input": (0,),
    "press_mode_cycle": (1,),
}
WRITERS = list(ARGS)


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


def _write(stack, writer, name=None):
    result = REAL[writer](name or stack.ids.NAME, *ARGS[writer])
    if asyncio.iscoroutine(result):
        result = stack.run(result)
    return result


@pytest.mark.parametrize("writer", WRITERS)
def test_a_session_parked_by_the_monitor_gets_no_write(stack, spawned, writer):
    qa.park_and_tick(stack)
    assert (_write(stack, writer), spawned) == (False, [])


@pytest.mark.parametrize("writer", WRITERS)
def test_a_park_loaded_from_disk_is_honoured_before_any_tick(stack, spawned, writer):
    stack.sess.parked_job = stack.ids.JOB
    stack.sess.parked_job_session_id = stack.ids.JS
    assert (_write(stack, writer), spawned) == (False, [])


def test_a_session_that_is_not_parked_is_not_parked(stack):
    assert inject.is_parked(stack.ids.NAME) is False


def test_an_unknown_session_is_not_parked(stack):
    qa.park_and_tick(stack)
    assert inject.is_parked("claude-nobody") is False


def test_another_session_is_not_held_back_by_this_ones_park(stack, spawned):
    qa.park_and_tick(stack)
    other = stack.registry.get_or_create("claude-backend")
    other.label = "backend"
    assert inject.is_parked(other.name) is False


def test_a_bring_back_lets_writes_through_again(stack):
    qa.park_and_tick(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert inject.is_parked(stack.ids.NAME) is False


@pytest.mark.parametrize("writer", WRITERS)
def test_a_failing_probe_counts_as_parked(stack, spawned, writer):
    def broken(_name):
        raise RuntimeError("registry unavailable")
    inject.set_parked_probe(broken)
    assert (_write(stack, writer), spawned) == (False, [])


def test_one_warning_per_park(stack, spawned, caplog):
    qa.park_and_tick(stack)
    caplog.set_level(logging.WARNING)
    for _ in range(4):
        _write(stack, "send_keys")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING
                and stack.ids.NAME in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.xfail(strict=True, reason=(
    "qa finding tester-iter1-001: the once-per-park warning re-arms only when a "
    "write goes out while not parked, so a second park with no write in between "
    "is never logged (DESIGN.md P4: logs once per park)"))
def test_a_new_park_warns_again(stack, spawned, caplog):
    qa.park_and_tick(stack)
    caplog.set_level(logging.WARNING)
    _write(stack, "send_keys")
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert not stack.sess.parked_job, "precondition: cleared"
    qa.park_and_tick(stack)
    _write(stack, "send_keys")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING
                and stack.ids.NAME in r.getMessage()]
    assert len(warnings) == 2


def test_a_new_park_after_a_write_in_between_warns_again(stack, spawned, caplog):
    """The same two parks, with one write going out while not parked."""
    qa.park_and_tick(stack)
    caplog.set_level(logging.WARNING)
    _write(stack, "send_keys")
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert _write(stack, "send_keys") is True, "precondition: writes go out again"
    qa.park_and_tick(stack)
    _write(stack, "send_keys")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING
                and stack.ids.NAME in r.getMessage()]
    assert len(warnings) == 2
