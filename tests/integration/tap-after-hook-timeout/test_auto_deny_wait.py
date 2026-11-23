"""A rule's automatic Deny of a background agent's prompt in its gap
(design.md "Success criteria"; entrypoints.md "Rule-based automatic
Deny"): through the hook while it waits; otherwise the Escape is typed
exactly once, only after the gap closes, with no further event needed;
a prompt closed, /stop or kill during the wait types nothing and leaves
no pending retry.

Each scenario runs in one event loop (a retry is a task on the loop that
scheduled it). The retry's wait goes through the ``aipager.bot.auth._sleep``
seam: the fake records the delay and lets that much time pass for the
open prompts' orders, as ``Rig.elapse`` does.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.two_prompts_rig as rigmod
from aipager import prompt_gap, prompt_stack
from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import Status
from tests.two_prompts_rig import ALICE, BG, BOB, CARD, GROUP, S, Rig

WRITE = {"file_path": "/src/a.py", "content": "x"}
MAX_ROUNDS = 20


def deny_write_scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", deny_tools=("Write",),
                  members=(Member(id=ALICE, label="alice", role="owner"),
                           Member(id=BOB, label="bob", role="user")))]


class Clock:
    """The fake ``auth._sleep``: records each delay, holds the retry until
    the test lets time run (``drain``), then moves every open prompt's
    order back by the delay. *during* runs once inside the first sleep,
    before the time passes."""

    def __init__(self, rig):
        self.rig = rig
        self.delays: list[float] = []
        self.during = None
        self.running = None

    async def sleep(self, seconds):
        self.delays.append(seconds)
        if self.running is None:
            self.running = asyncio.Event()
        await self.running.wait()
        if self.during is not None:
            during, self.during = self.during, None
            result = during()
            if hasattr(result, "__await__"):
                await result
        if isinstance(seconds, (int, float)) and seconds > 0:
            self.rig.elapse(seconds)

    def let_time_run(self):
        if self.running is None:
            self.running = asyncio.Event()
        self.running.set()


@pytest.fixture
def drig(mk_bot, monkeypatch, run_async, no_record):
    monkeypatch.setattr(rigmod, "scopes", deny_write_scopes)
    rig = Rig(mk_bot, monkeypatch, run_async)
    rig.sess.last_driver_user_id = BOB
    rig.clock = Clock(rig)
    monkeypatch.setattr("aipager.bot.auth._sleep", rig.clock.sleep)
    rig.gap_at_key: list[object] = []
    real_keys = inject.send_keys

    async def _keys(name, key, *a, **k):
        # What the gap rule said about the agent's prompt when a key went in.
        for perm in rig.sess.open_prompts():
            info = perm.get("tool_info") or {}
            if info.get("agent_id") == BG:
                rig.gap_at_key.append(
                    prompt_stack.dialog_gap(info.get("order"), time.monotonic()))
        return await real_keys(name, key, *a, **k)

    monkeypatch.setattr(inject, "send_keys", _keys)
    return rig


async def ahook(rig, event, **fields):
    rig.recv._recent_fingerprints.clear()
    payload = dict({"session": S, "hook_event_name": event,
                    "transcript_path": "", "_seq": next(rig.n)}, **fields)
    await rig.recv._on_datagram(json.dumps(payload).encode())


async def alaunch(rig, agent=BG):
    call = {"subagent_type": "ship-reviewer", "description": "Review",
            "prompt": "Review it", "run_in_background": True}
    await ahook(rig, "PreToolUse", tool_name="Agent", tool_input=call,
                tool_use_id=f"toolu_launch_{agent}")
    await ahook(rig, "SubagentStart", agent_id=agent, agent_type="ship-reviewer")
    await ahook(rig, "PostToolUse", tool_name="Agent", tool_input=call,
                tool_use_id=f"toolu_launch_{agent}",
                tool_response={"isAsync": True, "status": "async_launched",
                               "agentId": agent})


async def agent_write(rig, *, hook=False, req="req-w", agent=BG, tool_use_id=None):
    fields = {"tool_name": "Write", "tool_input": WRITE, "agent_id": agent,
              "agent_type": "ship-reviewer"}
    if tool_use_id:
        fields["tool_use_id"] = tool_use_id
    if hook:
        fields.update(aipager_reply_addr="/nonexistent/reply-w.sock",
                      aipager_request_id=req)
    await ahook(rig, "PermissionRequest", **fields)


async def agent_next_call(rig, agent=BG):
    await ahook(rig, "PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
                tool_use_id="toolu_next", agent_id=agent, agent_type="ship-reviewer")


async def atap(rig, msg_id, verb, uid=ALICE):
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
    await rig.bot._handle_callback(u, MagicMock())
    return q


async def drain(rig):
    """Let time run and await scheduled retries until none is left
    (bounded)."""
    rig.clock.let_time_run()
    for _ in range(MAX_ROUNDS):
        task = rig.bot.auto_deny_retry_task(S)
        if task is None:
            return
        await task
        if rig.bot.auto_deny_retry_task(S) is task:
            return
    raise AssertionError("auto-deny retries never settled")


def run(drig, scenario):
    out = {}

    async def _go():
        out["value"] = await scenario()
    drig.run_async(_go())
    return out.get("value")


# -- the lone prompt with no reply channel (its gap runs from arrival) ----------------------

def lone_no_hook(drig, *, drain_it=True):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        snap = {"keys": list(drig.keys),
                "pending": drig.bot.pending_auto_deny_retry(S),
                "sent": len(drig.sent),
                "status": drig.sess.status}
        if drain_it:
            await drain(drig)
        return snap
    return run(drig, _scenario)


def test_lone_gapped_refusal_types_nothing_at_once(drig):
    assert lone_no_hook(drig, drain_it=False)["keys"] == []


def test_lone_gapped_refusal_schedules_a_retry(drig):
    assert lone_no_hook(drig, drain_it=False)["pending"] is True


def test_lone_gapped_refusal_keeps_the_session_waiting(drig):
    assert lone_no_hook(drig, drain_it=False)["status"] == Status.INTERACTIVE


def test_lone_gapped_refusal_types_one_escape_after_the_gap(drig):
    lone_no_hook(drig)
    assert drig.keys == ["Escape"]


def test_lone_gapped_refusal_types_only_once_the_gap_closed(drig):
    lone_no_hook(drig)
    assert drig.gap_at_key == [None]


def test_lone_gapped_refusal_leaves_no_retry(drig):
    lone_no_hook(drig)
    assert drig.bot.pending_auto_deny_retry(S) is False


def test_lone_gapped_refusal_closes_the_prompt(drig):
    lone_no_hook(drig)
    assert drig.sess.open_prompts() == []


def test_lone_gapped_refusal_waits_about_the_grace(drig):
    """No reply channel: the gap ends OPEN_GRACE after arrival; the waits
    add up to it (with no other event)."""
    lone_no_hook(drig)
    assert sum(drig.clock.delays) == pytest.approx(prompt_stack.OPEN_GRACE, abs=0.5)


def test_lone_gapped_refusal_posts_the_blocked_notice(drig):
    snap = lone_no_hook(drig)
    assert len(drig.sent) > snap["sent"]


# -- the hook's socket gone, then its timeout ----------------------------------------------

def hook_gone_then_timeout(drig, record=None):
    async def _scenario():
        await alaunch(drig)
        drig.hook_ok = False
        await agent_write(drig, hook=True)
        before = list(drig.keys)
        await ahook(drig, "permission_reply_timeout", aipager_request_id="req-w")
        after_timeout = list(drig.keys)
        await drain(drig)
        return before, after_timeout
    return run(drig, _scenario)


def test_hook_gone_refusal_types_nothing_before_the_timeout(drig):
    before, _after = hook_gone_then_timeout(drig)
    assert before == []


def test_hook_gone_refusal_types_nothing_at_the_timeout(drig):
    _before, after = hook_gone_then_timeout(drig)
    assert after == []


def test_hook_gone_refusal_types_one_escape_after_the_gap(drig):
    hook_gone_then_timeout(drig)
    assert drig.keys == ["Escape"]


def test_hook_gone_refusal_types_only_once_the_gap_closed(drig):
    hook_gone_then_timeout(drig)
    assert drig.gap_at_key == [None]


def test_hook_gone_refusal_sends_no_hook_decision(drig):
    hook_gone_then_timeout(drig)
    assert drig.decisions == []


def test_record_evidence_shortens_the_wait(drig, monkeypatch):
    """Claude Code's record says the permission dialog is waiting 0.3 s
    after the timeout: the Escape goes in about DIALOG_SETTLE after that,
    before the 4 s grace."""
    async def _record(sess):
        perms = [p for p in sess.open_prompts()
                 if (p.get("tool_info") or {}).get("agent_id") == BG]
        if not perms:
            return None
        order = perms[0]["tool_info"]["order"]
        gone = order.get("timed_out_at") or 0
        if not gone:
            return None
        status_at = time.time() - (time.monotonic() - gone) + 0.3
        return {"status": "waiting", "waitingFor": "permission prompt",
                "statusAt": status_at}

    monkeypatch.setattr(prompt_gap, "_record", _record)
    hook_gone_then_timeout(drig)
    assert (drig.keys, sum(drig.clock.delays) < prompt_stack.OPEN_GRACE) == (["Escape"], True)


def test_parked_hook_refusal_is_a_hook_decision(drig):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig, hook=True)
    run(drig, _scenario)
    assert ([d["behavior"] for _r, d in drig.decisions], drig.keys) == (["deny"], [])


def test_parked_hook_refusal_schedules_nothing(drig):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig, hook=True)
        return drig.bot.pending_auto_deny_retry(S)
    assert run(drig, _scenario) is False


# -- an extra prompt: on top only once Claude's question closed, but still opening ---------

def extra_after_question(drig):
    async def _scenario():
        await alaunch(drig)
        await ahook(drig, "PreToolUse", tool_name="AskUserQuestion",
                    tool_input=rigmod.QUESTION, tool_use_id="toolu_q1")
        await agent_write(drig)
        await atap(drig, CARD, "opt0")
        typed = len(drig.keys)
        mid = "Escape" in drig.keys
        pending = drig.bot.pending_auto_deny_retry(S)
        await drain(drig)
        return typed, mid, pending
    return run(drig, _scenario)


def test_extra_refusal_waits_after_the_question_closes(drig):
    typed, mid, pending = extra_after_question(drig)
    assert (mid, pending) == (False, True)


def test_extra_refusal_types_one_escape_after_the_gap(drig):
    typed, _mid, _pending = extra_after_question(drig)
    assert drig.keys[typed:] == ["Escape"]


# -- close, /stop, kill during the wait ---------------------------------------------------

async def close_agent_prompt(rig, how):
    if how == "next-call":
        await agent_next_call(rig)
    else:
        await ahook(rig, "SubagentStop", agent_id=BG, agent_type="ship-reviewer")


CLOSES = ["next-call", "subagent-stop"]


@pytest.mark.parametrize("how", CLOSES)
def test_prompt_closed_during_wait_closes_it(drig, how):
    """Precondition of the two below: the agent's prompt is gone."""
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        await close_agent_prompt(drig, how)
    run(drig, _scenario)
    assert drig.sess.open_prompts() == []


