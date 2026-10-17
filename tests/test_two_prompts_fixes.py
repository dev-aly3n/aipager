"""Roadmap 8.120, review iteration 2: an agent's prompt with no tool_use_id
of its own closes at its tool's end; a foreground agent's prompt ends with
an interrupted turn; the Stop-carry window; the auto-deny's keys; a
deferred auto-deny behind a separate prompt; the wait resumed after a
Stop; Claude's own prompt refused while an agent's is open."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import state as state_mod
from aipager.bot import callbacks
from aipager.state import Status, TrackedSession
from tests.two_prompts_rig import BG, BOB, CARD, FG, GROUP, OTHER, S, Rig, answers


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def _bash(command):
    return {"command": command, "description": command}


def _agent_pre(rig, agent, command, tool_use_id):
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer")


def _agent_post(rig, agent, command, tool_use_id, event="PostToolUse"):
    extra = ({"tool_response": {"stdout": "ok"}} if event == "PostToolUse"
             else {"error": "The user doesn't want to proceed with this tool use."})
    rig.hook(event, tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer",
             **extra)


def _agent_request(rig, agent, command):
    """The agent's PermissionRequest, with no tool_use_id of its own."""
    rig.hook("PermissionRequest", tool_name="Bash", tool_input=_bash(command),
             agent_id=agent, agent_type="ship-reviewer",
             aipager_reply_addr=f"/nonexistent/reply-{agent}.sock",
             aipager_request_id=f"req-{agent}-{command}")


def _update(msg_id, verb, uid=BOB):
    """The rig's tap, as a coroutine's arguments (for a tap made while a
    hook is being handled)."""
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
    return u, q


# -- an agent's request with no id of its own (tester-iter1-001/-002) -------

def test_agent_request_borrows_its_one_unfinished_call(rig):
    rig.launch(BG)
    rig.question()
    _agent_pre(rig, BG, "make", "toolu_a")
    _agent_request(rig, BG, "make")
    [_, perm] = rig.sess.open_prompts()
    assert perm["tool_info"]["tool_use_id"] == "toolu_a"


def test_parallel_identical_calls_borrow_no_id(rig):
    """Two calls of one tool with one input in flight: which one asks is not
    known, so neither's end may close the prompt."""
    rig.launch(BG)
    rig.question()
    _agent_pre(rig, BG, "make", "toolu_a")
    _agent_pre(rig, BG, "make", "toolu_b")
    _agent_request(rig, BG, "make")
    assert rig.sess.open_prompts()[1]["tool_info"]["tool_use_id"] == ""
    _agent_post(rig, BG, "make", "toolu_a")
    assert owners(rig) == [None, BG]


def test_parallel_calls_with_other_inputs_close_only_the_asking_one(rig):
    rig.launch(BG)
    rig.question()
    _agent_pre(rig, BG, "make", "toolu_a")
    _agent_pre(rig, BG, "make test", "toolu_b")
    _agent_request(rig, BG, "make test")
    _agent_post(rig, BG, "make", "toolu_a")
    assert owners(rig) == [None, BG]
    _agent_post(rig, BG, "make test", "toolu_b")
    assert owners(rig) == [None]


def test_an_ended_call_is_forgotten(rig):
    """The same input asked again after an earlier call ended: only the new
    call is in flight, so it is the one borrowed."""
    rig.launch(BG)
    rig.question()
    _agent_pre(rig, BG, "make", "toolu_a")
    _agent_post(rig, BG, "make", "toolu_a")
    _agent_pre(rig, BG, "make", "toolu_b")
    _agent_request(rig, BG, "make")
    _agent_post(rig, BG, "make", "toolu_b")
    assert owners(rig) == [None]


def test_an_ended_call_by_failure_is_forgotten(rig):
    rig.launch(BG)
    rig.question()
    _agent_pre(rig, BG, "make", "toolu_a")
    _agent_post(rig, BG, "make", "toolu_a", "PostToolUseFailure")
    _agent_pre(rig, BG, "make", "toolu_b")
    _agent_request(rig, BG, "make")
    _agent_post(rig, BG, "make", "toolu_b", "PostToolUseFailure")
    assert owners(rig) == [None]


def test_another_agents_call_is_not_borrowed(rig):
    rig.launch(BG)
    rig.launch(OTHER)
    rig.question()
    _agent_pre(rig, OTHER, "make", "toolu_other")
    _agent_request(rig, BG, "make")
    assert rig.sess.open_prompts()[1]["tool_info"]["tool_use_id"] == ""


def test_subagent_stop_forgets_its_calls(rig):
    rig.launch(BG)
    _agent_pre(rig, BG, "make", "toolu_a")
    assert BG in rig.sess.agent_tool_calls
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert BG not in rig.sess.agent_tool_calls


