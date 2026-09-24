"""A background job's interim answer goes out NOW (roadmap 8.42, operator
decision 2026-09-24).

Live 2026-09-24 19:52: the operator's session started a background agent
(a ~4 min full test run) and Claude's turn ended at once with an answer. The
terminal showed it; Telegram showed only the waiting card — "🔄 aipager_boss
· 1 agent (general-purpose) still working" — because the job model held
every interim answer in ``job_interim_buffer`` and merged it into the job's
final message. The operator wants the opposite.

R1  a turn that ends while the job is open sends its answer now, as its own
    message threaded to the prompt, ending with 8.41's ⏳ line. The waiting
    card stays the job's live status (and its Stop button) in every layout.
R2  nothing is buffered: at job end the continuation's answer goes out on
    its own and an interim is never re-sent — the delivered-digest ring
    (8.39, persisted) holds that across a restart too.
R3  when the agents finish, 8.41's one silent edit turns each interim's ⏳
    line into ✅.
R4  the injected reply guidance says the reply is delivered now.

Every outbound call lands in one list, in order (the rich transport's
``_post`` and the PTB bot's raw calls alike). ``asyncio`` is never patched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.dtach import hook_receiver as hr
from aipager.state import (
    BG_AGENTS_SETTLE_SECONDS,
    SessionRegistry,
    Status,
)

CHAT = 555
TRIGGER = 7
CARD = 42
INTERIM = "Launched the full test run in the background; the fix itself is in."
INTERIM_2 = "The first run found one failure; a second agent is re-running it."
FINAL = "All 9597 tests pass. The fix is ready to commit."
RUNNING_1 = ("⏳ 1 agent still running — general-purpose · results will "
             "follow here")


# ── harness ─────────────────────────────────────────────────────────────────

class _Wire:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 9000

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def carrying(self, text: str) -> list[tuple[str, dict]]:
        """Every call whose visible text contains *text*."""
        out = []
        for m, p in self.calls:
            body = ""
            if "rich_message" in p:
                body = p["rich_message"].get("markdown", "")
            elif "_args" in p and len(p["_args"]) > 1:
                body = str(p["_args"][1])
            elif "text" in p:
                body = str(p["text"])
            if text in body:
                out.append((m, p))
        return out


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **kw):
        w.calls.append((method, dict(payload, _kw=kw)))
        w.next_id += 1
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _wire_bot(mk_bot, w: _Wire, registry=None):
    bot = mk_bot(registry)

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


def _sess(bot, *, layout="card", label="boss"):
    s = bot.registry.get_or_create(f"claude-{label}")
    s.label = label
    s.status = Status.IDLE
    s.scope_chat_id = CHAT
    s.scope_kind = "dm"
    s.busy_msg_id = CARD
    s.busy_card_trigger = TRIGGER
    s.trigger_msg_id = TRIGGER
    s.busy_started_at = time.monotonic() - 5
    s.tool_history = [("Agent: run the full suite", True)]
    prefs.set_preference(CHAT, "layout", layout)
    return s


def _agent_starts(sess, agent_id="a1", agent_type="general-purpose"):
    """A background agent the job launched: the per-turn table the job
    model reads AND 8.41's live set, as the SubagentStart hook fills them."""
    now = time.monotonic()
    sess.add_subagent(agent_id, {"type": agent_type, "started_at": now,
                                 "description": "run the suite"})
    assert sess.bg_agent_started(agent_id, agent_type, now)


def _agent_stops(sess, agent_id="a1"):
    sess.active_subagents.pop(agent_id, None)
    sess.bg_agent_stopped(agent_id, time.monotonic())


async def _drain_background():
    for _ in range(5):
        pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set())
                   if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _idle(run_async, bot, sess, text):
    """A Stop landing on the session: IDLE with *text* as its answer."""
    sess.status = Status.IDLE

    async def _go():
        await bot.notify(sess, "idle_prompt",
                         {"summary": text, "raw_md": text})
        await _drain_background()
    run_async(asyncio.wait_for(_go(), timeout=5))


