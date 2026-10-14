"""An answer's block follows its agents' own agents (agents tree,
2026-10-14): the one link setter puts a child under the nearest agent the
answer lists, moves a row the link proves misplaced, adds a child that
already stopped as done, and never adds an agent the session itself
starts. Then, through an answer and the monitor's tick, the block shows
the child under its parent."""

from __future__ import annotations

import time

import pytest

from aipager.bot.divider import DIVIDER_SEP
from aipager.state import (
    AGENTS_LINE_ITEMS_CAP,
    BG_AGENTS_SETTLE_SECONDS,
    Status,
    TrackedSession,
)
from tests.test_background_agents_line import (
    ANSWER,
    _finish,
    _hook,
    _markdown,
    _receiver,
    _sess,
    _settle,
    _Wire,
    _wire_bot,
)

P, Q, C, G = "a505c081ca3926ed5", "a9e0d1c2b3a4f5e61", "ac1d54c85d78f59ea", "a7e0d1c2b3a4f5e60"


def _state(*running) -> TrackedSession:
    sess = TrackedSession(name="claude-frontend", label="frontend", status=Status.BUSY)
    for agent_id, agent_type in running:
        sess.bg_agent_started(agent_id, agent_type, 100.0)
    return sess


def _rec(sess, *ids, parents=None):
    """An answer's record listing *ids*, as the send keeps it."""
    parents = parents or {}
    rec = {"chat_id": 1, "msg_id": 9001, "ends": {}, "ids": list(ids),
           "items": [{"id": i, "kind": "agent", "label": "x", "started": 100.0,
                      "parent": parents.get(i, "")} for i in ids]}
    sess.add_agents_line(rec)
    return rec


def _item(rec, agent_id):
    (item,) = [i for i in rec["items"] if i["id"] == agent_id]
    return item


# ---- the state -----------------------------------------------------------------------

def test_a_child_of_a_listed_agent_joins_under_it():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    rec = _rec(sess, P)
    assert sess.agent_launched_child(P, C, 101.0) is False
    assert rec["ids"] == [P, C]
    assert _item(rec, C) == {"id": C, "kind": "agent", "label": "ship-tester",
                             "started": 100.0, "parent": P, "late": True}
    assert C not in rec["ends"]


def test_a_child_linked_at_its_start_joins_too():
    sess = _state((P, "pipeline-runner"))
    rec = _rec(sess, P)
    sess.agent_call_launched("ship-tester", "Write the tests", 101.0, launcher=P)
    sess.bg_agent_started(C, "ship-tester", 101.5)
    assert _item(rec, C)["parent"] == P


def test_an_agent_the_session_starts_never_joins_an_earlier_answer():
    sess = _state((P, "pipeline-runner"))
    rec = _rec(sess, P)
    sess.agent_call_launched("ship-tester", "Main work", 101.0)
    sess.bg_agent_started(C, "ship-tester", 101.5)
    assert rec["ids"] == [P]


def test_an_ambiguous_start_joins_only_at_its_link():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"))
    rec = _rec(sess, P)
    sess.agent_call_launched("ship-tester", "a", 101.0, launcher=P)
    sess.agent_call_launched("ship-tester", "b", 101.0, launcher=Q)
    sess.bg_agent_started(C, "ship-tester", 101.5)
    assert rec["ids"] == [P]
    sess.agent_launched_child(P, C, 102.0)
    assert _item(rec, C)["parent"] == P


def test_a_grandchild_joins_under_its_own_parent():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"), (G, "Explore"))
    rec = _rec(sess, P)
    sess.agent_launched_child(P, C, 101.0)
    sess.agent_launched_child(C, G, 102.0)
    assert (_item(rec, C)["parent"], _item(rec, G)["parent"]) == (P, C)


def test_a_child_joins_under_the_nearest_listed_ancestor():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"), (G, "Explore"))
    sess.agent_parents[C] = P          # known before the answer, not listed
    rec = _rec(sess, P)
    sess.agent_launched_child(C, G, 102.0)
    assert rec["ids"] == [P, G]
    assert _item(rec, G)["parent"] == P


def test_the_link_moves_a_row_shown_at_the_top():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    rec = _rec(sess, P, C)
    sess.agent_launched_child(P, C, 101.0)
    assert rec["ids"] == [P, C]
    assert _item(rec, C)["parent"] == P


def test_a_listed_agents_own_agents_move_under_it_when_it_joins():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"), (G, "Explore"))
    sess.agent_parents[G] = C
    rec = _rec(sess, P, G)
    sess.agent_launched_child(P, C, 101.0)
    assert (_item(rec, C)["parent"], _item(rec, G)["parent"]) == (P, C)


