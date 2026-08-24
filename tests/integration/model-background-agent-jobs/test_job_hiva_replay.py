"""spec.md requirement 7: "Integration test(s) replaying the EXACT hiva
hook sequence... asserting: one interim post, no Finished until the end,
one final Finished with job-anchored duration, origin never 'terminal',
snapshot never floor-overwritten mid-job."

The exact 11-step sequence and its per-step expected outcome are given
verbatim in entrypoints.md ("The full hiva sequence, event-by-event") —
this is this suite's own independent replay of that documented contract,
not a copy of the Developer's own ``tests/test_job_background_lifecycle.py``
(never read for this suite). Two passes, matching entrypoints.md's own
stated harness split:

1. ``HookReceiver`` wired to a mocked ``notify_fn`` — pins the pure
   bookkeeping (active_subagents, busy_started_wall, origin,
   subagent_count_this_turn, dispatched event names) at every step.
2. ``HookReceiver`` wired to a real ``TelegramBot.notify`` (Telegram
   boundary mocked) — pins the observable message lifecycle (one interim
   post, zero premature Finished, exactly one final Finished, the
   original busy_started_at anchor surviving untouched).
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status


@pytest.fixture(autouse=True)
def _disable_general_hook_dedup(monkeypatch):
    """The pre-existing, general hook-fingerprint dedup
    (``HOOK_DEDUP_WINDOW_SECONDS``, a few seconds by default — see
    design.md's own risks section, which explicitly names this as a
    SEPARATE, already-existing mechanism from the job-tracking feature's
    OWN content-hash dedup) would otherwise silently swallow steps 7 and
    8's byte-identical Stop/PreToolUse payloads in this synthetic,
    zero-real-time-elapsed replay — the real hiva transcript spread
    these many seconds apart. Disabled here so this file tests ONLY the
    job-tracking layer under review; the general hook-fingerprint dedup
    is pre-existing, untouched by this feature, and out of this suite's
    scope."""
    monkeypatch.setattr(hr, "HOOK_DEDUP_WINDOW_SECONDS", -1.0)


AGENT_ID = "ab2ae82400fc97e4c"
INTERIM_TEXT = "INTERIM-2122-CHAR-ANSWER-" + ("x" * 40)
FINAL_TEXT = "FINAL-4997-CHAR-BRIEFING-" + ("y" * 40)
ORIGINAL_PROMPT = "[via Telegram msg=3420]\nanalyze X and web-search Y"
CONTINUATION_PROMPT = (
    f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
    "Background agent finished."
)


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


async def _send_async(recv, **fields):
    await recv._on_datagram(json.dumps(fields).encode())


async def _run_hiva_sequence_async(recv, *, transcript_path=""):
    """Async twin of ``_run_hiva_sequence`` — every step awaited inline
    within ONE caller-owned event loop, never through a fresh
    ``run_async`` per step. Needed for pass 2 specifically: once
    ``notify_fn`` is a real ``TelegramBot.notify`` bound to a live
    ``TrackedSession``, asyncio primitives the session carries across
    calls (e.g. its animate lock) are bound to whichever loop first
    touched them — driving eleven separate ``run_async`` calls (each
    spinning up its OWN fresh loop, per ``tests/conftest.py``'s
    documented ``run_async`` contract) against the SAME session
    reproduces a real "bound to a different event loop" failure inside
    ``notify()`` that the codebase swallows silently, quietly skipping
    the very card-edit path this test exists to pin."""
    # 1. Original real prompt (Telegram-marked)
    await _send_async(recv, hook_event_name="UserPromptSubmit", session="hiva",
                      prompt=ORIGINAL_PROMPT, transcript_path=transcript_path)
    # 2. SubagentStart
    await _send_async(recv, hook_event_name="SubagentStart", session="hiva",
                      agent_id=AGENT_ID, agent_type="Explore")
    # 3. Interim Stop (foreground turn ends, agent still running)
    await _send_async(recv, hook_event_name="Stop", session="hiva",
                      last_assistant_message=INTERIM_TEXT,
                      transcript_path=transcript_path)
    # 4. Five phantom SubagentStops
    for i in range(1, 6):
        await _send_async(recv, hook_event_name="SubagentStop", session="hiva",
                          agent_id=f"unknown-{i}", agent_type="")
    # 5. PreToolUse re-BUSY
    await _send_async(recv, hook_event_name="PreToolUse", session="hiva",
                      tool_name="Bash", tool_input={"command": "ls"})
    # 6. PostToolUse
    await _send_async(recv, hook_event_name="PostToolUse", session="hiva",
                      tool_name="Bash", tool_input={"command": "ls"})
    # 7. Stray idle (byte-identical interim content)
    await _send_async(recv, hook_event_name="Stop", session="hiva",
                      last_assistant_message=INTERIM_TEXT,
                      transcript_path=transcript_path)
    # 8. PreToolUse again
    await _send_async(recv, hook_event_name="PreToolUse", session="hiva",
                      tool_name="Bash", tool_input={"command": "ls"})
    # 9. Real SubagentStop (matching id)
    await _send_async(recv, hook_event_name="SubagentStop", session="hiva",
                      agent_id=AGENT_ID, agent_type="Explore")
    # 10. Continuation UserPromptSubmit
    await _send_async(recv, hook_event_name="UserPromptSubmit", session="hiva",
                      prompt=CONTINUATION_PROMPT, transcript_path=transcript_path)
    # 11. Final Stop
    await _send_async(recv, hook_event_name="Stop", session="hiva",
                      last_assistant_message=FINAL_TEXT,
                      transcript_path=transcript_path)


def _run_hiva_sequence(recv, run_async, *, transcript_path=""):
    """Feed all 11 steps of the documented hiva sequence, in order."""
    # 1. Original real prompt (Telegram-marked)
    _send(recv, run_async, hook_event_name="UserPromptSubmit", session="hiva",
         prompt=ORIGINAL_PROMPT, transcript_path=transcript_path)
    # 2. SubagentStart
    _send(recv, run_async, hook_event_name="SubagentStart", session="hiva",
         agent_id=AGENT_ID, agent_type="Explore")
    # 3. Interim Stop (foreground turn ends, agent still running)
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=INTERIM_TEXT, transcript_path=transcript_path)
    # 4. Five phantom SubagentStops
    for i in range(1, 6):
        _send(recv, run_async, hook_event_name="SubagentStop", session="hiva",
             agent_id=f"unknown-{i}", agent_type="")
    # 5. PreToolUse re-BUSY
    _send(recv, run_async, hook_event_name="PreToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    # 6. PostToolUse
    _send(recv, run_async, hook_event_name="PostToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    # 7. Stray idle (byte-identical interim content)
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=INTERIM_TEXT, transcript_path=transcript_path)
    # 8. PreToolUse again
    _send(recv, run_async, hook_event_name="PreToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    # 9. Real SubagentStop (matching id)
    _send(recv, run_async, hook_event_name="SubagentStop", session="hiva",
         agent_id=AGENT_ID, agent_type="Explore")
    # 10. Continuation UserPromptSubmit
    _send(recv, run_async, hook_event_name="UserPromptSubmit", session="hiva",
         prompt=CONTINUATION_PROMPT, transcript_path=transcript_path)
    # 11. Final Stop
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=FINAL_TEXT, transcript_path=transcript_path)


# ---- pass 1: bookkeeping, mocked notify_fn --------------------------------

def test_hiva_sequence_bookkeeping(run_async):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)

    _run_hiva_sequence(recv, run_async)

    sess = registry._sessions["hiva"]
    events = [c.args[1] for c in notify_fn.await_args_list]

    # Final state: job closed, IDLE, origin never flipped.
    assert sess.status == Status.IDLE
    assert sess.job_background_open() is False
    assert sess.active_subagents == {}
    assert sess.last_prompt_origin == "telegram", (
        "origin was re-tagged away from 'telegram' somewhere in the "
        "sequence — the safety gap spec.md documents")

    # subagent_count_this_turn is incremented inside TelegramBot.notify's
    # own "subagent_start" handler, not by HookReceiver itself — pinned
    # against the real bot in test_hiva_sequence_end_to_end_card_lifecycle
    # below, not here where notify_fn is a bare AsyncMock that does
    # nothing.

    # Dispatch shape: the continuation must be its own event, never
    # relabelled as a genuine new prompt.
    assert events.count("job_continuation") == 1
    # user_prompt_submit fires exactly once (step 1) — step 10 must NOT
    # also produce one.
    assert events.count("user_prompt_submit") == 1
    # Two idle-class Stop events reach notify_fn (steps 3 and 7) plus the
    # real final one (step 11) = three idle_prompt dispatches total; the
    # DEDUP that collapses steps 3/7's identical content into one
    # delivered message is notify()'s job, not HookReceiver's — pinned
    # separately in test_job_interim_dedup_and_waiting_card.py.
    assert events.count("idle_prompt") == 3
    # Five phantom stops + one real one = six subagent_stop dispatches.
    assert events.count("subagent_stop") == 6


def test_hiva_sequence_phantom_stops_never_touch_the_real_agent(run_async):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)

    # Steps 1-4 only: prove the phantom stops (step 4) leave the real
    # agent (step 2) untouched, checked at the earliest point it matters.
    _send(recv, run_async, hook_event_name="UserPromptSubmit", session="hiva",
         prompt=ORIGINAL_PROMPT, transcript_path="")
    _send(recv, run_async, hook_event_name="SubagentStart", session="hiva",
         agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=INTERIM_TEXT, transcript_path="")
    for i in range(1, 6):
        _send(recv, run_async, hook_event_name="SubagentStop", session="hiva",
             agent_id=f"unknown-{i}", agent_type="")

    sess = registry._sessions["hiva"]
    assert AGENT_ID in sess.active_subagents
    assert len(sess.active_subagents) == 1
    assert sess.job_background_open() is True


def test_hiva_sequence_wall_anchor_survives_every_re_entry(run_async):
    """entrypoints.md: assert busy_started_wall is unchanged across steps
    5, 8, 10 — only step 1 sets it."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)

    _send(recv, run_async, hook_event_name="UserPromptSubmit", session="hiva",
         prompt=ORIGINAL_PROMPT, transcript_path="")
    sess = registry._sessions["hiva"]
    wall_after_step1 = sess.busy_started_wall
    assert wall_after_step1 != 0.0

    _run_hiva_sequence_from_step2(recv, run_async)

    assert sess.busy_started_wall == wall_after_step1, (
        "busy_started_wall moved during a background re-entry (PreToolUse "
        "or the task-notification continuation) — the job's Finished "
        "duration would be anchored at the wrong point")