def test_agent_tool_calls_are_capped_newest_kept():
    sess = TrackedSession(name="claude-x", label="x")
    cap = state_mod.AGENT_TOOL_CALLS_CAP
    for i in range(cap + 3):
        sess.agent_tool_started(BG, f"t{i}", "Bash", f"d{i}")
    calls = sess.agent_tool_calls[BG]
    assert len(calls) == cap
    assert "t0" not in calls and f"t{cap + 2}" in calls


# -- a foreground agent's prompt and the interrupt (rev-iter1-001) ----------

def _interrupt(rig, run_async):
    run_async(rig.bot.notify(rig.sess, "dialog_ended_in_terminal",
                             {"entered_at": rig.sess.interactive_entered_at}))


def test_foreground_agent_prompt_ends_with_the_interrupted_turn(rig, run_async):
    rig.launch(FG, background=False)
    rig.agent_bash(FG, "make")
    _interrupt(rig, run_async)
    assert rig.sess.status != Status.INTERACTIVE
    assert rig.sess.open_prompts() == []


def test_agent_moved_to_the_background_keeps_waiting(rig, run_async):
    rig.launch(FG, background=False)
    rig.hook("PostToolUse", tool_name="Agent",
             tool_input={"subagent_type": "ship-reviewer", "description": "Review",
                         "prompt": "Review it"},
             tool_use_id=f"toolu_launch_{FG}",
             tool_response={"isAsync": True, "agentId": FG})
    rig.agent_bash(FG, "make")
    rig.claude_bash()
    _interrupt(rig, run_async)
    assert rig.sess.status == Status.INTERACTIVE
    assert owners(rig) == [FG]



def test_interrupt_closes_the_dead_foreground_agents_prompt_beside_a_background_one(
        rig, run_async):
    """rev-iter2-002: prompt by prompt, a foreground agent's prompt dies
    with the interrupted turn even while a background agent's survives."""
    rig.launch(BG)
    rig.launch(FG, background=False)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    fg_msg = [m["msg_id"] for m in rig.prompt_messages()
              if "pytest" in (m.get("text") or "")]
    _interrupt(rig, run_async)
    assert owners(rig) == [BG]
    assert fg_msg and all(rig.sess.prompt_known_closed(m) for m in fg_msg)
    bg = rig.sess.prompt_surface(rig.sess.open_prompts()[0])
    rig.tap(bg, "allow")
    assert rig.sess.status != Status.INTERACTIVE
    assert rig.sess.open_prompts() == []

# -- the Stop-carry window (rev-iter1-002) ----------------------------------

def _during_carry(rig, action):
    """Run *action* (a coroutine function) once, while Claude's Stop that
    carries an agent's prompt over is being handled."""
    real = rig.recv.notify_fn
    done = []

    async def _notify(sess, event, context):
        if sess.prompt_wait_carry and event == "idle_prompt" and not done:
            done.append(True)
            assert sess.status != Status.INTERACTIVE
            await action()
        await real(sess, event, context)

    rig.recv.notify_fn = _notify
    return done


def test_prompt_arriving_during_the_carry_is_one_more(rig):
    rig.launch(BG)
    rig.launch(OTHER)
    rig.question()
    rig.agent_bash(BG, "make")

    async def _other_asks():
        rig.recv._recent_fingerprints.clear()
        import json
        await rig.recv._on_datagram(json.dumps({
            "session": S, "hook_event_name": "PermissionRequest",
            "transcript_path": "", "tool_name": "Bash",
            "tool_input": _bash("npm test"), "agent_id": OTHER,
            "agent_type": "ship-tester",
            "aipager_reply_addr": "/nonexistent/reply-other.sock",
            "aipager_request_id": "req-other"}).encode())

    done = _during_carry(rig, _other_asks)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Agents run.")
    assert done
    assert rig.sess.status == Status.INTERACTIVE
    assert sorted(owners(rig)) == sorted([BG, OTHER])
    texts = [m["text"] for m in rig.prompt_messages()]
    assert any("npm test" in t for t in texts)


def test_tap_during_the_carry_refuses_and_keeps_its_buttons(rig):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make")
    extra = rig.prompt_messages()[-1]["msg_id"]
    seen = {}

    async def _tap():
        u, q = _update(extra, "allow")
        await rig.bot._handle_callback(u, MagicMock())
        seen["q"] = q

    _during_carry(rig, _tap)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Agents run.")
    q = seen["q"]
    assert answers(q) == [str(callbacks.PROMPT_SETTLING)]
    q.edit_message_reply_markup.assert_not_awaited()
    assert rig.decisions == [] and rig.keys == []
    # After the carry the same button answers.
    rig.tap(extra, "allow")
    assert [d["behavior"] for _r, d in rig.decisions] == ["allow"]


# -- a deferred auto-deny behind a separate prompt (rev-iter1-004) ----------

