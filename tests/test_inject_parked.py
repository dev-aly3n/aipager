"""The terminal guard for sessions parked on a Claude Code background job.

A session whose own claude moved its conversation into a background job
(``←``) shows Claude Code's agent view, and text typed there can start a
stray background job. ``aipager.dtach.inject`` therefore asks a probe (the
daemon wires it to its session registry) before every write, and again
once it holds the session's write lock: a park noticed while a writer
waited for the lock still refuses. A refused write runs no ``dtach -p``.

Only the module seam ``inject._create_subprocess_exec`` is replaced here,
never asyncio or os.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager.dtach import inject

NAME = "claude-frontend"


class _Proc:
    returncode = 0

    async def communicate(self, _stdin=None):
        return b"", b""


@pytest.fixture
def spawned(monkeypatch):
    """Every ``dtach -p`` the writers start, as its argv."""
    calls: list[tuple] = []

    async def fake_exec(*args, **_kw):
        calls.append(args)
        return _Proc()

    async def no_gap(_seconds):
        return None

    monkeypatch.setattr(inject, "_create_subprocess_exec", fake_exec)
    monkeypatch.setattr(inject, "_key_gap_sleep", no_gap)
    monkeypatch.setattr(inject, "SEND_NOW_CHORD_GAP", 0.0)
    return calls


WRITERS = {
    "send_now": lambda: inject.send_now(NAME),
    "send_keys": lambda: inject.send_keys(NAME, "Enter"),
    "send_text_and_enter": lambda: inject.send_text_and_enter(NAME, "hello"),
    "discard_queued_input": lambda: inject.discard_queued_input(NAME, 0),
    "press_mode_cycle": lambda: inject.press_mode_cycle(NAME, 2),
}
WRITER_IDS = list(WRITERS)


def _parked(names: set[str]):
    return lambda name: name in names


@pytest.mark.parametrize("writer", WRITER_IDS)
def test_a_parked_session_gets_no_write(writer, spawned, run_async):
    """S1: the probe says parked, so the writer returns False and no
    ``dtach -p`` is ever started (the outer check)."""
    inject.set_parked_probe(_parked({NAME}))
    assert run_async(WRITERS[writer]()) is False
    assert spawned == []


@pytest.mark.parametrize("writer", WRITER_IDS)
def test_a_park_seen_while_waiting_for_the_lock_still_refuses(
        writer, spawned, run_async):
    """The inner check: the writer passed the first check, waited for the
    write lock another write held, and the park was noticed meanwhile."""
    parked: set[str] = set()
    inject.set_parked_probe(_parked(parked))

    async def scenario():
        lock = inject._write_lock(NAME)
        await lock.acquire()
        task = asyncio.ensure_future(WRITERS[writer]())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done(), "the writer did not wait for the lock"
        parked.add(NAME)
        lock.release()
        return await task

    assert run_async(scenario()) is False
    assert spawned == []


@pytest.mark.parametrize("writer", WRITER_IDS)
def test_a_parked_writer_does_not_wait_for_the_lock(writer, spawned, run_async):
    """The outer check: a write to a session already parked is refused at
    once, never queued behind another write's lock hold."""
    inject.set_parked_probe(_parked({NAME}))

    async def scenario():
        lock = inject._write_lock(NAME)
        await lock.acquire()
        task = asyncio.ensure_future(WRITERS[writer]())
        for _ in range(5):
            await asyncio.sleep(0)
        done = task.done()
        lock.release()
        return done, await task

    assert run_async(scenario()) == (True, False)
    assert spawned == []


@pytest.mark.parametrize("writer", WRITER_IDS)
def test_by_default_every_write_goes_out(writer, spawned, run_async):
    """T2.12: the default probe parks nothing; writes run as before."""
    inject.set_parked_probe(None)
    assert run_async(WRITERS[writer]()) is True
    assert spawned and all(call[1] == "-p" for call in spawned)


def test_another_session_is_still_written(spawned, run_async):
    inject.set_parked_probe(_parked({"claude-other"}))
    assert run_async(inject.send_keys(NAME, "Enter")) is True
    assert len(spawned) == 1


def test_a_probe_that_raises_counts_as_parked(spawned, run_async):
    def broken(_name):
        raise RuntimeError("registry gone")

    inject.set_parked_probe(broken)
    assert inject.is_parked(NAME) is True
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is False
    assert spawned == []


def _refusals(caplog) -> int:
    return sum(1 for r in caplog.records
               if r.levelno == logging.WARNING and "parked on a Claude Code" in r.getMessage())


def test_one_warning_per_park(spawned, run_async, caplog):
    parked = {NAME}
    inject.set_parked_probe(_parked(parked))
    with caplog.at_level(logging.WARNING, logger="aipager.dtach.inject"):
        run_async(inject.send_keys(NAME, "Enter"))
        run_async(inject.send_text_and_enter(NAME, "hello"))
        assert _refusals(caplog) == 1
        parked.clear()
        assert run_async(inject.send_keys(NAME, "Enter")) is True
        parked.add(NAME)
        run_async(inject.send_keys(NAME, "Enter"))
        assert _refusals(caplog) == 2, "a new park logs again"


def test_a_new_park_with_no_write_in_between_warns_again(spawned, run_async, caplog):
    """The probe names the park (the daemon's: job and park number): a
    second park is told apart from the first even when nothing was written
    while not parked (QA tester-iter1-001)."""
    park = {"now": ("ab12cd34", 0)}
    inject.set_parked_probe(lambda name: park["now"] if name == NAME else False)
    with caplog.at_level(logging.WARNING, logger="aipager.dtach.inject"):
        run_async(inject.send_keys(NAME, "Enter"))
        run_async(inject.send_keys(NAME, "Enter"))
        assert _refusals(caplog) == 1
        park["now"] = ("ab12cd34", 1)     # unparked and parked again, same job
        run_async(inject.send_keys(NAME, "Enter"))
        run_async(inject.send_keys(NAME, "Enter"))
        assert _refusals(caplog) == 2
    assert spawned == []


def test_the_daemons_probe_names_each_park(tmp_state_file):
    from types import SimpleNamespace

    from aipager.cli.daemon import _wire_parked_state
    from aipager.state import SessionRegistry
    registry = SessionRegistry()
    sess = registry.get_or_create(NAME)
    _wire_parked_state(registry, SimpleNamespace(),
                       SimpleNamespace(check_parked=None, check_parked_async=None), SimpleNamespace())
    sess.parked_job = "ab12cd34"
    first = inject._parked_probe(NAME)
    sess.forget_park()
    assert inject._parked_probe(NAME) is False
    sess.parked_job = "ab12cd34"
    assert first and inject._parked_probe(NAME) and inject._parked_probe(NAME) != first