def _continuation_ends(run_async, bot, sess, text):
    """The <task-notification> continuation turn's own Stop."""
    sess.job_continuation_active = True
    _idle(run_async, bot, sess, text)


def _settle(run_async, bot, sess, now):
    run_async(asyncio.wait_for(
        bot.notify(sess, "agents_line_done", {"now": now}), timeout=5))


def _markdown(payload: dict) -> str:
    return payload["rich_message"]["markdown"]


def _last_line(text: str) -> str:
    return text.rpartition("\n\n")[2]


def _answers(wire, text):
    """Standalone sends (not card edits) carrying *text*."""
    return [(m, p) for m, p in wire.carrying(text)
            if m in ("sendRichMessage", "send_message")]


def _card_consumed(wire) -> bool:
    return any(p.get("message_id") == CARD or CARD in p.get("_args", ())
               for m, p in wire.calls
               if m in ("delete_message", "deleteMessage"))


# ── R1: the interim answer goes out now ─────────────────────────────────────

@pytest.mark.parametrize("layout", ["card", "merged", "replace"])
def test_the_interim_answer_goes_out_now_with_the_agents_line(
    mk_bot, run_async, wire, layout,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout=layout)
    _agent_starts(sess)

    _idle(run_async, bot, sess, INTERIM)

    assert sess.job_background_open() is True
    (sent,) = _answers(wire, INTERIM)
    method, payload = sent
    assert method == "sendRichMessage"
    text = _markdown(payload)
    assert text.startswith("💬 **boss**\n\n")
    assert _last_line(text) == RUNNING_1
    # Threaded to the prompt, and notifying like any answer.
    assert payload["reply_to_message_id"] == TRIGGER
    assert not payload.get("disable_notification")
    # The card is untouched as the job's status: never deleted, never
    # edited into the answer — in merged and replace too.
    assert sess.busy_msg_id == CARD
    assert not _card_consumed(wire)
    assert not [p for m, p in wire.calls
                if m == "editMessageText" and INTERIM in _markdown(p)]
    # 8.41 owes that line its ✅.
    (rec,) = sess.agents_lines
    assert rec["text"] == text


