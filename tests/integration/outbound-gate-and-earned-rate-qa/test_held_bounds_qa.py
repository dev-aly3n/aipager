"""T4 + ruling 7 + criterion 20 — every way an answer can leave the buffer.

T4: "'newest per turn wins' … means per **TURN**, not per chat. Two
different turns' answers are two different pieces of the user's work and
**both must arrive, in order**" — bounded by **count AND age**, and "if
anything is ever genuinely dropped it must be at **WARNING with what was
lost** — never silently at debug".

Ruling 7: a held answer whose rich delivery fails for a non-mute reason
falls back to the stored plain text "rather than losing the answer a
second time".

Criterion 20: ``_flush_job_buffer`` holds the buffer's contents BEFORE it
clears them — a sanctioned seam since iteration 2 (tester-iter1-005),
because the close paths that drive it in production run on timers this
suite cannot run.
"""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.held import (
    HELD,
    HELD_ANSWER_MAX_AGE_SECONDS,
    HELD_ANSWER_MAX_ATTEMPTS,
    HELD_ANSWER_MAX_PER_CHAT,
)
from aipager.state import Status, TrackedSession

CHAT = 123456
BAN = 34212.0


def _sess(label="jim", status=Status.IDLE):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=status)
    sess.busy_started_at = time.monotonic()
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def _answer_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def _hold(text="answer", session="claude-jim", at=None, plain=None,
          label=None):
    return HELD.hold(chat_id=CHAT, session=session,
                     label=label or session.removeprefix("claude-"),
                     rich_text=text, plain_text=plain or text,
                     reply_to=None, at=at)


def _bodies(rich_http) -> list[str]:
    out = []
    for _endpoint, _chat, payload in rich_http.requests:
        rich = payload.get("rich_message") or {}
        out.append(rich.get("markdown") or payload.get("text", ""))
    return out


def _warnings(caplog) -> list[logging.LogRecord]:
    """WARNINGs from the BUFFER only.

    ``caplog`` collects every propagated record, and arming a mute logs
    its own WARNING from ``aipager.bot.flood`` — counting that one would
    make "the drop was reported" pass for a run in which the buffer said
    nothing at all.
    """
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and r.name == "aipager.bot.held"]


def _plain_sent(bot) -> list[str]:
    """Everything that went out as PLAIN text, i.e. through PTB's
    ``send_message`` rather than the rich ``_post``."""
    out = []
    for call in bot._app.bot.send_message.await_args_list:
        if isinstance(call.kwargs.get("text"), str):
            out.append(call.kwargs["text"])
        out.extend(a for a in call.args if isinstance(a, str))
    return out


# ===== T4 — two turns, both delivered, in order =========================

def test_both_turns_of_one_session_are_delivered_after_the_ban(
    mk_bot, run_async, rich_http, qa_clock,
):
    """The 2026-09-15 shape: the user re-prompts when the first answer
    goes quiet, so one 9.5-hour ban spans two turns of one session."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="first turn")
    _hold(text="second turn")
    qa_clock.advance(601.0)
    assert run_async(bot.flush_held_answers(_sess())) == 2


def test_the_two_turns_arrive_in_the_order_they_were_asked(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Order is not a detail: delivered backwards, the second answer reads
    as a reply to the first question."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="first turn")
    _hold(text="second turn")
    qa_clock.advance(601.0)
    run_async(bot.flush_held_answers(_sess()))
    bodies = _bodies(rich_http)
    assert bodies.index([b for b in bodies if "first turn" in b][0]) < \
        bodies.index([b for b in bodies if "second turn" in b][0])


def test_neither_turn_is_left_behind_in_the_buffer(
    mk_bot, run_async, rich_http, qa_clock,
):
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="first turn")
    _hold(text="second turn")
    qa_clock.advance(601.0)
    run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 0


def test_three_turns_all_arrive(mk_bot, run_async, rich_http, qa_clock):
    """Equivalence: "both" is not a special case for two — a long ban is
    several turns."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    for i in range(3):
        _hold(text=f"turn {i}")
    qa_clock.advance(601.0)
    assert run_async(bot.flush_held_answers(_sess())) == 3


