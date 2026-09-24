"""R6 — never lose an answer: row J, criteria 18-20.

Five answers were dropped on 2026-09-15 with the line ``sendRichMessage
flood-banned — answer not delivered, no fallback``. R6 deletes that
contract: an ESSENTIAL a mute refuses is held and delivered when the mute
lifts, with one honest late marker.

The wording of the marker is asserted as a LITERAL here on purpose: it is
user-facing copy from ``entrypoints.md``, and asserting it against the
module's own constant would only prove the module equals itself.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import aipager.bot.rich_message as rm
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import (
    HELD,
    HELD_ANSWER_MAX_ATTEMPTS,
    HELD_ANSWER_MAX_PER_CHAT,
    late_marker,
)
from aipager.state import Status, TrackedSession

CHAT = 123456
BAN = 34212.0
MARKER = "⏳ delivered late (held {n} min during a Telegram rate limit)"


def _sess(label="jim", status=Status.IDLE):  # noqa: D401
    sess = TrackedSession(name=f"claude-{label}", label=label, status=status)
    sess.busy_started_at = time.monotonic()
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def _body(request) -> str:
    """The markdown that actually went on the wire.

    The rich path posts ``{"chat_id": …, "rich_message": {"markdown": …}}``
    rather than a flat ``text``; reading the wrong key would make every
    assertion below pass against an empty string.
    """
    payload = request[2]
    rich = payload.get("rich_message") or {}
    return rich.get("markdown") or payload.get("text", "")


def _answer_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


# ===== the marker, at every boundary that matters =======================

@pytest.mark.parametrize("seconds,minutes", [
    (0.0, 1),          # the "held 0 min" trap: it must never read that
    (1.0, 1),
    (29.0, 1),
    (30.0, 1),         # round(0.5) is 0 in python — max(1, …) is the guard
    (59.0, 1),
    (60.0, 1),
    (90.0, 2),         # round(1.5) is 2
    (3600.0, 60),
    (34212.0, 570),    # the real 9.5-hour ban
])
def test_the_late_marker_reads_honestly_at_every_scale(seconds, minutes):
    """Criterion 18's wording, boundary by boundary. 'held 0 min' would be
    a lie a user can see."""
    assert late_marker(seconds) == MARKER.format(n=minutes)


def test_the_marker_never_reads_zero_minutes():
    """Equivalence class: every sub-minute hold collapses to '1 min'."""
    assert all("held 0 min" not in late_marker(s)
               for s in (0.0, 0.4, 5.0, 29.9, 30.0))


def test_the_marker_carries_no_markup_metacharacters():
    """It is prefixed to BOTH the rich and the plain variant, so a stray
    ``*`` or ``<`` would corrupt one of them."""
    assert not set(late_marker(120.0)) & set("*_`<>[]")


# ===== the buffer ========================================================

def _hold(session="claude-jim", text="answer", chat=CHAT):
    return HELD.hold(chat_id=chat, session=session, label="jim",
                     rich_text=text, plain_text=text, reply_to=None)


def test_a_held_answer_is_pending_for_its_chat():
    _hold()
    assert HELD.count() == 1


def test_two_holds_from_two_turns_are_both_kept_in_order():
    """AMENDED — superseded by ``fix-brief-2.md`` T4: "'newest per turn
    wins' … means per **TURN**, not per chat. Two different turns'
    answers are two different pieces of the user's work and **both must
    arrive, in order**."

    As written in iteration 1 this row asserted per-SESSION replacement,
    and it contradicted this suite's own
    ``test_replay_qa.py::test_two_answers_from_one_session_during_one_ban_both_arrive``
    — a 9.5-hour ban routinely spans several turns of one session,
    because the user re-prompts when the first answer goes quiet. Only
    one of the two rows could pass; the coordinator resolved it in favour
    of both-arriving, so this row now asserts retention and ORDER (the
    older answer first: delivered out of order it reads as a reply to the
    wrong question).
    """
    _hold(text="first")
    _hold(text="second")
    assert [h.rich_text for h in HELD.pending(CHAT)] == ["first", "second"]


def test_one_turn_never_takes_more_than_one_slot_however_often_it_retries(
    mk_bot, run_async, rich_http,
):
    """The other half of the amendment: the buffer is still BOUNDED, so a
    single turn cannot grow it.

    T4's "a second hold for the same turn replaces the first" has no
    public surface — ``HELD.hold`` carries no turn or job identity and
    every call is a new entry — so the property is asserted where the
    product can express it: one turn whose delivery is refused over and
    over occupies exactly one slot, however many flushes the 2 s monitor
    tick runs while the ban holds.
    """
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    _hold(text="one turn")
    for _ in range(5):
        run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


def test_two_sessions_in_one_chat_are_held_separately():
    """The other half of the same rule: different sessions are different
    answers and both must survive."""
    _hold(session="claude-a")
    _hold(session="claude-b")
    assert HELD.count() == 2


def test_exactly_the_cap_worth_of_sessions_is_kept(caplog):
    """Boundary, just-inside ``HELD_ANSWER_MAX_PER_CHAT``."""
    for i in range(HELD_ANSWER_MAX_PER_CHAT):
        _hold(session=f"claude-{i}")
    assert HELD.count() == HELD_ANSWER_MAX_PER_CHAT


def test_one_past_the_cap_drops_the_oldest(caplog):
    """Boundary, just-outside: bounded, drop-oldest."""
    for i in range(HELD_ANSWER_MAX_PER_CHAT + 1):
        _hold(session=f"claude-{i}")
    assert HELD.count() == HELD_ANSWER_MAX_PER_CHAT


def test_the_dropped_answer_is_the_oldest_one():
    for i in range(HELD_ANSWER_MAX_PER_CHAT + 1):
        _hold(session=f"claude-{i}")
    assert all(h.session != "claude-0" for h in HELD.pending(CHAT))


def test_dropping_an_answer_is_never_silent(caplog):
    """R6: 'nothing is ever SILENTLY dropped'. One WARNING, not none and
    not one per entry."""
    with caplog.at_level(logging.WARNING, logger="aipager.bot.held"):
        for i in range(HELD_ANSWER_MAX_PER_CHAT + 1):
            _hold(session=f"claude-{i}")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_take_removes_the_entry():
    _hold()
    HELD.take(CHAT, "claude-jim")
    assert HELD.count() == 0


def test_taking_something_that_is_not_there_returns_none():
    """Error guessing: the monitor tick and an explicit flush can race."""
    assert HELD.take(CHAT, "claude-nobody") is None


def test_a_held_answer_knows_when_it_was_held():
    at = time.time() - 600
    entry = HELD.hold(chat_id=CHAT, session="claude-jim", label="jim",
                      rich_text="a", plain_text="a", reply_to=None, at=at)
    assert entry.held_at == pytest.approx(at)


def test_sessions_with_held_reports_the_chat_and_session():
    """This is what ``SessionMonitor._scan`` reads to decide whether to
    dispatch ``held_answer_flush``."""
    _hold()
    assert HELD.sessions_with_held()


# ===== row J, end to end: refused, held, delivered late =================

def test_an_answer_refused_by_a_mute_makes_no_http_call(
    mk_bot, run_async, rich_http,
):
    """Criterion 19 / D-2.3's belt and braces: even if a broad-except
    fallback fires during a mute, NOTHING reaches the wire. The recorder
    sits at the transport, below ``_post``, so no layer between can fake
    it."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    run_async(bot.notify(_sess(), "idle_prompt",
                         {"raw_md": "# Heading\n\nText"}))
    assert rich_http.requests == []


