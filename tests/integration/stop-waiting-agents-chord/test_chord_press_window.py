"""The window that takes a markerless prompt for the turn Claude Code
starts after aipager's kill chord (S1, roadmap 8.121 F5) is armed only
right before the second press (rev-iter6-001). One press stops nothing,
so that turn cannot come any sooner; a prompt a person types at the
terminal before then (during the typing gate's look, or in the second
between the presses) is a normal terminal turn: the session goes BUSY,
the second press is not written, and Stop interrupts that turn instead.

The terminal prompt is played as a hook datagram from inside the seams
the chord waits on (``inject._gate_sleep`` for the gate's look,
``inject._key_gap_sleep`` for the gap), as its own task, so a wrongly
armed window (which parks the prompt on the Stop's teardown) shows as a
prompt routed as the wake rather than as a hang.
"""

from __future__ import annotations

import asyncio
import json

from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import BG, OTHER, S

CHORD = b"\x18\x0b"
ESC = b"\x1b"
TYPED = "please also look at the failing test"


def _waiting(rig):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-other")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.")
    assert rig.sess.status is Status.IDLE and rig.sess.shown_state().state == "tasks"
    return rig.sess


async def _typed_at_the_terminal(rig, seen: dict) -> None:
    """A person's prompt, typed at the terminal (no marker), handled as the
    receiver handles any datagram; the routing it got is recorded."""
    rig.recv._recent_fingerprints.clear()
    payload = {"session": S, "hook_event_name": "UserPromptSubmit", "prompt": TYPED,
               "transcript_path": "", "_seq": 7001}
    task = asyncio.ensure_future(rig.recv._on_datagram(json.dumps(payload).encode()))
    for _ in range(50):
        await asyncio.sleep(0)
    seen["task"] = task
    sess = rig.sess
    seen["at"] = (sess.status, sess.last_prompt_origin, sess.turn_prompt,
                  sess.kill_expected_until)


def _stop(rig, seen: dict):
    async def go():
        outcome = await rig.bot._stop_session_core(rig.sess)
        await seen["task"]
        return outcome
    return rig.run_async(go())


def test_a_prompt_during_the_gate_look_is_a_terminal_turn(rig, monkeypatch):
    _waiting(rig)
    seen: dict = {}
    passes = iter([1])

    # One tool call just passed its hook: the gate looks again after a
    # poll, and the person's prompt lands during that poll.
    monkeypatch.setattr(inject, "_fresh_passes", lambda _s, _w: next(passes, 0))

    async def _poll(_delay):
        if "task" not in seen:
            await _typed_at_the_terminal(rig, seen)

    monkeypatch.setattr(inject, "_gate_sleep", _poll)
    outcome = _stop(rig, seen)
    status, origin, prompt, window = seen["at"]
    assert status is Status.BUSY
    assert origin == "terminal" and prompt == TYPED
    assert window == 0.0  # nothing armed: no press was written
    assert CHORD not in rig.writes.bytes()
    # The person's turn runs, so this Stop interrupts it as any working one.
    assert outcome.ok and outcome.mode == "keys"
    assert rig.writes.bytes() == [ESC, ESC]


def test_a_prompt_between_the_presses_is_a_terminal_turn(rig, monkeypatch):
    sess = _waiting(rig)
    seen: dict = {}

    async def _gap(delay):
        rig.writes.append(("gap", delay))
        await _typed_at_the_terminal(rig, seen)

    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    outcome = _stop(rig, seen)
    status, origin, prompt, window = seen["at"]
    assert status is Status.BUSY
    assert origin == "terminal" and prompt == TYPED
    assert window == 0.0
    # One press only: the second is not written into the person's turn.
    assert rig.writes.bytes().count(CHORD) == 1
    assert outcome.ok and outcome.mode == "keys"
    assert rig.writes.bytes() == [CHORD, ESC, ESC]
    # The agents were not stopped by aipager: still on record.
    assert BG in sess.bg_agents or BG in sess.active_subagents


def test_control_the_window_is_armed_for_the_second_press(rig, monkeypatch):
    """Without a person's prompt the window is open when the second press
    goes out, and the wake that follows is the job's own turn."""
    sess = _waiting(rig)
    armed: list[float] = []
    real_run = inject._run

    async def _run(args, stdin=b"", timeout=5):
        if stdin == CHORD:
            armed.append(sess.kill_expected_until)
        return await real_run(args, stdin=stdin, timeout=timeout)

    monkeypatch.setattr(inject, "_run", _run)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert outcome.ok and outcome.mode == "kill_agents"
    assert armed[0] == 0.0 and armed[1] > 0.0
    rig.hook("UserPromptSubmit", prompt="Background agent \"Review\" was stopped by the user.")
    assert sess.last_prompt_origin != "terminal"