# ===== bounded by COUNT and by AGE, and never silently ==================

def test_the_count_cap_drop_names_what_was_lost(caplog):
    """"a WARNING with what was lost". A WARNING that says only "dropped
    an answer" cannot be acted on; the session is what the operator
    needs."""
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.held"):
        for i in range(HELD_ANSWER_MAX_PER_CHAT + 1):
            _hold(session=f"claude-session-{i}", text=f"answer {i}")
    assert any("session-0" in record.getMessage()
               for record in _warnings(caplog))


def test_an_answer_past_the_age_cap_is_dropped(qa_clock):
    """The age half of the bound: an answer from a ban that lifted a week
    ago must not be delivered as if it were news."""
    _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 1)
    HELD.expire()
    assert HELD.count() == 0


def test_an_answer_just_inside_the_age_cap_is_kept(qa_clock):
    """Just-inside boundary."""
    _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS + 60)
    HELD.expire()
    assert HELD.count() == 1


def test_the_age_cap_drop_is_a_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.held"):
        _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 1)
        HELD.expire()
    assert _warnings(caplog)


def test_the_age_cap_outlasts_the_longest_possible_mute():
    """The bound that makes the age cap safe: a mute can be
    ``FLOOD_MUTE_MAX_SECONDS`` long, so an age cap below that would expire
    answers the daemon is still waiting to deliver — losing exactly the
    work this buffer exists to protect."""
    assert HELD_ANSWER_MAX_AGE_SECONDS >= config.FLOOD_MUTE_MAX_SECONDS


def test_a_delivered_answer_is_not_reported_as_lost(
    mk_bot, run_async, rich_http, qa_clock, caplog,
):
    """The control for every WARNING row above: a successful delivery is
    not a loss, and crying wolf on it would bury the real line."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold()
    qa_clock.advance(601.0)
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.held"):
        run_async(bot.flush_held_answers(_sess()))
    assert _warnings(caplog) == []


# ===== ruling 7 — the plain text earns its place ========================

def _break_the_rich_path(monkeypatch):
    """Make the RICH delivery fail for a non-mute reason, at the
    documented seam (``rich_message._post``) rather than anywhere inside
    the code under test."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("telegram said no")

    monkeypatch.setattr(rm, "_post", _boom)


