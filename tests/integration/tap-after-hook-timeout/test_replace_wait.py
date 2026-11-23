"""A rule's waiting automatic Deny of a background agent's prompt, then
the claude under the same session entry is swapped: the /new conflict
Replace tap, or a kill and relaunch whose launch fails or whose old
claude outlives the kill. No SessionEnd ever arrives from Claude Code.

Contract (design.md "Success criteria" 6: "a prompt closed, /stop, kill
or turn end during the wait types nothing and leaves no pending retry";
entrypoints.md "Rule-based automatic Deny": "kill ... during the wait: no
key, no notice from the wait, no pending retry left"). The refusal
belongs to the old claude's dialog: once that claude is gone nothing may
be typed for it into the fresh one, however late an event wakes the
runner (the fresh claude's first prompt, a late timeout datagram from the
old hook). While the old claude still lives, its dialog still waits, so
the refusal still answers it once the gap has closed.

Surfaces: the real callback handler with ``<session>:new_replace`` (as
tests/test_bot_callbacks.py drives Replace), ``bot._kill_and_relaunch_core``
(as the sibling test_kill_relaunch_wait.py drives /perms and restart),
``bot._run_due_auto_denies``, ``bot.pending_auto_deny_retry``,
``bot.auto_deny_retry_task``; the retry's wait is the ``auth._sleep`` seam
(the sibling ``Clock``, which holds a retry until the test lets time run).
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest

from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import ALICE, GROUP, S

_pkg = __name__.rpartition(".")[0]
_adw = importlib.import_module(f"{_pkg}.test_auto_deny_wait" if _pkg else "test_auto_deny_wait")
drig = _adw.drig
agent_write, ahook, alaunch, atap = _adw.agent_write, _adw.ahook, _adw.alaunch, _adw.atap

WAIT = 5.0
LATE = 30.0  # well past every gap: a stale refusal would be typable by now


def _blocked(rig, since=0):
    """Blocked notices (message key ``auth.blocked*``, all start with the
    no-entry sign) posted from index *since* on."""
    return [m for m in rig.sent[since:] if "⛔" in str(m.get("text"))]


def _other_live_tasks():
    me = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


async def _finish(task):
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), WAIT)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError as exc:
        raise AssertionError("the retry never ended") from exc


async def _settle(rig):
    """Let time run and await every retry until none is left (bounded)."""
    rig.clock.let_time_run()
    for _ in range(20):
        task = rig.bot.auto_deny_retry_task(S)
        if task is None:
            break
        await _finish(task)
        if rig.bot.auto_deny_retry_task(S) is task:
            break
    for _ in range(5):
        await asyncio.sleep(0)


def _go(rig, scenario):
    out = {}

    async def _main():
        out["v"] = await scenario()
    rig.run_async(_main())
    return out["v"]


async def _waiting_refusal(rig, hook_gone):
    """A background agent's Write that the team rule denies, waiting in
    its gap with its retry asleep."""
    await alaunch(rig)
    if hook_gone:
        rig.hook_ok = False  # the old hook already left; its timeout comes late
        await agent_write(rig, hook=True)
    else:
        await agent_write(rig)


async def _fresh_claude_first_prompt(rig):
    """The fresh claude's own first permission prompt (Bash, not a denied
    tool), through its own parked hook."""
    rig.hook_ok = True
    await ahook(rig, "PermissionRequest", tool_name="Bash",
                tool_input={"command": "ls"}, tool_use_id="toolu_fresh1",
                aipager_reply_addr="/nonexistent/reply-fresh.sock",
                aipager_request_id="req-fresh")


HOW = [False, True]
HOW_IDS = ["no-reply-channel", "hook-gone-timeout-late"]


# -- /new conflict Replace during the wait ----------------------------------------------

def replace_during_wait(rig, monkeypatch, mk_update, *, hook_gone):
    snap: dict = {}
    holder: dict = {}

    async def _kill(name):
        task = holder.get("task")
        for _ in range(3):
            await asyncio.sleep(0)
        snap["kill_name"] = name
        snap["pending_at_kill"] = rig.bot.pending_auto_deny_retry(S)
        snap["task_done_at_kill"] = task is None or task.done()
        snap["status_at_kill"] = rig.sess.status
        snap["registered_at_kill"] = rig.bot.registry.get(S) is rig.sess
        # Time passes while the old claude dies: a retry that survived the
        # tap wakes now, its gap closed, and would type into the session.
        rig.clock.let_time_run()
        await _finish(task)
        snap["keys_during_kill"] = list(rig.keys)
        return True

    async def _launch(short_name, **kw):
        snap["launched"] = short_name
        return True, ""

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)

    async def _scenario():
        await _waiting_refusal(rig, hook_gone)
        snap["pending_before"] = rig.bot.pending_auto_deny_retry(S)
        snap["status_before"] = rig.sess.status
        holder["task"] = task = rig.bot.auto_deny_retry_task(S)
        # The conflict card comes from a real /new for the same name, sent
        # while the refusal waits (so the card saw the session as it is).
        update = mk_update("/new api", user_id=ALICE, chat_id=GROUP)
        await rig.bot._handle_new_cmd(update, MagicMock())
        snap["conflict_card"] = S in rig.bot._new_conflict_pending
        sent0 = len(rig.sent)
        q = await atap(rig, 5, "new_replace", uid=ALICE)
        snap["replace_toasts"] = [c.args for c in q.answer.await_args_list]
        snap["sent0"] = sent0
        snap["task_cancelled"] = task is not None and task.cancelled()
        snap["keys_after_replace"] = list(rig.keys)
        snap["pending_after_replace"] = rig.bot.pending_auto_deny_retry(S)
        snap["entry_kept"] = rig.bot.registry.get(S) is rig.sess
        await _settle(rig)
        snap["status_after_replace"] = rig.sess.status
        snap["blocked_after_replace"] = _blocked(rig, sent0)
        # Long after: the old hook's late timeout datagram, then an
        # explicit runner pass (what any prompt event triggers).
        rig.elapse(LATE)
        await ahook(rig, "permission_reply_timeout", aipager_request_id="req-w")
        await rig.bot._run_due_auto_denies(rig.sess)
        await _settle(rig)
        snap["keys_after_late"] = list(rig.keys)
        snap["status_after_late"] = rig.sess.status
        snap["pending_after_late"] = rig.bot.pending_auto_deny_retry(S)
        snap["blocked_after_late"] = _blocked(rig, sent0)
        # The fresh claude's first prompt (the extra-prompt path).
        await _fresh_claude_first_prompt(rig)
        await _settle(rig)
        rig.elapse(LATE)
        await rig.bot._run_due_auto_denies(rig.sess)
        await _settle(rig)
        snap["keys_end"] = list(rig.keys)
        snap["blocked_end"] = _blocked(rig, sent0)
        snap["pending_end"] = rig.bot.pending_auto_deny_retry(S)
        snap["decisions_end"] = list(rig.decisions)
        snap["leftover_tasks"] = len(_other_live_tasks())
        return snap
    return _go(rig, _scenario)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_precondition_a_retry_waits_at_the_prompt(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert (snap["pending_before"], snap["status_before"], snap["conflict_card"]) == (
        True, Status.INTERACTIVE, True)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_precondition_kills_and_relaunches_the_same_entry(
        drig, monkeypatch, mk_update, hook_gone):
    """The swap is real: the old claude is killed and a fresh one launched
    under the same name, the entry kept and still waiting at its prompt
    when the kill runs (so status alone cannot be what protects it)."""
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert (snap.get("kill_name"), snap.get("launched") is not None,
            snap["registered_at_kill"], snap["status_at_kill"]) == (
        S, True, True, Status.INTERACTIVE)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_cancels_the_retry_before_the_kill(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert (snap["pending_at_kill"], snap["task_done_at_kill"]) == (False, True)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_retry_task_was_cancelled(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["task_cancelled"] is True


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_types_nothing_while_the_old_claude_dies(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["keys_during_kill"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_types_nothing_by_its_end(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["keys_after_replace"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_leaves_no_pending_retry(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["pending_after_replace"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_keeps_the_same_registry_entry(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["entry_kept"] is True


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_does_not_set_busy(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["status_after_replace"] != Status.BUSY


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_posts_no_blocked_notice(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["blocked_after_replace"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_late_old_timeout_types_nothing(drig, monkeypatch, mk_update, hook_gone):
    """The old hook's timeout datagram arrives after the swap, past every
    gap, followed by a runner pass: no Escape goes into the fresh claude."""
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["keys_after_late"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_late_old_timeout_posts_no_blocked_notice(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["blocked_after_late"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_late_old_timeout_does_not_set_busy(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["status_after_late"] != Status.BUSY


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_late_old_timeout_schedules_no_retry(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["pending_after_late"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_fresh_claudes_first_prompt_types_no_escape(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["keys_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_fresh_claudes_first_prompt_posts_no_blocked_notice(
        drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["blocked_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_fresh_claudes_first_prompt_leaves_no_retry(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["pending_end"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_answers_nothing_through_any_hook(drig, monkeypatch, mk_update, hook_gone):
    """No deny decision reaches the fresh claude's own parked hook (its
    Bash prompt was never the refused one)."""
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["decisions_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_replace_leaves_no_task_running(drig, monkeypatch, mk_update, hook_gone):
    snap = replace_during_wait(drig, monkeypatch, mk_update, hook_gone=hook_gone)
    assert snap["leftover_tasks"] == 0


# -- kill and relaunch: shared mocks ------------------------------------------------------

def _relaunch_mocks(monkeypatch, *, launch_ok=True, socket_survives=False, on_kill=None):
    calls: dict = {"kill": 0, "launch": 0}

    async def _kill(name):
        calls["kill"] += 1
        if on_kill is not None:
            await on_kill()
        return True

    async def _launch(short_name, **kw):
        calls["launch"] += 1
        return (True, "") if launch_ok else (False, "launch failed")

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=socket_survives))))
    return calls


# -- kill and relaunch whose launch fails -------------------------------------------------

def failed_relaunch(rig, monkeypatch, *, hook_gone):
    snap: dict = {}
    calls = _relaunch_mocks(monkeypatch, launch_ok=False)

    async def _scenario():
        await _waiting_refusal(rig, hook_gone)
        snap["pending_before"] = rig.bot.pending_auto_deny_retry(S)
        task = rig.bot.auto_deny_retry_task(S)
        outcome = await rig.bot._kill_and_relaunch_core(
            rig.sess, target_mode="full", interrupt_first=False)
        snap["ok"] = getattr(outcome, "ok", None)
        snap["calls"] = dict(calls)
        snap["registered"] = rig.bot.registry.get(S) is rig.sess
        snap["status_after"] = rig.sess.status
        sent0 = len(rig.sent)
        rig.clock.let_time_run()
        await _finish(task)
        await _settle(rig)
        snap["keys_after_settle"] = list(rig.keys)
        rig.elapse(LATE)
        await ahook(rig, "permission_reply_timeout", aipager_request_id="req-w")
        await rig.bot._run_due_auto_denies(rig.sess)
        await _settle(rig)
        snap["keys_end"] = list(rig.keys)
        snap["blocked_end"] = _blocked(rig, sent0)
        snap["status_end"] = rig.sess.status
        snap["pending_end"] = rig.bot.pending_auto_deny_retry(S)
        snap["decisions_end"] = list(rig.decisions)
        snap["leftover_tasks"] = len(_other_live_tasks())
        return snap
    return _go(rig, _scenario)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_precondition_the_launch_failed_after_a_kill(
        drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert (snap["pending_before"], snap["ok"], snap["calls"]) == (
        True, False, {"kill": 1, "launch": 1})


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_precondition_the_entry_stays(drig, monkeypatch, hook_gone):
    """The entry outlives the failed launch (so removal is not what
    protects it)."""
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["registered"] is True


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_types_nothing_once_time_runs(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["keys_after_settle"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_late_event_types_nothing(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["keys_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_posts_no_blocked_notice(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["blocked_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_does_not_set_busy(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["status_end"] != Status.BUSY


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_leaves_no_pending_retry(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["pending_end"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_sends_no_hook_decision(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["decisions_end"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_failed_relaunch_leaves_no_task_running(drig, monkeypatch, hook_gone):
    snap = failed_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["leftover_tasks"] == 0


# -- kill and relaunch whose old claude outlives the kill ---------------------------------

def survived_relaunch(rig, monkeypatch, *, hook_gone, run_after=True, poke_in_gap=True):
    """The old claude's socket never goes away: the relaunch stops short
    (no launch) and the old claude, with its dialog, still lives."""
    snap: dict = {}
    calls = _relaunch_mocks(monkeypatch, socket_survives=True)

    async def _scenario():
        await _waiting_refusal(rig, hook_gone)
        snap["pending_before"] = rig.bot.pending_auto_deny_retry(S)
        task = rig.bot.auto_deny_retry_task(S)
        outcome = await rig.bot._kill_and_relaunch_core(
            rig.sess, target_mode="full", interrupt_first=False)
        snap["ok"] = getattr(outcome, "ok", None)
        snap["calls"] = dict(calls)
        snap["registered"] = rig.bot.registry.get(S) is rig.sess
        snap["status_after"] = rig.sess.status
        snap["keys_after_relaunch"] = list(rig.keys)
        sent0 = len(rig.sent)
        snap["sent0"] = sent0
        if hook_gone:
            # The old hook's timeout arrives: the gap now runs from it.
            await ahook(rig, "permission_reply_timeout", aipager_request_id="req-w")
        if poke_in_gap:
            # Inside the gap a runner pass still types nothing.
            await rig.bot._run_due_auto_denies(rig.sess)
        snap["keys_in_gap"] = list(rig.keys)
        rig.clock.let_time_run()
        await _finish(task)
        if run_after:
            rig.elapse(LATE)
            await rig.bot._run_due_auto_denies(rig.sess)
        await _settle(rig)
        snap["keys_end"] = list(rig.keys)
        snap["gap_at_key"] = list(rig.gap_at_key)
        snap["blocked_end"] = _blocked(rig, sent0)
        snap["pending_end"] = rig.bot.pending_auto_deny_retry(S)
        snap["leftover_tasks"] = len(_other_live_tasks())
        return snap
    return _go(rig, _scenario)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_precondition_no_launch_and_entry_kept(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert (snap["pending_before"], snap["calls"]["launch"], snap["registered"],
            snap["status_after"]) == (True, 0, True, Status.INTERACTIVE)


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_types_nothing_during_the_relaunch(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["keys_after_relaunch"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_types_nothing_inside_the_gap(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["keys_in_gap"] == []


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_keeps_the_refusal_and_types_one_escape_after_the_gap(
        drig, monkeypatch, hook_gone):
    """The old claude's dialog still waits: its refusal answers it."""
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["keys_end"] == ["Escape"]


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_escape_goes_in_only_once_the_gap_closed(
        drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["gap_at_key"] == [None]


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_posts_the_blocked_notice_once(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert len(snap["blocked_end"]) == 1


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_leaves_no_pending_retry(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["pending_end"] is False


@pytest.mark.parametrize("hook_gone", HOW, ids=HOW_IDS)
def test_survived_kill_leaves_no_task_running(drig, monkeypatch, hook_gone):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=hook_gone)
    assert snap["leftover_tasks"] == 0


@pytest.mark.xfail(strict=True, reason=(
    "documented edge (fixes-4 'Issues NOT addressed'): a relaunch whose old "
    "claude outlives the kill drops the retry, so the kept refusal waits "
    "for the next prompt event instead of firing on its own as design.md "
    "success criterion 6 promises ('with no other event needed')"))
def test_survived_kill_types_the_escape_with_no_other_event(drig, monkeypatch):
    snap = survived_relaunch(drig, monkeypatch, hook_gone=False, run_after=False,
                             poke_in_gap=False)
    assert snap["keys_end"] == ["Escape"]

