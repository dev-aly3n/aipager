"""QA iteration 2: a safety halt that cannot stop the background job says so.

Contract (iteration-2 brief; design.md B1 "_halt_for_safety on a parked
session runs it with force=True, release_held=False ... then does its
existing card notice"): a safety halt that cannot stop the background job
says so in its notice. design.md's reasons: ``bg.err.stop_timeout`` "claude
stop did not answer in time", ``bg.err.stop_exit`` "claude stop ended with
exit code {code}", ``bg.err.still_running`` "the background job is still
running", ``bg.err.no_claude`` "the claude command could not be run".

Partitions for the forced stop the halt runs:
- succeeds (control: no reason, no word about the background);
- exits 1 / times out / cannot be run / leaves the job running;
- never gets to run, because a bring-back is already running and does not
  end.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import claude_jobs


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

HALT = "🛑 <b>frontend</b> · Blocked by safety policy"
REASONS = {
    "rc": "claude stop ended with exit code 1",
    "timeout": "claude stop did not answer in time",
    "missing": "the claude command could not be run",
    "still_running": "the background job is still running",
}


def _stop_fails(stack, monkeypatch, how):
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) != ["stop"]:
            return await inner(*argv, **kw)
        stack.claude.runs.append(list(argv[1:]))
        if how == "missing":
            raise FileNotFoundError(argv[0])
        if how == "still_running":
            class Done:
                returncode = None

                async def communicate(self, _stdin=None):
                    self.returncode = 0          # says it stopped; the job lives on
                    return b"", b""

                def kill(self):
                    pass
            return Done()

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if how == "timeout":
                    raise asyncio.TimeoutError
                self.returncode = 1
                return b"", b""

            def kill(self):
                pass

            async def wait(self):
                return self.returncode
        return Proc()
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)


def _halt(stack):
    stack.run(stack.bot._halt_for_safety(stack.sess, "qa halt"))
    notices = [t for t in qa.sent_texts(stack) if t.startswith(HALT)]
    assert len(notices) == 1, notices
    return notices[0]


FAILS = list(REASONS)


# ---- control -------------------------------------------------------------------------

def test_a_halt_that_stopped_the_job_names_no_failure(stack):
    qa.park_and_tick(stack, job_status="busy")
    notice = _halt(stack)
    assert [r for r in REASONS.values() if r in notice] == []


def test_a_halt_that_stopped_the_job_says_nothing_about_the_background(stack):
    qa.park_and_tick(stack, job_status="busy")
    notice = _halt(stack)
    assert "background" not in notice


# ---- the stop fails ------------------------------------------------------------------

@pytest.mark.parametrize("how", FAILS)
def test_a_halt_whose_stop_failed_names_the_reason(stack, monkeypatch, how):
    qa.park_and_tick(stack, job_status="busy")
    _stop_fails(stack, monkeypatch, how)
    notice = _halt(stack)
    assert REASONS[how] in notice


@pytest.mark.parametrize("how", FAILS)
def test_a_halt_whose_stop_failed_says_it_is_about_the_background_job(stack, monkeypatch,
                                                                       how):
    qa.park_and_tick(stack, job_status="busy")
    _stop_fails(stack, monkeypatch, how)
    notice = _halt(stack)
    assert "background" in notice


@pytest.mark.parametrize("how", FAILS)
def test_a_halt_whose_stop_failed_kills_nothing(stack, monkeypatch, how):
    """S5: nothing is killed when the stop did not succeed."""
    qa.park_and_tick(stack, job_status="busy")
    _stop_fails(stack, monkeypatch, how)
    _halt(stack)
    assert [k for k in stack.kinds() if k in ("kill", "launch")] == []


@pytest.mark.parametrize("how", FAILS)
def test_a_halt_whose_stop_failed_types_nothing(stack, monkeypatch, how):
    qa.park_and_tick(stack, job_status="busy")
    _stop_fails(stack, monkeypatch, how)
    _halt(stack)
    assert ("keys" not in stack.kinds(), stack.typed) == (True, [])


# ---- a bring-back already runs and never ends ----------------------------------------

def test_a_halt_that_could_not_get_past_a_running_bring_back_says_so(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.sess.relaunch_in_flight = True          # never ends in this test
    notice = _halt(stack)
    assert "background" in notice


def test_a_halt_that_could_not_get_past_a_running_bring_back_stops_nothing(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.sess.relaunch_in_flight = True
    _halt(stack)
    assert (qa.stops(stack), [k for k in stack.kinds() if k in ("kill", "launch")]) == (
        [], [])
