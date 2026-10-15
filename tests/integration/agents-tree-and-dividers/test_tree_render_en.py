"""The block's tree in English, through ``render_block`` (design.md success
criteria 2, 3, 4; entrypoints.md "Rendered shapes")."""

from __future__ import annotations

import pytest

from aipager.bot.agents_line import AgentRow, BlockView, render_block

PROMISE = "Results from the agents will follow here."
R = "\U0001f916"


def _row(t, *, parent=-1, prose="", done=False, seconds=None, waiting=False, **kw):
    return AgentRow(t, done, seconds, prose=prose, waiting=waiting, parent=parent, **kw)


def _example():
    """entrypoints.md's tree, in START order (not tree order): R1, R2,
    R1's two helpers, R2's helper, then the depth-3 agent under D."""
    return BlockView(agents=(
        _row("pipeline-runner", prose="Writing final.md for the pipeline"),     # 0
        _row("r2-runner", waiting=True, prose="Running a command"),            # 1
        _row("ship-tester", parent=0, prose="Write and run criteria tests"),   # 2
        _row("ship-developer", parent=0, prose="Editing state.py"),            # 3
        _row("Explore", parent=1, prose="Searching for the parents"),          # 4
        _row("ship-reviewer", parent=3,
             prose="Collect line numbers for review issues"),                  # 5
    ))


GOLDEN = (
    "\u23f3 6 agents working\n\n"
    f"\u25cb {R} **pipeline-runner** \u00b7 Writing final.md for the pipeline\n\n"
    f"- {R} **ship-tester** \u00b7 Write and run criteria tests\n"
    f"- {R} **ship-developer** \u00b7 Editing state.py\n"
    f"- {R} **ship-developer \u203a ship-reviewer** \u00b7 "
    "Collect line numbers for review issues\n\n"
    f"\u2016 {R} **r2-runner** \u00b7 waiting for its own agents\n\n"
    f"- {R} **Explore** \u00b7 Searching for the parents\n\n"
    f"{PROMISE}"
)


def test_the_example_tree_renders_exactly():
    assert render_block(_example()) == GOLDEN


def test_the_example_tree_levels_and_order(shapes):
    assert shapes.tree(render_block(_example())) == [
        (1, "pipeline-runner"), (2, "ship-tester"), (2, "ship-developer"),
        (2, "ship-developer \u203a ship-reviewer"), (1, "r2-runner"), (2, "Explore")]


# ---- criterion 2: robot, paragraphs, tight list, no indent ---------------------------

def test_every_agent_row_has_the_robot(shapes):
    block = render_block(_example())
    assert all(R in x for x in block.split("\n") if "**" in x)


def test_six_robots_for_six_agents():
    assert render_block(_example()).count(R) == 6


def test_no_line_starts_with_a_space():
    assert [x for x in render_block(_example()).split("\n") if x.startswith(" ")] == []


def test_top_agents_are_paragraphs(shapes):
    paras = shapes.paragraphs(render_block(_example()))
    tops = [p for p in paras if p.startswith(("\u25cb", "\u2016", "\u2713"))]
    assert len(tops) == 2 and all("\n" not in p for p in tops)


def test_each_helper_list_is_one_tight_paragraph(shapes):
    paras = shapes.paragraphs(render_block(_example()))
    lists = [p for p in paras if p.startswith("- ")]
    assert [len(p.split("\n")) for p in lists] == [3, 1]


def test_every_line_of_a_list_paragraph_is_an_item(shapes):
    paras = shapes.paragraphs(render_block(_example()))
    lists = [p for p in paras if p.startswith("- ")]
    assert all(x.startswith("- ") for p in lists for x in p.split("\n"))


def test_a_flat_block_has_no_list():
    view = BlockView(agents=(_row("pipeline-runner"), _row("Explore")))
    assert "\n- " not in render_block(view)


def test_a_single_helper_is_a_one_item_list():
    view = BlockView(agents=(_row("pipeline-runner"), _row("ship-tester", parent=0)))
    assert render_block(view) == (
        f"\u23f3 2 agents working\n\n\u25cb {R} **pipeline-runner**\n\n"
        f"- {R} **ship-tester**\n\n{PROMISE}")


