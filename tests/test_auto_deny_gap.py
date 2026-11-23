"""A rule's automatic Deny of a background agent's prompt in its gap.

The agent's dialog enters Claude Code's terminal only once its hook is
gone (and a fresh dialog ignores a key for a moment), so the Escape waits:
it is typed once, when the gap closes (Claude Code's record proved the
dialog up for a moment, or the grace after the hook's end ran out), on a
retry nothing else has to wake. A prompt closed, a /stop, a turn end, a
/kill or a kill and relaunch during the wait types nothing and leaves no
retry behind.

Every retry runs on one event loop: each test plays its datagrams and taps
inside one ``run_async`` coroutine and awaits the bot's retry task. The
wait goes through ``auth._sleep`` (replaced here; it moves the prompt's
stamps back by the delay, as time passing would).
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.two_prompts_rig as rigmod
from aipager import prompt_gap, prompt_stack
from aipager.bot import auth
from aipager.scope import Member, Scope
from aipager.state import Status
from tests.two_prompts_rig import ALICE, BG, BOB, CARD, GROUP, S, Rig

WRITE = {"file_path": "/src/a.py", "content": "x"}


def deny_write_scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", deny_tools=("Write",),
                  members=(Member(id=ALICE, label="alice", role="owner"),
                           Member(id=BOB, label="bob", role="user")))]


@pytest.fixture
def drig(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(rigmod, "scopes", deny_write_scopes)
    rig = Rig(mk_bot, monkeypatch, run_async)
    rig.sess.last_driver_user_id = BOB
    rig.record = None

    async def _record(sess):
        return rig.record(sess) if callable(rig.record) else rig.record

    monkeypatch.setattr(prompt_gap, "_record", _record)
    return rig


def datagram(rig, event, **fields) -> bytes:
    rig.recv._recent_fingerprints.clear()
    return json.dumps(dict({"session": S, "hook_event_name": event,
                            "transcript_path": "", "_seq": next(rig.n)},
                           **fields)).encode()


def agent_write(rig, *, request_id="req-bg-w"):
    fields = {"tool_name": "Write", "tool_input": WRITE, "agent_id": BG,
              "agent_type": "ship-reviewer"}
    if request_id:
        fields.update(aipager_reply_addr="/nonexistent/reply-bg.sock",
                      aipager_request_id=request_id)
    return datagram(rig, "PermissionRequest", **fields)


def tap_update(msg_id, verb, uid):
    q = MagicMock()
    q.data = f"{S}:{verb}"
    q.from_user = MagicMock(id=uid)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=GROUP)
    q.message.chat_id = GROUP
    q.message.message_id = msg_id
    q.message.text = "🔐 api · Permission needed"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    return u


def due_orders(sess):
    return [rec["perm"]["tool_info"]["order"] for rec in sess.extra_prompts
            if isinstance(rec, dict) and rec.get("auto_deny_due")]


def fake_sleep(monkeypatch, rig, hold: asyncio.Event | None = None):
    """``auth._sleep`` that records the delay and moves every open
    prompt's stamps back by it (the time passing). With *hold*, the time
    passes only once it is set."""
    delays: list[float] = []

    async def _sleep(delay):
        delays.append(delay)
        if hold is not None:
            await hold.wait()
        assert len(delays) < 20, "the retry did not settle"
        # Every order, a refusal's too when the session no longer waits
        # (open_prompts() lists none then).
        perms = list(rig.sess.open_prompts()) + [
            rec.get("perm") for rec in rig.sess.extra_prompts if isinstance(rec, dict)]
        orders = {id(o): o for o in (prompt_gap.order_of(p) for p in perms)
                  if o is not None}
        for order in orders.values():
            for key in ("arrived", "timed_out_at", "dialog_seen_at"):
                if order.get(key):
                    order[key] -= delay
        await asyncio.sleep(0)

    monkeypatch.setattr(auth, "_sleep", _sleep)
    return delays


async def drain(bot):
    """Await the session's retries until none is pending."""
    for _ in range(10):
        task = bot.auto_deny_retry_task(S)
        if task is None or task.done():
            return
        try:
            await task
        except asyncio.CancelledError:
            return


