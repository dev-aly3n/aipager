"""The hook datagram handler's side (design.md success criteria 3 and 9;
entrypoints.md "From aipager/dtach/hook_receiver.py").

A subagent's MessageDisplay (any non-empty agent_id) never becomes the
parent's ``assistant_text`` and never changes the turn's commentary; it
becomes that agent's activity, which shows up under the next answer. A
subagent's PreToolUse becomes its tool phrase; the parent's Agent call
gives the description, matched first-in-first-out per type and dropped
when ambiguous.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import Status
from tests import answer_status_bb as bb

PROSE = "Writing final.md for the pipeline"


def _events(sink):
    return [e for e, _c in sink]


def _md(run_async, recv, sess, delta, *, agent_id=None, message_id="m1", index=0):
    fields = {"delta": delta, "message_id": message_id, "index": index,
              "final": False}
    if agent_id is not None:
        fields["agent_id"] = agent_id
    bb.hook(run_async, recv, sess, "MessageDisplay", **fields)


# ---- criterion 9: never the parent's prose ------------------------------------------------

@pytest.fixture
def rig(bot, sess):
    sink: list = []
    return bot, sess, bb.receiver(bot.registry, sink), sink


def test_parents_own_prose_is_still_forwarded(rig, run_async):
    _bot, sess, recv, sink = rig
    _md(run_async, recv, sess, "parent words")
    assert _events(sink) == ["assistant_text"]


def test_an_active_subagents_prose_is_not_forwarded(rig, run_async):
    _bot, sess, recv, sink = rig
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    sink.clear()
    _md(run_async, recv, sess, PROSE, agent_id="a1")
    assert "assistant_text" not in _events(sink)


def test_a_detached_background_agents_prose_is_not_forwarded(rig, run_async):
    """The agent is a known background agent but no longer on the card's
    active list (a new card, /stop or the sweep took it off)."""
    _bot, sess, recv, sink = rig
    sess.bg_agent_started("bg1", "pipeline-runner", time.monotonic())
    sess.active_subagents.pop("bg1", None)
    _md(run_async, recv, sess, PROSE, agent_id="bg1")
    assert "assistant_text" not in _events(sink)


def test_a_never_seen_agents_prose_is_not_forwarded(rig, run_async):
    _bot, sess, recv, sink = rig
    _md(run_async, recv, sess, "who am I", agent_id="never-started")
    assert "assistant_text" not in _events(sink)


def test_a_never_seen_agents_prose_adds_no_agent(rig, run_async):
    _bot, sess, recv, _sink = rig
    _md(run_async, recv, sess, "who am I", agent_id="never-started")
    assert sess.bg_agent_labels() == []


def test_an_empty_agent_id_is_the_parents(rig, run_async):
    _bot, sess, recv, sink = rig
    _md(run_async, recv, sess, "parent words", agent_id="")
    assert _events(sink) == ["assistant_text"]


def _busy_with_real_notify(bot, sess):
    sess.status = Status.BUSY

    async def _notify(s, event, context):
        await bot.notify(s, event, context)

    from aipager.dtach import hook_receiver as hr
    return hr.HookReceiver(bot.registry, _notify)


def test_through_the_bot_the_parents_prose_reaches_the_commentary(
    bot, sess, run_async,
):
    """Control for the next test: the wiring does carry the parent's."""
    recv = _busy_with_real_notify(bot, sess)
    _md(run_async, recv, sess, "Parent narration here.")
    assert "Parent narration" in repr(sess.stream_commentary)


def test_through_the_bot_a_detached_agents_prose_leaves_commentary_alone(
    bot, sess, run_async,
):
    recv = _busy_with_real_notify(bot, sess)
    sess.bg_agent_started("bg1", "pipeline-runner", time.monotonic())
    sess.active_subagents.pop("bg1", None)
    before = repr(sess.stream_commentary)
    _md(run_async, recv, sess, PROSE, agent_id="bg1")
    assert repr(sess.stream_commentary) == before


