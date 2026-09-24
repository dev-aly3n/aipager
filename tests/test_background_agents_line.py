"""Background agents still running when a turn's answer goes out
(roadmap 8.41, live 2026-09-24 17:54-18:03).

The incident: a `/deliver` pipeline-runner was launched in the background.
It stopped at 18:01:29 with its own background work still running (a
backgrounded pytest run, which fires no SubagentStart/SubagentStop), the
parent's <task-notification> said so in its <note>, and the job closed on
that notification's turn: Telegram showed "💬 … Finished (7m 15s)" while
Claude Code's terminal still showed `○ pipeline-runner Running the full
pytest suite`. The agent resumed at 18:03:10.

R1  the session keeps a set of live background agents from exact signals:
    SubagentStart (id + type) adds, the matching SubagentStop removes, the
    parent's "stopped with background work of its own still running" note
    keeps a just-stopped agent live, a silence window expires a lost one,
    GONE clears it. Phantoms (unknown id, empty type) change nothing.
R2  an answer delivered while the set is non-empty ends with one line
    "⏳ N agent(s) still running — <labels> · results will follow here",
    in every layout and on 8.32's tool-less one-message path.
R3  once every agent that line named is gone (and stayed gone for the
    settle window), the line is edited ONCE to "✅ … done (Nm)": an
    ORNAMENT skip-class edit, one retry at most, never into a deleted
    message or a gone session.
R4  the pinned bar says "· ⏳ N agent(s) running" on the session's line.

Every outbound call is recorded in one list, in order (the rich transport's
``_post`` and the PTB bot's raw calls alike). ``asyncio`` is never patched.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.bot.flood_budget import FloodSkipped
from aipager.dtach import hook_receiver as hr
from aipager.state import (
    BG_AGENTS_SETTLE_SECONDS,
    Status,
    TrackedSession,
)

CHAT = 555
TRIGGER = 7
CARD = 42
ANSWER = "The settings fix is still running its final full test run."
RUNNING_1 = ("⏳ 1 agent still running — pipeline-runner · results will "
             "follow here")


# ── harness (the 8.32 wire, one list for every call) ─────────────────────────

class _Wire:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 9000

    def methods(self) -> list[str]:
        return [m for m, _p in self.calls if m != "send_chat_action"]

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **kw):
        w.calls.append((method, dict(payload, _kw=kw)))
        w.next_id += 1
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _wire_bot(mk_bot, w: _Wire):
    bot = mk_bot()

    def _rec(name):
        async def _call(*args, **kwargs):
            await asyncio.sleep(0)
            payload = dict(kwargs)
            if args:
                payload["_args"] = args
            w.calls.append((name, payload))
            w.next_id += 1
            return MagicMock(message_id=w.next_id)
        return _call

    for name in ("send_message", "delete_message", "edit_message_text",
                 "send_document", "send_chat_action"):
        setattr(bot._app.bot, name, _rec(name))

    async def _noop(*_a, **_kw):
        return None

    bot._maybe_update_bot_name = _noop
    return bot


def _sess(bot, *, layout="card", tools=(("Read: /a.py", True),), label="q"):
    s = bot.registry.get_or_create(f"claude-{label}")
    s.label = label
    s.status = Status.IDLE
    s.scope_chat_id = CHAT
    s.scope_kind = "dm"
    s.busy_msg_id = CARD
    s.busy_card_trigger = TRIGGER
    s.trigger_msg_id = TRIGGER
    s.busy_started_at = time.monotonic() - 5
    s.tool_history = list(tools)
    prefs.set_preference(CHAT, "layout", layout)
    return s


async def _drain_background():
    for _ in range(5):
        pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set())
                   if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _bounded(coro):
    return asyncio.wait_for(coro, timeout=5)


def _finish(run_async, bot, sess, **context):
    async def _go():
        await bot.notify(sess, "idle_prompt", context)
        await _drain_background()
    run_async(_bounded(_go()))


def _settle(run_async, bot, sess, now):
    run_async(_bounded(bot.notify(sess, "agents_line_done", {"now": now})))


def _receiver(bot):
    async def _notify(_sess, _event, _context):
        return None
    return hr.HookReceiver(bot.registry, _notify)


def _hook(run_async, recv, sess, event, **fields):
    recv._recent_fingerprints.clear()
    run_async(recv._on_datagram(json.dumps(dict(
        {"session": sess.name, "hook_event_name": event,
         "transcript_path": ""}, **fields)).encode()))


def _markdown(payload: dict) -> str:
    return payload["rich_message"]["markdown"]


def _last_line(text: str) -> str:
    return text.rpartition("\n\n")[2]


def _agents(sess, *pairs):
    now = time.monotonic()
    for agent_id, agent_type in pairs:
        assert sess.bg_agent_started(agent_id, agent_type, now)


# ── R1: the set, from exact signals ─────────────────────────────────────────

def test_subagent_start_adds_and_the_matching_stop_removes(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
          agent_type="pipeline-runner")
    assert sess.bg_agent_labels() == ["pipeline-runner"]
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a1",
          agent_type="pipeline-runner")
    assert sess.bg_agent_labels() == []


def test_a_phantom_subagent_stop_changes_nothing(mk_bot, run_async):
    """Unknown id, empty type, 0.0 s — arrives constantly, by design."""
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a9375cab24c9bf11f",
          agent_type="")
    assert sess.bg_agent_labels() == ["pipeline-runner"]


def test_a_start_with_no_type_is_not_an_agent(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1", agent_type="")
    assert sess.bg_agent_labels() == []


_NOTE_STILL_RUNNING = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<summary>Agent \"Deliver Mini App settings save fix\" "
    "finished</summary>\n<note>This agent stopped with background work of "
    "its own still running. It may resume on its own when that work "
    "completes or reports, and the same task-id notifies again if it does; "
    "the result below may be interim.</note>\n<result>Waiting on the full "
    "suite.</result>\n</task-notification>")
_NOTE_DONE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>A task-notification fires each time this agent stops "
    "with no live background children of its own.</note>\n"
    "</task-notification>")


def test_the_still_running_note_keeps_a_just_stopped_agent_live(
    mk_bot, run_async,
):
    """The fact-4 sequence: the agent's SubagentStop, then the parent's
    notification saying it stopped with background work still running."""
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_STILL_RUNNING.format(tid="a2d0"))
    assert sess.bg_agent_labels() == ["pipeline-runner"]


def test_the_final_note_does_not_revive_the_agent(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_DONE.format(tid="a2d0"))
    assert sess.bg_agent_labels() == []


def test_a_note_for_an_id_never_seen_starting_adds_nothing(mk_bot, run_async):
    """A background Bash task's id (bhvkhjl4x) never had a SubagentStart."""
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_STILL_RUNNING.format(tid="bhvkhjl4x"))
    assert sess.bg_agent_labels() == []


