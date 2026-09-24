"""Quiet tool-less turns (roadmap 8.32, vm3 2026-09-24).

Two rules, each with its reproductions:

R1 — a `card`-layout turn whose finished card has NO timeline content
(``animation.final_card_has_timeline``: no tool row, no subagent row, no
prose the card would keep) is delivered as ONE message: the answer, whose
first line carries the stats. The bare "✅ label · Done · Ns" card does not
stay. It is a fresh send (an edit would be silent), and the card is deleted
after it — two calls, where the kept card cost three.

R2 — a turn Claude woke itself for (hook_receiver's fresh
``<task-notification>`` path, ``user_prompt_submit`` with
``self_woken``) defers its busy card until its first tool use or
``SELF_WOKEN_CARD_DELAY``, whichever comes first. A turn that ends before
either sends only its answer, in R1's shape.

Every outbound call is recorded in ONE list, in order — the rich transport
(``rich_message._post``) and the PTB bot's raw calls alike — so "how many
calls did this turn cost" is a length, not an inference. The deferred-card
timer's wait is ``animation._lazy_card_sleep``, replaced here by a gate the
test opens by hand; ``asyncio.sleep`` itself is never patched (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from aipager import config
from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.bot.animation import _RICH_LIMIT, final_card_has_timeline
from aipager.state import Status, TrackedSession

CHAT = 555
TRIGGER = 7
CARD = 42
ANSWER = "Nothing new from that last notice: it was an old status check finishing."


# ── harness ──────────────────────────────────────────────────────────────────

class _Wire:
    """Every outbound call, in order: ``(method, payload_or_kwargs)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 9000

    def methods(self) -> list[str]:
        """Every call but the "typing…" chat action, which is budget-exempt
        and asserted on its own where it matters."""
        return [m for m, _p in self.calls if m != "send_chat_action"]

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **_kw):
        w.calls.append((method, payload))
        w.next_id += 1
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _wire_bot(mk_bot, w: _Wire):
    bot = mk_bot()

    def _rec(name):
        async def _call(*args, **kwargs):
            # A real round trip suspends: a cancel or a concurrent path gets
            # its chance here, exactly as it would against Telegram.
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


def _sess(*, layout="card", busy_msg_id=CARD, tools=(), label="q"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.scope_chat_id = CHAT
    s.scope_kind = "dm"
    s.busy_msg_id = busy_msg_id
    s.busy_card_trigger = TRIGGER
    s.trigger_msg_id = TRIGGER
    s.busy_started_at = time.monotonic() - 5
    s.tool_history = list(tools)
    prefs.set_preference(CHAT, "layout", layout)
    return s


async def _drain_background():
    """Let the finish path's fire-and-forget housekeeping run."""
    for _ in range(5):
        pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set())
                   if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _bounded(coro):
    """Every scenario here is bounded: a regression that leaves something
    waiting must fail the test, not hang the suite."""
    return asyncio.wait_for(coro, timeout=5)


def _finish(run_async, bot, sess, **context):
    async def _go():
        await bot.notify(sess, "idle_prompt", context)
        await _drain_background()
    run_async(_bounded(_go()))


# ── R1: what counts as timeline content ──────────────────────────────────────

def test_a_card_with_no_rows_has_no_timeline():
    assert final_card_has_timeline(_sess()) is False


@pytest.mark.parametrize("row", [
    ("Read: /a.py", True),
    ("Bash: pytest", "failed"),
    ("Grep: x in y", False),
    ("\U0001f916 general-purpose · 3 tool calls · 20s", True),
])
def test_any_tool_or_subagent_row_is_timeline(row):
    assert final_card_has_timeline(_sess(tools=[row])) is True


def test_transcript_prose_the_card_would_keep_is_timeline():
    """On the transcript fallback (hook not live) a block that is NOT the
    answer stays on the card, so the card is kept."""
    sess = _sess()
    sess.stream_commentary = [(0, "Let me think about the plan first.")]
    assert final_card_has_timeline(sess) is True


def test_hook_prose_with_no_row_after_it_is_the_answer_not_timeline():
    """With the MessageDisplay hook live, a sentence with no tool row after
    its anchor is the answer and the card never shows it."""
    sess = _sess()
    sess.stream_hook_live = True
    sess.stream_commentary = [(0, "Some sentence.")]
    assert final_card_has_timeline(sess) is False


# ── R1 reproductions ─────────────────────────────────────────────────────────