# ---- criterion 3 end to end: what the next answer shows -----------------------------------

def _block(run_async, bot, sess, wire):
    bb.finish(run_async, bot, sess)
    return bb.block_of(bb.answer_text(wire))


def _row_of(block, agent_type):
    rows = [x for x in block.split("\n\n") if f"**{agent_type}**" in x]
    assert len(rows) == 1, block
    return rows[0]


def test_an_agents_prose_shows_as_its_activity(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="pipeline-runner")
    _md(run_async, recv, sess, PROSE, agent_id="a1")
    assert _row_of(_block(run_async, bot, sess, wire), "pipeline-runner") == \
        f"○ 🤖 **pipeline-runner** · {PROSE}"


def test_chunks_of_one_message_join(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="pipeline-runner")
    _md(run_async, recv, sess, "Writing final", agent_id="a1", index=0)
    _md(run_async, recv, sess, ".md for the pipeline", agent_id="a1", index=1)
    assert _row_of(_block(run_async, bot, sess, wire), "pipeline-runner") == \
        f"○ 🤖 **pipeline-runner** · {PROSE}"


def test_a_new_message_replaces_the_prose(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="pipeline-runner")
    _md(run_async, recv, sess, "Old words", agent_id="a1", message_id="m1")
    _md(run_async, recv, sess, "New words", agent_id="a1", message_id="m2")
    assert _row_of(_block(run_async, bot, sess, wire), "pipeline-runner") == \
        "○ 🤖 **pipeline-runner** · New words"


def test_a_blank_new_message_keeps_the_last_prose(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="pipeline-runner")
    _md(run_async, recv, sess, "Real words", agent_id="a1", message_id="m1")
    _md(run_async, recv, sess, "   \n", agent_id="a1", message_id="m2")
    assert _row_of(_block(run_async, bot, sess, wire), "pipeline-runner") == \
        "○ 🤖 **pipeline-runner** · Real words"


def test_an_agents_read_shows_the_file_basename(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Read",
            tool_input={"file_path": "/home/someone/proj/aipager/state.py"})
    assert _row_of(_block(run_async, bot, sess, wire), "Explore") == \
        "○ 🤖 **Explore** · Reading state.py"


def test_an_agents_bash_never_shows_the_command(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Bash",
            tool_input={"command": "cat topsecret.env", "description": "peek"})
    # Its own description (Claude's words), never the command.
    row = _row_of(_block(run_async, bot, sess, wire), "Explore")
    assert row == "○ 🤖 **Explore** · peek"
    assert "topsecret" not in row