def test_a_result_quoting_the_note_is_not_the_note(mk_bot, run_async):
    """Only the notification's own <note> counts; the agent's <result> is
    free text (this very feature's pipeline quotes the wording)."""
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    prompt = _NOTE_DONE.format(tid="a2d0").replace(
        "</task-notification>",
        "<result>It says \"background work of its own still running\"."
        "</result>\n</task-notification>")
    _hook(run_async, recv, sess, "UserPromptSubmit", prompt=prompt)
    assert sess.bg_agent_labels() == []


def test_a_silent_agent_expires_after_the_window(mk_bot):
    from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS

    bot = mk_bot()
    sess = _sess(bot)
    now = time.monotonic() + 10_000
    assert sess.bg_agent_started("a1", "pipeline-runner", now)
    assert sess.sweep_bg_agents(now + SUBAGENT_SILENCE_SECONDS - 1,
                                SUBAGENT_SILENCE_SECONDS) == []
    assert sess.sweep_bg_agents(now + SUBAGENT_SILENCE_SECONDS + 1,
                                SUBAGENT_SILENCE_SECONDS) == ["a1"]
    assert sess.bg_agent_labels() == []


def test_any_hook_from_the_agent_refreshes_its_liveness(mk_bot, run_async):
    from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS

    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
          agent_type="pipeline-runner")
    sess.bg_agents["a1"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60
    _hook(run_async, recv, sess, "PostToolUse", agent_id="a1",
          tool_name="Bash", tool_input={"command": "ls"})
    assert sess.sweep_bg_agents(time.monotonic(),
                                SUBAGENT_SILENCE_SECONDS) == []


def test_the_monitor_sweeps_a_silent_agent(mk_bot, run_async, monkeypatch):
    from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS, SessionMonitor

    bot = mk_bot()
    sess = _sess(bot)
    sess.status = Status.IDLE
    _agents(sess, ("a1", "pipeline-runner"))
    sess.bg_agents["a1"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60

    async def _names():
        return [sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

    async def _noop(*_a, **_kw):
        return None

    run_async(_bounded(SessionMonitor(bot.registry, _noop)._scan()))
    assert sess.bg_agent_labels() == []


def test_session_end_clears_the_set_and_the_pending_line(mk_bot, run_async):
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a1",
          agent_type="pipeline-runner")
    sess.agents_lines = [{"msg_id": 1, "ids": ["a1"]}]
    _hook(run_async, recv, sess, "SessionEnd", reason="other")
    assert sess.status == Status.GONE
    assert sess.bg_agent_labels() == []
    assert sess.agents_lines == []


# ── R2: the ⏳ line on the delivered answer ──────────────────────────────────

@pytest.mark.parametrize("layout, tools, method", [
    ("card", (("Read: /a.py", True),), "sendRichMessage"),
    ("card", (), "sendRichMessage"),          # 8.32's tool-less one message
    ("replace", (("Read: /a.py", True),), "sendRichMessage"),
    ("merged", (("Read: /a.py", True),), "editMessageText"),
])
def test_an_answer_delivered_while_an_agent_runs_says_so(
    mk_bot, run_async, wire, layout, tools, method,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout=layout, tools=tools)
    _agents(sess, ("a1", "pipeline-runner"))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)

    answer = [p for p in wire.of(method) if ANSWER in _markdown(p)]
    assert len(answer) == 1
    text = _markdown(answer[0])
    assert _last_line(text) == RUNNING_1
    assert text.count("⏳") == 1
    (rec,) = sess.agents_lines
    assert rec["text"] == text


def test_no_agent_no_line(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (answer,) = wire.of("sendRichMessage")
    assert "⏳" not in _markdown(answer)
    assert sess.agents_lines == []


def test_two_agents_are_counted_and_named(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "pipeline-runner"), ("a2", "ship-reviewer"))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (answer,) = wire.of("sendRichMessage")
    assert _last_line(_markdown(answer)) == (
        "⏳ 2 agents still running — pipeline-runner, ship-reviewer · "
        "results will follow here")


def test_a_long_label_is_truncated(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "x" * 80))
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    line = _last_line(_markdown(wire.of("sendRichMessage")[0]))
    assert "x" * 80 not in line
    assert "…" in line
    assert len(line) < 120