def test_a_toolless_card_turn_is_one_message_with_the_stats(
    mk_bot, run_async, wire,
):
    """(a) The bigdog / catfish shape: a card-layout turn, zero tools.
    Exactly one visible message remains — the answer, stats in its first
    line, threaded to the prompt — and the card is deleted. Two calls,
    never the three (send, final edit, answer) it cost before.

    Guards: drop the R1 branch and a final editMessageText appears; the
    answer then opens with the short line.
    """
    bot = _wire_bot(mk_bot, wire)
    sess = _sess()
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)

    assert wire.methods() == ["sendRichMessage", "delete_message"]
    (answer,) = wire.of("sendRichMessage")
    first, _, body = answer["rich_message"]["markdown"].partition("\n\n")
    assert first.startswith("💬 **q** · Finished (")
    assert "s)" in first  # the elapsed time rides in the 💬 line
    assert body == ANSWER
    assert answer["reply_to_message_id"] == TRIGGER
    (delete,) = wire.of("delete_message")
    assert delete["message_id"] == CARD
    # Card housekeeping is an ORNAMENT: suspended in minimal mode, never
    # taking an answer's token.
    assert delete["rate_limit_args"] == {"class": "ornament"}
    assert sess.busy_msg_id is None


def test_the_answer_is_sent_before_the_card_is_deleted(mk_bot, run_async, wire):
    """The answer never waits behind card housekeeping, and the chat is
    never left with neither."""
    bot = _wire_bot(mk_bot, wire)
    _finish(run_async, bot, _sess(), summary=ANSWER)
    assert wire.methods().index("sendRichMessage") < \
        wire.methods().index("delete_message")


def test_a_card_whose_only_prose_repeats_the_answer_goes_too(
    mk_bot, run_async, wire,
):
    """Transcript fallback: the turn's one text block IS the answer, which
    `_drop_answer_tail` trims — nothing is left for the card to keep."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess()
    sess.stream_commentary = [(0, ANSWER)]
    _finish(run_async, bot, sess, summary=ANSWER, raw_md=ANSWER)
    assert wire.methods() == ["sendRichMessage", "delete_message"]


def test_a_turn_with_tools_keeps_its_card_and_answer(mk_bot, run_async, wire):
    """(b) Unchanged: the finished card is edited in place and kept, the
    answer follows with the SHORT result line, nothing is deleted."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(tools=[("Read: /a.py", True)])
    _finish(run_async, bot, sess, summary=ANSWER)

    assert wire.methods() == ["editMessageText", "sendRichMessage"]
    (card,) = wire.of("editMessageText")
    assert card["message_id"] == CARD
    assert card["rich_message"]["markdown"].rpartition("\n\n")[2] \
        .startswith("✅ **q** · Done")
    (answer,) = wire.of("sendRichMessage")
    assert answer["rich_message"]["markdown"] == f"💬 **q**\n\n{ANSWER}"


def test_a_toolless_turn_with_no_answer_keeps_its_card(mk_bot, run_async, wire):
    """Nothing to deliver: the card stays as the one record that the turn
    ended (one edit), rather than a delete plus a bare header (two)."""
    bot = _wire_bot(mk_bot, wire)
    _finish(run_async, bot, _sess(), summary="", no_response=True)
    assert wire.methods() == ["editMessageText"]


