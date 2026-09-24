"""An answer a mute refused is delivered late, not dropped (row J, R6; 8.29).

Five answers were lost on 2026-09-15, each leaving one INFO line —
``sendRichMessage flood-banned — answer not delivered, no fallback`` — and
nothing else. The text was a local variable of ``NotifyMixin.notify`` and
went out of scope with it; the work had been done and the tokens spent.

Declining the plain-text fallback was RIGHT: a second attempt into a ban
is a fresh violation that extends it. The mistake was reading "cannot send
now" as "cannot send". A ban lifts, and the answer is still worth reading.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest

from aipager import config
from aipager.bot.held import (
    HELD,
    HELD_ANSWER_MAX_AGE_SECONDS,
    HELD_ANSWER_MAX_ATTEMPTS,
    HELD_ANSWER_MAX_PER_CHAT,
    late_marker,
)
from aipager.state import Status

CHAT = 256113222
BAN = 34212.0


@pytest.fixture(autouse=True)
def _clean_buffer():
    HELD.clear()
    yield
    HELD.clear()


def _sess(bot, name="dev", chat=CHAT):
    sess = bot.registry.get_or_create(f"claude-{name}")
    sess.label = name
    sess.status = Status.BUSY
    sess.scope_chat_id = chat
    sess.trigger_msg_id = 77
    return sess


# ── the marker, as a pure function ───────────────────────────────────────────

def test_the_late_marker_never_claims_zero_minutes():
    """An answer held for forty seconds was still held, and "held 0 min"
    invites the reader to think the delay was theirs.

    Mutation: drop the `max(1, ...)` and a short ban produces a marker
    that contradicts itself.
    """
    assert late_marker(0) == \
        "⏳ delivered late (held 1 min during a Telegram rate limit)"
    assert late_marker(29) == \
        "⏳ delivered late (held 1 min during a Telegram rate limit)"
    assert "60 min" in late_marker(3600)
    assert "570 min" in late_marker(BAN)


def test_the_marker_carries_no_markup_metacharacters():
    """The same string is used for the rich and the plain variant. A
    marker that failed to parse would lose the very answer it exists to
    deliver."""
    marker = late_marker(120)
    for char in ("*", "_", "`", "[", "]", "<", ">", "&"):
        assert char not in marker, char


# ── the buffer, as data ──────────────────────────────────────────────────────

def _hold(session="claude-a", chat=CHAT, text="answer", at=None):
    return HELD.hold(chat_id=chat, session=session, label=session,
                     rich_text=text, plain_text=text, reply_to=1, at=at)


def test_two_answers_from_one_session_are_both_kept_in_order():
    """PER TURN, NOT PER SESSION (8.29 T4).

    This row is the inversion of the one it replaces. "Newest per turn
    wins" was read as "newest per session wins", so a second answer
    REPLACED the first at debug level — but a 9.5-hour ban routinely
    spans several turns of one session, because the user re-prompts when
    the first answer goes quiet, which is exactly what they did on
    2026-09-15. Two turns' answers are two pieces of the operator's work
    and both must arrive, oldest first. An answer silently replaced is the
    same failure as an answer silently dropped.

    Mutation: key the buffer by session again and the first answer of
    every multi-turn ban is lost without a line anyone reads.
    """
    _hold(text="first")
    _hold(text="second")
    assert HELD.count() == 2
    assert [e.rich_text for e in HELD.pending(CHAT)] == ["first", "second"]


def test_nothing_is_ever_dropped_below_warning_level(caplog):
    """R6 in one sentence: "nothing is ever SILENTLY dropped". The two
    caps are the only ways an answer leaves this buffer undelivered, and
    each names what was lost at WARNING."""
    caplog.set_level("DEBUG", logger="aipager.bot.held")
    for i in range(HELD_ANSWER_MAX_PER_CHAT + 1):
        _hold(session=f"claude-{i}", text=f"body {i}")   # one over the cap
    _hold(session="claude-old", text="ancient",          # one more over it,
          at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 60)
    HELD.expire()                                        # and one expiry

    losses = [r for r in caplog.records if "LOST" in r.getMessage()]
    assert len(losses) == 3, [r.getMessage() for r in losses]
    assert all(r.levelno >= logging.WARNING for r in losses)
    # "with what was lost" (T4), not just "something was lost".
    assert all("chars" in r.getMessage() for r in losses)


def test_an_answer_older_than_the_longest_possible_mute_is_expired(caplog):
    """The AGE half of "bounded by count AND age" (T4). Past
    `HELD_ANSWER_MAX_AGE_SECONDS` an answer is not waiting for a ban to
    lift — no mute can last that long — and delivering it later as news
    would be worse than saying it was lost.

    Mutation: drop `expire()` and an undeliverable answer sits in memory
    until the daemon restarts, arriving days late if it ever flushes.
    """
    caplog.set_level("WARNING", logger="aipager.bot.held")
    fresh = _hold(session="claude-new", text="recent")
    _hold(session="claude-old", text="ancient",
          at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 1.0)

    assert HELD.expire() == 1
    assert [e.key for e in HELD.pending(CHAT)] == [fresh.key]
    assert any("expired" in r.getMessage() for r in caplog.records)


def test_the_age_cap_outlasts_the_longest_possible_mute():
    """The two constants are in different modules on purpose (held.py has
    no aipager imports), so something has to pin them together: an age cap
    SHORTER than the longest mute would expire answers that are still
    waiting for the ban that refused them."""
    assert HELD_ANSWER_MAX_AGE_SECONDS >= config.FLOOD_MUTE_MAX_SECONDS


def test_the_buffer_is_bounded_per_chat_with_one_warning(caplog):
    """An unbounded buffer behind a 9.5-hour ban is a memory leak with
    extra steps. Drop-oldest, and exactly one WARNING naming the size —
    losing an answer must never be silent.

    Mutation: remove the cap and a ban plus a stray-idle storm grows this
    without limit.
    """
    caplog.set_level("WARNING", logger="aipager.bot.held")
    for i in range(HELD_ANSWER_MAX_PER_CHAT + 3):
        _hold(session=f"claude-{i}", text=f"answer {i}")

    assert HELD.count() == HELD_ANSWER_MAX_PER_CHAT
    assert len(caplog.records) == 3
    kept = {e.session for e in HELD.pending(CHAT)}
    assert "claude-0" not in kept, "the oldest was not the one dropped"
    assert f"claude-{HELD_ANSWER_MAX_PER_CHAT + 2}" in kept


def test_entries_come_back_oldest_first():
    """Delivery order: the answer that has waited longest goes first."""
    _hold(session="claude-a", at=100.0)
    _hold(session="claude-b", at=50.0)
    assert [e.session for e in HELD.pending()] == ["claude-b", "claude-a"]


def test_the_buffer_is_per_chat():
    """A ban is per chat; one chat's backlog must not block another's."""
    _hold(session="claude-a", chat=CHAT)
    _hold(session="claude-b", chat=-1009)
    assert HELD.count(CHAT) == 1
    assert HELD.count(-1009) == 1
    assert HELD.count() == 2


# ── row J: refused, held, delivered late ─────────────────────────────────────

def test_an_answer_refused_by_a_mute_is_held_and_then_delivered_once(
    mk_bot, limiter, flood_clock, run_async, rich_http, monkeypatch,
):
    """Row J, end to end, and the row that MUST fail on 0.7.12.

    The answer path runs into a muted chat, the answer is held with ZERO
    HTTP, the mute lifts, one flush delivers it exactly once with the late
    marker as its first line, and the buffer empties.

    Mutation: restore the `log.info(... "no fallback")` drop in
    `notify.py` and `HELD.count()` is 0 while muted — the answer is gone.
    """
    import aipager.bot.rich_message as rm
    monkeypatch.setattr(rm, "_rate_limiter", limiter)

    from aipager.bot.flood import MUTE
    bot = mk_bot()
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    bot._hold_answer(sess, "**the answer**", "the answer", 77)
    assert HELD.count(CHAT) == 1
    assert rich_http.requests == [], "the held answer still made a POST"

    # Still muted: the flush is a cheap no-op.
    assert run_async(bot.flush_held_answers(sess)) == 0
    assert HELD.count(CHAT) == 1

    flood_clock.advance(BAN + 1)
    assert not MUTE.is_muted(CHAT)

    assert run_async(bot.flush_held_answers(sess)) == 1
    assert HELD.count(CHAT) == 0
    assert len(rich_http.requests) == 1
    assert rich_http.requests[0][0] == "sendRichMessage"


def test_the_delivered_answer_opens_with_the_late_marker(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The marker is the honesty of the feature: a message arriving hours
    after the turn that produced it, with no explanation, reads as the
    daemon being broken.

    Mutation: drop the marker prefix and a late answer is
    indistinguishable from a confused one.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE

    sent: list[str] = []

    async def _send(chat_id, markdown, **kw):
        sent.append(markdown)
        return {"message_id": 5}

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)

    bot = mk_bot()
    sess = _sess(bot)
    MUTE.mute(CHAT, 600.0)
    bot._hold_answer(sess, "the body", "the body", 77)
    flood_clock.advance(660.0)

    assert run_async(bot.flush_held_answers(sess)) == 1
    first_line, blank, rest = sent[0].split("\n", 2)
    assert first_line.startswith("⏳ delivered late (held ")
    assert "during a Telegram rate limit)" in first_line
    assert blank == "", "the marker must be its own line"
    assert rest == "the body"


def test_nothing_is_delivered_while_the_chat_is_still_muted(
    mk_bot, limiter, flood_clock, run_async, rich_http, monkeypatch,
):
    """The flush runs on the monitor's 2 s tick, so it is called
    repeatedly DURING the ban. Delivering then would be the fresh
    violation the whole feature exists to avoid.

    Mutation: drop the `MUTE.is_muted` check at the top of
    `flush_held_answers` and this makes one request per tick into the ban
    — worse than the bug it replaces.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    bot = mk_bot()
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)
    bot._hold_answer(sess, "body", "body", 77)

    for _ in range(10):
        assert run_async(bot.flush_held_answers(sess)) == 0
    assert rich_http.requests == []
    assert HELD.count(CHAT) == 1


