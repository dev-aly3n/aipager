"""The block under an answer as a tree (agents tree, 2026-10-14): two
visual levels, each agent right after the one that started it, a deeper
one named after its parent, a waiting row, the row cap with every row's
chain shown, and the room and blocks the block may ever take, in every
language and both directions."""

from __future__ import annotations

import itertools

import pytest

from aipager import i18n
from aipager.bot import agents_line as al
from aipager.bot.agents_line import (
    BLOCKS_MAX,
    LABEL_MAX,
    ROWS_MAX,
    AgentRow,
    BlockView,
    render_block,
    reserve_bytes,
)
from aipager.bot.divider import DIVIDER_SEP
from aipager.i18n.locales import LOCALES
from tests import bidi_uba

A = AgentRow
SEP = "\n\n"
PROMISE = "Results from the agents will follow here."
RLM, LRM = "\u200f", "\u200e"
MARKS = (RLM, LRM, "\u061c")


def _lines(view: BlockView, tr=None) -> list[str]:
    return render_block(view, tr).split(SEP)


def _names(view: BlockView) -> list[str]:
    """Each row's name, both levels, in order, "- " kept on list items."""
    out = []
    for part in _lines(view)[1:]:
        for line in part.split("\n"):
            if "**" in line:
                lead = "- " if line.startswith("- ") else ""
                out.append(lead + line.split("**")[1])
    return out


# ---- order and form ---------------------------------------------------------------------

def test_the_screenshot_case_reads_as_two_trees():
    view = BlockView(agents=(
        A("r2-runner", False, tool="Bash"),
        A("ship-tester", False, description="Write the tests", parent=3),
        A("ship-reviewer", False, description="Collect line numbers", parent=0),
        A("pipeline-runner", False, prose="Writing final.md"),
    ))
    assert render_block(view) == SEP.join([
        "⏳ 4 agents working",
        "○ 🤖 **r2-runner** · Running a command",
        "- ○ 🤖 **ship-reviewer** · Collect line numbers",
        "○ 🤖 **pipeline-runner** · Writing final.md",
        "- ○ 🤖 **ship-tester** · Write the tests",
        PROMISE])


def test_rows_go_in_tree_order_each_after_its_own_parent():
    view = BlockView(agents=(
        A("R1", False), A("R2", False), A("a", False, parent=0), A("b", False, parent=1),
        A("c", False, parent=2), A("d", False, parent=0),
    ))
    assert _names(view) == ["R1", "- a", "- a › c", "- d", "R2", "- b"]


def test_a_list_is_tight_and_never_indented():
    view = BlockView(agents=(A("R1", False), A("a", False, parent=0),
                             A("b", False, parent=1), A("c", False, parent=0)))
    out = render_block(view)
    assert "- ○ 🤖 **a**\n- ○ 🤖 **a › b**\n- ○ 🤖 **c**" in out
    assert all(not line.startswith(" ") for line in out.split("\n"))


def test_a_second_level_row_has_no_hint_a_deeper_one_names_its_parent():
    view = BlockView(agents=(A("R1", False), A("a", False, parent=0),
                             A("b", False, parent=1), A("c", False, parent=2)))
    assert _names(view) == ["R1", "- a", "- a › b", "- b › c"]


def test_a_hint_is_cut_and_escaped_like_a_name():
    long = "x" * (LABEL_MAX + 5)
    view = BlockView(agents=(A("R1", False), A("general_purpose", False, parent=0),
                             A(long, False, parent=1)))
    row = _lines(view)[2].split("\n")[1]
    assert row == f"- ○ 🤖 **general\\_purpose › {'x' * (LABEL_MAX - 1)}…**"


def test_a_waiting_row_says_so_never_its_last_tool():
    view = BlockView(agents=(A("r2-runner", False, waiting=True, tool="Bash",
                               prose="Checking"),))
    assert _lines(view)[1] == "⏸ 🤖 **r2-runner** · waiting for its own agents"


def test_done_wins_over_waiting():
    view = BlockView(agents=(A("r2-runner", True, 42, waiting=True), A("x", False)))
    assert _lines(view)[1] == "✓ 🤖 **r2-runner** · done (0:42)"