@pytest.mark.parametrize("how", CLOSES)
def test_prompt_closed_during_wait_leaves_no_pending_retry(drig, how):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        await close_agent_prompt(drig, how)
        return drig.bot.pending_auto_deny_retry(S)
    assert run(drig, _scenario) is False


@pytest.mark.parametrize("how", CLOSES)
def test_prompt_closed_during_wait_types_nothing(drig, how):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        await close_agent_prompt(drig, how)
        await drain(drig)
    run(drig, _scenario)
    assert drig.keys == []


@pytest.mark.parametrize("how", CLOSES)
def test_prompt_closed_during_wait_settles_with_no_retry(drig, how):
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        await close_agent_prompt(drig, how)
        await drain(drig)
        return drig.bot.pending_auto_deny_retry(S)
    assert run(drig, _scenario) is False


def test_prompt_closed_inside_the_sleep_types_nothing(drig):
    async def _scenario():
        await alaunch(drig)
        drig.clock.during = lambda: agent_next_call(drig)
        await agent_write(drig)
        await drain(drig)
    run(drig, _scenario)
    assert drig.keys == []


@pytest.mark.parametrize("status", [Status.IDLE, Status.GONE], ids=["stop", "kill"])
def test_stop_or_kill_during_wait_types_nothing(drig, status):
    async def _scenario():
        await alaunch(drig)

        def _stopped():
            drig.sess.status = status
        drig.clock.during = _stopped
        await agent_write(drig)
        await drain(drig)
    run(drig, _scenario)
    assert drig.keys == []