def test_a_delivery_that_fails_otherwise_is_retried_then_dropped_loudly(
    mk_bot, limiter, flood_clock, run_async, monkeypatch, caplog,
):
    """An answer whose reply target was deleted, or whose bot was blocked,
    cannot be retried for ever. It is dropped — but with one WARNING
    naming the session and the size, never silently, which is the whole
    point of R6.

    Mutation: drop the attempt counter and a permanently-failing entry is
    retried every 2 s for the life of the daemon.

    The last attempt now spends the stored PLAIN TEXT before giving up
    (ruling 7), so this row makes the plain path fail too — the answer is
    genuinely undeliverable, and that is the case the WARNING is for.
    `test_the_last_attempt_falls_back_to_the_stored_plain_text` owns the
    case where it lands.
    """
    import aipager.bot.rich_message as rm

    async def _fail(*a, **kw):
        raise RuntimeError("message to reply not found")

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _fail)
    caplog.set_level("WARNING", logger="aipager.bot.notify")

    bot = mk_bot()
    bot._app.bot.send_message = _fail
    sess = _sess(bot)
    bot._hold_answer(sess, "body", "body", 77)

    for _ in range(HELD_ANSWER_MAX_ATTEMPTS - 1):
        assert run_async(bot.flush_held_answers(sess)) == 0
        assert HELD.count(CHAT) == 1, "dropped too early"

    assert run_async(bot.flush_held_answers(sess)) == 0
    assert HELD.count(CHAT) == 0
    assert any("held answer dropped" in r.getMessage() for r in caplog.records)


