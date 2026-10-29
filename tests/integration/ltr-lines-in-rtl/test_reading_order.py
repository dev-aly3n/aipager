"""aipager's own English lines inside a right-to-left rich message read in
their logical order (design.md Part A "Success criteria", Part C).

Judged with tests/bidi_uba.py on the shown lines of the real renderers
(entry points only): ``para=1`` is Telegram Desktop / Web A / Web K, which
lay out every block of an ``is_rtl`` rich message right to left (the
operator's two screenshots, 2026-10-17); ``para=None`` is iOS / Android
(each block's own first strong letter). Alignment is not judged: no app
lets text move an ``is_rtl`` block to the left (design.md Part A).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot import agents_line, animation, held_message
from aipager.bot.notify import finished_line
from tests import bidi_uba


def _load_kit():
    name = "ltr_lines_in_rtl_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
A, V = agents_line.AgentRow, agents_line.BlockView
PARAS = pytest.mark.parametrize("para", [1, None], ids=["desktop-web", "ios-android"])


def _twin():
    return i18n.get("en").directed(True)


def _in_order(line: str, para) -> None:
    assert kit.order_problems(line, para) == [], (line, kit.visual_text(line, para))
    if para is None:
        assert bidi_uba.paragraph_level(line) == 0, line


def _operator_block() -> str:
    """The operator's first screenshot: 8 agents, 6 rows, two of them
    indented under the first, searching the web for Persian words."""
    view = V(agents=(
        A("general-purpose", False,
          description="Geocode remaining villages and rivers, reverse-geocode the long "
                      "list of every place we found in the region today"),
        A("general-purpose", False, tool="WebSearch", tool_arg=kit.FA_RESTAURANT, parent=0),
        A("general-purpose", False, tool="WebSearch", tool_arg=kit.FA_PARK, parent=0),
        A("general-purpose", False, description="Test shab.ir city pages without dates"),
        A("general-purpose", False, description="Fetch OSM bbox data and list shops"),
        A("general-purpose", False, description="Compute sunrise/sunset for Shirgah"),
        A("general-purpose", False),
        A("general-purpose", False)))
    return agents_line.render_block(view, tr=_twin())


# ---- the oracle itself catches the operator's bug ------------------------------------------

def test_the_oracle_sees_the_reported_mirroring_in_base_english():
    """Without marks, Desktop's paragraph level 1 shows "agents working 8 ⏳"."""
    line = "⏳ 8 agents working"
    assert kit.order_problems(line, 1) != []


def test_the_oracle_sees_the_full_stop_jump_in_base_english():
    line = "Results from the agents will follow here."
    assert kit.visual_text(line, 1).startswith(".")


def test_base_english_block_is_mirrored_under_desktop_rules():
    """Base English (no direction) is what the operator saw: a line out of
    order. The twin must fix exactly this."""
    view = V(agents=tuple(A("general-purpose", False) for _ in range(8)))
    text = agents_line.render_block(view, tr=i18n.get("en"))
    assert any(kit.order_problems(ln, 1) for ln in kit.lines(text))


# ---- the agents block (operator screenshot 1) ----------------------------------------------

@PARAS
def test_agents_working_header_reads_in_order(para):
    _in_order(kit.line_with(_operator_block(), "agents working"), para)


def test_agents_working_header_shows_the_hourglass_first():
    line = kit.line_with(_operator_block(), "agents working")
    assert kit.visual_text(line, 1) == "⏳8agentsworking"


@PARAS
def test_a_row_with_a_long_description_reads_in_order(para):
    _in_order(kit.line_with(_operator_block(), "Geocode"), para)


def test_a_truncated_row_ends_with_its_ellipsis_on_the_visual_end():
    line = kit.line_with(_operator_block(), "Geocode")
    shown = kit.visual_text(line, 1)
    assert shown.startswith("○\U0001f916general-purpose·Geocode") and \
        shown.endswith("…"), shown


@PARAS
@pytest.mark.parametrize("persian", ["restaurant", "park"])
def test_an_indented_row_searching_for_persian_reads_in_order(para, persian):
    words = kit.FA_RESTAURANT if persian == "restaurant" else kit.FA_PARK
    _in_order(kit.line_with(_operator_block(), words.split()[0]), para)


def test_the_persian_search_words_stay_one_run_right_after_for():
    line = kit.line_with(_operator_block(), kit.FA_RESTAURANT.split()[0])
    shown = kit.visual_text(line, 1)
    persian = kit.FA_RESTAURANT.replace(" ", "")[::-1]
    assert shown.endswith("Searchingthewebfor" + persian), shown