def blocked_notices(rig):
    return [m for m in rig.sent if "blocked" in (m.get("text") or "")]


def _extra_waiting(drig):
    """Claude's own question open, then the agent's refused Write whose
    hook decision fails (a due refusal, parked, behind the question)."""
    drig.launch(BG)
    drig.question()
    drig.hook_ok = False
    drig.hook("PermissionRequest", tool_name="Write", tool_input=WRITE, agent_id=BG,
              agent_type="ship-reviewer", aipager_reply_addr="/nonexistent/reply-bg.sock",
              aipager_request_id="req-bg-w")
    assert drig.keys == []
    assert len(due_orders(drig.sess)) == 1


def test_due_refusal_waits_through_gap_then_types_escape(drig, monkeypatch, run_async):
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    seen = {}

    async def go():
        delays = fake_sleep(monkeypatch, drig)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        drig.keys.clear()
        n_notices = len(blocked_notices(drig))
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        seen["keys_at_timeout"] = list(drig.keys)
        seen["pending"] = bot.pending_auto_deny_retry(S)
        await asyncio.sleep(0)
        await drain(bot)
        seen["delays"] = delays
        seen["notices"] = len(blocked_notices(drig)) - n_notices

    run_async(go())
    assert seen["keys_at_timeout"] == []
    assert seen["pending"] is True
    assert seen["delays"][0] == pytest.approx(prompt_stack.DIALOG_SETTLE, abs=0.2)
    assert drig.keys == ["Escape"]
    assert seen["notices"] == 1
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY
    assert not bot.pending_auto_deny_retry(S)


def test_probe_finds_record_and_reschedules_to_seen_plus_settle(
        drig, monkeypatch, run_async):
    _extra_waiting(drig)
    bot = drig.bot

    def _record(sess):
        [order] = due_orders(sess)
        gone = order["timed_out_at"]
        wall_gone = time.time() - (time.monotonic() - gone)
        return {"status": "waiting", "waitingFor": "permission prompt",
                "statusAt": wall_gone + 0.3}

    drig.record = _record
    seen = {}

    async def go():
        delays = fake_sleep(monkeypatch, drig)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        drig.keys.clear()
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        await asyncio.sleep(0)
        await drain(bot)
        seen["delays"] = list(delays)

    run_async(go())
    # The probe at timeout + settle finds the record (waiting from 0.3 s
    # after the timeout): the gap closes 0.3 s later, not at the grace.
    assert seen["delays"][0] == pytest.approx(2.0, abs=0.2)
    assert seen["delays"][1] == pytest.approx(0.3, abs=0.2)
    assert drig.keys == ["Escape"]


async def _close_the_agents_prompt(drig, how):
    """The production paths that close the waiting refusal's prompt."""
    bot, sess = drig.bot, drig.sess
    if how == "next-call":
        # The agent carried on (its dialog was answered in the terminal).
        await drig.recv._on_datagram(datagram(
            drig, "PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
            tool_use_id="toolu_next", agent_id=BG, agent_type="ship-reviewer"))
    elif how == "subagent-stop":
        await drig.recv._on_datagram(datagram(
            drig, "SubagentStop", agent_id=BG, agent_type="ship-reviewer"))
    elif how == "stop":
        await bot._stop_session_core(sess)
    else:
        # The session monitor found the session gone (a kill, a crash).
        bot.registry.transition(sess.name, Status.GONE)


CLOSES = ["next-call", "subagent-stop", "stop", "gone"]