def test_a_rich_delivery_that_works_never_spends_the_plain_copy(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Ruling 7's first half: the fallback is a fallback. A plain copy
    sent alongside the rich one would double every late answer."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="**rich**", plain="rich")
    qa_clock.advance(601.0)
    run_async(bot.flush_held_answers(_sess()))
    assert _plain_sent(bot) == []


def test_an_exhausted_rich_delivery_falls_back_to_the_plain_copy(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """Ruling 7's second half: "a held answer that dies on delivery is the
    exact failure this feature exists to prevent"."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="**the answer**", plain="the answer")
    qa_clock.advance(601.0)
    _break_the_rich_path(monkeypatch)
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS):
        run_async(bot.flush_held_answers(_sess()))
    assert any("the answer" in text for text in _plain_sent(bot))


def test_the_answer_is_not_still_held_after_the_plain_delivery(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """…and the buffer lets it go, or the next tick delivers it again."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="**the answer**", plain="the answer")
    qa_clock.advance(601.0)
    _break_the_rich_path(monkeypatch)
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS):
        run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 0


def test_one_transient_rich_failure_does_not_spend_the_plain_copy(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """The developer's declared DEVIATION from ruling 7, measured.

    The fallback fires on the LAST attempt rather than the first, so a
    transient failure still gets four more RICH tries. Ruling 7's purpose
    — "rather than losing the answer a second time" — is met either way,
    because the loss happens at ``HELD_ANSWER_MAX_ATTEMPTS`` and that is
    where the fallback is spent; and the answer is demonstrably still held
    here, not lost. The cost of the alternative is a plain-text answer
    sent for a blip, which is a permanent downgrade of the user's answer
    for a temporary fault.
    """
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="**the answer**", plain="the answer")
    qa_clock.advance(601.0)
    _break_the_rich_path(monkeypatch)
    run_async(bot.flush_held_answers(_sess()))
    assert _plain_sent(bot) == []


def test_one_transient_rich_failure_keeps_the_answer(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """The half of that deviation that actually matters: nothing is lost
    while the retries are being spent."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    _hold(text="**the answer**", plain="the answer")
    qa_clock.advance(601.0)
    _break_the_rich_path(monkeypatch)
    run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


def test_a_mute_is_never_treated_as_a_delivery_failure(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """The exclusion in the ruling's own words — "for any **non-mute**
    reason". A mute must never spend the fallback, because a plain send
    into a ban is one more request into an active ban."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    _hold(text="**the answer**", plain="the answer")
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS + 2):
        run_async(bot.flush_held_answers(_sess()))
    assert _plain_sent(bot) == []


def test_a_mute_never_uses_up_the_answers_attempts(
    mk_bot, run_async, rich_http, qa_clock, monkeypatch,
):
    """…and the answer survives all of it: a 9.5-hour ban is thousands of
    monitor ticks, so a mute that counted as an attempt would drop every
    held answer long before the ban lifted."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    _hold(text="**the answer**", plain="the answer")
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS + 2):
        run_async(bot.flush_held_answers(_sess()))
    assert HELD.count() == 1


# ===== criterion 20 — the job buffer is HELD before it is cleared =======

def _seeded(sess, *chunks):
    sess.job_interim_buffer = list(chunks)
    return sess


def test_a_muted_job_buffer_is_held(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Criterion 20, through the sanctioned seam. The buffer is the only
    full copy of a background job's answers on the close paths that never
    reach the Finished composition."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert HELD.pending(CHAT)


def test_what_was_held_is_what_the_buffer_contained(
    mk_bot, run_async, rich_http, qa_clock,
):
    """The ORDERING *is* the criterion: cleared first, the hold would
    capture an empty buffer and the answer would be gone with a held
    entry to prove it was handled."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert any("the interim answer" in entry.rich_text
               for entry in HELD.pending(CHAT))


def test_the_buffer_is_cleared_once_it_has_been_held(
    mk_bot, run_async, rich_http, qa_clock,
):
    """The other side of the same ordering: held AND cleared, so a second
    close path cannot hold it twice."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert sess.job_interim_buffer == []


def test_a_muted_job_buffer_puts_nothing_on_the_wire(
    mk_bot, run_async, rich_http, qa_clock,
):
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert rich_http.requests == []


def test_a_healthy_job_buffer_is_delivered_not_held(
    mk_bot, run_async, rich_http, qa_clock,
):
    """The control: with no mute the same call delivers and holds
    nothing, so the rows above are about the mute and not about a seam
    that never does anything."""
    bot = _answer_bot(mk_bot)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert HELD.count() == 0


def test_a_healthy_job_buffer_really_reaches_telegram(
    mk_bot, run_async, rich_http, qa_clock,
):
    """…and it goes out, so "nothing held" above is a delivery and not a
    silent drop."""
    bot = _answer_bot(mk_bot)
    sess = _seeded(_sess(), "the interim answer")
    run_async(bot._flush_job_buffer(sess))
    assert any("the interim answer" in json.dumps(payload)
               for _e, _c, payload in rich_http.requests)


def test_an_empty_job_buffer_holds_nothing(
    mk_bot, run_async, rich_http, qa_clock,
):
    """Error guessing: the close paths fire on every job, most of which
    have nothing buffered. A held empty answer would be delivered later
    as a blank message."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, BAN)
    run_async(bot._flush_job_buffer(_seeded(_sess())))
    assert HELD.count() == 0


def test_a_held_job_buffer_is_delivered_when_the_ban_lifts(
    mk_bot, run_async, rich_http, qa_clock,
):
    """End to end: holding it is only worth anything if it comes back."""
    bot = _answer_bot(mk_bot)
    MUTE.mute(CHAT, 600.0)
    run_async(bot._flush_job_buffer(_seeded(_sess(), "the interim answer")))
    qa_clock.advance(601.0)
    run_async(bot.flush_held_answers(_sess()))
    assert any("the interim answer" in body for body in _bodies(rich_http))
