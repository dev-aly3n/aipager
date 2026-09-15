"""Chat-wide mute for the duration of a Telegram flood ban (roadmap 8.17).

When Telegram answers a send with ``429`` and a ``retry_after`` longer
than ``TELEGRAM_MAX_RETRY_AFTER`` (minutes to hours), the bot is
flood-banned in that chat. Every further attempt into the ban — a retry,
a plain-text fallback, the next finished turn's answer, the busy card's
next edit — is itself a fresh violation that extends it. This daemon has
lost a working day to that twice (``retry_after=34712``, then ``28911``
on a friend's VM): the bot looked dead for most of a day while every
session kept working and each finished turn fired more violations.

So a ban is remembered, per chat, for exactly as long as Telegram said,
and every outbound path checks :data:`MUTE` first and skips the send
while it holds (raising :class:`FloodMuted`, which callers already treat
as a non-fatal send failure). Reactions are exempt — they are a separate
bucket, and the 🚨 reaction in ``transport._send_with_retry`` is how the
user learns a message was dropped.

On the WALL clock, and persisted (roadmap 8.28). Until 0.7.12 this was
in memory only and on the MONOTONIC clock, and the module docstring said
so: "a daemon restart forgets the mute (its startup message is one
attempt — accepted)". That was false comfort. A restart during a 9.5-hour
ban came back knowing nothing, and its own startup notice was the first
request into the ban — which is how three bans in one day escalated
1283 -> 312 -> 34212 on 2026-09-15.

Both halves of the fix are here. The deadline is now wall-clock, because
a monotonic one is meaningless across a restart (``time.monotonic()`` is
seconds since BOOT and restarts at a different origin). And
:meth:`FloodMute.serialise` / :meth:`~FloodMute.restore` let
``bot/flood_state.py`` carry it across, with
``config.FLOOD_MUTE_MAX_SECONDS`` clamping both the arming and the
restore so an NTP jump cannot mute the bot for years.

The one file THIS module writes is still just a *signal* for ``aipager
status`` / ``doctor`` (``config.FLOOD_MUTE_FILE``), which run in another
process with no channel to the daemon; the daemon never reads it back.
The durable copy is ``config.FLOOD_STATE_FILE``, owned by
``bot/flood_state.py``.

Logging discipline: one ``warning`` when a mute starts, one ``info`` when
it lifts, nothing per skipped message.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("aipager.bot.flood")


class FloodMuted(Exception):
    """An outbound send was skipped because its chat is flood-muted.

    ``retry_after`` is how many seconds the mute still has to run when the
    exception was raised; ``chat_id`` is the muted chat. Nothing may retry
    or fall back on this — the next attempt into a ban extends it.
    """

    def __init__(self, retry_after: float, chat_id=None) -> None:
        self.retry_after = float(retry_after)
        self.chat_id = chat_id
        super().__init__(
            f"chat {chat_id} is flood-muted for another {int(self.retry_after)}s"
        )


def _key(chat_id):
    """Normalise a chat id for the registry: ``int`` when it parses as one.

    Callers hand over ``int`` ids (rich path, scopes) and the legacy
    ``str`` ``config.CHAT_ID`` alike; both must land on the same entry.
    """
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return str(chat_id)


def _clamped(retry_after, source: str = "send") -> float:
    """*retry_after* in seconds, never negative and never past the cap.

    The cap (``config.FLOOD_MUTE_MAX_SECONDS``, 24 h) is what makes a
    WALL-clock deadline safe to trust (8.28 D-7). A wall clock can jump —
    an NTP correction, a VM resumed from a snapshot, a container with a
    bad RTC — and a deadline computed across such a jump, or restored from
    a file written before one, could otherwise mute the bot for years.
    The longest ban ever observed here is 34212 s (9.5 h), so this is more
    than twice the worst real case: anything past it is a clock bug, not a
    ban.
    """
    from aipager.config import FLOOD_MUTE_MAX_SECONDS

    try:
        seconds = max(float(retry_after), 0.0)
    except (TypeError, ValueError):
        return 0.0
    if seconds > FLOOD_MUTE_MAX_SECONDS:
        log.warning(
            "Telegram flood mute from %s asked for %.0fs — clamped to %.0fs; "
            "a deadline that far out is a clock problem, not a ban",
            source, seconds, FLOOD_MUTE_MAX_SECONDS,
        )
        return FLOOD_MUTE_MAX_SECONDS
    return seconds


def _mark_state_dirty() -> None:
    """Tell the durable store something changed. Never raises.

    Late import, and swallowed: ``flood_state`` imports this module, and a
    failure to schedule a diagnostic write must never reach a send path.
    """
    try:
        from aipager.bot import flood_state

        flood_state.mark_dirty()
    except Exception:  # pragma: no cover - defensive
        log.debug("could not mark flood state dirty", exc_info=True)


def clear_time(wall_until: float) -> str:
    """``HH:MM`` local wall-clock time at which a mute lifts."""
    return _dt.datetime.fromtimestamp(wall_until).strftime("%H:%M")


class FloodMute:
    """Per-chat ``muted_until`` registry. One instance per daemon: :data:`MUTE`."""

    def __init__(self) -> None:
        # key -> (wall_until, retry_after)
        #
        # WALL clock, and that is load-bearing (8.28 D-7). It used to be
        # `(monotonic_until, wall_until, retry_after)` with every decision
        # reading the MONOTONIC value and the wall one used only for the
        # display string. A monotonic deadline cannot survive a restart —
        # it is seconds since boot — so persisting the mute at all
        # required inverting which one is the source of truth.
        self._entries: dict[int | str, tuple[float, float]] = {}

    # ── arming ──────────────────────────────────────────────────────────

    def mute(self, chat_id, retry_after: float, *, source: str = "send") -> None:
        """Mute *chat_id* for *retry_after* seconds (never shortening an
        existing, longer mute). Logs exactly one warning when a mute starts;
        an extension of a mute already in force is a debug line.

        This is the ONE arming point for a mute, together with its twin in
        ``rich_message._ban_if_excessive`` — the limiter observes it, it
        never arms one. So there is exactly one place to look when asking
        why a chat is muted.
        """
        key = _key(chat_id)
        retry_after = _clamped(retry_after, source)
        now = time.time()
        wall_until = now + retry_after
        existing = self._entries.get(key)
        already_muted = existing is not None and existing[0] > now
        if already_muted and existing[0] >= wall_until:
            log.debug("chat %s already flood-muted past %s — %s ignored",
                      key, clear_time(existing[0]), source)
            return
        self._entries[key] = (wall_until, retry_after)
        if already_muted:
            log.debug("Telegram flood mute on chat %s extended to %s by %s",
                      key, clear_time(wall_until), source)
        else:
            log.warning(
                "Telegram flood control — chat %s muted for %ds (until %s) "
                "after %s got retry_after=%ds; every send to it is skipped "
                "until then, nothing is retried or re-sent as plain text",
                key, int(retry_after), clear_time(wall_until), source,
                int(retry_after),
            )
        self._write_signal()
        _mark_state_dirty()

    # ── querying ────────────────────────────────────────────────────────

    def is_muted(self, chat_id) -> bool:
        """True while the chat's mute holds. The first call after it lapses
        forgets the entry and logs the one "lifted" line."""
        key = _key(chat_id)
        entry = self._entries.get(key)
        if entry is None:
            return False
        if time.time() < entry[0]:
            return True
        del self._entries[key]
        log.info("Telegram flood mute on chat %s lifted — sends resume", key)
        self._write_signal()
        _mark_state_dirty()
        return False

    def remaining(self, chat_id) -> float:
        """Seconds left on the chat's mute; ``0.0`` when not muted."""
        entry = self._entries.get(_key(chat_id))
        if entry is None:
            return 0.0
        return max(0.0, entry[0] - time.time())

    def check(self, chat_id) -> None:
        """Raise :class:`FloodMuted` if *chat_id* is muted; otherwise return."""
        if self.is_muted(chat_id):
            raise FloodMuted(self.remaining(chat_id), chat_id)

    def active(self) -> list[dict]:
        """Every mute still in force, as ``{"chat_id", "until", "retry_after"}``
        with ``until`` on the wall clock (what the signal file carries)."""
        now = time.time()
        return [
            {"chat_id": key, "until": wall_until, "retry_after": retry_after}
            for key, (wall_until, retry_after) in self._entries.items()
            if wall_until > now
        ]

    # ── persistence (8.28) ──────────────────────────────────────────────

    def serialise(self) -> list[dict]:
        """Every mute still in force, for ``config.FLOOD_STATE_FILE``.

        Same shape as :meth:`active` — wall-clock ``until`` — because the
        reader is another process and, after a reboot, another boot.
        """
        return self.active()

    def restore(self, entries) -> int:
        """Reinstate mutes from a loaded document. Returns how many held.

        Never raises, whatever the file contains: a corrupt state file
        must leave the daemon running, exactly as
        ``SessionRegistry.load`` does. Silently drops anything it cannot
        read.

        Two rules, both load-bearing:

        * a deadline already in the PAST is dropped, not restored — the
          ban lapsed while the daemon was down, and resurrecting it would
          mute a healthy chat;
        * a deadline further out than ``FLOOD_MUTE_MAX_SECONDS`` is
          CLAMPED, not trusted (D-7). The file may have been written
          before a clock jump.
        """
        if not isinstance(entries, list):
            return 0
        now = time.time()
        restored = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                until = float(entry.get("until"))
            except (TypeError, ValueError):
                continue
            if until <= now:
                continue
            remaining = _clamped(until - now, source="restore")
            if remaining <= 0.0:
                continue
            try:
                retry_after = float(entry.get("retry_after", remaining))
            except (TypeError, ValueError):
                retry_after = remaining
            key = _key(entry.get("chat_id"))
            self._entries[key] = (now + remaining, retry_after)
            restored += 1
        if restored:
            log.warning(
                "Telegram flood mute restored for %d chat(s) from the "
                "previous daemon — sends to them stay skipped until it lifts",
                restored,
            )
            self._write_signal()
        return restored

    # ── clearing ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Forget every mute and drop the signal file.

        SIGNAL-FILE HYGIENE ONLY since 8.28. `lifecycle.start` still calls
        it to drop whatever a previous daemon left beside the socket, but
        it now does so BEFORE `flood_state.load()` reinstates the real
        deadlines — clearing after the load would be the exact bug R5
        exists to fix.
        """
        self._entries.clear()
        self._write_signal()

    # ── the status signal ───────────────────────────────────────────────

    def _write_signal(self) -> None:
        """Best-effort: mirror :meth:`active` into ``config.FLOOD_MUTE_FILE``
        for ``aipager status``; unlink it when nothing is muted. Never
        raises into a send path."""
        from aipager.config import FLOOD_MUTE_FILE

        path = Path(FLOOD_MUTE_FILE)
        active = self.active()
        try:
            if not active:
                path.unlink(missing_ok=True)
                return
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"muted": active}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.debug("flood-mute signal file %s not updated", path,
                      exc_info=True)


MUTE = FloodMute()

__all__ = ["FloodMute", "FloodMuted", "MUTE", "clear_time"]