def test_answering_a_separate_prompt_types_the_refusal_behind_it(
        mk_bot, monkeypatch, run_async):
    rig = Rig(mk_bot, monkeypatch, run_async, card=False)
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1")
    first = rig.prompt_messages()[-1]["msg_id"]
    monkeypatch.setattr(rig.bot, "_rule_denial",
                        lambda sess, tool: (tool == "Bash", None))
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=False)
    assert rig.keys == []
    assert len(rig.sess.open_prompts()) == 2
    rig.tap(first, "allow")
    assert [d["behavior"] for _r, d in rig.decisions] == ["allow"]
    assert rig.keys == ["Down"] * 5 + ["Enter"]
    assert rig.sess.open_prompts() == []


# -- the wait resumed after a Stop (rev-iter1-005) --------------------------

def _carried(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Agent runs.")
    assert rig.sess.status == Status.INTERACTIVE
    assert rig.sess.resumed_after_stop
    return rig.prompt_messages()[-1]["msg_id"]


def test_last_carried_answer_by_tap_leaves_the_session_idle(rig):
    msg = _carried(rig)
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.IDLE
    assert not rig.sess.resumed_after_stop


def test_last_carried_prompt_closed_by_the_agent_leaves_it_idle(rig):
    _carried(rig)
    _agent_pre(rig, BG, "make", "toolu_a")
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.IDLE


def test_answer_after_a_carried_stop_releases_held_messages(rig, run_async, monkeypatch):
    msg = _carried(rig)
    injected = []

    async def _inject(sess, text, *a, **k):
        injected.append(text)
        return True

    monkeypatch.setattr(rig.bot, "_inject_prompt", _inject)
    sess = rig.sess
    sess.queue_prompt("also check the docs", 41)
    sess.dialog_hold = {"entered_at": sess.interactive_entered_at,
                        "told": set(), "notices": []}
    rig.tap(msg, "allow")
    assert sess.dialog_hold["closed"] == "answered"
    run_async(rig.bot.notify(sess, "dialog_hold_settle", {}))
    assert injected == ["also check the docs"]


def test_terminal_prompt_after_a_carried_stop_starts_a_turn(rig):
    msg = _carried(rig)
    sess = rig.sess
    seq = sess.turn_seq
    seen = {}
    real = rig.recv.notify_fn

    async def _notify(s, event, context):
        if event == "user_prompt_submit":
            seen["status"] = s.status
            seen["open"] = len(s.open_prompts())
        await real(s, event, context)

    rig.recv.notify_fn = _notify
    rig.hook("UserPromptSubmit", prompt="and update the changelog")
    assert seen == {"status": Status.BUSY, "open": 1}
    assert sess.turn_seq == seq + 1
    assert sess.status == Status.INTERACTIVE
    assert owners(rig) == [BG]
    assert not sess.resumed_after_stop and not sess.prompt_wait_carry
    # The turn runs now: the last answer makes the session work.
    rig.tap(msg, "allow")
    assert sess.status == Status.BUSY


def test_continuation_after_a_carried_stop_is_claudes_turn_again(rig):
    msg = _carried(rig)
    rig.hook("UserPromptSubmit",
             prompt="<task-notification><task-id>x</task-id></task-notification>")
    assert not rig.sess.resumed_after_stop
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


def test_claudes_tool_call_after_a_carried_stop_is_its_turn_again(rig):
    msg = _carried(rig)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_r")
    assert not rig.sess.resumed_after_stop
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


# -- Claude's own prompt refused while an agent's is open (rev-iter1-006) ---

def test_claudes_refused_prompt_closes_while_an_agents_is_open(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.claude_bash(tool_use_id="toolu_p1")
    rig.hook("PostToolUseFailure", tool_name="Bash",
             tool_input=_bash("rm -rf build/"), tool_use_id="toolu_p1",
             error="The user doesn't want to proceed with this tool use.")
    assert owners(rig) == [BG]
    assert rig.sess.status == Status.INTERACTIVE


def test_claudes_lone_refused_prompt_keeps_its_record(rig):
    """Alone, its record stays for dialog_ended_in_terminal's line."""
    rig.claude_bash(tool_use_id="toolu_p1")
    rig.hook("PostToolUseFailure", tool_name="Bash",
             tool_input=_bash("rm -rf build/"), tool_use_id="toolu_p1",
             error="The user doesn't want to proceed with this tool use.")
    assert owners(rig) == [None]
    assert rig.sess.prompt_known_closed(CARD)


# -- a second prompt after the wait ended is not shown (review nit) ---------

def test_extra_prompt_after_the_wait_ended_is_not_shown(rig, run_async):
    rig.sess.status = Status.IDLE
    before = len(rig.sent)
    run_async(rig.bot.notify(rig.sess, "extra_prompt", {
        "tool_info": {"name": "Bash", "summary": "make", "input": _bash("make")}}))
    assert len(rig.sent) == before
    assert rig.sess.extra_prompts == []
