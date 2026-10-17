"""Reading order of an English chat's own lines inside a right-to-left
rich message, judged with the Unicode Bidirectional Algorithm
(:mod:`tests.bidi_uba`).

``para=1`` models Telegram Desktop, Web A and Web K, which lay out every
block of an ``is_rtl`` rich message right to left whatever its text
(the operator's screenshots, 2026-10-17). ``para=None`` (P2/P3, the first
strong character) models iPhone and Android, where each block takes its
own direction: there every line keeps paragraph level 0. Alignment is
not judged: every app puts an ``is_rtl`` block on the right, and no text
can change that.

"Reads in order" here: outside the isolated parameters every shown
character is at an even level and appears in its logical order; each
isolate stays one contiguous run in its logical place; every
left-to-right letter reads left to right, every right-to-left word right
to left.
"""

from __future__ import annotations

import unicodedata

import pytest

from aipager import i18n
from aipager.bot import agents_line, animation, held_message
from aipager.bot.notify import _result_line_md, finished_line
from aipager.i18n import Safe
from aipager.state import SessionRegistry, Status
from tests import i18n_surfaces as surf
from tests.bidi_uba import bidi_class, levels, paragraph_level, shown_text, visual_order
from tests.footer_text import status_md

LRM, FSI, PDI = "\u200e", "\u2068", "\u2069"
ISO_OPEN = frozenset("\u2066\u2067\u2068")
FA = "رستوران ماهی سد"
FA_NAME = "سفر"
FA_FILE = "گزارش"


def _visible(ch: str) -> bool:
    return not (ch.isspace() or unicodedata.category(ch) in ("Cf", "Mn", "Me")
                or ch in ISO_OPEN or ch == PDI)


def _rtl_letter(ch: str) -> bool:
    return bidi_class(ch) in ("R", "AL") and unicodedata.category(ch).startswith("L")


def _units(line: str) -> list[int | None]:
    """Each character's unit: an isolate (with all it holds) is one unit,
    every other visible character its own; None for what is not shown."""
    out: list[int | None] = []
    depth = 0
    unit = -1
    for i, ch in enumerate(line):
        if ch in ISO_OPEN:
            if depth == 0:
                unit = i
            depth += 1
            out.append(None)
            continue
        if ch == PDI and depth:
            depth -= 1
            out.append(None)
            continue
        if not _visible(ch):
            out.append(None)
            continue
        out.append(unit if depth else i)
    return out


def reads_in_order(line: str, para: int | None = 1) -> bool:
    lv = levels(line, para)
    units = _units(line)
    seen: list[int] = []
    for i in visual_order(line, para):
        u = units[i]
        if u is None:
            continue
        if not seen or seen[-1] != u:
            if u in seen:
                return False          # an isolate split in two
            seen.append(u)
    if seen != sorted(seen):
        return False
    for i, ch in enumerate(line):
        if units[i] is not None and units[i] == i and lv[i] % 2:
            return False              # outside the isolates, at an odd level
    return _letters_in_order(line, para)


def _letters_in_order(line: str, para: int | None) -> bool:
    pos = {i: k for k, i in enumerate(visual_order(line, para))}
    latin = [pos[i] for i, ch in enumerate(line) if bidi_class(ch) == "L" and _visible(ch)]
    if latin != sorted(latin):
        return False
    word: list[int] = []
    for i, ch in enumerate(line + " "):
        if i < len(line) and _rtl_letter(ch):
            word.append(pos[i])
            continue
        if word != sorted(word, reverse=True):
            return False
        word = []
    return True


def visual(line: str, para: int | None = 1) -> str:
    return "".join(line[i] for i in visual_order(line, para) if _visible(line[i]))


def shown(md: str) -> str:
    """One Markdown line as the app shows it: no ``**``/backticks, no list
    marker (Telegram draws the bullet itself)."""
    text = shown_text(md)
    return text[2:] if text.startswith("- ") else text


def _judge(md: str) -> str:
    line = shown(md)
    assert reads_in_order(line, 1), (visual(line), line)
    assert paragraph_level(line) == 0 and reads_in_order(line, None), line
    return visual(line)


@pytest.fixture
def tr():
    return i18n.get("en").directed(True)


# ---- the agents block under the answer --------------------------------------------------