def test_a_child_whose_link_came_at_its_end_joins_as_done():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    rec = _rec(sess, P)
    sess.bg_agent_stopped(C, 105.0)
    sess.agent_launched_child(P, C, 106.0)
    assert _item(rec, C)["parent"] == P
    assert rec["ends"][C] == {"outcome": "done", "at": 106.0}


def test_an_unknown_child_does_not_join():
    sess = _state((P, "pipeline-runner"))
    rec = _rec(sess, P)
    sess.agent_launched_child(P, C, 101.0)
    assert rec["ids"] == [P]


def test_a_self_link_changes_nothing():
    sess = _state((P, "pipeline-runner"))
    rec = _rec(sess, P)
    assert sess.agent_launched_child(P, P, 101.0) is False
    assert sess.agent_parents == {}
    assert rec["ids"] == [P]


def test_a_full_answer_takes_no_more_agents():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    fillers = [f"f{i:016x}" for i in range(AGENTS_LINE_ITEMS_CAP - 1)]
    rec = _rec(sess, P, *fillers)
    sess.agent_launched_child(P, C, 101.0)
    assert C not in rec["ids"]
    rec["ids"].pop()
    rec["items"].pop()
    sess.agent_launched_child(P, C, 101.0)
    assert C in rec["ids"]


def test_a_done_child_keeps_its_place_after_the_links_go():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    rec = _rec(sess, P)
    sess.agent_launched_child(P, C, 101.0)
    sess.bg_agent_stopped(C, 105.0)
    assert C not in sess.agent_parents
    assert _item(rec, C)["parent"] == P
    assert rec["ends"][C]["outcome"] == "done"


def test_every_pending_answer_follows_the_link():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"))
    first, second = _rec(sess, P), _rec(sess, P)
    sess.agent_launched_child(P, C, 101.0)
    assert (C in first["ids"], C in second["ids"]) == (True, True)


# ---- the card label ------------------------------------------------------------------

def test_the_card_names_a_helper_after_the_agent_at_the_top():
    sess = _state((P, "pipeline-runner"), (C, "ship-tester"), (G, "Explore"))
    sess.agent_parents.update({C: P, G: C})
    assert sess.agent_card_label(P, "pipeline-runner") == "pipeline-runner"
    assert sess.agent_card_label(C, "ship-tester") == "pipeline-runner › ship-tester"
    assert sess.agent_card_label(G, "Explore") == "pipeline-runner › Explore"


@pytest.mark.parametrize("where", ["active", "recent"])
def test_the_top_label_comes_from_the_job_or_the_stopped_agents(where):
    sess = _state((C, "ship-tester"))
    sess.agent_parents[C] = P
    if where == "active":
        sess.active_subagents[P] = {"type": "pipeline-runner"}
    else:
        sess.bg_agents_recent[P] = {"label": "pipeline-runner"}
    assert sess.agent_card_label(C, "ship-tester") == "pipeline-runner › ship-tester"


def test_a_top_of_unknown_name_leaves_the_plain_type():
    sess = _state((C, "ship-tester"))
    sess.agent_parents[C] = P
    assert sess.agent_card_label(C, "ship-tester") == "ship-tester"


# ---- through an answer and the monitor's tick -----------------------------------------

@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **kw):
        w.calls.append((method, dict(payload, _kw=kw)))
        w.next_id += 1
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _block_of(text: str) -> str:
    body, sep, block = text.partition(DIVIDER_SEP)
    assert sep and body.endswith(ANSWER), text
    return block


def test_the_answer_shows_the_tree_and_a_late_child_joins_it(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id=P, agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "PreToolUse", agent_id=P, agent_type="pipeline-runner",
          tool_name="Agent", tool_input={"subagent_type": "ship-tester",
                                         "description": "Write the tests"})
    _hook(run_async, recv, sess, "SubagentStart", agent_id=C, agent_type="ship-tester")
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (sent,) = [p for m, p in wire.calls if m == "sendRichMessage" and ANSWER in _markdown(p)]
    assert _block_of(_markdown(sent)).split("\n\n")[1:3] == [
        "○ 🤖 **pipeline-runner** · Write the tests",
        "- ○ 🤖 **ship-tester** · Write the tests"]
    # C starts an agent of its own after the answer went out.
    _hook(run_async, recv, sess, "PreToolUse", agent_id=C, agent_type="ship-tester",
          tool_name="Agent", tool_input={"subagent_type": "Explore",
                                         "description": "Find the callers"})
    _hook(run_async, recv, sess, "SubagentStart", agent_id=G, agent_type="Explore")
    rec = sess.agents_lines[-1]
    rec["last_try"] -= 60
    before = len(wire.calls)
    _settle(run_async, bot, sess, time.monotonic())
    (edit,) = [p for m, p in wire.calls[before:] if m == "editMessageText"]
    block = _block_of(_markdown(edit))
    assert block.split("\n\n")[0] == "⏳ 3 agents working"
    assert block.split("\n\n")[2] == (
        "- ○ 🤖 **ship-tester** · Find the callers\n"
        "- ○ 🤖 **ship-tester › Explore** · Find the callers")


