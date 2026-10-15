"""Card and answer budgets with the divider and the tree (design.md
success criteria 10 and 14; entrypoints.md "Budgets")."""

from __future__ import annotations

import time

import pytest

from aipager import i18n, preferences
from aipager.bot.agents_line import reserve_bytes
from aipager.bot.animation import build_stream_card_ex
from aipager.bot.divider import DIVIDER_SEP
from aipager.state import Status, TrackedSession
from tests import answer_status_bb as bb
from tests.footer_text import as_md

CARD_CHARS = 8800
RICH_BYTES = 32768
FRAMES = [{}, {"final": True}, {"waiting": True}]
FRAME_IDS = ["live", "final", "waiting"]


def _card(n, row, kw, chat=None):
    s = TrackedSession(name="claude-frontend", label="frontend", status=Status.BUSY)
    s.busy_started_at = time.monotonic() - 45
    if chat is not None:
        s.scope_chat_id = chat
        s.scope_kind = "dm"
    s.tool_history = [(f"{row}{i}", True) for i in range(n)]
    card, _ = build_stream_card_ex(s, "Working", **kw)
    return card


ROWS = {
    "ascii": "Bash: " + "x" * 120 + " ",
    "four-byte": "Bash: " + "\U0001d538" * 120 + " ",
    "persian": "Bash: " + "\u0641\u0627\u06cc\u0644 " * 30,
}


@pytest.mark.parametrize("kw", FRAMES, ids=FRAME_IDS)
@pytest.mark.parametrize("row", sorted(ROWS))
@pytest.mark.parametrize("n", [1, 40, 60, 70, 80, 120, 400], ids=lambda n: f"n{n}")
def test_a_card_stays_within_the_char_ceiling(n, row, kw):
    assert len(_card(n, ROWS[row], kw)) <= CARD_CHARS


@pytest.mark.parametrize("kw", FRAMES, ids=FRAME_IDS)
@pytest.mark.parametrize("row", sorted(ROWS))
@pytest.mark.parametrize("n", [1, 40, 60, 70, 80, 120, 400], ids=lambda n: f"n{n}")
def test_a_card_stays_within_the_byte_ceiling(n, row, kw):
    assert len(_card(n, ROWS[row], kw).encode("utf-8")) <= RICH_BYTES


@pytest.mark.parametrize("kw", FRAMES, ids=FRAME_IDS)
@pytest.mark.parametrize("row", sorted(ROWS))
@pytest.mark.parametrize("n", [40, 70, 400], ids=lambda n: f"n{n}")
def test_a_card_at_the_budget_edge_keeps_its_divider_and_status(n, row, kw):
    card = _card(n, ROWS[row], kw)
    assert as_md(card.rpartition(DIVIDER_SEP)[2]).split(" ", 1)[1].startswith("**frontend**")


def test_the_char_sweep_reaches_the_ceiling():
    """The sweep above is not vacuous: some card sits near the ceiling."""
    longest = max(len(_card(n, ROWS["ascii"], {})) for n in range(55, 90))
    assert longest > CARD_CHARS - 400


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("kw", FRAMES, ids=FRAME_IDS)
def test_a_right_to_left_card_stays_within_both_ceilings(code, kw):
    preferences.set_ui_language(4242, code)
    card = _card(400, ROWS["persian"], kw, chat=4242)
    assert (len(card) <= CARD_CHARS, len(card.encode("utf-8")) <= RICH_BYTES) == (True, True)


@pytest.mark.parametrize("code", ["fa", "ar"])
@pytest.mark.parametrize("kw", FRAMES, ids=FRAME_IDS)
def test_a_right_to_left_card_divider_is_bare(code, kw):
    preferences.set_ui_language(4242, code)
    card = _card(30, ROWS["persian"], kw, chat=4242)
    assert DIVIDER_SEP in card


# ---- the answer reserve -------------------------------------------------------------------

def _sizes():
    r = reserve_bytes()
    # The text before the block is the lead ("\U0001f4ac **bbx**\n\n") plus
    # the answer; aim around the point where the reserve starts to bite.
    lead = len("\U0001f4ac **bbx**\n\n".encode())
    edge = RICH_BYTES - r - lead
    return [edge - 200, edge - 1, edge, edge + 1, edge + 200, RICH_BYTES - lead - 10]