# ---- criterion 3: depth 3+ at level 2, pre-order, hint ---------------------------------

def _chain(depth: int):
    """pipeline-runner -> a1 -> a2 -> ... (each started by the one before)."""
    rows = [_row("pipeline-runner")]
    for i in range(1, depth):
        rows.append(_row(f"a{i}", parent=i - 1))
    return BlockView(agents=tuple(rows))


def test_a_depth_two_agent_has_no_hint(shapes):
    assert shapes.tree(render_block(_chain(2)))[1] == (2, "a1")


def test_a_depth_three_agent_names_its_direct_parent(shapes):
    assert shapes.tree(render_block(_chain(3)))[2] == (2, "a1 \u203a a2")


def test_a_depth_five_agent_names_its_direct_parent_not_the_top(shapes):
    assert shapes.tree(render_block(_chain(5)))[4] == (2, "a3 \u203a a4")


def test_a_deep_chain_never_goes_past_level_two(shapes):
    assert {lvl for lvl, _ in shapes.tree(render_block(_chain(6)))} == {1, 2}


def test_a_deep_chain_is_in_pre_order(shapes):
    names = [n for _, n in shapes.tree(render_block(_chain(5)))]
    assert names == ["pipeline-runner", "a1", "a1 \u203a a2", "a2 \u203a a3", "a3 \u203a a4"]


def test_a_grandchild_follows_its_own_parent_before_the_parents_sibling(shapes):
    view = BlockView(agents=(
        _row("pipeline-runner"),
        _row("ship-developer", parent=0),
        _row("ship-tester", parent=0),
        _row("ship-reviewer", parent=1)))
    assert [n for _, n in shapes.tree(render_block(view))] == [
        "pipeline-runner", "ship-developer", "ship-developer \u203a ship-reviewer",
        "ship-tester"]


def test_siblings_keep_start_order(shapes):
    view = BlockView(agents=(
        _row("pipeline-runner"), _row("b-helper", parent=0), _row("a-helper", parent=0)))
    assert [n for _, n in shapes.tree(render_block(view))][1:] == ["b-helper", "a-helper"]


def test_a_child_listed_before_its_parent_still_nests(shapes):
    view = BlockView(agents=(_row("ship-tester", parent=1), _row("pipeline-runner")))
    assert shapes.tree(render_block(view)) == [(1, "pipeline-runner"), (2, "ship-tester")]


def test_tops_keep_start_order(shapes):
    view = BlockView(agents=(_row("zeta"), _row("alpha"), _row("child", parent=0)))
    assert shapes.tree(render_block(view)) == [(1, "zeta"), (2, "child"), (1, "alpha")]


# ---- invalid parents are tops ----------------------------------------------------------

@pytest.mark.parametrize("parent", [2, 99, -2, -5], ids=["just-out", "far-out", "minus-two", "minus-five"])
def test_an_out_of_range_parent_is_a_top(shapes, parent):
    view = BlockView(agents=(_row("pipeline-runner"), _row("ship-tester", parent=parent)))
    assert shapes.tree(render_block(view)) == [(1, "pipeline-runner"), (1, "ship-tester")]


def test_a_self_parent_is_a_top(shapes):
    view = BlockView(agents=(_row("pipeline-runner"), _row("ship-tester", parent=1)))
    assert shapes.tree(render_block(view)) == [(1, "pipeline-runner"), (1, "ship-tester")]


def test_a_two_agent_cycle_still_shows_both_rows():
    view = BlockView(agents=(_row("pipeline-runner", parent=1), _row("ship-tester", parent=0)))
    assert render_block(view).count(R) == 2


def test_a_two_agent_cycle_puts_nothing_in_a_list():
    view = BlockView(agents=(_row("pipeline-runner", parent=1), _row("ship-tester", parent=0)))
    assert "\n- " not in render_block(view)


def test_a_cycle_below_a_valid_top_keeps_every_row():
    view = BlockView(agents=(_row("top"), _row("x", parent=2), _row("y", parent=1)))
    assert render_block(view).count(R) == 3


# ---- criterion 4: the row states ---------------------------------------------------------