def test_the_fact_4_sequence_ends_with_the_line(mk_bot, run_async, wire):
    """The live incident end to end through the hook receiver: start, the
    agent's interim stop, the parent's still-running note, the answer."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_STILL_RUNNING.format(tid="a2d0"))
    sess.status = Status.IDLE
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (answer,) = [p for p in wire.of("sendRichMessage")
                 if ANSWER in _markdown(p)]
    assert _last_line(_markdown(answer)) == RUNNING_1


def test_the_plain_text_fallback_does_not_carry_the_line(
    mk_bot, run_async, wire, monkeypatch,
):
    """A chunked plain-text fallback cannot be rebuilt for the ✅ edit, so a
    ⏳ line in it would stay for good: it goes out without one."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "general_purpose"))

    async def _post(method, payload, **_kw):
        wire.calls.append((method, payload))
        return {"ok": False, "error_code": 400, "description": "nope"}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    chunks = [c["_args"][1] for c in wire.of("send_message")]
    assert chunks and ANSWER in chunks[-1]
    assert not any("⏳" in c for c in chunks)
    assert sess.agents_lines == []


@pytest.mark.parametrize("long", [
    ("paragraph of answer text. " * 40 + "\n\n") * 60,
    "x" * 40_000,   # no safe boundary: cut at the byte budget itself
], ids=["paragraphs", "no-boundary"])
def test_a_long_answer_keeps_the_line_last(mk_bot, run_async, wire, long):
    """The line is budgeted for: the truncated body plus the line still
    fit the rich-message ceiling."""
    from aipager.bot.animation import _RICH_LIMIT

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "pipeline-runner"))
    _finish(run_async, bot, sess, summary=long, raw_md=long)
    (answer,) = wire.of("sendRichMessage")
    text = _markdown(answer)
    assert _last_line(text) == RUNNING_1
    assert len(text.encode()) <= _RICH_LIMIT


