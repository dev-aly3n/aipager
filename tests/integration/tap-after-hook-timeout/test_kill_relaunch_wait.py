"""A rule's waiting automatic Deny of a background agent's prompt, then
/kill or a kill and relaunch, with no SessionEnd from Claude Code
(design.md "Success criteria" 6: "a prompt closed, /stop, kill or turn
end during the wait types nothing and leaves no pending retry";
entrypoints.md "Rule-based automatic Deny": "kill ... during the wait: no
key, no notice from the wait, no pending retry left").

Surfaces: ``bot._kill_session_core(name, label)`` (/kill's End, the Mini
App kill) and ``bot._kill_and_relaunch_core(sess, target_mode=...,
interrupt_first=...)`` (/perms, the Mini App restart), driven as the
repo's other tests drive them (``inject.kill_session``,
``inject.launch_session``, ``session_ops._sleep`` and ``session_ops.Path``
replaced). The retry's wait is the ``aipager.bot.auth._sleep`` seam (the
``Clock`` of test_auto_deny_wait.py: it holds the retry until the test
lets time run).

Two moments for each: the retry still sleeping (the kill must cancel it)
and the retry already awake (it must type nothing for a session that was
removed, replaced by a new one of the same name, or is relaunching).
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest

from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import S

# The rule rig, its clock and hook helpers from the sibling scenario file
# (this folder's name is not an identifier, so import it by name).
_pkg = __name__.rpartition(".")[0]
_adw = importlib.import_module(f"{_pkg}.test_auto_deny_wait" if _pkg else "test_auto_deny_wait")
drig = _adw.drig
agent_write, ahook, alaunch = _adw.agent_write, _adw.ahook, _adw.alaunch

WAIT = 5.0


async def _finish(task):
    """Let a captured retry task end however it ends (bounded)."""
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), WAIT)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError as exc:
        raise AssertionError("the retry never ended") from exc


def _other_live_tasks():
    me = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


def _kill_mocks(monkeypatch, on_kill=None):
    async def _kill(name):
        if on_kill is not None:
            await on_kill()
        return True

    monkeypatch.setattr(inject, "kill_session", _kill)


def _relaunch_mocks(monkeypatch, *, on_kill=None, on_launch=None):
    async def _kill(name):
        if on_kill is not None:
            await on_kill()
        return True

    async def _launch(short_name, **kw):
        if on_launch is not None:
            await on_launch()
        return True, ""

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=False))))


def _recreate(bot, status=Status.INTERACTIVE):
    """A new session of the same name (a new launch, or the monitor's
    GONE record), waiting at the prompt: a retry that looked the name up
    again would find it."""
    new = bot.registry.get_or_create(S)
    new.label = "api"
    new.status = status
    return new


def _go(drig, scenario):
    out = {}

    async def _main():
        out["v"] = await scenario()
    drig.run_async(_main())
    return out["v"]


# -- /kill while the retry sleeps --------------------------------------------------------

def kill_while_sleeping(drig, monkeypatch, *, hook_gone=False, recreate=Status.INTERACTIVE):
    _kill_mocks(monkeypatch)

    async def _scenario():
        await alaunch(drig)
        if hook_gone:
            drig.hook_ok = False
            await agent_write(drig, hook=True)
            await ahook(drig, "permission_reply_timeout", aipager_request_id="req-w")
        else:
            await agent_write(drig)
        snap = {"pending_before": drig.bot.pending_auto_deny_retry(S),
                "status_before": drig.sess.status}
        task = drig.bot.auto_deny_retry_task(S)
        snap["task_before"] = task is not None
        outcome = await drig.bot._kill_session_core(S, "api")
        snap["outcome"] = getattr(outcome, "result", None)
        snap["removed"] = drig.bot.registry.get(S) is None
        snap["keys_at_kill"] = list(drig.keys)
        snap["sent_at_kill"] = len(drig.sent)
        # Time is still held: a retry the kill did not cancel stays blocked.
        if task is not None:
            await asyncio.wait({task}, timeout=1.0)
        snap["task_done_after_kill"] = task is None or task.done()
        new = _recreate(drig.bot, recreate) if recreate is not None else None
        snap["pending_after"] = drig.bot.pending_auto_deny_retry(S)
        snap["task_after"] = drig.bot.auto_deny_retry_task(S)
        drig.clock.let_time_run()
        await _finish(task)
        await _finish(drig.bot.auto_deny_retry_task(S))
        for _ in range(5):
            await asyncio.sleep(0)
        snap["leftover_tasks"] = len(_other_live_tasks())
        snap["pending_end"] = drig.bot.pending_auto_deny_retry(S)
        snap["new_status"] = new.status if new is not None else None
        return snap
    return _go(drig, _scenario)


HOW = [False, True]
HOW_IDS = ["no-reply-channel", "hook-gone-then-timeout"]


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_sleeping_precondition_a_retry_waits(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert (snap["pending_before"], snap["task_before"],
            snap["status_before"]) == (True, True, Status.INTERACTIVE)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_sleeping_precondition_the_kill_removed_the_session(
        drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert (snap["outcome"], snap["removed"]) == ("killed", True)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_cancels_the_sleeping_retry_without_a_session_end(
        drig, monkeypatch, hook_gone):
    """Time never ran: the retry ended only because the kill cancelled it."""
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["task_done_after_kill"] is True


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_during_wait_types_no_escape_ever(drig, monkeypatch, hook_gone):
    kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert drig.keys == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_during_wait_posts_no_blocked_notice(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert drig.sent[snap["sent_at_kill"]:] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_during_wait_sends_no_hook_decision(drig, monkeypatch, hook_gone):
    kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert drig.decisions == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_recreated_session_has_no_pending_retry(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert (snap["pending_after"], snap["task_after"]) == (False, None)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_recreated_session_has_no_retry_after_time_runs(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["pending_end"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_recreated_session_is_left_as_it_was(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["new_status"] == Status.INTERACTIVE


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_kill_during_wait_leaves_no_task_running(drig, monkeypatch, hook_gone):
    snap = kill_while_sleeping(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["leftover_tasks"] == 0


def test_kill_then_monitor_gone_record_types_nothing(drig, monkeypatch):
    """The monitor records the vanished session as GONE under the same
    name: nothing is typed, nothing waits."""
    snap = kill_while_sleeping(drig, monkeypatch, recreate=Status.GONE)
    assert (drig.keys, snap["pending_end"]) == ([], False)


def test_kill_with_no_recreate_types_nothing(drig, monkeypatch):
    kill_while_sleeping(drig, monkeypatch, recreate=None)
    assert drig.keys == []


# -- /kill once the retry has woken (it is past its sleep) -------------------------------

def kill_while_awake(drig, monkeypatch, *, recreate=Status.INTERACTIVE):
    _kill_mocks(monkeypatch)
    snap: dict = {}

    async def _kill_now():
        outcome = await drig.bot._kill_session_core(S, "api")
        snap["outcome"] = getattr(outcome, "result", None)
        snap["sent_at_kill"] = len(drig.sent)
        snap["keys_at_kill"] = list(drig.keys)
        if recreate is not None:
            snap["new"] = _recreate(drig.bot, recreate)

    async def _scenario():
        await alaunch(drig)
        drig.clock.during = _kill_now
        await agent_write(drig)
        task = drig.bot.auto_deny_retry_task(S)
        snap["task_before"] = task is not None
        drig.clock.let_time_run()
        await _finish(task)
        for _ in range(MAX := 20):
            nxt = drig.bot.auto_deny_retry_task(S)
            if nxt is None:
                break
            await _finish(nxt)
        else:
            raise AssertionError(f"retries never settled in {MAX} rounds")
        for _ in range(5):
            await asyncio.sleep(0)
        snap["leftover_tasks"] = len(_other_live_tasks())
        snap["pending_end"] = drig.bot.pending_auto_deny_retry(S)
        new = snap.get("new")
        snap["new_status"] = new.status if new is not None else None
        return snap
    return _go(drig, _scenario)


def test_kill_awake_precondition_the_retry_ran_into_the_kill(drig, monkeypatch):
    snap = kill_while_awake(drig, monkeypatch)
    assert (snap["task_before"], snap["outcome"], drig.clock.delays != []) == (
        True, "killed", True)


def test_kill_awake_types_no_escape_for_the_removed_session(drig, monkeypatch):
    kill_while_awake(drig, monkeypatch)
    assert drig.keys == []


def test_kill_awake_posts_no_blocked_notice(drig, monkeypatch):
    snap = kill_while_awake(drig, monkeypatch)
    assert drig.sent[snap["sent_at_kill"]:] == []


def test_kill_awake_leaves_no_retry_for_the_recreated_session(drig, monkeypatch):
    snap = kill_while_awake(drig, monkeypatch)
    assert snap["pending_end"] is False


def test_kill_awake_leaves_no_orphan_retry_running(drig, monkeypatch):
    """No fresh retry scheduled for the removed session object either."""
    snap = kill_while_awake(drig, monkeypatch)
    assert snap["leftover_tasks"] == 0


def test_kill_awake_sleeps_only_once(drig, monkeypatch):
    """The woken retry found its session removed and went no further: no
    second wait."""
    kill_while_awake(drig, monkeypatch)
    assert len(drig.clock.delays) == 1


def test_kill_awake_recreated_session_is_left_as_it_was(drig, monkeypatch):
    snap = kill_while_awake(drig, monkeypatch)
    assert snap["new_status"] == Status.INTERACTIVE


def test_kill_awake_with_monitor_gone_record_types_nothing(drig, monkeypatch):
    kill_while_awake(drig, monkeypatch, recreate=Status.GONE)
    assert drig.keys == []


# -- kill and relaunch while the retry sleeps --------------------------------------------

def relaunch_while_sleeping(drig, monkeypatch):
    snap: dict = {}
    holder: dict = {}

    async def _at_kill():
        task = holder.get("task")
        # Give a requested cancel the loop turns it needs; time stays held.
        for _ in range(3):
            await asyncio.sleep(0)
        snap["pending_at_kill"] = drig.bot.pending_auto_deny_retry(S)
        snap["task_done_at_kill"] = task is None or task.done()
        snap["status_at_kill"] = drig.sess.status
        snap["keys_at_kill"] = list(drig.keys)

    _relaunch_mocks(monkeypatch, on_kill=_at_kill)

    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        snap["pending_before"] = drig.bot.pending_auto_deny_retry(S)
        holder["task"] = task = drig.bot.auto_deny_retry_task(S)
        outcome = await drig.bot._kill_and_relaunch_core(
            drig.sess, target_mode="full", interrupt_first=False)
        snap["ok"] = getattr(outcome, "ok", None)
        snap["sent_after"] = len(drig.sent)
        snap["pending_after"] = drig.bot.pending_auto_deny_retry(S)
        drig.clock.let_time_run()
        await _finish(task)
        await _finish(drig.bot.auto_deny_retry_task(S))
        for _ in range(5):
            await asyncio.sleep(0)
        snap["leftover_tasks"] = len(_other_live_tasks())
        snap["pending_end"] = drig.bot.pending_auto_deny_retry(S)
        snap["status_end"] = drig.sess.status
        return snap
    return _go(drig, _scenario)


def test_relaunch_sleeping_precondition_a_retry_waits(drig, monkeypatch):
    assert relaunch_while_sleeping(drig, monkeypatch)["pending_before"] is True


def test_relaunch_sleeping_precondition_the_relaunch_succeeds(drig, monkeypatch):
    assert relaunch_while_sleeping(drig, monkeypatch)["ok"] is True


def test_relaunch_cancels_the_retry_before_the_kill(drig, monkeypatch):
    snap = relaunch_while_sleeping(drig, monkeypatch)
    assert (snap["pending_at_kill"], snap["task_done_at_kill"]) == (False, True)


def test_relaunch_types_no_escape_for_the_refusal(drig, monkeypatch):
    relaunch_while_sleeping(drig, monkeypatch)
    assert drig.keys == []


def test_relaunch_posts_no_blocked_notice_after(drig, monkeypatch):
    snap = relaunch_while_sleeping(drig, monkeypatch)
    assert drig.sent[snap["sent_after"]:] == []


def test_relaunch_leaves_no_pending_retry(drig, monkeypatch):
    snap = relaunch_while_sleeping(drig, monkeypatch)
    assert (snap["pending_after"], snap["pending_end"]) == (False, False)


def test_relaunch_leaves_no_task_running(drig, monkeypatch):
    assert relaunch_while_sleeping(drig, monkeypatch)["leftover_tasks"] == 0


def test_relaunch_sends_no_hook_decision(drig, monkeypatch):
    relaunch_while_sleeping(drig, monkeypatch)
    assert drig.decisions == []


# -- a woken retry runs while a relaunch is in flight ------------------------------------

def relaunch_while_awake(drig, monkeypatch):
    """The retry wakes; a relaunch starts and is held inside its launch
    (the old claude killed, the new one not yet up, the session still the
    registry's); the retry runs its course; then the launch completes."""
    snap: dict = {}
    in_launch = asyncio.Event()
    release = asyncio.Event()
    holder: dict = {}

    async def _hold_launch():
        snap["status_in_launch"] = drig.sess.status
        snap["in_flight_in_launch"] = getattr(drig.sess, "relaunch_in_flight", None)
        snap["still_registered"] = drig.bot.registry.get(S) is drig.sess
        in_launch.set()
        await asyncio.wait_for(release.wait(), WAIT)

    _relaunch_mocks(monkeypatch, on_launch=_hold_launch)

    async def _start_relaunch():
        holder["relaunch"] = asyncio.ensure_future(drig.bot._kill_and_relaunch_core(
            drig.sess, target_mode="full", interrupt_first=False))
        await asyncio.wait_for(in_launch.wait(), WAIT)

    async def _scenario():
        await alaunch(drig)
        drig.clock.during = _start_relaunch
        await agent_write(drig)
        task = drig.bot.auto_deny_retry_task(S)
        snap["task_before"] = task is not None
        drig.clock.let_time_run()
        await _finish(task)
        nxt = drig.bot.auto_deny_retry_task(S)
        if nxt is not None and nxt is not task:
            await _finish(nxt)
        snap["keys_before_launch_ends"] = list(drig.keys)
        snap["sent_before_launch_ends"] = len(drig.sent)
        snap["pending_in_flight"] = drig.bot.pending_auto_deny_retry(S)
        release.set()
        relaunch = holder.get("relaunch")
        outcome = await asyncio.wait_for(relaunch, WAIT) if relaunch else None
        snap["ok"] = getattr(outcome, "ok", None)
        for _ in range(5):
            await asyncio.sleep(0)
        snap["leftover_tasks"] = len(_other_live_tasks())
        snap["pending_end"] = drig.bot.pending_auto_deny_retry(S)
        snap["status_end"] = drig.sess.status
        return snap
    return _go(drig, _scenario)


def test_relaunch_awake_precondition_held_mid_relaunch(drig, monkeypatch):
    """The overlap is real: during the held launch the session is still
    the registry's, still waiting at the prompt and marked relaunching."""
    snap = relaunch_while_awake(drig, monkeypatch)
    assert (snap["task_before"], snap["still_registered"], snap["status_in_launch"],
            snap["in_flight_in_launch"]) == (True, True, Status.INTERACTIVE, True)


def test_relaunch_awake_precondition_the_relaunch_succeeds(drig, monkeypatch):
    assert relaunch_while_awake(drig, monkeypatch)["ok"] is True


def test_relaunch_awake_types_nothing_while_relaunching(drig, monkeypatch):
    snap = relaunch_while_awake(drig, monkeypatch)
    assert snap["keys_before_launch_ends"] == []


def test_relaunch_awake_types_nothing_at_all(drig, monkeypatch):
    relaunch_while_awake(drig, monkeypatch)
    assert drig.keys == []


def test_relaunch_awake_leaves_no_pending_retry(drig, monkeypatch):
    snap = relaunch_while_awake(drig, monkeypatch)
    assert (snap["pending_in_flight"], snap["pending_end"]) == (False, False)


def test_relaunch_awake_leaves_no_task_running(drig, monkeypatch):
    assert relaunch_while_awake(drig, monkeypatch)["leftover_tasks"] == 0


def test_relaunch_awake_sends_no_hook_decision(drig, monkeypatch):
    relaunch_while_awake(drig, monkeypatch)
    assert drig.decisions == []


def test_relaunch_awake_ends_idle(drig, monkeypatch):
    """Control: the relaunch completed as usual."""
    assert relaunch_while_awake(drig, monkeypatch)["status_end"] == Status.IDLE

