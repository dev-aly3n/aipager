"""The status block under an answer, kept live (roadmap 8.41, 2026-10-10).

End to end through the hook receiver and ``bot.notify``: what each agent
is doing (its prose, its tool, its Agent call's description), each row
settling on its own, only the job's own shells, the cadence (only on a
visible change, never twice within 30 s, a refused edit waits for the
next change, never in front of an answer), the size caps, and the live
card guard. Every outbound call is recorded in one list; ``asyncio`` is
never patched.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from aipager.bot.flood_budget import FloodSkipped
from aipager.state import (
    AGENT_CALL_MAX_AGE,
    AGENT_CALLS_CAP,
    AGENTS_LINE_EDIT_INTERVAL,
    BG_AGENTS_SETTLE_SECONDS,
    Status,
    TrackedSession,
)
from tests.test_background_agents_line import (
    ANSWER,
    CHAT,
    _agents,
    _finish,
    _hook,
    _markdown,
    _receiver,
    _sess,
    _settle,
    _Wire,
    _wire_bot,
)

PROMISE = "Results from the agents will follow here."


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **kw):
        w.calls.append((method, dict(payload, _kw=kw)))
        w.next_id += 1
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _block(text: str, rec: dict) -> str:
    assert text.startswith(rec["body"] + "\n\n")
    return text[len(rec["body"]) + 2:]


def _edits(wire, before=0):
    return [p for m, p in wire.calls[before:] if m == "editMessageText"]


def _deliver(run_async, bot, sess, *pairs, age=6 * 60):
    """An answer carrying the block, sent *age* seconds ago (and its last
    attempt with it), so the edit interval has passed."""
    if pairs:
        _agents(sess, *pairs)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    rec = sess.agents_lines[-1]
    rec["sent_at"] -= age
    rec["last_try"] -= age
    return rec


# ── what each agent is doing ────────────────────────────────────────────────

def test_an_agents_prose_is_its_row(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "pipeline-runner"))
    before = len(wire.calls)
    _hook(run_async, recv, sess, "MessageDisplay", agent_id="a1", message_id="m1",
          index=0, final=False, delta="Writing final.md for the pipeline.\n\nMore.")
    _settle(run_async, bot, sess, time.monotonic())
    (edit,) = _edits(wire, before)
    assert _block(_markdown(edit), rec) == (
        "⏳ 1 agent working\n\n○ **pipeline-runner** · Writing final.md for the pipeline."
        "\n\n" + PROMISE)


def test_an_agents_tool_is_its_row_and_a_command_never_shows(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"))
    before = len(wire.calls)
    _hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Read",
          tool_input={"file_path": "/home/x/aipager/state.py"})
    _settle(run_async, bot, sess, time.monotonic())
    (edit,) = _edits(wire, before)
    assert "○ **Explore** · Reading state.py" in _block(_markdown(edit), rec)
    _hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Bash",
          tool_input={"command": "rm -rf build", "description": "clean"})
    _settle(run_async, bot, sess, time.monotonic() + AGENTS_LINE_EDIT_INTERVAL)
    last = _markdown(_edits(wire, before)[-1])
    # The call's own description (Claude's words), never its command.
    assert "○ **Explore** · clean" in last
    assert "rm -rf" not in last


def test_prose_stays_ahead_of_a_later_tool(mk_bot, run_async, wire):
    """Precedence: the agent's own prose, then its tool."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"))
    _hook(run_async, recv, sess, "MessageDisplay", agent_id="a1", message_id="m1",
          index=0, final=False, delta="Looking at the cache.")
    _hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Read",
          tool_input={"file_path": "/a/b.py"})
    before = len(wire.calls)
    _settle(run_async, bot, sess, time.monotonic())
    assert "· Looking at the cache." in _markdown(_edits(wire, before)[0])


