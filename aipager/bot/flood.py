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

In memory only, on the monotonic clock: a daemon restart forgets the
mute (its startup message is one attempt — accepted). Nothing here is
persisted to ``state.py``. The one file this module writes is a *signal*
for ``aipager status`` / ``doctor`` (``config.FLOOD_MUTE_FILE``), which
run in another process with no channel to the daemon; the daemon never
reads it back.

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


def clear_time(wall_until: float) -> str:
    """``HH:MM`` local wall-clock time at which a mute lifts."""
    return _dt.datetime.fromtimestamp(wall_until).strftime("%H:%M")


class FloodMute:
    """Per-chat ``muted_until`` registry. One instance per daemon: :data:`MUTE`."""

    def __init__(self) -> None:
        # key -> (monotonic_until, wall_until, retry_after)
        self._entries: dict[int | str, tuple[float, float, float]] = {}

    # ── arming ──────────────────────────────────────────────────────────

    def mute(self, chat_id, retry_after: float, *, source: str = "send") -> None:
        """Mute *chat_id* for *retry_after* seconds (never shortening an
        existing, longer mute). Logs exactly one warning when a mute starts;
        an extension of a mute already in force is a debug line.
        """
        key = _key(chat_id)
        retry_after = max(float(retry_after), 0.0)
        now = time.monotonic()
        mono_until = now + retry_after
        wall_until = time.time() + retry_after
        existing = self._entries.get(key)
        already_muted = existing is not None and existing[0] > now
        if already_muted and existing[0] >= mono_until:
            log.debug("chat %s already flood-muted past %s — %s ignored",
                      key, clear_time(existing[1]), source)
            return
        self._entries[key] = (mono_until, wall_until, retry_after)
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

    # ── querying ────────────────────────────────────────────────────────

    def is_muted(self, chat_id) -> bool:
        """True while the chat's mute holds. The first call after it lapses
        forgets the entry and logs the one "lifted" line."""
        key = _key(chat_id)
        entry = self._entries.get(key)
        if entry is None:
            return False
        if time.monotonic() < entry[0]:
            return True
        del self._entries[key]
        log.info("Telegram flood mute on chat %s lifted — sends resume", key)
        self._write_signal()
        return False

    def remaining(self, chat_id) -> float:
        """Seconds left on the chat's mute; ``0.0`` when not muted."""
        entry = self._entries.get(_key(chat_id))
        if entry is None:
            return 0.0
        return max(0.0, entry[0] - time.monotonic())

    def check(self, chat_id) -> None:
        """Raise :class:`FloodMuted` if *chat_id* is muted; otherwise return."""
        if self.is_muted(chat_id):
            raise FloodMuted(self.remaining(chat_id), chat_id)

    def active(self) -> list[dict]:
        """Every mute still in force, as ``{"chat_id", "until", "retry_after"}``
        with ``until`` on the wall clock (what the signal file carries)."""
        now = time.monotonic()
        return [
            {"chat_id": key, "until": wall, "retry_after": retry_after}
            for key, (mono, wall, retry_after) in self._entries.items()
            if mono > now
        ]

    # ── clearing ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Forget every mute and drop the signal file (daemon start/stop)."""
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
