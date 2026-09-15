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

**Bounded, newest-per-turn-wins**, the shape of
``notify._record_job_interim``: one entry per session (a second answer for
the same session REPLACES the first — the newer one is what the operator
wants) and at most :data:`HELD_ANSWER_MAX_PER_CHAT` sessions per chat,
drop-oldest with exactly one WARNING. An unbounded buffer behind a
9.5-hour ban is a memory leak with extra steps.

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

#: Sessions whose answers one chat may hold at once. A ban is per chat, so
#: this bounds the whole buffer.
HELD_ANSWER_MAX_PER_CHAT = 20

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
    """One answer waiting for a chat's mute to lift."""

    chat_id: object
    session: str
    label: str
    rich_text: str
    plain_text: str
    reply_to: int | None = None
    held_at: float = field(default_factory=time.time)
    attempts: int = 0

    def held_seconds(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.held_at)


class HeldAnswers:
    """Per-chat, per-session buffer of refused answers."""

    def __init__(self) -> None:
        # chat key -> {session name -> HeldAnswer}, insertion-ordered so
        # "drop oldest" is well defined without a second structure.
        self._entries: dict[object, dict[str, HeldAnswer]] = {}

    def hold(self, *, chat_id, session: str, label: str, rich_text: str,
             plain_text: str, reply_to: int | None = None,
             at: float | None = None) -> HeldAnswer:
        """Keep one answer for *session*. Newest per session wins.

        Replacing rather than appending is the same rule the busy card
        follows: within one turn the later render supersedes the earlier,
        and delivering both after a ban would be a wall of stale text.
        """
        per_chat = self._entries.setdefault(chat_id, {})
        if session in per_chat:
            log.debug("[%s] replacing the held answer with a newer one", label)
        per_chat.pop(session, None)          # re-insert to refresh its age
        entry = HeldAnswer(
            chat_id=chat_id, session=session, label=label,
            rich_text=rich_text, plain_text=plain_text, reply_to=reply_to,
            held_at=time.time() if at is None else at,
        )
        per_chat[session] = entry
        while len(per_chat) > HELD_ANSWER_MAX_PER_CHAT:
            dropped_session, dropped = next(iter(per_chat.items()))
            per_chat.pop(dropped_session)
            log.warning(
                "held-answer buffer for chat %s over cap — dropping the "
                "oldest (%s, %d chars)",
                chat_id, dropped.label, len(dropped.rich_text),
            )
        return entry

    def pending(self, chat_id=None) -> list[HeldAnswer]:
        """Held answers, oldest first — the order they are delivered in."""
        if chat_id is not None:
            return list(self._entries.get(chat_id, {}).values())
        out: list[HeldAnswer] = []
        for per_chat in self._entries.values():
            out.extend(per_chat.values())
        return sorted(out, key=lambda e: e.held_at)

    def count(self, chat_id=None) -> int:
        if chat_id is not None:
            return len(self._entries.get(chat_id, {}))
        return sum(len(per_chat) for per_chat in self._entries.values())

    def take(self, chat_id, session: str) -> HeldAnswer | None:
        """Remove and return one entry, or ``None``."""
        per_chat = self._entries.get(chat_id)
        if not per_chat:
            return None
        entry = per_chat.pop(session, None)
        if not per_chat:
            self._entries.pop(chat_id, None)
        return entry

    def drop(self, chat_id, session: str) -> None:
        self.take(chat_id, session)

    def sessions_with_held(self) -> list[tuple]:
        """``(chat_id, session)`` for everything held — what the monitor
        iterates to decide which sessions to flush."""
        return [(chat_id, session)
                for chat_id, per_chat in self._entries.items()
                for session in per_chat]

    def clear(self) -> None:
        self._entries.clear()


#: The daemon's one buffer. Cleared per test by a conftest fixture, like
#: ``flood.MUTE``.
HELD = HeldAnswers()

__all__ = [
    "HELD",
    "HELD_ANSWER_MAX_ATTEMPTS",
    "HELD_ANSWER_MAX_PER_CHAT",
    "HeldAnswer",
    "HeldAnswers",
    "LATE_MARKER",
    "late_marker",
]
