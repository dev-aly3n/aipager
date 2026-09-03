"""spec.md requirement 2 ("Agent row rendering"): the rich card's agent
row shows "starting" before its first attributed tool call, live
activity + elapsed once it has one, and settles on SubagentStop to a
frozen "N tool call[s]" shape (singular vs plural).

Driven purely through ``build_stream_card``/``build_stream_card_ex``
(the rich card) and ``TelegramBot.notify``'s ``subagent_stop`` event —
entrypoints.md's documented shapes, not the internal row-builder.
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card, build_stream_card_ex
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"  # 🤖


def _sess_with_live_agent(**agent_overrides):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic()
    sess.tool_history = [(f"{MARK} data-miner", False)]
    entry = {"type": "data-miner", "started_at": time.monotonic(), "history_idx": 0}
    entry.update(agent_overrides)
    sess.active_subagents["m-1"] = entry
    return sess


# ---- starting -------------------------------------------------------------

def test_live_agent_row_shows_starting_with_no_activity_yet():
    sess = _sess_with_live_agent()  # no "activity" key set at all
    card, _ = build_stream_card_ex(sess, "Working")
    assert f"{MARK} data-miner · starting ·" in card


def test_live_agent_row_starting_shows_zero_seconds_immediately():
    sess = _sess_with_live_agent(started_at=time.monotonic())
    card, _ = build_stream_card_ex(sess, "Working")
    assert f"{MARK} data-miner · starting · 0s" in card


# ---- active with activity --------------------------------------------------

def test_live_agent_row_shows_current_activity_and_elapsed():
    sess = _sess_with_live_agent(
        started_at=time.monotonic() - 45, activity="Grep: def handler",
    )
    card, _ = build_stream_card_ex(sess, "Working")
    assert f"{MARK} data-miner · Grep: def handler · 45s" in card


def test_live_agent_row_elapsed_uses_minutes_past_sixty_seconds():
    sess = _sess_with_live_agent(
        started_at=time.monotonic() - 130, activity="Bash: build",
    )
    card, _ = build_stream_card_ex(sess, "Working")
    assert "· 2m " in card
    assert "10s" in card


def test_live_agent_row_is_wrapped_in_hourglass_and_code_span():
    sess = _sess_with_live_agent(
        started_at=time.monotonic() - 3, activity="Read: /README.md",
    )
    card, _ = build_stream_card_ex(sess, "Working")
    assert f"⏳ `{MARK} data-miner · Read: /README.md · 3s`" in card


# ---- settled on SubagentStop ----------------------------------------------

def test_settled_agent_row_shows_plural_tool_call_count(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} data-miner", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "m-1", "agent_type": "data-miner",
        "elapsed": 61.0, "history_idx": 0, "tool_count": 8,
    }))
    card, _ = build_stream_card_ex(sess, "Working")
    assert f"✅ `{MARK} data-miner · 8 tool calls · 1m 1s`" in card


def test_settled_agent_row_shows_singular_tool_call_count(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} data-miner", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "m-1", "agent_type": "data-miner",
        "elapsed": 4.0, "history_idx": 0, "tool_count": 1,
    }))
    card, _ = build_stream_card_ex(sess, "Working")
    assert "1 tool call ·" in card
    assert "1 tool calls" not in card


def test_settled_agent_row_is_frozen_and_does_not_change_on_later_render(
    mk_bot, run_async,
):
    """A later render of the same session must reproduce byte-identical
    settled text — nothing recomputes it from live agent state (which is
    gone anyway, since SubagentStop pops the entry)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} data-miner", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "m-1", "agent_type": "data-miner",
        "elapsed": 12.0, "history_idx": 0, "tool_count": 3,
    }))
    card1, _ = build_stream_card_ex(sess, "Working")
    time.sleep(0.05)  # if elapsed were recomputed live, this would change it
    card2, _ = build_stream_card_ex(sess, "Working")
    assert card1 == card2


def test_build_stream_card_non_ex_variant_shows_same_live_row_shape():
    """build_stream_card (unchanged signature, no truncation flag) must
    render the identical row text as its _ex sibling."""
    sess = _sess_with_live_agent(
        started_at=time.monotonic() - 7, activity="Write: /out.txt",
    )
    card = build_stream_card(sess, "Working")
    assert f"{MARK} data-miner · Write: /out.txt · 7s" in card