def test_waiting_counts_as_working_in_the_header():
    view = BlockView(agents=(A("P", False, waiting=True), A("C", True, 3, parent=0)))
    assert _lines(view)[0] == "⏳ 1 agent working"
    assert _lines(view)[-1] == PROMISE


@pytest.mark.parametrize("parent", [-5, 7, 0], ids=["negative", "out-of-range", "self"])
def test_an_invalid_parent_is_a_top(parent):
    view = BlockView(agents=(A("a", False, parent=parent), A("b", False)))
    assert _names(view) == ["a", "b"]


def test_agents_on_a_loop_are_tops_and_what_hangs_below_them_stays():
    view = BlockView(agents=(A("a", False, parent=1), A("b", False, parent=0),
                             A("c", False, parent=1)))
    assert _names(view) == ["a", "b", "- c"]


def test_a_three_loop_is_broken_everywhere():
    view = BlockView(agents=(A("a", False, parent=2), A("b", False, parent=0),
                             A("c", False, parent=1)))
    assert _names(view) == ["a", "b", "c"]


# ---- the row cap ------------------------------------------------------------------------

def test_two_runners_with_three_helpers_each_show_six_rows():
    agents = [A("R1", False)] + [A(f"h1{i}", False, parent=0) for i in range(3)]
    agents += [A("R2", False)] + [A(f"h2{i}", False, parent=4) for i in range(3)]
    view = BlockView(agents=tuple(agents))
    assert _names(view) == ["R1", "- h10", "- h11", "- h12", "R2", "- h20"]
    lines = _lines(view)
    assert lines[0] == "⏳ 8 agents working"
    assert lines[-2:] == ["+2 more", PROMISE]


def test_working_rows_win_over_done_ones():
    agents = [A(f"d{i}", True, 1) for i in range(4)] + [A(f"w{i}", False) for i in range(5)]
    view = BlockView(agents=tuple(agents))
    assert _names(view) == ["d0", "w0", "w1", "w2", "w3", "w4"]
    assert "+3 more" in _lines(view)


def test_a_row_never_shows_without_its_chain():
    # Five working tops fill five places; a working grandchild needs its
    # done parent and its top: three places, so it does not fit; a done
    # top fills the last one.
    agents = [A(f"w{i}", False) for i in range(5)]
    agents += [A("top", True, 1), A("mid", True, 1, parent=5), A("deep", False, parent=6)]
    view = BlockView(agents=tuple(agents))
    names = _names(view)
    assert "- mid › deep" not in names and "- mid" not in names
    assert len(names) == ROWS_MAX
    assert "+2 more" in _lines(view)


def test_a_working_child_brings_its_done_parent():
    agents = [A("top", True, 1), A("child", False, parent=0)]
    agents += [A(f"w{i}", False) for i in range(3)]
    view = BlockView(agents=tuple(agents))
    assert _names(view) == ["top", "- child", "w0", "w1", "w2"]


@pytest.mark.parametrize("n", range(1, 15))
def test_more_counts_exactly_the_rows_not_shown(n):
    agents = tuple(A(f"a{i}", i % 3 == 0, 1, parent=(i - 1 if i % 2 else -1))
                   for i in range(n))
    view = BlockView(agents=agents + (A("w", False),))
    shown = len(_names(view))
    hidden = len(view.agents) - shown
    assert shown <= ROWS_MAX
    assert (f"+{hidden} more" in _lines(view)) is (hidden > 0)


# ---- the room it may take ---------------------------------------------------------------

def _blocks(text: str) -> int:
    """Telegram's count, high: a paragraph or a divider 1, a list 1 and
    each item 2."""
    total = 0
    for part in text.split(SEP):
        lines = part.split("\n")
        if all(line.startswith("- ") for line in lines):
            total += 1 + 2 * len(lines)
        else:
            total += 1
    return total


def _shapes():
    """Every forest of ROWS_MAX rows (each row's parent an earlier row)."""
    for parents in itertools.product(*[range(-1, i) for i in range(ROWS_MAX)]):
        yield parents


def test_no_tree_of_shown_rows_takes_more_than_blocks_max():
    most = 0
    for parents in _shapes():
        agents = tuple(A(f"a{i}", False, parent=p) for i, p in enumerate(parents))
        view = BlockView(agents=agents + (A("hidden", False),), shells_running=1)
        most = max(most, _blocks(DIVIDER_SEP.lstrip("\n") + render_block(view)))
    assert most <= BLOCKS_MAX
    assert BLOCKS_MAX == 4 + 2 * ROWS_MAX == 16


