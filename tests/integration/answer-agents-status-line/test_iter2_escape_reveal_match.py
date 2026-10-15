"""Iteration 2: inline Markdown in Claude's words is kept as written and
escaped, direction controls in agent types and tool names are made
visible, and description matching survives a normalised type
(design.md success criteria 2, 3; entrypoints.md render_block,
first_line, the hook handler's PreToolUse/SubagentStart).

Documented behaviour this file pins (orchestrator, iteration 2):
inline markup in prose and descriptions stays as written and comes out
backslash-escaped; leading block markers and whole fenced code blocks
are still removed; an agent type or tool name carrying a bidi or
direction control never reaches the output raw; the SubagentStart type
is normalised like the Agent call's type before matching.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n, preferences
from aipager.bot.agents_line import AgentRow, BlockView, first_line, render_block
from tests import answer_status_bb as bb

SEP = "\n\n"
META = "*_`[]"

# Every bidi/direction control the project guards against, as escapes.
ALL_CONTROLS = {
    "lre": "\u202a", "rle": "\u202b", "pdf": "\u202c", "lro": "\u202d",
    "rlo": "\u202e", "lri": "\u2066", "rli": "\u2067", "fsi": "\u2068",
    "pdi": "\u2069", "lrm": "\u200e", "rlm": "\u200f", "alm": "\u061c",
}
# The RTL renderer adds RLM, FSI and PDI itself, so in fa only the others
# can be told apart from its own marks.
FA_FOREIGN = {k: v for k, v in ALL_CONTROLS.items() if k not in ("rlm", "fsi", "pdi")}


def _row(agent_type="t", tr=None, **kw) -> str:
    view = BlockView(agents=(AgentRow(agent_type, False, **kw),))
    return render_block(view, tr).split(SEP)[1]


def _activity(row: str) -> str:
    head, sep, tail = row.partition(" · ")
    assert sep, row
    return tail


def _unescape(text: str) -> str:
    return re.sub(r"\\([*_`\[\]\\])", r"\1", text)


def _unescaped_meta(text: str) -> list[int]:
    """Positions of metacharacters NOT escaped (preceded by an even run of
    backslashes, zero included)."""
    bad = []
    for i, ch in enumerate(text):
        if ch in META:
            run = 0
            j = i - 1
            while j >= 0 and text[j] == "\\":
                run += 1
                j -= 1
            if run % 2 == 0:
                bad.append(i)
    return bad


# ---- inline markup in prose: kept as written, escaped -------------------------------------

@pytest.mark.parametrize("raw, shown", [
    ("Now **bold** here", "Now \\*\\*bold\\*\\* here"),
    ("Now __under__ here", "Now \\_\\_under\\_\\_ here"),
    ("Now `code` here", "Now \\`code\\` here"),
    ("Now *it* here", "Now \\*it\\* here"),
    ("See [docs](x) here", "See \\[docs\\](x) here"),
    ("a lone ` tick", "a lone \\` tick"),
    ("snake_case_name ok", "snake\\_case\\_name ok"),
], ids=["double-star", "double-underscore", "backticks", "single-star",
        "link", "lone-backtick", "snake-case"])
def test_prose_inline_markup_is_escaped_not_stripped(raw, shown):
    assert _activity(_row(prose=raw)) == shown


@pytest.mark.parametrize("raw, shown", [
    ("Run `make` now", "Run \\`make\\` now"),
    ("Fix the **hot** path", "Fix the \\*\\*hot\\*\\* path"),
    ("Rename my_var", "Rename my\\_var"),
], ids=["backticks", "double-star", "underscore"])
def test_description_inline_markup_is_escaped_not_stripped(raw, shown):
    assert _activity(_row(description=raw)) == shown


@pytest.mark.parametrize("raw", [
    "Now **bold** and `code` and __u__ here",
    "Weird ` * _ [ ] mix",
    "x_y*z`w[v]u",
], ids=["mixed", "lone-markers", "packed"])
def test_prose_unescaped_reads_exactly_as_written(raw):
    assert _unescape(_activity(_row(prose=raw))) == raw


@pytest.mark.parametrize("raw", [
    "Now **bold** and `code` and __u__ here",
    "Weird ` * _ [ ] mix",
], ids=["mixed", "lone-markers"])
def test_prose_leaves_no_metacharacter_unescaped(raw):
    assert _unescaped_meta(_activity(_row(prose=raw))) == []


def test_first_line_keeps_inline_markup_as_written():
    assert first_line("Now **bold** and `code` here") == "Now **bold** and `code` here"


def test_first_line_still_drops_a_heading_marker_before_inline_code():
    assert first_line("## Plan `x` today") == "Plan `x` today"


def test_first_line_still_drops_a_bullet_before_inline_bold():
    assert first_line("- Fix **this** now") == "Fix **this** now"


def test_a_heading_with_inline_code_shows_escaped_without_the_marker():
    assert _activity(_row(prose="## Plan `x` today")) == "Plan \\`x\\` today"


def test_first_line_skips_a_whole_fenced_block():
    """Documented: the whole fenced block is skipped, not only its fences."""
    assert first_line("```python\nprint(1)\n```") == ""


def test_a_fenced_only_prose_falls_back_to_the_tool():
    assert _activity(_row(prose="```\nx = 1\n```", tool="Read", tool_arg="a.py")) == \
        "Reading a.py"


def test_a_fenced_only_description_gives_a_bare_row():
    assert _row(description="```\nx = 1\n```") == "○ 🤖 **t**"


def test_a_description_after_a_fenced_block_is_found():
    assert _activity(_row(description="```\nx = 1\n```\nCheck the `result`")) == \
        "Check the \\`result\\`"


def test_a_cut_prose_leaves_no_metacharacter_unescaped():
    act = _activity(_row(prose="a_b " * 30))
    assert (act.endswith("…"), _unescaped_meta(act)) == (True, [])


def test_persian_prose_is_escaped_inside_its_isolate():
    row = _row(prose="Now **bold** here", tr=i18n.get("fa"))
    assert "\u2068Now \\*\\*bold\\*\\* here\u2069" in row


def test_persian_description_is_escaped_inside_its_isolate():
    row = _row(description="Run `make` now", tr=i18n.get("fa"))
    assert "\u2068Run \\`make\\` now\u2069" in row


# ---- error guessing: a backslash written by Claude must not unescape the next mark -------

@pytest.mark.parametrize("field, raw", [
    ("prose", "a\\*b* c"),
    ("prose", "Now \\*bold\\* here"),
    ("prose", "x \\_y_"),
    ("description", "q \\[z]"),
], ids=["prose-star", "prose-pair", "prose-underscore", "description-bracket"])
def test_a_backslash_before_a_mark_does_not_unescape_it(field, raw):
    assert _unescaped_meta(_activity(_row(**{field: raw}))) == []


def test_a_backslash_before_a_mark_in_a_tool_argument_does_not_unescape_it():
    act = _activity(_row(tool="Read", tool_arg="f\\*.py"))
    assert _unescaped_meta(act) == []


def test_a_backslash_before_a_mark_in_an_agent_type_does_not_unescape_it():
    row = _row(agent_type="a\\_b")
    inner = row[len("○ 🤖 **"):-len("**")]
    assert _unescaped_meta(inner) == []


def _backslash_run_before(text: str, end: int) -> int:
    run = 0
    while end - 1 - run >= 0 and text[end - 1 - run] == "\\":
        run += 1
    return run


def test_an_agent_type_ending_in_a_backslash_opens_its_bold():
    row = _row(agent_type="weird\\")
    assert row.startswith("○ 🤖 **")


def test_an_agent_type_ending_in_a_backslash_ends_with_the_closing_bold():
    row = _row(agent_type="weird\\")
    assert row.endswith("**")


def test_an_agent_type_ending_in_a_backslash_keeps_its_bold_closed():
    """'**weird\\**' (one backslash) would escape the closing star and leak
    the bold; the closing '**' must follow an EVEN run of backslashes, as in
    the correct '**weird\\\\**'."""
    row = _row(agent_type="weird\\")
    assert _backslash_run_before(row, len(row) - 2) % 2 == 0


# ---- direction controls in agent types and tool names: never raw -----------------------

@pytest.mark.parametrize("name", sorted(ALL_CONTROLS))
def test_en_a_control_in_the_agent_type_never_reaches_the_output(name):
    out = render_block(BlockView(agents=(AgentRow(f"ev{ALL_CONTROLS[name]}il", False),)))
    assert ALL_CONTROLS[name] not in out


@pytest.mark.parametrize("name", sorted(ALL_CONTROLS))
def test_en_a_control_in_a_done_agent_type_never_reaches_the_output(name):
    view = BlockView(agents=(AgentRow(f"ev{ALL_CONTROLS[name]}il", True, 5.0),
                             AgentRow("other", False)))
    assert ALL_CONTROLS[name] not in render_block(view)


@pytest.mark.parametrize("name", sorted(ALL_CONTROLS))
def test_en_a_control_in_a_tool_name_never_reaches_the_output(name):
    out = render_block(BlockView(agents=(AgentRow("t", False, tool=f"Do{ALL_CONTROLS[name]}It"),)))
    assert ALL_CONTROLS[name] not in out


@pytest.mark.parametrize("name", sorted(FA_FOREIGN))
def test_fa_a_control_in_the_agent_type_never_reaches_the_output(name):
    view = BlockView(agents=(AgentRow(f"ev{FA_FOREIGN[name]}il", False),))
    assert FA_FOREIGN[name] not in render_block(view, i18n.get("fa"))


@pytest.mark.parametrize("name", sorted(FA_FOREIGN))
def test_fa_a_control_in_a_tool_name_never_reaches_the_output(name):
    view = BlockView(agents=(AgentRow("t", False, tool=f"Do{FA_FOREIGN[name]}It"),))
    assert FA_FOREIGN[name] not in render_block(view, i18n.get("fa"))


@pytest.mark.parametrize("name", ["fsi", "pdi", "rlm"])
def test_fa_an_own_mark_in_the_agent_type_is_not_passed_through(name):
    """The marks fa adds itself: the type's own copy must not add one more."""
    ctl = ALL_CONTROLS[name]
    tr = i18n.get("fa")
    plain = render_block(BlockView(agents=(AgentRow("evil", False),)), tr)
    out = render_block(BlockView(agents=(AgentRow(f"ev{ctl}il", False),)), tr)
    assert out.count(ctl) == plain.count(ctl)