def test_a_late_child_settles_done_under_its_parent(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id=P, agent_type="pipeline-runner")
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    _hook(run_async, recv, sess, "PreToolUse", agent_id=P, agent_type="pipeline-runner",
          tool_name="Agent", tool_input={"subagent_type": "ship-tester",
                                         "description": "Write the tests"})
    _hook(run_async, recv, sess, "SubagentStart", agent_id=C, agent_type="ship-tester")
    _hook(run_async, recv, sess, "SubagentStop", agent_id=C, agent_type="ship-tester")
    rec = sess.agents_lines[-1]
    rec["last_try"] -= 60
    before = len(wire.calls)
    _settle(run_async, bot, sess, time.monotonic() + BG_AGENTS_SETTLE_SECONDS + 0.1)
    (edit,) = [p for m, p in wire.calls[before:] if m == "editMessageText"]
    lines = _block_of(_markdown(edit)).split("\n\n")
    assert lines[0] == "⏳ 1 agent working"
    assert lines[1].startswith("○ 🤖 **pipeline-runner**")
    assert lines[2].startswith("- ✓ 🤖 **ship-tester** · done (")


def test_agents_of_an_agent_the_answer_does_not_list_never_join():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"), (C, "ship-tester"))
    rec = _rec(sess, P)
    sess.agent_launched_child(Q, C, 101.0)
    assert rec["ids"] == [P]


def test_a_link_to_an_agent_the_answer_does_not_list_leaves_the_row_in_place():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"), (C, "ship-tester"))
    rec = _rec(sess, P, C, parents={C: P})
    sess.agent_launched_child(Q, C, 101.0)
    assert _item(rec, C)["parent"] == P


# ---- the authoritative link replacing a launcher match ---------------------------------

D = "ad2e3f4a5b6c7d8e9"


def _matched_late(sess, rec):
    """C starts while P's stale call of its type waits: it is matched to P
    and joins the answer late, under P."""
    sess.agent_call_launched("ship-tester", "helper work", 100.5, launcher=P)
    sess.bg_agent_started(C, "ship-tester", 101.0)
    assert (_item(rec, C)["parent"], _item(rec, C)["late"]) == (P, True)


def test_a_late_row_whose_link_is_replaced_by_an_unlisted_parent_leaves():
    """Q (not listed, its own chain reaches nothing listed) really started
    C: C is not one of the answer's agents, so it leaves the block."""
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"))
    rec = _rec(sess, P)
    _matched_late(sess, rec)
    sess.agent_launched_child(Q, C, 102.0)
    assert sess.agent_parents[C] == Q
    assert rec["ids"] == [P]
    assert [i["id"] for i in rec["items"]] == [P]


def test_a_left_late_row_takes_its_late_rows_and_its_end_with_it():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"), (D, "Explore"))
    rec = _rec(sess, P)
    _matched_late(sess, rec)
    sess.agent_launched_child(C, D, 101.5)
    assert _item(rec, D)["parent"] == C
    sess.bg_agent_stopped(D, 101.8)
    assert D in rec["ends"]
    sess.agent_launched_child(Q, C, 102.0)
    assert rec["ids"] == [P]
    assert rec["ends"] == {}


def test_a_sent_row_under_a_left_late_row_goes_to_the_top():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"), (G, "Explore"))
    rec = _rec(sess, P, G)
    _matched_late(sess, rec)
    sess.agent_launched_child(C, G, 101.5)
    assert _item(rec, G)["parent"] == C
    sess.agent_launched_child(Q, C, 102.0)
    assert rec["ids"] == [P, G]
    assert _item(rec, G)["parent"] == ""


def test_a_sent_row_whose_link_is_replaced_by_an_unlisted_parent_goes_to_the_top():
    sess = _state((P, "pipeline-runner"), (Q, "r2-runner"), (C, "ship-tester"))
    sess.agent_parents[C] = P
    rec = _rec(sess, P, C, parents={C: P})
    sess.agent_launched_child(Q, C, 102.0)
    assert rec["ids"] == [P, C]
    assert _item(rec, C)["parent"] == ""


def test_the_same_link_again_keeps_a_row_whose_chain_was_forgotten():
    """C -> X -> G put C under G; X's link went with a stop. X's own
    PostToolUse for C again replaces nothing: C stays under G."""
    sess = _state((G, "pipeline-runner"), (Q, "r2-runner"), (C, "ship-tester"))
    sess.agent_parents.update({C: Q, Q: G})
    rec = _rec(sess, G, C, parents={C: G})
    sess.agent_parents.pop(Q)
    sess.agent_launched_child(Q, C, 102.0)
    assert _item(rec, C)["parent"] == G