def test_a_new_message_replaces_the_prose_once_it_says_something(mk_bot):
    sess = TrackedSession(name="x", label="x")
    sess.bg_agent_started("a1", "Explore", 0.0)
    sess.bg_agent_prose("a1", "m1", "First. ", 1.0)
    sess.bg_agent_prose("a1", "m1", "Still first.", 1.0)
    assert sess.bg_agents["a1"]["prose"] == "First. Still first."
    sess.bg_agent_prose("a1", "m2", "  \n", 2.0)            # nothing visible yet
    assert sess.bg_agents["a1"]["prose"] == "First. Still first."
    sess.bg_agent_prose("a1", "m2", "Second.", 3.0)
    assert sess.bg_agents["a1"]["prose"] == "Second."
    sess.bg_agent_prose("a1", "m2", "x" * 5000, 4.0)
    assert len(sess.bg_agents["a1"]["prose"]) == 1000      # BG_AGENT_PROSE_CAP
    sess.bg_agent_prose("nobody", "m1", "lost", 5.0)
    sess.bg_agent_tool("nobody", "Read", "a.py", 5.0)
    assert set(sess.bg_agents) == {"a1"}


# ── the Agent call's description ─────────────────────────────────────────────

def test_the_agent_calls_description_is_its_row(mk_bot, run_async, wire):
    """The parent's Agent PreToolUse (no agent_id) carries type and
    description; the SubagentStart that follows takes it."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "PreToolUse", tool_name="Agent",
          tool_input={"subagent_type": "pipeline-runner",
                      "description": "Run the full suite", "prompt": "secret plan"})
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
          agent_type="pipeline-runner")
    assert sess.bg_agents["a1"]["desc"] == "Run the full suite"
    sess.status = Status.IDLE
    rec = _deliver(run_async, bot, sess)
    assert rec["shown"].split("\n\n")[1] == "○ **pipeline-runner** · Run the full suite"
    assert "secret plan" not in rec["shown"]


@pytest.mark.parametrize("agent_type", [
    "pipe\u202erunner",
    "pipeline  runner",
    "p" * 100,
], ids=["direction-control", "inner-whitespace", "over-80"])
def test_the_start_type_is_normalised_like_the_calls(mk_bot, run_async, wire, agent_type):
    """The Agent call's type and the SubagentStart's go through the same
    normalisation, so a type the cleaning changes still matches."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "PreToolUse", tool_name="Agent",
          tool_input={"subagent_type": agent_type, "description": "Run the full suite"})
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1", agent_type=agent_type)
    assert sess.bg_agents["a1"]["desc"] == "Run the full suite"
    assert sess.pending_agent_calls == []


def test_two_waiting_calls_of_one_type_give_no_description():
    """Never a wrong description: two of a type cannot be told apart."""
    sess = TrackedSession(name="x", label="x")
    sess.agent_call_launched("Explore", "Look at A", 0.0)
    sess.agent_call_launched("Explore", "Look at B", 0.0)
    sess.agent_call_launched("Plan", "Plan it", 0.0)
    sess.bg_agent_started("a1", "Explore", 1.0)
    sess.bg_agent_started("a2", "Explore", 1.0)
    sess.bg_agent_started("a3", "Plan", 1.0)
    assert sess.bg_agents["a1"]["desc"] == ""
    assert sess.bg_agents["a2"]["desc"] == ""
    assert sess.bg_agents["a3"]["desc"] == "Plan it"
    assert sess.pending_agent_calls == []


def test_calls_are_matched_one_by_one_in_launch_order():
    sess = TrackedSession(name="x", label="x")
    sess.agent_call_launched("Explore", "Look at A", 0.0)
    sess.bg_agent_started("a1", "Explore", 1.0)
    sess.agent_call_launched("Explore", "Look at B", 2.0)
    sess.bg_agent_started("a2", "Explore", 3.0)
    assert [sess.bg_agents[a]["desc"] for a in ("a1", "a2")] == ["Look at A", "Look at B"]