@pytest.mark.parametrize("name", ["fsi", "pdi", "rlm"])
def test_fa_an_own_mark_in_a_tool_name_is_not_passed_through(name):
    ctl = ALL_CONTROLS[name]
    tr = i18n.get("fa")
    plain = render_block(BlockView(agents=(AgentRow("t", False, tool="DoIt"),)), tr)
    out = render_block(BlockView(agents=(AgentRow("t", False, tool=f"Do{ctl}It"),)), tr)
    assert out.count(ctl) == plain.count(ctl)


def test_a_control_in_the_agent_type_is_made_visible_not_dropped():
    row = _row(agent_type="ev\u202eil")
    assert row not in ("○ 🤖 **evil**", "○ 🤖 **ev**", "○ 🤖 **il**")


def test_a_control_in_a_tool_name_is_made_visible_not_dropped():
    assert _activity(_row(tool="Do\u202eIt")) != "Using DoIt"


def test_the_visible_type_keeps_the_letters_around_the_control():
    row = _row(agent_type="ev\u202eil")
    assert re.fullmatch(r"○ 🤖 \*\*ev.+il\*\*", row) is not None


@pytest.mark.parametrize("field", ["prose", "description"])
def test_en_a_control_in_claudes_words_never_reaches_the_output(field):
    row = _row(**{field: "safe\u202etxt.exe words"})
    assert "\u202e" not in row


