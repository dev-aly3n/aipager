"""The block on a delivered answer and its live edits (design.md success
criteria 4-8 and 10; entrypoints.md "Bot notify_fn events" and "Side
effects").

An answer goes out through ``notify("idle_prompt")``; the monitor's tick is
``notify("agents_line_done", {"now": t})``. State changes go through the
public ``TrackedSession`` methods. Every assertion reads what Telegram was
sent; no internal record field is read.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import i18n, preferences
from aipager.bot.agents_line import AgentRow, BlockView, render_block
from aipager.state import (
    AGENTS_LINE_EDIT_INTERVAL,
    BG_AGENTS_RETRY_SECONDS,
    BG_AGENTS_SETTLE_SECONDS,
    SessionRegistry,
    Status,
)
from tests import answer_status_bb as bb

PROMISE = "Results from the agents will follow here."
SEP = "\n\n"
HEADER = "💬 **bbx**\n\n"     # the answer's own header in this harness


def _deliver(run_async, bot, sess, *agents, text=bb.ANSWER):
    """Start ``agents`` ((id, type, started) triples) and send the answer.
    Returns (t_before, t_after, the sent message's markdown)."""
    for agent_id, agent_type, started in agents:
        assert sess.bg_agent_started(agent_id, agent_type, started)
    t0, t1 = bb.finish(run_async, bot, sess, text)
    return t0, t1, bb.answer_text(bot_wire(bot), text)


_WIRES: dict[int, bb.Wire] = {}


def bot_wire(bot):
    return _WIRES[id(bot)]


@pytest.fixture(autouse=True)
def _remember_wire(bot, wire):
    _WIRES[id(bot)] = wire
    yield
    _WIRES.pop(id(bot), None)


def _edits(wire, body=bb.ANSWER):
    return bb.answer_edits(wire, body)


def _last_block(wire, body=bb.ANSWER):
    return bb.block_of(bb.md(_edits(wire, body)[-1]), body)


def test_interval_is_thirty_seconds():
    assert AGENTS_LINE_EDIT_INTERVAL == 30.0


# ---- on send -------------------------------------------------------------------------

def test_the_answer_ends_with_the_block(bot, sess, wire, run_async):
    _t0, _t1, text = _deliver(run_async, bot, sess,
                              ("a1", "pipeline-runner", time.monotonic()))
    assert bb.block_of(text) == f"⏳ 1 agent working{SEP}○ **pipeline-runner**{SEP}{PROMISE}"


def test_no_agent_and_no_shell_means_no_block(bot, sess, wire, run_async):
    _t0, _t1, text = _deliver(run_async, bot, sess)
    assert text.endswith(bb.ANSWER)


def test_the_block_is_rendered_in_the_chats_language(bot, sess, wire, run_async):
    preferences.set_ui_language(bb.CHAT, "fa")
    _t0, _t1, text = _deliver(run_async, bot, sess,
                              ("a1", "pipeline-runner", time.monotonic()))
    expected = render_block(BlockView(agents=(AgentRow("pipeline-runner", False),)),
                            i18n.get("fa"))
    assert text.endswith(SEP + expected)


def test_a_persian_edit_stays_persian(bot, sess, wire, run_async):
    preferences.set_ui_language(bb.CHAT, "fa")
    _t0, t1, _text = _deliver(run_async, bot, sess,
                              ("a1", "pipeline-runner", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    expected = render_block(
        BlockView(agents=(AgentRow("pipeline-runner", False, tool="Read",
                                   tool_arg="state.py"),)), i18n.get("fa"))
    assert bb.md(_edits(wire)[-1]).endswith(SEP + expected)


# ---- criterion 7: cadence ------------------------------------------------------------

def test_no_edit_when_nothing_visible_changed(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "pipeline-runner", time.monotonic()))
    for dt in (31, 61, 300, 3600):
        bb.tick(run_async, bot, sess, t1 + dt)
    assert _edits(wire) == []


def test_an_invisible_change_costs_no_edit(bot, sess, wire, run_async):
    """Prose already wins: a new tool under it changes nothing shown."""
    now = time.monotonic()
    sess.bg_agent_started("a1", "Explore", now)
    sess.bg_agent_prose("a1", "m1", "Looking around", now)
    _t0, t1, _ = _deliver(run_async, bot, sess)
    sess.bg_agent_tool("a1", "Read", "x.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    assert _edits(wire) == []


def test_a_visible_change_is_edited_in(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    assert _last_block(wire) == \
        f"⏳ 1 agent working{SEP}○ **Explore** · Reading state.py{SEP}{PROMISE}"


def test_the_edit_keeps_the_answer_text(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    sent = bb.answer_text(wire)
    body = sent[:len(sent) - len(bb.block_of(sent))]
    assert bb.md(_edits(wire)[-1]).startswith(body)


def test_the_edit_targets_the_answers_message(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    block_edits = [p for m, p in wire.calls if m == "editMessageText"
                   and "Reading state.py" in bb.md(p)]
    assert [p["message_id"] for p in block_edits] == [bb.answer_msg_id(wire)]


def test_a_live_edit_is_a_skip_kind_ornament(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    assert _edits(wire)[-1]["_kw"] == {"kind": "skip", "priority": "ornament"}


def test_no_edit_within_thirty_seconds_of_the_send(bot, sess, wire, run_async):
    t0, _t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t0)
    bb.tick(run_async, bot, sess, t0 + 29.5)
    assert _edits(wire) == []


def test_the_edit_comes_once_thirty_seconds_passed(bot, sess, wire, run_async):
    t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "state.py", t0)
    bb.tick(run_async, bot, sess, t0 + 29.5)
    bb.tick(run_async, bot, sess, t1 + 30.1)
    assert len(_edits(wire)) == 1


def test_never_twice_within_thirty_seconds(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    first = t1 + 31
    bb.tick(run_async, bot, sess, first)
    sess.bg_agent_tool("a1", "Read", "two.py", first)
    bb.tick(run_async, bot, sess, first + 29.9)
    assert len(_edits(wire)) == 1


def test_the_second_change_goes_out_after_thirty_seconds(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    first = t1 + 31
    bb.tick(run_async, bot, sess, first)
    sess.bg_agent_tool("a1", "Read", "two.py", first)
    bb.tick(run_async, bot, sess, first + 29.9)
    bb.tick(run_async, bot, sess, first + 30.1)
    assert _last_block(wire).split(SEP)[1] == "○ **Explore** · Reading two.py"


def test_a_refused_edit_is_not_retried_for_the_same_text(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    wire.refuse_edits = True
    bb.tick(run_async, bot, sess, t1 + 31)
    wire.refuse_edits = False
    for dt in (62, 93, 600):
        bb.tick(run_async, bot, sess, t1 + dt)
    assert len(_edits(wire)) == 1           # only the refused attempt


def test_a_refused_edit_waits_for_the_next_change(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    wire.refuse_edits = True
    bb.tick(run_async, bot, sess, t1 + 31)
    wire.refuse_edits = False
    bb.tick(run_async, bot, sess, t1 + 62)
    sess.bg_agent_tool("a1", "Read", "two.py", t1 + 70)
    bb.tick(run_async, bot, sess, t1 + 100)
    assert _last_block(wire).split(SEP)[1] == "○ **Explore** · Reading two.py"


def test_a_muted_chat_gets_no_live_edit(bot, sess, wire, run_async):
    from aipager.bot.flood import MUTE

    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    MUTE.mute(bb.CHAT, 600)
    try:
        bb.tick(run_async, bot, sess, t1 + 31)
    finally:
        MUTE.clear()
    assert _edits(wire) == []


def test_a_refused_ornament_never_holds_back_the_next_answer(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "one.py", t1)
    wire.refuse_edits = True
    bb.tick(run_async, bot, sess, t1 + 31)
    bb.finish(run_async, bot, sess, "The second answer.")
    assert any("The second answer." in bb.md(p) for p in wire.of("sendRichMessage"))


def test_a_tick_with_nothing_due_sends_nothing(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    before = len(wire.calls)
    bb.tick(run_async, bot, sess, t1 + 1)
    assert len(wire.calls) == before


# ---- criterion 5: per-agent settle order -------------------------------------------------

def _two(run_async, bot, sess):
    base = time.monotonic()
    t0, t1, _ = _deliver(run_async, bot, sess,
                         ("a1", "pipeline-runner", base - 300),
                         ("a2", "Explore", base - 300))
    return base, t0, t1


def test_the_second_agent_settles_first_in_its_own_row(bot, sess, wire, run_async):
    base, _t0, t1 = _two(run_async, bot, sess)
    stop = base + 40                      # a2 ran 340 s
    sess.bg_agent_stopped("a2", stop)
    bb.tick(run_async, bot, sess, stop + BG_AGENTS_SETTLE_SECONDS + 0.1)
    assert _last_block(wire) == SEP.join([
        "⏳ 1 agent working",
        "○ **pipeline-runner**",
        "✓ **Explore** · done (5:40)",
        PROMISE])


def test_no_done_row_inside_the_settle_window(bot, sess, wire, run_async):
    base, _t0, _t1 = _two(run_async, bot, sess)
    stop = base + 40
    sess.bg_agent_stopped("a2", stop)
    bb.tick(run_async, bot, sess, stop + BG_AGENTS_SETTLE_SECONDS - 0.1)
    assert _edits(wire) == []


def test_the_header_counts_down_then_settles(bot, sess, wire, run_async):
    base, t0, t1 = _two(run_async, bot, sess)
    assert t1 - t0 < 0.3
    sess.bg_agent_stopped("a2", base + 40)
    bb.tick(run_async, bot, sess, base + 46)
    last = t1 + 372.1                     # 6:12 after the answer went out
    sess.bg_agent_stopped("a1", last)
    bb.tick(run_async, bot, sess, last + BG_AGENTS_SETTLE_SECONDS + 0.1)
    assert _last_block(wire) == "✅ 2 agents done (6:12)"


def test_a_settled_block_is_never_edited_again(bot, sess, wire, run_async):
    base, _t0, t1 = _two(run_async, bot, sess)
    sess.bg_agent_stopped("a1", base + 40)
    sess.bg_agent_stopped("a2", base + 41)
    bb.tick(run_async, bot, sess, base + 50)
    n = len(_edits(wire))
    for dt in (100, 1000, 10_000):
        bb.tick(run_async, bot, sess, base + dt)
    assert (n, len(_edits(wire))) == (1, 1)


def test_a_phantom_stop_settles_nothing(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_stopped("not-an-agent", t1)
    bb.tick(run_async, bot, sess, t1 + 3600)
    assert _edits(wire) == []


def test_a_revived_agent_inside_the_window_is_not_done(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_stopped("a1", t1 + 40)
    sess.bg_agent_still_running("a1", t1 + 41)
    bb.tick(run_async, bot, sess, t1 + 60)
    assert _edits(wire) == []


# ---- criterion 8: retries, mute, GONE, restart --------------------------------------------

def test_the_final_settle_is_retried_once_after_a_refusal(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_stopped("a1", t1 + 40)
    wire.refuse_edits = True
    bb.tick(run_async, bot, sess, t1 + 50)
    wire.refuse_edits = False
    bb.tick(run_async, bot, sess, t1 + 50 + BG_AGENTS_RETRY_SECONDS + 1)
    assert len(_edits(wire)) == 2


def test_the_final_settle_gives_up_after_two_attempts(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_stopped("a1", t1 + 40)
    wire.refuse_edits = True
    t = t1 + 50
    for _ in range(5):
        bb.tick(run_async, bot, sess, t)
        t += BG_AGENTS_RETRY_SECONDS + 31
    assert len(_edits(wire)) == 2


def test_live_refusals_do_not_use_up_the_final_attempts(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    wire.refuse_edits = True
    for i, dt in enumerate((31, 62, 93)):
        sess.bg_agent_tool("a1", "Read", f"f{i}.py", t1 + dt - 1)
        bb.tick(run_async, bot, sess, t1 + dt)
    sess.bg_agent_stopped("a1", t1 + 100)
    bb.tick(run_async, bot, sess, t1 + 130)            # final, refused
    wire.refuse_edits = False
    bb.tick(run_async, bot, sess, t1 + 130 + BG_AGENTS_RETRY_SECONDS + 1)
    assert _last_block(wire).startswith("✅ 1 agent done")


def test_the_final_settle_waits_out_a_mute(bot, sess, wire, run_async):
    from aipager.bot.flood import MUTE

    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_stopped("a1", t1 + 40)
    MUTE.mute(bb.CHAT, 600)
    try:
        bb.tick(run_async, bot, sess, t1 + 50)
        assert _edits(wire) == []
    finally:
        MUTE.clear()
    bb.tick(run_async, bot, sess, t1 + 50 + 601)
    assert _last_block(wire).startswith("✅ 1 agent done")


def test_a_gone_session_gets_no_edit(bot, sess, wire, run_async):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    sess.bg_agent_tool("a1", "Read", "x.py", t1)
    bot.registry.transition(sess.name, Status.GONE)
    bb.tick(run_async, bot, sess, t1 + 3600)
    assert _edits(wire) == []


def test_a_restart_forgets_the_pending_block(bot, sess, wire, run_async, tmp_state_file):
    _t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    bot.registry.save()
    again = SessionRegistry()
    again.load()
    loaded = again.get(sess.name)
    assert loaded is not None and loaded.agents_lines_due(t1 + 10_000) == []


def test_a_restart_persists_no_agent_activity(bot, sess, wire, run_async, tmp_state_file):
    now = time.monotonic()
    sess.bg_agent_started("a1", "Explore", now)
    sess.bg_agent_prose("a1", "m1", "Secret narration words", now)
    sess.bg_agent_tool("a1", "Read", "hidden_file.py", now)
    sess.agent_call_launched("Explore", "Pending description", now)
    bb.finish(run_async, bot, sess)
    bot.registry.save()
    raw = tmp_state_file.read_text(encoding="utf-8")
    assert [w in raw for w in ("Secret narration", "hidden_file", "Pending description")] \
        == [False, False, False]


def test_after_a_restart_the_loaded_session_has_no_agents(bot, sess, wire, run_async,
                                                          tmp_state_file):
    _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    bot.registry.save()
    again = SessionRegistry()
    again.load()
    assert again.get(sess.name).bg_agent_labels() == []


# ---- criterion 6: shells of this job only -------------------------------------------------

def _shell(sess, tid, started, label="npm run dev"):
    sess.bg_shell_started(tid, label, f"Bash: {label}", started)


def test_an_older_shell_is_not_listed(bot, sess, wire, run_async):
    now = time.monotonic()
    _shell(sess, "old", now - 900)
    sess.bg_shells_leave_job()
    _t0, _t1, text = _deliver(run_async, bot, sess, ("a1", "Explore", now))
    assert bb.block_of(text).split(SEP)[0] == "⏳ 1 agent working"


def test_a_shell_started_in_the_job_is_listed(bot, sess, wire, run_async):
    now = time.monotonic()
    _shell(sess, "old", now - 900)
    sess.bg_shells_leave_job()
    _shell(sess, "new", now - 5, label="pytest -q")
    _t0, _t1, text = _deliver(run_async, bot, sess, ("a1", "Explore", now))
    assert bb.block_of(text).split(SEP)[0] == "⏳ 1 agent working · 🖥 1 shell running"


def test_only_an_older_shell_means_no_block(bot, sess, wire, run_async):
    _shell(sess, "old", time.monotonic() - 900)
    sess.bg_shells_leave_job()
    _t0, _t1, text = _deliver(run_async, bot, sess)
    assert text.endswith(bb.ANSWER)


def test_a_job_shell_alone_is_one_line(bot, sess, wire, run_async):
    _shell(sess, "s1", time.monotonic() - 5)
    _t0, _t1, text = _deliver(run_async, bot, sess)
    assert bb.block_of(text) == "🖥 1 shell running"


def test_a_shells_command_is_never_shown(bot, sess, wire, run_async):
    _shell(sess, "s1", time.monotonic() - 5, label="npm run secret-dev-server")
    _t0, _t1, text = _deliver(run_async, bot, sess)
    assert "secret-dev-server" not in bb.block_of(text)


def test_the_pinned_bar_still_counts_every_shell(bot, sess, wire, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(bb.CHAT))
    now = time.monotonic()
    _shell(sess, "old", now - 900)
    sess.bg_shells_leave_job()
    _shell(sess, "new", now - 5)
    bb.finish(run_async, bot, sess)
    assert "2 shells" in bot._render_pinned(bb.CHAT)[0]


def _shell_answer(run_async, bot, sess, ran):
    """A job shell that started ``ran`` seconds before ``end``; returns end."""
    base = time.monotonic()
    _shell(sess, "s1", base - ran + 100)
    _t0, t1, _ = _deliver(run_async, bot, sess)
    return base + 100, t1


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_a_shell_that_exits_settles_to_finished(bot, sess, wire, run_async, outcome):
    end, _t1 = _shell_answer(run_async, bot, sess, 7985)
    sess.end_bg_shell("s1", outcome, end)
    bb.tick(run_async, bot, sess, end + 31)
    assert _last_block(wire) == "1 shell finished (2:13:05)"


@pytest.mark.parametrize("outcome", ["killed", "stopped"])
def test_a_killed_shell_settles_to_stopped(bot, sess, wire, run_async, outcome):
    end, _t1 = _shell_answer(run_async, bot, sess, 65)
    sess.end_bg_shell("s1", outcome, end)
    bb.tick(run_async, bot, sess, end + 31)
    assert _last_block(wire) == "1 shell stopped (1:05)"


def test_a_lost_shell_leaves_the_block_as_sent(bot, sess, wire, run_async):
    end, _t1 = _shell_answer(run_async, bot, sess, 65)
    sess.end_bg_shell("s1", "lost", end)
    for dt in (31, 100, 3600):
        bb.tick(run_async, bot, sess, end + dt)
    assert _edits(wire) == []


def test_agents_done_while_the_shell_runs(bot, sess, wire, run_async):
    base = time.monotonic()
    _shell(sess, "s1", base - 5)
    t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", base - 10))
    assert t1 - t0 < 0.3
    stop = t1 + 372.1
    sess.bg_agent_stopped("a1", stop)
    bb.tick(run_async, bot, sess, stop + 6)
    assert _last_block(wire) == "✅ 1 agent done (6:12) · 🖥 1 shell running"


def test_then_the_shell_finishes(bot, sess, wire, run_async):
    base = time.monotonic()
    _shell(sess, "s1", base - 5)
    t0, t1, _ = _deliver(run_async, bot, sess, ("a1", "Explore", base - 10))
    assert t1 - t0 < 0.3
    stop = t1 + 372.1
    sess.bg_agent_stopped("a1", stop)
    bb.tick(run_async, bot, sess, stop + 6)
    sess.end_bg_shell("s1", "completed", base - 5 + 7985)
    bb.tick(run_async, bot, sess, base + 7985 + 60)
    assert _last_block(wire) == "✅ 1 agent done (6:12) · 1 shell finished (2:13:05)"


# ---- criterion 10: size ------------------------------------------------------------------

def _body(chars):
    unit = "plain words "
    return (unit * (chars // len(unit) + 1))[:chars - 1] + "."


def test_an_edit_never_grows_the_message_past_8800_chars(bot, sess, wire, run_async):
    initial_block = f"⏳ 1 agent working{SEP}○ **t**{SEP}{PROMISE}"
    body = _body(8790 - 2 - len(initial_block) - len(HEADER))
    _t0, t1, text = _deliver(run_async, bot, sess, ("a1", "t", time.monotonic()),
                             text=body)
    assert len(text) == 8790                     # precondition: sent whole
    sess.bg_agent_prose("a1", "m1", "A thirty character narration..", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    assert _edits(wire, body) == []


def test_a_growing_edit_under_8800_chars_goes_out(bot, sess, wire, run_async):
    initial_block = f"⏳ 1 agent working{SEP}○ **t**{SEP}{PROMISE}"
    body = _body(8700 - 2 - len(initial_block) - len(HEADER))
    _t0, t1, text = _deliver(run_async, bot, sess, ("a1", "t", time.monotonic()),
                             text=body)
    assert len(text) == 8700
    sess.bg_agent_prose("a1", "m1", "A thirty character narration..", t1)
    bb.tick(run_async, bot, sess, t1 + 31)
    assert len(_edits(wire, body)) == 1


def test_a_shrinking_settle_over_8800_chars_goes_out(bot, sess, wire, run_async):
    now = time.monotonic()
    sess.bg_agent_started("a1", "t", now)
    sess.bg_agent_prose("a1", "m1", "A fairly long narration of what it does now", now)
    body = _body(9000)
    _t0, t1, text = _deliver(run_async, bot, sess, text=body)
    assert len(text) > 8800
    sess.bg_agent_stopped("a1", t1 + 40)
    bb.tick(run_async, bot, sess, t1 + 50)
    assert _last_block(wire, body).startswith("✅ 1 agent done")


def test_no_message_ever_exceeds_the_rich_byte_limit(bot, sess, wire, run_async):
    from aipager.bot.animation import _RICH_LIMIT

    wide = "\U0001f600"
    body = (wide * 10 + " ") * 720           # ~7,900 chars, ~31,700 bytes
    now = time.monotonic()
    for i in range(4):
        sess.bg_agent_started(f"a{i}", "t" * 32, now)
    bb.finish(run_async, bot, sess, body)
    t = time.monotonic()
    for i in range(4):
        sess.bg_agent_prose(f"a{i}", "m", (wide * 9 + " ") * 10, t)
    bb.tick(run_async, bot, sess, t + 31)
    mine = [bb.md(p) for m, p in wire.calls
            if m in ("sendRichMessage", "editMessageText") and "rich_message" in p
            and p.get("message_id", p.get("_id")) == bb.answer_msg_id(wire, wide * 10)]
    sizes = [len(x.encode()) for x in mine]
    assert (len(mine), max(sizes) <= _RICH_LIMIT) == (2, True)


def test_reserve_covers_a_worst_real_block_in_every_language():
    from aipager.bot.agents_line import reserve_bytes
    from aipager.i18n import locales

    wide = "\U0001f600"
    view = BlockView(
        agents=tuple(AgentRow(wide * 40, False, prose=(wide * 9 + " ") * 10)
                     for _ in range(9)),
        shells_running=150, shells_finished=150, shells_stopped=150)
    short = []
    for code in sorted(locales.LOCALES):
        tr = i18n.get(code)
        if len((SEP + render_block(view, tr)).encode()) > reserve_bytes(tr):
            short.append(code)
    assert short == []


def test_reserve_covers_a_settled_block_in_every_language():
    from aipager.bot.agents_line import reserve_bytes
    from aipager.i18n import locales

    view = BlockView(
        agents=tuple(AgentRow("x" * 40, True, 359_999.0) for _ in range(150)),
        agents_done_seconds=359_999.0, shells_finished=150,
        shells_finished_seconds=359_999.0, shells_stopped=150,
        shells_stopped_seconds=359_999.0)
    short = [code for code in sorted(locales.LOCALES)
             if len((SEP + render_block(view, i18n.get(code))).encode())
             > reserve_bytes(i18n.get(code))]
    assert short == []


def test_saved_state_is_valid_json(bot, sess, wire, run_async, tmp_state_file):
    _deliver(run_async, bot, sess, ("a1", "Explore", time.monotonic()))
    bot.registry.save()
    assert isinstance(json.loads(tmp_state_file.read_text(encoding="utf-8")), (dict, list))