def test_waiting_calls_expire_and_are_capped():
    sess = TrackedSession(name="x", label="x")
    sess.agent_call_launched("Explore", "old", 0.0)
    sess.bg_agent_started("a1", "Explore", AGENT_CALL_MAX_AGE + 1)
    assert sess.bg_agents["a1"]["desc"] == ""
    for i in range(AGENT_CALLS_CAP + 5):
        sess.agent_call_launched(f"t{i}", "d", 10_000.0)
    assert len(sess.pending_agent_calls) == AGENT_CALLS_CAP
    assert sess.pending_agent_calls[0]["type"] == "t5"
    sess.agent_call_launched("", "no type", 10_000.0)
    sess.agent_call_launched(None, "no type", 10_000.0)
    assert len(sess.pending_agent_calls) == AGENT_CALLS_CAP
    assert sess.pending_agent_calls[0]["type"] == "t5"      # nothing pushed out
    assert all(c["type"] for c in sess.pending_agent_calls)


def test_a_restarted_agent_keeps_what_it_was_doing():
    sess = TrackedSession(name="x", label="x")
    sess.agent_call_launched("Explore", "Look at A", 0.0)
    sess.bg_agent_started("a1", "Explore", 1.0)
    sess.bg_agent_tool("a1", "Read", "a.py", 2.0)
    sess.agent_call_launched("Explore", "Look at B", 3.0)
    sess.bg_agent_started("a1", "Explore", 4.0)            # the same id again
    info = sess.bg_agents["a1"]
    assert (info["started"], info["desc"], info["tool"]) == (1.0, "Look at A", "Read")
    assert len(sess.pending_agent_calls) == 1               # not consumed


def test_gone_clears_the_waiting_calls(mk_bot):
    bot = mk_bot()
    sess = _sess(bot)
    sess.agent_call_launched("Explore", "d", time.monotonic())
    bot.registry.transition(sess.name, Status.GONE)
    assert sess.pending_agent_calls == []


# ── each row settles on its own ─────────────────────────────────────────────

def test_rows_settle_in_the_order_their_agents_stop(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"), ("a2", "pipeline-runner"),
                   ("a3", "Plan"))
    before = len(wire.calls)
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2", agent_type="pipeline-runner")
    t = time.monotonic() + BG_AGENTS_SETTLE_SECONDS + 0.1
    _settle(run_async, bot, sess, t)
    lines = _block(_markdown(_edits(wire, before)[-1]), rec).split("\n\n")
    assert lines[0] == "⏳ 2 agents working"
    assert lines[1] == "○ **Explore**"
    # Its own run time (it started just before the answer went out).
    assert re.fullmatch(r"✓ \*\*pipeline-runner\*\* · done \(0:0\d\)", lines[2])
    assert lines[3] == "○ **Plan**"
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a1", agent_type="Explore")
    t += AGENTS_LINE_EDIT_INTERVAL
    _settle(run_async, bot, sess, t)
    lines = _block(_markdown(_edits(wire, before)[-1]), rec).split("\n\n")
    assert lines[0] == "⏳ 1 agent working"
    assert lines[1].startswith("✓ **Explore** · done (")
    assert lines[3] == "○ **Plan**"
    assert len(_edits(wire, before)) == 2


