"""The long-run flood policy as arithmetic (roadmap 8.30).

Pure functions and nothing else: no clock, no I/O, no state, no import
from ``aipager.bot``. Three readers share them — the daemon's limiter
(``bot/flood_budget.py``), the busy-card animator (``bot/animation.py``)
and ``aipager status``, which is a DIFFERENT PROCESS reading the durable
file — and before this module the status command kept its own copy of
the ban-memory rule, hardcoded to 24 hours, which would have silently
disagreed with the daemon the day the daemon's rule changed. It is that
day. One copy, so the operator's reading of a chat and the daemon's
treatment of it cannot drift.

The numbers come from ``aipager.config`` as defaults; every function takes
them as arguments too, so a limiter built with different limits (a test,
a tool) is answered with ITS limits rather than the module's.

Why each rule exists is written where the constant is defined
(``config.py``, the "long-run volume budget" block): the vm3 DM ban of
2026-09-23 — two sessions streaming for 3 h 23 min under every short
window we had, then a straight 7-hour ban.
"""

from __future__ import annotations

import math

from aipager.config import (
    CARD_AGE_TIER1_AT,
    CARD_AGE_TIER1_INTERVAL,
    CARD_AGE_TIER2_AT,
    CARD_AGE_TIER2_INTERVAL,
    CARD_AGE_TIER3_AT,
    CARD_AGE_TIER3_INTERVAL,
    FLOOD_HOURLY_ESSENTIAL_RESERVE,
    FLOOD_HOURLY_MAX,
    FLOOD_WARNING_HOURS,
    TYPING_AGE_TIER2_INTERVAL,
    TYPING_AGE_TIER3_INTERVAL,
    TYPING_INDICATOR_INTERVAL,
)

#: The elapsed-time units a busy card can render in: seconds (today's
#: ``45s`` / ``4m 10s``), whole minutes (``<1m`` / ``23m``) and hours plus
#: minutes (``23m`` / ``1h 23m``).
UNIT_SECONDS = "s"
UNIT_MINUTES = "m"
UNIT_HOURS = "h"


def bans_within(stamps, wall_now: float, seconds: float) -> int:
    """How many ban stamps fall in the last *seconds* before *wall_now*.

    A stamp counts when ``0 <= wall_now - stamp <= seconds``: a stamp in
    the future (a clock that moved) is not a ban that has happened, and is
    the restore path's to clamp, not this function's to guess at.

    Tolerant by design, because the status command feeds it straight out
    of a file: a non-list is zero bans, and any entry that is not a finite
    real number (a string, a bool, ``None``, NaN) is skipped rather than
    raised on.
    """
    if not isinstance(stamps, (list, tuple)):
        return 0
    count = 0
    for stamp in stamps:
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        if not math.isfinite(stamp):
            continue
        if 0.0 <= wall_now - stamp <= seconds:
            count += 1
    return count


def effective_ceiling(
    *, max_rate: float, min_rate: float, bans: int, warned: bool,
    warned_ceiling: float,
) -> float:
    """The rate ceiling a chat's earned rate may climb to, right now.

    ``max_rate / (1 + bans)`` — one ban in the memory window halves it,
    two third it — capped further at *warned_ceiling* while the chat is in
    its post-429 warning regime, and never below *min_rate*: a ceiling
    under the floor would invert the two and pin the chat at a rate it can
    never leave. A negative ban count is treated as none.
    """
    ceiling = float(max_rate) / (1 + max(int(bans), 0))
    if warned:
        ceiling = min(ceiling, float(warned_ceiling))
    return max(float(min_rate), ceiling)


def clamp_warned_until(
    value, wall_now: float, *,
    warning_seconds: float = FLOOD_WARNING_HOURS * 3600.0,
) -> float:
    """A persisted ``warned_until`` as the wall-clock end of AT MOST one
    warning regime from *wall_now*, or ``0.0`` for "not warned".

    The one clamp both readers of the durable file apply — the daemon's
    ``restore`` and ``aipager status`` — so the operator is never shown a
    regime the daemon itself would not hold (a file written before a clock
    jump can claim one a year long). Anything that does not read as a
    finite, positive number (a bool, ``None``, a word, NaN, infinity) is
    ``0.0``: an unreadable regime is not a regime. A numeric string reads
    as its number, as every other field of the file does.
    """
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or value <= 0.0:
        return 0.0
    return min(value, float(wall_now) + max(float(warning_seconds), 0.0))


def card_floor_beside_typing(
    chat_floor: float, typing_interval: float, *,
    card_min_rate: float = 1.0 / CARD_AGE_TIER1_INTERVAL,
) -> float:
    """The seconds-per-call floor a chat's CARDS share once its typing
    bubble's share is reserved (8.30, operator ruling 2026-09-23: "typing
    always shows, the young card yields").

    *chat_floor* is the chat's own floor — the tightest of its cadence
    floor, its earned rate and its sustained window — so ``1 / chat_floor``
    is what the chat can take per second. The bubble costs
    ``1 / typing_interval`` of it; the cards divide the rest. In a healthy
    DM that is 0.5/s less 0.22/s, a floor of 3.6 s instead of 2.0 s.

    Never below *card_min_rate* per second for the cards together (one
    edit per ``CARD_AGE_TIER1_INTERVAL``, the pace a two-minute-old card
    has anyway): in a chat too slow for both — just penalised by a 429 —
    the reservation shrinks rather than the card stopping, and the bubble,
    the chat's lowest ornament, is what the limiter's extra reserve
    refuses. Non-positive or non-finite input reserves nothing.
    """
    try:
        floor = float(chat_floor)
        interval = float(typing_interval)
    except (TypeError, ValueError):
        return chat_floor
    if not (math.isfinite(floor) and math.isfinite(interval)) \
            or floor <= 0.0 or interval <= 0.0:
        return chat_floor
    capacity = 1.0 / floor
    cards = max(capacity - 1.0 / interval, min(capacity, card_min_rate))
    return 1.0 / cards


