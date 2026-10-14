"""The busy card's robot rows name a helper's top agent (design.md
success criterion 9; entrypoints.md "Busy card helper rows").

Rows are read from ``sess.tool_history`` and from the rich card markdown
``build_stream_card_ex`` renders (the card renderer earlier black-box
suites drive)."""

from __future__ import annotations

import re

import pytest

from aipager.bot.animation import build_stream_card_ex

R1 = "a505c081ca3926ed5"
T = "ac1d54c85d78f59ea"
G = "ab77e10f00d1c2b3a"
R1_TYPE, T_TYPE, G_TYPE = "pipeline-runner", "ship-tester", "Explore"
ROBOT = "\U0001f916"
SEP = " \u203a "


def _robots(sess):
    return [s for s, _d in sess.tool_history if s.startswith(ROBOT)]


def _row_named(sess, tail):
    rows = [s for s in _robots(sess)
            if s[len(ROBOT) + 1:].split(" \u00b7 ")[0].endswith(tail)]
    assert len(rows) == 1, _robots(sess)
    return rows[0]


@pytest.fixture
def tree(rig):
    rig.prompt()
    rig.agent_call(R1, R1_TYPE)
    rig.launch(T, T_TYPE, "helper work", by=R1)
    return rig


# ---- stored rows -------------------------------------------------------------------------

def test_the_sessions_own_agent_row_is_unchanged(tree):
    assert _row_named(tree.sess, R1_TYPE) == f"{ROBOT} {R1_TYPE}"


def test_a_helper_row_names_its_top(tree):
    assert _row_named(tree.sess, T_TYPE) == f"{ROBOT} {R1_TYPE}{SEP}{T_TYPE}"


def test_the_helper_card_label_is_kept(tree):
    assert tree.sess.active_subagents[T]["card_label"] == f"{R1_TYPE}{SEP}{T_TYPE}"


def test_the_top_card_label_is_its_type(tree):
    assert tree.sess.active_subagents[R1].get("card_label") == R1_TYPE


def test_a_depth_three_row_names_the_top_not_its_parent(tree):
    tree.launch(G, G_TYPE, "look around", by=T)
    assert _row_named(tree.sess, G_TYPE) == f"{ROBOT} {R1_TYPE}{SEP}{G_TYPE}"


def test_rows_stay_in_start_order(tree):
    tree.launch(G, G_TYPE, "look around", by=T)
    assert _robots(tree.sess) == [f"{ROBOT} {R1_TYPE}", f"{ROBOT} {R1_TYPE}{SEP}{T_TYPE}",
                                  f"{ROBOT} {R1_TYPE}{SEP}{G_TYPE}"]


def test_a_resumed_helper_adds_no_second_row(tree):
    tree.start(T, T_TYPE)
    assert len([s for s in _robots(tree.sess) if s.endswith(T_TYPE)]) == 1


# ---- settled rows --------------------------------------------------------------------------

def test_a_settled_helper_row_keeps_the_top(tree):
    tree.tool(T, "Read", "/a.py")
    tree.tool(T, "Read", "/b.py")
    tree.sleep(15)
    tree.stop(T, T_TYPE)
    row = _row_named(tree.sess, T_TYPE)
    assert re.fullmatch(
        rf"{ROBOT} {R1_TYPE}{SEP}{T_TYPE} \u00b7 2 tool calls \u00b7 \d+:\d\d", row), row


def test_a_settled_own_agent_row_is_unchanged(tree):
    tree.tool(R1, "Read", "/a.py")
    tree.sleep(10)
    tree.stop(T, T_TYPE)
    tree.stop(R1, R1_TYPE)
    row = _row_named(tree.sess, R1_TYPE)
    assert re.fullmatch(rf"{ROBOT} {R1_TYPE} \u00b7 2 tool calls \u00b7 0:10", row), row


# ---- the card markdown -----------------------------------------------------------------------

def test_the_live_card_row_names_the_top(tree):
    tree.tool(T, "Read", "/a.py", post=False)
    tree.sess.status = tree.sess.status.__class__.BUSY
    card, _ = build_stream_card_ex(tree.sess, "Working")
    assert re.search(rf"`{ROBOT} {R1_TYPE}{SEP}{T_TYPE} \u00b7 Read: /a\.py \u00b7 \d+:\d\d`",
                     card), card


def test_the_final_card_row_names_the_top(tree):
    tree.tool(T, "Read", "/a.py")
    tree.stop(T, T_TYPE)
    card, _ = build_stream_card_ex(tree.sess, "Done", final=True)
    assert f"{ROBOT} {R1_TYPE}{SEP}{T_TYPE} \u00b7 1 tool call" in card


# ---- an unknown launcher ---------------------------------------------------------------------

@pytest.fixture
def unknown(rig):
    rig.prompt()
    rig.agent_call(R1, R1_TYPE)
    rig.call(T_TYPE, "main work")
    rig.call(T_TYPE, "helper work", by=R1)
    rig.start(T, T_TYPE)
    return rig


def test_an_unknown_launcher_gives_a_plain_row(unknown):
    assert _row_named(unknown.sess, T_TYPE) == f"{ROBOT} {T_TYPE}"


def test_a_later_link_does_not_change_the_card_row(unknown):
    unknown.link(T, by=R1)
    assert _row_named(unknown.sess, T_TYPE) == f"{ROBOT} {T_TYPE}"


def test_a_helper_without_any_call_gives_a_plain_row(rig):
    rig.prompt()
    rig.agent_call(R1, R1_TYPE)
    rig.start(T, T_TYPE)
    assert _row_named(rig.sess, T_TYPE) == f"{ROBOT} {T_TYPE}"


# ---- agent_card_label ---------------------------------------------------------------------------

def test_card_label_of_a_top_is_its_type(tree):
    assert tree.sess.agent_card_label(R1, R1_TYPE) == R1_TYPE


def test_card_label_of_a_helper(tree):
    assert tree.sess.agent_card_label(T, T_TYPE) == f"{R1_TYPE}{SEP}{T_TYPE}"


def test_card_label_of_an_unknown_agent_is_its_type(tree):
    assert tree.sess.agent_card_label("affffffffffffffff", "x-type") == "x-type"