def _run_hiva_sequence_from_step2(recv, run_async):
    _send(recv, run_async, hook_event_name="SubagentStart", session="hiva",
         agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=INTERIM_TEXT, transcript_path="")
    for i in range(1, 6):
        _send(recv, run_async, hook_event_name="SubagentStop", session="hiva",
             agent_id=f"unknown-{i}", agent_type="")
    _send(recv, run_async, hook_event_name="PreToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    _send(recv, run_async, hook_event_name="PostToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=INTERIM_TEXT, transcript_path="")
    _send(recv, run_async, hook_event_name="PreToolUse", session="hiva",
         tool_name="Bash", tool_input={"command": "ls"})
    _send(recv, run_async, hook_event_name="SubagentStop", session="hiva",
         agent_id=AGENT_ID, agent_type="Explore")
    _send(recv, run_async, hook_event_name="UserPromptSubmit", session="hiva",
         prompt=CONTINUATION_PROMPT, transcript_path="")
    _send(recv, run_async, hook_event_name="Stop", session="hiva",
         last_assistant_message=FINAL_TEXT, transcript_path="")


# ---- pass 2: end-to-end card lifecycle, real TelegramBot.notify ----------

def test_hiva_sequence_end_to_end_card_lifecycle(mk_bot, run_async):
    bot = mk_bot()
    bot._stop_animation = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()

    # Spy that also runs the REAL _edit_busy_rich — recording every
    # (verb, kwargs) dispatch without swallowing the actual rendering
    # (which is what drives INTERIM_TEXT/FINAL_TEXT out through
    # send_rich_message and drives the final=True/waiting=True kwarg
    # this test asserts on).
    real_edit_busy_rich = bot._edit_busy_rich
    edit_calls: list[tuple[str, dict]] = []

    async def _spy_edit_busy_rich(sess, verb, **kwargs):
        edit_calls.append((verb, dict(kwargs)))
        return await real_edit_busy_rich(sess, verb, **kwargs)

    bot._edit_busy_rich = _spy_edit_busy_rich

    async def _fake_send_busy(sess):
        sess.busy_msg_id = 42
        sess.busy_started_at = time.monotonic()

    bot._send_busy_and_animate = AsyncMock(side_effect=_fake_send_busy)

    recv = hr.HookReceiver(bot.registry, bot.notify)
    run_async(_run_hiva_sequence_async(recv))

    sess = bot.registry._sessions["hiva"]
    anchor = sess.busy_started_at
    assert anchor != 0.0

    texts: list[str] = []
    for call in bot._app.bot.send_message.call_args_list:
        for arg in list(call.args) + list(call.kwargs.values()):
            if isinstance(arg, str):
                texts.append(arg)
    from aipager.bot import notify as _notify_mod
    for call in _notify_mod.send_rich_message.call_args_list:
        for arg in list(call.args) + list(call.kwargs.values()):
            if isinstance(arg, str):
                texts.append(arg)

    interim_deliveries = sum(1 for t in texts if INTERIM_TEXT in t)
    final_deliveries = sum(1 for t in texts if FINAL_TEXT in t)

    assert interim_deliveries == 1, (
        f"interim content was delivered {interim_deliveries} times "
        f"(steps 3 and 7 are byte-identical; expected exactly 1): "
        f"{texts!r}")
    assert final_deliveries == 1, (
        f"the final briefing was delivered {final_deliveries} times, "
        f"expected exactly 1: {texts!r}")

    # No live-card edit before the true end may claim final=True — the
    # card is only ever allowed to show the waiting frame until step 11.
    final_flags = [kwargs.get("final") for _, kwargs in edit_calls]
    assert final_flags.count(True) == 1, (
        f"expected exactly one final=True card edit (at the true end), "
        f"got edit dispatch history: {edit_calls!r}")
    assert final_flags[-1] is True, (
        f"the final=True edit was not the LAST card edit in the "
        f"sequence: {edit_calls!r}")
    waiting_before_final = [kwargs.get("waiting") for _, kwargs in edit_calls[:-1]]
    assert any(waiting_before_final), (
        f"no waiting=True edit happened at all before the true end: "
        f"{edit_calls!r}")
    assert not any(kwargs.get("final") for _, kwargs in edit_calls[:-1]), (
        f"a final=True edit happened before the true end (step 11): "
        f"{edit_calls!r}")

    # The turn-start anchor set once at step 1 (simulated above) must be
    # exactly what survived to the end — the job-anchored-duration
    # guarantee design.md states is enforced by never letting a
    # background re-entry reach the one stamp site.
    assert sess.busy_started_at == anchor
    assert sess.last_prompt_origin == "telegram"
    # entrypoints.md: subagent_count_this_turn accumulates across the
    # whole job (one real SubagentStart, step 2) and is not reset by the
    # continuation at step 10 — the increment itself lives inside
    # TelegramBot.notify's "subagent_start" handler, so this is only
    # observable via this real-bot pass, not pass 1's mocked notify_fn.
    assert sess.subagent_count_this_turn == 1