SIZES = list(range(6))


def _deliver_big(bot, sess, wire, run_async, size):
    now = time.monotonic()
    sess.bg_agent_started("a505c081ca3926ed5", "pipeline-runner", now)
    text = "x" * size
    bb.finish(run_async, bot, sess, text)
    return [bb.md(p) for m, p in wire.calls
            if m in ("sendRichMessage", "editMessageText") and "rich_message" in p
            and DIVIDER_SEP in bb.md(p) and text[:50] in bb.md(p)]


@pytest.mark.parametrize("i", SIZES, ids=["under", "edge-1", "edge", "edge+1", "over", "full"])
def test_a_sent_block_leaves_the_reserve(bot, sess, wire, run_async, i):
    sent = _deliver_big(bot, sess, wire, run_async, _sizes()[i])
    bad = [len(t[:t.rindex(DIVIDER_SEP)].encode()) + reserve_bytes()
           for t in sent if len(t[:t.rindex(DIVIDER_SEP)].encode()) + reserve_bytes() > RICH_BYTES]
    assert bad == []


@pytest.mark.parametrize("i", SIZES, ids=["under", "edge-1", "edge", "edge+1", "over", "full"])
def test_no_rich_send_exceeds_the_byte_limit(bot, sess, wire, run_async, i):
    _deliver_big(bot, sess, wire, run_async, _sizes()[i])
    sizes = [len(bb.md(p).encode()) for m, p in wire.calls
             if m in ("sendRichMessage", "editMessageText") and "rich_message" in p]
    assert max(sizes) <= RICH_BYTES


def test_an_answer_under_the_edge_carries_the_block(bot, sess, wire, run_async):
    assert _deliver_big(bot, sess, wire, run_async, _sizes()[0])


@pytest.mark.parametrize("code", ["fa", "ar", "de"])
def test_a_translated_sent_block_leaves_its_reserve(bot, sess, wire, run_async, code):
    preferences.set_ui_language(bb.CHAT, code)
    tr = i18n.get(code).directed(False)
    r = max(reserve_bytes(tr), reserve_bytes(i18n.get(code)))
    lead = len("\U0001f4ac **bbx**\n\n".encode())
    sent = _deliver_big(bot, sess, wire, run_async, RICH_BYTES - r - lead - 50)
    assert sent and all(len(t[:t.rindex(DIVIDER_SEP)].encode()) + reserve_bytes(tr) <= RICH_BYTES
                        for t in sent)


# ---- live growth never passes the byte limit ---------------------------------------------------

def test_late_helpers_never_push_an_edit_over_the_limit(rig):
    """The answer is near its byte edge; R1 then starts many helpers, some
    with grandchildren, across several ticks."""
    r1 = "a505c081ca3926ed5"
    rig.agent_call(r1, "pipeline-runner")
    lead = len("\U0001f4ac **frontend**\n\n".encode())
    # Four-byte letters: near the BYTE edge while under the 8,800-char
    # rule (an answer past it keeps today's rule: growing edits refused).
    big = "\U0001d538" * ((RICH_BYTES - reserve_bytes() - lead - 20) // 4)
    rig.answer(big)
    for i in range(12):
        child = f"c{i:016x}"
        rig.launch(child, "\U0001d538" * 60 + f"-{i}", "\U0001d538" * 200, by=r1)
        rig.launch(f"g{i:016x}", "\U0001d538" * 60 + f"-g{i}", "\U0001d538" * 200, by=child)
        rig.tick(31)
    edits = bb.answer_edits(rig.wire, big)
    assert edits and max(len(bb.md(e).encode()) for e in edits) <= RICH_BYTES


def test_late_helpers_keep_the_tail_within_sixteen_blocks(rig, shapes):
    r1 = "a505c081ca3926ed5"
    rig.agent_call(r1, "pipeline-runner")
    rig.answer()
    for i in range(12):
        child = f"c{i:016x}"
        rig.launch(child, f"helper-{i}", "work", by=r1)
        rig.launch(f"g{i:016x}", f"grand-{i}", "work", by=child)
        rig.tick(31)
    block = rig.last_block()
    assert shapes.conservative_blocks(DIVIDER_SEP + block) <= 16