def test_the_last_attempt_falls_back_to_the_stored_plain_text(
    mk_bot, limiter, flood_clock, run_async, monkeypatch, rich_http,
):
    """Ruling 7. `HeldAnswer.plain_text` was stored by every hold site and
    read by nothing, so an answer that survived a 9.5-hour ban could still
    be lost to a markdown parse error hours later — with a plain-text
    fallback available, safe (the chat is demonstrably not muted) and
    unused.

    Mutation: delete `_deliver_held_as_plain_text`'s call and the answer
    is dropped as "lost" with the text sitting in the entry.
    """
    import aipager.bot.rich_message as rm

    async def _fail(*a, **kw):
        raise RuntimeError("can't parse entities")

    sent: list[tuple] = []

    async def _plain(chat_id, text, **kw):
        sent.append((chat_id, text))
        return SimpleNamespace(message_id=9)

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _fail)
    bot = mk_bot()
    bot._app.bot.send_message = _plain
    sess = _sess(bot)
    bot._hold_answer(sess, "**rich**", "the plain one", 77)

    for _ in range(HELD_ANSWER_MAX_ATTEMPTS - 1):
        assert run_async(bot.flush_held_answers(sess)) == 0
    assert sent == [], "the plain text was spent before the rich attempts were"

    assert run_async(bot.flush_held_answers(sess)) == 1
    assert HELD.count(CHAT) == 0
    assert len(sent) == 1
    assert "the plain one" in sent[0][1]
    assert "delivered late" in sent[0][1], "the late marker goes out either way"


