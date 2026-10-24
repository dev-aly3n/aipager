"""The busy card never ends with a folded section (2026-10-24).

Telegram draws its own line under every `<details>` block, and aipager
draws its divider above the card's status line, so a card body ending in
a fold showed two lines over the status line. An agent's own tool calls
therefore keep their newest call as a plain row after the fold, and the
fold counts only the calls it holds; a run's fold is never the newest
run, which always follows it. These tests pin that for every layout and
every budget path (section drops, the agent-keeping chop, the raw chop).
"""

from __future__ import annotations

import random
import time

import pytest

from aipager import i18n
from aipager.bot.animation import (
    _CARD_CHAR_BUDGET,
    _FOLD_MIN_ROWS,
    _fit_sections,
    _ROW_SEP,
    build_stream_card_ex,
)
from aipager.bot.divider import DIVIDER_SEP
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"
_CLOSE = "</details>"


def _sess(label="x"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    s.busy_started_at = time.monotonic() - 180
    return s


def _body(card: str) -> str:
    """The card above aipager's divider (the status line is under it)."""
    assert DIVIDER_SEP in card
    return card.rsplit(DIVIDER_SEP, 1)[0]


def _live_agent(sess, n_tools, *, agent_type="worker", prefix="Read: /f"):
    idx = sess.record_tool(f"{MARK} {agent_type}", False)
    sess.active_subagents[f"a{idx}"] = {
        "type": agent_type, "activity": "Bash: probing",
        "started_at": time.monotonic() - 180, "history_idx": idx,
        "tools": [f"{prefix}{i}" for i in range(n_tools)],
    }
    return idx


def _settled_agent(sess, n_tools, *, agent_type="worker"):
    idx = sess.record_tool(f"{MARK} {agent_type} · {n_tools} tool calls · 5s", True)
    sess.finished_subagents.append({
        "type": agent_type, "started_at": 0.0, "elapsed": 5.0,
        "tool_count": n_tools,
        "tools": [f"Read: /s{i}" for i in range(n_tools)],
        "history_idx": idx,
    })
    return idx


# ---- the reported shape: an agent with many calls ends the card ------------

@pytest.mark.parametrize("final", [False, True], ids=["live", "final"])
def test_an_agent_with_many_calls_ends_the_card_with_its_newest_call_plain(final):
    sess = _sess()
    sess.record_tool("Bash: ls", True)
    _live_agent(sess, 58)
    card, dropped = build_stream_card_ex(sess, "Working", final=final)
    body = _body(card)
    assert dropped is False
    assert not body.endswith(_CLOSE)
    assert body.endswith("✅ `Read: /f57`")
    # The fold holds the 57 older calls, and the newest is outside it.
    assert "<details><summary>▸ 57 tool calls</summary>" in body
    fold = body[body.index("<details>"):body.index(_CLOSE)]
    assert "Read: /f56`" in fold
    assert "Read: /f57`" not in fold


def test_a_settled_agent_ends_the_card_with_its_newest_call_plain():
    sess = _sess()
    sess.record_tool("Bash: ls", True)
    _settled_agent(sess, 10)
    card, _ = build_stream_card_ex(sess, "Working")
    body = _body(card)
    assert body.endswith("✅ `Read: /s9`")
    assert "<details><summary>▸ 9 tool calls</summary>" in body


@pytest.mark.parametrize("n_tools", [1, 2, _FOLD_MIN_ROWS])
def test_a_short_group_stays_plain(n_tools):
    """Up to _FOLD_MIN_ROWS calls would leave fewer than _FOLD_MIN_ROWS in
    the fold once the newest is out of it: no fold at all."""
    sess = _sess()
    _live_agent(sess, n_tools)
    card, _ = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    for i in range(n_tools):
        assert f"`Read: /f{i}`" in card


def test_one_more_than_the_minimum_folds_all_but_the_newest():
    sess = _sess()
    _live_agent(sess, _FOLD_MIN_ROWS + 1)
    card, _ = build_stream_card_ex(sess, "Working")
    body = _body(card)
    assert f"▸ {_FOLD_MIN_ROWS} tool calls</summary>" in body
    assert body.endswith(f"✅ `Read: /f{_FOLD_MIN_ROWS}`")


def test_a_fold_mid_card_keeps_its_newest_call_before_the_next_section():
    sess = _sess()
    _live_agent(sess, 8)
    sess.record_tool("Bash: make", False)
    card, _ = build_stream_card_ex(sess, "Working")
    body = _body(card)
    assert f"{_CLOSE}{_ROW_SEP}✅ `Read: /f7`{_ROW_SEP}" in body
    assert body.endswith("⏳ `Bash: make`")


def test_a_right_to_left_card_does_not_end_with_the_fold_either():
    sess = _sess()
    _live_agent(sess, 12)
    card, _ = build_stream_card_ex(sess, "Working", tr=i18n.get("fa"))
    body = _body(card)
    assert body.count("<details>") == 1
    assert not body.rstrip("\u200e\u200f").endswith(_CLOSE)
    assert body.rstrip("\u200e\u200f").endswith("`Read: /f11`")


# ---- the budget paths ------------------------------------------------------

def _heavy_with_trailing_agent(nrows: int, n_tools: int):
    """Old prose and runs to shed, a newest run and an agent LAST: the
    shape where a drop or a chop leaves the agent's fold at the end."""
    sess = _sess("probe")
    for k in range(4):
        sess.stream_commentary.append((len(sess.tool_history), f"OLD-{k} " + "o" * 900))
        for i in range(5):
            sess.record_tool(f"Bash: old-{k}-{i} " + "x" * 200, True)
    sess.stream_commentary.append((len(sess.tool_history), "Newest prose."))
    for i in range(nrows):
        sess.record_tool(f"Grep: pattern_{i} in some/long/path/here", True)
    _live_agent(sess, n_tools, prefix="Read: /path/to/a/file_")
    return sess


@pytest.mark.parametrize("nrows", [0, 20, 60, 150, 200], ids=lambda n: f"rows{n}")
@pytest.mark.parametrize("n_tools", [4, 40, 120], ids=lambda n: f"tools{n}")
def test_no_drop_or_chop_path_leaves_a_fold_at_the_end(nrows, n_tools):
    sess = _heavy_with_trailing_agent(nrows, n_tools)
    for final in (False, True):
        card, _dropped = build_stream_card_ex(sess, "Working", final=final)
        assert len(card) <= _CARD_CHAR_BUDGET
        assert not _body(card).endswith(_CLOSE), (nrows, n_tools, final)


def test_fit_sections_under_tight_reserves_never_ends_with_a_fold():
    """Straight at the fitter, sweeping the reserve so every path runs:
    fits as is, drops sections, the agent-keeping chop and the raw chop."""
    tr = i18n.get("en")
    tools = [f"Read: /f{i}" for i in range(30)]
    sections = [
        ("run", [f"✅ `Bash: old{i}`" for i in range(6)], None),
        ("prose", ["> " + "p" * 400], None),
        ("run", [f"✅ `Bash: new{i}`" for i in range(4)], None),
        ("agent-run", ["⏳ `\U0001f916 worker · Read · 1:00`"], tools),
    ]
    seen_drop = False
    for reserve in range(0, _CARD_CHAR_BUDGET, 97):
        body, dropped = _fit_sections(sections, reserve, reserve, tr=tr)
        seen_drop = seen_drop or dropped
        assert not body.endswith(_CLOSE), reserve
    assert seen_drop


# ---- a property sweep over generated card shapes ---------------------------

def _rand_text(rng, n):
    return "".join(rng.choice("abcdefghij klmnop0123") for _ in range(rng.randint(0, n)))


def _random_session(rng):
    sess = _sess("x")
    for _ in range(rng.randint(1, 10)):
        kind = rng.choice(["run", "run", "prose", "agent_live", "agent_settled"])
        if kind == "prose" or rng.random() < 0.3:
            sess.stream_commentary.append(
                (len(sess.tool_history), _rand_text(rng, rng.choice([20, 600, 4000])) or "p"))
        if kind == "run":
            for _j in range(rng.randint(1, 8)):
                sess.record_tool(f"Bash: {_rand_text(rng, rng.choice([5, 300]))}",
                                 rng.choice([True, True, False, "failed"]))
        elif kind == "agent_live":
            idx = sess.record_tool(f"{MARK} explore", False)
            sess.active_subagents[f"a{idx}"] = {
                "type": "explore", "activity": "Bash: x",
                "started_at": time.monotonic() - 30, "history_idx": idx,
                "tools": [f"Bash: {_rand_text(rng, 60)}"
                          for _ in range(rng.choice([0, 1, 2, 3, 4, 9, 80]))],
            }
        else:
            n = rng.choice([0, 2, 3, 4, 9, 80])
            idx = sess.record_tool(f"{MARK} review · {n} tool calls · 5s", True)
            sess.finished_subagents.append({
                "type": "review", "started_at": 0.0, "elapsed": 5.0, "tool_count": n,
                "tools": [f"Read: {_rand_text(rng, 60)}" for _ in range(n)],
                "history_idx": idx,
            })
    return sess


@pytest.mark.parametrize("seed", [20261024, 4242])
def test_property_no_generated_card_ends_with_a_fold(seed):
    rng = random.Random(seed)
    for _ in range(250):
        sess = _random_session(rng)
        waiting = rng.random() < 0.15
        final = not waiting and rng.random() < 0.3
        lang = rng.choice(["en", "en", "fa", "ar", "de"])
        # rtl forced either way covers the merged answer's direction,
        # the in-RTL twin of a left-to-right chat included.
        rtl = rng.choice([None, None, True, False])
        if rng.random() < 0.2:
            sess.peer_from = {"status": "verified", "sender_session": "claude-frontend"}
        card, _dropped = build_stream_card_ex(
            sess, "Working", final=final, waiting=waiting, tr=i18n.get(lang), rtl=rtl)
        assert len(card) <= _CARD_CHAR_BUDGET
        assert len(card.encode("utf-8")) <= 32_768
        body = _body(card)
        assert not body.rstrip("\u200e\u200f\u2068\u2069").endswith(_CLOSE), (seed, body[-200:])
        # Every fold leaves at least _FOLD_MIN_ROWS rows inside it.
        for span in body.split("<details>")[1:]:
            inner = span.split(_CLOSE, 1)[0].split("</summary>", 1)[1]
            assert len([r for r in inner.split(_ROW_SEP) if r.strip()]) >= _FOLD_MIN_ROWS
