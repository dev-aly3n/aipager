"""After Stop pressed the kill chord (roadmap 8.121 F5), every surface says
so (design-6 section 6): background agents and one job shell (the probe's
G2 shape, with a second agent so one prompt has a message of its own). The card's second line names the shell, which keeps running; the
pinned bar keeps "(idle, 1 shell running)" and no agent; an agent's open
prompt message loses its buttons and says its agent was stopped, and a
late tap on it types nothing and sends no hook reply."""

from __future__ import annotations

import time

from aipager.state import Status
from tests.two_prompts_rig import ALICE, BG, CARD, GROUP, OTHER, answers

CHORD = b"\x18\x0b"


def _waiting_with_a_shell(rig):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-other")
    rig.sess.bg_shells["b3vwpa6yy"] = {"label": "sleep 150", "in_job": True,
                                       "started_at": time.monotonic()}
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.")
    assert rig.sess.status is Status.IDLE
    return rig.sess


def test_card_toast_and_bar_after_the_stop(rig):
    sess = _waiting_with_a_shell(rig)
    toast = answers(rig.tap(CARD, "stop", uid=ALICE))
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert rig.card_edits[-1][1].split("\n") == [
        "⚠️ <b>api</b> · @alice stopped 2 background agents",
        "🖥 1 background shell keeps running."]
    assert toast == ["⏹ @alice stopped 2 background agents of api\n"
                     "1 background shell keeps running."]
    bar = rig.bot._render_pinned(GROUP, [sess])[0]
    assert "(idle, 1 shell running)" in bar
    assert "agent" not in bar
    assert sess.shown_state().state == "idle"
    assert sess.bg_agents == {} and len(sess.bg_shells) == 1


def _agent_prompt_message(rig):
    """The second agent's prompt, sent as its own message (the first one's
    sits on the card)."""
    msgs = rig.prompt_messages()
    assert msgs, rig.sent
    return msgs[-1]


def test_an_agents_open_prompt_message_says_its_agent_was_stopped(rig):
    sess = _waiting_with_a_shell(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.agent_bash(OTHER, "rm -rf dist")
    assert sess.status is Status.INTERACTIVE
    msg = _agent_prompt_message(rig)
    rig.tap(CARD, "stop", uid=ALICE)
    assert rig.writes.bytes() == [CHORD, CHORD]
    edits = [c for c in rig.bot._app.bot.edit_message_text.await_args_list
             if c.kwargs.get("message_id") == msg["msg_id"]]
    assert edits, rig.bot._app.bot.edit_message_text.await_args_list
    last = edits[-1].kwargs
    assert last["text"].endswith(
        "\n\n⏹ This prompt closed because its background agent was stopped.")
    assert last["reply_markup"] is None


def test_a_late_tap_on_it_types_nothing(rig):
    _waiting_with_a_shell(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.agent_bash(OTHER, "rm -rf dist")
    msg = _agent_prompt_message(rig)
    rig.tap(CARD, "stop", uid=ALICE)
    # Closed for good: its surface is on record as closed, and no record of
    # it is left to be counted open again by a later wait.
    sess = rig.sess
    assert msg["msg_id"] in sess.closed_prompt_msgs
    assert sess.extra_prompts == [] and sess.pending_prompt_msg is None
    assert sess.pending_permission is None and sess.carried_inline_prompt is None
    before = list(rig.writes)
    rig.decisions.clear()
    rig.tap(msg["msg_id"], "allow", uid=ALICE)
    assert rig.writes == before
    assert rig.decisions == []


def test_control_the_same_tap_without_the_stop_answers(rig):
    """Without the Stop the tap does reach the agent's dialog, so the test
    above is not passing for an unrelated reason."""
    _waiting_with_a_shell(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.agent_bash(OTHER, "rm -rf dist")
    msg = _agent_prompt_message(rig)
    rig.decisions.clear()
    rig.tap(msg["msg_id"], "allow", uid=ALICE)
    assert rig.decisions or rig.writes


def test_an_answers_agents_block_settles_to_stopped(rig, monkeypatch):
    """The answer that went out while the agents worked carries their
    block; after the Stop it settles to "⏹ 2 agents stopped", never
    "done" (design-6 section 6)."""
    from aipager.bot import notify
    from aipager.state import BG_AGENTS_SETTLE_SECONDS

    sess = _waiting_with_a_shell(rig)
    sess.bg_shells.clear()  # the agents only
    snap = rig.bot._agents_snapshot(sess)
    assert snap is not None and "2 agents working" in snap["line"]
    rig.bot._remember_agents_line(sess, GROUP, 555,
                                  notify._with_agents_block("Started them.", snap["line"]),
                                  snap, False)
    edits: list[str] = []

    async def _rich(chat_id, msg_id, text, **_kw):
        if msg_id == 555:
            edits.append(text)
        return {"ok": True}

    monkeypatch.setattr(notify, "edit_message_text_rich", _rich)
    rig.tap(CARD, "stop", uid=ALICE)
    assert rig.writes.bytes() == [CHORD, CHORD]
    later = time.monotonic() + BG_AGENTS_SETTLE_SECONDS + 3600.0
    rig.run_async(rig.bot._tick_agents_lines(sess, later))
    assert edits, sess.agents_lines
    block = edits[-1].rsplit("\n", 1)[-1]
    assert block.startswith("<footer>⏹ 2 agents stopped ("), edits[-1]
    assert "done" not in edits[-1]