def test_agents_working_reads_in_order(tr):
    head = agents_line._header(agents_line.BlockView(
        agents=tuple(agents_line.AgentRow("general-purpose", False) for _ in range(8))), tr)
    assert _judge(head) == "⏳8agentsworking"
    # The bug, without the twin: "agents working 8 ⏳".
    plain = shown(agents_line._header(agents_line.BlockView(
        agents=tuple(agents_line.AgentRow("general-purpose", False) for _ in range(8))),
        i18n.get("en")))
    assert not reads_in_order(plain, 1)


@pytest.mark.parametrize("item", [False, True], ids=["row", "item"])
def test_a_row_with_a_persian_search_reads_in_order(tr, item):
    row = agents_line.AgentRow("general-purpose", False, tool="WebSearch",
                               tool_arg=FA + " " + FA * 6)
    md = agents_line._row(row, tr, item=item)
    if item:
        md = agents_line.LIST_ITEM + md
    seen = _judge(md)
    assert "…" in md and seen.endswith("…")
    line = shown(md)
    # The Persian stays one run, right after "for".
    vis = visual(line)
    assert vis.index("for") < vis.index(FA.split(" ")[0][::-1])


def test_a_row_whose_activity_is_persian_prose_shows_the_agent_first(tr):
    md = agents_line._row(agents_line.AgentRow("general-purpose", False,
                                               prose=FA + "."), tr)
    seen = _judge(md)
    assert seen.startswith("○\U0001f916general-purpose·")


def test_a_cut_english_row_ends_with_its_ellipsis(tr):
    prose = ("Geocode remaining villages and rivers, reverse-geocode the rest of "
             "the points along the coast and the hills")
    md = agents_line._row(agents_line.AgentRow("general-purpose", False, prose=prose), tr)
    seen = _judge(md)
    assert seen.startswith("○\U0001f916general-purpose·Geocode")
    assert seen.endswith("…")


def test_more_reads_in_order(tr):
    assert _judge(tr.n(agents_line.MORE, 2, kind="md")) == "+2more"


def test_the_promise_ends_with_its_full_stop(tr):
    assert _judge(tr.md(agents_line.PROMISE)) == "Resultsfromtheagentswillfollowhere."
    assert not reads_in_order(shown(i18n.get("en").md(agents_line.PROMISE)), 1)


def test_the_shell_lines_read_in_order(tr):
    head = agents_line._header(agents_line.BlockView(shells_running=1), tr)
    assert _judge(head).startswith("\U0001f5a5")
    head = agents_line._header(agents_line.BlockView(
        shells_finished=1, shells_finished_seconds=7985,
        shells_stopped=1, shells_stopped_seconds=250), tr)
    seen = _judge(head)
    assert "2:13:05" in shown(head) and seen.endswith("4:10)")


# ---- the answer's first line and the late marker --------------------------------------

@pytest.mark.parametrize("label", ["x1", FA_NAME], ids=["latin", "persian"])
def test_the_result_line_reads_in_order(tr, label):
    seen = _judge(_result_line_md(label, tr))
    assert seen.startswith("\U0001f4ac")


@pytest.mark.parametrize("label", ["x1", FA_NAME], ids=["latin", "persian"])
def test_the_finished_line_reads_in_order(tr, label):
    line = finished_line(tr, _result_line_md(label, tr), "md", seconds=250, added=10,
                         removed=5)
    seen = _judge(line)
    assert seen.startswith("\U0001f4ac") and seen.endswith("(4:10,+10-5)")


def test_the_late_marker_reads_in_order(tr):
    seen = _judge(held_message.late_marker_text(tr, 180))
    assert seen.startswith("⏳deliveredlate") and seen.endswith(")")


# ---- the busy card -------------------------------------------------------------------

def _card_session(label="x1"):
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x1")
    s.label = label
    s.status = Status.BUSY
    s.busy_started_at = surf.NOW - 125
    s.cost_baseline, s.last_cost_usd = 0.0, 1.21
    s.tool_history = [("Read: /a", True), ("Read: /b", True)]
    s.stream_commentary = [(0, FA + ".")]
    return s


@pytest.fixture
def frozen(monkeypatch):
    surf.Env(None, None, monkeypatch, "en")


@pytest.mark.parametrize("label", ["x1", FA_NAME], ids=["latin", "persian"])
def test_the_card_status_line_reads_in_order(frozen, label):
    card, _ = animation.build_stream_card_ex(_card_session(label), "Working", rtl=True)
    status = status_md(card)
    seen = _judge(status)
    assert seen.startswith("⏳") and seen.endswith("Read×2")
    if label == "x1":
        assert seen == "⏳x1·Working·2:05·$1.21·Read×2"
    else:
        assert seen.startswith("⏳" + FA_NAME[::-1] + "·Working")