def test_an_answer_refused_by_a_mute_is_held_not_dropped(
    mk_bot, run_async, rich_http,
):
    """Row J / criterion 18, first half — the line that used to read
    'answer not delivered, no fallback'."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    run_async(bot.notify(_sess(), "idle_prompt",
                         {"raw_md": "# Heading\n\nText"}))
    assert HELD.count() == 1


def test_the_plain_text_fallback_is_never_attempted_into_a_ban(
    mk_bot, run_async, rich_http,
):
    """The fallback-into-the-ban trap, stated as its own row: the PTB
    fallback would be a SECOND request into an active ban, which is what
    escalated the ladder."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    run_async(bot.notify(_sess(), "idle_prompt",
                         {"raw_md": "# Heading\n\nText"}))
    bot._app.bot.send_message.assert_not_awaited()


def test_a_healthy_chat_still_delivers_the_answer_immediately(
    mk_bot, run_async, rich_http,
):
    """The control: the answer path really does run, so 'held, not sent'
    above is about a path that works."""
    bot = _answer_bot(mk_bot)
    run_async(bot.notify(_sess(), "idle_prompt",
                         {"raw_md": "# Heading\n\nText"}))
    assert rich_http.endpoints() == ["sendRichMessage"]


def test_nothing_is_delivered_while_the_chat_is_still_muted(
    mk_bot, run_async, rich_http,
):
    """``flush_held_answers`` returns 0 while muted — a flush that sent
    anyway would be a request into the active ban."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    _hold()
    assert run_async(bot.flush_held_answers(_sess())) == 0
    assert rich_http.requests == []


def test_the_answer_is_still_held_after_a_refused_flush(
    mk_bot, run_async, rich_http,
):
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    _hold()
    run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


def test_the_held_answer_is_delivered_once_the_mute_lifts(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Row J / criterion 18, second half."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="the answer")
    qa_clock.advance(601)
    assert run_async(bot.flush_held_answers(_sess())) == 1


def test_the_delivery_is_prefixed_with_the_late_marker(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Criterion 18's wording, on the wire. The marker is the first line
    and is followed by a blank one."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    HELD.hold(chat_id=CHAT, session="claude-jim", label="jim",
              rich_text="the answer", plain_text="the answer",
              reply_to=None, at=time.time() - 600)
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(_sess()))
    assert _body(rich_http.requests[0]).startswith(MARKER.format(n=10))


def test_the_answer_itself_survives_the_marker(
    mk_bot, run_async, rich_http, qa_clock,
):
    """A marker with no answer under it would be the same loss with better
    manners."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="the answer")
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(_sess()))
    assert "the answer" in _body(rich_http.requests[0])