@pytest.mark.parametrize("how", CLOSES)
def test_close_during_wait_types_nothing_and_leaves_no_task(
        drig, monkeypatch, run_async, how):
    """Every path that closes the last waiting refusal cancels its retry
    at once (``TrackedSession.drop_auto_deny_retry``), with no further
    call from the closer."""
    _extra_waiting(drig)
    bot = drig.bot
    seen = {}

    async def go():
        hold = asyncio.Event()
        delays = fake_sleep(monkeypatch, drig, hold)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        drig.keys.clear()
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        task = bot.auto_deny_retry_task(S)
        seen["before"] = bot.pending_auto_deny_retry(S)
        await _close_the_agents_prompt(drig, how)
        seen["after"] = bot.pending_auto_deny_retry(S)
        n, k = len(delays), len(drig.keys)
        hold.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        seen["cancelled"] = task is not None and task.cancelled()
        await drain(bot)
        seen["slept"] = len(delays) - n
        # The closer's own keys (/stop's Escapes) aside: nothing later.
        seen["keys"] = drig.keys[k:]

    run_async(go())
    assert seen["before"] is True
    assert seen["after"] is False
    assert seen["cancelled"] is True
    assert seen["slept"] == 0
    assert seen["keys"] == []
    assert due_orders(drig.sess) == []


def test_closing_the_refusal_while_another_prompt_stays_open_cancels_its_retry(
        drig, monkeypatch, run_async):
    """Claude's own question stays open (the session still waits, so no
    status change drops anything): the agent's next call closing its
    refused prompt alone cancels the retry."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    seen = {}

    async def go():
        hold = asyncio.Event()
        fake_sleep(monkeypatch, drig, hold)
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        seen["before"] = bot.pending_auto_deny_retry(S)
        await _close_the_agents_prompt(drig, "next-call")
        seen["after"] = bot.pending_auto_deny_retry(S)
        seen["status"] = sess.status
        seen["open"] = len(sess.open_prompts())
        hold.set()
        await drain(bot)

    run_async(go())
    assert seen == {"before": True, "after": False, "status": Status.INTERACTIVE,
                    "open": 1}
    assert "Escape" not in drig.keys


def test_a_retry_survives_a_close_that_leaves_its_refusal_waiting(
        drig, monkeypatch, run_async):
    """Control for the cancel above: another prompt closing while the
    refusal still waits keeps the retry."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    seen = {}

    async def go():
        hold = asyncio.Event()
        fake_sleep(monkeypatch, drig, hold)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        sess.close_prompts(lambda _perm: False)
        seen["after"] = bot.pending_auto_deny_retry(S)
        hold.set()
        await drain(bot)

    run_async(go())
    assert seen["after"] is True
    assert drig.keys.count("Escape") == 1


def test_a_close_elsewhere_while_the_woken_retry_types_does_not_cut_it_short(
        drig, monkeypatch, run_async):
    """Once its sleep is over the retry is no longer the pending one: a
    close in another task while it types (its refusal no longer due, so
    none is left waiting) must not cancel it before its notice and the
    BUSY transition."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    real_type = bot._type_auto_deny
    seen = {}

    async def _typing(s, tool_name):
        async def _elsewhere():
            sess.close_prompts(lambda _perm: False)
        await asyncio.get_running_loop().create_task(_elsewhere())
        return await real_type(s, tool_name)

    monkeypatch.setattr(bot, "_type_auto_deny", _typing)

    async def go():
        fake_sleep(monkeypatch, drig)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        drig.keys.clear()
        n_notices = len(blocked_notices(drig))
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        task = bot.auto_deny_retry_task(S)
        await task
        seen["cancelled"] = task.cancelled()
        await drain(bot)
        seen["notices"] = len(blocked_notices(drig)) - n_notices

    run_async(go())
    assert seen == {"cancelled": False, "notices": 1}
    assert drig.keys == ["Escape"]
    assert sess.status == Status.BUSY


def _left_the_wait(drig, monkeypatch, run_async, *, gap_over):
    """The retry is scheduled, then the session leaves the wait before it
    fires (no closer ran yet: the record is still there). *gap_over*: by
    then the dialog's gap is over too. Returns what was seen."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    seen = {}

    async def go():
        delays = fake_sleep(monkeypatch, drig)
        await bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
        drig.keys.clear()
        await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                              aipager_request_id="req-bg-w"))
        seen["before"] = bot.pending_auto_deny_retry(S)
        n = len(delays)
        sess.status = Status.IDLE
        if gap_over:
            for order in due_orders(sess):
                order["timed_out_at"] -= prompt_stack.OPEN_GRACE
        await drain(bot)
        seen["after"] = bot.pending_auto_deny_retry(S)
        seen["retries"] = len(delays) - n

    run_async(go())
    return seen