def test_the_waiting_card_keeps_its_stop_button(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    edits = [p for m, p in wire.calls if m == "editMessageText"
             and p.get("message_id") == CARD]
    assert edits, "the card is re-rendered to its waiting frame"
    markup = json.dumps(edits[-1].get("reply_markup") or {})
    assert "Stop" in markup


def test_a_stray_idle_with_the_same_answer_sends_nothing_more(
    mk_bot, run_async, wire,
):
    """Claude Code's idle Notification (60 s after the Stop) and phantom
    re-entries carry the same text: the digest ring refuses it."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    _idle(run_async, bot, sess, INTERIM)
    assert len(_answers(wire, INTERIM)) == 1
    assert len(sess.agents_lines) == 1


def test_the_interim_is_confirmed_in_the_persisted_ring(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    before = time.time()
    _idle(run_async, bot, sess, INTERIM)
    digest = hashlib.md5(INTERIM.encode()).hexdigest()
    assert digest in sess.persisted_digests()
    assert sess.answer_delivered_wall >= before


# ── R2: the job's end sends only its own answer ─────────────────────────────

@pytest.mark.parametrize("layout", ["card", "merged", "replace"])
def test_the_job_end_sends_the_final_answer_and_never_the_interim_again(
    mk_bot, run_async, wire, layout,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout=layout)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    _agent_stops(sess)
    _continuation_ends(run_async, bot, sess, FINAL)

    assert sess.job_background_open() is False
    # The interim went out exactly once, as a standalone answer — and no
    # card edit (waiting or finished) quotes it either.
    assert len(_answers(wire, INTERIM)) == 1
    assert len(wire.carrying(INTERIM)) == 1
    finals = wire.carrying(FINAL)
    assert len(finals) == 1
    # The final answer carries no interim text and no ⏳ line.
    final_text = (_markdown(finals[0][1]) if "rich_message" in finals[0][1]
                  else "")
    assert INTERIM not in final_text
    assert "⏳" not in final_text


def test_the_job_end_settles_the_interim_line_to_done(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    rec = dict(sess.agents_lines[0])
    _agent_stops(sess)
    _continuation_ends(run_async, bot, sess, FINAL)
    before = len(wire.calls)

    now = time.monotonic() + 1_000
    assert bool(sess.agents_lines_due(now)) is False
    later = now + BG_AGENTS_SETTLE_SECONDS + 0.1
    _settle(run_async, bot, sess, later)

    new = wire.calls[before:]
    assert [m for m, _p in new] == ["editMessageText"]
    edit = new[0][1]
    assert edit["message_id"] == rec["msg_id"]
    assert _last_line(_markdown(edit)).startswith(
        "✅ general-purpose — done (")
    assert _markdown(edit)[: -len(_last_line(_markdown(edit)))] == \
        rec["text"][: -len(RUNNING_1)]
    assert edit["_kw"] == {"kind": "skip", "priority": "ornament"}
    assert sess.agents_lines == []


def test_two_interims_in_one_job_each_go_out_once_with_their_own_line(
    mk_bot, run_async, wire,
):
    """The first agent reports back and its continuation launches a second
    one: two interim answers, one job, one final."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess, "a1")
    _idle(run_async, bot, sess, INTERIM)
    _agent_starts(sess, "a2")
    _agent_stops(sess, "a1")
    _continuation_ends(run_async, bot, sess, INTERIM_2)
    assert sess.job_background_open() is True
    _agent_stops(sess, "a2")
    _continuation_ends(run_async, bot, sess, FINAL)

    assert len(_answers(wire, INTERIM)) == 1
    assert len(_answers(wire, INTERIM_2)) == 1
    assert len(wire.carrying(FINAL)) == 1
    assert INTERIM not in _markdown(wire.carrying(INTERIM_2)[0][1])
    assert [r["ids"] for r in sess.agents_lines] == [["a1"], ["a2"]]
    # Both settle, each with its own edit.
    now = time.monotonic() + 1_000
    sess.agents_lines_due(now)
    before = len(wire.calls)
    _settle(run_async, bot, sess, now + BG_AGENTS_SETTLE_SECONDS + 0.1)
    edits = wire.calls[before:]
    assert [m for m, _p in edits] == ["editMessageText", "editMessageText"]
    assert len({p["message_id"] for _m, p in edits}) == 2


# ── R6: Stop, reclaim, restart ──────────────────────────────────────────────

def test_stop_while_waiting_resends_nothing(mk_bot, run_async, wire,
                                            monkeypatch):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input",
                        AsyncMock(return_value=True))
    before = len(wire.calls)

    run_async(asyncio.wait_for(bot._stop_session_core(sess), timeout=5))

    assert not [c for c in wire.calls[before:] if INTERIM in json.dumps(
        c[1], default=str)]
    assert sess.active_subagents == {}
    assert sess.job_background_open() is False