def test_a_toolless_card_is_not_re_anchored_the_answer_goes_to_the_target(
    mk_bot, run_async, wire,
):
    """A card anchored to a message the turn did not consume is not re-sent
    under the new target just to be deleted: the answer itself replies to
    the target."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess()
    sess.trigger_msg_id = 11  # consumption moved the target
    _finish(run_async, bot, sess, summary=ANSWER)
    assert wire.methods() == ["sendRichMessage", "delete_message"]
    assert wire.of("sendRichMessage")[0]["reply_to_message_id"] == 11
    assert wire.of("delete_message")[0]["message_id"] == CARD


def test_a_long_answer_on_a_toolless_turn_still_arrives_whole(
    mk_bot, run_async, wire,
):
    """(g) Over the rich ceiling: the stats header (with the attachment
    note), the truncated body and the full log — and still no card."""
    bot = _wire_bot(mk_bot, wire)
    long = ("paragraph of answer text. " * 40 + "\n\n") * 60
    assert len(long.encode()) > _RICH_LIMIT
    _finish(run_async, bot, _sess(), summary=long, raw_md=long)

    assert "editMessageText" not in wire.methods()
    (header,) = wire.of("send_message")
    assert header["_args"][1].startswith("💬 <b>q</b> · Finished (")
    assert "attached below" in header["_args"][1]
    assert len(wire.of("sendRichMessage")) == 1
    assert len(wire.of("send_document")) == 1
    assert [d["message_id"] for d in wire.of("delete_message")] == [CARD]


def test_a_plain_text_fallback_on_a_toolless_turn_opens_with_the_stats(
    mk_bot, run_async, wire, monkeypatch,
):
    """(g) Multi-part plain-text delivery (rich refused): the first chunk
    carries the stats line, and the card still goes."""
    bot = _wire_bot(mk_bot, wire)

    async def _post(method, payload, **_kw):
        wire.calls.append((method, payload))
        if method == "sendRichMessage":
            return {"ok": False, "error_code": 400, "description": "nope"}
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    body = "\n\n".join(f"part {i} " + "x" * 1500 for i in range(6))
    _finish(run_async, bot, _sess(), summary=body)

    chunks = [c["_args"][1] for c in wire.of("send_message")]
    assert len(chunks) > 1
    assert chunks[0].startswith("💬 q · Finished (")
    assert "editMessageText" not in wire.methods()
    assert [d["message_id"] for d in wire.of("delete_message")] == [CARD]


def test_a_muted_chat_holds_the_answer_and_delivers_it_once_with_no_card(
    mk_bot, run_async, wire,
):
    """(h) Flood-muted: nothing goes on the wire — not the answer, not the
    delete — and the answer is HELD. Once the mute lifts it is delivered
    exactly once, with its stats, and no card call follows it."""
    from aipager.bot.flood import MUTE
    from aipager.bot.held import HELD

    bot = _wire_bot(mk_bot, wire)
    sess = _sess()
    MUTE.mute(CHAT, 600)
    _finish(run_async, bot, sess, summary=ANSWER)
    assert wire.calls == []
    (held,) = HELD.pending(CHAT)
    assert held.rich_text.startswith("💬 **q** · Finished (")
    assert held.rich_text.endswith(ANSWER)

    MUTE.clear()
    assert run_async(_bounded(bot.flush_held_answers(sess))) == 1
    assert wire.methods() == ["sendRichMessage"]
    assert ANSWER in wire.of("sendRichMessage")[0]["rich_message"]["markdown"]
    assert run_async(_bounded(bot.flush_held_answers(sess))) == 0  # once, not twice


@pytest.mark.parametrize("layout", ["merged", "replace"])
def test_other_layouts_are_unchanged_for_a_toolless_turn(
    mk_bot, run_async, wire, layout,
):
    """(j) `merged` still edits the card into the answer; `replace` still
    deletes the card FIRST, as an essential call, then sends."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess(layout=layout)
    _finish(run_async, bot, sess, summary=ANSWER)
    if layout == "merged":
        assert wire.methods() == ["editMessageText"]
        md = wire.of("editMessageText")[0]["rich_message"]["markdown"]
        assert md.endswith(f"💬 **q**\n\n{ANSWER}")
    else:
        assert wire.methods() == ["delete_message", "sendRichMessage"]
        assert "rate_limit_args" not in wire.of("delete_message")[0]


def test_an_in_flight_re_anchor_is_waited_for_and_its_new_card_deleted(
    mk_bot, run_async, wire,
):
    """Race: a live tick's re-anchor holds ``animate_lock`` and swaps the
    card to a new message as the turn finishes. The finish path must take
    the card the re-anchor leaves behind; reading the old id first deletes
    a card the re-anchor deletes anyway and strands the new one on
    "Working" for good."""
    bot = _wire_bot(mk_bot, wire)
    sess = _sess()

    async def _scenario():
        started = asyncio.Event()

        async def _reanchor():
            async with sess.animate_lock:
                started.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                sess.busy_msg_id = 77  # the re-anchored card

        task = asyncio.create_task(_reanchor())
        await started.wait()
        await bot.notify(sess, "idle_prompt", {"summary": ANSWER})
        await task
        await _drain_background()

    run_async(_bounded(_scenario()))
    assert [d["message_id"] for d in wire.of("delete_message")] == [77]
    assert sess.busy_msg_id is None


# ── R2: self-woken turns get a lazy card ─────────────────────────────────────

class _Gate:
    """Stands in for the deferred card's wait: records the delay asked
    for and returns only when the test opens it."""

    def __init__(self) -> None:
        self.asked: list[float] = []
        self.event: asyncio.Event | None = None

    async def sleep(self, seconds):
        self.asked.append(seconds)
        self.event = asyncio.Event()
        await self.event.wait()