def test_a_successful_rich_flush_never_touches_the_plain_text(
    mk_bot, limiter, flood_clock, run_async, monkeypatch, rich_http,
):
    """The other half of ruling 7's test: the fallback is a LAST resort,
    not a second copy of every answer."""
    plain_sends: list = []
    bot = mk_bot()

    async def _plain(*a, **kw):                      # pragma: no cover
        plain_sends.append(a)
        return SimpleNamespace(message_id=9)

    bot._app.bot.send_message = _plain
    sess = _sess(bot)
    bot._hold_answer(sess, "**rich**", "the plain one", 77)

    assert run_async(bot.flush_held_answers(sess)) == 1
    assert plain_sends == []
    assert len(rich_http.requests) == 1


def test_a_re_mute_mid_flush_postpones_rather_than_spending_an_attempt(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The chat can be re-banned between the check and the send. That is a
    POSTPONED delivery, not a failed one, and must not count against the
    attempt budget — five such races would otherwise discard the answer.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.rich_message import RichMessageFloodBanned

    async def _banned(*a, **kw):
        raise RichMessageFloodBanned(BAN, CHAT)

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _banned)

    bot = mk_bot()
    sess = _sess(bot)
    bot._hold_answer(sess, "body", "body", 77)

    for _ in range(HELD_ANSWER_MAX_ATTEMPTS + 3):
        assert run_async(bot.flush_held_answers(sess)) == 0
    assert HELD.count(CHAT) == 1, "a re-mute consumed the attempt budget"
    assert HELD.pending(CHAT)[0].attempts == 0


# ── criterion 20: a banned job interim is held, not lost ─────────────────────
#
# These drove `_flush_job_buffer` until roadmap 8.42 (operator decision
# 2026-09-24) removed the interim buffer: an interim answer now goes out the
# moment its turn ends, through `_deliver_job_interim`, and the same
# criteria are asserted there.

def test_a_banned_job_interim_is_held_not_lost(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """Criterion 20: a ban at the moment a background job's interim answer
    goes out holds it, and its digest stays PENDING — so it is neither lost
    nor counted as delivered across a restart until it lands.

    Mutation: drop the hold and `HELD.count()` is 0.
    """
    import hashlib

    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE
    from aipager.bot.rich_message import RichMessageFloodBanned

    async def _banned(*a, **kw):
        raise RichMessageFloodBanned(BAN, CHAT)

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _banned)

    bot = mk_bot()
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    run_async(bot._deliver_job_interim(
        sess, "the background job's interim answer", 0.0))

    assert HELD.count(CHAT) == 1, "the interim answer was lost"
    held = HELD.pending(CHAT)[0]
    assert "the background job's interim answer" in held.rich_text
    digest = hashlib.md5(b"the background job's interim answer").hexdigest()
    assert digest not in sess.persisted_digests()


def test_a_deduped_job_interim_is_neither_sent_nor_held(
    mk_bot, limiter, run_async,
):
    """A stray re-run of an interim already delivered sends and holds
    nothing."""
    bot = mk_bot()
    sess = _sess(bot)
    import hashlib
    sess.remember_delivered(hashlib.md5(b"x").hexdigest())

    run_async(bot._deliver_job_interim(sess, "x", 0.0))
    assert HELD.count() == 0
    bot._app.bot.send_message.assert_not_called()


# ── criterion 19: belt and braces ────────────────────────────────────────────

