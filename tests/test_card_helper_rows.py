"""The busy card's row of a helper agent names the agent at the top of its
chain (agents tree, 2026-10-14): ``🤖 pipeline-runner › ship-tester``,
live and settled; the session's own agents' rows are as before."""

from __future__ import annotations

import json
import time

import pytest

from aipager import i18n
from aipager.bot import animation, card_rows
from aipager.dtach import hook_receiver as hr
from tests import answer_status_bb as bb

P, Q, C, G = "a505c081ca3926ed5", "a9e0d1c2b3a4f5e61", "ac1d54c85d78f59ea", "a7e0d1c2b3a4f5e60"
ROBOT = "\U0001f916"


@pytest.fixture
def rig(mk_bot, run_async):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="frontend")
    sess.busy_msg_id = 0          # no card edits: the rows are the point
    sess.tool_history = []
    recv = hr.HookReceiver(bot.registry, bot.notify)
    n = [0]

    def hook(event, **fields):
        n[0] += 1
        recv._recent_fingerprints.clear()
        payload = dict({"session": sess.name, "hook_event_name": event,
                        "transcript_path": "", "_seq": n[0]}, **fields)
        run_async(recv._on_datagram(json.dumps(payload).encode()))

    def call(by, child_type, desc="go"):
        own = {"agent_id": by, "agent_type": "x"} if by else {}
        hook("PreToolUse", tool_name="Agent",
             tool_input={"subagent_type": child_type, "description": desc}, **own)

    return sess, hook, call


def _robots(sess):
    return [s for s, _d in sess.tool_history if s.startswith(ROBOT)]


def test_the_sessions_own_agent_row_is_unchanged(rig):
    sess, hook, call = rig
    call("", "pipeline-runner")
    hook("SubagentStart", agent_id=P, agent_type="pipeline-runner")
    assert _robots(sess) == [f"{ROBOT} pipeline-runner"]
    assert sess.active_subagents[P]["card_label"] == "pipeline-runner"


def test_a_helper_row_names_its_launcher(rig):
    sess, hook, call = rig
    hook("SubagentStart", agent_id=P, agent_type="pipeline-runner")
    call(P, "ship-tester")
    hook("SubagentStart", agent_id=C, agent_type="ship-tester")
    assert _robots(sess)[-1] == f"{ROBOT} pipeline-runner › ship-tester"
    assert sess.active_subagents[C]["card_label"] == "pipeline-runner › ship-tester"


def test_a_deeper_helper_names_the_agent_at_the_top(rig):
    sess, hook, call = rig
    hook("SubagentStart", agent_id=P, agent_type="pipeline-runner")
    call(P, "ship-tester")
    hook("SubagentStart", agent_id=C, agent_type="ship-tester")
    call(C, "Explore")
    hook("SubagentStart", agent_id=G, agent_type="Explore")
    assert _robots(sess)[-1] == f"{ROBOT} pipeline-runner › Explore"


def test_a_helper_of_an_unknown_launcher_is_its_type_alone(rig):
    sess, hook, call = rig
    hook("SubagentStart", agent_id=P, agent_type="pipeline-runner")
    hook("SubagentStart", agent_id=Q, agent_type="r2-runner")
    call(P, "ship-tester")
    call(Q, "ship-tester")
    hook("SubagentStart", agent_id=C, agent_type="ship-tester")
    assert _robots(sess)[-1] == f"{ROBOT} ship-tester"
    # A later exact link does not rename the row: it is fixed at the start.
    hook("PostToolUse", agent_id=P, agent_type="x", tool_name="Agent",
         tool_input={"subagent_type": "ship-tester"},
         tool_response={"isAsync": True, "status": "async_launched", "agentId": C})
    assert _robots(sess)[-1] == f"{ROBOT} ship-tester"


def test_the_settled_helper_row_keeps_its_name(rig):
    sess, hook, call = rig
    hook("SubagentStart", agent_id=P, agent_type="pipeline-runner")
    call(P, "ship-tester")
    hook("SubagentStart", agent_id=C, agent_type="ship-tester")
    hook("SubagentStop", agent_id=C, agent_type="ship-tester")
    (row,) = [(s, d) for s, d in sess.tool_history if "ship-tester" in s]
    assert row[1] is True
    assert row[0].startswith(f"{ROBOT} pipeline-runner › ship-tester · 0 tool calls · ")
    # The card's tally still reads it as an agent row.
    assert card_rows._AGENT_DONE_RE.match(row[0]).group("agent") == (
        "pipeline-runner › ship-tester")


def test_the_live_rows_use_the_card_label():
    info = {"type": "ship-tester", "card_label": "pipeline-runner › ship-tester",
            "activity": "Read: /a.py", "started_at": time.monotonic() - 12}
    assert animation._agent_live_row(info).startswith(
        f"{ROBOT} pipeline-runner › ship-tester · Read: /a.py · ")
    en, fa = i18n.get("en"), i18n.get("fa")
    assert card_rows.live_agent_final(en, info) == (
        f"`{ROBOT} pipeline-runner › ship-tester (still running)`")
    assert "`pipeline-runner › ship-tester`" in card_rows.live_agent(fa, info)
    assert "`pipeline-runner › ship-tester`" in card_rows.live_agent_final(fa, info)


def test_a_row_with_no_card_label_uses_its_type():
    info = {"type": "Explore", "activity": "", "started_at": time.monotonic()}
    assert animation._agent_live_row(info).startswith(f"{ROBOT} Explore · starting · ")
    assert card_rows.live_agent_final(i18n.get("en"), info) == (
        f"`{ROBOT} Explore (still running)`")


def test_a_new_turn_inside_the_job_carries_the_helper_row_by_its_name(
    mk_bot, run_async, monkeypatch,
):
    """A new prompt while a helper still runs (roadmap 8.56): the new
    turn's card gets the helper's row, under the name its start drew."""
    from unittest.mock import AsyncMock, MagicMock

    from aipager.state import Status

    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.IDLE
    sess.busy_msg_id = 99
    sess.job_interim_seen = True
    sess.busy_started_at = time.monotonic() - 5
    # The turn that launched it: one tool of its own, then the helper.
    sess.tool_history = [("Read: /a.py", True),
                         (f"{ROBOT} pipeline-runner › ship-tester", False)]
    sess.active_subagents = {C: {
        "type": "ship-tester", "card_label": "pipeline-runner › ship-tester",
        "started_at": time.monotonic() - 5, "history_idx": 1, "activity": "",
        "tool_count": 0, "last_tool_at": 0.0, "tools": []}}

    async def _edit(*_a, **_k):
        return {}

    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich", _edit)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    assert bot.registry.transition("claude-jim", Status.BUSY) is not None
    run_async(bot._send_busy_and_animate(sess))
    assert sess.busy_msg_id == 42
    assert sess.tool_history == [(f"{ROBOT} pipeline-runner › ship-tester", False)]
    assert sess.active_subagents[C]["history_idx"] == 0