@pytest.mark.parametrize("status", [Status.IDLE, Status.GONE], ids=["stop", "kill"])
def test_stop_or_kill_during_wait_leaves_no_retry(drig, status):
    async def _scenario():
        await alaunch(drig)

        def _stopped():
            drig.sess.status = status
        drig.clock.during = _stopped
        await agent_write(drig)
        await drain(drig)
        return drig.bot.pending_auto_deny_retry(S)
    assert run(drig, _scenario) is False


@pytest.mark.parametrize("status", [Status.IDLE, Status.GONE], ids=["stop", "kill"])
def test_stop_or_kill_during_wait_posts_no_notice(drig, status):
    async def _scenario():
        await alaunch(drig)

        def _stopped():
            drig.sess.status = status
        drig.clock.during = _stopped
        await agent_write(drig)
        n = len(drig.sent)
        await drain(drig)
        return n
    n = run(drig, _scenario)
    assert drig.sent[n:] == []


def test_owner_turn_schedules_no_refusal(drig):
    """Control: the rule is the user's; on the owner's turn nothing waits."""
    drig.sess.last_driver_user_id = ALICE

    async def _scenario():
        await alaunch(drig)
        await agent_write(drig)
        return drig.bot.pending_auto_deny_retry(S)
    assert (run(drig, _scenario), drig.keys) == (False, [])