@PARAS
def test_a_top_level_row_searching_for_persian_reads_in_order(para):
    view = V(agents=(A("general-purpose", False, tool="WebSearch",
                       tool_arg=kit.FA_RESTAURANT),))
    text = agents_line.render_block(view, tr=_twin())
    _in_order(kit.line_with(text, "Searching the web"), para)


def test_an_all_persian_activity_row_keeps_the_agent_before_the_persian():
    view = V(agents=(A("general-purpose", False, prose=kit.FA_RESTAURANT + "."),))
    text = agents_line.render_block(view, tr=_twin())
    line = kit.line_with(text, kit.FA_RESTAURANT.split()[0])
    assert kit.visual_text(line, 1).startswith("○\U0001f916general-purpose·")


@PARAS
def test_an_all_persian_activity_row_reads_in_order(para):
    view = V(agents=(A("general-purpose", False, prose=kit.FA_RESTAURANT + "."),))
    text = agents_line.render_block(view, tr=_twin())
    _in_order(kit.line_with(text, kit.FA_RESTAURANT.split()[0]), para)


@PARAS
def test_plus_more_reads_in_order(para):
    _in_order(kit.line_with(_operator_block(), "more"), para)


def test_plus_more_shows_the_plus_first():
    assert kit.visual_text(kit.line_with(_operator_block(), "more"), 1) == "+2more"


@PARAS
def test_results_will_follow_reads_in_order(para):
    _in_order(kit.line_with(_operator_block(), "Results from the agents"), para)


def test_results_will_follow_ends_with_its_full_stop():
    shown = kit.visual_text(kit.line_with(_operator_block(), "Results from the agents"), 1)
    assert shown == "Resultsfromtheagentswillfollowhere."


@PARAS
@pytest.mark.parametrize("view", [
    V(shells_running=1),
    V(shells_running=2),
    V(shells_finished=1, shells_finished_seconds=7985, shells_stopped=1,
      shells_stopped_seconds=250),
    V(agents=(A("Explore", True, 42),), agents_done_seconds=42),
    V(agents=(A("Explore", True, 42), A("pipeline-runner", False,
                                         description="Running the full suite")),
      shells_running=1),
], ids=["one-shell", "two-shells", "shells-settled", "one-done", "mixed"])
def test_every_line_of_other_block_states_reads_in_order(para, view):
    text = agents_line.render_block(view, tr=_twin())
    for line in kit.lines(text):
        _in_order(line, para)


def test_shells_settled_line_keeps_its_clocks_in_order():
    view = V(shells_finished=1, shells_finished_seconds=7985, shells_stopped=1,
             shells_stopped_seconds=250)
    line = kit.lines(agents_line.render_block(view, tr=_twin()))[0]
    assert kit.visual_text(line, 1) == "1shellfinished(2:13:05)·1shellstopped(4:10)"


@PARAS
def test_every_line_of_the_operator_block_reads_in_order(para):
    for line in kit.lines(_operator_block()):
        _in_order(line, para)


# ---- the busy card (operator screenshot 2) ---------------------------------------------------

def _card(label="x1", *, waiting=False, waiting_agents=0, persian_span=False):
    s = kit.card_session(label, kit.FA_PROSE, waiting_agents=waiting_agents)
    if persian_span:
        s.tool_history.append(("\U0001f916 general-purpose › general-purpose", False))
        s.active_subagents["a3"] = {
            "type": "general-purpose", "card_label": "general-purpose › general-purpose",
            "activity": "WebSearch: " + kit.FA_RESTAURANT,
            "started_at": time.monotonic() - 36 * 60 - 30,
            "history_idx": len(s.tool_history) - 1, "tools": []}
    text, _ok = animation.build_stream_card_ex(s, "Working", tr=i18n.get("en"), rtl=True,
                                               waiting=waiting)
    return text


@PARAS
def test_card_status_line_reads_in_order(para):
    _in_order(kit.line_with(_card(), "Working"), para)


def test_card_status_line_shows_mark_name_verb_clock_cost_in_order():
    shown = kit.visual_text(kit.line_with(_card(), "Working"), 1)
    assert shown.startswith("⏳x1·Working·2:05·$1.21·Read×2"), shown


@PARAS
def test_card_status_line_with_a_persian_session_name_reads_in_order(para):
    _in_order(kit.line_with(_card(kit.FA_NAME), "Working"), para)


def test_card_status_line_keeps_the_persian_name_in_its_slot_after_the_hourglass():
    shown = kit.visual_text(kit.line_with(_card(kit.FA_NAME), "Working"), 1)
    assert shown.startswith("⏳" + kit.FA_NAME[::-1] + "·Working·"), shown