def test_an_agents_bash_with_no_description_says_running_a_command(bot, sess, wire,
                                                                    run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Bash", tool_input={"command": "cat topsecret.env"})
    row = _row_of(_block(run_async, bot, sess, wire), "Explore")
    assert row == "○ 🤖 **Explore** · Running a command"


def test_an_agents_grep_shows_the_pattern(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Grep", tool_input={"pattern": "agents_lines"})
    assert _row_of(_block(run_async, bot, sess, wire), "Explore") == \
        "○ 🤖 **Explore** · Searching for agents\\_lines"


def test_the_latest_tool_wins(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Read", tool_input={"file_path": "/x/one.py"})
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Write", tool_input={"file_path": "/x/two.md"})
    assert _row_of(_block(run_async, bot, sess, wire), "Explore") == \
        "○ 🤖 **Explore** · Writing two.md"


def test_prose_beats_a_later_tool(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    _md(run_async, recv, sess, "Looking around", agent_id="a1")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Read", tool_input={"file_path": "/x/one.py"})
    assert _row_of(_block(run_async, bot, sess, wire), "Explore") == \
        "○ 🤖 **Explore** · Looking around"


def test_a_subagents_tool_call_is_still_reported_as_tool_use(rig, run_async):
    _bot, sess, recv, sink = rig
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    sink.clear()
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
            tool_name="Read", tool_input={"file_path": "/x/one.py"})
    assert "tool_use" in _events(sink)


def _launch(run_async, recv, sess, agent_type, description):
    bb.hook(run_async, recv, sess, "PreToolUse", tool_name="Agent",
            tool_input={"subagent_type": agent_type, "description": description,
                        "prompt": "go", "run_in_background": True})


def test_the_agent_calls_description_shows_until_anything_else(
    bot, sess, wire, run_async,
):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "pipeline-runner", "Deliver the settings fix")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="pipeline-runner")
    assert _row_of(_block(run_async, bot, sess, wire), "pipeline-runner") == \
        "○ 🤖 **pipeline-runner** · Deliver the settings fix"


def test_a_task_call_also_gives_the_description(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "PreToolUse", tool_name="Task",
            tool_input={"subagent_type": "Explore", "description": "Map the repo",
                        "prompt": "go"})
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    assert _row_of(_block(run_async, bot, sess, wire), "Explore") == \
        "○ 🤖 **Explore** · Map the repo"


def test_two_pending_calls_of_one_type_give_no_description(
    bot, sess, wire, run_async,
):
    """Ambiguous: never a wrong description, so none."""
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "general-purpose", "First job")
    _launch(run_async, recv, sess, "general-purpose", "Second job")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="general-purpose")
    assert _row_of(_block(run_async, bot, sess, wire), "general-purpose") == \
        "○ 🤖 **general-purpose**"


def test_descriptions_match_by_type(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "Explore", "Explore job")
    _launch(run_async, recv, sess, "pipeline-runner", "Runner job")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a2",
            agent_type="pipeline-runner")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    block = _block(run_async, bot, sess, wire)
    assert (_row_of(block, "pipeline-runner"), _row_of(block, "Explore")) == (
        "○ 🤖 **pipeline-runner** · Runner job", "○ 🤖 **Explore** · Explore job")


def test_a_used_description_is_not_given_twice(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "Explore", "Only once")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a2",
            agent_type="Explore")
    block = _block(run_async, bot, sess, wire)
    assert block.count("Only once") == 1


def test_an_old_pending_call_expires(bot, sess, wire, run_async):
    """A call older than AGENT_CALL_MAX_AGE is dropped when the next call
    is recorded, so it attaches to nothing."""
    from aipager.state import AGENT_CALL_MAX_AGE

    now = time.monotonic()
    sess.agent_call_launched("Explore", "Stale job", now - AGENT_CALL_MAX_AGE - 1)
    sess.agent_call_launched("pipeline-runner", "Fresh job", now)
    sess.bg_agent_started("a1", "Explore", now)
    assert "Stale job" not in _block(run_async, bot, sess, wire)


def test_a_young_pending_call_still_matches(bot, sess, wire, run_async):
    from aipager.state import AGENT_CALL_MAX_AGE

    now = time.monotonic()
    sess.agent_call_launched("Explore", "Young job", now - AGENT_CALL_MAX_AGE + 5)
    sess.agent_call_launched("pipeline-runner", "Fresh job", now)
    sess.bg_agent_started("a1", "Explore", now)
    assert "Young job" in _block(run_async, bot, sess, wire)


def test_pending_calls_are_capped(bot, sess, wire, run_async):
    """Past AGENT_CALLS_CAP the oldest go: the first call of a burst no
    longer attaches."""
    from aipager.state import AGENT_CALLS_CAP

    now = time.monotonic()
    sess.agent_call_launched("Explore", "Oldest job", now)
    for i in range(AGENT_CALLS_CAP):
        sess.agent_call_launched(f"type{i}", f"job {i}", now)
    sess.bg_agent_started("a1", "Explore", now)
    assert "Oldest job" not in _block(run_async, bot, sess, wire)


def test_an_empty_type_records_no_call(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "", "Nobody")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="general-purpose")
    assert "Nobody" not in _block(run_async, bot, sess, wire)