def test_a_held_answer_does_not_carry_the_line(mk_bot, run_async, wire):
    """Muted at the turn's end: the answer is held and goes out later with
    a late marker. A ⏳ line frozen into it could never be settled, so it is
    not added, and no edit is owed."""
    from aipager.bot.flood import MUTE
    from aipager.bot.held import HELD

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "pipeline-runner"))
    MUTE.mute(CHAT, 600)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (held,) = HELD.pending(CHAT)
    assert "⏳" not in held.rich_text
    assert sess.agents_lines == []


def test_a_merged_answer_held_by_a_mute_does_not_carry_the_line(
    mk_bot, run_async, wire,
):
    from aipager.bot.flood import MUTE
    from aipager.bot.held import HELD

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout="merged")
    _agents(sess, ("a1", "pipeline-runner"))
    MUTE.mute(CHAT, 600)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    held = HELD.pending(CHAT)
    assert held
    assert all("⏳" not in h.rich_text for h in held)
    assert sess.agents_lines == []


# ── R3: one edit to ✅ once they are all gone ───────────────────────────────

def _delivered_with(run_async, bot, sess, *pairs):
    _agents(sess, *pairs)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    assert sess.agents_lines
    sess.agents_lines[-1]["sent_wall"] -= 6 * 60


def test_the_agents_stop_edits_the_line_once_to_done(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    rec = dict(sess.agents_lines[0])
    before = len(wire.calls)

    _hook(run_async, recv, sess, "SubagentStop", agent_id="a1",
          agent_type="pipeline-runner")
    now = time.monotonic() + 1_000
    assert bool(sess.agents_lines_due(now)) is False       # settle starts now
    later = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    assert bool(sess.agents_lines_due(later)) is True
    _settle(run_async, bot, sess, later)

    new = wire.calls[before:]
    assert [m for m, _p in new] == ["editMessageText"]
    edit = new[0][1]
    assert edit["message_id"] == rec["msg_id"]
    assert _last_line(_markdown(edit)) == "✅ pipeline-runner — done (6m)"
    assert _markdown(edit)[: -len("✅ pipeline-runner — done (6m)")] == \
        rec["text"][: -len(RUNNING_1)]
    # ORNAMENT, skip-class: the flood gate may refuse it, never queue it.
    assert edit["_kw"] == {"kind": "skip", "priority": "ornament"}
    assert sess.agents_lines == []
    assert bool(sess.agents_lines_due(later + 100)) is False
    _settle(run_async, bot, sess, later + 100)
    assert len(wire.calls) == before + 1


def test_one_of_two_finishing_is_no_edit_both_is_one(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"),
                    ("a2", "ship-reviewer"))
    before = len(wire.calls)
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a1",
          agent_type="pipeline-runner")
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    assert bool(sess.agents_lines_due(now + 3600)) is False
    assert len(wire.calls) == before

    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2",
          agent_type="ship-reviewer")
    bool(sess.agents_lines_due(now))
    later = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    assert bool(sess.agents_lines_due(later)) is True
    _settle(run_async, bot, sess, later)
    (edit,) = [p for m, p in wire.calls[before:]]
    assert _last_line(_markdown(edit)) == "✅ 2 agents done (6m)"


def test_a_phantom_stop_never_settles_the_line(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    _hook(run_async, recv, sess, "SubagentStop", agent_id="zzz", agent_type="")
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    assert bool(sess.agents_lines_due(now + 3600)) is False


def test_a_note_right_after_the_stop_holds_the_edit(mk_bot, run_async, wire):
    """The agent stopped AGAIN with background work: the note lands within
    the settle window and the line stays ⏳."""
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a2d0", "pipeline-runner"))
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    now = time.monotonic() + 1_000
    assert bool(sess.agents_lines_due(now)) is False
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_STILL_RUNNING.format(tid="a2d0"))
    assert bool(sess.agents_lines_due(now + BG_AGENTS_SETTLE_SECONDS + 1)) is False