def test_a_new_prompt_reclaims_the_card_and_resends_nothing(
    mk_bot, run_async, wire,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    # A genuinely new prompt while the agent still runs: transition() marks
    # the reclaim; the old card is settled and a new turn starts.
    bot.registry.transition(sess.name, Status.BUSY)
    assert sess.job_reclaim_pending is True
    sess.trigger_msg_id = TRIGGER + 1
    _idle(run_async, bot, sess, FINAL)

    assert len(_answers(wire, INTERIM)) == 1
    assert len(wire.carrying(FINAL)) == 1
    assert INTERIM not in json.dumps(wire.carrying(FINAL)[0][1], default=str)


def test_a_restart_mid_job_resends_nothing(mk_bot, run_async, wire,
                                           tmp_state_file, tmp_path):
    """Save after the interim went out, load in a fresh daemon, and let the
    stray idle Notification (60 s after the Stop) and the job's end land:
    the interim never goes out again."""
    registry = SessionRegistry()
    bot = _wire_bot(mk_bot, wire, registry)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    assert len(_answers(wire, INTERIM)) == 1
    registry.save()

    registry2 = SessionRegistry()
    registry2.load()
    bot2 = _wire_bot(mk_bot, wire, registry2)
    sess2 = registry2.get(sess.name)
    assert sess2 is not None
    sess2.status = Status.IDLE
    # The idle Notification's late path, reading the transcript's newest
    # text — the interim already delivered.
    tp = tmp_path / "t.jsonl"
    tp.write_text("\n".join(json.dumps(x) for x in (
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": INTERIM}]}},
    )) + "\n")
    recv = hr.HookReceiver(registry2, bot2.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "Notification", "notification_type": "idle_prompt",
        "session": sess.name, "transcript_path": str(tp),
        "message": "Claude is waiting for your input"}).encode()))
    run_async(asyncio.wait_for(_drain_background(), timeout=5))
    # And the continuation that produced no text of its own reads the same.
    sess2 = registry2.get(sess.name)
    sess2.scope_chat_id = CHAT
    _continuation_ends(run_async, bot2, sess2, INTERIM)

    assert len(_answers(wire, INTERIM)) == 1


# ── flood: held, not lost, and no ⏳ frozen into it ─────────────────────────

def test_a_muted_interim_is_held_without_the_line(mk_bot, run_async, wire):
    from aipager.bot.flood import MUTE
    from aipager.bot.held import HELD

    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    MUTE.mute(CHAT, 600)
    _idle(run_async, bot, sess, INTERIM)

    assert _answers(wire, INTERIM) == []
    (entry,) = HELD.pending(CHAT)
    assert INTERIM in entry.rich_text
    assert "⏳" not in entry.rich_text
    assert sess.agents_lines == []
    digest = hashlib.md5(INTERIM.encode()).hexdigest()
    assert digest not in sess.persisted_digests()       # pending until it lands
    assert entry.digests == (digest,)


def test_a_long_interim_keeps_the_line_last_and_attaches_the_rest(
    mk_bot, run_async, wire,
):
    from aipager.bot.animation import _RICH_LIMIT

    long = ("paragraph of interim text. " * 40 + "\n\n") * 60
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, long)
    (answer,) = wire.of("sendRichMessage")
    text = _markdown(answer)
    assert _last_line(text) == RUNNING_1
    assert len(text.encode()) <= _RICH_LIMIT
    assert len(wire.of("send_document")) == 1


# ── R4: the guidance ───────────────────────────────────────────────────────

def test_the_guidance_says_the_reply_is_delivered_now():
    text = prefs.style_text(prefs.get_preferences(CHAT))
    assert ("If you start background agents, your reply is delivered now; "
            "results from those agents arrive later as a separate message."
            ) in text
    assert "ONE message" not in text
    assert "never promise" not in text


def test_a_long_interim_that_falls_back_to_plain_text_is_not_attached(
    mk_bot, run_async, wire, monkeypatch,
):
    """The plain chunks already carry the whole text: an attachment on top
    would be the same answer twice. Only the first chunk is threaded."""
    long = ("paragraph of interim text. " * 40 + "\n\n") * 60
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)

    async def _post(method, payload, **_kw):
        wire.calls.append((method, payload))
        return {"ok": False, "error_code": 400, "description": "nope"}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    _idle(run_async, bot, sess, long)
    chunks = wire.of("send_message")
    assert len(chunks) > 1
    assert chunks[0]["reply_to_message_id"] == TRIGGER
    assert all(c["reply_to_message_id"] is None for c in chunks[1:])
    assert not any("⏳" in c["_args"][1] for c in chunks)
    assert wire.of("send_document") == []
    assert sess.agents_lines == []