def _rows(card: str) -> list[str]:
    return [line for line in card.split("\n") if line.strip()
            and line.lstrip(LRM)[:1] in ("✅", "⏳", "❌", "⏹")]


def test_the_operators_card_rows_read_in_order(frozen):
    s = _card_session("safar")
    s.tool_history = [
        ("\U0001f916 general-purpose · 43 tool calls · 17:38", True),
        ("\U0001f916 general-purpose", False),
        ("\U0001f916 general-purpose", False),
        ("SendMessage", True),
    ]
    s.active_subagents = {
        "a": {"type": "general-purpose", "history_idx": 1,
              "activity": "Bash: Dump listing details and reviews",
              "started_at": surf.NOW - (38 * 60 + 30), "tools": []},
        "b": {"type": "general-purpose", "card_label": "general-purpose › general-purpose",
              "history_idx": 2,
              "activity": "Bash: Search for local dairy, products, bread near the village",
              "started_at": surf.NOW - 37 * 60, "tools": []},
    }
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    seen = [_judge(row) for row in _rows(card)]
    assert seen == [
        "✅\U0001f916general-purpose·43toolcalls·17:38",
        "⏳\U0001f916general-purpose·Bash:Dumplistingdetailsandreviews·38:30",
        "⏳\U0001f916general-purpose›general-purpose·Bash:Searchforlocal"
        "dairy,products,breadnearthevillage·37:00",
        "✅SendMessage",
    ]


def test_the_waiting_line_reads_in_order(frozen):
    s = _card_session("safar")
    s.busy_started_at = surf.NOW - (39 * 60 + 30)
    s.active_subagents = {f"a{i}": {"type": "general-purpose", "started_at": surf.NOW}
                          for i in range(5)}
    card, _ = animation.build_stream_card_ex(s, "Working", waiting=True, rtl=True)
    status = status_md(card)
    assert "**" in status and "safar" in status
    seen = _judge(status)
    assert seen == "\U0001f504safar·5agents(general-purpose)stillworking·39:30"


def test_the_waiting_line_with_a_persian_name_keeps_the_mark_first(frozen):
    s = _card_session(FA_NAME)
    s.active_subagents = {"a": {"type": "general-purpose", "started_at": surf.NOW}}
    card, _ = animation.build_stream_card_ex(s, "Working", waiting=True, rtl=True)
    seen = _judge(status_md(card))
    assert seen.startswith("\U0001f504" + FA_NAME[::-1] + "·")


