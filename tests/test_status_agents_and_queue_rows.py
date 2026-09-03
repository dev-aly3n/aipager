"""/status and the session dashboard must tell the truth about now.

Roadmap 4.5 shipped its busy-message half — `💰 $0.75 (3 agents)` — but the
chat surfaces showed neither agents nor, since the queue handoff, an honest
queue: both computed Queue from `pending_queue`, which now holds only
dialog/mixed-sender holds. A message already sent to Claude and awaiting
pick-up lives as a note, counted by `policy_snapshot.combined_queue_depth`
— the single seam the Mini App and /clearqueue already read. `/status`
saying `Queue 0` while three messages wait inside Claude tells the
operator the opposite of the truth at exactly the moment they check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import policy_snapshot as ps
from aipager.state import Status

NAME = "claude-x"


def _sess(bot, *, agents=0, notes=0, held=0):
    sess = bot.registry.get_or_create(NAME)
    sess.label = "x"
    bot.registry.transition(NAME, Status.BUSY)
    for i in range(agents):
        sess.add_subagent(f"a{i}", {"type": "explore" if i % 2 else "plan",
                                    "started_at": float(i),
                                    "history_idx": None})
    for i in range(held):
        sess.queue_prompt(f"held {i}", 100 + i)
    for i in range(notes):
        ps.write_note(NAME, body=f"note {i}", raw_text=f"note {i}",
                      msg_id=200 + i, chat_id=-1001, sender_key=(0, 1),
                      role=None, scope=None, member=None,
                      style_text="", reply_context="")
    return sess


def _dashboard(bot):
    bot._read_status_file = MagicMock(return_value=None)
    return bot._build_session_dashboard(bot.registry.get(NAME))


# ── the reported gaps ──────────────────────────────────────────────────

def test_the_dashboard_shows_live_agents(mk_bot):
    bot = mk_bot()
    _sess(bot, agents=3)

    text = _dashboard(bot)

    assert "Agents" in text, "no agents row despite 3 running"
    assert "3" in text.split("Agents", 1)[1].split("\n", 1)[0]


def test_the_dashboard_queue_counts_messages_inside_claude(mk_bot):
    """The handoff drift: notes are queued work, pending_queue is not the
    whole story any more."""
    bot = mk_bot()
    _sess(bot, notes=3)

    text = _dashboard(bot)

    assert "Queue" in text, "queue row missing with 3 messages outstanding"
    assert "3 pending" in text


def test_status_shows_agents_and_the_combined_queue(mk_bot, run_async):
    bot = mk_bot()
    _sess(bot, agents=2, notes=2, held=1)
    replies = []
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock(
        side_effect=lambda *a, **k: replies.append(a[0]))
    update.effective_chat = MagicMock(id=-1001)
    update.effective_user = MagicMock(id=1)
    run_async(bot._handle_status(update, MagicMock()))

    text = "\n".join(replies)
    assert "Agents 2" in text
    assert "Queue  3" in text, f"expected combined 2 notes + 1 held; got:\n{text}"
    assert "1 queued" in text and "2 notes" in text, (
        "intent.md requirement 4: the combined total must be broken "
        f"down into queued vs. outstanding notes so a pile of stale "
        f"notes can never read as real pending messages; got:\n{text}")


def test_status_queue_breakdown_distinguishes_all_notes_from_all_queued(
    mk_bot, run_async,
):
    """The exact failure mode from the live incident: a combined total
    that is ENTIRELY stale notes (0 real queued messages) must still
    say so explicitly, not just show a bare "15 pending" that reads as
    15 real messages."""
    bot = mk_bot()
    _sess(bot, notes=3, held=0)
    replies = []
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock(
        side_effect=lambda *a, **k: replies.append(a[0]))
    update.effective_chat = MagicMock(id=-1001)
    update.effective_user = MagicMock(id=1)
    run_async(bot._handle_status(update, MagicMock()))

    text = "\n".join(replies)
    assert "0 queued" in text and "3 notes" in text, (
        f"an all-notes queue must say '0 queued', not hide the split; "
        f"got:\n{text}")


# ── what must not move ─────────────────────────────────────────────────

def test_zero_agents_and_empty_queue_render_no_rows(mk_bot):
    """Both renderers omit empty rows; the new rows follow suit."""
    bot = mk_bot()
    _sess(bot)

    text = _dashboard(bot)

    assert "Agents" not in text
    assert "Queue" not in text


def test_agent_type_breakdown_appears_when_types_are_few(mk_bot):
    bot = mk_bot()
    _sess(bot, agents=3)          # plan, explore, plan

    text = _dashboard(bot)
    row = text.split("Agents", 1)[1].split("\n", 1)[0]

    assert "explore" in row and "plan" in row


def test_the_dashboard_breaks_down_queued_vs_outstanding_notes(mk_bot):
    bot = mk_bot()
    _sess(bot, notes=2, held=1)

    text = _dashboard(bot)

    assert "1 queued" in text and "2 notes" in text, (
        f"dashboard queue row does not distinguish queued from "
        f"outstanding notes; got:\n{text}")


def test_the_dashboard_number_is_the_seams_number(mk_bot):
    """The drift this fixes came from three surfaces and one rewired.
    2 notes + 2 held = 4 is producible only by the combined seam — the
    stale field alone would say 2. (Whether chat CALLS the seam is pinned
    by the mutations on each surface, not by an import identity check.)"""
    bot = mk_bot()
    sess = _sess(bot, notes=2, held=2)

    assert ps.combined_queue_depth(sess) == 4
    assert "4 pending" in _dashboard(bot)


def test_agent_types_are_escaped_and_odd_types_cannot_break_the_render(mk_bot):
    """Found by review: `type` is hook-sourced and this message renders
    with parse_mode=HTML. An angle-bracketed type must arrive escaped, and
    a None/int type must not crash the whole render (one bad session must
    not take down /status for the chat)."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "x"
    bot.registry.transition(NAME, Status.BUSY)
    sess.add_subagent("evil", {"type": "<b>boom</b>", "started_at": 1.0,
                               "history_idx": None})
    sess.add_subagent("odd", {"type": None, "started_at": 2.0,
                              "history_idx": None})
    sess.add_subagent("num", {"type": 7, "started_at": 3.0,
                              "history_idx": None})

    text = _dashboard(bot)

    assert "<b>boom</b>" not in text, "hook-controlled HTML reached the message"
    assert "&lt;b&gt;boom&lt;/b&gt;" in text
    assert "Agents  3" in text


def test_status_keeps_a_bare_agent_count_by_design(mk_bot, run_async):
    """/status is a multi-session overview; the type breakdown lives on
    the single-session dashboard. Pinned so a later 'fix' adding it there
    is a deliberate choice, not drift."""
    bot = mk_bot()
    _sess(bot, agents=3)
    replies = []
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock(
        side_effect=lambda *a, **k: replies.append(a[0]))
    update.effective_chat = MagicMock(id=-1001)
    update.effective_user = MagicMock(id=1)
    run_async(bot._handle_status(update, MagicMock()))

    text = "\n".join(replies)
    assert "Agents 3" in text
    assert "explore" not in text and "plan" not in text
