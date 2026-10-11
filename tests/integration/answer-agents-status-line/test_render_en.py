"""The block's English render, against the spec's own examples
(design.md success criteria 1, 3, 4).

Goldens are the spec's five states and the shell-only states, written
the way entrypoints.md says they come out: lines separated by a blank
line (rich Markdown collapses a single newline) and the agent type in
bold. Nothing here reads the renderer's internals.
"""

from __future__ import annotations

import pytest

from aipager.bot.agents_line import (
    ACTIVITY_MAX,
    ROWS_MAX,
    AgentRow,
    BlockView,
    first_line,
    render_block,
)

SEP = "\n\n"
PROMISE = "Results from the agents will follow here."


def _lines(*parts: str) -> str:
    return SEP.join(parts)


# ---- criterion 1: the spec's five states, golden --------------------------------------

def test_state_working_two_agents_and_a_shell():
    view = BlockView(
        agents=(AgentRow("pipeline-runner", False,
                         prose="Writing final.md for the pipeline"),
                AgentRow("Explore", False, tool="Read", tool_arg="state.py")),
        shells_running=1)
    assert render_block(view) == _lines(
        "⏳ 2 agents working · 🖥 1 shell running",
        "○ **pipeline-runner** · Writing final.md for the pipeline",
        "○ **Explore** · Reading state.py",
        PROMISE)


def test_state_one_agent_done_the_other_still_going():
    view = BlockView(
        agents=(AgentRow("Explore", True, 42.0),
                AgentRow("pipeline-runner", False,
                         prose="Running the full suite")),
        shells_running=1)
    assert render_block(view) == _lines(
        "⏳ 1 agent working · 🖥 1 shell running",
        "✓ **Explore** · done (0:42)",
        "○ **pipeline-runner** · Running the full suite",
        PROMISE)


def test_state_agents_done_a_shell_still_up():
    view = BlockView(
        agents=(AgentRow("Explore", True, 42.0),
                AgentRow("pipeline-runner", True, 300.0)),
        agents_done_seconds=372.0, shells_running=1)
    assert render_block(view) == "✅ 2 agents done (6:12) · 🖥 1 shell running"


def test_state_agents_done_and_the_shell_finished():
    view = BlockView(
        agents=(AgentRow("Explore", True, 42.0),
                AgentRow("pipeline-runner", True, 300.0)),
        agents_done_seconds=372.0, shells_finished=1,
        shells_finished_seconds=7985.0)
    assert render_block(view) == \
        "✅ 2 agents done (6:12) · 1 shell finished (2:13:05)"


def test_state_shell_only_running_has_no_promise():
    assert render_block(BlockView(shells_running=1)) == "🖥 1 shell running"


def test_state_shell_only_finished():
    view = BlockView(shells_finished=1, shells_finished_seconds=7985.0)
    assert render_block(view) == "1 shell finished (2:13:05)"


def test_state_shell_only_stopped():
    view = BlockView(shells_stopped=1, shells_stopped_seconds=65.0)
    assert render_block(view) == "1 shell stopped (1:05)"


def test_finished_and_stopped_shells_are_both_counted():
    view = BlockView(shells_finished=2, shells_finished_seconds=65.0,
                     shells_stopped=1, shells_stopped_seconds=5.0)
    assert render_block(view) == \
        "2 shells finished (1:05) · 1 shell stopped (0:05)"


def test_nothing_to_show_is_empty():
    assert render_block(BlockView()) == ""


def test_one_agent_singular_with_no_activity_is_a_bare_row():
    view = BlockView(agents=(AgentRow("general-purpose", False),))
    assert render_block(view) == _lines(
        "⏳ 1 agent working", "○ **general-purpose**", PROMISE)


def test_one_agent_done_alone_is_singular():
    view = BlockView(agents=(AgentRow("Explore", True, 9.0),),
                     agents_done_seconds=61.0)
    assert render_block(view) == "✅ 1 agent done (1:01)"


def test_none_given_means_english():
    view = BlockView(shells_running=3)
    assert render_block(view, None) == "🖥 3 shells running"


# ---- criterion 4: rows, "+N more", promise, shells --------------------------------------

def _working(n: int) -> BlockView:
    return BlockView(agents=tuple(AgentRow(f"type{i}", False) for i in range(n)))