def test_a_reply_to_the_interim_routes_to_its_session(mk_bot, run_async, wire):
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)
    msg_id = sess.agents_lines[0]["msg_id"]
    assert bot.registry.get_session_by_msg(msg_id, CHAT) is sess


def test_a_delivered_interim_marks_the_state_file_for_saving(
    mk_bot, run_async, monkeypatch,
):
    """Even a send that returned no message id to track: the confirmed
    digest must reach the state file, or a restart re-sends it."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(return_value={}))
    sess = bot.registry.get_or_create("claude-boss")
    sess.scope_chat_id = CHAT
    bot.registry._dirty = False
    run_async(bot._deliver_job_interim(sess, INTERIM, time.time()))
    assert bot.registry._dirty is True


@pytest.mark.parametrize("layout", ["card", "merged"])
@pytest.mark.parametrize("hook_live", [True, False])
def test_the_card_never_quotes_the_interim_it_sent(
    mk_bot, run_async, wire, layout, hook_live,
):
    """The interim message's prose is on the card before its Stop (the
    MessageDisplay hook, or the transcript scan). With the answer now its
    own message, neither the waiting frame nor the job's finished card may
    quote it a second time (review rev-iter1-001)."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot, layout=layout)
    sess.stream_hook_live = hook_live
    _agent_starts(sess)
    sess.stream_commentary = [(len(sess.tool_history), INTERIM)]
    _idle(run_async, bot, sess, INTERIM)
    _agent_stops(sess)
    sess.tool_history.append(("Bash: pytest", True))  # the continuation's
    _continuation_ends(run_async, bot, sess, FINAL)

    assert len(_answers(wire, INTERIM)) == 1
    assert [m for m, _p in wire.carrying(INTERIM)] == ["sendRichMessage"]


def test_an_unscoped_session_goes_straight_to_plain_text(
    mk_bot, run_async, wire, monkeypatch,
):
    """No numeric chat id: no rich request is made with a null chat."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)
    monkeypatch.setattr(notify_mod, "resolve_chat_id_int", lambda _s: None)
    _idle(run_async, bot, sess, INTERIM)
    assert sess.job_background_open() is True
    assert wire.of("sendRichMessage") == []
    (chunk,) = [p for p in wire.of("send_message")
                if INTERIM in p["_args"][1]]
    assert chunk["reply_to_message_id"] == TRIGGER


def test_bookkeeping_that_fails_after_the_send_never_resends(
    mk_bot, run_async, wire, monkeypatch,
):
    """The rich send landed; a failure in what follows must not fall into
    the plain-text fallback and post the same answer again."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    _agent_starts(sess)

    def _boom(*_a, **_kw):
        raise RuntimeError("bookkeeping")

    monkeypatch.setattr(bot.registry, "track_message", _boom)
    with pytest.raises(RuntimeError):
        run_async(bot._deliver_job_interim(sess, INTERIM, time.time()))
    assert len(_answers(wire, INTERIM)) == 1


def test_the_fallback_does_not_read_the_interim_back_onto_the_card(
    mk_bot, run_async, wire, tmp_path,
):
    """Without the MessageDisplay hook the card reads prose from the
    transcript on its ticks. The interim answer's text block is already
    flushed when its Stop lands; it must be consumed and trimmed then, not
    read in by the continuation turn's first tick."""
    from aipager.bot.animation import _read_stream_text

    tp = tmp_path / "t.jsonl"
    tp.write_text("\n".join(json.dumps(x) for x in (
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": INTERIM}]}},
    )) + "\n")
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(bot)
    sess.stream_hook_live = False
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    _agent_starts(sess)
    _idle(run_async, bot, sess, INTERIM)

    _read_stream_text(sess)      # the continuation turn's first tick
    assert not [b for _a, b in sess.stream_commentary if INTERIM in b]
    assert [m for m, _p in wire.carrying(INTERIM)] == ["sendRichMessage"]