# ---- through the hook: the delivered answer, en and fa ----------------------------------

def _deliver_via_hooks(run_async, bot, sess, wire, *, agent_type, tool_name=None,
                       tool_input=None, lang=None):
    if lang:
        preferences.set_ui_language(bb.CHAT, lang)
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type=agent_type)
    if tool_name is not None:
        bb.hook(run_async, recv, sess, "PreToolUse", agent_id="a1",
                tool_name=tool_name, tool_input=tool_input or {})
    bb.finish(run_async, bot, sess)
    return bb.block_of(bb.answer_text(wire))


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_hook_a_control_in_the_subagent_type_never_reaches_the_answer(
    bot, sess, wire, run_async, lang,
):
    block = _deliver_via_hooks(run_async, bot, sess, wire,
                               agent_type="Ex\u202eplore", lang=lang)
    assert "\u202e" not in block


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_hook_a_control_in_a_subagent_tool_name_never_reaches_the_answer(
    bot, sess, wire, run_async, lang,
):
    block = _deliver_via_hooks(run_async, bot, sess, wire, agent_type="Explore",
                               tool_name="mcp\u2066tool", tool_input={}, lang=lang)
    assert "\u2066" not in block


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_hook_a_control_in_a_tool_argument_never_reaches_the_answer(
    bot, sess, wire, run_async, lang,
):
    block = _deliver_via_hooks(run_async, bot, sess, wire, agent_type="Explore",
                               tool_name="Read",
                               tool_input={"file_path": "/x/ev\u202ecod.py"}, lang=lang)
    assert "\u202e" not in block