def test_an_agent_that_comes_back_and_stops_again_restarts_the_settle(
    mk_bot, run_async, wire,
):
    bot = _wire_bot(mk_bot, wire)
    recv = _receiver(bot)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a2d0", "pipeline-runner"))
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    t0 = time.monotonic() + 1_000
    assert bool(sess.agents_lines_due(t0)) is False
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=_NOTE_STILL_RUNNING.format(tid="a2d0"))
    assert bool(sess.agents_lines_due(t0 + 100)) is False
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    t1 = t0 + 200
    assert bool(sess.agents_lines_due(t1)) is False          # a fresh settle
    assert bool(sess.agents_lines_due(t1 + BG_AGENTS_SETTLE_SECONDS - 1)) is False
    assert bool(sess.agents_lines_due(t1 + BG_AGENTS_SETTLE_SECONDS + 0.1)) is True


def test_an_agent_started_after_the_answer_does_not_hold_its_edit(
    mk_bot, run_async, wire,
):
    """The line named the agents running when it went out; those are the
    ones it waits for."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    _agents(sess, ("a2", "ship-reviewer"))
    sess.bg_agent_stopped("a1", time.monotonic())
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    assert bool(sess.agents_lines_due(now + BG_AGENTS_SETTLE_SECONDS + 0.1)) is True


def test_the_sets_are_bounded():
    from aipager.state import ACTIVE_SUBAGENTS_CAP, BG_AGENTS_RECENT_CAP

    sess = TrackedSession(name="x", label="x")
    for i in range(ACTIVE_SUBAGENTS_CAP + 5):
        sess.bg_agent_started(f"a{i}", "t", float(i))
    assert len(sess.bg_agents) == ACTIVE_SUBAGENTS_CAP
    assert "a0" not in sess.bg_agents                 # the oldest went
    for i in range(5, ACTIVE_SUBAGENTS_CAP + 5):
        sess.bg_agent_stopped(f"a{i}", 0.0)
    assert len(sess.bg_agents_recent) == BG_AGENTS_RECENT_CAP


def test_the_monitor_scan_makes_the_edit(mk_bot, run_async, wire, monkeypatch):
    """The wiring: the 2 s scan asks for the edit once it is due."""
    from aipager.session_monitor import SessionMonitor

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.bg_agent_stopped("a1", time.monotonic())
    sess.agents_lines[0]["gone_at"] = 1.0  # long settled

    async def _names():
        return [sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)
    seen = []

    async def _notify(s, event, context):
        seen.append(event)
        if event == "agents_line_done":
            await bot.notify(s, event, context)

    run_async(_bounded(SessionMonitor(bot.registry, _notify)._scan()))
    assert seen.count("agents_line_done") == 1
    assert _last_line(_markdown(wire.of("editMessageText")[-1])) == \
        "✅ pipeline-runner — done (6m)"


def test_a_deleted_answer_is_never_retried(mk_bot, run_async, wire, monkeypatch):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.bg_agent_stopped("a1", time.monotonic())
    calls = []

    async def _post(method, payload, **kw):
        calls.append(method)
        return {"ok": False, "error_code": 400,
                "description": "Bad Request: message to edit not found"}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    _settle(run_async, bot, sess, now + 10)
    assert calls == ["editMessageText"]
    assert sess.agents_lines == []


def test_a_skipped_edit_is_retried_once_and_then_given_up(
    mk_bot, run_async, wire, monkeypatch,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.bg_agent_stopped("a1", time.monotonic())
    calls = []

    async def _post(method, payload, **kw):
        calls.append(method)
        raise FloodSkipped(CHAT, method)

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    t1 = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    assert bool(sess.agents_lines_due(t1))
    _settle(run_async, bot, sess, t1)
    assert sess.agents_lines != []
    assert bool(sess.agents_lines_due(t1 + 1)) is False     # not straight away
    t2 = t1 + 3600
    assert bool(sess.agents_lines_due(t2))
    _settle(run_async, bot, sess, t2)
    assert calls == ["editMessageText", "editMessageText"]
    assert sess.agents_lines == []                  # bounded: given up


def test_muted_no_edit_and_one_try_after_the_lift(mk_bot, run_async, wire):
    from aipager.bot.flood import MUTE

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.bg_agent_stopped("a1", time.monotonic())
    before = len(wire.calls)
    MUTE.mute(CHAT, 600)
    now = time.monotonic() + 1_000
    bool(sess.agents_lines_due(now))
    t1 = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    _settle(run_async, bot, sess, t1)
    assert len(wire.calls) == before                 # nothing into the ban
    assert bool(sess.agents_lines_due(t1 + 300)) is False   # not before the lift
    MUTE.clear()
    t2 = t1 + 601
    assert bool(sess.agents_lines_due(t2))
    _settle(run_async, bot, sess, t2)
    assert [m for m, _p in wire.calls[before:]] == ["editMessageText"]
    assert sess.agents_lines == []


def test_a_gone_session_gets_no_edit(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    before = len(wire.calls)
    bot.registry.transition(sess.name, Status.GONE)
    assert sess.bg_agent_labels() == []
    now = time.monotonic() + 1_000
    assert bool(sess.agents_lines_due(now + 3600)) is False
    _settle(run_async, bot, sess, now + 3600)
    assert len(wire.calls) == before


# ── R4: the pinned bar ───────────────────────────────────────────────────────

def test_the_bar_shows_and_clears_the_count(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    bot = mk_bot()
    sess = _sess(bot, label="aipager_boss")
    assert bot._render_pinned(CHAT)[0] == "💤 aipager_boss — idle"
    _agents(sess, ("a1", "pipeline-runner"))
    assert bot._render_pinned(CHAT)[0] == \
        "💤 aipager_boss — idle · ⏳ 1 agent running"
    _agents(sess, ("a2", "ship-reviewer"))
    assert bot._render_pinned(CHAT)[0] == \
        "💤 aipager_boss — idle · ⏳ 2 agents running"
    sess.bg_agent_stopped("a1", time.monotonic())
    sess.bg_agent_stopped("a2", time.monotonic())
    assert bot._render_pinned(CHAT)[0] == "💤 aipager_boss — idle"


def test_the_bar_puts_the_count_on_the_sessions_own_line(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    bot = mk_bot()
    a = _sess(bot, label="a")
    a.status = Status.BUSY
    b = _sess(bot, label="b")
    _agents(b, ("a1", "pipeline-runner"))
    assert bot._render_pinned(CHAT)[0] == (
        "⚙️ 1 working · 1 idle\n"
        "• <b>a</b> — working\n"
        "• <b>b</b> — idle · ⏳ 1 agent running")


def test_the_bar_never_names_the_agents(mk_bot, monkeypatch):
    """Only the count moves the bar: an agent swapped for another of a
    different type renders identically, so it costs no edit."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    bot = mk_bot()
    sess = _sess(bot, label="solo")
    _agents(sess, ("a1", "pipeline-runner"))
    first = bot._render_pinned(CHAT)
    sess.bg_agent_stopped("a1", time.monotonic())
    _agents(sess, ("a2", "ship-reviewer"))
    assert bot._render_pinned(CHAT)[0] == first[0]
    assert "pipeline-runner" not in first[0]


