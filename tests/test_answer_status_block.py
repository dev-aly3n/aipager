"""The status block under an answer: the pure renderer
(``aipager.bot.agents_line``, roadmap 8.41, Claude Code's agent panel
style).

The spec's states in English, byte for byte; the same states in fa, ar,
ru and zh-Hans (marks, isolates, digits, plural forms, frame words); the
activity's precedence (prose > tool phrase > description > the bare
type), its first-line trimming and escaping; the four-row limit, the
promise only while an agent works; and the reserve that keeps every later
edit inside the ceiling.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot import agents_line as al
from aipager.i18n import core, plurals

A, V = al.AgentRow, al.BlockView
RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
PROMISE = "Results from the agents will follow here."

#: The spec's states (2026-10-10), as views.
WORKING = V(agents=(A("pipeline-runner", False, prose="Writing final.md for the pipeline"),
                    A("Explore", False, tool="Read", tool_arg="state.py")),
            shells_running=1)
ONE_DONE = V(agents=(A("Explore", True, 42),
                     A("pipeline-runner", False, tool="Bash", tool_arg="")),
             shells_running=1)
AGENTS_DONE_SHELL_UP = V(agents=(A("Explore", True, 42), A("pipeline-runner", True, 300)),
                         agents_done_seconds=372, shells_running=1)
AGENTS_DONE_SHELL_ENDED = V(agents=(A("Explore", True, 42), A("pipeline-runner", True, 300)),
                            agents_done_seconds=372, shells_finished=1,
                            shells_finished_seconds=2 * 3600 + 13 * 60 + 5)
SHELL_ONLY = V(shells_running=1)
SHELL_ONLY_FINISHED = V(shells_finished=1, shells_finished_seconds=90)
SHELL_ONLY_STOPPED = V(shells_stopped=1, shells_stopped_seconds=90)
STATES = {
    "working": WORKING, "one-done": ONE_DONE, "agents-done-shell-up": AGENTS_DONE_SHELL_UP,
    "agents-done-shell-ended": AGENTS_DONE_SHELL_ENDED, "shell-only": SHELL_ONLY,
    "shell-only-finished": SHELL_ONLY_FINISHED, "shell-only-stopped": SHELL_ONLY_STOPPED,
}

#: The English goldens: exactly what the operator's spec drew.
GOLDEN = {
    "working": ("⏳ 2 agents working · 🖥 1 shell running\n\n"
                "○ 🤖 **pipeline-runner** · Writing final.md for the pipeline\n\n"
                "○ 🤖 **Explore** · Reading state.py\n\n" + PROMISE),
    "one-done": ("⏳ 1 agent working · 🖥 1 shell running\n\n"
                 "✓ 🤖 **Explore** · done (0:42)\n\n"
                 "○ 🤖 **pipeline-runner** · Running a command\n\n" + PROMISE),
    "agents-done-shell-up": "✅ 2 agents done (6:12) · 🖥 1 shell running",
    "agents-done-shell-ended": "✅ 2 agents done (6:12) · 1 shell finished (2:13:05)",
    "shell-only": "🖥 1 shell running",
    "shell-only-finished": "1 shell finished (1:30)",
    "shell-only-stopped": "1 shell stopped (1:30)",
}

OTHERS = ("fa", "ar", "ru", "zh-Hans")


def _lines(block: str) -> list[str]:
    return block.split("\n\n")


# ── English: the spec, byte for byte ────────────────────────────────────────

@pytest.mark.parametrize("state", list(GOLDEN), ids=list(GOLDEN))
def test_every_spec_state_in_english(state):
    assert al.render_block(STATES[state]) == GOLDEN[state]
    assert al.render_block(STATES[state], i18n.get("en")) == GOLDEN[state]


def test_nothing_to_show_is_no_block():
    assert al.render_block(V()) == ""


def test_the_header_comes_first_and_counts_shells_only():
    """Rule 1: counts first, shells as a count, never a command."""
    block = al.render_block(V(agents=(A("Explore", False),), shells_running=3))
    assert _lines(block)[0] == "⏳ 1 agent working · 🖥 3 shells running"


def test_the_promise_is_only_while_an_agent_works():
    """Rule 2: about agents only, and only while one works."""
    assert al.render_block(WORKING).endswith("\n\n" + PROMISE)
    assert PROMISE not in al.render_block(AGENTS_DONE_SHELL_UP)
    assert PROMISE not in al.render_block(SHELL_ONLY)


def test_rows_show_only_while_an_agent_works():
    """The spec draws "agents done" as the header alone."""
    assert len(_lines(al.render_block(ONE_DONE))) == 4
    assert len(_lines(al.render_block(AGENTS_DONE_SHELL_UP))) == 1


def test_at_most_six_rows_then_more():
    """Rule 3 (six rows since the agents tree, 2026-10-14)."""
    view = V(agents=tuple(A(f"t{i}", False) for i in range(9)))
    lines = _lines(al.render_block(view))
    assert lines[0] == "⏳ 9 agents working"
    assert lines[1:7] == [f"○ 🤖 **t{i}**" for i in range(6)]
    assert lines[7] == "+3 more"
    assert lines[8] == PROMISE
    exactly_six = V(agents=tuple(A(f"t{i}", False) for i in range(6)))
    assert "more" not in al.render_block(exactly_six)


def test_the_header_counts_down_as_rows_settle():
    one = al.render_block(V(agents=(A("a", True, 5), A("b", False), A("c", False))))
    assert _lines(one)[0] == "⏳ 2 agents working"
    assert _lines(one)[1] == "✓ 🤖 **a** · done (0:05)"


def test_shells_finished_and_stopped_together_and_running_wins():
    assert al.render_block(V(shells_finished=1, shells_finished_seconds=90,
                             shells_stopped=2, shells_stopped_seconds=30)) == \
        "1 shell finished (1:30) · 2 shells stopped (0:30)"
    assert al.render_block(V(shells_running=1, shells_finished=1, shells_stopped=1)) == \
        "🖥 1 shell running"


# ── the activity ─────────────────────────────────────────────────────────────

def _activity(row: A) -> str:
    block = al.render_block(V(agents=(row,)))
    line = _lines(block)[1]
    return line.partition(" · ")[2]


def test_prose_wins_over_the_tool_and_the_description():
    row = A("t", False, prose="Checking the cache.", tool="Read", tool_arg="a.py",
            description="Audit the cache")
    assert _activity(row) == "Checking the cache."


def test_the_tool_wins_over_the_description():
    row = A("t", False, tool="Read", tool_arg="a.py", description="Audit the cache")
    assert _activity(row) == "Reading a.py"


def test_the_description_when_nothing_else_is_known():
    assert _activity(A("t", False, description="Audit the cache")) == "Audit the cache"


def test_nothing_known_is_the_bare_type():
    assert _lines(al.render_block(V(agents=(A("Explore", False),))))[1] == "○ 🤖 **Explore**"


def test_blank_prose_falls_through():
    assert _activity(A("t", False, prose="  \n```\ncode\n```\n", tool="Bash")) == \
        "Running a command"


@pytest.mark.parametrize("tool, arg, text", [
    ("Read", "state.py", "Reading state.py"),
    ("Write", "final.md", "Writing final.md"),
    ("Edit", "notify.py", "Editing notify.py"),
    ("MultiEdit", "a.py", "Editing a.py"),
    ("NotebookEdit", "n.ipynb", "Editing n.ipynb"),
    ("Bash", "Verify the mutation copy matches the worktree",
     "Verify the mutation copy matches the worktree"),
    ("Bash", "", "Running a command"),
    ("Grep", "agents_line", "Searching for agents\\_line"),
    ("Glob", "*.py", "Searching for \\*.py"),
    ("WebSearch", "telegram bot api", "Searching the web for telegram bot api"),
    ("WebFetch", "docs.python.org", "Fetching docs.python.org"),
    ("Agent", "Ship phase 5", "Ship phase 5"),
    ("Agent", "", "Starting an agent"),
    ("Task", "", "Starting an agent"),
    ("TodoWrite", "", "Using TodoWrite"),
    ("Read", "", "Using Read"),
    ("my_tool", "", "Using my\\_tool"),
], ids=["read", "write", "edit", "multiedit", "notebookedit", "bash-described", "bash",
        "grep", "glob", "websearch", "webfetch", "agent-described", "agent", "task", "other",
        "file-without-arg", "escaped-name"])
def test_each_tool_phrase(tool, arg, text):
    assert _activity(A("t", False, tool=tool, tool_arg=arg)) == text


def test_a_bash_row_shows_its_description_in_english_in_every_language():
    # The hook records a Bash call's description, never its command (see
    # tests/test_answer_status_live.py); the row shows it as written.
    for code in ("en", "fa", "ru"):
        block = al.render_block(V(agents=(A("t", False, tool="Bash",
                                            tool_arg="Verify the build"),)), tr=i18n.get(code))
        assert "Verify the build" in block, code


def test_a_long_argument_is_cut():
    act = _activity(A("t", False, tool="Read", tool_arg="x" * 100))
    assert act == "Reading " + "x" * (al.ARG_MAX - 1) + "…"


def test_markdown_in_prose_types_and_arguments_is_escaped():
    block = al.render_block(V(agents=(
        A("general_purpose", False, prose="Fix [the] `x_y` *now*"),
        A("a_b", False, tool="Grep", tool_arg="[a]*"))))
    lines = _lines(block)
    assert lines[1] == "○ 🤖 **general\\_purpose** · Fix \\[the\\] \\`x\\_y\\` \\*now\\*"
    assert lines[2] == "○ 🤖 **a\\_b** · Searching for \\[a\\]\\*"


@pytest.mark.parametrize("field, raw, escaped", [
    ("prose", "Now **bold** here", "Now \\*\\*bold\\*\\* here"),
    ("prose", "Now __u__ here", "Now \\_\\_u\\_\\_ here"),
    ("prose", "Now `code` here", "Now \\`code\\` here"),
    ("prose", "A lone ` tick", "A lone \\` tick"),
    ("description", "Run `make` now", "Run \\`make\\` now"),
], ids=["prose-bold", "prose-underline", "prose-code", "prose-lone-tick",
        "description-code"])
def test_inline_markup_is_kept_as_written_and_escaped(field, raw, escaped):
    """Claude's words are shown as written: inline markup is escaped,
    never stripped."""
    line = _lines(al.render_block(V(agents=(A("t", False, **{field: raw}),))))[1]
    assert line == "○ 🤖 **t** · " + escaped


@pytest.mark.parametrize("field", ["agent_type", "tool"], ids=["agent-type", "tool-name"])
def test_a_direction_control_in_a_name_is_revealed(field):
    """An agent type or a tool name carrying a direction control is shown
    with a visible marker, never raw (an RLO would reorder the row)."""
    agent = "Ex\u202eplore" if field == "agent_type" else "t"
    tool = "My\u202eTool" if field == "tool" else ""
    for code in ("en", "fa"):
        line = _lines(al.render_block(V(agents=(A(agent, False, tool=tool),)),
                                      i18n.get(code)))[1]
        assert "\u202e" not in line
        assert ("Ex" if field == "agent_type" else "My") in line


# A backslash Claude wrote is doubled before the card's escaper runs, so it
# can never cancel the escape of the mark after it (or the bold's closing
# star): each case below fails when its own site loses the doubling.
@pytest.mark.parametrize("agent, kw, line", [
    ("t", {"prose": "a\\*b* c"}, "○ 🤖 **t** · a\\\\\\*b\\* c"),
    ("t", {"description": "q \\[z]"}, "○ 🤖 **t** · q \\\\\\[z\\]"),
    ("t", {"tool": "Read", "tool_arg": "f\\*.py"}, "○ 🤖 **t** · Reading f\\\\\\*.py"),
    ("t", {"tool": "My\\Tool"}, "○ 🤖 **t** · Using My\\\\Tool"),
    ("a\\_b", {}, "○ 🤖 **a\\\\\\_b**"),
    ("weird\\", {}, "○ 🤖 **weird\\\\**"),
], ids=["prose", "description", "tool-argument", "tool-name", "agent-type",
        "agent-type-trailing"])
def test_a_backslash_in_claudes_text_is_escaped(agent, kw, line):
    assert _lines(al.render_block(V(agents=(A(agent, False, **kw),))))[1] == line


def test_a_backslash_in_a_done_agents_type_is_escaped():
    block = al.render_block(V(agents=(A("weird\\", True, 42), A("t", False))))
    assert _lines(block)[1] == "✓ 🤖 **weird\\\\** · done (0:42)"


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_a_backslash_is_escaped_inside_its_isolate(code):
    tr = i18n.get(code)
    prose = _lines(al.render_block(V(agents=(A("t", False, prose="a\\*b"),)), tr))[1]
    agent = _lines(al.render_block(V(agents=(A("x\\", False),)), tr))[1]
    assert FSI + "a\\\\\\*b" + PDI in prose
    assert FSI + "x\\\\" + PDI in agent


def test_a_long_type_is_cut():
    line = _lines(al.render_block(V(agents=(A("x" * 80, False),))))[1]
    assert line == "○ 🤖 **" + "x" * (al.LABEL_MAX - 1) + "…**"


# ── first_line ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, first", [
    ("Writing final.md for the pipeline", "Writing final.md for the pipeline"),
    ("\n\n  Reading state.py now.\nThen the tests.", "Reading state.py now."),
    ("First sentence. Second sentence.", "First sentence."),
    ("Is it? Yes.", "Is it?"),
    ("Done! Next.", "Done!"),
    ("See final.md and notify.py", "See final.md and notify.py"),
    ("## Plan\nmore", "Plan"),
    ("> quoted line", "quoted line"),
    ("- a bullet", "a bullet"),
    ("1. a numbered step", "a numbered step"),
    ("```python\nprint(1)\n```\nAfter the code.", "After the code."),
    ("**Bold** and `code`", "**Bold** and `code`"),
    ("too    many\tspaces", "too many spaces"),
    ("读取文件。然后测试。", "读取文件。"),
    ("", ""),
    ("   \n\n", ""),
], ids=["plain", "first-line", "sentence", "question", "exclaim", "file-dots", "heading",
        "quote", "bullet", "numbered", "fence", "inline-markup", "whitespace", "cjk",
        "empty", "blank"])
def test_first_line(text, first):
    assert al.first_line(text) == first


def test_first_line_is_cut_at_a_word_boundary():
    text = "word " * 30
    out = al.first_line(text)
    assert len(out) <= al.ACTIVITY_MAX
    assert out.endswith("word…")


def test_first_line_is_cut_hard_without_a_space():
    out = al.first_line("字" * 100)
    assert out == "字" * (al.ACTIVITY_MAX - 1) + "…"


def test_first_line_reveals_direction_controls():
    out = al.first_line("abc\u202edef")
    assert "\u202e" not in out
    assert out != "abcdef"


def test_first_line_takes_only_strings():
    assert al.first_line(None) == ""


# ── other languages ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("state", list(STATES), ids=list(STATES))
def test_rtl_every_line_starts_with_the_mark(code, state):
    block = al.render_block(STATES[state], i18n.get(code))
    assert block
    for line in _lines(block):
        assert line.startswith(RLM), (code, state, line)


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_rtl_claudes_text_and_names_are_isolated_never_converted(code):
    tr = i18n.get(code)
    block = al.render_block(V(agents=(
        A("agent7", False, prose="Checked 42 files"),
        A("Explore", False, tool="Read", tool_arg="v2.py"),
        A("Plan", False, description="Audit 3 modules"))), tr)
    assert f"**{FSI}agent7{PDI}**" in block
    assert f"{FSI}Checked 42 files{PDI}" in block
    assert f"{FSI}Reading v2.py{PDI}" in block      # the English activity, whole
    assert f"{FSI}Audit 3 modules{PDI}" in block


def test_fa_counts_and_clocks_use_persian_digits():
    tr = i18n.get("fa")
    block = al.render_block(AGENTS_DONE_SHELL_ENDED, tr)
    assert "۲" in block and "۶:۱۲" in block and "۲:۱۳:۰۵" in block
    assert not re.search("[0-9]", block)
    working = al.render_block(V(agents=(A("a", False),) * 12), tr)
    assert "۱۲" in _lines(working)[0]


def test_ar_counts_and_clocks_use_arabic_digits():
    block = al.render_block(AGENTS_DONE_SHELL_ENDED, i18n.get("ar"))
    assert "٦:١٢" in block and "٢:١٣:٠٥" in block
    assert not re.search("[0-9]", block)


def _form(code, key, n):
    form = core._catalog(code)[key][plurals.category(code, n)]
    return form.replace("{n}", i18n.get(code).num(n))


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25, 100])
def test_ru_header_takes_the_cldr_form(n):
    tr = i18n.get("ru")
    header = _lines(al.render_block(V(agents=(A("a", False),) * n, shells_running=n), tr))[0]
    assert header == (_form("ru", "agents.head.working", n) + " · "
                      + _form("ru", "agents.head.shells_running", n))


@pytest.mark.parametrize("n", [1, 2, 3, 11, 100])
def test_ar_header_takes_the_cldr_form(n):
    tr = i18n.get("ar")
    header = _lines(al.render_block(V(agents=(A("a", False),) * n), tr))[0]
    assert header == RLM + _form("ar", "agents.head.working", n) + RLM


def test_ar_one_and_two_name_the_count_in_words():
    tr = i18n.get("ar")
    one = _lines(al.render_block(V(agents=(A("a", False),)), tr))[0]
    two = _lines(al.render_block(V(agents=(A("a", False),) * 2), tr))[0]
    assert "١" not in one and "٢" not in two
    assert one != two


def test_zh_hans_frame_words_are_translated():
    tr = i18n.get("zh-Hans")
    for state, view in STATES.items():
        block = al.render_block(view, tr)
        # The frame words are Chinese; the activity stays Claude Code's
        # English ("Reading state.py"), by design (operator, 2026-10-10).
        for word in ("agent", "working", "shell running", "done", "finished",
                     "stopped", "Results"):
            assert word not in block, (state, word, block)


@pytest.mark.parametrize("code", OTHERS)
def test_other_languages_never_translate_claudes_text_or_types(code):
    tr = i18n.get(code)
    block = al.render_block(WORKING, tr)
    assert "pipeline-runner" in block
    assert "Explore" in block
    assert "Writing final.md for the pipeline" in block
    assert "state.py" in block


@pytest.mark.parametrize("code", OTHERS)
@pytest.mark.parametrize("state", list(STATES), ids=list(STATES))
def test_other_languages_render_every_state_unlike_english(code, state):
    block = al.render_block(STATES[state], i18n.get(code))
    assert block and block != GOLDEN[state]
    assert "—" not in block


# ── the reserve ──────────────────────────────────────────────────────────────

def _big_views():
    wide = "\U0001f600"
    agents = tuple(A(wide * 50, False, prose=wide * 200) for _ in range(9))
    yield V(agents=agents, shells_running=50)
    for tool in ("Read", "WebSearch", "WebFetch", "Grep", "TodoWrite" * 9, "Bash"):
        yield V(agents=tuple(A("_" * 40, False, tool=tool, tool_arg=wide * 300)
                             for _ in range(99)), shells_running=49)
    yield V(agents=tuple(A(wide * 40, True, 360000) for _ in range(99)),
            agents_done_seconds=3_000_000, shells_finished=50,
            shells_finished_seconds=3_000_000, shells_stopped=50,
            shells_stopped_seconds=3_000_000)
    yield V(agents=(A("*" * 60, False, description="_" * 300),) * 5, shells_running=1)


#: English and every shipped catalog, sorted (stable ids).
ALL_CODES = ["en", *sorted(p.stem for p in core.LOCALES_DIR.glob("*.jsonl"))]


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_reserve_holds_the_largest_real_block(code):
    tr = i18n.get(code)
    reserve = al.reserve_bytes(tr)
    for view in _big_views():
        assert len(("\n\n" + al.render_block(view, tr)).encode()) <= reserve, code


def _backslash_views():
    bs = "\\"
    yield V(agents=tuple(A(bs * 80, False, prose=(bs + "*") * 200) for _ in range(9)),
            shells_running=50)
    yield V(agents=tuple(A(bs * 80, False, description=(bs + "_") * 200) for _ in range(9)),
            shells_running=50)
    for tool in ("Read", "WebSearch", "WebFetch", "Grep", bs * 80):
        yield V(agents=tuple(A((bs + "*") * 40, False, tool=tool, tool_arg=bs * 300)
                             for _ in range(99)), shells_running=49)
    yield V(agents=tuple(A(bs * 80, True, 360000) for _ in range(99)),
            agents_done_seconds=3_000_000, shells_finished=50,
            shells_finished_seconds=3_000_000, shells_stopped=50,
            shells_stopped_seconds=3_000_000)


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_reserve_holds_a_backslash_heavy_block(code):
    """Doubling a backslash makes it two bytes, still under the four-byte
    character the reserve assumes everywhere."""
    tr = i18n.get(code)
    reserve = al.reserve_bytes(tr)
    for view in _backslash_views():
        assert len(("\n\n" + al.render_block(view, tr)).encode()) <= reserve, code


def test_the_reserve_is_per_language_and_cached():
    assert al.reserve_bytes() == al.reserve_bytes(i18n.get("en"))
    assert al.reserve_bytes(i18n.get("fa")) > al.reserve_bytes(i18n.get("en"))
