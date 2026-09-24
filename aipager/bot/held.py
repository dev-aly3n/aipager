"""Answers a flood mute refused, kept until it lifts (roadmap 8.29, R6).

Five answers were silently dropped on 2026-09-15. Each left one INFO line
— ``sendRichMessage flood-banned — answer not delivered, no fallback`` —
and nothing else: the text lived in a local variable of
``NotifyMixin.notify`` and went out of scope with it. The work had been
done, the tokens had been spent, and the operator never saw the result.

Not falling back to plain text was CORRECT: a second attempt into a ban is
a fresh violation that extends it. The mistake was treating "cannot send
now" as "cannot send". A ban lifts; the answer is still worth reading
afterwards. So it is held here and delivered late with an honest marker.

**Data only.** No I/O, no clock beyond ``time.time()`` for the stamp, no
imports from ``notify``/``animation``. The sending lives in
``NotifyMixin.flush_held_answers``, the trigger in the session monitor's
existing 2 s tick — so this module can be tested by construction and the
delivery path can be tested without it.

**One entry per TURN, bounded by COUNT and by AGE** (8.29 T4). Until
iteration 2 this kept one entry per SESSION and a second answer replaced
the first at DEBUG — but a 9.5-hour ban routinely spans several turns of
one session, because the user re-prompts when the first answer goes
quiet, which is exactly what happened on 2026-09-15. Two turns' answers
are two pieces of the user's work: both are kept and both are delivered,
oldest first. Nothing is REPLACED; the only way an answer leaves this
buffer without being sent is the count cap or the age cap, and each of
those logs a WARNING naming what was lost. "Nothing is ever SILENTLY
dropped" (R6) does not hold if the drop is a debug line.

An unbounded buffer behind a 9.5-hour ban is a memory leak with extra
steps, so: at most :data:`HELD_ANSWER_MAX_PER_CHAT` entries per chat,
drop-oldest, and nothing older than :data:`HELD_ANSWER_MAX_AGE_SECONDS`,
which is the longest mute that can exist — past it the answer is not
waiting for anything.

**Not persisted.** A daemon restart while answers are held loses them;
the text can be large and ``FLOOD_STATE_FILE`` is a status artefact, not a
queue. Accepted for this ship, logged once at WARNING by
``lifecycle.stop`` rather than silently, and stated in
``docs/troubleshooting.md``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Answers one chat may hold at once. A ban is per chat, so this bounds
#: the whole buffer. Since 8.29 T4 it counts ENTRIES, not sessions: the
#: buffer keeps one entry per turn, so three sessions finishing two turns
#: each inside one ban hold six answers, not three.
HELD_ANSWER_MAX_PER_CHAT = 20

#: Oldest an entry may get before it is dropped. 24 hours, the same
#: number as ``config.FLOOD_MUTE_MAX_SECONDS`` — the longest mute that
#: can exist, armed or restored — so anything older than this is not
#: waiting for a ban to lift, it is waiting for nothing.
#: ``tests/...::test_the_age_cap_outlasts_the_longest_possible_mute``
#: pins the two together; the constant is duplicated rather than imported
#: to keep this module free of aipager imports (it is data only, and that
#: is what lets it be tested by construction).
HELD_ANSWER_MAX_AGE_SECONDS = 86400.0

#: Delivery attempts before an entry is dropped. A held answer that keeps
#: failing for a reason that is NOT the mute (the message it replies to
#: was deleted, the bot was blocked) must not be retried for ever.
HELD_ANSWER_MAX_ATTEMPTS = 5

#: The late marker, as one template so the wording exists once.
LATE_MARKER = "⏳ delivered late (held {minutes} min during a Telegram rate limit)"


def late_marker(held_seconds: float) -> str:
    """The one line a late answer opens with. Pure.

    ``max(1, round(...))`` so it never reads "held 0 min": an answer held
    for forty seconds was still held, and a marker that claims otherwise
    invites the reader to think the delay was theirs.

    Deliberately free of markdown and HTML metacharacters, so the SAME
    string is used for the rich and the plain variant and neither can fail
    to parse — a marker that broke the send it is attached to would lose
    the very answer it exists to deliver.
    """
    try:
        seconds = max(float(held_seconds), 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    return LATE_MARKER.format(minutes=max(1, round(seconds / 60)))


@dataclass
class HeldAnswer:
    """One answer waiting for a chat's mute to lift.

    ``key`` identifies the TURN, not the session: it is what makes two
    answers from one session inside one ban two entries rather than one
    overwriting the other (8.29 T4). Assigned by :meth:`HeldAnswers.hold`
    and never reused.
    """

    chat_id: object
    session: str
    label: str
    rich_text: str
    plain_text: str
    reply_to: int | None = None
    held_at: float = field(default_factory=time.time)
    attempts: int = 0
    key: str = ""
    # Roadmap 8.39: the finish path's pending delivery record, confirmed
    # on the session when this entry finally lands. Empty for holds that
    # are not a turn's answer.
    digests: tuple[str, ...] = ()
    selected_wall: float = 0.0

    def held_seconds(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.held_at)


class HeldAnswers:
    """Per-chat buffer of refused answers, one entry per TURN."""

    def __init__(self) -> None:
        # chat key -> {entry key -> HeldAnswer}, insertion-ordered so
        # "drop oldest" and "deliver in order" are both well defined
        # without a second structure.
        self._entries: dict[object, dict[str, HeldAnswer]] = {}
        self._seq: int = 0

    def hold(self, *, chat_id, session: str, label: str, rich_text: str,
             plain_text: str, reply_to: int | None = None,
             at: float | None = None, digests: tuple[str, ...] = (),
             selected_wall: float = 0.0) -> HeldAnswer:
        """Keep one answer. Every call is a NEW entry (8.29 T4).

        Nothing is replaced. A session can finish two turns inside one
        9.5-hour ban — the user re-prompts when the first answer goes
        quiet, which is what they did on 2026-09-15 — and those are two
        different pieces of work, both of which have to arrive, in order.
        The old "newest per session wins" rule meant the first was
        overwritten and lost at DEBUG: an answer silently REPLACED is the
        same failure as an answer silently dropped, and this module exists
        because of that failure.

        Bounded by the two caps instead, each of which logs a WARNING with
        what was lost.
        """
        per_chat = self._entries.setdefault(chat_id, {})
        self._seq += 1
        entry = HeldAnswer(
            chat_id=chat_id, session=session, label=label,
            rich_text=rich_text, plain_text=plain_text, reply_to=reply_to,
            held_at=time.time() if at is None else at,
            key=f"{session}#{self._seq}",
            digests=digests, selected_wall=selected_wall,
        )
        per_chat[entry.key] = entry
        self.expire(at)
        while len(per_chat) > HELD_ANSWER_MAX_PER_CHAT:
            dropped_key, dropped = next(iter(per_chat.items()))
            per_chat.pop(dropped_key)
            log.warning(
                "held-answer buffer for chat %s over cap (%d) — dropping the "
                "oldest: %s, %d chars, held %d min. IT IS LOST",
                chat_id, HELD_ANSWER_MAX_PER_CHAT, dropped.label,
                len(dropped.rich_text),
                max(1, round(dropped.held_seconds() / 60)),
            )
        return entry

    def expire(self, now: float | None = None) -> int:
        """Drop everything past :data:`HELD_ANSWER_MAX_AGE_SECONDS`.

        The other half of "bounded by count AND age" (8.29 T4). The count
        cap alone leaves an answer from a ban that lifted a week ago
        queued behind nothing, waiting for a flush that will deliver it as
        news; the age cap alone leaves a burst unbounded. Driven from the
        session monitor's existing 2 s sweep, so there is no timer of our
        own to leak, and every drop is a WARNING naming what was lost —
        never a debug line.
        """
        now = time.time() if now is None else now
        dropped = 0
        for chat_id, per_chat in list(self._entries.items()):
            for key, entry in list(per_chat.items()):
                if now - entry.held_at <= HELD_ANSWER_MAX_AGE_SECONDS:
                    continue
                per_chat.pop(key, None)
                dropped += 1
                log.warning(
                    "held answer for %s in chat %s expired after %d h "
                    "undelivered (%d chars). IT IS LOST",
                    entry.label, chat_id,
                    round(HELD_ANSWER_MAX_AGE_SECONDS / 3600),
                    len(entry.rich_text),
                )
            if not per_chat:
                self._entries.pop(chat_id, None)
        return dropped

    def pending(self, chat_id=None) -> list[HeldAnswer]:
        """Held answers, OLDEST FIRST — the order they are delivered in.

        Order is part of the contract, not an accident of the dict: two
        turns of one session read as nonsense the other way round, and
        after 8.29 T4 two turns of one session is the ordinary case."""
        if chat_id is not None:
            return sorted(self._entries.get(chat_id, {}).values(),
                          key=lambda e: e.held_at)
        out: list[HeldAnswer] = []
        for per_chat in self._entries.values():
            out.extend(per_chat.values())
        return sorted(out, key=lambda e: e.held_at)

    def count(self, chat_id=None) -> int:
        if chat_id is not None:
            return len(self._entries.get(chat_id, {}))
        return sum(len(per_chat) for per_chat in self._entries.values())

    def take(self, chat_id, key: str) -> HeldAnswer | None:
        """Remove and return one entry by its ``key``, or ``None``.

        A *key* that names no entry is treated as a SESSION name and the
        OLDEST entry of that session is taken. That is the pre-T4 call
        shape, and it is kept because "drop this session's oldest held
        answer" is still a meaningful request — and because the two
        namespaces cannot collide: a key is always ``session#seq``, and a
        session name never contains ``#``.
        """
        per_chat = self._entries.get(chat_id)
        if not per_chat:
            return None
        entry = per_chat.pop(key, None)
        if entry is None:
            for candidate in sorted(per_chat.values(), key=lambda e: e.held_at):
                if candidate.session == key:
                    entry = per_chat.pop(candidate.key)
                    break
        if not per_chat:
            self._entries.pop(chat_id, None)
        return entry

    def drop(self, chat_id, key: str) -> None:
        self.take(chat_id, key)

    def sessions_with_held(self) -> list[tuple]:
        """``(chat_id, session)`` for everything held — what the monitor
        iterates to decide which sessions to flush. One pair per ENTRY, so
        a session with two held answers appears twice; the caller only
        ever asks "is this session in here", and de-duplicating would cost
        a set for no reader."""
        return [(chat_id, entry.session)
                for chat_id, per_chat in self._entries.items()
                for entry in per_chat.values()]

    def clear(self) -> None:
        self._entries.clear()


#: The daemon's one buffer. Cleared per test by a conftest fixture, like
#: ``flood.MUTE``.
HELD = HeldAnswers()

__all__ = [
    "HELD",
    "HELD_ANSWER_MAX_AGE_SECONDS",
    "HELD_ANSWER_MAX_ATTEMPTS",
    "HELD_ANSWER_MAX_PER_CHAT",
    "HeldAnswer",
    "HeldAnswers",
    "LATE_MARKER",
    "late_marker",
]