_W = "\U0001f600"


def _worst_views():
    """Views as wide as the block gets: every row the widest name and the
    widest activity, deeper rows with their hint, a hidden count and every
    header part."""
    name = _W * (LABEL_MAX + 3)
    activities = [dict(prose=_W * 80), dict(description=_W * 80),
                  dict(tool="WebSearch", tool_arg=_W * 60), dict(tool=_W * 40),
                  dict(waiting=True)]
    for kw in activities:
        chain = [A(name, False, **kw)] + [A(name, False, parent=max(i - 1, 0), **kw)
                                          for i in range(1, ROWS_MAX)]
        star = [A(name, False, **kw), A(name, False, parent=0, **kw)] + [
            A(name, False, parent=1, **kw) for _ in range(ROWS_MAX - 2)]
        for shown in (chain, star):
            hidden = [A(name, False) for _ in range(114)]
            yield BlockView(agents=tuple(shown + hidden), shells_running=999)
    done = [A(name, True, 999 * 3600.0)] + [
        A(name, True, 999 * 3600.0, parent=max(i - 1, 0)) for i in range(1, ROWS_MAX)]
    yield BlockView(agents=tuple(done) + (A("w", False),),
                    shells_finished=999, shells_finished_seconds=999 * 3600.0,
                    shells_stopped=999, shells_stopped_seconds=999 * 3600.0)


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_the_reserve_covers_the_widest_block_in_both_directions(code):
    for rtl in (False, True):
        tr = i18n.get(code).directed(rtl)
        reserve = reserve_bytes(tr)
        for view in _worst_views():
            size = len((DIVIDER_SEP + render_block(view, tr)).encode("utf-8"))
            assert size <= reserve, (code, rtl, size, reserve)


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_the_reserve_is_the_divider_and_the_widest_lines(code):
    """Each part counted once: the divider, the header, six rows each with
    the most that can come before it (a blank line and a list marker),
    "+N more" and the promise. A real tree hints at most four of its six
    rows, so the widest render stays some way under this (the test above);
    the parts are pinned here so none is dropped from the count."""
    tr = i18n.get(code)
    more = max(len(str(tr.n(al.MORE, n, kind="md")).encode()) for n in al._WORST_COUNTS)
    expected = (len(DIVIDER_SEP) + al._worst_header(tr)
                + ROWS_MAX * (len("\n\n- ") + al._worst_rows(tr))
                + len(SEP) + more + len(SEP) + len(str(tr.md(al.PROMISE)).encode()))
    assert reserve_bytes(tr) == expected


# ---- right to left ----------------------------------------------------------------------

def _tree_view():
    return BlockView(agents=(
        A("pipeline-runner", False, prose="Writing final.md"),
        A("ship-tester", False, description="Write the tests", parent=0),
        A("ship-reviewer", False, tool="Read", tool_arg="a.py", parent=1),
        A("r2-runner", False, waiting=True),
        A("Explore", True, 42, parent=3),
    ), shells_running=1)


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("rtl", [True, False], ids=["rtl", "drawn-ltr"])
def test_a_list_marker_comes_before_the_lines_start_mark(code, rtl):
    tr = i18n.get(code).directed(rtl)
    first = str(tr.md(al.PROMISE))[0]
    assert first in MARKS
    items = [line for line in render_block(_tree_view(), tr).split("\n")
             if line.startswith("- ")]
    assert len(items) == 3
    assert all(line[2] == first for line in items), items


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_no_two_marks_touch_and_every_line_sits_right(code):
    out = render_block(_tree_view(), i18n.get(code))
    for a, b in zip(out, out[1:]):
        assert not (a in MARKS and b in MARKS), ascii(out)
    for line in out.split("\n"):
        if line and bidi_uba.has_rtl_letter(bidi_uba.shown_text(line)):
            assert bidi_uba.sits_right_on_iphone(line), ascii(line)


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_the_divider_above_the_block_carries_no_mark(code):
    block = render_block(_tree_view(), i18n.get(code))
    text = "answer" + DIVIDER_SEP + block
    assert "\n---\n" in text
    assert not any(m + "---" in text or "---" + m in text for m in MARKS)
