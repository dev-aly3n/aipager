"""Row cap, "+N more", the byte reserve and the 16-block bound
(design.md success criteria 5 and 14; entrypoints.md "Cap" and
"Budgets")."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot.agents_line import (
    BLOCKS_MAX,
    ROWS_MAX,
    AgentRow,
    BlockView,
    render_block,
    reserve_bytes,
)
from aipager.bot.divider import DIVIDER_SEP

R = "\U0001f916"
_LOCALES = Path(i18n.__file__).parent / "locales"
CODES = ["en"] + sorted(p.stem for p in _LOCALES.glob("*.jsonl"))


def _w(t, parent=-1, **kw):
    return AgentRow(t, False, parent=parent, **kw)


def _d(t, parent=-1, seconds=10.0):
    return AgentRow(t, True, seconds, parent=parent)


def _more(block: str) -> int | None:
    m = re.search(r"\+(\d+) more", block)
    return int(m.group(1)) if m else None


def test_rows_max_is_six():
    assert ROWS_MAX == 6


def test_blocks_max_is_sixteen():
    assert BLOCKS_MAX == 16


def test_fifteen_languages():
    assert len(CODES) == 15


# ---- the spec's cap example -----------------------------------------------------------

def _eight():
    """R1 with 3 helpers and R2 with 3 helpers, all working, start order
    R1, R2, then R1's helpers, then R2's."""
    return BlockView(agents=(
        _w("pipeline-runner"), _w("r2-runner"),
        _w("h1", 0), _w("h2", 0), _w("h3", 0),
        _w("e1", 1), _w("e2", 1), _w("e3", 1)))


def test_the_cap_example_shows_r1_its_helpers_r2_and_its_first(shapes):
    assert shapes.tree(render_block(_eight())) == [
        (1, "pipeline-runner"), (2, "h1"), (2, "h2"), (2, "h3"),
        (1, "r2-runner"), (2, "e1")]


def test_the_cap_example_says_two_more():
    assert _more(render_block(_eight())) == 2


def test_the_cap_example_header_counts_all_eight():
    assert render_block(_eight()).startswith("\u23f3 8 agents working\n\n")


def test_the_more_note_follows_the_rows_and_precedes_the_promise():
    paras = render_block(_eight()).split("\n\n")
    assert _more(paras[-2]) == 2 and paras[-1] == "Results from the agents will follow here."


# ---- boundaries on the count -----------------------------------------------------------

@pytest.mark.parametrize("n", [1, 5, 6], ids=["one", "five", "six"])
def test_at_or_under_the_cap_no_more_note(n):
    view = BlockView(agents=tuple(_w(f"t{i}") for i in range(n)))
    assert _more(render_block(view)) is None


@pytest.mark.parametrize("n", [7, 8, 12, 100], ids=["seven", "eight", "twelve", "hundred"])
def test_over_the_cap_shows_exactly_six(n):
    view = BlockView(agents=tuple(_w(f"t{i}") for i in range(n)))
    assert render_block(view).count(R) == 6


@pytest.mark.parametrize("n", [7, 8, 12, 100], ids=["seven", "eight", "twelve", "hundred"])
def test_more_is_total_minus_shown_flat(n):
    view = BlockView(agents=tuple(_w(f"t{i}") for i in range(n)))
    assert _more(render_block(view)) == n - 6


@pytest.mark.parametrize("helpers", [6, 7, 20], ids=["six", "seven", "twenty"])
def test_more_is_total_minus_shown_under_one_top(helpers):
    view = BlockView(agents=(_w("pipeline-runner"),) + tuple(_w(f"h{i}", 0) for i in range(helpers)))
    assert _more(render_block(view)) == helpers + 1 - 6


@pytest.mark.parametrize("helpers", [6, 7, 20], ids=["six", "seven", "twenty"])
def test_one_top_with_many_helpers_shows_the_top_and_five(shapes, helpers):
    view = BlockView(agents=(_w("pipeline-runner"),) + tuple(_w(f"h{i}", 0) for i in range(helpers)))
    assert shapes.tree(render_block(view)) == [(1, "pipeline-runner")] + \
        [(2, f"h{i}") for i in range(5)]


# ---- not-done rows win -------------------------------------------------------------------

def test_a_done_top_gives_way_to_working_ones(shapes):
    view = BlockView(agents=(_d("old"),) + tuple(_w(f"t{i}") for i in range(6)))
    assert "old" not in [n for _, n in shapes.tree(render_block(view))]


def test_a_done_helper_gives_way_to_a_working_one(shapes):
    view = BlockView(agents=(_w("pipeline-runner"),) + tuple(_d(f"d{i}", 0) for i in range(5))
                     + (_w("late", 0),))
    assert "late" in [n for _, n in shapes.tree(render_block(view))]


def test_a_done_top_shows_when_its_working_helper_does(shapes):
    view = BlockView(agents=(_d("pipeline-runner"), _w("h1", 0))
                     + tuple(_w(f"t{i}") for i in range(6)))
    tree = shapes.tree(render_block(view))
    assert tree[:2] == [(1, "pipeline-runner"), (2, "h1")]


def test_a_shown_helper_always_has_its_top_shown(shapes):
    view = BlockView(agents=tuple(_w(f"t{i}") for i in range(5))
                     + (_d("pipeline-runner"), _w("h1", 5)))
    tree = shapes.tree(render_block(view))
    names = [n for _, n in tree]
    assert ("h1" not in names) or ("pipeline-runner" in names)


def test_a_helper_never_lands_under_the_wrong_top(shapes):
    view = BlockView(agents=tuple(_w(f"t{i}") for i in range(5))
                     + (_d("pipeline-runner"), _w("h1", 5)))
    tree = shapes.tree(render_block(view))
    names = [n for _, n in tree]
    if "h1" in names:
        i = names.index("h1")
        assert names[i - 1] == "pipeline-runner"


def test_a_chain_longer_than_the_cap_never_shows_its_tail(shapes):
    """pipeline-runner -> a1 -> ... -> a6, all done but a6: a6 needs seven
    rows with its chain, so it cannot show (no row without its ancestors)."""
    rows = [_d("pipeline-runner")] + [_d(f"a{i}", i - 1) for i in range(1, 6)] + [_w("a6", 5)]
    block = render_block(BlockView(agents=tuple(rows)))
    assert [n.split(" \u203a ")[-1] for _, n in shapes.tree(block)] == \
        ["pipeline-runner", "a1", "a2", "a3", "a4", "a5"]


def test_a_chain_longer_than_the_cap_says_one_more():
    rows = [_d("pipeline-runner")] + [_d(f"a{i}", i - 1) for i in range(1, 6)] + [_w("a6", 5)]
    assert _more(render_block(BlockView(agents=tuple(rows)))) == 1


def test_a_chain_of_exactly_six_shows_its_working_tail(shapes):
    rows = [_d("pipeline-runner")] + [_d(f"a{i}", i - 1) for i in range(1, 5)] + [_w("a5", 4)]
    block = render_block(BlockView(agents=tuple(rows)))
    assert shapes.tree(block)[-1] == (2, "a4 \u203a a5")


@pytest.mark.parametrize("seed", range(12), ids=[f"s{i}" for i in range(12)])
def test_random_trees_keep_the_cap_and_the_count(seed):
    import random
    rnd = random.Random(seed)
    n = rnd.randint(7, 30)
    rows = []
    for i in range(n):
        parent = rnd.randrange(-1, i) if i else -1
        rows.append(AgentRow(f"t{i}", rnd.random() < 0.4, 5.0, parent=parent))
    if all(r.done for r in rows):
        rows[-1] = AgentRow(f"t{n - 1}", False, parent=rows[-1].parent)
    block = render_block(BlockView(agents=tuple(rows)))
    shown = block.count(R)
    assert (shown <= 6, _more(block) or 0) == (True, n - shown)


@pytest.mark.parametrize("seed", range(12), ids=[f"s{i}" for i in range(12)])
def test_random_trees_never_show_a_row_without_its_chain(shapes, seed):
    import random
    rnd = random.Random(seed)
    n = rnd.randint(7, 30)
    rows = []
    for i in range(n):
        parent = rnd.randrange(-1, i) if i else -1
        rows.append(AgentRow(f"t{i}", rnd.random() < 0.4, 5.0, parent=parent))
    if all(r.done for r in rows):
        rows[-1] = AgentRow(f"t{n - 1}", False, parent=rows[-1].parent)
    shown = {n.split(" \u203a ")[-1] for _, n in shapes.tree(render_block(BlockView(agents=tuple(rows))))}
    missing = []
    for name in shown:
        i = int(name[1:])
        p = rows[i].parent
        while p != -1:
            if f"t{p}" not in shown:
                missing.append((name, f"t{p}"))
            p = rows[p].parent
    assert missing == []


@pytest.mark.parametrize("seed", range(12), ids=[f"s{i}" for i in range(12)])
def test_random_trees_prefer_not_done_rows(shapes, seed):
    """If a done row shows while a not-done row is hidden, the hidden one
    must have been unable to fit with its chain."""
    import random
    rnd = random.Random(seed)
    n = rnd.randint(7, 30)
    rows = []
    for i in range(n):
        parent = rnd.randrange(-1, i) if i else -1
        rows.append(AgentRow(f"t{i}", rnd.random() < 0.4, 5.0, parent=parent))
    if all(r.done for r in rows):
        rows[-1] = AgentRow(f"t{n - 1}", False, parent=rows[-1].parent)
    shown = {n.split(" \u203a ")[-1] for _, n in shapes.tree(render_block(BlockView(agents=tuple(rows))))}
    working_shown = sum(1 for i, r in enumerate(rows) if not r.done and f"t{i}" in shown)
    working_total = sum(1 for r in rows if not r.done)
    # Every shown row is either not done or an ancestor of a shown
    # not-done row, until every not-done row that fits is in.
    if working_shown < working_total:
        needed_ancestors = set()
        for i, r in enumerate(rows):
            if not r.done and f"t{i}" in shown:
                p = r.parent
                while p != -1:
                    needed_ancestors.add(f"t{p}")
                    p = rows[p].parent
        stray_done = [x for x in shown if rows[int(x[1:])].done and x not in needed_ancestors]
        assert stray_done == []


# ---- the byte reserve ----------------------------------------------------------------------

_BIG = "\U0001d538" * 200          # four-byte letters, far past any label cut
_TOOLS = ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "WebFetch", "WebSearch",
          "NotebookEdit", "Agent", "Task", "TodoWrite", "mcp__x__y", "Unknown"]


def _row_variants(parent):
    yield AgentRow(_BIG, False, parent=parent)
    yield AgentRow(_BIG, False, prose=_BIG * 5, parent=parent)
    yield AgentRow(_BIG, False, description=_BIG * 5, parent=parent)
    yield AgentRow(_BIG, False, waiting=True, parent=parent)
    yield AgentRow(_BIG, True, 359999.0, parent=parent)
    for tool in _TOOLS:
        yield AgentRow(_BIG, False, tool=tool, tool_arg=_BIG * 5, parent=parent)


def _worst_views():
    """Six shown rows at their widest (each a depth-3+ row carrying the
    hint where it can), with a big "+N more" and the widest header."""
    for proto in _row_variants(0):
        # chain: top, then five rows each started by the one before (hints)
        rows = [AgentRow(_BIG, False, prose=_BIG * 5)]
        for i in range(1, 6):
            rows.append(AgentRow(proto.agent_type, proto.done, proto.seconds,
                                 prose=proto.prose, tool=proto.tool,
                                 tool_arg=proto.tool_arg, description=proto.description,
                                 waiting=proto.waiting, parent=i - 1))
        rows += [AgentRow(_BIG, False) for _ in range(94)]
        yield BlockView(agents=tuple(rows), shells_running=99)
        yield BlockView(agents=tuple(rows), shells_running=99, shells_finished=99,
                        shells_finished_seconds=359999.0, shells_stopped=99,
                        shells_stopped_seconds=359999.0)
    done = tuple(AgentRow(_BIG, True, 359999.0) for _ in range(100))
    yield BlockView(agents=done, agents_done_seconds=359999.0, shells_running=99,
                    shells_finished=99, shells_finished_seconds=359999.0,
                    shells_stopped=99, shells_stopped_seconds=359999.0)
    yield BlockView(shells_running=99, shells_finished=99, shells_finished_seconds=359999.0,
                    shells_stopped=99, shells_stopped_seconds=359999.0)


def _translators(code):
    tr = i18n.get(code)
    return [("default", tr), ("rtl", tr.directed(True)), ("ltr", tr.directed(False))]


@pytest.mark.parametrize("code", CODES)
def test_reserve_covers_every_worst_block(code):
    over = []
    for label, tr in _translators(code):
        reserve = reserve_bytes(tr)
        for view in _worst_views():
            need = len((DIVIDER_SEP + render_block(view, tr)).encode("utf-8"))
            if need > reserve:
                over.append((label, need, reserve))
    assert over == []


def test_reserve_with_no_translator_covers_english():
    need = max(len((DIVIDER_SEP + render_block(v)).encode("utf-8")) for v in _worst_views())
    assert reserve_bytes() >= need


def test_reserve_is_at_least_the_divider():
    assert reserve_bytes() > len(DIVIDER_SEP.encode())


def test_reserve_leaves_room_for_an_answer():
    assert reserve_bytes() < 32768 // 2


# ---- the 16-block bound ----------------------------------------------------------------------

def _block_views():
    yield _eight()
    yield BlockView(agents=(_w("pipeline-runner"),) + tuple(_w(f"h{i}", 0) for i in range(10)))
    yield BlockView(agents=tuple(_w(f"t{i}") for i in range(10)))
    yield BlockView(agents=tuple(_w(f"t{i}") for i in range(3))
                    + (_w("a", 0), _w("b", 1), _w("c", 2), _w("d", 0)), shells_running=2)
    yield from _worst_views()


@pytest.mark.parametrize("code", ["en", "fa", "ar"])
def test_the_tail_never_exceeds_sixteen_blocks(shapes, code):
    tr = i18n.get(code)
    counts = [shapes.conservative_blocks(DIVIDER_SEP + render_block(v, tr)) for v in _block_views()]
    assert max(counts) <= BLOCKS_MAX


def test_one_top_with_five_shown_helpers_reaches_exactly_sixteen(shapes):
    view = BlockView(agents=(_w("pipeline-runner"),) + tuple(_w(f"h{i}", 0) for i in range(10)))
    assert shapes.conservative_blocks(DIVIDER_SEP + render_block(view)) == 16
