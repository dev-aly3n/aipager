"""Where the divider goes, per message type (design.md success criteria
1, 10, 11; entrypoints.md "Divider placement per message type").

Answers go out through ``notify("idle_prompt")`` and are read from the
recorded Telegram calls; the card is read from ``build_stream_card_ex``,
the renderer the card's rich sends carry."""

from __future__ import annotations

import time

import pytest

from aipager import preferences as prefs
from aipager.bot.animation import build_stream_card_ex
from aipager.bot.divider import DIVIDER_SEP
from aipager.state import Status, TrackedSession
from tests import answer_status_bb as bb
from tests.footer_text import as_md

A1 = "a505c081ca3926ed5"
A2 = "ac1d54c85d78f59ea"
DASHES = "\u2015" * 13
SECOND = "Here is the second answer, after the agents woke the session."


def _agents(sess):
    now = time.monotonic()
    sess.bg_agent_started(A1, "pipeline-runner", now)
    sess.bg_agent_started(A2, "ship-tester", now)
    sess.agent_launched_child(A1, A2, now)


def _rich_with(wire, text):
    return [bb.md(p) for m, p in wire.calls
            if m in ("sendRichMessage", "editMessageText") and "rich_message" in p
            and text in bb.md(p)]


# ---- answers ------------------------------------------------------------------------------

def test_an_answer_without_background_work_has_no_divider(bot, sess, wire, run_async):
    bb.finish(run_async, bot, sess)
    assert bb.answer_text(wire) == f"\U0001f4ac **bbx**\n\n{bb.ANSWER}"