def test_a_live_agent_and_a_read_row_read_in_order(frozen):
    s = _card_session()
    s.tool_history = [("Read: /home/alice/notes.md", True), ("\U0001f916 general-purpose", False)]
    s.active_subagents = {"a": {"type": "general-purpose", "history_idx": 1,
                                "activity": "Grep: todo", "started_at": surf.NOW - 45,
                                "tools": []}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    seen = [_judge(row) for row in _rows(card)]
    assert seen == ["✅Read:/home/alice/notes.md",
                    "⏳\U0001f916general-purpose·Grep:todo·0:45"]


def test_a_persian_word_inside_a_code_span_reads_in_its_place(frozen):
    # Nothing may go inside a code span, so a Persian run in one cannot
    # get an isolate of its own. The row's whole span is isolated from
    # outside instead: the ⏳ stays first, the agent and the tool read in
    # order, the colon stays after the tool and the Persian reads right
    # to left in its place. Known limit (UBA W7/N1): a number right after
    # the span's last Persian word (" · 36:30") joins that word's run, so
    # the clock shows before it. Only an isolate inside the span could
    # stop that, and nothing goes inside a code span.
    s = _card_session()
    s.tool_history = [("\U0001f916 general-purpose", False)]
    s.active_subagents = {"a": {
        "type": "general-purpose", "card_label": "general-purpose \u203a general-purpose",
        "history_idx": 0, "activity": "WebSearch: " + FA + " OR " + FA,
        "started_at": surf.NOW - (36 * 60 + 30), "tools": []}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    (row,) = _rows(card)
    assert "`" + FSI not in row and FSI + "`" in row
    line = shown(row)
    seen = visual(line)
    persian = FA.replace(" ", "")[::-1]
    assert seen.startswith("\u23f3\U0001f916general-purpose\u203ageneral-purpose"
                           "\u00b7WebSearch:" + persian + "OR")
    assert seen.endswith(persian) and "36:30" in seen
    assert paragraph_level(line) == 0
    # Without the twin the ⏳ is not first.
    plain, _ = animation.build_stream_card_ex(s, "Working", rtl=False)
    assert not visual(shown(_rows(plain)[0])).startswith("\u23f3")


def test_a_read_row_with_a_persian_file_name_reads_in_order(frozen):
    s = _card_session()
    s.tool_history = [(f"Read: /home/alice/{FA_FILE}.md", True)]
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    (row,) = _rows(card)
    seen = _judge(row)
    assert seen == "\u2705Read:/home/alice/" + FA_FILE[::-1] + ".md"


def test_an_agents_own_tool_rows_read_in_order(frozen):
    s = _card_session()
    s.tool_history = [("\U0001f916 general-purpose", False)]
    s.active_subagents = {"a": {"type": "general-purpose", "history_idx": 0,
                                "activity": "Read: /a", "started_at": surf.NOW - 5,
                                "tools": ["SendMessage", f"Read: /home/alice/{FA_FILE}.md"]}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    rows = [_judge(row) for row in _rows(card)]
    assert rows[1:] == ["\u2705SendMessage", "\u2705Read:/home/alice/" + FA_FILE[::-1] + ".md"]


def test_the_fold_summary_reads_in_order(tr):
    _judge(animation._tool_fold_summary(5, tr))
    assert _judge(tr.n(animation.FOLD, 5, kind="md")).startswith("▸" + "5")


def test_the_plain_card_frames_are_judged_on_their_own(tr):
    # Without the twin a status line is the bug: the clock and ⏳ move.
    en = i18n.get("en")
    line = en.md(animation.CARD_STATUS, mark=Safe("⏳"), session="x1",
                 verb=Safe(animation._verb(en, "Working"))) + " · 2:05"
    assert not reads_in_order(shown(line), 1)
    marked = tr.line(tr.md(animation.CARD_STATUS, mark=Safe("⏳"), session="x1",
                           verb=Safe(animation._verb(tr, "Working"))) + " · 2:05",
                     kind="md")
    assert reads_in_order(shown(marked), 1)


def test_a_live_shell_row_reads_in_order(frozen):
    s = _card_session()
    s.tool_history = [("Bash: npm run dev", False),
                      ("shell: npm test - stopped (0:12)", "stopped")]
    s.bg_shells = {"t1": {"label": "npm run dev", "summary": "Bash: npm run dev",
                          "started": surf.NOW - 75, "in_job": True, "history_idx": 0}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    seen = [_judge(row) for row in _rows(card)]
    assert seen == ["⏳shell:npmrundev(1:15)",
                    "⏹shell:npmtest-stopped(0:12)"]
    # Without the twin the live shell row is the bug: its clock moves.
    plain, _ = animation.build_stream_card_ex(s, "Working", rtl=False)
    assert not reads_in_order(shown(_rows(plain)[0]), 1)


def test_a_live_shell_row_with_a_persian_label_keeps_the_mark_first(frozen):
    s = _card_session()
    s.tool_history = [("Bash: " + FA_FILE, False)]
    s.bg_shells = {"t1": {"label": FA_FILE, "summary": "Bash: " + FA_FILE,
                          "started": surf.NOW - 75, "in_job": True, "history_idx": 0}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    (row,) = _rows(card)
    assert visual(shown(row)).startswith("⏳shell:")


@pytest.mark.parametrize("label", ["x1", FA_NAME], ids=["latin", "persian"])
def test_the_paused_line_reads_in_order(tr, label):
    seen = _judge(tr.md(animation.PAUSED_MD, session=label))
    assert seen.startswith("⏳")
    assert seen.endswith("working(updatespaused)")
    if label == FA_NAME:
        assert seen.startswith("⏳" + FA_NAME[::-1] + "·")


@pytest.mark.parametrize("sender", ["claude-alice", "claude-" + FA_NAME],
                         ids=["latin", "persian"])
def test_the_peer_line_on_the_card_reads_in_order(frozen, sender):
    s = _card_session()
    s.peer_from = {"status": "verified", "sender_session": sender,
                   "driver": {"labels": ["@alice"]}}
    card, _ = animation.build_stream_card_ex(s, "Working", rtl=True)
    first = card.split("\n")[0]
    seen = _judge(first)
    name = sender[len("claude-"):]
    shown_name = name if name.isascii() else name[::-1]
    assert seen == "\U0001f4e8from" + shown_name + "(@alice)"