# -- iteration 2: every close path cancels the retry at once --------------------------------
#
# The retry is held (``Clock`` lets no time pass until ``drain``), so
# "pending right after the close" is read before the retry could run on
# its own. Close paths, each through a black-box surface:
# - next-call: the agent's next PreToolUse (its prompt was answered elsewhere);
# - tool-ran: the agent's PostToolUse for the refused call;
# - subagent-stop: the agent's SubagentStop;
# - stop-button: the owner taps Stop on the session's card;
# - gone: the session goes GONE, as the session monitor records it.

ALL_CLOSES = ["next-call", "tool-ran", "subagent-stop", "stop-button", "gone"]


async def close_by(rig, how):
    if how == "next-call":
        await agent_next_call(rig)
    elif how == "tool-ran":
        await ahook(rig, "PostToolUse", tool_name="Write", tool_input=WRITE,
                    tool_use_id="toolu_w", agent_id=BG, agent_type="ship-reviewer",
                    tool_response={"type": "create", "filePath": WRITE["file_path"]})
    elif how == "subagent-stop":
        await ahook(rig, "SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    elif how == "stop-button":
        await atap(rig, CARD, "stop", uid=ALICE)
    elif how == "gone":
        rig.bot.registry.transition(S, Status.GONE)
    else:
        raise AssertionError(how)


def waiting_then_closed(drig, how):
    """The lone gapped refusal waits; then *how* ends it; then time runs."""
    async def _scenario():
        await alaunch(drig)
        await agent_write(drig, tool_use_id="toolu_w")
        snap = {"pending_before": drig.bot.pending_auto_deny_retry(S)}
        await close_by(drig, how)
        snap["pending_after"] = drig.bot.pending_auto_deny_retry(S)
        snap["task_after"] = drig.bot.auto_deny_retry_task(S)
        snap["keys_at_close"] = list(drig.keys)
        snap["sent_at_close"] = len(drig.sent)
        snap["open_at_close"] = len(drig.sess.open_prompts())
        snap["status_at_close"] = drig.sess.status
        await drain(drig)
        snap["pending_end"] = drig.bot.pending_auto_deny_retry(S)
        return snap
    return run(drig, _scenario)


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_refusal_waits_before_each_close(drig, how):
    """Precondition: before the close a retry is pending."""
    assert waiting_then_closed(drig, how)["pending_before"] is True


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_ends_the_wait(drig, how):
    """Precondition: the close really ended the refusal's prompt or the
    session's wait (no prompt open, or the session left INTERACTIVE)."""
    snap = waiting_then_closed(drig, how)
    assert (snap["open_at_close"] == 0
            or snap["status_at_close"] != Status.INTERACTIVE) is True


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_leaves_no_pending_retry_at_once(drig, how):
    assert waiting_then_closed(drig, how)["pending_after"] is False


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_leaves_no_retry_task(drig, how):
    assert waiting_then_closed(drig, how)["task_after"] is None


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_then_time_types_no_further_key(drig, how):
    """The close's own keys (Stop's Escape) aside, nothing is typed later."""
    snap = waiting_then_closed(drig, how)
    assert drig.keys[len(snap["keys_at_close"]):] == []


@pytest.mark.parametrize("how", [h for h in ALL_CLOSES if h != "stop-button"])
def test_each_close_types_no_escape_for_the_refusal(drig, how):
    """No Escape went in before the close either (the refusal never typed;
    Stop is left out, it types its own Escape)."""
    assert waiting_then_closed(drig, how)["keys_at_close"] == []


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_then_time_posts_no_notice(drig, how):
    snap = waiting_then_closed(drig, how)
    assert drig.sent[snap["sent_at_close"]:] == []


@pytest.mark.parametrize("how", ALL_CLOSES)
def test_each_close_settles_with_no_retry(drig, how):
    assert waiting_then_closed(drig, how)["pending_end"] is False


# -- the refusal closes while another prompt stays open ------------------------------------

def refusal_closed_beside_question(drig):
    async def _scenario():
        await alaunch(drig)
        await ahook(drig, "PreToolUse", tool_name="AskUserQuestion",
                    tool_input=rigmod.QUESTION, tool_use_id="toolu_q1")
        await agent_write(drig)
        await atap(drig, CARD, "opt0")     # Claude's question answered: refusal now waits
        before = drig.bot.pending_auto_deny_retry(S)
        await ahook(drig, "PreToolUse", tool_name="AskUserQuestion",
                    tool_input=rigmod.QUESTION, tool_use_id="toolu_q2")
        await agent_next_call(drig)        # the refusal's prompt ends; the question stays
        after = drig.bot.pending_auto_deny_retry(S)
        keys = len(drig.keys)
        await drain(drig)
        return before, after, keys
    return run(drig, _scenario)


def test_refusal_closed_beside_an_open_question_was_waiting(drig):
    before, _after, _keys = refusal_closed_beside_question(drig)
    assert before is True


def test_refusal_closed_beside_an_open_question_leaves_no_retry(drig):
    _before, after, _keys = refusal_closed_beside_question(drig)
    assert after is False


def test_refusal_closed_beside_an_open_question_types_nothing_later(drig):
    _before, _after, keys = refusal_closed_beside_question(drig)
    assert drig.keys[keys:] == []


def test_question_close_beside_a_waiting_refusal_keeps_its_retry(drig):
    """Control: closing another prompt while the refusal still waits in its
    gap keeps the retry (it is not over-cancelled)."""
    _typed, _mid, pending = extra_after_question(drig)
    assert pending is True