@pytest.mark.parametrize("n", [3, 4], ids=["three", "four"])
def test_up_to_four_rows_have_no_more_line(n):
    assert "more" not in render_block(_working(n))


@pytest.mark.parametrize("n", [3, 4], ids=["three", "four"])
def test_up_to_four_rows_are_all_shown(n):
    out = render_block(_working(n))
    assert [f"○ **type{i}**" in out for i in range(n)] == [True] * n


def test_rows_max_is_four():
    assert ROWS_MAX == 4


def test_five_agents_show_four_rows():
    out = render_block(_working(5)).split(SEP)
    assert sum(1 for x in out if x.startswith("○ ")) == 4


def test_five_agents_show_plus_one_more():
    assert "+1 more" in render_block(_working(5)).split(SEP)


def test_seven_agents_show_plus_three_more():
    assert "+3 more" in render_block(_working(7)).split(SEP)


def test_the_fifth_agent_is_the_one_folded():
    assert "type4" not in render_block(_working(5))


def test_the_more_line_comes_before_the_promise():
    out = render_block(_working(6)).split(SEP)
    assert out[-2:] == ["+2 more", PROMISE]


def test_the_header_counts_every_agent_not_just_the_rows():
    assert render_block(_working(6)).split(SEP)[0] == "⏳ 6 agents working"


def test_no_promise_once_every_agent_is_done():
    view = BlockView(agents=(AgentRow("a", True, 1.0), AgentRow("b", True, 2.0)),
                     agents_done_seconds=10.0, shells_running=2)
    assert PROMISE not in render_block(view)


def test_promise_while_one_agent_of_many_works():
    view = BlockView(agents=(AgentRow("a", True, 1.0), AgentRow("b", False)))
    assert render_block(view).endswith(PROMISE)


def test_shells_alone_never_carry_the_promise():
    view = BlockView(shells_running=2, shells_finished=1)
    assert PROMISE not in render_block(view)


def test_header_comes_first():
    view = BlockView(agents=(AgentRow("x", False, prose="hello"),), shells_running=1)
    assert render_block(view).split(SEP)[0].startswith("⏳ 1 agent working")


def test_running_shells_win_over_finished_in_the_header():
    view = BlockView(shells_running=1, shells_finished=2,
                     shells_finished_seconds=30.0)
    assert render_block(view) == "🖥 1 shell running"


# ---- criterion 3: activity precedence ------------------------------------------------

def _row(**kw) -> str:
    view = BlockView(agents=(AgentRow("t", False, **kw),))
    return render_block(view).split(SEP)[1]


def test_prose_wins_over_tool_and_description():
    assert _row(prose="Thinking aloud", tool="Read", tool_arg="a.py",
                description="Audit the code") == "○ **t** · Thinking aloud"


def test_tool_wins_over_description():
    assert _row(tool="Write", tool_arg="final.md",
                description="Audit the code") == "○ **t** · Writing final.md"


def test_description_when_no_prose_and_no_tool():
    assert _row(description="Audit the code") == "○ **t** · Audit the code"


def test_bare_type_when_nothing_is_known():
    assert _row() == "○ **t**"


def test_whitespace_only_prose_falls_back_to_the_tool():
    assert _row(prose="  \n \n", tool="Read", tool_arg="a.py") == \
        "○ **t** · Reading a.py"


@pytest.mark.parametrize("tool, arg, phrase", [
    ("Read", "state.py", "Reading state.py"),
    ("Write", "final.md", "Writing final.md"),
    ("Edit", "app.py", "Editing app.py"),
    ("MultiEdit", "app.py", "Editing app.py"),
    ("NotebookEdit", "nb.ipynb", "Editing nb.ipynb"),
    ("Bash", "", "Running a command"),
    ("Grep", "TODO", "Searching for TODO"),
    ("Glob", "src/x.py", "Searching for src/x.py"),
    ("WebSearch", "dtach pty", "Searching the web for dtach pty"),
    ("WebFetch", "example.org", "Fetching example.org"),
    ("Agent", "", "Starting an agent"),
    ("Task", "", "Starting an agent"),
    ("TodoWrite", "", "Using TodoWrite"),
], ids=["read", "write", "edit", "multiedit", "notebookedit", "bash", "grep",
        "glob", "websearch", "webfetch", "agent", "task", "other"])