# ── the rendering helpers ────────────────────────────────────────────────────

def test_done_line_durations():
    from aipager.bot.agents_line import done_line

    assert done_line(["x"], 45) == "✅ x — done (45s)"
    assert done_line(["x"], 6 * 60 + 59) == "✅ x — done (6m)"
    assert done_line(["x", "y"], 3 * 3600 + 5 * 60) == "✅ 2 agents done (3h 5m)"


def test_the_running_line_names_at_most_three_and_counts_the_rest():
    from aipager.bot.agents_line import running_line

    line = running_line(["a", "b", "c", "d", "e"])
    assert line == ("⏳ 5 agents still running — a, b, c +2 more · results "
                    "will follow here")


def test_markdown_metacharacters_in_a_label_are_escaped():
    from aipager.bot.agents_line import running_line

    assert "general\\_purpose" in running_line(["general_purpose"],
                                               markdown=True)


def test_the_set_is_never_persisted():
    from aipager.state import SessionRegistry

    for name in ("bg_agents", "bg_agents_recent", "agents_lines"):
        assert name not in SessionRegistry._PERSIST_FIELDS
    assert TrackedSession(name="x", label="x").bg_agents == {}


# ── review iteration 1 ───────────────────────────────────────────────────────

def test_two_answers_carrying_the_line_are_each_settled_once(
    mk_bot, run_async, wire,
):
    """A job's agent reports back more than once, or the operator asks
    something while it runs: every ⏳ answer gets its ✅, not just the
    newest."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.trigger_msg_id = TRIGGER
    sess.status = Status.IDLE
    _finish(run_async, bot, sess, summary="second answer",
            raw_md="second answer")
    assert len(sess.agents_lines) == 2
    ids = [r["msg_id"] for r in sess.agents_lines]
    before = len(wire.calls)
    sess.bg_agent_stopped("a1", time.monotonic())
    now = time.monotonic() + 1_000
    sess.agents_lines_due(now)
    later = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    _settle(run_async, bot, sess, later)
    edits = [p for m, p in wire.calls[before:] if m == "editMessageText"]
    assert sorted(e["message_id"] for e in edits) == sorted(ids)
    assert all(_last_line(_markdown(e)).startswith("✅ pipeline-runner — done")
               for e in edits)
    assert sess.agents_lines == []
    _settle(run_async, bot, sess, later + 3600)
    assert len(wire.calls) == before + 2


def test_the_pending_answers_are_bounded(mk_bot, run_async, wire):
    from aipager.state import AGENTS_LINES_CAP

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "pipeline-runner"))
    for i in range(AGENTS_LINES_CAP + 2):
        sess.trigger_msg_id = TRIGGER
        sess.status = Status.IDLE
        _finish(run_async, bot, sess, summary=f"answer {i}",
                raw_md=f"answer {i}")
    assert len(sess.agents_lines) == AGENTS_LINES_CAP
    assert "answer 0" not in sess.agents_lines[0]["text"]


def test_an_expired_agent_leaves_its_line_as_sent(
    mk_bot, run_async, wire, monkeypatch,
):
    """Silence is not completion (an agent parked on long background work
    emits nothing): the ⏳ is never turned into ✅ on no evidence."""
    from aipager.session_monitor import SUBAGENT_SILENCE_SECONDS, SessionMonitor

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _delivered_with(run_async, bot, sess, ("a1", "pipeline-runner"))
    sess.bg_agents["a1"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60
    before = len(wire.calls)

    async def _names():
        return [sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

    async def _notify(s, event, context):
        if event == "agents_line_done":
            await bot.notify(s, event, context)

    monitor = SessionMonitor(bot.registry, _notify)
    run_async(_bounded(monitor._scan()))
    assert sess.bg_agent_labels() == []
    assert sess.agents_lines == []
    assert [m for m, _p in wire.calls[before:]
            if m == "editMessageText"] == []


def test_each_notification_in_one_prompt_is_read_on_its_own(
    mk_bot, run_async,
):
    """A background shell's notification (no note) ahead of the agent's:
    the agent's own task id is the one restored."""
    bot = mk_bot()
    recv = _receiver(bot)
    sess = _sess(bot)
    _hook(run_async, recv, sess, "SubagentStart", agent_id="a2d0",
          agent_type="pipeline-runner")
    _hook(run_async, recv, sess, "SubagentStop", agent_id="a2d0",
          agent_type="pipeline-runner")
    shell = ("<task-notification>\n<task-id>bhvkhjl4x</task-id>\n<status>"
             "completed</status>\n</task-notification>\n")
    _hook(run_async, recv, sess, "UserPromptSubmit",
          prompt=shell + _NOTE_STILL_RUNNING.format(tid="a2d0"))
    assert sess.bg_agent_labels() == ["pipeline-runner"]


def test_an_agent_starting_during_the_send_is_not_waited_for(
    mk_bot, run_async, wire, monkeypatch,
):
    """The line, the ids it waits for and the labels its ✅ repeats are one
    snapshot: an agent that starts while the answer is in flight was never
    named, so it neither holds the ✅ nor changes its count."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agents(sess, ("a1", "pipeline-runner"))

    async def _post(method, payload, **_kw):
        wire.calls.append((method, payload))
        if method == "sendRichMessage":
            sess.bg_agent_started("a2", "ship-reviewer", time.monotonic())
        wire.next_id += 1
        return {"ok": True, "result": {"message_id": wire.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    (rec,) = sess.agents_lines
    assert rec["ids"] == ["a1"]
    assert rec["labels"] == ["pipeline-runner"]
    sess.bg_agent_stopped("a1", time.monotonic())
    now = time.monotonic() + 1_000
    sess.agents_lines_due(now)
    assert sess.agents_lines_due(now + BG_AGENTS_SETTLE_SECONDS + 0.1)