def test_stop_during_wait_types_nothing(drig, monkeypatch, run_async):
    """The retry fires with the gap over: only its check that the session
    still waits on the prompt keeps the Escape out."""
    seen = _left_the_wait(drig, monkeypatch, run_async, gap_over=True)
    assert seen["before"] is True
    assert drig.keys == []
    assert seen["after"] is False
    assert len(due_orders(drig.sess)) == 1


def test_stop_during_wait_schedules_no_further_retry(drig, monkeypatch, run_async):
    """The retry fires still in the gap: with the session no longer
    waiting, no later retry is scheduled."""
    seen = _left_the_wait(drig, monkeypatch, run_async, gap_over=False)
    assert seen["before"] is True
    assert seen["retries"] == 1
    assert seen["after"] is False
    assert drig.keys == []


def test_lone_auto_deny_in_gap_becomes_due_and_types_after_grace(
        drig, monkeypatch, run_async):
    """An agent's refused tool with no reply channel: its dialog appears
    only once the hook is gone, so the Escape waits the grace instead of
    going into the input line at once."""
    drig.launch(BG)
    bot, sess = drig.bot, drig.sess
    seen = {}

    async def go():
        delays = fake_sleep(monkeypatch, drig)
        await drig.recv._on_datagram(agent_write(drig, request_id=""))
        seen["keys_at_arrival"] = list(drig.keys)
        seen["due"] = len(due_orders(sess))
        seen["pending"] = bot.pending_auto_deny_retry(S)
        seen["status"] = sess.status
        await asyncio.sleep(0)
        await drain(bot)
        seen["delays"] = list(delays)

    run_async(go())
    assert seen["keys_at_arrival"] == []
    assert seen["due"] == 1
    assert seen["pending"] is True
    assert seen["status"] == Status.INTERACTIVE
    assert seen["delays"][0] == pytest.approx(prompt_stack.OPEN_GRACE, abs=0.2)
    assert drig.keys == ["Escape"]
    assert len(blocked_notices(drig)) == 1
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


def test_control_claudes_own_lone_auto_deny_types_at_once(drig):
    """Claude's own refused tool with no hook: its dialog is up from
    arrival, so the Escape goes in at once, as before."""
    drig.hook("PermissionRequest", tool_name="Write", tool_input=WRITE,
              tool_use_id="toolu_w1")
    assert drig.keys == ["Escape"]
    assert not drig.bot.pending_auto_deny_retry(S)


# -- /kill and kill+relaunch: no SessionEnd needed ---------------------------------------

def _kill_ok(monkeypatch, hold: asyncio.Event | None = None):
    """``inject.kill_session`` that succeeds (after *hold*, when given);
    calls go to the returned list. Claude Code's SessionEnd never comes."""
    from aipager.dtach import inject
    calls: list[str] = []

    async def _kill(name):
        calls.append(name)
        if hold is not None:
            await hold.wait()
        return True

    monkeypatch.setattr(inject, "kill_session", _kill)
    return calls