def test_tool_phrase(tool, arg, phrase):
    assert _row(tool=tool, tool_arg=arg) == f"○ **t** · {phrase}"


def test_bash_shows_its_description_or_running_a_command():
    # tool_arg is what the hook recorded: the call's description, never
    # its command (tests/integration/.../test_hook_capture.py).
    assert _row(tool="Bash", tool_arg="Verify the build") == "○ **t** · Verify the build"
    assert _row(tool="Bash", tool_arg="") == "○ **t** · Running a command"


def test_a_file_tool_with_no_file_names_the_tool():
    assert _row(tool="Read", tool_arg="") == "○ **t** · Using Read"


# ---- criterion 3: first line, 60 code points, escaping ----------------------------------

def test_prose_shows_only_its_first_line():
    assert _row(prose="Line one here\nLine two there") == "○ **t** · Line one here"


def test_prose_stops_at_the_first_sentence():
    assert "Then" not in _row(prose="Reading the config. Then fixing the bug.")


def test_a_dotted_file_name_is_not_a_sentence_end():
    assert _row(prose="Writing final.md for the pipeline") == \
        "○ **t** · Writing final.md for the pipeline"


def test_first_line_skips_blank_lines():
    assert first_line("\n\n   \nChecking imports") == "Checking imports"


def test_first_line_drops_a_heading_marker():
    assert first_line("## Plan of attack") == "Plan of attack"


def test_first_line_drops_a_bullet_marker():
    assert first_line("- Running the suite") == "Running the suite"


def test_first_line_drops_a_quote_marker():
    assert first_line("> Quoted status") == "Quoted status"


def test_first_line_never_shows_a_code_fence_marker():
    assert "```" not in first_line("```python\nprint(1)\n```")


def test_first_line_finds_prose_after_a_fenced_block():
    assert first_line("```\nx = 1\n```\nChecking the result") == \
        "Checking the result"


def test_first_line_collapses_whitespace():
    assert first_line("Lots    of\tspace") == "Lots of space"


def test_activity_max_is_sixty():
    assert ACTIVITY_MAX == 60


def test_exactly_sixty_code_points_stay_whole():
    text = ("abcd " * 12)[:59] + "z"
    assert len(text) == 60
    assert first_line(text) == text


def test_sixty_one_code_points_are_cut_with_an_ellipsis():
    text = "word " * 12 + "x"
    assert first_line(text).endswith("…")


def test_the_cut_is_at_most_sixty_code_points():
    assert len(first_line("word " * 40)) <= 60


def test_the_cut_falls_on_a_word_boundary():
    text = "alphabet " * 12
    cut = first_line(text)
    kept = cut[:-1].rstrip()
    assert kept.split(" ")[-1] == "alphabet"


def test_a_spaceless_text_is_cut_hard():
    text = "這是一個沒有空格的很長的句子" * 8
    assert len(first_line(text)) == 60


def test_the_trimmed_prose_reaches_the_row():
    row = _row(prose="word " * 40)
    assert row.endswith("…")


@pytest.mark.parametrize("raw, escaped", [
    ("*bold*", "\\*bold\\*"),
    ("_under_", "\\_under\\_"),
    ("`code`", "\\`code\\`"),
    ("[link]", "\\[link\\]"),
], ids=["star", "underscore", "backtick", "brackets"])
def test_prose_markdown_is_escaped(raw, escaped):
    assert escaped in _row(prose=f"Now {raw} here")


def test_an_agent_type_is_escaped_inside_the_bold():
    out = render_block(BlockView(agents=(AgentRow("general_purpose", False),)))
    assert "○ **general\\_purpose**" in out


def test_a_tool_argument_is_escaped():
    assert _row(tool="Read", tool_arg="my_file.py") == "○ **t** · Reading my\\_file.py"


def test_a_description_is_escaped():
    assert _row(description="fix *all* the [things]") == \
        "○ **t** · fix \\*all\\* the \\[things\\]"


def test_an_unknown_tool_name_is_escaped():
    assert _row(tool="mcp__srv__go") == "○ **t** · Using mcp\\_\\_srv\\_\\_go"


def test_a_done_row_drops_its_activity():
    view = BlockView(agents=(AgentRow("t", True, 5.0, prose="busy"),
                             AgentRow("u", False)))
    assert "busy" not in render_block(view)
