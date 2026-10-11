"""Iteration 3: a backslash Claude wrote is escaped in the status block, so
it can never cancel the escape of the next metacharacter or of the bold
that closes an agent type (design.md success criteria 2, 3; entrypoints.md
render_block: "Markdown metacharacters *_`[] in types, prose, descriptions
and arguments come out backslash-escaped", plus the orchestrator's
iteration-3 note that backslashes in prose, descriptions, tool arguments,
agent types and tool names are escaped too).

The property pinned throughout: every metacharacter and every closing
mark the block itself writes follows an EVEN run of backslashes (zero
included), and un-escaping the shown text gives back what Claude wrote.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot.agents_line import (
    ACTIVITY_MAX,
    AgentRow,
    BlockView,
    first_line,
    render_block,
    reserve_bytes,
)
from aipager.i18n import locales
from tests import answer_status_bb as bb

SEP = "\n\n"
META = "*_`[]"
BS = "\\"
FSI, PDI, RLM = "\u2068", "\u2069", "\u200f"
ELLIPSIS = "\u2026"


def _row(agent_type="t", tr=None, done=False, seconds=None, **kw) -> str:
    view = BlockView(agents=(AgentRow(agent_type, done, seconds, **kw),
                             AgentRow("other", False)))
    return render_block(view, tr).split(SEP)[1]


def _activity(row: str) -> str:
    head, sep, tail = row.partition(" · ")
    assert sep, row
    return tail


def _bold_inner(row: str) -> str:
    start = row.index("**") + 2
    end = row.index("**", start)
    return row[start:end]


def _unescape(text: str) -> str:
    return re.sub(r"\\([*_`\[\]\\])", r"\1", text)


def _run_before(text: str, end: int) -> int:
    run = 0
    while end - 1 - run >= 0 and text[end - 1 - run] == BS:
        run += 1
    return run


def _unescaped_meta(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch in META and _run_before(text, i) % 2 == 0]


def _isolates(row: str) -> list[str]:
    return re.findall(f"{FSI}([^{FSI}{PDI}]*){PDI}", row)


# Each field Claude writes, and how to find what the block shows for it.
def _shown(field: str, raw: str, tr=None) -> str:
    if field == "type":
        return _bold_inner(_row(agent_type=raw, tr=tr))
    if field == "done-type":
        return _bold_inner(_row(agent_type=raw, tr=tr, done=True, seconds=5.0))
    if field == "prose":
        return _activity(_row(prose=raw, tr=tr))
    if field == "description":
        return _activity(_row(description=raw, tr=tr))
    if field == "tool-arg":
        return _activity(_row(tool="Read", tool_arg=raw, tr=tr))[len("Reading "):]
    if field == "tool-name":
        return _activity(_row(tool=raw, tr=tr))[len("Using "):]
    raise AssertionError(field)


FIELDS = ["type", "done-type", "prose", "description", "tool-arg", "tool-name"]


# ---- a lone backslash, and two of them ------------------------------------------------

@pytest.mark.parametrize("field", FIELDS)
def test_a_lone_backslash_reads_back_as_written(field):
    assert _unescape(_shown(field, BS)) == BS


@pytest.mark.parametrize("field", FIELDS)
def test_a_lone_backslash_is_shown_as_an_even_run(field):
    shown = _shown(field, BS)
    assert _run_before(shown, len(shown)) % 2 == 0


@pytest.mark.parametrize("field", FIELDS)
def test_two_backslashes_read_back_as_written(field):
    assert _unescape(_shown(field, BS * 2)) == BS * 2


@pytest.mark.parametrize("field", FIELDS)
def test_two_backslashes_are_shown_as_an_even_run(field):
    shown = _shown(field, BS * 2)
    assert _run_before(shown, len(shown)) % 2 == 0


def test_a_lone_backslash_prose_is_exactly_one_escaped_backslash():
    assert _activity(_row(prose=BS)) == BS * 2


def test_first_line_keeps_a_lone_backslash():
    """A backslash is not a Markdown block marker: it is not dropped."""
    assert first_line(BS) == BS


def test_first_line_keeps_a_backslash_as_written():
    assert first_line("C:\\dir\\file now") == "C:\\dir\\file now"


# ---- a backslash right before each metacharacter, in every field ---------------------

@pytest.mark.parametrize("mark", list(META), ids=["star", "underscore", "backtick",
                                                  "open-bracket", "close-bracket"])
@pytest.mark.parametrize("field", FIELDS)
def test_a_backslash_before_a_mark_leaves_no_mark_unescaped(field, mark):
    assert _unescaped_meta(_shown(field, f"a{BS}{mark}b")) == []


@pytest.mark.parametrize("mark", list(META), ids=["star", "underscore", "backtick",
                                                  "open-bracket", "close-bracket"])
@pytest.mark.parametrize("field", FIELDS)
def test_a_backslash_before_a_mark_reads_back_as_written(field, mark):
    assert _unescape(_shown(field, f"a{BS}{mark}b")) == f"a{BS}{mark}b"


# ---- the bold around an agent type that ends in backslashes ---------------------------

@pytest.mark.parametrize("n", [1, 2, 3], ids=["one", "two", "three"])
def test_a_running_type_ending_in_backslashes_closes_its_bold(n):
    row = _row(agent_type="weird" + BS * n)
    assert (row.startswith("○ **"), row.endswith("**"),
            _run_before(row, len(row) - 2) % 2) == (True, True, 0)


@pytest.mark.parametrize("n", [1, 2, 3], ids=["one", "two", "three"])
def test_a_done_type_ending_in_backslashes_closes_its_bold(n):
    row = _row(agent_type="weird" + BS * n, done=True, seconds=5.0)
    close = row.index(" · ") - 2
    assert (row[close:close + 2], _run_before(row, close) % 2) == ("**", 0)


def test_a_type_that_is_only_a_backslash_closes_its_bold():
    row = _row(agent_type=BS)
    assert (row.startswith("○ **"), row.endswith("**"),
            _run_before(row, len(row) - 2) % 2) == (True, True, 0)


def test_a_type_of_backslashes_at_the_label_cap_closes_its_bold():
    """LABEL_MAX is 32: a cut type of backslashes must not end on an odd run."""
    row = _row(agent_type=BS * 40)
    assert (row.endswith("**"), _run_before(row, len(row) - 2) % 2) == (True, 0)


def test_a_type_with_an_odd_run_one_past_the_label_cap_closes_its_bold():
    row = _row(agent_type="x" * 31 + BS * 3)
    assert (row.endswith("**"), _run_before(row, len(row) - 2) % 2) == (True, 0)


@pytest.mark.parametrize("code", sorted(locales.LOCALES))
def test_every_language_closes_the_bold_of_a_type_ending_in_a_backslash(code):
    row = _row(agent_type="weird" + BS, tr=i18n.get(code))
    close = row.rindex("**")
    before = row[:close].rstrip(PDI)
    assert _run_before(before, len(before)) % 2 == 0


# ---- prose at the 60-code-point cut ---------------------------------------------------

def _cut_ok(shown: str) -> bool:
    body = shown[:-1] if shown.endswith(ELLIPSIS) else shown
    return _run_before(body, len(body)) % 2 == 0 and _unescaped_meta(body) == []


@pytest.mark.parametrize("at", [ACTIVITY_MAX - 3, ACTIVITY_MAX - 2, ACTIVITY_MAX - 1,
                                ACTIVITY_MAX, ACTIVITY_MAX + 1],
                         ids=["at-58", "at-59", "at-60", "at-61", "at-62"])
def test_a_hard_cut_near_a_backslash_leaves_an_even_run(at):
    raw = "x" * (at - 1) + BS + "y" * 10
    assert _cut_ok(_activity(_row(prose=raw)))


@pytest.mark.parametrize("at", [ACTIVITY_MAX - 2, ACTIVITY_MAX - 1, ACTIVITY_MAX],
                         ids=["at-59", "at-60", "at-61"])
def test_a_hard_cut_near_a_backslash_shows_first_line_escaped(at):
    raw = "x" * (at - 1) + BS + "y" * 10
    assert _unescape(_activity(_row(prose=raw))) == first_line(raw)


@pytest.mark.parametrize("at", [ACTIVITY_MAX - 2, ACTIVITY_MAX - 1, ACTIVITY_MAX],
                         ids=["at-59", "at-60", "at-61"])
def test_a_hard_cut_near_a_backslashed_star_leaves_no_mark_unescaped(at):
    raw = "x" * (at - 1) + BS + "*" + "y" * 10
    assert _cut_ok(_activity(_row(prose=raw)))


@pytest.mark.parametrize("at", [ACTIVITY_MAX - 2, ACTIVITY_MAX - 1],
                         ids=["at-59", "at-60"])
def test_a_word_cut_right_after_a_backslash_leaves_an_even_run(at):
    raw = "a" * (at - 1) + BS + " b c d e f g h"
    assert _cut_ok(_activity(_row(prose=raw)))


def test_a_word_cut_after_a_word_ending_in_a_backslash_is_cut():
    act = _activity(_row(prose=("word" + BS + " ") * 20))
    assert act.endswith(ELLIPSIS)


def test_a_word_cut_after_a_word_ending_in_a_backslash_leaves_an_even_run():
    assert _cut_ok(_activity(_row(prose=("word" + BS + " ") * 20)))


def test_an_all_backslash_prose_is_cut_to_an_even_run():
    assert _cut_ok(_activity(_row(prose=BS * 70)))


def test_an_all_backslash_prose_reads_back_as_first_line():
    raw = BS * 70
    shown = _activity(_row(prose=raw))
    assert _unescape(shown) == first_line(raw)


def test_an_all_backslash_prose_shows_at_most_the_activity_cap():
    """The cap is on what Claude wrote (60 code points with the ellipsis),
    not on the escaped text."""
    assert len(_unescape(_activity(_row(prose=BS * 70)))) <= ACTIVITY_MAX


def test_a_description_cut_near_a_backslash_leaves_an_even_run():
    raw = "x" * (ACTIVITY_MAX - 2) + BS + "y" * 10
    assert _cut_ok(_activity(_row(description=raw)))


def test_a_backslash_before_a_sentence_end_keeps_the_text_before_it():
    raw = "Check C:\\tmp\\. Then more"
    assert _unescape(_activity(_row(prose=raw))) == first_line(raw)


# ---- Persian and Arabic: escaped inside the isolate -----------------------------------

FA_FIELDS = ["type", "prose", "description", "tool-arg", "tool-name"]


def _fa_row(field: str, raw: str, code: str) -> str:
    tr = i18n.get(code)
    return {
        "type": lambda: _row(agent_type=raw, tr=tr),
        "prose": lambda: _row(prose=raw, tr=tr),
        "description": lambda: _row(description=raw, tr=tr),
        "tool-arg": lambda: _row(tool="Read", tool_arg=raw, tr=tr),
        "tool-name": lambda: _row(tool=raw, tr=tr),
    }[field]()


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("field", FA_FIELDS)
def test_rtl_a_backslash_before_a_bracket_leaves_no_mark_unescaped(field, code):
    row = _fa_row(field, f"q {BS}[z]", code)
    assert [s for s in _isolates(row) if _unescaped_meta(s)] == []


#: A tool's activity is one English phrase (operator, 2026-10-10), isolated
#: whole in an RTL row: the raw value reads back after its phrase word.
_PHRASE = {"tool-arg": "Reading ", "tool-name": "Using "}


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("field", FA_FIELDS)
def test_rtl_a_backslash_before_a_bracket_reads_back_as_written(field, code):
    row = _fa_row(field, f"q {BS}[z]", code)
    assert _PHRASE.get(field, "") + f"q {BS}[z]" in [_unescape(s) for s in _isolates(row)]


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("field", FA_FIELDS)
def test_rtl_a_trailing_backslash_is_an_even_run_before_the_isolate_closes(field, code):
    row = _fa_row(field, "end" + BS, code)
    shown = [s for s in _isolates(row) if _unescape(s) == _PHRASE.get(field, "") + "end" + BS]
    assert [s for s in shown if _run_before(s, len(s)) % 2] == [] and shown


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_a_type_ending_in_a_backslash_ends_isolate_then_bold(code):
    row = _row(agent_type="weird" + BS, tr=i18n.get(code))
    assert row.endswith(PDI + "**")


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_a_row_with_backslashes_still_starts_with_rlm(code):
    assert _row(agent_type=BS, prose=BS + "[x]", tr=i18n.get(code)).startswith(RLM)


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_a_cut_prose_ending_in_a_backslash_leaves_an_even_run(code):
    raw = "x" * (ACTIVITY_MAX - 2) + BS + "y" * 10
    row = _row(prose=raw, tr=i18n.get(code))
    cut = [s for s in _isolates(row) if s.startswith("xxx")]
    assert len(cut) == 1 and _cut_ok(cut[0])


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_backslashes_are_not_digit_converted_neighbours(code):
    """A backslash next to digits Claude wrote leaves the digits Latin."""
    row = _row(prose=f"step {BS}42 now", tr=i18n.get(code))
    assert f"step {BS}{BS}42 now" in _isolates(row)


# ---- through the hook: the delivered answer ------------------------------------------

def test_hook_a_type_ending_in_a_backslash_closes_its_bold(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="weird" + BS)
    bb.finish(run_async, bot, sess)
    rows = bb.block_of(bb.answer_text(wire)).split(SEP)
    row = next(r for r in rows if "weird" in r)
    assert (row.endswith("**"), _run_before(row, len(row) - 2) % 2) == (True, 0)


def test_hook_prose_with_a_backslashed_star_leaves_no_mark_unescaped(
    bot, sess, wire, run_async,
):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "MessageDisplay", agent_id="a1",
            delta="Now \\*bold\\* here", message_id="m1", index=0, final=False)
    bb.finish(run_async, bot, sess)
    rows = bb.block_of(bb.answer_text(wire)).split(SEP)
    row = next(r for r in rows if r.startswith("○ **Explore**"))
    assert _unescaped_meta(_activity(row)) == []


def test_hook_a_windows_path_argument_reads_back_as_written(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Read",
            tool_input={"file_path": "C:\\dir\\"})
    bb.finish(run_async, bot, sess)
    rows = bb.block_of(bb.answer_text(wire)).split(SEP)
    row = next(r for r in rows if r.startswith("○ **Explore**"))
    assert _unescape(_activity(row)).endswith("C:\\dir\\")


def test_hook_a_windows_path_argument_ends_on_an_even_run(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Read",
            tool_input={"file_path": "C:\\dir\\"})
    bb.finish(run_async, bot, sess)
    rows = bb.block_of(bb.answer_text(wire)).split(SEP)
    row = next(r for r in rows if r.startswith("○ **Explore**"))
    assert _run_before(row, len(row)) % 2 == 0


# ---- the size reserve with backslash-heavy input (each one doubles) --------------------

def _backslash_heavy_view(done: bool) -> BlockView:
    return BlockView(
        agents=tuple(AgentRow(BS * 40, done, 359_999.0 if done else None,
                              prose=(BS * 9 + " ") * 10, description=BS * 200,
                              tool=BS * 40, tool_arg=BS * 200)
                     for _ in range(9)),
        agents_done_seconds=359_999.0 if done else None,
        shells_running=0 if done else 150, shells_finished=150,
        shells_finished_seconds=359_999.0, shells_stopped=150,
        shells_stopped_seconds=359_999.0)


@pytest.mark.parametrize("done", [False, True], ids=["working", "settled"])
def test_reserve_covers_a_backslash_heavy_block_in_every_language(done):
    view = _backslash_heavy_view(done)
    short = [code for code in sorted(locales.LOCALES)
             if len((SEP + render_block(view, i18n.get(code))).encode())
             > reserve_bytes(i18n.get(code))]
    assert short == []


def test_reserve_covers_backslash_tool_phrases_in_every_language():
    view = BlockView(agents=tuple(AgentRow(BS * 40, False, tool=BS * 40)
                                  for _ in range(4)), shells_running=150)
    short = [code for code in sorted(locales.LOCALES)
             if len((SEP + render_block(view, i18n.get(code))).encode())
             > reserve_bytes(i18n.get(code))]
    assert short == []