def test_even_a_broad_except_fallback_makes_no_http_during_a_mute(
    mk_bot, limiter, flood_clock, run_async, rich_http, gated_bot, monkeypatch,
):
    """Criterion 19 / D-2.3, the belt to the translation's braces.

    Force the plain-text fallback to fire during a mute — the exact path
    the fix could have CREATED, by letting a bare `FloodMuted` fall into
    `notify.py`'s `except (RichMessageFallbackRequired, Exception)` arm.
    Even when it fires, the gate underneath refuses every chunk, so the
    loop makes zero HTTP calls. The difference between a wasted code path
    and a fresh violation.

    Mutation: remove the gate and this records one `sendMessage` per
    chunk, straight into the ban.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE
    from aipager.bot.rich_message import RichMessageFallbackRequired

    async def _needs_fallback(*a, **kw):
        raise RichMessageFallbackRequired("forced")

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _needs_fallback)

    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = _sess(bot)
    MUTE.mute(CHAT, BAN)

    run_async(bot._deliver_job_interim(sess, "output", 0.0))

    assert gated_bot.calls == [], "the plain-text fallback fired into the ban"
    assert rich_http.requests == []


# ── row J through the REAL answer path, not the helper ──────────────────────
#
# The tests above call `bot._hold_answer(...)` directly, which exercises the
# buffer but NOT the site that has to call it. The mutation protocol caught
# that: restoring the old `log.info(..., "no fallback")` drop in
# `notify.notify` left every one of them green. These two drive the answer
# path itself.

def test_the_real_answer_path_holds_instead_of_dropping(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """THE site. `NotifyMixin.notify`'s `except RichMessageFloodBanned`
    arm is the one the 2026-09-15 log named five times:

        sendRichMessage flood-banned — answer not delivered, no fallback

    Driven end to end through `notify(sess, "idle_prompt", ...)` so the
    answer really is a local of that method, exactly as it was when five
    of them were lost.

    Mutation: put that `log.info` back in place of `self._hold_answer(...)`
    and `HELD.count()` is 0 — the answer is gone and only a log line
    remains. (This is the mutation that passed while every other test in
    this file stayed green.)
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE
    from aipager.bot.rich_message import RichMessageFloodBanned

    async def _banned(*a, **kw):
        raise RichMessageFloodBanned(BAN, CHAT)

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _banned)

    bot = mk_bot()
    sess = _sess(bot)
    sess.status = Status.IDLE
    MUTE.mute(CHAT, BAN)

    run_async(bot.notify(sess, "idle_prompt",
                         {"summary": "the answer nobody saw"}))

    assert HELD.count(CHAT) == 1, "the answer was dropped, not held"
    held = HELD.pending(CHAT)[0]
    assert "the answer nobody saw" in held.rich_text
    assert held.session == sess.name


def test_the_monitor_event_delivers_a_held_answer_after_the_lift(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The trigger, as the session monitor dispatches it: one
    `notify(sess, "held_answer_flush", {})` on the 2 s tick it already
    runs — no busy-wait, no timer, no new task. A held answer therefore
    lands within one tick of the mute lifting.

    Mutation: remove the `held_answer_flush` branch from `notify` and the
    monitor's dispatch is a silent no-op, so the answer is held for ever.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot.flood import MUTE
    from aipager.session_monitor import _has_held_answer

    sent: list[str] = []

    async def _send(chat_id, markdown, **kw):
        sent.append(markdown)
        return {"message_id": 9}

    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)

    bot = mk_bot()
    sess = _sess(bot)
    MUTE.mute(CHAT, 600.0)
    bot._hold_answer(sess, "body", "body", 77)

    # What the monitor checks before dispatching.
    assert _has_held_answer(sess) is True

    # Still muted: dispatching is harmless.
    run_async(bot.notify(sess, "held_answer_flush", {}))
    assert sent == []

    flood_clock.advance(601.0)
    run_async(bot.notify(sess, "held_answer_flush", {}))

    assert len(sent) == 1
    assert sent[0].startswith("⏳ delivered late (held ")
    assert HELD.count(CHAT) == 0
    assert _has_held_answer(sess) is False


def test_the_monitor_predicate_is_false_when_nothing_is_held(mk_bot):
    """It runs for every session on every 2 s tick, so its false case is
    the one that matters for cost."""
    from aipager.session_monitor import _has_held_answer

    bot = mk_bot()
    sess = _sess(bot)
    assert _has_held_answer(sess) is False