@pytest.fixture
def gate(monkeypatch):
    g = _Gate()
    monkeypatch.setattr("aipager.bot.animation._lazy_card_sleep", g.sleep)
    return g


def _busy_sess(bot):
    sess = bot.registry.get_or_create("claude-cat")
    sess.label = "cat"
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    prefs.set_preference(CHAT, "layout", "card")
    bot.registry.transition(sess.name, Status.BUSY)
    return sess


async def _yield(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


def _cards(w: _Wire) -> list[dict]:
    return [c for c in w.of("send_message") if "Thinking" in str(c)]


def test_a_self_woken_turn_ending_quickly_sends_only_its_answer(
    mk_bot, run_async, wire, gate,
):
    """(c) The catfish turn: woken by a <task-notification>, 3 s, no tool.
    No busy card at the prompt, none at the end — one answer message
    carrying the stats."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        assert _cards(wire) == []
        assert sess.lazy_card_at > 0
        timer = sess.lazy_card_task
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": ANSWER})
        await _drain_background()
        await _yield()
        return timer

    timer = run_async(_bounded(_scenario()))
    assert gate.asked == [config.SELF_WOKEN_CARD_DELAY]
    assert wire.methods() == ["sendRichMessage"]
    md = wire.of("sendRichMessage")[0]["rich_message"]["markdown"]
    assert md.startswith("💬 **cat** · Finished")
    assert md.endswith(ANSWER)
    assert timer.cancelled() or timer.done()
    assert sess.lazy_card_at == 0.0


def test_a_self_woken_turn_gets_its_card_at_the_first_tool_use(
    mk_bot, run_async, wire, gate,
):
    """(d) The card appears when the turn first does work, threaded like an
    immediate card, and the timer is stood down."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        assert _cards(wire) == []
        await bot.notify(sess, "tool_use", {
            "tool_name": "Bash", "tool_summary": "Bash: ls",
        })
        await _yield()

    run_async(_bounded(_scenario()))
    (card,) = _cards(wire)
    assert sess.busy_msg_id and sess.busy_msg_id > 0
    assert sess.lazy_card_at == 0.0
    assert sess.lazy_card_task is None
    # No second call for the row straight after the send: the card's own
    # first tick renders it.
    assert "editMessageText" not in wire.methods()
    assert sess.tool_history == [("Bash: ls", False)]
    bot._stop_animation(sess)


def test_a_self_woken_turn_with_no_tools_gets_its_card_after_the_delay(
    mk_bot, run_async, wire, gate,
):
    """(e) 20 s without a tool: the card goes up when the delay runs out."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        assert _cards(wire) == []
        gate.event.set()  # SELF_WOKEN_CARD_DELAY has passed
        await _yield(10)

    run_async(_bounded(_scenario()))
    assert gate.asked == [config.SELF_WOKEN_CARD_DELAY]
    assert len(_cards(wire)) == 1
    assert sess.busy_msg_id and sess.busy_msg_id > 0
    assert sess.lazy_card_at == 0.0
    bot._stop_animation(sess)


def test_the_self_woken_card_delay_is_fifteen_seconds():
    assert config.SELF_WOKEN_CARD_DELAY == 15.0


def test_a_human_prompt_still_gets_its_card_at_once(
    mk_bot, run_async, wire, gate,
):
    """(f) A terminal-typed prompt (the bigdog turns): the card goes out at
    the prompt, as before, and no deferral is armed."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)
    run_async(_bounded(bot.notify(sess, "user_prompt_submit", {})))
    assert len(_cards(wire)) == 1
    assert sess.lazy_card_at == 0.0
    assert gate.asked == []
    bot._stop_animation(sess)


def test_a_lazy_card_replies_to_the_target_an_immediate_one_would(
    mk_bot, run_async, wire, gate,
):
    """R3: a message the turn consumed while it had no card moved the
    target; the late card goes straight there and records it, so the
    re-anchor check finds nothing to fix."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        sess.trigger_msg_id = 31  # consumption while card-less
        await bot.notify(sess, "tool_use", {
            "tool_name": "Read", "tool_summary": "Read: /x",
        })
        await _yield()

    run_async(_bounded(_scenario()))
    (card,) = _cards(wire)
    assert card["reply_to_message_id"] == 31
    assert sess.busy_card_trigger == 31
    bot._stop_animation(sess)


def test_a_permission_wait_keeps_the_deferral_for_the_next_tool(
    mk_bot, run_async, wire, gate,
):
    """The delay running out while Claude waits on a question sends no
    card over it; the tool call after the answer still earns one."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        sess.status = Status.INTERACTIVE
        gate.event.set()
        await _yield(10)
        assert _cards(wire) == []
        assert sess.lazy_card_at > 0
        sess.status = Status.BUSY
        await bot.notify(sess, "tool_use", {
            "tool_name": "Bash", "tool_summary": "Bash: ls",
        })
        await _yield()

    run_async(_bounded(_scenario()))
    assert len(_cards(wire)) == 1
    bot._stop_animation(sess)


def test_a_card_whose_send_is_in_flight_at_the_finish_is_not_stranded(
    mk_bot, run_async, wire, gate,
):
    """The delay runs out as the turn ends: the finish waits for the
    card's send, then settles it like any card (tool-less: deleted after
    the answer), instead of the card landing afterwards, animating a turn
    that is already over."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)
    release = {}

    async def _slow_send_busy(s, **_kw):
        release["event"] = asyncio.Event()
        await release["event"].wait()
        s.busy_card_trigger = s.trigger_msg_id
        return 88

    bot.send_busy = _slow_send_busy

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        gate.event.set()
        await _yield()
        assert "event" in release  # the late send is in flight
        bot.registry.transition(sess.name, Status.IDLE)
        finish = asyncio.create_task(
            bot.notify(sess, "idle_prompt", {"summary": ANSWER}))
        await _yield()
        release["event"].set()
        await finish
        await _drain_background()
        await _yield()

    run_async(_bounded(_scenario()))
    assert [d["message_id"] for d in wire.of("delete_message")] == [88]
    assert sess.busy_msg_id is None
    assert not sess.animation_running()


def test_the_hook_receiver_marks_a_fresh_task_notification_turn_self_woken(
    mk_bot, run_async,
):
    """The wiring: hook_receiver's "no open job" branch is what says the
    turn is self-woken. A human prompt's submit carries no such mark."""
    import json

    from aipager.dtach import hook_receiver as hr

    bot = mk_bot()
    seen: list[tuple[str, dict]] = []

    async def _notify(sess, event, context):
        seen.append((event, context))

    recv = hr.HookReceiver(bot.registry, _notify)
    sess = bot.registry.get_or_create("claude-cat")
    sess.status = Status.IDLE

    def _send(prompt):
        recv._recent_fingerprints.clear()
        run_async(recv._on_datagram(json.dumps({
            "session": "claude-cat", "hook_event_name": "UserPromptSubmit",
            "prompt": prompt, "transcript_path": "",
        }).encode()))

    _send("<task-notification>\n<task-id>a1</task-id>\nDone.")
    assert ("user_prompt_submit", {"self_woken": True}) in seen

    seen.clear()
    bot.registry.transition("claude-cat", Status.IDLE)
    sess.last_idle_at = 0.0
    _send("say hi")
    assert seen == [("user_prompt_submit", {})]


# ── the remaining guards, one test each ──────────────────────────────────────

def test_an_api_error_on_a_toolless_turn_still_removes_the_card(
    mk_bot, run_async, wire,
):
    """The API-error notice returns early, before the answer path; the
    tool-less card it would otherwise strand on its last frame is deleted
    there too."""
    bot = _wire_bot(mk_bot, wire)
    _finish(run_async, bot, _sess(),
            summary="API Error: 500 internal server error")
    assert [d["message_id"] for d in wire.of("delete_message")] == [CARD]
    assert "editMessageText" not in wire.methods()


def test_a_subagent_start_earns_the_deferred_card_too(
    mk_bot, run_async, wire, gate,
):
    """An agent is work: if its row arrives before any tool_use event, the
    card goes up with it."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await bot.notify(sess, "subagent_start", {
            "agent_type": "general-purpose", "agent_id": "a1",
        })
        await _yield()

    run_async(_bounded(_scenario()))
    assert len(_cards(wire)) == 1
    bot._stop_animation(sess)


def test_a_card_already_up_is_the_turns_card(mk_bot, run_async, wire, gate):
    """A compaction put its own card up during the card-less window: the
    first tool use adopts it rather than sending a second one."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        sess.busy_msg_id = 500  # the compaction card
        await bot.notify(sess, "tool_use", {
            "tool_name": "Bash", "tool_summary": "Bash: ls",
        })
        await _yield()

    run_async(_bounded(_scenario()))
    assert _cards(wire) == []
    assert sess.busy_msg_id == 500
    assert sess.lazy_card_at == 0.0
    # An adopted card is not a card just sent: the row's edit goes out as
    # it would on any live card.
    assert [p["message_id"] for p in wire.of("editMessageText")] == [500]


def test_a_message_mid_turn_earns_the_card_without_restarting_the_turn(
    mk_bot, run_async, wire, gate,
):
    """A Telegram message sent into a card-less self-woken turn reaches
    `_send_busy_and_animate` like any other send. It earns the card — and
    must not run the turn-start reset again, which would restart the
    turn's clock and drop the prose it has already produced."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        started = sess.busy_started_at
        sess.stream_commentary = [(0, "Checking the notice.")]
        sess.cost_baseline = 1.25
        old_timer = sess.lazy_card_task
        await bot._send_busy_and_animate(sess)  # the handler's call
        await _yield()
        return started, old_timer

    started, old_timer = run_async(_bounded(_scenario()))
    assert len(_cards(wire)) == 1
    assert sess.busy_started_at == started
    assert sess.stream_commentary == [(0, "Checking the notice.")]
    assert sess.cost_baseline == 1.25
    assert sess.lazy_card_at == 0.0
    assert old_timer.cancelled() or old_timer.done()
    bot._stop_animation(sess)


def test_re_arming_a_deferral_stands_the_old_timer_down(
    mk_bot, run_async, wire, gate,
):
    """Only one deferral timer per session: a second lazy start cancels
    the first, so two timers can never each send a card."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot._send_busy_and_animate(sess, lazy=True)
        await _yield()
        first = sess.lazy_card_task
        await bot._send_busy_and_animate(sess, lazy=True)
        await _yield()
        second = sess.lazy_card_task
        bot._cancel_lazy_card(sess)
        bot._stop_animation(sess)
        return first, second

    first, second = run_async(_bounded(_scenario()))
    assert first.cancelled() or first.done()
    assert second is not first
    assert _cards(wire) == []


def test_a_card_less_self_woken_turn_still_shows_typing(
    mk_bot, run_async, wire, gate,
):
    """R3: the typing bubble is not deferred with the card — it is
    budget-exempt and the one sign in the chat list that the session is
    working — and it stops with the turn."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        typing = sess.typing_task
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": ANSWER})
        await _drain_background()
        await _yield()
        return typing

    typing = run_async(_bounded(_scenario()))
    assert _cards(wire) == []
    assert len(wire.of("send_chat_action")) >= 1
    assert typing is not None and (typing.cancelled() or typing.done())


@pytest.mark.parametrize("recovered", [False, True])
def test_an_already_delivered_answer_leaves_the_toolless_card_in_place(
    mk_bot, run_async, wire, recovered,
):
    """A turn with no text of its own reads an EARLIER turn's answer from
    the transcript; the finish path suppresses it as already delivered.
    With nothing to deliver, R1 does not apply: the card stays as the one
    record the turn ended — not traded for a bare notifying header, nor,
    on the monitor's recovered path, for nothing at all."""
    import hashlib

    bot = _wire_bot(mk_bot, wire)
    sess = _sess()
    sess.remember_delivered(hashlib.md5(ANSWER.encode("utf-8")).hexdigest())
    ctx = {"summary": ANSWER}
    if recovered:
        ctx["recovered"] = True
    _finish(run_async, bot, sess, **ctx)
    assert wire.methods() == ["editMessageText"]
    assert wire.of("editMessageText")[0]["message_id"] == CARD


def test_a_session_ending_mid_send_does_not_strand_the_late_card(
    mk_bot, run_async, wire, gate,
):
    """The session exits while the deferred card's send is in flight: the
    exit waits for it and deletes it, rather than the card landing after
    the exit and staying up for good."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)
    release = {}

    async def _slow_send_busy(s, **_kw):
        release["event"] = asyncio.Event()
        await release["event"].wait()
        return 89

    bot.send_busy = _slow_send_busy

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        gate.event.set()
        await _yield()
        assert "event" in release
        ending = asyncio.create_task(
            bot.notify(sess, "session_end", {"source": "other"}))
        await _yield()
        release["event"].set()
        await ending
        await _yield()

    run_async(_bounded(_scenario()))
    assert 89 in [d.get("message_id") for d in wire.of("delete_message")]
    assert sess.busy_msg_id is None


# ── a stopped card-less turn does not leak its deferral (rev-iter2-001) ─────

@pytest.fixture
def no_terminal(monkeypatch):
    """The stop/halt paths press Escape in the terminal; nothing real."""
    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr("aipager.dtach.inject.send_keys", _noop)
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _noop)


def _stop(bot, sess, how):
    if how == "stop":
        return bot._stop_session_core(sess)
    return bot._halt_for_safety(sess, "blocked for the test")


@pytest.mark.parametrize("how", ["stop", "halt"])
@pytest.mark.parametrize("waiting", [False, True])
def test_stopping_a_card_less_turn_clears_its_deferral(
    mk_bot, run_async, wire, gate, no_terminal, how, waiting,
):
    """`/stop` and the safety halt end a turn WITHOUT the finish path. A
    card-less self-woken turn's deferral must end with it — including
    one stopped while waiting on a question, whose mark is otherwise kept
    indefinitely for the tool call that would follow the answer."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        timer = sess.lazy_card_task
        if waiting:
            sess.status = Status.INTERACTIVE
            gate.event.set()  # the delay runs out during the wait
            await _yield(10)
            assert sess.lazy_card_at > 0  # kept through the wait
        await _stop(bot, sess, how)
        await _yield()
        return timer

    timer = run_async(_bounded(_scenario()))
    assert sess.lazy_card_at == 0.0
    assert sess.lazy_card_task is None
    assert timer.cancelled() or timer.done()
    assert _cards(wire) == []


@pytest.mark.parametrize("how", ["stop", "halt"])
def test_the_next_prompt_after_a_stop_is_a_fresh_turn(
    mk_bot, run_async, wire, gate, no_terminal, how,
):
    """End to end: stopped while waiting on a question, then a new human
    prompt. It gets a fresh card with a fresh clock and none of the
    stopped turn's prose."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        sess.stream_commentary = [(0, "old turn prose")]
        sess.busy_started_at -= 600
        stale_start = sess.busy_started_at
        sess.status = Status.INTERACTIVE
        gate.event.set()
        await _yield(10)
        await _stop(bot, sess, how)
        bot.registry.transition(sess.name, Status.BUSY)  # the new prompt
        await bot._send_busy_and_animate(sess)
        await _yield()
        bot._stop_animation(sess)
        return stale_start

    stale_start = run_async(_bounded(_scenario()))
    assert len(_cards(wire)) == 1
    assert sess.busy_started_at > stale_start
    assert sess.stream_commentary == []


def test_a_genuinely_new_turn_is_never_mistaken_for_a_mid_turn_message(
    mk_bot, run_async, wire, gate,
):
    """The shortcut that lets a mid-turn message earn a deferred card
    without a reset must not fire for a NEW turn, even if a stale mark
    survived somehow: `transition()` flags every genuinely new turn entry
    (`job_reclaim_pending`), and a message absorbed by a running turn
    never does."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        sess.stream_commentary = [(0, "old turn prose")]
        sess.busy_started_at -= 600
        stale_start = sess.busy_started_at
        # The turn ends by a path that leaves the mark behind.
        sess.status = Status.IDLE
        bot.registry.transition(sess.name, Status.BUSY)  # new human turn
        assert sess.job_reclaim_pending
        await bot._send_busy_and_animate(sess)
        await _yield()
        bot._stop_animation(sess)
        return stale_start

    stale_start = run_async(_bounded(_scenario()))
    assert len(_cards(wire)) == 1
    assert sess.busy_started_at > stale_start
    assert sess.stream_commentary == []
    assert sess.lazy_card_at == 0.0


@pytest.mark.parametrize("how", ["stop", "halt"])
def test_a_stop_during_the_late_cards_send_settles_that_card(
    mk_bot, run_async, wire, gate, no_terminal, how,
):
    """The stop lands while the deferred card's send is in flight: it waits
    for the card under the card lock and settles THAT card, instead of
    cancelling the send half-way and leaving a card nothing tracks."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)
    release = {}

    async def _slow_send_busy(s, **_kw):
        release["event"] = asyncio.Event()
        await release["event"].wait()
        return 88

    bot.send_busy = _slow_send_busy

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        gate.event.set()
        await _yield()
        assert "event" in release  # the late send is in flight
        stopping = asyncio.create_task(_stop(bot, sess, how))
        # Past the stop's own Escape pair (a real 0.15 s pause between the
        # two keys), so it is at the card, not still at the terminal.
        await asyncio.sleep(0.3)
        release["event"].set()
        await stopping
        await _yield()

    run_async(_bounded(_scenario()))
    settled = [c for c in wire.of("edit_message_text")
               if c.get("message_id") == 88]
    assert len(settled) == 1
    assert sess.busy_msg_id is None
    assert not sess.animation_running()


# ── paths that remove or relaunch a session (review-3 rev-iter3-001) ────────
#
# The finish path, session_end (the SessionEnd hook, and the monitor's GONE
# detection, which dispatches the same event), /stop and the safety halt
# stand a deferral down. Three more paths tear a session down WITHOUT
# reaching any of them when Claude's own SessionEnd is not processed first:
# /kill (which then drops the entry from the registry, out of reach of
# every later cleanup), /perms' kill-and-relaunch (the monitor skips a
# restarting session on purpose) and /new → Replace. Each now cancels the
# deferral itself; these rows open the timer's gate AFTER the teardown to
# prove no card can follow.

@pytest.fixture
def dead_terminal(monkeypatch):
    """No real dtach. ``hooks["launch"]`` runs inside the relaunch, while
    the session is still BUSY between its kill and its fresh start."""
    hooks: dict = {}

    async def _ok(*_a, **_kw):
        return True

    async def _launched(*_a, **_kw):
        if "launch" in hooks:
            await hooks["launch"]()
        return True, ""

    monkeypatch.setattr("aipager.dtach.inject.kill_session", _ok)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _ok)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _ok)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _launched)
    # The relaunch polls for the old socket to go: it has.
    monkeypatch.setattr("aipager.bot.session_ops._PERMS_POLL_INTERVAL", 0)
    monkeypatch.setattr("aipager.bot.session_ops.Path", lambda *_a: MagicMock(
        is_socket=MagicMock(return_value=False)))
    return hooks


async def _teardown(bot, sess, how):
    if how == "kill":
        await bot._kill_session_core(sess.name, sess.label)
    elif how == "perms":
        await bot._kill_and_relaunch_core(
            sess, target_skip_perms=True, interrupt_first=False)
    else:  # /new → Replace
        from pathlib import Path

        bot._new_conflict_pending[sess.name] = {
            "prompt": "", "skip_perms": False, "user_id": 1, "msg_id": 5,
        }
        query = MagicMock()
        query.data = f"{sess.name}:new_replace"
        query.message = MagicMock(message_id=None, text="")
        query.from_user = MagicMock(id=12345)

        async def _answer(*_a, **_kw):
            return None

        query.answer = _answer
        query.edit_message_text = _answer
        update = MagicMock(callback_query=query, effective_user=query.from_user)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "is_socket", lambda self: False)
            await bot._handle_callback(update, MagicMock())


@pytest.mark.parametrize("how", ["kill", "perms", "new_replace"])
def test_tearing_a_session_down_stands_its_deferred_card_down(
    mk_bot, run_async, wire, gate, dead_terminal, how,
):
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        timer = sess.lazy_card_task
        event = gate.event

        async def _mid_relaunch():
            # The 15 s run out between the kill and the fresh start.
            event.set()
            await _yield(10)

        dead_terminal["launch"] = _mid_relaunch
        await _teardown(bot, sess, how)
        await _yield()
        event.set()  # ...or after the teardown
        await _yield(10)
        return timer

    timer = run_async(_bounded(_scenario()))
    assert timer.cancelled() or timer.done()
    assert sess.lazy_card_at == 0.0
    assert _cards(wire) == []
    assert not sess.animation_running()


@pytest.mark.parametrize("how", ["kill", "perms", "new_replace"])
def test_a_teardown_during_the_late_cards_send_waits_for_it(
    mk_bot, run_async, wire, gate, dead_terminal, how,
):
    """The teardown lands while the deferred card's send is in flight. The
    card that went out ends up as the session's KNOWN card — never a
    half-cancelled send with the -1 claim left behind — and /kill, which
    stops the animation, waits for it under the card lock first."""
    bot = _wire_bot(mk_bot, wire)
    sess = _busy_sess(bot)
    release = {}

    async def _slow_send_busy(s, **_kw):
        release["event"] = asyncio.Event()
        await release["event"].wait()
        return 88

    bot.send_busy = _slow_send_busy

    async def _scenario():
        await bot.notify(sess, "user_prompt_submit", {"self_woken": True})
        await _yield()
        gate.event.set()
        await _yield()
        assert "event" in release  # the late send is in flight
        tearing = asyncio.create_task(_teardown(bot, sess, how))
        await _yield()
        release["event"].set()
        await tearing
        await _yield()
        running = sess.animation_running()
        bot._stop_animation(sess)
        return running

    animating_after = run_async(_bounded(_scenario()))
    assert sess.busy_msg_id == 88
    assert sess.lazy_card_at == 0.0
    if how == "kill":
        # /kill stops the animation AFTER waiting for the card, so the late
        # card cannot start animating a dead session behind its back.
        assert not animating_after