def test_a_done_row_shows_the_agents_own_run_time(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"), ("a2", "Plan"))
    rec["items"][0]["started"] = time.monotonic() - 42
    sess.bg_agent_stopped("a1", time.monotonic())
    before = len(wire.calls)
    _settle(run_async, bot, sess, time.monotonic() + BG_AGENTS_SETTLE_SECONDS + 0.1)
    line = _block(_markdown(_edits(wire, before)[-1]), rec).split("\n\n")[1]
    assert re.fullmatch(r"✓ \*\*Explore\*\* · done \(0:4[23]\)", line)


def test_a_row_waits_the_settle_window(mk_bot, run_async, wire):
    """A stop the still-running note takes back within the window never
    showed as done."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"), ("a2", "Plan"))
    before = len(wire.calls)
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a1", agent_type="Explore")
    _settle(run_async, bot, sess, time.monotonic() + 1)
    assert _edits(wire, before) == []


# ── only the job's own shells ───────────────────────────────────────────────

def test_an_older_shell_is_not_listed_a_shell_of_the_job_is(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    now = time.monotonic()
    sess.bg_shell_started("bold", "npm run dev", "Bash: npm run dev", now - 600)
    sess.bg_shells_leave_job()                       # a dev server from before
    sess.bg_shell_started("bnew", "pytest", "Bash: pytest", now)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (rec,) = sess.agents_lines
    assert rec["ids"] == ["bnew"]
    assert rec["shown"] == "🖥 1 shell running"
    # The pinned bar still counts both.
    assert len(sess.bg_shells) == 2


def test_only_an_older_shell_means_no_block(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    sess.bg_shell_started("bold", "npm run dev", "Bash: npm run dev", time.monotonic())
    sess.bg_shells_leave_job()
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    assert sess.agents_lines == []
    assert "🖥" not in _markdown(wire.of("sendRichMessage")[0])


@pytest.mark.parametrize("outcome, word", [
    ("completed", "finished"), ("failed", "finished"),
    ("killed", "stopped"), ("stopped", "stopped"),
], ids=["completed", "failed", "killed", "stopped"])
def test_a_shells_end_settles_the_block(mk_bot, run_async, wire, outcome, word):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    now = time.monotonic()
    sess.bg_shell_started("b1", "pytest", "Bash: pytest", now - 90)
    rec = _deliver(run_async, bot, sess)
    before = len(wire.calls)
    sess.end_bg_shell("b1", outcome, now)
    _settle(run_async, bot, sess, now + BG_AGENTS_SETTLE_SECONDS + 0.1)
    (edit,) = _edits(wire, before)
    assert re.fullmatch(rf"1 shell {word} \(1:3\d\)", _block(_markdown(edit), rec))
    assert sess.agents_lines == []


def test_a_lost_shell_leaves_the_block_as_shown(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    sess.bg_shell_started("b1", "pytest", "Bash: pytest", time.monotonic())
    _deliver(run_async, bot, sess)
    before = len(wire.calls)
    sess.end_bg_shell("b1", "lost", time.monotonic())
    assert sess.agents_lines == []
    _settle(run_async, bot, sess, time.monotonic() + 3600)
    assert _edits(wire, before) == []


# ── the cadence ─────────────────────────────────────────────────────────────

def test_no_edit_when_nothing_visible_changed(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"))
    before = len(wire.calls)
    # A heartbeat and a tool without a phrase of its own change nothing.
    _hook(run_async, recv, sess, "PostToolUse", agent_id="a1", tool_name="Bash",
          tool_input={"command": "ls"})
    for k in range(5):
        _settle(run_async, bot, sess, time.monotonic() + 40 * k)
    assert _edits(wire, before) == []


def test_never_twice_within_the_interval(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"))
    before = len(wire.calls)
    t0 = time.monotonic()
    sess.bg_agent_tool("a1", "Read", "a.py", t0)
    _settle(run_async, bot, sess, t0)
    assert len(_edits(wire, before)) == 1
    sess.bg_agent_tool("a1", "Read", "b.py", t0)
    for dt in (1, 10, AGENTS_LINE_EDIT_INTERVAL - 0.1):
        _settle(run_async, bot, sess, t0 + dt)
    assert len(_edits(wire, before)) == 1
    _settle(run_async, bot, sess, t0 + AGENTS_LINE_EDIT_INTERVAL)
    assert len(_edits(wire, before)) == 2
    assert "Reading b.py" in _markdown(_edits(wire, before)[-1])


def test_the_send_counts_as_the_first_edit(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "Explore"))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    rec = sess.agents_lines[0]
    before = len(wire.calls)
    sess.bg_agent_tool("a1", "Read", "a.py", rec["sent_at"])
    _settle(run_async, bot, sess, rec["sent_at"] + AGENTS_LINE_EDIT_INTERVAL - 1)
    assert _edits(wire, before) == []
    _settle(run_async, bot, sess, rec["sent_at"] + AGENTS_LINE_EDIT_INTERVAL)
    assert len(_edits(wire, before)) == 1


def test_a_refused_live_edit_waits_for_the_next_change(
    mk_bot, run_async, wire, monkeypatch,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"))
    refuse = {"on": True}

    async def _post(method, payload, **kw):
        wire.calls.append((method, dict(payload, _kw=kw)))
        if method == "editMessageText" and refuse["on"]:
            raise FloodSkipped(CHAT, method)
        wire.next_id += 1
        return {"ok": True, "result": {"message_id": wire.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    before = len(wire.calls)
    t0 = time.monotonic()
    sess.bg_agent_tool("a1", "Read", "a.py", t0)
    _settle(run_async, bot, sess, t0)
    assert len(_edits(wire, before)) == 1                 # refused
    refuse["on"] = False
    for k in range(1, 4):                                 # the same text: not again
        _settle(run_async, bot, sess, t0 + k * AGENTS_LINE_EDIT_INTERVAL)
    assert len(_edits(wire, before)) == 1
    assert rec["attempts"] == 0                           # live tries never count
    sess.bg_agent_tool("a1", "Read", "b.py", t0)          # the next change
    _settle(run_async, bot, sess, t0 + 4 * AGENTS_LINE_EDIT_INTERVAL)
    assert len(_edits(wire, before)) == 2
    assert "Reading b.py" in rec["shown"]


def test_after_refused_live_edits_the_final_still_gets_two_attempts(
    mk_bot, run_async, wire, monkeypatch,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"))

    async def _post(method, payload, **kw):
        wire.calls.append((method, dict(payload, _kw=kw)))
        raise FloodSkipped(CHAT, method)

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    before = len(wire.calls)
    t = time.monotonic()
    for name in ("a.py", "b.py", "c.py"):
        sess.bg_agent_tool("a1", "Read", name, t)
        _settle(run_async, bot, sess, t)
        t += AGENTS_LINE_EDIT_INTERVAL
    assert len(_edits(wire, before)) == 3
    sess.bg_agent_stopped("a1", t)
    t += BG_AGENTS_SETTLE_SECONDS + 0.1
    _settle(run_async, bot, sess, t)
    assert sess.agents_lines                              # one more try owed
    _settle(run_async, bot, sess, t + 3600)
    assert len(_edits(wire, before)) == 5
    assert sess.agents_lines == []


def test_a_live_edit_is_an_ornament_that_never_delays_an_answer(
    mk_bot, run_async, wire, monkeypatch,
):
    """Every block edit is skip-kind ornament (refused, never queued);
    with edits refused, an answer still goes out at once."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _deliver(run_async, bot, sess, ("a1", "Explore"))
    sess.bg_agent_tool("a1", "Read", "a.py", time.monotonic())
    before = len(wire.calls)
    _settle(run_async, bot, sess, time.monotonic())
    (edit,) = _edits(wire, before)
    assert edit["_kw"] == {"kind": "skip", "priority": "ornament"}

    async def _post(method, payload, **kw):
        wire.calls.append((method, dict(payload, _kw=kw)))
        if method == "editMessageText":
            raise FloodSkipped(CHAT, method)
        wire.next_id += 1
        return {"ok": True, "result": {"message_id": wire.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    sess.trigger_msg_id = 7
    sess.status = Status.IDLE
    _finish(run_async, bot, sess, summary="the next answer", raw_md="the next answer")
    assert any("the next answer" in _markdown(p) for p in wire.of("sendRichMessage"))


# ── the size caps ───────────────────────────────────────────────────────────

def test_an_edit_past_the_byte_ceiling_is_skipped(mk_bot, run_async, wire):
    from aipager.bot.animation import _RICH_LIMIT

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"))
    # Wide characters: far below the 8,800-character fold, at the bytes'.
    rec["body"] = "\U0001f600" * ((_RICH_LIMIT - len(rec["shown"].encode()) - 2) // 4)
    assert len(rec["body"]) < 8_800
    before = len(wire.calls)
    sess.bg_agent_tool("a1", "Read", "a-much-longer-file-name.py", time.monotonic())
    _settle(run_async, bot, sess, time.monotonic())
    assert _edits(wire, before) == []
    assert rec["refused"]                               # waits for a change


def test_an_edit_growing_past_the_fold_is_skipped_a_shrinking_one_is_not(
    mk_bot, run_async, wire,
):
    from aipager.bot.animation import _CARD_CHAR_BUDGET

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"))
    rec["body"] = "y" * _CARD_CHAR_BUDGET
    before = len(wire.calls)
    t = time.monotonic()
    sess.bg_agent_tool("a1", "Read", "a.py", t)           # longer: skipped
    _settle(run_async, bot, sess, t)
    assert _edits(wire, before) == []
    sess.bg_agent_stopped("a1", t)                        # the done header is shorter
    _settle(run_async, bot, sess, t + BG_AGENTS_SETTLE_SECONDS + 0.1)
    (edit,) = _edits(wire, before)
    assert re.search(r"\n\n✅ 1 agent done \(6:0\d\)$", _markdown(edit))


def test_a_final_edit_that_does_not_fit_is_given_up(mk_bot, run_async, wire):
    from aipager.bot.animation import _RICH_LIMIT

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    sess.bg_shell_started("b1", "pytest", "Bash: pytest", time.monotonic())
    rec = _deliver(run_async, bot, sess)
    rec["body"] = "\U0001f600" * ((_RICH_LIMIT - len(rec["shown"].encode()) - 2) // 4)
    assert len(rec["body"]) < 8_800
    before = len(wire.calls)
    sess.end_bg_shell("b1", "completed", time.monotonic())
    _settle(run_async, bot, sess, time.monotonic() + BG_AGENTS_SETTLE_SECONDS + 0.1)
    assert _edits(wire, before) == []
    assert sess.agents_lines == []


# ── the live card ───────────────────────────────────────────────────────────

def test_a_message_that_is_the_live_card_is_never_edited(mk_bot, run_async, wire):
    """If the answer's message is the session's live card again, an edit
    would replace the card with the old answer: wait instead."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"))
    sess.busy_msg_id = rec["msg_id"]
    sess.status = Status.BUSY
    before = len(wire.calls)
    sess.bg_agent_tool("a1", "Read", "a.py", time.monotonic())
    _settle(run_async, bot, sess, time.monotonic())
    assert _edits(wire, before) == []
    sess.busy_msg_id = None
    _settle(run_async, bot, sess, time.monotonic())
    assert len(_edits(wire, before)) == 1


def test_a_merged_answer_is_not_the_next_turns_card(mk_bot, run_async, wire):
    """The merged layout edits the card INTO the answer; the card is then
    retired, so the next turn's card is a new message and the block's
    edits go to the answer, never to that card."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout="merged")
    _agents(sess, ("a1", "Explore"))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (rec,) = sess.agents_lines
    assert not sess.busy_msg_id
    assert rec["msg_id"] == 42                         # the old card's message


# ── the tool argument the block names ───────────────────────────────────────

@pytest.mark.parametrize("tool, tool_input, arg", [
    ("Read", {"file_path": "/home/x/aipager/state.py"}, "state.py"),
    ("Write", {"file_path": "docs/final.md"}, "final.md"),
    ("Edit", {"file_path": "notify.py"}, "notify.py"),
    ("MultiEdit", {"file_path": "/a/b/"}, "b"),
    ("NotebookEdit", {"notebook_path": "/n/run.ipynb"}, "run.ipynb"),
    ("Grep", {"pattern": "agents_line", "path": "/secret"}, "agents_line"),
    ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
    ("WebSearch", {"query": "telegram  bot\napi"}, "telegram bot api"),
    ("WebFetch", {"url": "https://docs.python.org/3/library/re.html"}, "docs.python.org"),
    ("WebFetch", {"url": "not a url"}, ""),
    ("Bash", {"command": "rm -rf /", "description": "wipe"}, "wipe"),
    ("Read", {"file_path": 7}, ""),
], ids=["read", "write", "edit", "multiedit-dir", "notebook", "grep", "glob", "websearch",
        "webfetch", "webfetch-bad", "bash", "read-not-str"])
def test_a_subagents_tool_argument(mk_bot, run_async, tool, tool_input, arg):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    sess.bg_agent_started("a1", "Explore", time.monotonic())
    _hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name=tool,
          tool_input=tool_input)
    assert sess.bg_agents["a1"]["tool"] == tool
    assert sess.bg_agents["a1"]["tool_arg"] == arg


def test_a_direction_control_in_an_argument_is_revealed(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    sess.bg_agent_started("a1", "Explore", time.monotonic())
    _hook(run_async, recv, sess, "PreToolUse", agent_id="a1", tool_name="Read",
          tool_input={"file_path": "/x/evil\u202etxt.py"})
    arg = sess.bg_agents["a1"]["tool_arg"]
    assert "\u202e" not in arg and arg.startswith("evil")


def test_the_parents_agent_call_never_records_its_prompt(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "PreToolUse", tool_name="Task",
          tool_input={"subagent_type": "Plan", "description": "x" * 500,
                      "prompt": "the whole plan"})
    (call,) = sess.pending_agent_calls
    assert call["type"] == "Plan" and call["desc"] == "x" * 200
    assert "the whole plan" not in json.dumps(sess.pending_agent_calls)


# ── the room the block keeps, and the record ────────────────────────────────

def test_a_merged_answer_without_room_for_the_largest_block_falls_back(
    mk_bot, run_async, wire, monkeypatch,
):
    """The merged edit must leave room for the block's LARGEST form; when
    it cannot, the answer goes out the replace way (which cuts the body to
    make room), never as an edit that a later block could push over."""
    from aipager.bot import agents_line
    from aipager.bot.animation import _RICH_LIMIT

    monkeypatch.setattr(agents_line, "reserve_bytes", lambda tr=None: _RICH_LIMIT - 100)
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout="merged")
    _agents(sess, ("a1", "Explore"))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    assert not [p for p in wire.of("editMessageText") if ANSWER in _markdown(p)]
    (sent,) = [p for p in wire.of("sendRichMessage") if ANSWER in _markdown(p)]
    (rec,) = sess.agents_lines
    assert rec["msg_id"] != 42
    assert len(rec["body"].encode()) + _RICH_LIMIT - 100 <= _RICH_LIMIT


def test_a_text_that_does_not_end_with_the_block_is_not_kept(mk_bot):
    bot = mk_bot()
    sess = _sess(bot)
    agents = {"line": "⏳ 1 agent working", "items": [], "ids": ["a1"],
              "sent_at": time.monotonic()}
    bot._remember_agents_line(sess, CHAT, 5, "an answer without it", agents, False)
    assert sess.agents_lines == []
    bot._remember_agents_line(sess, CHAT, 5, "an answer\n\n⏳ 1 agent working",
                              agents, False)
    (rec,) = sess.agents_lines
    assert rec["body"] == "an answer"


def test_the_header_clock_runs_to_the_last_agents_end(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"), ("a2", "Plan"), age=0)
    sess.bg_agent_stopped("a1", rec["sent_at"] + 60)
    sess.bg_agent_stopped("a2", rec["sent_at"] + 200)
    before = len(wire.calls)
    _settle(run_async, bot, sess, rec["sent_at"] + 300)
    (edit,) = _edits(wire, before)
    assert _block(_markdown(edit), rec) == "✅ 2 agents done (3:20)"


def test_an_agent_started_again_after_its_stop_is_working_again(mk_bot, run_async, wire):
    """A SubagentStart for an id that stopped (an agent resuming itself)
    takes its end back at the next look."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    rec = _deliver(run_async, bot, sess, ("a1", "Explore"), ("a2", "Plan"))
    t = time.monotonic()
    sess.bg_agent_stopped("a1", t)
    sess.bg_agent_started("a1", "Explore", t + 1)
    sess.agents_lines_due(t + 2)
    assert "a1" not in rec["ends"]
    before = len(wire.calls)
    _settle(run_async, bot, sess, t + BG_AGENTS_SETTLE_SECONDS + 10)
    assert _edits(wire, before) == []                    # nothing changed