def _relaunch_ok(monkeypatch):
    """The relaunch's outside effects: the socket is gone at once, the
    launch works, no real wait."""
    from aipager.dtach import inject
    launches: list[str] = []

    async def _launch(short_name, **kw):
        launches.append(short_name)
        return True, ""

    async def _no_sleep(_secs):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=False))))
    return launches


async def _wait_out(task):
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _due_after_timeout(drig, monkeypatch, hold=None):
    """The question answered, then the agent's hook timed out: its refusal
    waits on a scheduled retry. Returns (delays, notices so far)."""
    delays = fake_sleep(monkeypatch, drig, hold)
    await drig.bot._handle_callback(tap_update(CARD, "opt0", ALICE), MagicMock())
    drig.keys.clear()
    n_notices = len(blocked_notices(drig))
    await drig.recv._on_datagram(datagram(drig, "permission_reply_timeout",
                                          aipager_request_id="req-bg-w"))
    return delays, n_notices


def test_kill_during_wait_cancels_the_retry_without_a_session_end(
        drig, monkeypatch, run_async):
    """/kill removes the session before (or without) Claude Code's
    SessionEnd: the removal itself cancels the waiting refusal's retry
    (``SessionRegistry.remove``), so nothing wakes later to type an Escape
    into whatever answers to that name, post a notice or move the
    re-created entry to BUSY."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    _kill_ok(monkeypatch)
    seen = {}

    async def go():
        hold = asyncio.Event()
        delays, n_notices = await _due_after_timeout(drig, monkeypatch, hold)
        task = bot.auto_deny_retry_task(S)
        seen["before"] = bot.pending_auto_deny_retry(S)
        outcome = await bot._kill_session_core(S, "api")
        seen["result"] = outcome.result
        seen["slot"] = sess.auto_deny_retry_task
        n = len(delays)
        # The monitor's next scan (or a late SessionEnd) re-creates it.
        again = bot.registry.get_or_create(S)
        bot.registry.transition(S, Status.GONE)
        hold.set()
        await _wait_out(task)
        seen["cancelled"] = task is not None and task.cancelled()
        seen["slept"] = len(delays) - n
        seen["notices"] = len(blocked_notices(drig)) - n_notices
        seen["again"] = again.status

    run_async(go())
    assert seen["before"] is True
    assert seen["result"] == "killed"
    assert seen["slot"] is None
    assert seen["cancelled"] is True
    assert seen["slept"] == 0
    assert drig.keys == []
    assert seen["notices"] == 0
    assert seen["again"] == Status.GONE


def test_a_kill_while_the_woken_retry_reads_the_queue_types_nothing(
        drig, monkeypatch, run_async):
    """A retry that has woken is out of the removal's reach: it must see
    for itself, right before the key, that its session is no longer the
    registry's (the name may answer to a new process)."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    _kill_ok(monkeypatch)
    seen = {}

    async def _queue_read(s):
        if s is sess:
            await bot._kill_session_core(S, "api")
            again = bot.registry.get_or_create(S)
            bot.registry.transition(S, Status.GONE)
            seen["again"] = again
        return []

    monkeypatch.setattr(bot, "_claude_queue_before_typed_refusal", _queue_read)

    async def go():
        _delays, n_notices = await _due_after_timeout(drig, monkeypatch)
        task = bot.auto_deny_retry_task(S)
        await _wait_out(task)
        seen["cancelled"] = task.cancelled()
        seen["notices"] = len(blocked_notices(drig)) - n_notices
        await _wait_out(sess.auto_deny_retry_task)

    run_async(go())
    assert seen["cancelled"] is False
    assert drig.keys == []
    assert seen["notices"] == 0
    assert seen["again"].status == Status.GONE
    assert sess.auto_deny_retry_task is None