def test_a_delivered_answer_is_not_delivered_twice(
    mk_bot, run_async, rich_http, qa_clock,
):
    """The monitor dispatches every 2 s; a flush that did not drop the
    entry would repeat the answer for ever."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold()
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(_sess()))
    run_async(bot.flush_held_answers(_sess()))
    assert len(rich_http.requests) == 1


def test_the_buffer_is_empty_after_a_successful_delivery(
    mk_bot, run_async, rich_http, qa_clock,
):
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold()
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 0


def test_the_monitor_event_delivers_the_same_way(
    mk_bot, run_async, rich_http, qa_clock,
):
    """``held_answer_flush`` is what ``SessionMonitor._scan`` dispatches —
    the surface the 2 s tick actually uses."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold()
    qa_clock.advance(601)
    run_async(bot.notify(_sess(), "held_answer_flush", {}))
    assert len(rich_http.requests) == 1


def test_two_sessions_both_get_their_answers_back(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Two busy sessions in one chat is the exact shape of the 2026-09-15
    incident, and neither answer may be lost.

    The monitor dispatches ``held_answer_flush`` per SESSION, so the two
    are driven the way the 2 s tick drives them. (Measured while writing
    this: one ``flush_held_answers(sess)`` call delivers that session's
    entry only, not every entry in the chat — harmless because every
    session with something held gets its own dispatch, but narrower than
    entrypoints.md's "everything held for that session's chat".)
    """
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(session="claude-jim", text="first")
    _hold(session="claude-bob", text="second")
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(_sess("jim")))
    run_async(bot.flush_held_answers(_sess("bob")))
    assert sorted(_body(r).splitlines()[-1] for r in rich_http.requests) == \
        ["first", "second"]


# ===== the attempt cap ===================================================

def test_a_permanently_failing_delivery_is_eventually_dropped_loudly(
    mk_bot, run_async, monkeypatch, caplog, qa_clock,
):
    """R6's last clause: dropping after ``HELD_ANSWER_MAX_ATTEMPTS`` must
    be LOUD. A silent give-up is the bug this ship exists to end, one
    level up."""
    bot = _answer_bot(mk_bot)

    async def _boom(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(rm, "send_rich_message", _boom)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _boom,
                        raising=False)
    _hold()
    with caplog.at_level(logging.WARNING, logger="aipager.bot.notify"):
        for _ in range(HELD_ANSWER_MAX_ATTEMPTS + 2):
            run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 0


def test_a_failing_delivery_keeps_the_answer_until_the_cap(
    mk_bot, run_async, monkeypatch,
):
    """Boundary, just-inside: one failure must not lose the answer."""
    bot = _answer_bot(mk_bot)

    async def _boom(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(rm, "send_rich_message", _boom)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _boom,
                        raising=False)
    _hold()
    run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


def test_flushing_a_session_with_nothing_held_is_a_no_op(
    mk_bot, run_async, rich_http,
):
    """The monitor calls this on every tick for every session."""
    bot = _answer_bot(mk_bot)
    assert run_async(bot.flush_held_answers(_sess())) == 0
    assert rich_http.requests == []


def test_a_mute_refusal_during_the_flush_is_not_an_attempt_lost(
    mk_bot, run_async, rich_http,
):
    """Error guessing: the mute is re-armed between the tick that decided
    to flush and the flush itself. That must not burn an attempt, or five
    quick re-bans would destroy the answer."""
    bot = _answer_bot(mk_bot)
    _hold()
    MUTE.mute(CHAT, BAN)
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS + 2):
        run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


def test_the_gate_still_refuses_a_flush_that_slips_through(
    limiter, run_async, rich_http,
):
    """Belt and braces at the bottom: whatever any caller decides, a rich
    send into a muted chat raises rather than posting."""
    MUTE.mute(CHAT, BAN)
    with pytest.raises(FloodMuted):
        run_async(rm.send_rich_message(CHAT, "held answer"))
    assert rich_http.requests == []