def test_the_waiting_row_exact():
    view = BlockView(agents=(_row("r2-runner", waiting=True),))
    assert f"\u2016 {R} **r2-runner** \u00b7 waiting for its own agents" in render_block(view)


@pytest.mark.parametrize("kw", [
    {"prose": "Running a command"},
    {"tool": "Bash", "tool_arg": "pytest -q"},
    {"description": "Collect line numbers"},
], ids=["prose", "tool", "description"])
def test_a_waiting_row_never_shows_its_last_activity(kw):
    view = BlockView(agents=(AgentRow("r2-runner", False, waiting=True, **kw),))
    row = render_block(view).split("\n\n")[1]
    assert row == f"\u2016 {R} **r2-runner** \u00b7 waiting for its own agents"


def test_a_waiting_agent_counts_as_working_in_the_header():
    view = BlockView(agents=(_row("r2-runner", waiting=True), _row("Explore", parent=0)))
    assert render_block(view).startswith("\u23f3 2 agents working\n\n")


def test_a_done_row_keeps_its_clock():
    view = BlockView(agents=(_row("Explore", done=True, seconds=42.0), _row("pipeline-runner")))
    assert f"\u2713 {R} **Explore** \u00b7 done (0:42)" in render_block(view)


def test_a_done_helper_keeps_its_clock_in_the_list():
    view = BlockView(agents=(_row("pipeline-runner"),
                             _row("ship-tester", parent=0, done=True, seconds=75.0)))
    assert f"\n- \u2713 {R} **ship-tester** \u00b7 done (1:15)" in render_block(view)


def test_done_wins_over_waiting():
    view = BlockView(agents=(_row("r2-runner", done=True, seconds=5.0, waiting=True),
                             _row("pipeline-runner")))
    assert f"\u2713 {R} **r2-runner** \u00b7 done (0:05)" in render_block(view)


def test_a_bare_working_row():
    view = BlockView(agents=(_row("Explore"),))
    assert render_block(view).split("\n\n")[1] == f"\u25cb {R} **Explore**"


def test_a_working_row_with_prose():
    view = BlockView(agents=(_row("pipeline-runner", prose="Writing final.md for the pipeline"),))
    assert render_block(view).split("\n\n")[1] == \
        f"\u25cb {R} **pipeline-runner** \u00b7 Writing final.md for the pipeline"


def test_a_waiting_depth_three_row_keeps_its_hint(shapes):
    view = BlockView(agents=(_row("pipeline-runner"), _row("ship-developer", parent=0),
                             _row("ship-reviewer", parent=1, waiting=True)))
    assert shapes.agent_lines(render_block(view))[2] == \
        f"- \u2016 {R} **ship-developer \u203a ship-reviewer** \u00b7 waiting for its own agents"


# ---- all done: the header alone ----------------------------------------------------------

def test_a_tree_all_done_is_the_header_alone():
    view = BlockView(agents=(_row("pipeline-runner", done=True, seconds=300.0),
                             _row("ship-tester", parent=0, done=True, seconds=42.0),
                             _row("Explore", parent=1, done=True, seconds=30.0)),
                     agents_done_seconds=372.0)
    assert render_block(view) == "\u2705 3 agents done (6:12)"


def test_a_tree_all_done_with_a_shell_running():
    view = BlockView(agents=(_row("pipeline-runner", done=True, seconds=300.0),
                             _row("ship-tester", parent=0, done=True, seconds=42.0),
                             _row("Explore", parent=1, done=True, seconds=30.0)),
                     agents_done_seconds=372.0, shells_running=1)
    assert render_block(view) == "\u2705 3 agents done (6:12) \u00b7 \U0001f5a5 1 shell running"


def test_one_working_helper_keeps_every_row():
    view = BlockView(agents=(_row("pipeline-runner", done=True, seconds=300.0),
                             _row("ship-tester", parent=0)))
    assert render_block(view).count(R) == 2


def test_nothing_is_an_empty_block():
    assert render_block(BlockView()) == ""


def test_the_block_has_no_leading_separator():
    assert not render_block(_example()).startswith(("\n", "---"))