@PARAS
@pytest.mark.parametrize("needle,want", [
    ("43 tool calls", "✅\U0001f916general-purpose·43toolcalls·17:38"),
    ("Dump listing", "⏳\U0001f916general-purpose·Bash:Dumplistingdetailsandreviews"
                     "·38:30"),
    ("local dairy", "⏳\U0001f916general-purpose›general-purpose·Bash:Search"
                    "forlocaldairy,products,breadnearthevillage·37:00"),
    ("SendMessage", "✅SendMessage"),
    ("npm run build", "❌Bash:npmrunbuild"),
    ("a.md", "✅Read:/home/alice/a.md"),
], ids=["agent-done", "agent-live", "agent-nested", "tool-sendmessage", "tool-failed",
        "tool-read"])
def test_card_rows_show_their_mark_first_then_the_row_in_order(para, needle, want):
    line = kit.line_with(_card(), needle, without="Working")
    _in_order(line, para)
    assert kit.visual_text(line, para) == want


@PARAS
def test_card_row_with_a_persian_file_name_reads_in_order(para):
    line = kit.line_with(_card(), kit.FA_NAME + ".md")
    _in_order(line, para)


def test_card_row_with_a_persian_file_name_keeps_the_check_first():
    line = kit.line_with(_card(), kit.FA_NAME + ".md")
    assert kit.visual_text(line, 1).startswith("✅Read:/home/alice/")


@PARAS
def test_card_waiting_line_reads_in_order(para):
    _in_order(kit.line_with(_card("frontend", waiting=True, waiting_agents=3),
                            "waiting for"), para)


def test_card_waiting_line_shows_the_arrows_first_and_the_clock_last():
    line = kit.line_with(_card("frontend", waiting=True, waiting_agents=3), "waiting for")
    shown = kit.visual_text(line, 1)
    assert shown == "\U0001f504frontend·waitingfor5agents·2:05", shown


def test_card_waiting_line_with_a_persian_name_keeps_the_name_after_the_arrows():
    line = kit.line_with(_card(kit.FA_NAME, waiting=True, waiting_agents=3), "waiting for")
    assert kit.visual_text(line, 1).startswith("\U0001f504" + kit.FA_NAME[::-1] + "·")


@PARAS
def test_card_waiting_line_with_a_persian_name_reads_in_order(para):
    line = kit.line_with(_card(kit.FA_NAME, waiting=True, waiting_agents=3), "waiting for")
    _in_order(line, para)


def test_card_row_with_persian_inside_its_code_span_keeps_its_left_part_in_order():
    """Known limit (design.md Part C): no isolate can go inside a code span,
    so the Persian run and the neutrals beside it may move under
    paragraph level 1. Only the part before the Persian is asserted: the
    hourglass first, then the agent chain and the tool name in order."""
    line = kit.line_with(_card(persian_span=True), "WebSearch")
    shown = kit.visual_text(line, 1)
    assert shown.startswith("⏳\U0001f916general-purpose›general-purpose·WebSearch"), \
        shown


def test_card_prose_quote_is_claudes_and_carries_no_mark():
    text = _card()
    quote = [ln for ln in text.split("\n") if kit.FA_PROSE in ln]
    assert quote and all(kit.LRM not in ln and kit.FSI not in ln for ln in quote)


# ---- result, Finished and late-marker lines ------------------------------------------------

@PARAS
def test_finished_line_reads_in_order(para):
    line = str(finished_line(_twin(), "\U0001f4ac **x1**", "md", seconds=250, added=10,
                             removed=5))
    _in_order(kit.lines(line)[0], para)


def test_finished_line_shows_glyph_name_then_the_clock_and_counts():
    line = str(finished_line(_twin(), "\U0001f4ac **x1**", "md", seconds=250, added=10,
                             removed=5))
    assert kit.visual_text(kit.lines(line)[0], 1) == \
        "\U0001f4acx1·Finished(4:10,+10-5)"


@PARAS
def test_late_marker_reads_in_order(para):
    line = str(held_message.late_marker_text(_twin(), 180))
    _in_order(kit.lines(line)[0], para)


def test_late_marker_shows_the_hourglass_first_and_the_bracket_last():
    shown = kit.visual_text(str(held_message.late_marker_text(_twin(), 180)), 1)
    assert shown == "⏳deliveredlate(held3minduringaTelegramratelimit)", shown


@PARAS
def test_fold_summary_reads_in_order(para):
    _in_order(kit.lines(str(_twin().n(animation.FOLD, 5, kind="md")))[0], para)