def test_an_answer_with_agents_is_body_divider_block(bot, sess, wire, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    text = bb.answer_text(wire)
    assert text.startswith(f"\U0001f4ac **bbx**\n\n{bb.ANSWER}{DIVIDER_SEP}\u23f3 2 agents working\n\n")


def test_an_answer_with_agents_has_exactly_one_divider(bot, sess, wire, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    assert bb.answer_text(wire).split("\n").count("---") == 1


def test_an_answer_with_agents_carries_the_tree(bot, sess, wire, run_async, shapes):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    assert shapes.tree(bb.block_of(bb.answer_text(wire))) == \
        [(1, "pipeline-runner"), (2, "ship-tester")]


def test_an_answer_with_a_shell_only(bot, sess, wire, run_async):
    sess.bg_shell_started("b1", "npm run dev", "Bash: npm run dev", time.monotonic())
    bb.finish(run_async, bot, sess)
    assert bb.answer_text(wire).endswith(f"{bb.ANSWER}{DIVIDER_SEP}\U0001f5a5 1 shell running")


@pytest.mark.parametrize("layout", ["card", "replace"])
def test_the_divider_follows_the_answer_in_each_layout(bot, sess, wire, run_async, layout):
    prefs.set_preference(bb.CHAT, "layout", layout)
    _agents(sess)
    bb.finish(run_async, bot, sess)
    assert f"{bb.ANSWER}{DIVIDER_SEP}\u23f3 2 agents working" in bb.answer_text(wire)


def test_a_later_answer_in_the_open_job_carries_divider_and_block(bot, sess, wire, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    bb.finish(run_async, bot, sess, SECOND)
    (text,) = _rich_with(wire, SECOND)[:1]
    assert f"{SECOND}{DIVIDER_SEP}\u23f3 2 agents working" in text


# ---- merged --------------------------------------------------------------------------------

@pytest.fixture
def merged(bot, sess, wire):
    prefs.set_preference(bb.CHAT, "layout", "merged")
    return sess


def _merged_text(wire):
    texts = [bb.md(p) for m, p in wire.calls if m == "editMessageText"
             and "rich_message" in p and bb.ANSWER in bb.md(p)]
    assert texts, wire.calls
    return texts[0]


def test_merged_without_agents_has_card_and_answer_dividers(bot, merged, wire, run_async):
    bb.finish(run_async, bot, merged)
    parts = _merged_text(wire).split(DIVIDER_SEP)
    assert (len(parts), parts[1].startswith("\u2705 **bbx**"),
            parts[2]) == (3, True, f"\U0001f4ac **bbx**\n\n{bb.ANSWER}")


def test_merged_with_agents_has_three_dividers_in_order(bot, merged, wire, run_async):
    _agents(merged)
    bb.finish(run_async, bot, merged)
    parts = _merged_text(wire).split(DIVIDER_SEP)
    assert (len(parts), parts[1].startswith("\u2705 **bbx**"),
            parts[2] == f"\U0001f4ac **bbx**\n\n{bb.ANSWER}",
            parts[3].startswith("\u23f3 2 agents working")) == (4, True, True, True)


def test_merged_status_line_is_one_paragraph(bot, merged, wire, run_async):
    bb.finish(run_async, bot, merged)
    status = _merged_text(wire).split(DIVIDER_SEP)[1]
    assert "\n" not in status


def test_merged_has_no_dash_row(bot, merged, wire, run_async):
    _agents(merged)
    bb.finish(run_async, bot, merged)
    assert DASHES not in _merged_text(wire)


def test_merged_timeline_comes_first(bot, merged, wire, run_async):
    bb.finish(run_async, bot, merged)
    assert _merged_text(wire).split(DIVIDER_SEP)[0].strip() != ""


# ---- the busy card -----------------------------------------------------------------------------

FRAMES = [({}, "\u23f3"), ({"final": True}, "\u2705"), ({"waiting": True}, "\U0001f504")]
FRAME_IDS = ["live", "final", "waiting"]


def _card_sess(rows=None):
    s = TrackedSession(name="claude-frontend", label="frontend", status=Status.BUSY)
    s.busy_started_at = time.monotonic() - 45
    s.tool_history = list(rows if rows is not None else [("Bash: ls", True), ("Read: /a.py", True)])
    return s


@pytest.mark.parametrize("kw,sigil", FRAMES, ids=FRAME_IDS)
def test_a_card_ends_with_divider_then_status(kw, sigil):
    card, _ = build_stream_card_ex(_card_sess(), "Working", **kw)
    body, _, status = card.rpartition(DIVIDER_SEP)
    assert (body != "", as_md(status).startswith(f"{sigil} **frontend**"), "\n" in status) == \
        (True, True, False)


@pytest.mark.parametrize("kw,sigil", FRAMES, ids=FRAME_IDS)
def test_a_card_has_one_divider(kw, sigil):
    card, _ = build_stream_card_ex(_card_sess(), "Working", **kw)
    assert card.split("\n").count("---") == 1


@pytest.mark.parametrize("kw,sigil", FRAMES, ids=FRAME_IDS)
def test_an_empty_card_is_its_status_line_alone(kw, sigil):
    card, _ = build_stream_card_ex(_card_sess([]), "Working", **kw)
    assert ("---" in card.split("\n"), as_md(card).startswith(f"{sigil} **frontend**")) == (
        False, True)


@pytest.mark.parametrize("kw,sigil", FRAMES, ids=FRAME_IDS)
def test_a_card_with_folds_still_ends_divider_status(kw, sigil):
    s = _card_sess([(f"Bash: step-{i}", True) for i in range(20)])
    s.stream_commentary = [(0, "Phase one."), (6, "Phase two."), (12, "Phase three.")]
    card, _ = build_stream_card_ex(s, "Working", **kw)
    assert as_md(card.rpartition(DIVIDER_SEP)[2]).startswith(f"{sigil} **frontend**")


def test_a_card_with_a_helper_row_ends_divider_status():
    s = _card_sess()
    s.tool_history.append(("\U0001f916 pipeline-runner \u203a ship-tester", False))
    s.active_subagents["ac1d54c85d78f59ea"] = {
        "type": "ship-tester", "card_label": "pipeline-runner \u203a ship-tester",
        "activity": "Read: /a.py", "started_at": time.monotonic() - 12,
        "history_idx": len(s.tool_history) - 1, "tools": ["Read: /a.py"]}
    card, _ = build_stream_card_ex(s, "Working")
    assert as_md(card.rpartition(DIVIDER_SEP)[2]).startswith("\u23f3 **frontend**")


def test_the_kept_card_in_the_card_layout_ends_divider_status(bot, sess, wire, run_async):
    bb.finish(run_async, bot, sess)
    cards = [bb.md(p) for m, p in wire.calls if m == "editMessageText"
             and "rich_message" in p and p.get("message_id") == bb.CARD]
    assert cards and cards[-1].rpartition(DIVIDER_SEP)[2].startswith("\u2705 **bbx**")
