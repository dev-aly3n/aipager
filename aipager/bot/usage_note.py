"""The busy card's near-limit note (roadmap 8.131 follow-up).

When the newest plan reading of the sign-in a session runs under has a
window at 90% or more (:func:`aipager.usage_store.limit_level`, the rule
the Mini App's amber bar uses too), the session's busy card shows one row
per such window above its divider, with /usage's own reset sentence. It
rides on card edits that happen anyway: no message and no request of its
own, nothing remembered, gone from the first render after the reset.

Privacy, the hard rule: plan usage is the whole account's, so the note is
shown only where /usage would show plan limits to every reader.

- Only a private chat (id > 0). A group's card is read by every member,
  people aipager does not know included, so no group, supergroup, legacy
  team group or negative personal-mode chat ever gets it, the owner's
  sessions there included.
- Only when that chat's one reader (a private chat's id is its person's
  user id) passes :func:`aipager.bot.usage_cmd.usage_access` with plan
  limits: the owner or an admin in scope mode, the operator in personal
  mode, an ADMIN in a legacy team. A plain user's or read-only member's
  private chat never gets it.
- Only the reading stored under the session's own sign-in key
  (``TrackedSession.usage_plan_key``, stamped where the reading arrives),
  never the newest reading of any sign-in; an unknown key shows nothing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aipager import usage_store
from aipager.bot import usage_cmd
from aipager.bot.transport import resolve_chat_id_int
from aipager.i18n import Msg

log = logging.getLogger(__name__)

# One whole sentence per window and step (the window's name is part of it).
_ROWS = {
    ("five_hour", 1): Msg(
        "usage.note.five_hour_near", "⚠️ 5-hour limit almost reached: {pct} used",
        eg={"pct": "92%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The plan's 5-hour limit is 90% used or "
             "more."),
    ("seven_day", 1): Msg(
        "usage.note.seven_day_near", "⚠️ Weekly limit almost reached: {pct} used",
        eg={"pct": "92%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The plan's weekly limit (all models "
             "together) is 90% used or more."),
    ("spend_limit", 1): Msg(
        "usage.note.spend_limit_near", "⚠️ Spend limit almost reached: {pct} used",
        eg={"pct": "92%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The spend limit a gateway sets is 90% "
             "used or more."),
    ("five_hour", 2): Msg(
        "usage.note.five_hour_reached", "⛔ 5-hour limit reached: {pct} used",
        eg={"pct": "100%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The plan's 5-hour limit is used up."),
    ("seven_day", 2): Msg(
        "usage.note.seven_day_reached", "⛔ Weekly limit reached: {pct} used",
        eg={"pct": "100%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The plan's weekly limit (all models "
             "together) is used up."),
    ("spend_limit", 2): Msg(
        "usage.note.spend_limit_reached", "⛔ Spend limit reached: {pct} used",
        eg={"pct": "250%"},
        note="A row on the busy card, shown only in the private chat of someone allowed to "
             "see plan usage. {pct} is a percentage. The /usage sentence saying when the "
             "limit resets follows after \" · \". The spend limit a gateway sets is used "
             "up (it can go past 100%)."),
}


def card_rows(tr, windows: Any, *, now: float) -> list[str]:
    """The note's rows (Markdown, in *tr*'s language), one per window of
    *windows* that is at or over the near-limit step and has not reset by
    *now*, in /usage's order (at most three). ``[]`` for no windows. Pure."""
    if not isinstance(windows, dict) or not windows:
        return []
    rows = []
    for name in usage_cmd.WINDOW_ORDER:
        window = windows.get(name)
        if window is None or not window.resets_at > now:
            continue
        level = usage_store.limit_level(window.pct)
        if level < 1:
            continue
        head = tr.md(_ROWS[(name, min(level, 2))], pct=usage_cmd.pct_text(window.pct, tr))
        rows.append(head + " · " + usage_cmd.reset_text(window, now, tr, "md"))
    return rows


def windows_for(bot, sess, *, now: float | None = None) -> dict | None:
    """The plan windows *sess*'s busy card may show, or ``None`` (see the
    module's privacy rule). Memory only: it runs on every card edit.
    Never raises."""
    try:
        chat = resolve_chat_id_int(sess)
        if type(chat) is not int or chat <= 0:
            return None
        key = getattr(sess, "usage_plan_key", "")
        if not isinstance(key, str) or not key or key == "unknown":
            return None
        access = usage_cmd.usage_access(bot, chat, chat, for_group=False)
        if access is None or not access.plan:
            return None
        return usage_store.plan_windows(key, now=time.time() if now is None else now)
    except Exception:  # noqa: BLE001 - no note rather than a broken card
        log.debug("usage note: windows not worked out", exc_info=True)
        return None