def test_hook_prose_markup_reaches_the_answer_escaped(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.hook(run_async, recv, sess, "MessageDisplay", agent_id="a1",
            delta="Now `code` and **bold** here", message_id="m1", index=0,
            final=False)
    bb.finish(run_async, bot, sess)
    # Drawn as a footer (HTML): Claude's marks show as written, never bold.
    assert "<footer>○ 🤖 <b>Explore</b> · Now `code` and **bold** here</footer>" in \
        bb.answer_raw(wire)


def test_hook_description_markup_reaches_the_answer_escaped(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "Explore", "Run `make` now")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.finish(run_async, bot, sess)
    assert "<footer>○ 🤖 <b>Explore</b> · Run `make` now</footer>" in bb.answer_raw(wire)


# ---- description matching survives a normalised type ------------------------------------

def _launch(run_async, recv, sess, agent_type, description):
    bb.hook(run_async, recv, sess, "PreToolUse", tool_name="Agent",
            tool_input={"subagent_type": agent_type, "description": description,
                        "prompt": "go", "run_in_background": True})


def _matched(run_async, bot, sess, wire, call_type, start_type, desc="The job"):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, call_type, desc)
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type=start_type)
    bb.finish(run_async, bot, sess)
    return bb.block_of(bb.answer_text(wire))


@pytest.mark.parametrize("call_type, start_type", [
    ("Explore", "Explore"),
    ("  Explore  ", "  Explore  "),
    ("Explore\t", "Explore\t"),
    ("Ex\u202eplore", "Ex\u202eplore"),
    ("Ex\u2066plore\u2069", "Ex\u2066plore\u2069"),
    ("Ex\u200eplore", "Ex\u200eplore"),
    ("r" * 81, "r" * 81),
    ("long-" * 20, "long-" * 20),
    ("q" * 200, "q" * 200),
], ids=["plain", "padded-both", "tab-both", "rlo-both", "isolates-both",
        "lrm-both", "81-chars", "100-chars", "200-chars"])
def test_the_same_raw_type_on_both_sides_still_matches(
    bot, sess, wire, run_async, call_type, start_type,
):
    assert "The job" in _matched(run_async, bot, sess, wire, call_type, start_type)


@pytest.mark.parametrize("call_type, start_type", [
    ("  Explore  ", "Explore"),
    ("Explore", " Explore\n"),
], ids=["call-padded", "start-padded"])
def test_surrounding_whitespace_on_one_side_still_matches(
    bot, sess, wire, run_async, call_type, start_type,
):
    assert "The job" in _matched(run_async, bot, sess, wire, call_type, start_type)


def test_a_different_type_still_does_not_match(bot, sess, wire, run_async):
    assert "The job" not in _matched(run_async, bot, sess, wire, "Explore", "Explorer")


def test_a_different_long_type_does_not_match(bot, sess, wire, run_async):
    assert "The job" not in _matched(run_async, bot, sess, wire,
                                     "a" * 40, "b" * 40)


def test_two_calls_that_normalise_alike_are_ambiguous(bot, sess, wire, run_async):
    """'Explore' and ' Explore ' are one type once normalised: two pending
    calls of it give no description (never a wrong one)."""
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "Explore", "First job")
    _launch(run_async, recv, sess, " Explore ", "Second job")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="Explore")
    bb.finish(run_async, bot, sess)
    block = bb.block_of(bb.answer_text(wire))
    assert ("First job" in block, "Second job" in block) == (False, False)


def test_a_whitespace_only_call_type_records_nothing(bot, sess, wire, run_async):
    recv = bb.receiver(bot.registry)
    _launch(run_async, recv, sess, "   ", "Nobody")
    bb.hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
            agent_type="general-purpose")
    bb.finish(run_async, bot, sess)
    assert "Nobody" not in bb.block_of(bb.answer_text(wire))


def test_a_matched_type_with_a_control_still_shows_it_visibly(bot, sess, wire, run_async):
    block = _matched(run_async, bot, sess, wire, "Ex\u202eplore", "Ex\u202eplore")
    assert ("\u202e" in block, "The job" in block) == (False, True)


def test_persian_chat_a_long_type_still_matches(bot, sess, wire, run_async):
    preferences.set_ui_language(bb.CHAT, "fa")
    assert "The job" in _matched(run_async, bot, sess, wire, "z" * 100, "z" * 100)