def hourly_limits(
    bans: int, *, hourly_max: int = FLOOD_HOURLY_MAX,
    reserve: int = FLOOD_HOURLY_ESSENTIAL_RESERVE,
) -> tuple[int, int]:
    """``(total_budget, ornament_budget)`` for one rolling hour.

    Both divided by ``1 + bans`` and floored to whole calls: ``(1200,
    1080)`` for a clean week, ``(600, 540)`` with one ban in it, ``(400,
    360)`` with two. The ornament share is the total less the essential
    reserve, so the reserve shrinks with the budget rather than eating a
    penalised chat's entire allowance.
    """
    divisor = 1 + max(int(bans), 0)
    total = int(math.floor(max(int(hourly_max), 0) / divisor))
    ornament = int(math.floor(max(int(hourly_max) - int(reserve), 0) / divisor))
    return total, min(ornament, total)


def card_age_floor(age_seconds: float, enabled: bool) -> float:
    """The minimum seconds between busy-card edits for a turn this old.

    ``0`` below ``CARD_AGE_TIER1_AT`` (2 min) — the card keeps today's
    cadence exactly — then 10 s, 30 s from 10 min and 60 s from an hour.
    Always ``0`` when the ``card_age_decay`` preference is off, and for a
    negative or non-finite age (no age is not an old age).
    """
    if not enabled:
        return 0.0
    try:
        age = float(age_seconds)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(age) or age < CARD_AGE_TIER1_AT:
        return 0.0
    if age < CARD_AGE_TIER2_AT:
        return float(CARD_AGE_TIER1_INTERVAL)
    if age < CARD_AGE_TIER3_AT:
        return float(CARD_AGE_TIER2_INTERVAL)
    return float(CARD_AGE_TIER3_INTERVAL)


def typing_interval(
    age_seconds: float, enabled: bool, *,
    base: float = TYPING_INDICATOR_INTERVAL,
) -> float:
    """Seconds between typing bubbles for a chat whose OLDEST busy turn is
    this old (8.30, operator ruling #2).

    *base* (``TYPING_INDICATOR_INTERVAL``, 4.5 s — a steady bubble) below
    ``CARD_AGE_TIER2_AT`` (10 min), ``TYPING_AGE_TIER2_INTERVAL`` (9 s) up
    to ``CARD_AGE_TIER3_AT`` (an hour), ``TYPING_AGE_TIER3_INTERVAL`` (15 s)
    after — never faster than *base*. Always *base* when the
    ``card_age_decay`` preference is off, for a non-positive *base* (the
    bubble disabled) and for a negative or non-finite age. At 4.5 s the
    bubble alone is 800 calls an hour, which trips the hour's typing shed
    on any turn much past an hour; at 15 s it is 240.
    """
    if not enabled or not base or base <= 0:
        return base
    try:
        age = float(age_seconds)
    except (TypeError, ValueError):
        return base
    if not math.isfinite(age) or age < CARD_AGE_TIER2_AT:
        return base
    if age < CARD_AGE_TIER3_AT:
        return max(float(base), float(TYPING_AGE_TIER2_INTERVAL))
    return max(float(base), float(TYPING_AGE_TIER3_INTERVAL))


def elapsed_unit(age_seconds: float, enabled: bool) -> str:
    """The unit a card this old shows its elapsed counters in.

    Seconds below 10 min (today's ``4m 10s``), minutes from 10 min, hours
    and minutes from an hour. THE UNIT MATCHES THE REFRESH: a card edited
    once a minute that showed seconds would look frozen for 59 of them —
    and a counter that stops reads as a hung session, which the operator
    rejected outright on 2026-09-12. Always seconds when decay is off.
    """
    if not enabled:
        return UNIT_SECONDS
    try:
        age = float(age_seconds)
    except (TypeError, ValueError):
        return UNIT_SECONDS
    if not math.isfinite(age) or age < CARD_AGE_TIER2_AT:
        return UNIT_SECONDS
    if age < CARD_AGE_TIER3_AT:
        return UNIT_MINUTES
    return UNIT_HOURS


def format_elapsed(seconds: float, unit: str = UNIT_SECONDS) -> str:
    """*seconds* rendered in *unit*.

    * ``"s"`` — today's rule: ``45s`` under a minute, else ``4m 10s``.
    * ``"m"`` — ``<1m`` under a minute, else whole minutes, ``23m``.
    * ``"h"`` — whole minutes under an hour (``23m``), else ``1h 23m``.

    Negative and non-finite input clamps to zero; an unknown unit falls
    back to seconds, which is always a truthful rendering.
    """
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    total = int(value) if math.isfinite(value) and value > 0 else 0
    if unit == UNIT_MINUTES:
        return "<1m" if total < 60 else f"{total // 60}m"
    if unit == UNIT_HOURS:
        if total < 60:
            return "<1m"
        if total < 3600:
            return f"{total // 60}m"
        return f"{total // 3600}h {(total % 3600) // 60}m"
    if total >= 60:
        return f"{total // 60}m {total % 60}s"
    return f"{total}s"


__all__ = [
    "UNIT_HOURS",
    "UNIT_MINUTES",
    "UNIT_SECONDS",
    "bans_within",
    "card_age_floor",
    "card_floor_beside_typing",
    "clamp_warned_until",
    "effective_ceiling",
    "elapsed_unit",
    "format_elapsed",
    "hourly_limits",
    "typing_interval",
]