def test_a_kill_while_the_retry_looks_for_the_dialog_leaves_no_retry_behind(
        drig, monkeypatch, run_async):
    """The retry woke in the gap (the probe of Claude Code's record) and
    the session was killed during that read: no new retry is scheduled for
    the session the registry no longer has."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    _kill_ok(monkeypatch)
    seen = {"reads": 0}

    async def _record(s):
        seen["reads"] += 1
        if s is sess and seen["reads"] == 1:
            await bot._kill_session_core(S, "api")
        return None

    monkeypatch.setattr(prompt_gap, "_record", _record)

    async def go():
        delays, n_notices = await _due_after_timeout(drig, monkeypatch)
        task = bot.auto_deny_retry_task(S)
        await _wait_out(task)
        seen["orphan_retry"] = sess.auto_deny_retry_task
        await _wait_out(sess.auto_deny_retry_task)
        seen["delays"] = list(delays)
        seen["notices"] = len(blocked_notices(drig)) - n_notices

    run_async(go())
    assert seen["reads"] == 1
    assert seen["delays"][0] == pytest.approx(prompt_stack.DIALOG_SETTLE, abs=0.2)
    assert seen["orphan_retry"] is None
    assert len(seen["delays"]) == 1
    assert drig.keys == []
    assert seen["notices"] == 0
    assert bot.registry.get(S) is None


def test_kill_and_relaunch_cancels_the_retry_before_the_kill(
        drig, monkeypatch, run_async):
    """The session stays INTERACTIVE through the kill and the launch: the
    waiting refusal's retry is gone before the old process is killed, and
    the new claude gets no Escape."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    from aipager.dtach import inject
    seen = {}

    async def go():
        hold = asyncio.Event()
        delays, n_notices = await _due_after_timeout(drig, monkeypatch, hold)
        task = bot.auto_deny_retry_task(S)
        seen["before"] = bot.pending_auto_deny_retry(S)

        async def _kill(name):
            seen["at_kill"] = (bot.pending_auto_deny_retry(S), sess.status)
            await asyncio.sleep(0)
            seen["cancelled_by_kill"] = task.cancelled()
            hold.set()
            return True

        monkeypatch.setattr(inject, "kill_session", _kill)
        launches = _relaunch_ok(monkeypatch)
        n = len(delays)
        outcome = await bot._kill_and_relaunch_core(
            sess, target_mode="default", interrupt_first=False)
        seen["ok"] = outcome.ok
        seen["launches"] = launches
        await _wait_out(task)
        await drain(bot)
        seen["slept"] = len(delays) - n
        seen["notices"] = len(blocked_notices(drig)) - n_notices

    run_async(go())
    assert seen["before"] is True
    assert seen["at_kill"] == (False, Status.INTERACTIVE)
    assert seen["cancelled_by_kill"] is True
    assert seen["ok"] is True
    assert seen["launches"] == [S.removeprefix("claude-")]
    assert seen["slept"] == 0
    assert drig.keys == []
    assert seen["notices"] == 0
    assert sess.status == Status.IDLE


def test_a_relaunch_while_the_woken_retry_reads_the_queue_types_nothing(
        drig, monkeypatch, run_async):
    """A retry that has woken is out of the relaunch's reach too: a kill
    and relaunch under way (the session still INTERACTIVE) keeps its Escape
    out of the dying claude and the new one."""
    _extra_waiting(drig)
    bot, sess = drig.bot, drig.sess
    kill_hold = asyncio.Event()
    kills = _kill_ok(monkeypatch, kill_hold)
    launches = _relaunch_ok(monkeypatch)
    seen = {}

    async def _queue_read(s):
        if s is sess and "relaunch" not in seen:
            seen["relaunch"] = asyncio.get_running_loop().create_task(
                bot._kill_and_relaunch_core(sess, target_mode="default",
                                            interrupt_first=False))
            while not kills:
                await asyncio.sleep(0)
            seen["in_flight"] = (sess.relaunch_in_flight, sess.status)
        return []

    monkeypatch.setattr(bot, "_claude_queue_before_typed_refusal", _queue_read)

    async def go():
        _delays, n_notices = await _due_after_timeout(drig, monkeypatch)
        await drain(bot)
        seen["keys_during"] = list(drig.keys)
        kill_hold.set()
        outcome = await seen["relaunch"]
        seen["ok"] = outcome.ok
        await drain(bot)
        seen["notices"] = len(blocked_notices(drig)) - n_notices

    run_async(go())
    assert seen["in_flight"] == (True, Status.INTERACTIVE)
    assert seen["keys_during"] == []
    assert seen["ok"] is True
    assert launches == [S.removeprefix("claude-")]
    assert drig.keys == []
    assert seen["notices"] == 0
    assert sess.status == Status.IDLE


# -- TrackedSession.drop_auto_deny_retry: the one place every close ends ----------------

def _sess_with_retry(status, *, due=True):
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-solo", label="solo", status=status)
    sess.extra_prompts.append({"text": "", "keyboard": None, "summary": "x",
                               "prompt_token": None,
                               "perm": {"tool_info": {"name": "Write"}},
                               "auto_deny_due": due})
    return sess


@pytest.mark.parametrize("status", [Status.INTERACTIVE, Status.IDLE],
                         ids=["waiting", "no-longer-waiting"])
def test_close_all_prompts_cancels_the_retry(run_async, status):
    """/stop, a halt and a turn ended at its dialog close every prompt,
    whatever the status already says (a record left outside the open list
    is dropped all the same, and its retry with it)."""
    seen = {}

    async def go():
        sess = _sess_with_retry(status)
        task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        sess.auto_deny_retry_task = task
        sess.close_all_prompts()
        await asyncio.sleep(0)
        seen["slot"] = sess.auto_deny_retry_task
        seen["cancelled"] = task.cancelled()

    run_async(go())
    assert seen == {"slot": None, "cancelled": True}


def test_close_prompts_cancels_the_retry_once_its_refusal_is_gone(run_async):
    """The state's own contract, whoever closes: another prompt still open
    (no status change), only the refusal's record closed."""
    seen = {}

    async def go():
        sess = _sess_with_retry(Status.INTERACTIVE)
        due_perm = sess.extra_prompts[0]["perm"]
        sess.extra_prompts.append({"text": "q", "keyboard": None, "summary": "q",
                                   "prompt_token": None, "msg_id": 7,
                                   "perm": {"tool_info": {"name": "AskUserQuestion"}}})
        task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        sess.auto_deny_retry_task = task
        sess.close_prompts(lambda perm: perm is due_perm)
        await asyncio.sleep(0)
        seen["open"] = len(sess.open_prompts())
        seen["slot"] = sess.auto_deny_retry_task
        seen["cancelled"] = task.cancelled()

    run_async(go())
    assert seen == {"open": 1, "slot": None, "cancelled": True}


def test_a_close_that_leaves_a_refusal_waiting_keeps_the_retry(run_async):
    seen = {}

    async def go():
        sess = _sess_with_retry(Status.INTERACTIVE)
        task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        sess.auto_deny_retry_task = task
        sess.close_prompts(lambda _perm: False)
        sess.drop_auto_deny_retry()
        seen["kept"] = sess.auto_deny_retry_task is task and not task.done()
        task.cancel()

    run_async(go())
    assert seen["kept"] is True


def test_a_running_retry_is_never_cancelled_by_its_own_close(run_async):
    """The retry that types the last refusal closes it itself: the close
    must not cancel the task it runs in (its notice and BUSY come after)."""
    seen = {}

    async def go():
        sess = _sess_with_retry(Status.INTERACTIVE)

        async def _retry():
            sess.close_all_prompts()
            await asyncio.sleep(0)
            seen["ran_on"] = True

        task = asyncio.get_running_loop().create_task(_retry())
        sess.auto_deny_retry_task = task
        await task
        seen["cancelled"] = task.cancelled()

    run_async(go())
    assert seen == {"ran_on": True, "cancelled": False}
